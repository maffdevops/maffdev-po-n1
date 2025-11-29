import asyncio
import datetime as dt
import logging
import re
from zoneinfo import ZoneInfo
from typing import List, Optional, Dict, Any

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

from sqlalchemy import select, delete, func

from app.settings import settings
from app.db import SessionLocal
from app.models import Tenant, UserAccess, UserLang, Event

logger = logging.getLogger("pocket_saas.parent")

router = Router()

# Проверка формата токена бота
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_\-]{20,}$")

# Состояние рассылки для GA: admin_id -> state
ga_broadcast_state: Dict[int, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _is_ga(user_id: int) -> bool:
    """Пользователь является глобальным админом (GA)?"""
    is_admin = user_id in settings.ga_admin_ids
    if not is_admin:
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


async def _list_tenant_user_ids(tenant_id: int) -> List[int]:
    """Все пользователи одного тенанта."""
    async with SessionLocal() as session:
        res_u = await session.execute(
            select(UserAccess.user_id)
            .where(UserAccess.tenant_id == tenant_id)
            .distinct()
        )
        return [row[0] for row in res_u.all()]


async def _get_tenants_page(page: int, page_size: int = 5) -> tuple[List[Tenant], int]:
    """
    Возвращает список тенантов и общее количество страниц.
    """
    if page < 1:
        page = 1

    async with SessionLocal() as session:
        total = await session.scalar(
            select(func.count()).select_from(Tenant)
        ) or 0

        offset = (page - 1) * page_size
        res = await session.execute(
            select(Tenant)
            .order_by(Tenant.id.asc())
            .offset(offset)
            .limit(page_size)
        )
        tenants = list(res.scalars().all())

    total_pages = max(1, (total + page_size - 1) // page_size)
    return tenants, total_pages


async def _get_tenant_stats(tenant_id: int) -> tuple[int, int, int]:
    """
    Статистика по одному тенанту:
    - total_users
    - regs
    - deps
    """
    async with SessionLocal() as session:
        total_users = await session.scalar(
            select(func.count()).select_from(UserAccess).where(
                UserAccess.tenant_id == tenant_id
            )
        ) or 0

        regs = await session.scalar(
            select(func.count()).select_from(UserAccess).where(
                UserAccess.tenant_id == tenant_id,
                UserAccess.is_registered.is_(True),
            )
        ) or 0

        deps = await session.scalar(
            select(func.count()).select_from(UserAccess).where(
                UserAccess.tenant_id == tenant_id,
                UserAccess.has_deposit.is_(True),
            )
        ) or 0

    return total_users, regs, deps


async def _resolve_owner_username(bot: Bot, owner_id: int) -> Optional[str]:
    """
    Пытаемся получить username владельца по его Telegram ID.
    """
    try:
        chat = await bot.get_chat(owner_id)
        return getattr(chat, "username", None)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to resolve owner username %s: %s", owner_id, e)
        return None


async def _delete_tenant_with_all_data(tenant_id: int) -> bool:
    """
    Удаляем тенанта «с концами»:
    - UserAccess
    - UserLang
    - Event
    - Tenant
    """
    async with SessionLocal() as session:
        res = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant: Optional[Tenant] = res.scalar_one_or_none()
        if tenant is None:
            return False

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
        return True


# ---------------------------------------------------------------------------
# /start — подключение (или обновление) тенанта
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# /stas — главное GA-меню
# ---------------------------------------------------------------------------


@router.message(Command("stas"))
async def cmd_stas(message: Message) -> None:
    """
    Главная админка (только для GA).
    """
    user = message.from_user
    if user is None:
        return

    if not _is_ga(user.id):
        await message.answer("❌ Эта команда только для владельцев (GA).")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 Рассылка всем пользователям",
                    callback_data="ga:bc_all",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📤 Рассылка по тенанту",
                    callback_data="ga:bc_select_tenant",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Клиенты",
                    callback_data="ga:tenants:1",
                )
            ],
        ]
    )

    await message.answer(
        "👑 Главное меню глобального админа.\n\n"
        "• «Рассылка всем пользователям» — по всем юзерам всех активных ботов.\n"
        "• «Рассылка по тенанту» — только пользователям выбранного клиента.\n"
        "• «Клиенты» — список тенантов, карточки и удаление.",
        reply_markup=kb,
    )


