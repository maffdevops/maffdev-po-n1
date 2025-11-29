import asyncio
import datetime as dt
import logging
import os
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Tuple
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
    WebAppInfo,
)
from aiogram.client.default import DefaultBotProperties

from sqlalchemy import select, func, delete
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import Tenant, UserAccess, UserLang, Event
from app.settings import settings

from .texts import (
    LANGS,
    NATIVE_LANG_NAMES,
    t_user,
    t_admin,
)

logger = logging.getLogger("pocket_saas.child")


# ---------------------------------------------------------------------------
# helpers общие
# ---------------------------------------------------------------------------


async def _get_tenant(tenant_id: int) -> Optional[Tenant]:
    async with SessionLocal() as session:
        res = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        return res.scalar_one_or_none()


async def _get_or_create_access(
    tenant_id: int,
    user_id: int,
    username: Optional[str],
) -> UserAccess:
    # защита от ситуации, когда нам передают username бота
    # (боты всегда заканчиваются на "bot")
    if username and username.lower().endswith("bot"):
        username = None

    async with SessionLocal() as session:
        res = await session.execute(
            select(UserAccess).where(
                UserAccess.tenant_id == tenant_id,
                UserAccess.user_id == user_id,
            )
        )
        ua: UserAccess | None = res.scalar_one_or_none()
        if ua is not None:
            changed = False
            if username and ua.username != username:
                ua.username = username
                changed = True
            if ua.click_id is None:
                ua.click_id = str(user_id)
                changed = True
            if changed:
                await session.commit()
                await session.refresh(ua)
            return ua

        ua = UserAccess(
            tenant_id=tenant_id,
            user_id=user_id,
            username=username,
            click_id=str(user_id),
        )
        session.add(ua)

        try:
            await session.commit()
            await session.refresh(ua)
            return ua
        except IntegrityError:
            await session.rollback()
            res = await session.execute(
                select(UserAccess).where(
                    UserAccess.tenant_id == tenant_id,
                    UserAccess.user_id == user_id,
                )
            )
            ua = res.scalar_one_or_none()
            if ua is None:
                raise
            changed = False
            if username and ua.username != username:
                ua.username = username
                changed = True
            if ua.click_id is None:
                ua.click_id = str(user_id)
                changed = True
            if changed:
                await session.commit()
                await session.refresh(ua)
            return ua


async def _get_user_lang(tenant_id: int, user_id: int) -> Optional[str]:
    async with SessionLocal() as session:
        res = await session.execute(
            select(UserLang).where(
                UserLang.tenant_id == tenant_id,
                UserLang.user_id == user_id,
            )
        )
        ul: UserLang | None = res.scalar_one_or_none()
        if ul is None:
            return None
        return ul.lang


async def _set_user_lang(tenant_id: int, user_id: int, lang: str) -> None:
    async with SessionLocal() as session:
        res = await session.execute(
            select(UserLang).where(
                UserLang.tenant_id == tenant_id,
                UserLang.user_id == user_id,
            )
        )
        ul: UserLang | None = res.scalar_one_or_none()

        if ul is None:
            ul = UserLang(
                tenant_id=tenant_id,
                user_id=user_id,
                lang=lang,
            )
            session.add(ul)
        else:
            ul.lang = lang

        await session.commit()


async def _is_tenant_admin(tenant_id: int, user_id: int) -> bool:
    if user_id in settings.ga_admin_ids:
        return True

    async with SessionLocal() as session:
        res = await session.execute(
            select(Tenant.owner_telegram_id).where(Tenant.id == tenant_id)
        )
        owner_id = res.scalar_one_or_none()

    return owner_id == user_id


def _tenant_pb_code(tenant: Tenant) -> str:
    """
    Короткий код для постбэков:
    tn1, tn2, tn3 ...
    """
    return f"tn{tenant.id}"


async def _get_access_flags_from_events(
    tenant_id: int,
    user_id: int,
) -> Tuple[bool, bool]:
    """
    Читаем из таблицы Event:
    - есть ли событие reg -> зарегистрирован
    - есть ли ftd/rd -> есть депозит
    """
    async with SessionLocal() as session:
        reg_cnt = await session.scalar(
            select(func.count()).select_from(Event).where(
                Event.tenant_id == tenant_id,
                Event.user_id == user_id,
                Event.kind == "reg",
            )
        ) or 0

        dep_cnt = await session.scalar(
            select(func.count()).select_from(Event).where(
                Event.tenant_id == tenant_id,
                Event.user_id == user_id,
                Event.kind.in_(["ftd", "rd"]),
            )
        ) or 0

    return reg_cnt > 0, dep_cnt > 0


async def _get_effective_access(
    tenant_id: int,
    user_id: int,
    username: Optional[str] = None,
) -> Tuple[UserAccess, bool, bool]:
    """
    Возвращает:
    - UserAccess
    - is_registered (флаг или есть reg-событие)
    - has_deposit (флаг или есть ftd/rd-событие)

    И синхронизирует флаги в UserAccess с событиями Event,
    чтобы в админке всё было консистентно.
    """
    ua = await _get_or_create_access(tenant_id, user_id, username)
    reg_by_event, dep_by_event = await _get_access_flags_from_events(tenant_id, user_id)

    is_registered = ua.is_registered or reg_by_event
    has_deposit = ua.has_deposit or dep_by_event

    if (is_registered != ua.is_registered) or (has_deposit != ua.has_deposit):
        async with SessionLocal() as session:
            res = await session.execute(
                select(UserAccess).where(
                    UserAccess.tenant_id == tenant_id,
                    UserAccess.user_id == user_id,
                )
            )
            ua_db: UserAccess | None = res.scalar_one_or_none()
            if ua_db is not None:
                ua_db.is_registered = is_registered
                ua_db.has_deposit = has_deposit
                await session.commit()

    return ua, is_registered, has_deposit


# ---------------------------------------------------------------------------
# клавиатуры админки
# ---------------------------------------------------------------------------


def _admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="adm:users")],
            [InlineKeyboardButton(text="📩 Постбэки", callback_data="adm:events")],
            [
                InlineKeyboardButton(text="⚙️ Параметры", callback_data="adm:params"),
                InlineKeyboardButton(text="🔗 Ссылки", callback_data="adm:links"),
            ],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data="adm:bc")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats")],
        ]
    )


