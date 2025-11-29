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

# Простейшая проверка формата токена бота
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_\-]{20,}$")

# состояние глобальной рассылки для GA: admin_id -> state
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
    Если не выйдет — вернём None.
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
                    text="📢 Глобальная рассылка",
                    callback_data="ga:bc",
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
        "• «Глобальная рассылка» — по всем пользователям всех активных ботов.\n"
        "• «Клиенты» — список тенантов с карточками и удалением.",
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


# ---------------------------------------------------------------------------
# GA: глобальная рассылка
# ---------------------------------------------------------------------------


def _ga_bc_media_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить медиа",
                    callback_data="ga:bc:media:yes",
                )
            ],
            [
                InlineKeyboardButton(
                    text="➡️ Без медиа",
                    callback_data="ga:bc:media:no",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✖️ Отмена",
                    callback_data="ga:bc:cancel",
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
                    callback_data="ga:bc:time:now",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏰ Запланировать по времени (МСК)",
                    callback_data="ga:bc:time:later",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✖️ Отмена",
                    callback_data="ga:bc:cancel",
                )
            ],
        ]
    )


async def _ga_bc_ask_time(message: Message, admin_id: int) -> None:
    await message.answer(
        "Когда отправить рассылку?",
        reply_markup=_ga_bc_time_kb(),
    )


async def _ga_do_broadcast(
    bot: Bot,
    admin_chat_id: int,
    text: str,
    media: Optional[dict],
) -> tuple[int, int]:
    """Фактическая отправка по всем пользователям всех активных тенантов."""
    user_ids = await _list_all_active_tenant_user_ids()
    if not user_ids:
        await bot.send_message(
            admin_chat_id,
            "Нет ни одного пользователя в активных ботах — рассылать некому.",
        )
        return 0, 0

    sent = 0
    failed = 0

    for uid in user_ids:
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