# ---------------------------------------------------------------------------
# GA: работа с клиентами (тенантами)
# ---------------------------------------------------------------------------


async def _ga_show_tenants_page(
    call: CallbackQuery,
    page: int,
) -> None:
    user = call.from_user
    if user is None:
        await call.answer()
        return

    if not _is_ga(user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    tenants, total_pages = await _get_tenants_page(page)
    lines = [f"👥 Список клиентов (страница {page} из {total_pages})", ""]

    if not tenants:
        lines.append("Пока нет ни одного клиента.")
    else:
        for t in tenants:
            status = "✅ активен" if t.is_active else "⛔️ выключен"
            lines.append(
                f"ID <code>{t.id}</code> — @{t.bot_username or '—'} ({status})"
            )

    text = "\n".join(lines)

    kb_rows: List[List[InlineKeyboardButton]] = []

    # кнопки по каждому тенанту
    for t in tenants:
        kb_rows.append(
            [
                InlineKeyboardButton(
                    text=f"ID {t.id} (@{t.bot_username or '—'})",
                    callback_data=f"ga:tenant:{t.id}",
                )
            ]
        )

    # пагинация
    if total_pages > 1:
        pag_row: List[InlineKeyboardButton] = []
        if page > 1:
            pag_row.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"ga:tenants:{page-1}",
                )
            )
        pag_row.append(
            InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data=f"ga:tenants:{page}",
            )
        )
        if page < total_pages:
            pag_row.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"ga:tenants:{page+1}",
                )
            )
        kb_rows.append(pag_row)

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows or [[
        InlineKeyboardButton(text="Обновить", callback_data=f"ga:tenants:{page}")
    ]])

    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


async def _ga_show_tenant_card(call: CallbackQuery, tenant_id: int) -> None:
    user = call.from_user
    if user is None:
        await call.answer()
        return

    if not _is_ga(user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    async with SessionLocal() as session:
        res = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant: Optional[Tenant] = res.scalar_one_or_none()

    if tenant is None:
        await call.answer("Тенант не найден", show_alert=True)
        return

    total_users, regs, deps = await _get_tenant_stats(tenant.id)
    owner_username = await _resolve_owner_username(call.message.bot, tenant.owner_telegram_id)

    status = "✅ активен" if tenant.is_active else "⛔️ выключен"

    text_lines = [
        "👤 Клиент (тенант)",
        "",
        f"ID: <code>{tenant.id}</code>",
        f"Owner TG ID: <code>{tenant.owner_telegram_id}</code>",
        f"Owner username: @{owner_username or '—'}",
        f"Bot username: @{tenant.bot_username or '—'}",
        f"Статус: <b>{status}</b>",
        "",
        f"Всего пользователей: <b>{total_users}</b>",
        f"С регистрацией: <b>{regs}</b>",
        f"С депозитом: <b>{deps}</b>",
        "",
        f"Ссылка поддержки: {tenant.support_url or settings.default_support_url or '—'}",
        f"ID канала: <code>{tenant.gate_channel_id or '—'}</code>",
        f"URL канала: {tenant.gate_channel_url or '—'}",
        f"Реф-ссылка: {tenant.ref_link or '—'}",
        f"Ссылка депозита: {tenant.deposit_link or '—'}",
    ]

    text = "\n".join(text_lines)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Рассылка пользователям этого тенанта",
                    callback_data=f"ga:bc_tenant:{tenant.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Удалить клиента (с концами)",
                    callback_data=f"ga:tenantdel:{tenant.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К списку клиентов",
                    callback_data="ga:tenants:1",
                )
            ],
        ]
    )

    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


