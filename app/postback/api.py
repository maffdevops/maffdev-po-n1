import logging
import os
from typing import Optional

from fastapi import APIRouter, Query
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionLocal
from app.models import Tenant, UserAccess, UserLang, Event
from app.settings import settings
from app.bots.child.texts import t_user

logger = logging.getLogger("pocket_saas.postback")

router = APIRouter(prefix="/pb", tags=["postback"])


async def _get_tenant_by_code(code: str) -> Optional[Tenant]:
    """
    code может быть:
    - pb_secret
    - tn{id} (например, tn1)
    """
    async with SessionLocal() as session:
        # 1) пробуем по pb_secret
        res = await session.execute(
            select(Tenant).where(Tenant.pb_secret == code)
        )
        tenant = res.scalar_one_or_none()
        if tenant:
            return tenant

        # 2) если не нашли — tn{id}
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


async def _safe_send_message(
    bot: Bot,
    chat_id: int,
    text: str,
    kb: Optional[InlineKeyboardMarkup] = None,
) -> None:
    try:
        await bot.send_message(chat_id, text, reply_markup=kb)
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.warning("Failed to send message to chat %s: %s", chat_id, e)


async def _send_screen_with_photo_to_user(
    bot: Bot,
    chat_id: int,
    lang: str,
    screen: str,
    text: str,
    kb: Optional[InlineKeyboardMarkup] = None,
) -> None:
    """
    Пытаемся отправить картинку из assets/<lang>/<screen>.jpg,
    если нет — из assets/<settings.lang_default>/..., если нет —
    просто текстом.
    """
    langs_to_try = []
    for l in (lang, settings.lang_default, "en"):
        if l not in langs_to_try:
            langs_to_try.append(l)

    for l in langs_to_try:
        path = os.path.join("assets", l, f"{screen}.jpg")
        if os.path.exists(path):
            try:
                photo = FSInputFile(path)
                await bot.send_photo(chat_id, photo, caption=text, reply_markup=kb)
                return
            except (TelegramBadRequest, TelegramForbiddenError) as e:
                logger.warning(
                    "Failed to send photo %s to chat %s: %s", path, chat_id, e
                )
                # если телеграм не даёт отправить в чат — выходим
                return
            except Exception as e:  # noqa: BLE001
                logger.warning("Unexpected error sending photo %s: %s", path, e)
                break  # падаем на текстовую отправку

    # если не смогли картинку — просто текст
    await _safe_send_message(bot, chat_id, text, kb)


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
    Привязка постбэка к пользователю.

    Логика:
    1. Пытаемся трактовать click_id как tg_id (int) и ищем по user_id.
    2. Если не нашли или click_id не int — ищем по click_id (строкой).
    3. Если вообще ничего нет, но tg_id есть — создаём новую запись.
    """
    try:
        tg_id = int(click_id)
    except ValueError:
        logger.warning("Invalid click_id (not int): %s", click_id)
        tg_id = None

    logger.info(
        "get_or_create_user_access: tenant_id=%s click_id=%s tg_id=%s trader_id=%s",
        tenant.id,
        click_id,
        tg_id,
        trader_id,
    )

    async with SessionLocal() as session:
        ua: Optional[UserAccess] = None

        # 1. Если есть tg_id — пробуем найти по user_id
        if tg_id is not None:
            res = await session.execute(
                select(UserAccess).where(
                    UserAccess.tenant_id == tenant.id,
                    UserAccess.user_id == tg_id,
                )
            )
            ua = res.scalar_one_or_none()

        # 2. Если не нашли — пробуем по click_id (строкой)
        if ua is None:
            res = await session.execute(
                select(UserAccess).where(
                    UserAccess.tenant_id == tenant.id,
                    UserAccess.click_id == click_id,
                )
            )
            ua = res.scalar_one_or_none()

        # 3. Если ничего не нашли, но есть tg_id — создаём новую запись
        if ua is None and tg_id is not None:
            ua = UserAccess(
                tenant_id=tenant.id,
                user_id=tg_id,
                click_id=click_id,
                trader_id=trader_id,
            )
            session.add(ua)
        elif ua is not None:
            # Обновляем click_id и trader_id, если нужно
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
    logger.info(
        "postback_reg: code=%s click_id=%s trader_id=%s",
        code,
        click_id,
        trader_id,
    )

    tenant = await _get_tenant_by_code(code)
    if tenant is None:
        logger.warning("postback_reg: tenant not found for code=%s", code)
        return {"status": "error", "reason": "tenant_not_found"}

    ua = await _get_or_create_user_access_for_click(tenant, click_id, trader_id)
    if ua is None or ua.user_id is None:
        logger.warning(
            "postback_reg: user_access not found or user_id is None "
            "(tenant_id=%s, click_id=%s)",
            tenant.id,
            click_id,
        )
        return {"status": "ok"}

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
            db_ua.click_id = click_id

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

    # решаем, что слать пользователю
    lang = await _get_user_lang(tenant.id, ua.user_id)
    bot = Bot(
        token=tenant.bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )

    # флаг проверки депозита: если в Tenant есть поле deposit_required — используем его,
    # иначе по умолчанию считаем, что депозит нужен.
    deposit_required = getattr(tenant, "deposit_required", True)

    try:
        if deposit_required:
            await _send_deposit_screen_to_user(bot, tenant, ua.user_id, lang)
        else:
            # проверка депозита выключена — сразу открываем доступ
            await _send_access_open_screen_to_user(bot, tenant, ua.user_id, lang)
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
    logger.info(
        "postback_ftd: code=%s click_id=%s trader_id=%s sumdep=%s",
        code,
        click_id,
        trader_id,
        sumdep,
    )

    tenant = await _get_tenant_by_code(code)
    if tenant is None:
        logger.warning("postback_ftd: tenant not found for code=%s", code)
        return {"status": "error", "reason": "tenant_not_found"}

    ua = await _get_or_create_user_access_for_click(tenant, click_id, trader_id)
    if ua is None or ua.user_id is None:
        logger.warning(
            "postback_ftd: user_access not found or user_id is None "
            "(tenant_id=%s, click_id=%s)",
            tenant.id,
            click_id,
        )
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
            db_ua.click_id = click_id
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

    # после FTD всегда шлём "доступ открыт"
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
    Повторный депозит (re-deposit).
    """
    logger.info(
        "postback_rd: code=%s click_id=%s trader_id=%s sumdep=%s",
        code,
        click_id,
        trader_id,
        sumdep,
    )

    tenant = await _get_tenant_by_code(code)
    if tenant is None:
        logger.warning("postback_rd: tenant not found for code=%s", code)
        return {"status": "error", "reason": "tenant_not_found"}

    ua = await _get_or_create_user_access_for_click(tenant, click_id, trader_id)
    if ua is None or ua.user_id is None:
        logger.warning(
            "postback_rd: user_access not found or user_id is None "
            "(tenant_id=%s, click_id=%s)",
            tenant.id,
            click_id,
        )
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
            db_ua.click_id = click_id
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

    # Можно ещё раз отправить "доступ открыт" (человек задепал позже)
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