def _admin_params_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Проверять подписку",
                    callback_data="adm:params:sub",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Проверять депозит",
                    callback_data="adm:params:dep",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В меню",
                    callback_data="adm:back",
                )
            ],
        ]
    )


def _admin_links_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Задать реф ссылку",
                    callback_data="adm:links:set:ref",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Задать ссылку на деп",
                    callback_data="adm:links:set:dep",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Задать ссылку поддержки",
                    callback_data="adm:links:set:support",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Задать айди канала",
                    callback_data="adm:links:set:chanid",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Задать ссылку на канал",
                    callback_data="adm:links:set:chanurl",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В меню",
                    callback_data="adm:back",
                )
            ],
        ]
    )


def _admin_broadcast_segment_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t_admin("broadcast_seg_all"),
                    callback_data="adm:bc:seg:all",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t_admin("broadcast_seg_reg"),
                    callback_data="adm:bc:seg:reg",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t_admin("broadcast_seg_dep"),
                    callback_data="adm:bc:seg:dep",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t_admin("broadcast_seg_lang"),
                    callback_data="adm:bc:seg:lang",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В меню",
                    callback_data="adm:back",
                )
            ],
        ]
    )


def _admin_broadcast_lang_kb() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for code in LANGS:
        rows.append(
            [
                InlineKeyboardButton(
                    text=NATIVE_LANG_NAMES[code],
                    callback_data=f"adm:bc:lang:{code}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="adm:bc",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _admin_broadcast_media_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t_admin("broadcast_media_add"),
                    callback_data="adm:bc:media:yes",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t_admin("broadcast_media_skip"),
                    callback_data="adm:bc:media:no",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="adm:bc:cancel",
                )
            ],
        ]
    )


def _admin_broadcast_time_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t_admin("broadcast_time_now"),
                    callback_data="adm:bc:time:now",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t_admin("broadcast_time_later"),
                    callback_data="adm:bc:time:later",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="adm:bc:cancel",
                )
            ],
        ]
    )


def _admin_stats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ В меню",
                    callback_data="adm:back",
                )
            ]
        ]
    )


# broadcast_state: admin_user_id -> dict
broadcast_state: Dict[int, Dict[str, object]] = {}
search_user_waiting: Dict[int, int] = {}
link_waiting: Dict[int, Tuple[int, str]] = {}  # admin -> (tenant_id, field)

# для «окно доступ открыт только один раз»
access_welcome_shown: set[tuple[int, int]] = set()


# ---------------------------------------------------------------------------
# пользовательское меню + онбординг
# ---------------------------------------------------------------------------


def _build_main_menu_kb(
    tenant: Tenant,
    lang: str,
    full_access: bool,
) -> InlineKeyboardMarkup:
    support_url = tenant.support_url or settings.default_support_url
    miniapp_url = settings.miniapp_url or "https://jeempocket.github.io/mini-app/"

    row1 = [
        InlineKeyboardButton(
            text=t_user(lang, "btn_instruction"),
            callback_data="menu:instruction",
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
            text=t_user(lang, "btn_lang"),
            callback_data="menu:lang",
        )
    )

    # Если доступ открыт — сразу web_app, иначе запускаем онбординг
    if full_access:
        row3 = [
            InlineKeyboardButton(
                text=t_user(lang, "btn_signal"),
                web_app=WebAppInfo(url=miniapp_url),
            )
        ]
    else:
        row3 = [
            InlineKeyboardButton(
                text=t_user(lang, "btn_signal"),
                callback_data="menu:signal",
            )
        ]

    return InlineKeyboardMarkup(inline_keyboard=[row1, row2, row3])


async def _send_screen_with_photo(
    message: Message,
    lang: str,
    screen: str,
    text: str,
    kb: Optional[InlineKeyboardMarkup] = None,
) -> None:
    path = os.path.join("assets", "en", f"{screen}.jpg")

    if os.path.exists(path):
        try:
            photo = FSInputFile(path)
            await message.answer_photo(photo, caption=text, reply_markup=kb)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to send photo %s: %s", path, e)

    await message.answer(text, reply_markup=kb)


async def _send_lang_menu(message: Message) -> None:
    base_lang = settings.lang_default
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=NATIVE_LANG_NAMES["en"], callback_data="lang:en"
                ),
                InlineKeyboardButton(
                    text=NATIVE_LANG_NAMES["ru"], callback_data="lang:ru"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=NATIVE_LANG_NAMES["hi"], callback_data="lang:hi"
                ),
                InlineKeyboardButton(
                    text=NATIVE_LANG_NAMES["ar"], callback_data="lang:ar"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=NATIVE_LANG_NAMES["es"], callback_data="lang:es"
                ),
                InlineKeyboardButton(
                    text=NATIVE_LANG_NAMES["fr"], callback_data="lang:fr"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=NATIVE_LANG_NAMES["ro"], callback_data="lang:ro"
                ),
            ],
        ]
    )
    await _send_screen_with_photo(
        message,
        base_lang,
        "lang",
        t_user(base_lang, "choose_lang"),
        kb,
    )


async def _send_main_menu(message: Message, tenant_id: int, lang: str) -> None:
    tenant = await _get_tenant(tenant_id)
    if tenant is None:
        await message.answer("Configuration error: tenant not found.")
        return

    full_access = False
    user = message.from_user

    if user is not None:
        _, is_registered, has_deposit = await _get_effective_access(
            tenant_id=tenant_id,
            user_id=user.id,
            username=user.username,
        )

        # Полный доступ:
        #  - регистрация обязательна всегда
        #  - депозит обязателен только если включена проверка депозита
        full_access = is_registered and (
            (not tenant.check_deposit) or has_deposit
        )

    text = f"{t_user(lang, 'menu_title')}\n\n{t_user(lang, 'menu_body')}"
    kb = _build_main_menu_kb(tenant, lang, full_access)
    await _send_screen_with_photo(message, lang, "menu", text, kb)


