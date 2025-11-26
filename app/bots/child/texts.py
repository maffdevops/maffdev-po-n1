from typing import Dict, List

from app.settings import settings

# Какие языки доступны пользователю
LANGS: List[str] = ["en", "ru", "hi", "ar", "es", "fr", "ro"]

# Названия языков для кнопок
NATIVE_LANG_NAMES: Dict[str, str] = {
    "en": "🇬🇧 English",
    "ru": "🇷🇺 Русский",
    "hi": "🇮🇳 हिन्दी",
    "ar": "🇦🇪 عربي",
    "es": "🇪🇸 Español",
    "fr": "🇫🇷 Français",
    "ro": "🇷🇴 Română",
}

# ---------- Пользовательские тексты (многоязычные) ----------

USER_TEXTS: Dict[str, Dict[str, str]] = {
    "ru": {
        "choose_lang": "Выберите язык:",
        "lang_changed": "Язык сохранён: Русский 🇷🇺",

        "menu_title": "🏠 Главное меню",
        "menu_body": "Здесь будет доступ к сигналам, инструкции и поддержке.",
        "btn_instruction": "📘 Инструкция",
        "btn_support": "🆘 Поддержка",
        "btn_lang": "🌐 Сменить язык",
        "btn_signal": "📈 Получить сигнал",

        "instruction_title": "📘 Инструкция",
        "instruction_body": (
            "Здесь будет твоя инструкция: как подписаться, зарегистрироваться у брокера "
            "и начать получать сигналы.\n\n"
            "Пока это заглушка — позже подставим реальные тексты."
        ),
        "back_to_menu": "⬅️ В меню",

        "signal_coming_soon": "Скоро здесь будет логика получения сигналов 🔧",

        # --- Онбординг по кнопке «Получить сигнал» ---

        # Шаг 1 — подписка
        "sub_title": "Шаг 1. Подписка на канал",
        "sub_body": "Первым шагом подпишитесь на канал по кнопке ниже, чтобы продолжить.",
        "btn_subscribe": "Подписаться",
        "btn_i_subscribed": "Я подписался",

        # Шаг 2 — регистрация
        "reg_title": "Шаг 2. Регистрация у брокера",
        "reg_body": (
            "Чтобы использовать бота, необходимо зарегистрироваться или создать новый "
            "аккаунт по ссылке ниже."
        ),
        "btn_register": "Зарегистрироваться",

        # Шаг 3 — депозит
        "dep_title": "Шаг 3. Внесение депозита",
        "dep_body": (
            "Последний шаг — внесите свой депозит на сайте брокера, чтобы сразу начать работу."
        ),
        "btn_deposit": "Внести депозит",

        # Финальное окно
        "access_title": "✅ Доступ открыт",
        "access_body": (
            "Вам открыт доступ к инструменту для заработка, можете начать прямо сейчас."
        ),
        "btn_open_app": "📈 Получить сигнал",
    },
    "en": {
        "choose_lang": "Choose your language:",
        "lang_changed": "Language saved: English 🇬🇧",

        "menu_title": "🏠 Main menu",
        "menu_body": "Here you will get access to signals, instructions and support.",
        "btn_instruction": "📘 Instruction",
        "btn_support": "🆘 Support",
        "btn_lang": "🌐 Change language",
        "btn_signal": "📈 Get signal",

        "instruction_title": "📘 Instruction",
        "instruction_body": (
            "Here will be your instruction: how to subscribe, register with the broker, "
            "and start receiving signals.\n\n"
            "For now it's just a placeholder."
        ),
        "back_to_menu": "⬅️ Back to menu",

        "signal_coming_soon": "Signal flow will be added soon 🔧",

        # Onboarding — step 1: subscription
        "sub_title": "Step 1. Subscribe to the channel",
        "sub_body": "First step: subscribe to the channel using the button below to continue.",
        "btn_subscribe": "Subscribe",
        "btn_i_subscribed": "I've subscribed",

        # Onboarding — step 2: registration
        "reg_title": "Step 2. Broker registration",
        "reg_body": (
            "To use the bot, you need to register or create a new account using the link below."
        ),
        "btn_register": "Register",

        # Onboarding — step 3: deposit
        "dep_title": "Step 3. Make a deposit",
        "dep_body": (
            "Last step — make your deposit on the broker's website to start working immediately."
        ),
        "btn_deposit": "Make a deposit",

        # Final screen
        "access_title": "✅ Access granted",
        "access_body": (
            "You now have access to the earning tool and can start right away."
        ),
        "btn_open_app": "📈 Get signal",
    },
}

