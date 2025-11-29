import asyncio
import datetime as dt
import logging
import re
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional

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

from sqlalchemy import select, delete

from app.settings import settings
from app.db import SessionLocal
from app.models import Tenant, UserAccess, UserLang, Event

logger = logging.getLogger("pocket_saas.parent")

router = Router()

# Простейшая проверка формата токена бота
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_\-]{20,}$")

# состояние продвинутой рассылки для /stas
# stas_bc_state[admin_id] = {
#     "mode": "global" | "tenant",
#     "tenant_id": Optional[int],
#     "stage": "await_post" | "await_time_value" | ...,
#     "current_post": {"text": str, "media": Optional[dict]},
#     "admin_chat_id": int,
# }
stas_bc_state: Dict[int, Dict[str, Any]] = {}


# --- helpers общие ---------------------------------------------------------


def _is_ga(user_id: int) -> bool:
    """Пользователь является глобальным админом (GA)?"""
    return user_id in settings.ga_admin_ids


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


async def _get_owner_tenant(owner_id: int) -> Tenant | None:
    """Возвращаем единственного тенанта владельца (если есть)."""
    async with SessionLocal() as session:
        res = await session.execute(
            select(Tenant)
            .where(Tenant.owner_telegram_id == owner_id)
            .order_by(Tenant.id.asc())
        )
        return res.scalar_one_or_none()


async def _save_tenant(owner_id: int, token: str, username: str | None) -> Tenant:
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
        tenant: Tenant | None = res.scalar_one_or_none()

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


async def _test_bot_token(token: str) -> tuple[bool, str | None]:
    """
    Проверяем токен, пробуем получить username бота.
    """
    test_bot = Bot(token=token)
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


async def _list_tenant_user_ids(tenant_id: int) -> List[int]:
    """
    Все уникальные user_id одного конкретного тенанта.
    """
    async with SessionLocal() as session:
        res_u = await session.execute(
            select(UserAccess.user_id)
            .where(UserAccess.tenant_id == tenant_id)
            .distinct()
        )
        return [row[0] for row in res_u.all()]


async def _delete_tenant_completely(tenant_id: int) -> None:
    """
    Полное удаление тенанта со всеми пользователями и событиями.

    Удаляем:
    - UserAccess
    - UserLang
    - Event
    - сам Tenant
    """
    async with SessionLocal() as session:
        await session.execute(
            delete(UserAccess).where(UserAccess.tenant_id == tenant_id)
        )
        await session.execute(
            delete(UserLang).where(UserLang.tenant_id == tenant_id)
        )
        await session.execute(
            delete(Event).where(Event.tenant_id == tenant_id)
        )
        await session.execute(
            delete(Tenant).where(Tenant.id == tenant_id)
        )
        await session.commit()


async def _build_tenant_card_text(bot: Bot, tenant: Tenant) -> str:
    """
    Текст карточки тенанта для /stas -> Клиенты.

    Показываем:
    - ID тенанта
    - owner_telegram_id
    - username владельца (если вдруг есть в Telegram)
    - username бота
    - флаги is_active / check_subscription / check_deposit
    - основные ссылки
    """
    owner_username = "—"
    owner_name = "—"

    if tenant.owner_telegram_id:
        try:
            chat = await bot.get_chat(tenant.owner_telegram_id)
            if getattr(chat, "username", None):
                owner_username = f"@{chat.username}"
            if getattr(chat, "full_name", None):
                owner_name = chat.full_name
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to fetch owner info for tenant %s: %s",
                tenant.id,
                e,
            )

    bot_username = tenant.bot_username or "—"
    bot_link = f"https://t.me/{bot_username}" if tenant.bot_username else "—"

    text = (
        "👤 Клиент (тенант)\n\n"
        f"ID тенанта: <code>{tenant.id}</code>\n"
        f"Owner TG ID: <code>{tenant.owner_telegram_id or '—'}</code>\n"
        f"Owner username: {owner_username}\n"
        f"Owner name: {owner_name}\n\n"
        f"Bot username: @{bot_username}\n"
        f"Ссылка на бота: {bot_link}\n"
        f"Активен: <b>{'да' if tenant.is_active else 'нет'}</b>\n\n"
        f"Поддержка: {tenant.support_url or settings.default_support_url or '—'}\n"
        f"Реф ссылка: {tenant.ref_link or '—'}\n"
        f"Ссылка на депозит: {tenant.deposit_link or '—'}\n"
        f"ID канала: <code>{tenant.gate_channel_id or '—'}</code>\n"
        f"URL канала: {tenant.gate_channel_url or '—'}\n\n"
        f"Проверять подписку: <b>{'да' if tenant.check_subscription else 'нет'}</b>\n"
        f"Проверять депозит: <b>{'да' if tenant.check_deposit else 'нет'}</b>\n"
    )
    return text