async def _send_instruction(message: Message, lang: str) -> None:
    text = f"{t_user(lang, 'instruction_title')}\n\n{t_user(lang, 'instruction_body')}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t_user(lang, "back_to_menu"),
                    callback_data="menu:back",
                )
            ]
        ]
    )
    await _send_screen_with_photo(message, lang, "instruction", text, kb)


async def _check_subscription(bot: Bot, tenant: Tenant, user_id: int) -> bool:
    """
    Проверяем, подписан ли юзер на канал gate_channel_id.
    Если канал не задан или проверка выключена — считаем, что всё ок.
    """
    if not tenant.check_subscription:
        return True
    if not tenant.gate_channel_id:
        return True

    try:
        member = await bot.get_chat_member(tenant.gate_channel_id, user_id)
        return member.status in ("member", "administrator", "creator", "owner")
    except Exception as e:  # noqa: BLE001
        logger.warning("Subscription check failed: %s", e)
        return False


async def _send_subscribe_screen(message: Message, tenant: Tenant, lang: str) -> None:
    channel_url = tenant.gate_channel_url or "https://t.me"
    text = f"{t_user(lang, 'sub_title')}\n\n{t_user(lang, 'sub_body')}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t_user(lang, "btn_subscribe"),
                    url=channel_url,
                )
            ],
            [
                InlineKeyboardButton(
                    text=t_user(lang, "btn_i_subscribed"),
                    callback_data="signal:sub_ok",
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
    await _send_screen_with_photo(message, lang, "subscribe", text, kb)


def _append_click_id_to_ref(ref_link: str, click_id: int) -> str:
    """
    Добавляем к реф-ссылке параметр click_id = tg_id.
    """
    if not ref_link:
        return "https://t.me"
    parsed = urlparse(ref_link)
    qs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    qs["click_id"] = str(click_id)
    new_query = urlencode(qs)
    new_parsed = parsed._replace(query=new_query)
    return urlunparse(new_parsed)


async def _send_register_screen(
    message: Message,
    tenant: Tenant,
    lang: str,
    user_id: int,
) -> None:
    base_url = tenant.ref_link or "https://t.me"
    reg_url = _append_click_id_to_ref(base_url, user_id)
    text = f"{t_user(lang, 'reg_title')}\n\n{t_user(lang, 'reg_body')}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t_user(lang, "btn_register"),
                    url=reg_url,
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
    await _send_screen_with_photo(message, lang, "register", text, kb)


async def _send_deposit_screen(message: Message, tenant: Tenant, lang: str) -> None:
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
    await _send_screen_with_photo(message, lang, "deposit", text, kb)


aasync def _send_access_open_screen(
    message: Message,
    tenant: Tenant,
    lang: str,
) -> None:
    """
    Окно «Доступ открыт».

    Здесь КНОПКА ДОЛЖНА ОТКРЫВАТЬ МИНИ-АПП через web_app, а не через callback.
    Текст оставляем как «Получить сигнал» (btn_signal), чтобы всё
    выглядело одинаково и в меню, и в этом окне.
    """
    support_url = tenant.support_url or settings.default_support_url
    miniapp_url = settings.miniapp_url or "https://jeempocket.github.io/mini-app/"

    text = f"{t_user(lang, 'access_title')}\n\n{t_user(lang, 'access_body')}"

    # основная кнопка — сразу web_app
    row1 = [
        InlineKeyboardButton(
            text=t_user(lang, "btn_signal"),  # «Получить сигнал»
            web_app=WebAppInfo(url=miniapp_url),
        )
    ]

    row2: list[InlineKeyboardButton] = []
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
    await _send_screen_with_photo(message, lang, "access", text, kb)


async def _open_miniapp(message: Message, lang: str) -> None:
    """
    Исторический хэндлер для callback'а signal:open_app.
    Сейчас мини-апп открывается только web_app-кнопками,
    поэтому тут ничего не отправляем, чтобы не спамить текстом.
    """
    return


async def _handle_signal_flow(
    bot: Bot,
    message: Message,
    tenant_id: int,
    user_id: int,
) -> None:
    """
    Общая логика по кнопке «Получить сигнал»:
    1) Подписка (если включена),
    2) Регистрация (обязательно всегда),
    3) Депозит (если включён),
    4) Окно «Доступ открыт» один раз.

    Флаги регистрации и депозита берём ИЗ СОБЫТИЙ Event
    (reg / ftd / rd) + синхронизация в UserAccess.
    """
    tenant = await _get_tenant(tenant_id)
    if tenant is None:
        await message.answer("Configuration error: tenant not found.")
        return

    lang = await _get_user_lang(tenant_id, user_id) or settings.lang_default

    ua, is_registered, has_deposit = await _get_effective_access(
        tenant_id=tenant_id,
        user_id=user_id,
        username=message.from_user.username if message.from_user else None,
    )

    # 1) Подписка (если включена)
    if tenant.check_subscription:
        is_subscribed = await _check_subscription(bot, tenant, user_id)
        if not is_subscribed:
            await _send_subscribe_screen(message, tenant, lang)
            return

    # 2) Регистрация — ОБЯЗАТЕЛЬНА всегда
    if not is_registered:
        await _send_register_screen(message, tenant, lang, user_id)
        return

    # 3) Депозит — только если включена проверка депозита
    if tenant.check_deposit and not has_deposit:
        await _send_deposit_screen(message, tenant, lang)
        return

    # 4) Всё ок — показываем "Доступ открыт" один раз
    key = (tenant_id, user_id)
    if key not in access_welcome_shown:
        access_welcome_shown.add(key)
        await _send_access_open_screen(message, tenant, lang)
    # Если уже показывали — ничего не шлём,
    # в главном меню кнопка "Получить сигнал" уже открывает мини-апп.


# ---------------------------------------------------------------------------
# пользователи (админка)
# ---------------------------------------------------------------------------


def _build_user_card_text(ua: UserAccess, user_lang: Optional[str]) -> str:
    lang_label = user_lang or "—"
    reg_label = "да" if ua.is_registered else "нет"
    dep_label = "да" if ua.has_deposit else "нет"

    return (
        "👤 Пользователь\n\n"
        f"TG ID: <code>{ua.user_id}</code>\n"
        f"Username: @{ua.username or '—'}\n"
        f"Язык: <b>{lang_label}</b>\n"
        f"Зарегистрирован: <b>{reg_label}</b>\n"
        f"С депозитом: <b>{dep_label}</b>\n"
        f"Trader ID: <code>{ua.trader_id or '—'}</code>\n"
        f"Всего депозитов (счётчик): <b>{ua.total_deposits}</b>\n"
        f"click_id: <code>{ua.click_id or '—'}</code>\n"
    )


def _build_user_card_kb(ua: UserAccess) -> InlineKeyboardMarkup:
    reg_text = "✅ Снять регистрацию" if ua.is_registered else "✅ Выдать регистрацию"
    dep_text = "💰 Снять депозит" if ua.has_deposit else "💰 Выдать депозит"

    rows = [
        [
            InlineKeyboardButton(
                text=reg_text,
                callback_data=f"adm:user:reg:{ua.user_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text=dep_text,
                callback_data=f"adm:user:dep:{ua.user_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="🗑 Удалить пользователя",
                callback_data=f"adm:user:del:{ua.user_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ К списку",
                callback_data="adm:users",
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _fetch_user_and_lang(
    tenant_id: int,
    user_id: int,
) -> tuple[Optional[UserAccess], Optional[str]]:
    async with SessionLocal() as session:
        res = await session.execute(
            select(UserAccess).where(
                UserAccess.tenant_id == tenant_id,
                UserAccess.user_id == user_id,
            )
        )
        ua: UserAccess | None = res.scalar_one_or_none()
        if ua is None:
            return None, None

        res_l = await session.execute(
            select(UserLang).where(
                UserLang.tenant_id == tenant_id,
                UserLang.user_id == user_id,
            )
        )
        ul: UserLang | None = res_l.scalar_one_or_none()
        user_lang = ul.lang if ul else None

    return ua, user_lang


async def _admin_show_user_card(
    call: CallbackQuery,
    tenant_id: int,
    user_id: int,
) -> None:
    ua, user_lang = await _fetch_user_and_lang(tenant_id, user_id)
    if ua is None:
        await call.answer("Пользователь не найден", show_alert=True)
        return

    text = _build_user_card_text(ua, user_lang)
    kb = _build_user_card_kb(ua)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


async def _admin_toggle_user_flag(
    tenant_id: int,
    user_id: int,
    flag: str,
) -> bool:
    async with SessionLocal() as session:
        res = await session.execute(
            select(UserAccess).where(
                UserAccess.tenant_id == tenant_id,
                UserAccess.user_id == user_id,
            )
        )
        ua: UserAccess | None = res.scalar_one_or_none()
        if ua is None:
            return False

        if flag == "reg":
            ua.is_registered = not ua.is_registered
        elif flag == "dep":
            ua.has_deposit = not ua.has_deposit
        else:
            return False

        await session.commit()
        return True


async def _admin_delete_user_record(tenant_id: int, user_id: int) -> None:
    async with SessionLocal() as session:
        await session.execute(
            delete(UserAccess).where(
                UserAccess.tenant_id == tenant_id,
                UserAccess.user_id == user_id,
            )
        )
        await session.execute(
            delete(UserLang).where(
                UserLang.tenant_id == tenant_id,
                UserLang.user_id == user_id,
            )
        )
        await session.execute(
            delete(Event).where(
                Event.tenant_id == tenant_id,
                Event.user_id == user_id,
            )
        )
        await session.commit()
    # сбрасываем флаг «доступ открыт» в памяти бота
    access_welcome_shown.discard((tenant_id, user_id))


async def _admin_search_and_show_user(
    message: Message,
    tenant_id: int,
    query: str,
) -> None:
    ua: Optional[UserAccess] = None

    async with SessionLocal() as session:
        if query.isdigit():
            uid = int(query)
            res = await session.execute(
                select(UserAccess).where(
                    UserAccess.tenant_id == tenant_id,
                    UserAccess.user_id == uid,
                )
            )
            ua = res.scalar_one_or_none()

        if ua is None:
            res = await session.execute(
                select(UserAccess).where(
                    UserAccess.tenant_id == tenant_id,
                    UserAccess.trader_id == query,
                )
            )
            ua = res.scalar_one_or_none()

    if ua is None:
        await message.answer("Пользователь не найден.")
        return

    user_lang = await _get_user_lang(tenant_id, ua.user_id)
    text = _build_user_card_text(ua, user_lang)
    kb = _build_user_card_kb(ua)
    await message.answer(text, reply_markup=kb)


async def _admin_show_users(
    call: CallbackQuery,
    tenant_id: int,
    page: int = 1,
) -> None:
    page_size = 5
    if page < 1:
        page = 1

    async with SessionLocal() as session:
        total = await session.scalar(
            select(func.count()).select_from(
                UserAccess
            ).where(UserAccess.tenant_id == tenant_id)
        ) or 0

        offset = (page - 1) * page_size
        res = await session.execute(
            select(UserAccess)
            .where(UserAccess.tenant_id == tenant_id)
            .order_by(UserAccess.user_id.asc())
            .offset(offset)
            .limit(page_size)
        )
        users = list(res.scalars().all())

    total_pages = max(1, (total + page_size - 1) // page_size)

    lines = ["🔎 Поиск по пользователям"]
    if not users:
        lines.append("")
        lines.append("Пользователей пока нет.")
    text = "\n".join(lines)

    kb_rows: List[List[InlineKeyboardButton]] = []

    kb_rows.append(
        [
            InlineKeyboardButton(
                text="🔍 Найти юзера",
                callback_data="adm:users:search",
            )
        ]
    )

    for ua in users:
        kb_rows.append(
            [
                InlineKeyboardButton(
                    text=str(ua.user_id),
                    callback_data=f"adm:user:show:{ua.user_id}",
                )
            ]
        )

    if total_pages > 1:
        pag_row: List[InlineKeyboardButton] = []
        if page > 1:
            pag_row.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"adm:users:page:{page-1}",
                )
            )
        pag_row.append(
            InlineKeyboardButton(
                text=f"{page} стр",
                callback_data=f"adm:users:page:{page}",
            )
        )
        if page < total_pages:
            pag_row.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"adm:users:page:{page+1}",
                )
            )
        kb_rows.append(pag_row)

    kb_rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ В админку",
                callback_data="adm:back",
            )
        ]
    )

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


