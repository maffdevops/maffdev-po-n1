import logging
import os
from typing import Optional

from fastapi import APIRouter, Query
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.exceptions import TelegramBadRequest, TelegramForbidden

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionLocal
from app.models import Tenant, UserAccess, UserLang, Event
from app.settings import settings
from app.bots.child.texts import t_user

logger = logging.getLogger("pocket_saas.postback")

router = APIRouter(prefix="/pb", tags=["postback"])


async def _get_tenant_by_code(code: str) -> Optional[Tenant]:
    async with SessionLocal() as session:
        # сначала пробуем по pb_secret
        res = await session.execute(
            select(Tenant).where(Tenant.pb_secret == code)
        )
        tenant = res.scalar_one_or_none()
        if tenant:
            return tenant

        # если нет — пробуем tn{id}
        if code.startswith("tn") and code[2:].isdigit():
            tid = int(code[2:])
            tenant = await session.get(Tenant, tid)
            return tenant

    return None


async def _get_user_lang(tenant_id: int, user_id: int) -> str:
    async with SessionLocal() as session:
        res = await session.execute(
            select(UserLang.lang).where(
                UserLang.tenant_id == tenant_id,
                UserLang.user_id == user_id,
            )
        )
        lang = res.scalar_one_or_none()
    return lang or settings.lang_default


async def _send_screen_with_photo_to_user(
    bot: Bot,
    chat_id: int,
    lang: str,
    screen: str,
    text: str,
    kb: Optional[InlineKeyboardMarkup] = None,
) -> None:
    """
    Отправка экрана юзеру из postback-сервиса.
    Любые ошибки Telegram (chat not found / blocked / etc)
    НЕ должны ломать обработчик постбэка.
    """
    # как и в детском боте, пока используем assets/en
    path = os.path.join("assets", "en", f"{screen}.jpg")

    try:
        if os.path.exists(path):
            try:
                photo = FSInputFile(path)
                await bot.send_photo(chat_id, photo, caption=text, reply_markup=kb)
                return
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Failed to send photo %s to chat %s: %s",
                    path,
                    chat_id,
                    e,
                )

        # если фотка не отправилась или её нет — шлём просто текст
        await bot.send_message(chat_id, text, reply_markup=kb)

    except (TelegramBadRequest, TelegramForbidden) as e:
        # сюда попадает, например, "Bad Request: chat not found"
        logger.warning(
            "Cannot deliver screen %s to chat %s: %s",
            screen,
            chat_id,
            e,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "Unexpected error sending screen %s to chat %s: %s",
            screen,
            chat_id,
            e,
        )


async def _send_deposit_screen_to_user(
    bot: Bot,
    tenant: Tenant,
    user_id: int,
    lang: str,
) -> None:
    dep_url = tenant.deposit_link or "https://t.me"
    text = f"{t_user(lang, 'dep_title')}\n\n{t_user(lang, 'dep_body')}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t_user(lang, "btn_deposit"),
                    url=dep_url,
                )
            ],
            [
                InlineKeyboardButton(
                    text=t_user(lang, "back_to_menu"),
                    callback_data="menu:back",
                )
            ],
        ]
    )
    await _send_screen_with_photo_to_user(bot, user_id, lang, "deposit", text, kb)


async def _send_access_open_screen_to_user(
    bot: Bot,
    tenant: Tenant,
    user_id: int,
    lang: str,
) -> None:
    support_url = tenant.support_url or settings.default_support_url
    text = f"{t_user(lang, 'access_title')}\n\n{t_user(lang, 'access_body')}"
    row1 = [
        InlineKeyboardButton(
            text=t_user(lang, "btn_open_app"),
            callback_data="signal:open_app",
        )
    ]
    row2 = []
    if support_url:
        row2.append(
            InlineKeyboardButton(
                text=t_user(lang, "btn_support"),
                url=support_url,
            )
        )
    row2.append(
        InlineKeyboardButton(
            text=t_user(lang, "back_to_menu"),
            callback_data="menu:back",
        )
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[row1, row2])
    await _send_screen_with_photo_to_user(bot, user_id, lang, "access", text, kb)


async def _get_or_create_user_access_for_click(
    tenant: Tenant,
    click_id: str,
    trader_id: Optional[str] = None,
) -> Optional[UserAccess]:
    """
    click_id = tg_id пользователя.
    """
    try:
        tg_id = int(click_id)
    except ValueError:
        logger.warning("Invalid click_id (not int): %s", click_id)
        tg_id = None

    async with SessionLocal() as session:
        ua: Optional[UserAccess] = None

        if tg_id is not None:
            res = await session.execute(
                select(UserAccess).where(
                    UserAccess.tenant_id == tenant.id,
                    UserAccess.user_id == tg_id,
                )
            )
            ua = res.scalar_one_or_none()

        if ua is None and tg_id is not None:
            ua = UserAccess(
                tenant_id=tenant.id,
                user_id=tg_id,
                click_id=click_id,
                trader_id=trader_id,
            )
            session.add(ua)
        elif ua is not None:
            ua.click_id = click_id
            if trader_id:
                ua.trader_id = trader_id

        await session.commit()
        if ua is not None:
            await session.refresh(ua)

    return ua


