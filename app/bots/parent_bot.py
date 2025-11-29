import asyncio
import logging
import re
from typing import List, Optional

from aiogram import Bot, Dispatcher, Router, F
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.client.default import DefaultBotProperties

from sqlalchemy import select

from app.settings import settings
from app.db import SessionLocal
from app.models import Tenant, UserAccess

logger = logging.getLogger("pocket_saas.parent")

router = Router()

# Простейшая проверка формата токена бота
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_\-]{20,}$")

# кто сейчас вводит текст рассылки
waiting_broadcast: set[int] = set()


# --- helpers ---------------------------------------------------------------


def _is_ga(user_id: int) -> bool:
    """Пользователь является глобальным админом (GA)?"""
    is_admin = user_id in settings.ga_admin_ids
    if not is_admin:
        # чтобы можно было понять по логам, кто стучится в /stas
        logger.warning(
            "User %s is not in GA_ADMIN_IDS %s",
            user_id,
            settings.ga_admin_ids,
        )
    return is_admin


async def _is_channel_member(bot: Bot, user_id: int) -> bool:
    """
    Проверяем, состоит ли пользователь в приватном канале.

    Если бот не может получить информацию о канале (не админ / неверный ID),
    то *не* режем доступ, чтобы не сломать работу.
    """
    try:
        member = await bot.get_chat_member(settings.private_channel_id, user_id)
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.warning("Cannot check channel membership: %s", e)
        # В спорных случаях считаем, что можно пускать
        return True

    status = getattr(member, "status", None)
    # всё, что не left/kicked — считаем членом
    return status not in ("left", "kicked")


async def _get_owner_tenant(owner_id: int) -> Optional[Tenant]:
    """Возвращаем единственного тенанта владельца (если есть)."""
    async with SessionLocal() as session:
        res = await session.execute(
            select(Tenant)
            .where(Tenant.owner_telegram_id == owner_id)
            .order_by(Tenant.id.asc())
        )
        return res.scalar_one_or_none()


async def _save_tenant(owner_id: int, token: str, username: Optional[str]) -> Tenant:
    """
    Создаём или обновляем ТОЛЬКО ОДНОГО тенанта на человека.

    Если у owner уже есть запись — обновляем в ней токен/username.
    """
    async with SessionLocal() as session:
        res = await session.execute(
            select(Tenant)
            .where(Tenant.owner_telegram_id == owner_id)
            .order_by(Tenant.id.asc())
        )
        tenant: Optional[Tenant] = res.scalar_one_or_none()

        if tenant:
            tenant.bot_token = token
            tenant.bot_username = username
            tenant.is_active = True
        else:
            tenant = Tenant(
                owner_telegram_id=owner_id,
                bot_token=token,
                bot_username=username,
                support_url=settings.default_support_url,
            )
            session.add(tenant)

        await session.commit()
        await session.refresh(tenant)
        return tenant


async def _test_bot_token(token: str) -> tuple[bool, Optional[str]]:
    """
    Проверяем токен, пробуем получить username бота.
    """
    test_bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    try:
        me = await test_bot.get_me()
        return True, me.username
    except Exception as e:  # noqa: BLE001
        logger.warning("Bot token validation failed: %s", e)
        return False, None
    finally:
        await test_bot.session.close()


async def _list_all_active_tenant_user_ids() -> List[int]:
    """
    Все уникальные user_id всех пользователей всех активных тенантов.
    """
    async with SessionLocal() as session:
        res_t = await session.execute(
            select(Tenant.id).where(Tenant.is_active == True)  # noqa: E712
        )
        tenant_ids = [row[0] for row in res_t.all()]

        if not tenant_ids:
            return []

        res_u = await session.execute(
            select(UserAccess.user_id)
            .where(UserAccess.tenant_id.in_(tenant_ids))
            .distinct()
        )
        return [row[0] for row in res_u.all()]


# --- handlers: подключение тенанта ----------------------------------------


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    bot = message.bot
    user = message.from_user
    if user is None:
        return

    # проверяем членство в приватном канале
    if not await _is_channel_member(bot, user.id):
        await message.answer(
            "❌ Доступ только для участников приватного канала.\n"
            "Добавься в канал и попробуй снова."
        )
        return

    tenant = await _get_owner_tenant(user.id)

    if tenant:
        link = f"https://t.me/{tenant.bot_username}" if tenant.bot_username else "—"
        await message.answer(
            "У тебя уже подключен бот 👇\n\n"
            f"ID: <code>{tenant.id}</code>\n"
            f"Username: <b>{tenant.bot_username or '—'}</b>\n"
            f"Ссылка: {link}\n\n"
            "Если ты отправишь новый токен, я обновлю этого же бота "
            "(по-прежнему только 1 бот на человека)."
        )
    else:
        await message.answer(
            "👋 Привет!\n\n"
            "Ты можешь подключить <b>один</b> бот.\n\n"
            "Просто отправь сюда токен своего Telegram-бота (из BotFather), "
            "и я привяжу его к твоему аккаунту."
        )


