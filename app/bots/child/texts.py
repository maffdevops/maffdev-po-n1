from typing import Dict

# Какие языки доступны пользователю
LANGS = ["en", "ru", "hi", "ar", "es", "fr", "ro"]

# Названия языков в меню выбора (на родном языке)
NATIVE_LANG_NAMES: Dict[str, str] = {
    "en": "English",
    "ru": "Русский",
    "hi": "हिन्दी",
    "ar": "العربية",
    "es": "Español",
    "fr": "Français",
    "ro": "Română",
}


# ---------------------------------------------------------------------------
# Пользовательские тексты (по языкам)
# ---------------------------------------------------------------------------

# Ключи, которые должны быть определены для каждого языка:
# - choose_lang
# - lang_changed
# - menu_title
# - menu_body
# - btn_instruction
# - btn_support
# - btn_lang
# - btn_signal
# - instruction_title
# - instruction_body
# - back_to_menu
# - sub_title
# - sub_body
# - btn_subscribe
# - btn_i_subscribed
# - reg_title
# - reg_body
# - btn_register
# - dep_title
# - dep_body
# - btn_deposit
# - access_title
# - access_body
# - btn_open_app

USER_TEXTS: Dict[str, Dict[str, str]] = {
    "en": {
        "choose_lang": "Choose your language 👇",
        "lang_changed": "Language has been changed ✅",

        "menu_title": "Pocket Signals",
        "menu_body": (
            "Here you will get trading signals and instructions.\n\n"
            "Start with the instructions so everything works correctly."
        ),

        "btn_instruction": "📘 Instructions",
        "btn_support": "💬 Support",
        "btn_lang": "🌐 Language",
        "btn_signal": "📲 Get signal",

        "instruction_title": "How to start",
        "instruction_body": (
            "1. Subscribe to the channel (if required).\n"
            "2. Register with the broker using our link.\n"
            "3. Make a deposit (if required).\n"
            "4. Click «Get signal» and follow the instructions in the mini app."
        ),

        "back_to_menu": "⬅️ Back to menu",

        "sub_title": "Subscribe to the channel",
        "sub_body": (
            "To continue you need to subscribe to the channel.\n\n"
            "After subscribing, return to the bot and press «I subscribed»."
        ),
        "btn_subscribe": "📲 Open channel",
        "btn_i_subscribed": "✅ I subscribed",

        "reg_title": "Registration",
        "reg_body": (
            "Next step is registration with the broker.\n\n"
            "It is important to register using our link so that access to signals "
            "is opened automatically."
        ),
        "btn_register": "📝 Register",

        "dep_title": "Make a deposit",
        "dep_body": (
            "To get full access to the signals, you need to make a deposit "
            "to your trading account.\n\n"
            "After the deposit is credited, the bot will open access automatically."
        ),
        "btn_deposit": "💳 Make a deposit",

        "access_title": "Access is open",
        "access_body": (
            "Everything is ready! Access to the signals is open.\n\n"
            "Press the button below to open the mini app and get your signal."
        ),
        "btn_open_app": "📲 Open mini app",
    },

    "ru": {
        "choose_lang": "Выбери свой язык 👇",
        "lang_changed": "Язык успешно изменён ✅",

        "menu_title": "Pocket Signals",
        "menu_body": (
            "Здесь ты будешь получать торговые сигналы и инструкции.\n\n"
            "Начни с инструкции, чтобы всё работало правильно."
        ),

        "btn_instruction": "📘 Инструкция",
        "btn_support": "💬 Поддержка",
        "btn_lang": "🌐 Язык",
        "btn_signal": "📲 Получить сигнал",

        "instruction_title": "Как начать",
        "instruction_body": (
            "1. Подпишись на канал (если требуется).\n"
            "2. Зарегистрируйся у брокера по нашей ссылке.\n"
            "3. Пополни депозит (если требуется).\n"
            "4. Нажми «Получить сигнал» и следуй шагам в мини-приложении."
        ),

        "back_to_menu": "⬅️ В меню",

        "sub_title": "Подписка на канал",
        "sub_body": (
            "Чтобы продолжить, нужно подписаться на канал.\n\n"
            "После подписки вернись в бота и нажми «Я подписался»."
        ),
        "btn_subscribe": "📲 Открыть канал",
        "btn_i_subscribed": "✅ Я подписался",

        "reg_title": "Регистрация",
        "reg_body": (
            "Следующий шаг — регистрация у брокера.\n\n"
            "Важно зарегистрироваться именно по нашей ссылке, чтобы доступ "
            "к сигналам открылся автоматически."
        ),
        "btn_register": "📝 Зарегистрироваться",

        "dep_title": "Пополни депозит",
        "dep_body": (
            "Чтобы получить полный доступ к сигналам, пополни торговый счёт.\n\n"
            "После зачисления депозита бот автоматически откроет доступ."
        ),
        "btn_deposit": "💳 Пополнить депозит",

        "access_title": "Доступ открыт",
        "access_body": (
            "Готово! Доступ к сигналам открыт.\n\n"
            "Нажми на кнопку ниже, чтобы открыть мини-приложение и получить сигнал."
        ),
        "btn_open_app": "📲 Открыть мини-приложение",
    },

    "hi": {
        "choose_lang": "अपनी भाषा चुनें 👇",
        "lang_changed": "भाषा बदल दी गई ✅",

        "menu_title": "Pocket Signals",
        "menu_body": (
            "यहाँ आप ट्रेडिंग सिग्नल और निर्देश प्राप्त करेंगे।\n\n"
            "सब कुछ सही काम करे, इसके लिए पहले निर्देश पढ़ें।"
        ),

        "btn_instruction": "📘 निर्देश",
        "btn_support": "💬 सपोर्ट",
        "btn_lang": "🌐 भाषा",
        "btn_signal": "📲 सिग्नल प्राप्त करें",

        "instruction_title": "कैसे शुरू करें",
        "instruction_body": (
            "1. चैनल को सब्सक्राइब करें (अगर आवश्यक है).\n"
            "2. हमारी रेफरल लिंक से ब्रोक़र पर रजिस्टर करें.\n"
            "3. अगर आवश्यक हो तो डिपॉज़िट करें.\n"
            "4. «सिग्नल प्राप्त करें» दबाएँ और मिनी-ऐप में दिए गए चरणों का पालन करें."
        ),

        "back_to_menu": "⬅️ मेनू पर वापस",

        "sub_title": "चैनल सब्सक्रिप्शन",
        "sub_body": (
            "आगे बढ़ने के लिए आपको चैनल को सब्सक्राइब करना होगा।\n\n"
            "सब्सक्राइब करने के बाद बॉट पर वापस आएँ और «मैंने सब्सक्राइब किया» दबाएँ."
        ),
        "btn_subscribe": "📲 चैनल खोलें",
        "btn_i_subscribed": "✅ मैंने सब्सक्राइब किया",

        "reg_title": "रजिस्ट्रेशन",
        "reg_body": (
            "अगला कदम ब्रोक़र पर रजिस्ट्रेशन है।\n\n"
            "यह ज़रूरी है कि आप हमारी लिंक से रजिस्टर करें ताकि सिग्नल का एक्सेस "
            "अपनेआप खुल जाए."
        ),
        "btn_register": "📝 रजिस्टर करें",

        "dep_title": "डिपॉज़िट करें",
        "dep_body": (
            "सिग्नल का पूरा एक्सेस पाने के लिए आपको अपने ट्रेडिंग अकाउंट में "
            "डिपॉज़िट करना होगा.\n\n"
            "डिपॉज़िट आने के बाद बॉट अपनेआप एक्सेस खोल देगा."
        ),
        "btn_deposit": "💳 डिपॉज़िट करें",

        "access_title": "एक्सेस खुल गया",
        "access_body": (
            "सब सेट है! सिग्नल का एक्सेस खुल चुका है.\n\n"
            "नीचे दिए गए बटन को दबाएँ, मिनी-ऐप खोलें और सिग्नल प्राप्त करें."
        ),
        "btn_open_app": "📲 मिनी-ऐप खोलें",
    },

    "ar": {
        "choose_lang": "اختر لغتك 👇",
        "lang_changed": "تم تغيير اللغة ✅",

        "menu_title": "Pocket Signals",
        "menu_body": (
            "ستحصل هنا على إشارات التداول والتعليمات.\n\n"
            "ابدأ بالتعليمات حتى يعمل كل شيء بشكل صحيح."
        ),

        "btn_instruction": "📘 التعليمات",
        "btn_support": "💬 الدعم",
        "btn_lang": "🌐 اللغة",
        "btn_signal": "📲 الحصول على إشارة",

        "instruction_title": "كيف تبدأ",
        "instruction_body": (
            "1. اشترك في القناة (إذا كان ذلك مطلوباً).\n"
            "2. سجّل في شركة الوساطة من خلال رابطنا.\n"
            "3. أودِع الأموال (إذا كان ذلك مطلوباً).\n"
            "4. اضغط «الحصول على إشارة» واتبع الخطوات في التطبيق المصغّر."
        ),

        "back_to_menu": "⬅️ العودة إلى القائمة",

        "sub_title": "الاشتراك في القناة",
        "sub_body": (
            "للمتابعة يجب عليك الاشتراك في القناة.\n\n"
            "بعد الاشتراك ارجع إلى البوت واضغط «لقد اشتركت»."
        ),
        "btn_subscribe": "📲 فتح القناة",
        "btn_i_subscribed": "✅ لقد اشتركت",

        "reg_title": "التسجيل",
        "reg_body": (
            "الخطوة التالية هي التسجيل لدى الوسيط.\n\n"
            "من المهم أن تسجّل من خلال رابطنا لكي يتم فتح الوصول إلى الإشارات "
            "بشكل تلقائي."
        ),
        "btn_register": "📝 التسجيل",

        "dep_title": "إيداع الأموال",
        "dep_body": (
            "للحصول على وصول كامل إلى الإشارات يجب أن تقوم بإيداع في حساب التداول.\n\n"
            "بعد وصول الإيداع سيتم فتح الوصول تلقائياً."
        ),
        "btn_deposit": "💳 القيام بالإيداع",

        "access_title": "تم فتح الوصول",
        "access_body": (
            "كل شيء جاهز! تم فتح الوصول إلى الإشارات.\n\n"
            "اضغط الزر في الأسفل لفتح التطبيق المصغّر والحصول على الإشارة."
        ),
        "btn_open_app": "📲 فتح التطبيق المصغّر",
    },

    "es": {
        "choose_lang": "Elige tu idioma 👇",
        "lang_changed": "El idioma ha sido cambiado ✅",

        "menu_title": "Pocket Signals",
        "menu_body": (
            "Aquí recibirás señales de trading e instrucciones.\n\n"
            "Empieza por las instrucciones para que todo funcione correctamente."
        ),

        "btn_instruction": "📘 Instrucciones",
        "btn_support": "💬 Soporte",
        "btn_lang": "🌐 Idioma",
        "btn_signal": "📲 Obtener señal",

        "instruction_title": "Cómo empezar",
        "instruction_body": (
            "1. Suscríbete al canal (si es necesario).\n"
            "2. Regístrate en el bróker usando nuestro enlace.\n"
            "3. Haz un depósito (si es requerido).\n"
            "4. Pulsa «Obtener señal» y sigue los pasos en la mini-app."
        ),

        "back_to_menu": "⬅️ Volver al menú",

        "sub_title": "Suscripción al canal",
        "sub_body": (
            "Para continuar debes suscribirte al canal.\n\n"
            "Después de suscribirte, vuelve al bot y pulsa «Ya me he suscrito»."
        ),
        "btn_subscribe": "📲 Abrir canal",
        "btn_i_subscribed": "✅ Ya me he suscrito",

        "reg_title": "Registro",
        "reg_body": (
            "El siguiente paso es registrarse en el bróker.\n\n"
            "Es importante registrarse usando nuestro enlace para que el acceso "
            "a las señales se abra automáticamente."
        ),
        "btn_register": "📝 Registrarse",

        "dep_title": "Hacer un depósito",
        "dep_body": (
            "Para obtener acceso completo a las señales debes hacer un depósito "
            "en tu cuenta de trading.\n\n"
            "Cuando el depósito se acredite, el bot abrirá el acceso automáticamente."
        ),
        "btn_deposit": "💳 Hacer depósito",

        "access_title": "Acceso abierto",
        "access_body": (
            "¡Todo listo! El acceso a las señales está abierto.\n\n"
            "Pulsa el botón de abajo para abrir la mini-app y obtener tu señal."
        ),
        "btn_open_app": "📲 Abrir mini-app",
    },

    "fr": {
        "choose_lang": "Choisis ta langue 👇",
        "lang_changed": "La langue a été modifiée ✅",

        "menu_title": "Pocket Signals",
        "menu_body": (
            "Ici tu recevras des signaux de trading et des instructions.\n\n"
            "Commence par les instructions pour que tout fonctionne correctement."
        ),

        "btn_instruction": "📘 Instructions",
        "btn_support": "💬 Support",
        "btn_lang": "🌐 Langue",
        "btn_signal": "📲 Obtenir un signal",

        "instruction_title": "Comment commencer",
        "instruction_body": (
            "1. Abonne-toi à la chaîne (si nécessaire).\n"
            "2. Inscris-toi chez le broker via notre lien.\n"
            "3. Fais un dépôt (si nécessaire).\n"
            "4. Clique sur « Obtenir un signal » et suis les étapes dans la mini-app."
        ),

        "back_to_menu": "⬅️ Retour au menu",

        "sub_title": "Abonnement à la chaîne",
        "sub_body": (
            "Pour continuer, tu dois t'abonner à la chaîne.\n\n"
            "Après l'abonnement, reviens sur le bot et clique sur « Je me suis abonné »."
        ),
        "btn_subscribe": "📲 Ouvrir la chaîne",
        "btn_i_subscribed": "✅ Je me suis abonné",

        "reg_title": "Inscription",
        "reg_body": (
            "L'étape suivante est l'inscription chez le broker.\n\n"
            "Il est important de s'inscrire via notre lien pour que l'accès "
            "aux signaux s'ouvre automatiquement."
        ),
        "btn_register": "📝 S'inscrire",

        "dep_title": "Effectuer un dépôt",
        "dep_body": (
            "Pour avoir un accès complet aux signaux, tu dois effectuer un dépôt "
            "sur ton compte de trading.\n\n"
            "Une fois le dépôt crédité, le bot ouvrira l'accès automatiquement."
        ),
        "btn_deposit": "💳 Faire un dépôt",

        "access_title": "Accès ouvert",
        "access_body": (
            "Tout est prêt ! L'accès aux signaux est ouvert.\n\n"
            "Clique sur le bouton ci-dessous pour ouvrir la mini-app et recevoir un signal."
        ),
        "btn_open_app": "📲 Ouvrir la mini-app",
    },

    "ro": {
        "choose_lang": "Alege limba ta 👇",
        "lang_changed": "Limba a fost schimbată ✅",

        "menu_title": "Pocket Signals",
        "menu_body": (
            "Aici vei primi semnale de tranzacționare și instrucțiuni.\n\n"
            "Începe cu instrucțiunile ca totul să funcționeze corect."
        ),

        "btn_instruction": "📘 Instrucțiuni",
        "btn_support": "💬 Suport",
        "btn_lang": "🌐 Limbă",
        "btn_signal": "📲 Primește semnal",

        "instruction_title": "Cum să începi",
        "instruction_body": (
            "1. Abonează-te la canal (dacă este necesar).\n"
            "2. Înregistrează-te la broker folosind linkul nostru.\n"
            "3. Fă un depozit (dacă este necesar).\n"
            "4. Apasă «Primește semnal» și urmează pașii din mini-aplicație."
        ),

        "back_to_menu": "⬅️ Înapoi la meniu",

        "sub_title": "Abonare la canal",
        "sub_body": (
            "Pentru a continua trebuie să te abonezi la canal.\n\n"
            "După abonare, revino în bot și apasă «M-am abonat»."
        ),
        "btn_subscribe": "📲 Deschide canalul",
        "btn_i_subscribed": "✅ M-am abonat",

        "reg_title": "Înregistrare",
        "reg_body": (
            "Următorul pas este înregistrarea la broker.\n\n"
            "Este important să te înregistrezi prin linkul nostru pentru ca accesul "
            "la semnale să se deschidă automat."
        ),
        "btn_register": "📝 Înregistrează-te",

        "dep_title": "Fă un depozit",
        "dep_body": (
            "Pentru acces complet la semnale trebuie să faci un depozit "
            "în contul tău de tranzacționare.\n\n"
            "După ce depozitul este creditat, botul va deschide automat accesul."
        ),
        "btn_deposit": "💳 Fă un depozit",

        "access_title": "Acces deschis",
        "access_body": (
            "Gata! Accesul la semnale este deschis.\n\n"
            "Apasă butonul de mai jos pentru a deschide mini-aplicația și a primi semnalul."
        ),
        "btn_open_app": "📲 Deschide mini-aplicația",
    },
}