# --- helpers: продвинутая рассылка для /stas ------------------------------


async def _collect_bc_targets(mode: str, tenant_id: Optional[int]) -> List[int]:
    """
    Собираем список user_id для рассылки:
    - mode == "global"  -> все активные тенанты
    - mode == "tenant"  -> только один тенант
    """
    if mode == "global":
        return await _list_all_active_tenant_user_ids()
    if mode == "tenant" and tenant_id is not None:
        return await _list_tenant_user_ids(tenant_id)
    return []


async def _do_broadcast_post(
    bot: Bot,
    mode: str,
    tenant_id: Optional[int],
    text: str,
    media: Optional[dict],
) -> tuple[int, int]:
    """
    Отправка одного поста (одного сообщения) по выбранному сегменту.
    """
    user_ids = await _collect_bc_targets(mode, tenant_id)
    if not user_ids:
        return 0, 0

    sent = 0
    failed = 0

    for uid in user_ids:
        try:
            if media is None:
                await bot.send_message(uid, text or "")
            else:
                mtype = media.get("type")
                file_id = media.get("file_id")
                if mtype == "photo":
                    await bot.send_photo(uid, file_id, caption=text or None)
                elif mtype == "video":
                    await bot.send_video(uid, file_id, caption=text or None)
                elif mtype == "document":
                    await bot.send_document(uid, file_id, caption=text or None)
                elif mtype == "animation":
                    await bot.send_animation(uid, file_id, caption=text or None)
                elif mtype == "video_note":
                    await bot.send_video_note(uid, file_id)
                else:
                    await bot.send_message(uid, text or "")
            sent += 1
            await asyncio.sleep(0.05)
        except TelegramForbiddenError:
            failed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            logger.warning("Global/Tenant broadcast send error to %s: %s", uid, e)

    return sent, failed