async def _ga_delete_tenant_handler(call: CallbackQuery, tenant_id: int) -> None:
    user = call.from_user
    if user is None:
        await call.answer()
        return

    if not _is_ga(user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    ok = await _delete_tenant_with_all_data(tenant_id)
    if not ok:
        await call.answer("Тенант не найден или уже удалён", show_alert=True)
        return

    await call.message.edit_text(
        f"✅ Тенант <code>{tenant_id}</code> и все его данные удалены."
    )
    await call.answer()


@router.callback_query(F.data.startswith("ga:tenants:"))
async def cb_ga_tenants(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        await call.answer()
        return
    if not _is_ga(user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    try:
        _, _, page_str = call.data.split(":", 2)
        page = int(page_str)
    except (ValueError, IndexError):
        page = 1

    await _ga_show_tenants_page(call, page)


@router.callback_query(F.data.startswith("ga:tenant:"))
async def cb_ga_tenant(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        await call.answer()
        return
    if not _is_ga(user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    try:
        _, _, tid_str = call.data.split(":", 2)
        tenant_id = int(tid_str)
    except (ValueError, IndexError):
        await call.answer("Некорректный tenant_id", show_alert=True)
        return

    await _ga_show_tenant_card(call, tenant_id)


@router.callback_query(F.data.startswith("ga:tenantdel:"))
async def cb_ga_tenantdel(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        await call.answer()
        return
    if not _is_ga(user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    try:
        _, tid_str = call.data.split("ga:tenantdel:")
        tenant_id = int(tid_str)
    except ValueError:
        await call.answer("Некорректный tenant_id", show_alert=True)
        return

    await _ga_delete_tenant_handler(call, tenant_id)


# ---------------------------------------------------------------------------
# GA: мощная рассылка (всем / по одному тенанту)
# ---------------------------------------------------------------------------


def _ga_bc_finish_msgs_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➡️ Перейти к настройке времени",
                    callback_data="ga:bc_done_msgs",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✖️ Отмена",
                    callback_data="ga:bc_cancel",
                )
            ],
        ]
    )


def _ga_bc_time_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Отправить сейчас",
                    callback_data="ga:bc_time:now",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏰ Запланировать по времени (МСК)",
                    callback_data="ga:bc_time:later",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✖️ Отмена",
                    callback_data="ga:bc_cancel",
                )
            ],
        ]
    )


async def _ga_bc_ask_time(message: Message) -> None:
    await message.answer(
        "Когда отправить рассылку?",
        reply_markup=_ga_bc_time_kb(),
    )


async def _collect_target_user_ids(target_type: str, tenant_id: Optional[int]) -> List[int]:
    if target_type == "all":
        return await _list_all_active_tenant_user_ids()
    if target_type == "tenant" and tenant_id is not None:
        return await _list_tenant_user_ids(tenant_id)
    return []


async def _ga_do_broadcast_posts(
    bot: Bot,
    admin_chat_id: int,
    target_type: str,
    tenant_id: Optional[int],
    messages: List[Dict[str, Any]],
) -> tuple[int, int]:
    """
    Фактическая отправка кампании (несколько постов) по целевой аудитории.
    """
    user_ids = await _collect_target_user_ids(target_type, tenant_id)
    if not user_ids:
        await bot.send_message(
            admin_chat_id,
            "Нет ни одного пользователя под выбранную аудиторию — рассылать некому.",
        )
        return 0, 0

    sent = 0
    failed = 0

    for uid in user_ids:
        for post in messages:
            text = str(post.get("text") or "")
            media = post.get("media")
            try:
                if media is None:
                    await bot.send_message(uid, text)
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
                    else:
                        await bot.send_message(uid, text)
                sent += 1
                await asyncio.sleep(0.05)
            except TelegramForbiddenError:
                failed += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                logger.warning("GA broadcast send error to %s: %s", uid, e)

    return sent, failed


