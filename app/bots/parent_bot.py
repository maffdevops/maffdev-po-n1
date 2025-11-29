import asyncio
import logging
import re
from typing import List

from aiogram import Bot, Dispatcher, Router, F
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from sqlalchemy import select, func, delete

from app.settings import settings
from app.db import SessionLocal
from app.models import Tenant, UserAccess, UserLang, Event

logger = logging.getLogger("pocket_saas.parent")

router = Router()

# Простейшая проверка формата токена бота
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_\-]{20,}$")

# кто сейчас вводит текст рассылки
waiting_broadcast: set[int] = set()


# --- helpers ---------------------------------------------------------------


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


def _ga_main_menu_kb() -> InlineKeyboardMarkup:
    """
    Главное меню для /stas (ГА).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 Глобальная рассылка",
                    callback_data="adm:broadcast",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Клиенты",
                    callback_data="adm:clients",
                )
            ],
        ]
    )


async def _ga_show_clients(call: CallbackQuery) -> None:
    """
    Список всех клиентов (тенантов).
    """
    async with SessionLocal() as session:
        res = await session.execute(
            select(Tenant).order_by(Tenant.id.asc())
        )
        tenants = list(res.scalars().all())

    if not tenants:
        text = "👥 Клиентов пока нет."
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ В главное меню",
                        callback_data="adm:back",
                    )
                ]
            ]
        )
        await call.message.edit_text(text, reply_markup=kb)
        await call.answer()
        return

    lines = ["👥 Список клиентов:\n"]
    kb_rows: list[list[InlineKeyboardButton]] = []

    for t in tenants:
        name = t.bot_username or "без username"
        owner = t.owner_telegram_id or "—"
        lines.append(f"<b>{t.id}</b> — @{name} (owner: <code>{owner}</code>)")
        kb_rows.append(
            [
                InlineKeyboardButton(
                    text=f"{t.id} — @{name}",
                    callback_data=f"adm:client:{t.id}",
                )
            ]
        )

    kb_rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ В главное меню",
                callback_data="adm:back",
            )
        ]
    )

    text = "\n".join(lines)
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


async def _ga_show_client_card(call: CallbackQuery, tenant_id: int) -> None:
    """
    Карточка конкретного клиента (тенанта).
    """
    async with SessionLocal() as session:
        res = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant: Tenant | None = res.scalar_one_or_none()
        if tenant is None:
            await call.answer("Клиент не найден", show_alert=True)
            return

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

        q_dep = select(
            func.coalesce(func.sum(Event.amount), 0.0),
            func.count(),
        ).where(
            Event.tenant_id == tenant_id,
            Event.kind.in_(["ftd", "rd"]),
        )
        total_amount, dep_events = (await session.execute(q_dep)).one()

    link = f"https://t.me/{tenant.bot_username}" if tenant.bot_username else "—"

    text = (
        "👤 Клиент (тенант)\n\n"
        f"ID: <code>{tenant.id}</code>\n"
        f"Owner TG ID: <code>{tenant.owner_telegram_id or '—'}</code>\n"
        f"Bot username: @{tenant.bot_username or '—'}\n"
        f"Ссылка на бота: {link}\n"
        f"Активен: <b>{'да' if tenant.is_active else 'нет'}</b>\n\n"
        f"Проверять подписку: <b>{'да' if tenant.check_subscription else 'нет'}</b>\n"
        f"Проверять депозит: <b>{'да' if tenant.check_deposit else 'нет'}</b>\n\n"
        f"Реф. ссылка: {tenant.ref_link or '—'}\n"
        f"Ссылка на депозит: {tenant.deposit_link or '—'}\n"
        f"ID канала: <code>{tenant.gate_channel_id or '—'}</code>\n"
        f"URL канала: {tenant.gate_channel_url or '—'}\n"
        f"URL поддержки: {tenant.support_url or settings.default_support_url or '—'}\n\n"
        f"Пользователей в боте: <b>{total_users}</b>\n"
        f"Из них с регистрацией: <b>{regs}</b>\n"
        f"Из них с депозитом: <b>{deps}</b>\n\n"
        f"Всего депозитов (FTD+RD), сумма: <b>{total_amount}</b>\n"
        f"Количество депозитных событий: <b>{dep_events}</b>\n"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить клиента полностью",
                    callback_data=f"adm:client:del:{tenant_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К списку клиентов",
                    callback_data="adm:clients",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В главное меню",
                    callback_data="adm:back",
                )
            ],
        ]
    )

    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


async def _ga_delete_tenant_full(tenant_id: int) -> bool:
    """
    Полное удаление клиента:
    - все UserAccess
    - все UserLang
    - все Event
    - сам Tenant

    После этого его дочерний бот по сути "отключен".
    """
    async with SessionLocal() as session:
        res = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant: Tenant | None = res.scalar_one_or_none()
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

        await session.delete(tenant)
        await session.commit()

    logger.info("GA deleted tenant %s with all related data", tenant_id)
    return True


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
            f"Username: <b>{tenant.bot_username or '—'}</b>\n"
            f"Ссылка: {link}\n\n"
            "Если ты отправишь новый токен, я обновлю этого же бота (по-прежнему только 1 бот на человека)."
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
    Через неё:
    - глобальная рассылка по всем пользователям
    - управление клиентами (тенантами)
    """
    if not _is_ga(message.from_user.id):
        await message.answer("❌ Эта команда только для владельца.")
        return

    await message.answer(
        "👑 Главное меню админа.",
        reply_markup=_ga_main_menu_kb(),
    )


@router.callback_query(F.data.startswith("adm:"))
async def cb_adm(call: CallbackQuery) -> None:
    """
    Все клики по меню /stas.
    """
    user_id = call.from_user.id
    if not _is_ga(user_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    data = call.data
    parts = data.split(":")

    if len(parts) < 2:
        await call.answer("Некорректная команда", show_alert=True)
        return

    cmd = parts[1]

    # back -> в главное меню
    if cmd == "back":
        await call.message.edit_text(
            "👑 Главное меню админа.",
            reply_markup=_ga_main_menu_kb(),
        )
        await call.answer()
        return

    # глобальная рассылка (как было раньше)
    if cmd == "broadcast":
        waiting_broadcast.add(user_id)
        await call.message.answer(
            "✏️ Отправь текст рассылки одним сообщением.\n\n"
            "Как только получу текст — начну отправку по всем пользователям "
            "всех активных тенантов."
        )
        await call.answer()
        return

    # список клиентов
    if cmd == "clients":
        await _ga_show_clients(call)
        return

    # работа с конкретным клиентом
    if cmd == "client":
        # варианты:
        # adm:client:<id>
        # adm:client:del:<id>
        if len(parts) < 3:
            await call.answer("Некорректная команда", show_alert=True)
            return

        sub = parts[2]

        # показ карточки
        if sub.isdigit():
            tenant_id = int(sub)
            await _ga_show_client_card(call, tenant_id)
            return

        # удаление
        if sub == "del":
            if len(parts) < 4:
                await call.answer("Некорректная команда", show_alert=True)
                return
            try:
                tenant_id = int(parts[3])
            except ValueError:
                await call.answer("Некорректный ID клиента", show_alert=True)
                return

            ok = await _ga_delete_tenant_full(tenant_id)
            if not ok:
                await call.answer("Клиент не найден", show_alert=True)
                return

            await call.message.edit_text(
                "🗑 Клиент и все связанные с ним данные полностью удалены."
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

    user_id = message.from_user.id

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
    bot = Bot(token=settings.parent_bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Starting parent bot polling")
    await dp.start_polling(bot)