# ---------------------------------------------------------------------------
# параметры и ссылки
# ---------------------------------------------------------------------------


async def _admin_toggle_param(tenant_id: int, field: str) -> bool:
    async with SessionLocal() as session:
        res = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant: Tenant | None = res.scalar_one_or_none()
        if tenant is None:
            return False

        if field == "sub":
            tenant.check_subscription = not tenant.check_subscription
        elif field == "dep":
            tenant.check_deposit = not tenant.check_deposit
        else:
            return False

        await session.commit()
        return True


def _build_links_text(tenant: Tenant) -> str:
    return (
        f"{t_admin('links_header')}\n\n"
        f"Реф ссылка: {tenant.ref_link or '—'}\n"
        f"Ссылка на депозит: {tenant.deposit_link or '—'}\n"
        f"URL поддержки: {tenant.support_url or settings.default_support_url or '—'}\n"
        f"ID канала: <code>{tenant.gate_channel_id or '—'}</code>\n"
        f"URL канала: {tenant.gate_channel_url or '—'}\n\n"
        "Чтобы очистить поле, отправь «-» (дефис)."
    )


async def _admin_show_links(call: CallbackQuery, tenant_id: int) -> None:
    tenant = await _get_tenant(tenant_id)
    if tenant is None:
        await call.answer("Тенант не найден", show_alert=True)
        return

    text = _build_links_text(tenant)
    await call.message.edit_text(text, reply_markup=_admin_links_kb())
    await call.answer()