async def _handle_new_bot_token(message: Message, token: str) -> None:
    bot = message.bot
    user = message.from_user
    if user is None:
        return

    # ещё раз проверяем членство, если юзер сразу прислал токен без /start
    if not await _is_channel_member(bot, user.id):
        await message.answer(
            "❌ Ты не можешь подключить бота, так как не состоишь в приватном канале."
        )
        return

    ok, username = await _test_bot_token(token)
    if not ok:
        await message.answer("❌ Не удалось проверить токен. Проверь, что он верный.")
        return

    tenant = await _save_tenant(
        owner_id=user.id,
        token=token,
        username=username,
    )

    if username:
        link = f"https://t.me/{username}"
        await message.answer(
            "✅ Бот подключён.\n\n"
            f"ID: <code>{tenant.id}</code>\n"
            f"Ссылка: {link}\n\n"
            "Помни: ты можешь иметь только одного бота. "
            "Если пришлёшь другой токен, я обновлю текущего."
        )
    else:
        await message.answer(
            "✅ Бот подключён, но username получить не удалось.\n\n"
            f"ID: <code>{tenant.id}</code>\n\n"
            "Помни: ты можешь иметь только одного бота. "
            "Если пришлёшь другой токен, я обновлю текущего."
        )


# --- handlers: админка /stas ----------------------------------------------


@router.message(Command("stas"))
async def cmd_stas(message: Message) -> None:
    """
    Главная админка (только для GA).
    Через неё запускаем рассылку по всем пользователям всех тенантов.
    """
    user = message.from_user
    if user is None:
        return

    if not _is_ga(user.id):
        await message.answer("❌ Эта команда только для владельца.")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 Сделать рассылку по всем пользователям",
                    callback_data="adm:broadcast",
                )
            ],
        ]
    )

    await message.answer(
        "👑 Главное меню админа.\n\n"
        "Пока здесь только один пункт — глобальная рассылка по всем пользователям "
        "всех активных тенантов.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "adm:broadcast")
async def cb_adm_broadcast(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        await call.answer()
        return

    if not _is_ga(user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    waiting_broadcast.add(user.id)
    await call.message.answer(
        "✏️ Отправь текст рассылки одним сообщением.\n\n"
        "Как только получу текст — начну отправку по всем пользователям."
    )
    await call.answer()  # закрываем кружочек


# --- fallback: текстовые сообщения ----------------------------------------


@router.message(F.text)
async def handle_text(message: Message) -> None:
    text = (message.text or "").strip()
    if not text:
        return

    user = message.from_user
    if user is None:
        return
    user_id = user.id

    # 1) если админ сейчас в режиме ввода рассылки
    if user_id in waiting_broadcast and _is_ga(user_id):
        waiting_broadcast.discard(user_id)

        body = text
        user_ids = await _list_all_active_tenant_user_ids()
        if not user_ids:
            await message.answer(
                "Нет ни одного пользователя в активных ботах — рассылать некому."
            )
            return

        await message.answer(
            f"Начинаю рассылку по <b>{len(user_ids)}</b> пользователям…"
        )

        sent = 0
        failed = 0
        for uid in user_ids:
            try:
                await message.bot.send_message(uid, body)
                sent += 1
                await asyncio.sleep(0.05)
            except TelegramForbiddenError:
                failed += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                logger.warning("Broadcast send error to %s: %s", uid, e)

        await message.answer(
            "Рассылка завершена.\n"
            f"✅ Успешно: <b>{sent}</b>\n"
            f"⚠️ Ошибок: <b>{failed}</b>"
        )
        return

    # 2) для всех остальных — попытка принять токен бота
    if TOKEN_RE.match(text):
        await _handle_new_bot_token(message, text)
        return

    # 3) просто подсказка
    await message.answer(
        "❓ Не понял сообщение.\n\n"
        "Чтобы подключить бота — отправь сюда его токен из BotFather.\n\n"
        "Админ может открыть панель командой /stas."
    )


# --- entrypoint ------------------------------------------------------------


async def run_parent_bot() -> None:
    """
    Запуск parent-бота.
    """
    bot = Bot(
        token=settings.parent_bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Starting parent bot polling")
    await dp.start_polling(bot)