# ---------------------------------------------------------------------------
# Тексты админки (один язык — русский)
# ---------------------------------------------------------------------------

ADMIN_TEXTS: Dict[str, str] = {
    "no_admin": "У тебя нет прав администратора для этого бота.",

    "menu": (
        "Админ-панель\n\n"
        "Выбери действие в меню ниже."
    ),

    "links_header": "🔗 Ссылки и параметры доступа",
    "params_header": "⚙️ Параметры проверки доступа",

    "broadcast_seg_all": "Всем пользователям",
    "broadcast_seg_reg": "Только зарегистрированные",
    "broadcast_seg_dep": "Только с депозитом",
    "broadcast_seg_lang": "По языку",

    "broadcast_choose": "Выбери сегмент аудитории для рассылки:",
    "broadcast_prompt": "Отправь текст рассылки одним сообщением.",
    "broadcast_empty": "Под подходящий сегмент не найдено ни одного пользователя.",

    "broadcast_media_add": "Добавить медиа",
    "broadcast_media_skip": "Без медиа",

    "broadcast_media_question": (
        "Хочешь добавить к рассылке фото/видео/документ?"
    ),

    "broadcast_time_question": (
        "Когда отправить рассылку?"
    ),
    "broadcast_time_later": "Отправить позже",
    "broadcast_time_now": "Отправить сейчас",

    "broadcast_time_hint": (
        "Отправь время по МСК в формате ЧЧ:ММ, например 15:30.\n"
        "Если время уже прошло — отправим на следующий день."
    ),
    "broadcast_time_parse_error": (
        "Не получилось разобрать время. Отправь в формате ЧЧ:ММ, например 09:45."
    ),

    "broadcast_scheduled": "Рассылка запланирована на {time} по МСК ✅",
    "broadcast_done": "Рассылка завершена.\n\nОтправлено: {sent}\nОшибок: {failed}",
    "broadcast_cancelled": "Рассылка отменена.",

    "stats_header": "📊 Статистика по боту",
    "stats_body": (
        "Всего пользователей: <b>{total_users}</b>\n"
        "Подписчиков (в боте): <b>{subs}</b>\n"
        "С регистрацией: <b>{regs}</b>\n"
        "С депозитом: <b>{deps}</b>\n\n"
        "Всего депозитов (сумма): <b>{total_amount}</b>\n"
        "Количество депозитов (FTD+RD): <b>{count}</b>"
    ),
}


# ---------------------------------------------------------------------------
# Функции доступа к текстам
# ---------------------------------------------------------------------------


def _safe_get_text(
    mapping: Dict[str, Dict[str, str]],
    lang: str,
    key: str,
    default_lang: str = "en",
) -> str:
    # основной язык
    lang_map = mapping.get(lang) or mapping.get(default_lang) or {}
    text = lang_map.get(key)
    if text is not None:
        return text
    # если даже в дефолтном нет — просто возвращаем имя ключа,
    # чтобы не падать KeyError'ом
    return key


def t_user(lang: str, key: str, **kwargs) -> str:
    """
    Текст для пользователя. Есть fallback по языку и форматирование через .format().
    """
    text = _safe_get_text(USER_TEXTS, lang, key, default_lang="en")
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            # если форматирование не удалось — хотя бы вернём сырой текст
            return text
    return text


def t_admin(key: str, **kwargs) -> str:
    """
    Текст админки (один язык — русский).
    """
    text = ADMIN_TEXTS.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text