async def _ga_scheduled_broadcast_posts(
    bot: Bot,
    admin_chat_id: int,
    target_type: str,
    tenant_id: Optional[int],
    messages: List[Dict[str, Any]],
    delay_seconds: float,
) -> None:
    try:
        await asyncio.sleep(delay_seconds)
        sent, failed = await _ga_do_broadcast_posts(
            bot,
            admin_chat_id,
            target_type,
            tenant_id,
            messages,
        )
        await bot.send_message(
            admin_chat_id,
            "Рассылка по кампании завершена.\n"
            f"✅ Отправлено сообщений: <b>{sent}</b>\n"
            f"⚠️ Ошибок: <b>{failed}</b>",
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Scheduled GA broadcast error: %s", e)


# --- вход в рассылки ---


@router.callback_query(F.data == "ga:bc_all")
async def cb_ga_bc_all(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        await call.answer()
        return
    uid = user.id

    if not _is_ga(uid):
        await call.answer("Нет доступа", show_alert=True)
        return

    # Сбрасываем старое состояние и начинаем новую кампанию
    ga_broadcast_state[uid] = {
        "target_type": "all",
        "tenant_id": None,
        "stage": "collect_msgs",
        "messages": [],
    }

    await call.message.answer(
        "Начинаем кампанию по <b>ВСЕМ пользователям</b>.\n\n"
        "Отправь первый пост кампании (текст или медиа с подписью).\n"
        "Каждый пост — отдельное сообщение.\n\n"
        "Когда закончишь добавлять посты — нажми кнопку:",
        reply_markup=_ga_bc_finish_msgs_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "ga:bc_select_tenant")
async def cb_ga_bc_select_tenant(call: CallbackQuery) -> None:
    """
    Просто открываем список клиентов — дальше из карточки можно запустить
    рассылку по конкретному тенанту.
    """
    await cb_ga_tenants(call)  # показываем страницу 1 по тем же правилам


@router.callback_query(F.data.startswith("ga:bc_tenant:"))
async def cb_ga_bc_tenant(call: CallbackQuery) -> None:
    """
    Запуск кампании по одному тенанту.
    """
    user = call.from_user
    if user is None:
        await call.answer()
        return
    uid = user.id

    if not _is_ga(uid):
        await call.answer("Нет доступа", show_alert=True)
        return

    try:
        _, _, tid_str = call.data.split(":", 2)
        tenant_id = int(tid_str)
    except (ValueError, IndexError):
        await call.answer("Некорректный tenant_id", show_alert=True)
        return

    ga_broadcast_state[uid] = {
        "target_type": "tenant",
        "tenant_id": tenant_id,
        "stage": "collect_msgs",
        "messages": [],
    }

    await call.message.answer(
        f"Кампания по пользователям тенанта <code>{tenant_id}</code>.\n\n"
        "Отправь первый пост (текст или медиа с подписью).\n"
        "Каждый пост — отдельное сообщение.\n\n"
        "Когда закончишь добавлять посты — нажми кнопку:",
        reply_markup=_ga_bc_finish_msgs_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "ga:bc_done_msgs")
async def cb_ga_bc_done_msgs(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        await call.answer()
        return
    uid = user.id

    state = ga_broadcast_state.get(uid)
    if state is None or state.get("stage") != "collect_msgs":
        await call.answer("Нет активной кампании", show_alert=True)
        return

    messages: List[Dict[str, Any]] = state.get("messages") or []
    if not messages:
        await call.answer("Сначала добавь хотя бы один пост", show_alert=True)
        return

    state["stage"] = "ask_time"
    await _ga_bc_ask_time(call.message)
    await call.answer()


@router.callback_query(F.data.startswith("ga:bc_time:"))
async def cb_ga_bc_time(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        await call.answer()
        return
    uid = user.id

    state = ga_broadcast_state.get(uid)
    if state is None:
        await call.answer("Нет активной кампании", show_alert=True)
        return

    choice = call.data.split(":", 2)[2]
    target_type = str(state.get("target_type"))
    tenant_id = state.get("tenant_id")
    messages: List[Dict[str, Any]] = state.get("messages") or []

    if not messages:
        await call.answer("Нет постов для рассылки", show_alert=True)
        return

    if choice == "now":
        # сразу отправляем
        ga_broadcast_state.pop(uid, None)
        await call.message.answer("Начинаю рассылку кампании…")
        sent, failed = await _ga_do_broadcast_posts(
            call.message.bot,
            call.message.chat.id,
            target_type,
            tenant_id,  # type: ignore[arg-type]
            messages,
        )
        await call.message.answer(
            "Рассылка по кампании завершена.\n"
            f"✅ Отправлено сообщений: <b>{sent}</b>\n"
            f"⚠️ Ошибок: <b>{failed}</b>"
        )
        await call.answer()
        return

    if choice == "later":
        state["stage"] = "await_time"
        await call.message.answer(
            "Отправь время по МСК в формате ЧЧ:ММ, например 15:30.\n"
            "Если время уже прошло, отправим на следующий день."
        )
        await call.answer()
        return

    await call.answer("Неизвестная команда", show_alert=True)


@router.callback_query(F.data == "ga:bc_cancel")
async def cb_ga_bc_cancel(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        await call.answer()
        return
    uid = user.id

    ga_broadcast_state.pop(uid, None)
    await call.message.answer("Кампания рассылки отменена.")
    await call.answer()


# ---------------------------------------------------------------------------
# Общий обработчик текстов
# ---------------------------------------------------------------------------


@router.message(F.text)
async def handle_text(message: Message) -> None:
    text = (message.text or "").strip()
    if not text:
        return

    user = message.from_user
    if user is None:
        return
    uid = user.id

    # --- если GA сейчас собирает кампанию ---
    state = ga_broadcast_state.get(uid)
    if state is not None and _is_ga(uid):
        stage = state.get("stage")

        # собираем текстовые посты
        if stage == "collect_msgs":
            messages: List[Dict[str, Any]] = state.get("messages") or []
            messages.append({"text": text, "media": None})
            state["messages"] = messages

            await message.answer(
                f"Пост #{len(messages)} сохранён (только текст).\n\n"
                "Отправь следующий пост (текст или медиа), "
                "или нажми «Перейти к настройке времени».",
                reply_markup=_ga_bc_finish_msgs_kb(),
            )
            return

        # ожидаем время
        if stage == "await_time":
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

            target_type = str(state.get("target_type"))
            tenant_id = state.get("tenant_id")
            messages_list: List[Dict[str, Any]] = state.get("messages") or []

            ga_broadcast_state.pop(uid, None)

            asyncio.create_task(
                _ga_scheduled_broadcast_posts(
                    message.bot,
                    message.chat.id,
                    target_type,
                    tenant_id,  # type: ignore[arg-type]
                    messages_list,
                    delay,
                )
            )

            await message.answer(
                f"Кампания запланирована на {text} по МСК ✅"
            )
            return

    # --- если это GA, но не в кампании — может быть токен бота ---
    if TOKEN_RE.match(text):
        await _handle_new_bot_token(message, text)
        return

    # обычный фоллбек
    await message.answer(
        "❓ Не понял сообщение.\n\n"
        "Чтобы подключить бота — отправь сюда его токен из BotFather.\n\n"
        "Глобальный админ может открыть панель командой /stas."
    )


# ---------------------------------------------------------------------------
# медиа для кампаний (фото, видео, документы, гифки)
# ---------------------------------------------------------------------------


@router.message(F.photo | F.video | F.document | F.animation)
async def handle_media(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    uid = user.id

    state = ga_broadcast_state.get(uid)
    if state is not None and _is_ga(uid) and state.get("stage") == "collect_msgs":
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

        text = message.caption or ""

        messages: List[Dict[str, Any]] = state.get("messages") or []
        messages.append({"text": text, "media": media})
        state["messages"] = messages

        await message.answer(
            f"Пост #{len(messages)} сохранён (медиа + подпись).\n\n"
            "Отправь следующий пост (текст или медиа), "
            "или нажми «Перейти к настройке времени».",
            reply_markup=_ga_bc_finish_msgs_kb(),
        )
        return

    # если это не часть кампании — игнорируем


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------


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
    try:
        await dp.start_polling(bot)
    except Exception as e:  # noqa: BLE001
        logger.exception("Parent bot crashed: %s", e)
    finally:
        await bot.session.close()
        logger.info("Parent bot stopped")