async def _admin_send_links_message(message: Message, tenant_id: int) -> None:
    tenant = await _get_tenant(tenant_id)
    if tenant is None:
        await message.answer("Тенант не найден.")
        return
    text = _build_links_text(tenant)
    await message.answer(text, reply_markup=_admin_links_kb())


async def _admin_update_link_value(tenant_id: int, field: str, value: str) -> bool:
    async with SessionLocal() as session:
        res = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant: Tenant | None = res.scalar_one_or_none()
        if tenant is None:
            return False

        val = value.strip()
        if val in ("-", "—", ""):
            val = None

        if field == "ref":
            tenant.ref_link = val
        elif field == "dep":
            tenant.deposit_link = val
        elif field == "support":
            tenant.support_url = val
        elif field == "chanid":
            tenant.gate_channel_id = val
        elif field == "chanurl":
            tenant.gate_channel_url = val
        else:
            return False

        await session.commit()
        return True


async def _admin_show_params(call: CallbackQuery, tenant_id: int) -> None:
    tenant = await _get_tenant(tenant_id)
    if tenant is None:
        await call.answer("Тенант не найден", show_alert=True)
        return

    text = (
        f"{t_admin('params_header')}\n\n"
        f"Проверять подписку: <b>{'да' if tenant.check_subscription else 'нет'}</b>\n"
        f"Проверять депозит: <b>{'да' if tenant.check_deposit else 'нет'}</b>\n"
    )

    await call.message.edit_text(text, reply_markup=_admin_params_kb())
    await call.answer()


# ---------------------------------------------------------------------------
# постбэки (экран с URL)
# ---------------------------------------------------------------------------


async def _admin_show_postbacks(call: CallbackQuery, tenant_id: int) -> None:
    tenant = await _get_tenant(tenant_id)
    if tenant is None:
        await call.answer("Тенант не найден", show_alert=True)
        return

    base = settings.postback_base.rstrip("/")
    code = _tenant_pb_code(tenant)

    reg_url = (
        f"{base}/pb/{code}/reg"
        f"?click_id={{click_id}}&trader_id={{trader_id}}"
    )
    ftd_url = (
        f"{base}/pb/{code}/ftd"
        f"?click_id={{click_id}}&trader_id={{trader_id}}&sumdep={{sumdep}}"
    )
    rd_url = (
        f"{base}/pb/{code}/rd"
        f"?click_id={{click_id}}&trader_id={{trader_id}}&sumdep={{sumdep}}"
    )

    text = (
        "📩 Настройка постбэков\n\n"
        "Регистрация:\n"
        f"<code>{reg_url}</code>\n\n"
        "Первый депозит:\n"
        f"<code>{ftd_url}</code>\n\n"
        "Повторный депозит:\n"
        f"<code>{rd_url}</code>\n\n"
        "Параметры:\n"
        "- <b>click_id</b> — tg_id пользователя (мы передаём его в реф-ссылке);\n"
        "- <b>trader_id</b> — ID трейдера в кабинете PocketOption;\n"
        "- <b>sumdep</b> — сумма депозита.\n\n"
        "Просто скопируй нужный URL и вставь в настройки постбэков партнёрки."
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ В админку",
                    callback_data="adm:back",
                )
            ]
        ]
    )

    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


# ---------------------------------------------------------------------------
# рассылки
# ---------------------------------------------------------------------------


async def _admin_start_broadcast_menu(
    call: CallbackQuery,
    tenant_id: int,
) -> None:
    text = t_admin("broadcast_choose")
    kb = _admin_broadcast_segment_kb()
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


async def _admin_collect_broadcast_targets(
    tenant_id: int,
    segment: str,
    lang_code: Optional[str],
) -> List[int]:
    async with SessionLocal() as session:
        if segment in ("all", "sub"):
            q = select(UserAccess.user_id).where(
                UserAccess.tenant_id == tenant_id
            )
        elif segment == "reg":
            q = select(UserAccess.user_id).where(
                UserAccess.tenant_id == tenant_id,
                UserAccess.is_registered.is_(True),
            )
        elif segment == "dep":
            q = select(UserAccess.user_id).where(
                UserAccess.tenant_id == tenant_id,
                UserAccess.has_deposit.is_(True),
            )
        elif segment == "lang" and lang_code:
            q = (
                select(UserAccess.user_id)
                .join(
                    UserLang,
                    (UserLang.tenant_id == UserAccess.tenant_id)
                    & (UserLang.user_id == UserAccess.user_id),
                )
                .where(
                    UserAccess.tenant_id == tenant_id,
                    UserLang.lang == lang_code,
                )
            )
        else:
            q = select(UserAccess.user_id).where(
                UserAccess.tenant_id == tenant_id
            )

        res = await session.execute(q)
        return [row[0] for row in res.all()]