# остальные языки пока используют английские тексты
for code in ("hi", "ar", "es", "fr", "ro"):
    USER_TEXTS[code] = USER_TEXTS["en"]


def t_user(lang: str, key: str, **kwargs) -> str:
    """
    Текст для пользователя (зависит от выбранного языка).
    """
    base = USER_TEXTS.get(settings.lang_default, USER_TEXTS["en"])
    raw_dict = USER_TEXTS.get(lang, base)
    raw = raw_dict.get(key, base.get(key, key))
    try:
        return raw.format(**kwargs)
    except Exception:  # noqa: BLE001
        return raw


# ---------- Админские тексты (всегда на русском) ----------

ADMIN_TEXTS_RU: Dict[str, str] = {
    "no_admin": "❌ У тебя нет доступа к админке этого бота.",
    "menu": "👑 Админ-меню бота:",

    # пользователи
    "users_header": "👥 Пользователи этого бота:",
    "users_stats": (
        "Всего: <b>{total}</b>\n"
        "С регистрацией: <b>{regs}</b>\n"
        "С депозитом: <b>{deps}</b>"
    ),

    # постбэки
    "events_header": "📩 Последние постбэки:",
    "events_empty": "Постбэков пока нет.",

    # параметры
    "params_header": "⚙️ Параметры бота",

    # ссылки
    "links_header": "🔗 Настройка ссылок",

    # рассылка
    "broadcast_choose": "Выбери аудиторию для рассылки:",
    "broadcast_prompt": (
        "✏️ Отправь <b>текст</b> рассылки одним сообщением.\n\n"
        "Поддерживается форматирование Telegram."
    ),
    "broadcast_media_question": "Добавить к рассылке фото или видео?",
    "broadcast_media_add": "➕ Добавить фото/видео",
    "broadcast_media_skip": "Без медиа",
    "broadcast_cancelled": "Рассылка отменена.",

    "broadcast_time_question": "Когда запустить рассылку?",
    "broadcast_time_now": "Запустить сейчас",
    "broadcast_time_later": "Запланировать по времени",
    "broadcast_time_hint": (
        "Отправь время по МСК в формате <b>ЧЧ:ММ</b>, например <code>15:40</code>."
    ),
    "broadcast_time_parse_error": (
        "Не получилось разобрать время. Отправь в формате <b>ЧЧ:ММ</b>, "
        "например <code>15:40</code>."
    ),
    "broadcast_scheduled": "Ок, запланировал рассылку на <b>{time}</b> по МСК.",

    "broadcast_empty": "Пока нет ни одного пользователя — рассылать некому.",
    "broadcast_done": (
        "Рассылка завершена.\n"
        "✅ Успешно: <b>{sent}</b>\n"
        "⚠️ Ошибок: <b>{failed}</b>"
    ),
    "broadcast_seg_all": "Всем",
    "broadcast_seg_reg": "С регистрацией",
    "broadcast_seg_dep": "С депозитом",
    "broadcast_seg_lang": "По языку",

    # статистика
    "stats_header": "📊 Статистика",
    "stats_body": (
        "Всего пользователей: <b>{total_users}</b>\n"
        "С подпиской: <b>{subs}</b>\n"
        "С регистрацией: <b>{regs}</b>\n"
        "С депозитом: <b>{deps}</b>\n"
        "Общая сумма депозитов: <b>{total_amount}</b>\n"
        "Количество депозитных событий (ftd+rd): <b>{count}</b>"
    ),
}


def t_admin(key: str, **kwargs) -> str:
    """
    Текст для админки — всегда на русском, не зависит от языка пользователя.
    """
    raw = ADMIN_TEXTS_RU.get(key, key)
    try:
        return raw.format(**kwargs)
    except Exception:  # noqa: BLE001
        return raw