async def _scheduled_broadcast_post(
    bot: Bot,
    admin_chat_id: int,
    mode: str,
    tenant_id: Optional[int],
    text: str,
    media: Optional[dict],
    delay_seconds: float,
) -> None:
    """
    Отправка ОДНОГО поста по таймеру.
    """
    try:
        await asyncio.sleep(delay_seconds)
        sent, failed = await _do_broadcast_post(bot, mode, tenant_id, text, media)
        await bot.send_message(
            admin_chat_id,
            (
                "⏰ Запланированный пост отправлен.\n"
                f"✅ Успешно: <b>{sent}</b>\n"
                f"⚠️ Ошибок: <b>{failed}</b>"
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Scheduled broadcast error: %s", e)


async def _start_broadcast_flow(
    message: Message,
    mode: str,
    tenant_id: Optional[int] = None,
) -> None:
    """
    Запустить диалог рассылки для GA:
    - mode: "global" / "tenant"
    """
    user = message.from_user
    if user is None:
        return

    if not _is_ga(user.id):
        await message.answer("Нет доступа.")
        return

    stas_bc_state[user.id] = {
        "mode": mode,
        "tenant_id": tenant_id,
        "stage": "await_post",
        "current_post": None,
        "admin_chat_id": message.chat.id,
    }

    target_text = "по всем пользователям всех активных тенантов"
    if mode == "tenant" and tenant_id is not None:
        target_text = f"по пользователям тенанта ID <code>{tenant_id}</code>"

    await message.answer(
        "✏️ Запускаем гибкую рассылку.\n\n"
        f"Сегмент: <b>{target_text}</b>.\n\n"
        "Отправь пост: текст одним сообщением\n"
        "и при желании медиа (фото/видео/док/гиф/кружок).\n\n"
        "Каждый пост можно отправить сразу или запланировать по времени (МСК)."
    )


async def _store_post_and_ask_time(
    message: Message,
    user_id: int,
    text: str,
    media: Optional[dict],
) -> None:
    """
    Сохраняем текущий пост и спрашиваем, когда отправить.
    """
    state = stas_bc_state.get(user_id)
    if not state:
        return

    state["current_post"] = {"text": text, "media": media}
    state["stage"] = "await_time_choice"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Отправить сейчас",
                    callback_data="stas:bc:time:now",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏰ Запланировать по времени (МСК)",
                    callback_data="stas:bc:time:later",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить рассылку",
                    callback_data="stas:bc:cancel",
                )
            ],
        ]
    )

    await message.answer("Когда отправить этот пост?", reply_markup=kb)


# --- handlers: подключение тенанта ----------------------------------------


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    bot = message.bot

    # проверяем членство в приватном канале
    if not await _is_channel_member(bot, message.from_user.id):
        await message.answer(
            "❌ Доступ только для участников приватного канала.\n"
            "Добавься в канал и попробуй снова."
        )
        return

    tenant = await _get_owner_tenant(message.from_user.id)

    if tenant:
        link = f"https://t.me/{tenant.bot_username}" if tenant.bot_username else "—"
        await message.answer(
            "У тебя уже подключен бот 👇\n\n"
            f"ID: <code>{tenant.id}</code>\n"
            f"Username бота: <b>{tenant.bot_username or '—'}</b>\n"
            f"Ссылка: {link}\n\n"
            "Если ты отправишь новый токен, я обновлю этого же бота "
            "(по-прежнему только 1 бот на человека)."
        )
    else:
        await message.answer(
            "👋 Привет!\n\n"
            "Ты можешь подключить *один* бот.\n\n"
            "Просто отправь сюда токен своего Telegram-бота (из BotFather), "
            "и я привяжу его к твоему аккаунту."
        )


async def _handle_new_bot_token(message: Message, token: str) -> None:
    bot = message.bot

    # ещё раз проверяем членство, если юзер сразу прислал токен без /start
    if not await _is_channel_member(bot, message.from_user.id):
        await message.answer(
            "❌ Ты не можешь подключить бота, так как не состоишь в приватном канале."
        )
        return

    ok, username = await _test_bot_token(token)
    if not ok:
        await message.answer("❌ Не удалось проверить токен. Проверь, что он верный.")
        return

    tenant = await _save_tenant(
        owner_id=message.from_user.id,
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
            "✅ Бот подключён, но username получить не удалось.\n"
            f"ID: <code>{tenant.id}</code>\n\n"
            "Помни: ты можешь иметь только одного бота. "
            "Если пришлёшь другой токен, я обновлю текущего."
        )


# --- handlers: админка /stas ----------------------------------------------