async def _admin_do_broadcast(
    bot: Bot,
    admin_chat_id: int,
    tenant_id: int,
    segment: str,
    lang_code: Optional[str],
    text: str,
    media: Optional[dict],
) -> Tuple[int, int]:
    user_ids = await _admin_collect_broadcast_targets(tenant_id, segment, lang_code)

    if not user_ids:
        await bot.send_message(admin_chat_id, t_admin("broadcast_empty"))
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
        except Exception as e:  # noqa: BLE001
            failed += 1
            logger.warning(
                "Child broadcast error tenant=%s user=%s: %s",
                tenant_id,
                uid,
                e,
            )

    return sent, failed


async def _admin_ask_time(message: Message, admin_id: int) -> None:
    state = broadcast_state.get(admin_id)
    if not state:
        return
    await message.answer(
        t_admin("broadcast_time_question"),
        reply_markup=_admin_broadcast_time_kb(),
    )


async def _scheduled_broadcast(
    bot: Bot,
    admin_chat_id: int,
    tenant_id: int,
    segment: str,
    lang_code: Optional[str],
    text: str,
    media: Optional[dict],
    delay_seconds: float,
) -> None:
    try:
        await asyncio.sleep(delay_seconds)
        sent, failed = await _admin_do_broadcast(
            bot, admin_chat_id, tenant_id, segment, lang_code, text, media
        )
        await bot.send_message(
            admin_chat_id,
            t_admin("broadcast_done", sent=sent, failed=failed),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Scheduled broadcast error: %s", e)


# ---------------------------------------------------------------------------
# статистика
# ---------------------------------------------------------------------------


async def _admin_show_stats(call: CallbackQuery, tenant_id: int) -> None:
    async with SessionLocal() as session:
        total_users = await session.scalar(
            select(func.count()).select_from(
                UserAccess
            ).where(UserAccess.tenant_id == tenant_id)
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

        q = select(
            func.coalesce(func.sum(Event.amount), 0.0),
            func.count(),
        ).where(
            Event.tenant_id == tenant_id,
            Event.kind.in_(["ftd", "rd"]),
        )
        total_amount, count = (await session.execute(q)).one()

    subs = total_users  # формально тут те, кто дошёл до бота

    text = (
        f"{t_admin('stats_header')}\n\n"
        + t_admin(
            "stats_body",
            total_users=total_users,
            subs=subs,
            regs=regs,
            deps=deps,
            total_amount=total_amount,
            count=count,
        )
    )
    await call.message.edit_text(text, reply_markup=_admin_stats_kb())
    await call.answer()


# ---------------------------------------------------------------------------
# router
# ---------------------------------------------------------------------------


def make_child_router(tenant_id: int) -> Router:
    router = Router(name=f"tenant-{tenant_id}")

    # ---------- стандартные команды ----------

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        user = message.from_user
        if user is None:
            return

        await _get_or_create_access(
            tenant_id=tenant_id,
            user_id=user.id,
            username=user.username,
        )

        lang = await _get_user_lang(tenant_id, user.id)

        if lang is None:
            await _send_lang_menu(message)
            return

        await _send_main_menu(message, tenant_id, lang)

    @router.message(Command("lang"))
    async def cmd_lang(message: Message) -> None:
        await _send_lang_menu(message)

    # ---------- выбор языка пользователем ----------

    @router.callback_query(F.data.startswith("lang:"))
    async def cb_lang(call: CallbackQuery) -> None:
        user = call.from_user
        if user is None:
            await call.answer()
            return

        _, code = call.data.split(":", 1)
        if code not in LANGS:
            await call.answer("Unknown language", show_alert=True)
            return

        await _set_user_lang(tenant_id, user.id, code)

        text = t_user(code, "lang_changed")
        await call.message.answer(text)
        await _send_main_menu(call.message, tenant_id, code)

        await call.answer()

    @router.callback_query(F.data == "menu:lang")
    async def cb_menu_lang(call: CallbackQuery) -> None:
        await _send_lang_menu(call.message)
        await call.answer()

    @router.callback_query(F.data == "menu:instruction")
    async def cb_menu_instruction(call: CallbackQuery) -> None:
        user = call.from_user
        if user is None:
            await call.answer()
            return
        lang = await _get_user_lang(tenant_id, user.id) or settings.lang_default
        await _send_instruction(call.message, lang)
        await call.answer()

    @router.callback_query(F.data == "menu:back")
    async def cb_menu_back(call: CallbackQuery) -> None:
        user = call.from_user
        if user is None:
            await call.answer()
            return
        lang = await _get_user_lang(tenant_id, user.id) or settings.lang_default
        await _send_main_menu(call.message, tenant_id, lang)
        await call.answer()

    # ---------- «Получить сигнал» + signal:... ----------

    @router.callback_query(F.data == "menu:signal")
    async def cb_menu_signal(call: CallbackQuery) -> None:
        user = call.from_user
        if user is None:
            await call.answer()
            return

        await _handle_signal_flow(
            call.message.bot,
            call.message,
            tenant_id=tenant_id,
            user_id=user.id,
        )
        await call.answer()

    @router.callback_query(F.data == "signal:sub_ok")
    async def cb_signal_sub_ok(call: CallbackQuery) -> None:
        """
        Нажали «Я подписался» — проверяем подписку и идём дальше по цепочке.
        """
        user = call.from_user
        if user is None:
            await call.answer()
            return

        tenant = await _get_tenant(tenant_id)
        if tenant is None:
            await call.answer("Тенант не найден", show_alert=True)
            return

        is_sub = await _check_subscription(call.message.bot, tenant, user.id)
        if not is_sub:
            await call.answer(
                "Я не вижу тебя в канале. Подпишись и попробуй ещё раз.",
                show_alert=True,
            )
            return

        # раз подписка есть — двигаем цепочку дальше
        await _handle_signal_flow(
            call.message.bot,
            call.message,
            tenant_id=tenant_id,
            user_id=user.id,
        )
        await call.answer()

    @router.callback_query(F.data == "signal:open_app")
    async def cb_signal_open_app(call: CallbackQuery) -> None:
        # исторический callback, оставлен для совместимости;
        # сейчас ничего не делает, чтобы не спамить лишними сообщениями.
        user = call.from_user
        if user is None:
            await call.answer()
            return

        lang = await _get_user_lang(tenant_id, user.id) or settings.lang_default
        await _open_miniapp(call.message, lang)
        await call.answer()

    # ---------- админка ----------

    @router.message(Command("admin"))
    async def cmd_admin(message: Message) -> None:
        user = message.from_user
        if user is None:
            return

        if not await _is_tenant_admin(tenant_id, user.id):
            await message.answer(t_admin("no_admin"))
            return

        await message.answer(
            t_admin("menu"),
            reply_markup=_admin_menu_kb(),
        )

    @router.callback_query(F.data.startswith("adm:"))
    async def cb_admin(call: CallbackQuery) -> None:
        user = call.from_user
        if user is None:
            await call.answer()
            return

        if not await _is_tenant_admin(tenant_id, user.id):
            await call.answer("Нет доступа", show_alert=True)
            return

        data = call.data
        parts = data.split(":")

        if len(parts) == 1:
            await call.answer("Неизвестная команда", show_alert=True)
            return

        cmd = parts[1]

        if cmd == "back":
            await call.message.edit_text(
                t_admin("menu"),
                reply_markup=_admin_menu_kb(),
            )
            await call.answer()
            return

        # ---- пользователи ----
        if cmd == "users":
            if len(parts) == 2:
                page = 1
                await _admin_show_users(call, tenant_id, page)
                return

            sub = parts[2]
            if sub == "page":
                try:
                    page = int(parts[3])
                except (IndexError, ValueError):
                    page = 1
                await _admin_show_users(call, tenant_id, page)
                return
            if sub == "search":
                search_user_waiting[user.id] = tenant_id
                await call.message.answer(
                    "Отправь <code>tg_id</code> или <code>trader_id</code> пользователя,"
                    " и я покажу его карточку."
                )
                await call.answer()
                return

        # ---- отдельный пользователь ----
        if cmd == "user":
            if len(parts) < 4:
                await call.answer("Некорректная команда", show_alert=True)
                return
            action = parts[2]
            try:
                uid = int(parts[3])
            except ValueError:
                await call.answer("Некорректный user_id", show_alert=True)
                return

            if action == "show":
                await _admin_show_user_card(call, tenant_id, uid)
                return
            if action in ("reg", "dep"):
                ok = await _admin_toggle_user_flag(tenant_id, uid, action)
                if not ok:
                    await call.answer("Пользователь не найден", show_alert=True)
                    return
                await _admin_show_user_card(call, tenant_id, uid)
                return
            if action == "del":
                await _admin_delete_user_record(tenant_id, uid)
                await call.message.edit_text("Пользователь удалён.")
                await call.answer()
                return

        # ---- постбэки (экран с URL) ----
        if cmd == "events":
            await _admin_show_postbacks(call, tenant_id)
            return

        # ---- параметры ----
        if cmd == "params":
            if len(parts) == 2:
                await _admin_show_params(call, tenant_id)
                return
            action = parts[2]
            if action in ("sub", "dep"):
                ok = await _admin_toggle_param(tenant_id, action)
                if not ok:
                    await call.answer("Не удалось изменить параметр", show_alert=True)
                    return
                await _admin_show_params(call, tenant_id)
                return

        # ---- ссылки ----
        if cmd == "links":
            if len(parts) == 2:
                await _admin_show_links(call, tenant_id)
                return
            sub = parts[2]
            if sub == "set":
                if len(parts) < 4:
                    await call.answer("Некорректная команда", show_alert=True)
                    return
                field = parts[3]
                link_waiting[user.id] = (tenant_id, field)

                if field == "ref":
                    text = "Отправь новую реф-ссылку (полный URL)."
                elif field == "dep":
                    text = "Отправь ссылку на депозит (URL)."
                elif field == "support":
                    text = "Отправь URL поддержки."
                elif field == "chanid":
                    text = "Отправь ID канала (например, -1001234567890)."
                elif field == "chanurl":
                    text = "Отправь URL канала."
                else:
                    await call.answer("Неизвестное поле", show_alert=True)
                    return

                await call.message.answer(
                    text + "\nЕсли хочешь очистить, отправь «-»."
                )
                await call.answer()
                return

        # ---- рассылки ----
        if cmd == "bc":
            # adm:bc -> выбор сегмента
            if len(parts) == 2:
                await _admin_start_broadcast_menu(call, tenant_id)
                return

            sub = parts[2]

            # выбор сегмента
            if sub == "seg":
                if len(parts) < 4:
                    await call.answer("Некорректная команда", show_alert=True)
                    return
                seg = parts[3]
                if seg == "lang":
                    # дальше выбираем язык
                    broadcast_state[user.id] = {
                        "tenant_id": tenant_id,
                        "segment": "lang",
                        "lang_code": None,
                        "stage": "await_lang",
                        "text": None,
                        "media": None,
                    }
                    await call.message.edit_text(
                        "Выбери язык для рассылки:",
                        reply_markup=_admin_broadcast_lang_kb(),
                    )
                    await call.answer()
                    return

                if seg in ("all", "reg", "dep"):
                    broadcast_state[user.id] = {
                        "tenant_id": tenant_id,
                        "segment": seg,
                        "lang_code": None,
                        "stage": "await_text",
                        "text": None,
                        "media": None,
                    }
                    await call.message.answer(t_admin("broadcast_prompt"))
                    await call.answer()
                    return

            # выбор языка для сегмента lang
            if sub == "lang":
                if len(parts) < 4:
                    await call.answer("Некорректная команда", show_alert=True)
                    return
                code = parts[3]
                if code not in LANGS:
                    await call.answer("Неизвестный язык", show_alert=True)
                    return
                broadcast_state[user.id] = {
                    "tenant_id": tenant_id,
                    "segment": "lang",
                    "lang_code": code,
                    "stage": "await_text",
                    "text": None,
                    "media": None,
                }
                await call.message.answer(
                    t_admin("broadcast_prompt")
                    + f"\n\nВыбран язык: {NATIVE_LANG_NAMES.get(code, code)}"
                )
                await call.answer()
                return

            # вопрос про медиа
            if sub == "media":
                if len(parts) < 4:
                    await call.answer("Некорректная команда", show_alert=True)
                    return
                choice = parts[3]
                state = broadcast_state.get(user.id)
                if not state or state.get("tenant_id") != tenant_id:
                    await call.answer("Нет активной рассылки", show_alert=True)
                    return
                if choice == "yes":
                    state["stage"] = "await_media"
                    await call.message.answer(
                        "Отправь фото или видео для рассылки (можно с подписью)."
                    )
                    await call.answer()
                    return
                if choice == "no":
                    state["stage"] = "ask_time"
                    await _admin_ask_time(call.message, user.id)
                    await call.answer()
                    return

            # выбор времени
            if sub == "time":
                if len(parts) < 4:
                    await call.answer("Некорректная команда", show_alert=True)
                    return
                choice = parts[3]
                state = broadcast_state.get(user.id)
                if not state or state.get("tenant_id") != tenant_id:
                    await call.answer("Нет активной рассылки", show_alert=True)
                    return
                if choice == "now":
                    text_val = str(state.get("text") or "")
                    media_val = state.get("media")  # type: ignore[assignment]
                    seg = str(state.get("segment"))
                    code = state.get("lang_code")
                    broadcast_state.pop(user.id, None)
                    sent, failed = await _admin_do_broadcast(
                        call.message.bot,
                        call.message.chat.id,
                        tenant_id,
                        seg,
                        code,  # type: ignore[arg-type]
                        text_val,
                        media_val,  # type: ignore[arg-type]
                    )
                    await call.message.answer(
                        t_admin("broadcast_done", sent=sent, failed=failed)
                    )
                    await call.answer()
                    return
                if choice == "later":
                    state["stage"] = "await_time"
                    await call.message.answer(
                        t_admin("broadcast_time_hint")
                    )
                    await call.answer()
                    return

            # отмена
            if sub == "cancel":
                broadcast_state.pop(user.id, None)
                await call.message.answer(t_admin("broadcast_cancelled"))
                await call.answer()
                return

        # ---- статистика ----
        if cmd == "stats":
            await _admin_show_stats(call, tenant_id)
            return

        await call.answer("Неизвестная команда", show_alert=True)

    # ---------- обработка текстов ----------

    @router.message(F.text)
    async def handle_text(message: Message) -> None:
        text = (message.text or "").strip()
        if not text:
            return

        user = message.from_user
        if user is None:
            return

        # поиск пользователя
        tid_search = search_user_waiting.pop(user.id, None)
        if (
            tid_search is not None
            and tid_search == tenant_id
            and await _is_tenant_admin(tenant_id, user.id)
        ):
            await _admin_search_and_show_user(message, tenant_id, text)
            return

        # ввод ссылки
        link_state = link_waiting.pop(user.id, None)
        if (
            link_state is not None
            and link_state[0] == tenant_id
            and await _is_tenant_admin(tenant_id, user.id)
        ):
            field = link_state[1]
            ok = await _admin_update_link_value(tenant_id, field, text)
            if not ok:
                await message.answer("Не удалось сохранить значение.")
            else:
                await _admin_send_links_message(message, tenant_id)
            return

        # шаги рассылки
        state = broadcast_state.get(user.id)
        if (
            state is not None
            and state.get("tenant_id") == tenant_id
            and await _is_tenant_admin(tenant_id, user.id)
        ):
            stage = state.get("stage")

            # ожидаем текст рассылки
            if stage == "await_text":
                state["text"] = text
                state["stage"] = "ask_media"
                await message.answer(
                    t_admin("broadcast_media_question"),
                    reply_markup=_admin_broadcast_media_kb(),
                )
                return

            # ожидаем время по МСК
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
                    await message.answer(t_admin("broadcast_time_parse_error"))
                    return

                text_val = str(state.get("text") or "")
                media_val = state.get("media")  # type: ignore[assignment]
                seg = str(state.get("segment"))
                code = state.get("lang_code")
                broadcast_state.pop(user.id, None)

                asyncio.create_task(
                    _scheduled_broadcast(
                        message.bot,
                        message.chat.id,
                        tenant_id,
                        seg,
                        code,  # type: ignore[arg-type]
                        text_val,
                        media_val,  # type: ignore[arg-type]
                        delay,
                    )
                )

                await message.answer(
                    t_admin("broadcast_scheduled", time=text)
                )
                return

        # обычный пользовательский фоллбек
        lang = await _get_user_lang(tenant_id, user.id)
        if lang is None:
            await _send_lang_menu(message)
        else:
            await _send_main_menu(message, tenant_id, lang)

    # ---------- медиа (для рассылок) ----------

    @router.message(F.photo | F.video | F.document | F.animation)
    async def handle_media(message: Message) -> None:
        user = message.from_user
        if user is None:
            return

        state = broadcast_state.get(user.id)
        if (
            state is not None
            and state.get("tenant_id") == tenant_id
            and await _is_tenant_admin(tenant_id, user.id)
        ):
            stage = state.get("stage")
            if stage == "await_media":
                media: dict | None = None
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
                await _admin_ask_time(message, user.id)
                return

        # если это не часть рассылки — пока игнорируем

    return router


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------


async def run_child_bot(bot_token: str, tenant_id: int) -> None:
    tenant = await _get_tenant(tenant_id)
    if tenant is None:
        logger.error("Tenant %s not found, child bot will not start", tenant_id)
        return

    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher()
    dp.include_router(make_child_router(tenant_id))

    logger.info("Starting child bot for tenant %s", tenant_id)
    try:
        await dp.start_polling(bot)
    except Exception as e:  # noqa: BLE001
        logger.exception("Child bot for tenant %s crashed: %s", tenant_id, e)
    finally:
        await bot.session.close()
        logger.info("Child bot for tenant %s stopped", tenant_id)