@router.get("/{code}/reg")
@router.post("/{code}/reg")
async def postback_reg(
    code: str,
    click_id: str = Query(...),
    trader_id: str = Query(...),
):
    """
    Постбэк регистрации.
    """
    tenant = await _get_tenant_by_code(code)
    if tenant is None:
        logger.warning("postback_reg: tenant not found for code=%s", code)
        return {"status": "error", "reason": "tenant_not_found"}

    ua = await _get_or_create_user_access_for_click(tenant, click_id, trader_id)
    if ua is None or ua.user_id is None:
        return {"status": "ok"}  # сохранить факт всё равно смогли

    async with SessionLocal() as session:
        # обновляем флажок регистрации и trader_id
        res = await session.execute(
            select(UserAccess).where(
                UserAccess.tenant_id == tenant.id,
                UserAccess.user_id == ua.user_id,
            )
        )
        db_ua: UserAccess | None = res.scalar_one_or_none()
        if db_ua is not None:
            db_ua.is_registered = True
            db_ua.trader_id = trader_id

        event = Event(
            tenant_id=tenant.id,
            user_id=ua.user_id,
            click_id=click_id,
            trader_id=trader_id,
            kind="reg",
            amount=None,
        )
        session.add(event)

        try:
            await session.commit()
        except SQLAlchemyError as e:  # noqa: BLE001
            logger.exception("postback_reg DB error: %s", e)
            return {"status": "error", "reason": "db_error"}

    # присылаем шаг депозита
    lang = await _get_user_lang(tenant.id, ua.user_id)
    bot = Bot(
        token=tenant.bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    try:
        await _send_deposit_screen_to_user(bot, tenant, ua.user_id, lang)
    finally:
        await bot.session.close()

    return {"status": "ok"}


@router.get("/{code}/ftd")
@router.post("/{code}/ftd")
async def postback_ftd(
    code: str,
    click_id: str = Query(...),
    trader_id: str = Query(...),
    sumdep: float = Query(...),
):
    """
    Первый депозит (FTD).
    """
    tenant = await _get_tenant_by_code(code)
    if tenant is None:
        logger.warning("postback_ftd: tenant not found for code=%s", code)
        return {"status": "error", "reason": "tenant_not_found"}

    ua = await _get_or_create_user_access_for_click(tenant, click_id, trader_id)
    if ua is None or ua.user_id is None:
        return {"status": "ok"}

    async with SessionLocal() as session:
        res = await session.execute(
            select(UserAccess).where(
                UserAccess.tenant_id == tenant.id,
                UserAccess.user_id == ua.user_id,
            )
        )
        db_ua: UserAccess | None = res.scalar_one_or_none()
        if db_ua is not None:
            db_ua.has_deposit = True
            db_ua.trader_id = trader_id
            db_ua.total_deposits = (db_ua.total_deposits or 0) + float(sumdep)

        event = Event(
            tenant_id=tenant.id,
            user_id=ua.user_id,
            click_id=click_id,
            trader_id=trader_id,
            kind="ftd",
            amount=float(sumdep),
        )
        session.add(event)

        try:
            await session.commit()
        except SQLAlchemyError as e:  # noqa: BLE001
            logger.exception("postback_ftd DB error: %s", e)
            return {"status": "error", "reason": "db_error"}

    # присылаем окно "доступ открыт"
    lang = await _get_user_lang(tenant.id, ua.user_id)
    bot = Bot(
        token=tenant.bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    try:
        await _send_access_open_screen_to_user(bot, tenant, ua.user_id, lang)
    finally:
        await bot.session.close()

    return {"status": "ok"}


@router.get("/{code}/rd")
@router.post("/{code}/rd")
async def postback_rd(
    code: str,
    click_id: str = Query(...),
    trader_id: str = Query(...),
    sumdep: float = Query(...),
):
    """
    Повторный депозит.
    """
    tenant = await _get_tenant_by_code(code)
    if tenant is None:
        logger.warning("postback_rd: tenant not found for code=%s", code)
        return {"status": "error", "reason": "tenant_not_found"}

    ua = await _get_or_create_user_access_for_click(tenant, click_id, trader_id)
    if ua is None or ua.user_id is None:
        return {"status": "ok"}

    async with SessionLocal() as session:
        res = await session.execute(
            select(UserAccess).where(
                UserAccess.tenant_id == tenant.id,
                UserAccess.user_id == ua.user_id,
            )
        )
        db_ua: UserAccess | None = res.scalar_one_or_none()
        if db_ua is not None:
            db_ua.trader_id = trader_id
            db_ua.total_deposits = (db_ua.total_deposits or 0) + float(sumdep)

        event = Event(
            tenant_id=tenant.id,
            user_id=ua.user_id,
            click_id=click_id,
            trader_id=trader_id,
            kind="rd",
            amount=float(sumdep),
        )
        session.add(event)

        try:
            await session.commit()
        except SQLAlchemyError as e:  # noqa: BLE001
            logger.exception("postback_rd DB error: %s", e)
            return {"status": "error", "reason": "db_error"}

    # Дополнительно можем ещё раз отправить "доступ открыт" (на случай,
    # если человек пополнил счёт позже).
    lang = await _get_user_lang(tenant.id, ua.user_id)
    bot = Bot(
        token=tenant.bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    try:
        await _send_access_open_screen_to_user(bot, tenant, ua.user_id, lang)
    finally:
        await bot.session.close()

    return {"status": "ok"}