@router.message(Command("stas"))
async def cmd_stas(message: Message) -> None:
    """
    Главная админка (только для GA).
    Тут:
    - гибкая глобальная рассылка
    - рассылка по конкретному тенанту
    - список клиентов (тенантов)
    """
    if not _is_ga(message.from_user.id):
        await message.answer("❌ Эта команда только для владельца.")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 Глобальная рассылка (все пользователи)",
                    callback_data="stas:bc:global",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Рассылка по тенанту",
                    callback_data="stas:bc:tenant_menu",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Клиенты (тенанты)",
                    callback_data="stas:clients",
                )
            ],
        ]
    )

    await message.answer(
        "👑 Главное меню админа.\n\n"
        "Ты можешь:\n"
        "• сделать гибкую рассылку по всем пользователям;\n"
        "• выбрать конкретный тенант и отправить рассылку только его клиентам;\n"
        "• посмотреть список клиентов (тенантов) и их карточки, а также удалить тенант полностью.",
        reply_markup=kb,
    )


# --- callbacks: старая кнопка adm:broadcast (совместимость) ---------------


@router.callback_query(F.data == "adm:broadcast")
async def cb_old_adm_broadcast(call: CallbackQuery) -> None:
    """
    Поддержка старой кнопки с callback_data="adm:broadcast".
    Запускаем новую гибкую глобальную рассылку.
    """
    user_id = call.from_user.id
    if not _is_ga(user_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    await _start_broadcast_flow(call.message, mode="global")
    await call.answer()


# --- callbacks: меню /stas -------------------------------------------------


@router.callback_query(F.data == "stas:bc:global")
async def cb_stas_bc_global(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    if not _is_ga(user_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    await _start_broadcast_flow(call.message, mode="global")
    await call.answer()


@router.callback_query(F.data == "stas:bc:tenant_menu")
async def cb_stas_bc_tenant_menu(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    if not _is_ga(user_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    async with SessionLocal() as session:
        res = await session.execute(
            select(Tenant).order_by(Tenant.id.asc())
        )
        tenants = list(res.scalars().all())

    if not tenants:
        await call.message.edit_text("Тенантов пока нет.")
        await call.answer()
        return

    kb_rows: List[List[InlineKeyboardButton]] = []
    for t in tenants:
        title = f"ID {t.id} — @{t.bot_username or 'no_username'}"
        kb_rows.append(
            [
                InlineKeyboardButton(
                    text=title,
                    callback_data=f"stas:bc:tenant:{t.id}",
                )
            ]
        )
    kb_rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ В меню /stas",
                callback_data="stas:back",
            )
        ]
    )

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await call.message.edit_text(
        "Выбери тенант для рассылки:",
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(F.data.startswith("stas:bc:tenant:"))
async def cb_stas_bc_tenant(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    if not _is_ga(user_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    parts = call.data.split(":")
    try:
        tenant_id = int(parts[-1])
    except ValueError:
        await call.answer("Некорректный tenant_id", show_alert=True)
        return

    await _start_broadcast_flow(call.message, mode="tenant", tenant_id=tenant_id)
    await call.answer()


@router.callback_query(F.data == "stas:back")
async def cb_stas_back(call: CallbackQuery) -> None:
    """
    Просто перерисовываем меню /stas.
    """
    await cmd_stas(call.message)
    await call.answer()


# --- callbacks: управление рассылкой (тайминг, ещё пост, отмена) ----------


@router.callback_query(F.data.startswith("stas:bc:time:"))
async def cb_stas_bc_time(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    if not _is_ga(user_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    state = stas_bc_state.get(user_id)
    if not state:
        await call.answer("Нет активной рассылки", show_alert=True)
        return

    choice = call.data.split(":")[-1]
    post = state.get("current_post") or {}
    text = str(post.get("text") or "")
    media = post.get("media")

    if not text and not media:
        await call.answer("Нет сохранённого поста", show_alert=True)
        return

    mode = str(state.get("mode"))
    tenant_id = state.get("tenant_id")

    if choice == "now":
        sent, failed = await _do_broadcast_post(
            call.message.bot,
            mode,
            tenant_id,
            text,
            media,  # type: ignore[arg-type]
        )
        state["current_post"] = None
        state["stage"] = "await_post"

        await call.message.answer(
            "Пост отправлен.\n"
            f"✅ Успешно: <b>{sent}</b>\n"
            f"⚠️ Ошибок: <b>{failed}</b>\n\n"
            "Хочешь добавить ещё один пост для этой же рассылки?",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="➕ Добавить ещё пост",
                            callback_data="stas:bc:more:yes",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="✅ Закончить рассылку",
                            callback_data="stas:bc:more:no",
                        )
                    ],
                ]
            ),
        )
        await call.answer()
        return

    if choice == "later":
        state["stage"] = "await_time_value"
        await call.message.answer(
            "Отправь время по МСК в формате ЧЧ:ММ, например 15:30.\n"
            "Если время уже прошло — отправим на следующий день."
        )
        await call.answer()
        return

    await call.answer("Неизвестный выбор времени", show_alert=True)


@router.callback_query(F.data.startswith("stas:bc:more:"))
async def cb_stas_bc_more(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    if not _is_ga(user_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    state = stas_bc_state.get(user_id)
    if not state:
        await call.answer("Нет активной рассылки", show_alert=True)
        return

    choice = call.data.split(":")[-1]

    if choice == "yes":
        state["stage"] = "await_post"
        state["current_post"] = None
        await call.message.answer(
            "Ок, отправь следующий пост: текст и, при желании, медиа."
        )
        await call.answer()
        return

    if choice == "no":
        stas_bc_state.pop(user_id, None)
        await call.message.answer("Рассылка завершена ✅")
        await call.answer()
        return

    await call.answer("Неизвестная команда", show_alert=True)


@router.callback_query(F.data == "stas:bc:cancel")
async def cb_stas_bc_cancel(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    if not _is_ga(user_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    stas_bc_state.pop(user_id, None)
    await call.message.answer("Рассылка отменена.")
    await call.answer()


# --- callbacks: клиенты (тенанты) -----------------------------------------


@router.callback_query(F.data == "stas:clients")
async def cb_stas_clients(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    if not _is_ga(user_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    async with SessionLocal() as session:
        res = await session.execute(
            select(Tenant).order_by(Tenant.id.asc())
        )
        tenants = list(res.scalars().all())

    if not tenants:
        await call.message.edit_text(
            "Клиентов (тенантов) пока нет.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ В меню /stas",
                            callback_data="stas:back",
                        )
                    ]
                ]
            ),
        )
        await call.answer()
        return

    kb_rows: List[List[InlineKeyboardButton]] = []
    for t in tenants:
        title = f"ID {t.id} — @{t.bot_username or 'no_username'}"
        kb_rows.append(
            [
                InlineKeyboardButton(
                    text=title,
                    callback_data=f"stas:client:show:{t.id}",
                )
            ]
        )
    kb_rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ В меню /stas",
                callback_data="stas:back",
            )
        ]
    )

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    await call.message.edit_text(
        "Список клиентов (тенантов).\n\n"
        "Нажми на нужного, чтобы открыть карточку.",
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(F.data.startswith("stas:client:"))
async def cb_stas_client(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    if not _is_ga(user_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    parts = call.data.split(":")
    if len(parts) < 4:
        await call.answer("Некорректная команда", show_alert=True)
        return

    action = parts[2]
    try:
        tenant_id = int(parts[3])
    except ValueError:
        await call.answer("Некорректный tenant_id", show_alert=True)
        return

    async with SessionLocal() as session:
        res = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant: Tenant | None = res.scalar_one_or_none()

    if tenant is None:
        await call.answer("Тенант не найден", show_alert=True)
        return

    if action == "show":
        text = await _build_tenant_card_text(call.message.bot, tenant)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🗑 Удалить тенанта полностью",
                        callback_data=f"stas:client:del:{tenant.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ К списку клиентов",
                        callback_data="stas:clients",
                    )
                ],
            ]
        )
        await call.message.edit_text(text, reply_markup=kb)
        await call.answer()
        return

    if action == "del":
        await _delete_tenant_completely(tenant_id)
        await call.message.edit_text(
            "Тенант и все его данные (пользователи, языки, события) удалены полностью. 🗑"
        )
        await call.answer("Удалено", show_alert=True)
        return

    await call.answer("Неизвестная команда", show_alert=True)


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

    # 1) если GA сейчас в режиме продвинутой рассылки
    state = stas_bc_state.get(user_id)
    if state is not None and _is_ga(user_id):
        stage = state.get("stage")

        # ждём текст поста (без медиа)
        if stage == "await_post":
            await _store_post_and_ask_time(
                message,
                user_id,
                text=text,
                media=None,
            )
            return

        # ждём время по МСК
        if stage == "await_time_value":
            try:
                hour_str, min_str = text.split(":", 1)
                hour = int(hour_str)
                minute = int(min_str)
                tz = ZoneInfo("Europe/Moscow")
                now_msk = dt.datetime.now(tz)
                target = now_msk.replace(
                    hour=hour,
                    minute=minute,
                    second=0,
                    microsecond=0,
                )
                if target <= now_msk:
                    target = target + dt.timedelta(days=1)
                delay = (target - now_msk).total_seconds()
            except Exception:  # noqa: BLE001
                await message.answer(
                    "Не получилось разобрать время. Отправь в формате ЧЧ:ММ, например 09:45."
                )
                return

            post = state.get("current_post") or {}
            post_text = str(post.get("text") or "")
            media = post.get("media")
            mode = str(state.get("mode"))
            tenant_id = state.get("tenant_id")
            admin_chat_id = int(state.get("admin_chat_id", message.chat.id))

            # сбрасываем текущий пост, но оставляем состояние для возможных следующих постов
            state["current_post"] = None
            state["stage"] = "await_post"

            asyncio.create_task(
                _scheduled_broadcast_post(
                    message.bot,
                    admin_chat_id,
                    mode,
                    tenant_id,
                    post_text,
                    media,  # type: ignore[arg-type]
                    delay,
                )
            )

            await message.answer(
                f"Пост запланирован на {text} по МСК ✅\n\n"
                "Можешь сразу отправить следующий пост для этой же рассылки "
                "или завершить её с помощью кнопки «Отменить рассылку»."
            )
            return

    # 2) для обычных юзеров и GA вне режима рассылки — обработка токена
    if TOKEN_RE.match(text):
        await _handle_new_bot_token(message, text)
        return

    # 3) просто подсказка
    await message.answer(
        "❓ Не понял сообщение.\n\n"
        "Чтобы подключить бота — отправь сюда его токен из BotFather.\n\n"
        "Админ может открыть панель командой /stas."
    )


# --- медиа: для рассылок /stas --------------------------------------------


@router.message(F.photo | F.video | F.document | F.animation | F.video_note)
async def handle_media(message: Message) -> None:
    """
    Медиа нам нужно только, если GA сейчас в режиме рассылки и мы ждём пост.
    Для обычных пользователей игнорируем.
    """
    user = message.from_user
    if user is None:
        return

    user_id = user.id
    state = stas_bc_state.get(user_id)
    if state is None or not _is_ga(user_id):
        # вне режима рассылки ничего не делаем
        return

    if state.get("stage") != "await_post":
        # медиа не ожидается
        return

    media: Optional[dict] = None
    if message.photo:
        file_id = message.photo[-1].file_id
        media = {"type": "photo", "file_id": file_id}
    elif message.video:
        media = {"type": "video", "file_id": message.video.file_id}
    elif message.document:
        media = {"type": "document", "file_id": message.document.file_id}
    elif message.animation:
        media = {"type": "animation", "file_id": message.animation.file_id}
    elif message.video_note:
        media = {"type": "video_note", "file_id": message.video_note.file_id}

    caption = (message.caption or "").strip()
    await _store_post_and_ask_time(
        message,
        user_id,
        text=caption,
        media=media,
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