async def _ga_scheduled_broadcast(
    bot: Bot,
    admin_chat_id: int,
    text: str,
    media: Optional[dict],
    delay_seconds: float,
) -> None:
    try:
        await asyncio.sleep(delay_seconds)
        sent, failed = await _ga_do_broadcast(bot, admin_chat_id, text, media)
        await bot.send_message(
            admin_chat_id,
            "Глобальная рассылка завершена.\n"
            f"✅ Успешно: <b>{sent}</b>\n"
            f"⚠️ Ошибок: <b>{failed}</b>",
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Scheduled GA broadcast error: %s", e)


@router.callback_query(F.data.startswith("ga:"))
async def cb_ga(call: CallbackQuery) -> None:
    """
    Все колбэки префикса ga: — родительская админка.
    """
    user = call.from_user
    if user is None:
        await call.answer()
        return
    uid = user.id

    parts = call.data.split(":")
    # ga:...
    if len(parts) < 2:
        await call.answer("Некорректная команда", show_alert=True)
        return

    cmd = parts[1]

    if cmd == "bc":
        # старт глобальной рассылки
        if not _is_ga(uid):
            await call.answer("Нет доступа", show_alert=True)
            return

        ga_broadcast_state[uid] = {
            "stage": "await_text",
            "text": None,
            "media": None,
        }
        await call.message.answer(
            "✏️ Отправь текст рассылки одним сообщением.\n\n"
            "После этого я спрошу, нужно ли добавить медиа и по времени/сразу."
        )
        await call.answer()
        return

    if cmd == "tenants":
        # ga:tenants:<page>
        if not _is_ga(uid):
            await call.answer("Нет доступа", show_alert=True)
            return
        try:
            page = int(parts[2])
        except (IndexError, ValueError):
            page = 1
        await _ga_show_tenants_page(call, page)
        return

    if cmd == "tenant":
        # ga:tenant:<id>
        if not _is_ga(uid):
            await call.answer("Нет доступа", show_alert=True)
            return
        try:
            tenant_id = int(parts[2])
        except (IndexError, ValueError):
            await call.answer("Некорректный tenant_id", show_alert=True)
            return
        await _ga_show_tenant_card(call, tenant_id)
        return

    if cmd == "tenantdel":
        # ga:tenantdel:<id>
        if not _is_ga(uid):
            await call.answer("Нет доступа", show_alert=True)
            return
        try:
            tenant_id = int(parts[2])
        except (IndexError, ValueError):
            await call.answer("Некорректный tenant_id", show_alert=True)
            return
        await _ga_delete_tenant_handler(call, tenant_id)
        return

    # --- шаги глобальной рассылки ---

    if cmd == "bc":
        # уже обработали выше
        await call.answer()
        return

    if cmd == "bc" and len(parts) >= 3:
        # сюда не попадём, оставлено на всякий
        await call.answer()
        return

    if cmd == "bc" or cmd == "ga":
        await call.answer()
        return

    # ga:bc:media:yes|no
    if cmd == "bc" and len(parts) >= 3 and parts[2] == "media":
        # не используется в таком виде, оставлено для совместимости
        await call.answer()
        return

    # общий обработчик подкоманд ga:bc:...
    if parts[1] == "bc" or parts[1] == "ga":
        # сюда не дойдём, потому что cmd == parts[1]
        await call.answer()
        return

    # далее — обработка ga:bc:* вынесена в отдельные сравнения
    # но, чтобы не запутаться, сделаем проще: распознаем по второму элементу

    await call.answer("Неизвестная команда", show_alert=True)


# Отдельно распарсим подробные колбэки для расслылки,
# чтобы не превращать один хэндлер в ад:
@router.callback_query(F.data.startswith("ga:bc:"))
async def cb_ga_bc(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        await call.answer()
        return
    uid = user.id

    if not _is_ga(uid):
        await call.answer("Нет доступа", show_alert=True)
        return

    parts = call.data.split(":")
    # ga:bc:...
    if len(parts) < 3:
        await call.answer("Некорректная команда", show_alert=True)
        return

    sub = parts[2]
    state = ga_broadcast_state.get(uid)

    if sub == "media":
        if len(parts) < 4 or state is None:
            await call.answer("Нет активной рассылки", show_alert=True)
            return
        choice = parts[3]
        if choice == "yes":
            state["stage"] = "await_media"
            await call.message.answer(
                "Отправь фото/видео/документ/гифку для рассылки (можно с подписью, но используем основной текст)."
            )
            await call.answer()
            return
        if choice == "no":
            state["stage"] = "ask_time"
            await _ga_bc_ask_time(call.message, uid)
            await call.answer()
            return

    if sub == "time":
        if len(parts) < 4 or state is None:
            await call.answer("Нет активной рассылки", show_alert=True)
            return
        choice = parts[3]
        text_val = str(state.get("text") or "")
        media_val = state.get("media")  # type: ignore[assignment]

        if choice == "now":
            ga_broadcast_state.pop(uid, None)
            await call.message.answer("Начинаю глобальную рассылку…")
            sent, failed = await _ga_do_broadcast(
                call.message.bot,
                call.message.chat.id,
                text_val,
                media_val,  # type: ignore[arg-type]
            )
            await call.message.answer(
                "Глобальная рассылка завершена.\n"
                f"✅ Успешно: <b>{sent}</b>\n"
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

    if sub == "cancel":
        ga_broadcast_state.pop(uid, None)
        await call.message.answer("Глобальная рассылка отменена.")
        await call.answer()
        return

    await call.answer("Неизвестная команда", show_alert=True)


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

    # --- шаги глобальной рассылки для GA ---
    state = ga_broadcast_state.get(uid)
    if state is not None and _is_ga(uid):
        stage = state.get("stage")

        if stage == "await_text":
            state["text"] = text
            state["stage"] = "ask_media"
            await message.answer(
                "Текст сохранён.\n\n"
                "Хочешь добавить медиа к рассылке?",
                reply_markup=_ga_bc_media_kb(),
            )
            return

        if stage == "await_time":
            # ждём время формата ЧЧ:ММ по МСК
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

            text_val = str(state.get("text") or "")
            media_val = state.get("media")  # type: ignore[assignment]
            ga_broadcast_state.pop(uid, None)

            asyncio.create_task(
                _ga_scheduled_broadcast(
                    message.bot,
                    message.chat.id,
                    text_val,
                    media_val,  # type: ignore[arg-type]
                    delay,
                )
            )

            await message.answer(
                f"Глобальная рассылка запланирована на {text} по МСК ✅"
            )
            return

    # --- если это GA, но не в рассылке — может быть токен бота ---
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
# медиа для глобальной рассылки
# ---------------------------------------------------------------------------


@router.message(F.photo | F.video | F.document | F.animation)
async def handle_media(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    uid = user.id

    state = ga_broadcast_state.get(uid)
    if state is not None and _is_ga(uid) and state.get("stage") == "await_media":
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

        state["media"] = media
        state["stage"] = "ask_time"
        await _ga_bc_ask_time(message, uid)
        return

    # если это не часть рассылки — игнорируем


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