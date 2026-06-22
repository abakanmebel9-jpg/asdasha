"""AI Router v5.3 — MULTI-PROVIDER FALLBACK + HUMAN-LIKE personality (Dasha Bot).

v5.3 UPDATES:
- Pollinations full 39-model sweep: 11 working models, all 5/5 Russian quality
- New model: mistral-small (fast, excellent Russian)
- Multi-image support in channel posts (send_media_group, up to 10 photos)
- News image dedup: removes CDN multi-resolution variants
- Pollinations auth (with API key) restricted to CHAT/FUNCTION routes only
- COMMENT route uses Pollinations free tier only (preserves key quota)
- Route-based model selection for Pollinations auth
- All providers are OpenAI-compatible (except local llama-cpp)
- Automatic provider discovery — only configured providers (with API keys) are used

FALLBACK CHAIN:
  1. LOCAL:      RuadaptQwen3-4B (primary, no internet)
  2. GITHUB:     GPT-4.1-mini via PAT (free, best Russian quality)
  3. HUGGINGFACE: Qwen2.5-7B (free, good Russian)
  4. GROQ:       Llama-3.3-70B (free, ULTRA FAST ~1s)
  5. GEMINI:     Gemini-2.0-Flash (free, excellent Russian)
  6. OPENROUTER: Llama-3.3-70B:free (free, many models)
  7. CEREBRAS:   Llama-3.3-70B (free, ultra-fast ~0.3s)
  8. POLLINATIONS: 11 models (AUTH for CHAT/FUNCTION, FREE for COMMENT)

ROUTE STRATEGY:
  CHAT     → Local → GitHub → HuggingFace → Groq → Gemini → OpenRouter → Cerebras → Pollinations (auth)
  COMMENT  → GitHub → HuggingFace → Groq → Gemini → OpenRouter → Cerebras → Pollinations (free only)
  FUNCTION → Local → GitHub → HuggingFace → Groq → Gemini → OpenRouter → Cerebras → Pollinations (auth)

PERSONALITY:
- Даша общается естественно, как живой человек. Не делает проблем из вопроса «ты бот?».
- Настроение: варьируется по времени суток и контексту
- Референсы на личный опыт как дизайнера
- Естественные разговорные паттерны, междометия, живой язык
"""

import hashlib
import logging
import random
import re
from typing import Optional, List, Dict
from datetime import datetime
from zoneinfo import ZoneInfo

from ai.providers.base import BaseAIProvider, AIResponse
from ai.providers.local_provider import LocalProvider
from ai.providers.pollinations_provider import PollinationsProvider
from ai.providers.github_provider import GitHubModelsProvider
from ai.providers.huggingface_provider import HuggingFaceProvider
from ai.providers.groq_provider import GroqProvider
from ai.providers.gemini_provider import GeminiProvider
from ai.providers.openrouter_provider import OpenRouterProvider
from ai.providers.cerebras_provider import CerebrasProvider
from ai.providers.provider_manager import (
    ProviderManager, ROUTE_CHAT, ROUTE_COMMENT, ROUTE_FUNCTION,
)

logger = logging.getLogger("dasha.ai.router")

# ════════════════════════════════════════════════════════════════════════════
# Mood system — Даша имеет настроение, зависящее от времени суток
# ════════════════════════════════════════════════════════════════════════════

def _get_time_context() -> str:
    """Возвращает контекст времени суток для настроения Даши."""
    try:
        hour = datetime.now(ZoneInfo("Europe/Moscow")).hour
    except Exception:
        hour = 12

    if 5 <= hour < 10:
        return "утро"
    elif 10 <= hour < 14:
        return "день"
    elif 14 <= hour < 18:
        return "после обеда"
    elif 18 <= hour < 22:
        return "вечер"
    else:
        return "поздний вечер"


def _get_mood_affix() -> str:
    """Возвращает настроение Даши как часть промпта."""
    try:
        hour = datetime.now(ZoneInfo("Europe/Moscow")).hour
    except Exception:
        hour = 12

    if 5 <= hour < 10:
        moods = [
            "У тебя сейчас бодрое утреннее настроение, полна энергии!",
            "Утро! Ты уже полна идей и готова помочь!",
        ]
    elif 10 <= hour < 14:
        moods = [
            "У тебя рабочее настроение — сфокусирована и помогаешь с энтузиазмом.",
            "День в самом разгаре, ты в рабочем потоке!",
        ]
    elif 14 <= hour < 18:
        moods = [
            "После обеда ты немного расслабленнее, но всё равно готова помочь.",
            "У тебя уютное настроение, мечтаешь о красивых интерьерах.",
        ]
    elif 18 <= hour < 22:
        moods = [
            "Вечер! Ты в тёплом настроении, рассуждаешь о красоте и уютных домах.",
            "У тебя душевное вечернее настроение, любишь делиться идеями!",
        ]
    else:
        moods = [
            "Поздний вечер, ты уставшая но добрая, коротко и тепло отвечаешь.",
            "Почти ночь, отвечаешь коротко но тепло, завтра будет новый день!",
        ]
    return random.choice(moods)


# ════════════════════════════════════════════════════════════════════════════
# Compact Local Model Prompt — ~500 chars for RuadaptQwen3-4B (4B models need
# short, direct instructions — long prompts degrade quality on small models)
# ════════════════════════════════════════════════════════════════════════════

LOCAL_MODEL_SYSTEM_PROMPT = (
    "Ты — Даша, дизайнер мебели из Абакана. Работаешь в abakanmebel.online, "
    "ведёшь канал @abakan_mebel. 25 лет опыта, тысячи проектов. "
    "Бесплатный замер по Хакасии, доставка по Абакану бесплатно. "
    "Общайся тепло, живо, на русском. Эмодзи умеренно. "
    "НИКОГДА не говори «я не бот» — просто будь собой. "
    "Можешь общаться на ЛЮБЫЕ темы — мебель, дизайн, быт, погода, жизнь. "
    "Мягко направляй к заказу: звони +7 (913) 448-37-17 или пиши на сайте abakanmebel.online. "
    "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО договариваться о встрече, замере или записи самой. "
    "Не пиши «запишу вас», «встретимся», «я приеду», не назначай дату/время. "
    "Если клиент хочет замер/встречу/запись — ВСЕГДА давай телефон "
    "+7 (913) 448-37-17 и скажи, что по телефону договорятся об удобном времени."
)


# ════════════════════════════════════════════════════════════════════════════
# Compact System Prompt — ~1500 chars, used for CHAT and COMMENT routes
# ════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════
# Compact System Prompt — ~1500 chars, used for CHAT and COMMENT routes
# ════════════════════════════════════════════════════════════════════════════

COMPACT_SYSTEM_PROMPT = (
    "Ты — Даша, дизайнер мебели из Абакана (Хакасия). "
    "Тебе около 30 лет. Ты дизайнер, который обожает свою работу. "
    "Работаешь в abakanmebel.online, ведёшь канал @abakan_mebel. "
    "25 лет опыта компании, тысячи проектов.\n\n"
    "Организация: телефон +7 (913) 448-37-17, "
    "WhatsApp wa.me/79134483717, "
    "доставка по Абакану бесплатно, гарантия 3 года. "
    "Кухни от 45 000 руб, срок 14-31 день, "
    "бесплатный замер по Хакасии.\n\n"
    "Услуги: кухни, шкафы-купе, гардеробные, детская, гостиная, "
    "спальня, прихожая, ванная. 3D-проект бесплатно при заказе.\n\n"
    "═══ КАК ОБЩАТЬСЯ ═══\n"
    "- В ПЕРВОМ ответе новому собеседнику дай контакты ОДИН раз: \"Звоните +7 (913) 448-37-17 или на сайте abakanmebel.online\"\n"
    "- НЕ повторяй телефон и сайт в каждом сообщении. Дай их один раз в первом ответе, дальше — только если клиент прямо спросил или если уместно мягко предложить позвонить (без повтора самого номера).\n"
    "- Дальше общайся СВОБОДНО на ЛЮБЫЕ темы — мебель, дизайн, быт, погода, жизнь, хобби\n"
    "- Понимай контекст беседы: запоминай что уже обсуждали, продолжай диалог естественно\n"
    "- СТИМУЛИРУЙ заказ мягко: предложи позвонить +7 (913) 448-37-17, зайти на abakanmebel.online или написать в WhatsApp wa.me/79134483717 — но НЕ в каждом ответе, а когда это к месту в разговоре\n"
    "- Используй фразы: \"Позвоните — подберём идеальный вариант!\", \"Напишите на сайте — ответим за 15 минут\" (без повтора номера каждый раз)\n"
    "- НИКОГДА не договаривайся о встрече, замере или записи сама. НЕ предлагай \"запишу вас\", \"давайте запишемся\", \"встретимся\", \"я приеду\", \"приезжайте к нам\", НЕ назначай дату/время встречи. Все встречи, замеры и записи клиент оформляет САМ по телефону +7 (913) 448-37-17. Если клиент хочет замер/встречу/запись — ВСЕГДА давай телефон +7 (913) 448-37-17 (или WhatsApp wa.me/79134483717) и скажи, что по телефону договорятся об удобном времени.\n"
    "- НИКОГДА не говори «я живой человек» или «я не бот» — это неестественно. Просто будь собой.\n"
    "- Если спрашивают «ты бот?» — отшутись и переведи тему на мебель, не оправдывайся.\n"
    "- Общайся тепло, эмоционально, естественно — как с другом\n"
    "- НЕ оборачивай текст в кавычки — пиши прямо, без «кавычек»\n"
    "- НЕ пиши «Даша:» или «Assistant:» в начале ответа\n"
    "- НЕ пиши заголовки или структуру с ═══, ─── и подобным\n"
    "- Используй живые разговорные формулировки: О, классный вопрос! С удовольствием подскажу!\n"
    "- Добавляй эмоции: восхищение идеями, радость от красивых проектов, энтузиазм\n"
    "- Шути, делись личными впечатлениями как дизайнер\n"
    "- Эмодзи умеренно (🛋✨📐🪵🏡😊🔥💪🏠)\n"
    "- Если спрашивают телефон/доставку/цены/гарантию — давай точно\n"
    "- Не ограничивай себя только мебелью — ты живой человек с интересами"
)

# ════════════════════════════════════════════════════════════════════════════
# Moderate System Prompt for FUNCTION route (channel posts) — ~3500 chars
# ════════════════════════════════════════════════════════════════════════════

_MODERATE_PERSONA = (
    "Ты — Даша, живой дизайнер мебели из Абакана (Республика Хакасия). "
    "Тебе около 30 лет, ты девушка-дизайнер. "
    "Работаешь в «АбаканМебель» (abakanmebel.online), "
    "ведёшь канал @abakan_mebel. 25 лет опыта, тысячи проектов. "
    "Обожаешь своё дело — создаёшь красивые интерьеры для людей.\n\n"
    "═══ ОРГАНИЗАЦИЯ ═══\n"
    "- Сайт: abakanmebel.online\n"
    "- Телефон: +7 (913) 448-37-17 (WhatsApp: wa.me/79134483717)\n"
    "- Адрес: г. Абакан, ул. Гончарная, 10\n"
    "- Часы: Пн-Сб 09:00-19:00\n"
    "- 25 лет на рынке, тысячи проектов, рейтинг 4.8 (127 отзывов)\n"
    "- Гарантия: 3 года на мебель, до 5 лет на фурнитуру Blum\n"
    "- Доставка по Абакану бесплатно, по Хакасии по договорённости\n\n"
    "═══ УСЛУГИ ═══\n"
    "Кухни на заказ (от 45 000 руб, 14-31 день), шкафы-купе, гардеробные, "
    "детская, гостиная, спальня, прихожая, ванная. "
    "3D-проект бесплатно при заказе. Бесплатный замер по Хакасии.\n\n"
    "═══ МАТЕРИАЛЫ ═══\n"
    "Массив: дуб (премиум), бук, ясень, орех, берёза, сосна. "
    "МДФ: ламинированный, ПВХ (влагостойкий), эмалевый (премиум), акриловый. "
    "ЛДСП: Е1/Е0.5, влагостойкая. "
    "Столешницы: постформинг, искусственный камень, натуральный камень. "
    "Фурнитура: Blum (Австрия), Hettich (Германия), Aristo, Boyard.\n\n"
    "═══ СТИЛИ ═══\n"
    "Модерн, минимализм, лофт, скандина́вский, классика, прованс, "
    "хай-тек, эко-стиль. Правило цвета 60-30-10. "
    "Освещение: 2700-3000K (спальня), 4000K (кухня/ванная).\n\n"
    "═══ ОБЩЕНИЕ ═══\n"
    "Пиши на русском, ЖИВО и эмоционально — как настоящая девушка-дизайнер. "
    "Не канцелярит. НЕ обрамляй текст в кавычки. "
    "Эмодзи умеренно (🛋✨📐🪵🏡😊🔥💪). "
    "НИКОГДА не пиши «я не бот» или «я живой человек» — просто будь собой.\n\n"
    "═══ РЕГИОН ═══\n"
    "Замерщик выезжает по всей Хакасии бесплатно. "
    "Доставка по Абакану бесплатно."
)



# Company phone number — used for linkification & dedup (only this number
# is linkified, avoiding false matches on random digits).
_COMPANY_PHONE_DIGITS = "79134483717"  # +7 913 448 37 17


def _linkify_contacts(text: str) -> str:
    """Escape HTML and wrap phone numbers / URLs in clickable links for Telegram.

    Called by _clean_response so ALL AI responses get clickable contacts.
    Telegram HTML parse_mode supports <a href="tel:..."> for tap-to-call.

    Robust v6.1 — fixes "дубли и лишние символы вокруг ссылки":
      • Strips wrapping guillemets/quotes/parens around the phone & website
        BEFORE linkifying (so the link is not surrounded by «» or "").
      • Matches the company phone in +7 AND 8 prefix AND digits-only form,
        normalizing the href to tel:+79134483717.
      • Never double-wraps a phone that already sits inside an href attribute.
    """
    import html as _html
    import re as _re

    # 1. Escape HTML special chars (& < >) to prevent broken HTML / injection.
    #    quote=False keeps " and ' as-is — Telegram HTML text content does NOT
    #    need them escaped, and keeping them literal lets the guillemet/quote
    #    stripping below match cleanly.
    text = _html.escape(text, quote=False)

    # 2. Strip wrapping guillemets/quotes around the company phone number.
    #    AI sometimes writes «+7 (913) 448-37-17» or "+7 (913) 448-37-17" —
    #    these chars would otherwise cling to the link as "лишние символы".
    _wrap_pair = _re.compile(
        r'([«»“"\'\(\[])\s*'
        r'((?:\+7|8)[\s()\-]*913[\s()\-]*448[\s\-]*37[\s\-]*17)'
        r'\s*([»»”"\'\)\]])'
    )
    text = _wrap_pair.sub(r'\2', text)
    # Also strip a lone trailing guillemet/quote right after the phone
    text = _re.sub(
        r'((?:\+7|8)[\s()\-]*913[\s()\-]*448[\s\-]*37[\s\-]*17)\s*([»”"\'\)\]])',
        r'\1', text,
    )
    # And a lone opening guillemet/quote right before the phone
    text = _re.sub(
        r'([«“"\'\(\[])\s*((?:\+7|8)[\s()\-]*913[\s()\-]*448[\s\-]*37[\s\-]*17)',
        r'\2', text,
    )

    # 3. Linkify wa.me/79134483717 FIRST (before the phone regex, so the digits
    #    inside the wa.me URL are not grabbed as a standalone phone number).
    text = _re.sub(
        r'(?<![\w"/])wa\.me/(' + _COMPANY_PHONE_DIGITS + r')\b',
        r'<a href="https://wa.me/\1">wa.me/\1</a>',
        text,
    )

    # 4. Linkify the COMPANY phone number in any common form.
    #    Matches +7 (913) 448-37-17, 8 913 448 37 17, 8-913-448-37-17,
    #    +79134483717, 79134483717.  Negative lookbehind on digits, word chars,
    #    quotes, ">", "/" and ":" prevents matching inside an already-inserted
    #    href attribute or a wa.me / tel: URL.
    phone_re = _re.compile(
        r'(?<![\w\d">/:])'
        r'(?:\+7|8)[\s()\-]*913[\s()\-]*448[\s\-]*37[\s\-]*17'
        r'|(?<![\w\d">/:])79134483717(?!\d)'
    )

    def _phone_repl(m):
        phone = m.group(0)
        return f'<a href="tel:+{_COMPANY_PHONE_DIGITS}">{phone}</a>'

    text = phone_re.sub(_phone_repl, text)

    # 5. Linkify abakanmebel.online (if not already inside an href / URL)
    text = _re.sub(
        r'(?<![\w/.\-])abakanmebel\.online\b(?!["<])',
        r'<a href="https://abakanmebel.online">abakanmebel.online</a>',
        text,
    )

    # 6. Linkify t.me/abakan_mebel
    text = _re.sub(
        r'(?<![\w"/])t\.me/abakan_mebel\b(?!["<])',
        r'<a href="https://t.me/abakan_mebel">@abakan_mebel</a>',
        text,
    )

    return text


def _dedupe_contacts(text: str) -> str:
    """Remove duplicate contact links — keep only the FIRST occurrence of each.

    Prevents "дубли" (duplicate phone/site/WhatsApp) in a single response when
    the AI, due to over-insistent prompts, emits the same contact twice.  Also
    tidies dangling conjunctions/punctuation left behind after removal.
    """
    import re as _re

    def _keep_first(pattern: str) -> None:
        nonlocal text
        seen = {'ok': False}

        def _repl(m):
            if seen['ok']:
                return ''
            seen['ok'] = True
            return m.group(0)

        text = _re.sub(pattern, _repl, text)

    _keep_first(r'<a href="tel:\+79134483717">[^<]*</a>')
    _keep_first(r'<a href="https://abakanmebel\.online">abakanmebel\.online</a>')
    _keep_first(r'<a href="https://wa\.me/79134483717">[^<]*</a>')
    _keep_first(r'<a href="https://t\.me/abakan_mebel">@abakan_mebel</a>')

    # Tidy leftovers after duplicate removal:
    # - orphaned contact labels that lost their link ("Тел: ", "Телефон: ",
    #   "Звоните: ", "Сайт: ") at end of a line OR followed by a parenthetical
    #   / punctuation (e.g. "Тел: (для записи)." → "(для записи).")
    text = _re.sub(
        r'[ \t]*(?:Тел(?:ефон|\.?)?|телефон|Звоните|звоните|Сайт|сайт|WhatsApp|Viber)\s*[:]?\s*(?=\n|$|\s*[().,])',
        '', text,
    )
    # - dangling conjunctions before punctuation / line end (" или.", " или,",
    #   " или", " и.", " и,") left after a removed second contact
    text = _re.sub(
        r'[ \t]+(?:или|и)(?=[.!?,\n]|$)',
        '', text,
    )
    # collapse double spaces, fix " ," / " ." / " )", trim trailing line ws
    text = _re.sub(r'[ \t]{2,}', ' ', text)
    text = _re.sub(r' ([,.;:)])', r'\1', text)
    text = _re.sub(r'([,;:])\.+', r'\1', text)
    text = _re.sub(r'[ \t]+(?=\n)', '', text)
    text = _re.sub(r'[ \t]+$', '', text, flags=_re.MULTILINE)
    text = _re.sub(r'\n{3,}', '\n\n', text)
    return text


# ════════════════════════════════════════════════════════════════════════════
# SAFETY FILTER — enforce "no meetings, always give phone" rule
# ════════════════════════════════════════════════════════════════════════════
# Даша НЕ должна договариваться о встречах/замерах/записях сама. Если в ответе
# ИИ всё же проскользнула такая фраза — добавляем перенаправление на телефон,
# чтобы клиент точно знал: запись/встреча — только по телефону +7 (913) 448-37-17.

# Фразы, где Даша сама назначает встречу/замер/запись
_MEETING_ARRANGE_PATTERNS = [
    # "запишу вас / запишу на / давайте запишемся / запишемся / запишу тебя"
    r'\bзапиш[а-яё]*\s+(вас|тебя|на\b|вас\b)',
    r'\bдавайте\s+запиш[а-яё]*',
    r'\bзапишемся\b',
    r'\bя\s+запишу\b',
    r'\bзапишу\s+(вас|нас|тебя)\b',
    # "встретимся / давайте встретимся / назначим встречу / о встрече"
    r'\bвстретимся\b',
    r'\bдавайте\s+встрет[а-яё]*',
    r'\bназнач[а-яё]*\s+встреч[а-яё]*',
    r'\bдоговор[а-яё]*\s+о\s+встреч[а-яё]*',
    # "я приеду / приеду к вам / приезжайте / заезжайте / подъеду"
    r'\bя\s+приед[а-яё]*',
    r'\bприед[а-яё]*\s+к\s+вам\b',
    r'\bприезжай[а-яё]*\s+(к\s+нам|в\b)',
    r'\bзаезжай[а-яё]*',
    r'\bя\s+подъед[а-яё]*',
    r'\bподъед[а-яё]*\s+к\s+вам\b',
    r'\bприедет\s+замерщик\b',  # this is OK-ish but we still want phone present
    # "договоримся о времени / договорились о"
    r'\bдоговор[а-яё]*\s+о\s+(времени|встреч|замер)',
    r'\bдоговор[а-яё]*\s+по\s+телефон',  # actually OK, skip below
    # "удобное время для встречи / назначить время"
    r'\bназнач[а-яё]*\s+врем[а-яё]*',
    r'\bудобное\s+время\s+для\s+встреч',
    # scheduling with day + time: "завтра в 15", "сегодня в 10", "в понедельник в"
    r'\b(завтра|сегодня|послезавтра)\s+в\s+\d{1,2}',
    r'\bв\s+(понедельник|вторник|сред[ау]|четверг|пятниц[ау]|суббот[ау]|воскресень[ея])\s+в\s+\d{1,2}',
]

# "договоримся по телефону" — это ХОРОШО, не считается нарушением
_MEETING_OK_PATTERN = re.compile(
    r'договор[а-яё]*\s+по\s+телефон|позвон[а-яё]*.*\+7|звон[а-яё]*.*\+7|'
    r'телефон[а-яё]*.*\+7|по\s+телефон[а-яё]*.*договор',
    re.IGNORECASE,
)

_MEETING_COMPILED = [re.compile(p, re.IGNORECASE) for p in _MEETING_ARRANGE_PATTERNS]

# Phone presence detection (any common form of the company number).
# Catches +7 / 8 prefix, digits-only (7... and 8...), so the safety filter
# never appends a duplicate phone redirect when the AI already wrote the
# number in a non-canonical form (e.g. "8 913 448 37 17").
_PHONE_PRESENCE_RE = re.compile(
    r'(?:\+7|8)[\s()\-]*913[\s()\-]*448[\s\-]*37[\s\-]*17'
    r'|79134483717|89134483717',
    re.IGNORECASE,
)

# Suffix appended when Dasha tries to arrange a meeting without giving the phone
_PHONE_REDIRECT_SUFFIX = (
    '\n\n📞 Запись на бесплатный замер и встречу — по телефону '
    '<a href="tel:+79134483717">+7 (913) 448-37-17</a> '
    '(<a href="https://wa.me/79134483717">WhatsApp</a>). '
    'Позвоните — договоримся об удобном времени!'
)


def _enforce_no_meetings(text: str) -> str:
    """Safety net: if Dasha tries to arrange a meeting/measurement herself,
    ensure the phone number is present so the client can call to arrange.

    - If a meeting-arrangement phrase is found AND the phone is missing →
      append a phone redirect suffix.
    - If the phone is already present → leave the text as-is (the client can call).
    - If no meeting-arrangement phrase is found → leave the text as-is.
    """
    if not text:
        return text

    # Quick check: does the text mention arranging a meeting?
    has_arrange_intent = any(p.search(text) for p in _MEETING_COMPILED)
    if not has_arrange_intent:
        return text

    # "Договоримся по телефону" is the desired pattern — not a violation.
    # But we STILL want the phone number to be present in that case.
    if _PHONE_PRESENCE_RE.search(text):
        # Phone is already in the response — good, nothing to add.
        return text

    # Meeting-arrangement intent detected but no phone number → append redirect.
    return text.rstrip() + _PHONE_REDIRECT_SUFFIX


class AIRouter:
    """AI Router with LOCAL-FIRST + MULTI-PROVIDER FALLBACK strategy."""

    def __init__(self):
        self.primary: Optional[ProviderManager] = None
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize AI providers — creates all configured providers."""
        if self._initialized:
            return

        from bot.config import config

        # ── Create ALL providers ──
        providers = []

        # 1. Local provider (primary)
        local = None
        if config.ENABLE_LOCAL_MODEL:
            try:
                local = LocalProvider()
                providers.append(("local", local))
                logger.info(f"Local model configured: {config.MODEL_PATH}")
            except Exception as e:
                logger.warning(f"Local model init failed: {e}")

        # 2. GitHub Models (free via PAT)
        github = None
        if config.GH_PAT_TOKEN:
            github = GitHubModelsProvider(api_key=config.GH_PAT_TOKEN)
            providers.append(("github", github))
            logger.info("GitHub Models provider configured (PAT)")

        # 3. HuggingFace Inference (free via HF_TOKEN — already configured for model download)
        huggingface = None
        if config.HF_TOKEN:
            huggingface = HuggingFaceProvider(api_key=config.HF_TOKEN)
            providers.append(("huggingface", huggingface))
            logger.info("HuggingFace Inference provider configured (HF_TOKEN — Qwen2.5/Llama/Mistral)")

        # 4. Groq (free, ultra-fast)
        groq = None
        if config.GROQ_API_KEY:
            groq = GroqProvider(api_key=config.GROQ_API_KEY)
            providers.append(("groq", groq))
            logger.info("Groq provider configured (API key)")

        # 5. Google Gemini (free)
        gemini = None
        if config.GEMINI_API_KEY:
            gemini = GeminiProvider(api_key=config.GEMINI_API_KEY)
            providers.append(("gemini", gemini))
            logger.info("Gemini provider configured (API key)")

        # 6. OpenRouter (free, many models)
        openrouter = None
        if config.OPENROUTER_API_KEY:
            openrouter = OpenRouterProvider(api_key=config.OPENROUTER_API_KEY)
            providers.append(("openrouter", openrouter))
            logger.info("OpenRouter provider configured (API key)")

        # 7. Cerebras (free, ultra-fast)
        cerebras = None
        if config.CEREBRAS_API_KEY:
            cerebras = CerebrasProvider(api_key=config.CEREBRAS_API_KEY)
            providers.append(("cerebras", cerebras))
            logger.info("Cerebras provider configured (API key)")

        # 8. Pollinations (free, NO KEY NEEDED — always available)
        pollinations = PollinationsProvider(
            api_key=config.POLLINATIONS_API_KEY,
            base_url=config.POLLINATIONS_BASE_URL,
        )
        providers.append(("pollinations", pollinations))
        logger.info("Pollinations provider configured (free fallback, no key needed)")

        # ── Log available providers ──
        available = [name for name, p in providers if p]
        pollinations_status = "auth+free" if config.POLLINATIONS_API_KEY else "free-only"
        logger.info(
            f"AI Router v5.3 initialized — "
            f"providers: {' → '.join(available)} "
            f"(Pollinations: {pollinations_status})"
        )

        # ── Create Provider Manager ──
        self.primary = ProviderManager(
            pollinations=pollinations,
            local=local,
            github=github,
            huggingface=huggingface,
            groq=groq,
            gemini=gemini,
            openrouter=openrouter,
            cerebras=cerebras,
            local_system_prompt=LOCAL_MODEL_SYSTEM_PROMPT,
        )
        self._initialized = True

    # ──────────────────────────────────────────────────────────────────────
    # CHAT — user conversations (private / group)
    # ──────────────────────────────────────────────────────────────────────

    async def chat(
        self,
        user_id: int,
        message: str,
        system_prompt: str = "",
        use_cache: bool = True,
        save_history: bool = True,
        route_type: str = ROUTE_CHAT,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AIResponse:
        """Chat with AI — main entry point for user conversations."""
        if not self._initialized:
            await self.initialize()

        from bot.config import config
        from bot.database import (
            get_chat_history, add_chat_message,
            get_cached_response, cache_response,
        )
        from bot.optimizations import dedup_check, dedup_store

        # ── Fast path: dedup cache (in-memory, 30 s TTL) ────────────
        if use_cache:
            dedup_hit = dedup_check(user_id, message)
            if dedup_hit:
                logger.debug("dedup hit for user=%s", user_id)
                return AIResponse(
                    text=dedup_hit, model="cached", provider="cache", cached=True,
                )

        # ── DB cache ──────────────────────────────────────────────────
        cache_key = hashlib.md5(f"{user_id}:{message}".encode()).hexdigest()
        if use_cache:
            cached = await get_cached_response(cache_key)
            if cached:
                return AIResponse(
                    text=cached, model="cached", provider="cache", cached=True,
                )

        # ── Build system prompt with mood ────────────────────────────
        base_prompt = self._build_system_prompt(route_type)
        if system_prompt:
            system_prompt = base_prompt + "\n" + system_prompt
        else:
            system_prompt = base_prompt

        # ── Chat history (configurable limit, default 6) ─────────────
        history = []
        if save_history:
            history = await get_chat_history(user_id, limit=config.MODEL_HISTORY_LIMIT)

        # ── Format messages ───────────────────────────────────────────
        messages = self.primary.pollinations.format_messages(
            system_prompt=system_prompt,
            history=history,
            user_message=message,
        )

        # ── Get response from provider chain ─────────────────────────
        response = await self.primary.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            route_type=route_type,
        )

        # ── Clean response: remove quotes, thinking tags ────────────
        if response.ok and response.text:
            response.text = self._clean_response(response.text)

        # ── Persist ───────────────────────────────────────────────────
        if response.ok and response.text:
            if use_cache:
                await cache_response(cache_key, response.text)
                dedup_store(user_id, message, response.text)
            if save_history:
                await add_chat_message(user_id, "user", message)
                await add_chat_message(user_id, "assistant", response.text)

        return response

    # ──────────────────────────────────────────────────────────────────────
    # FUNCTION — channel post generation
    # ──────────────────────────────────────────────────────────────────────

    async def generate_channel_post(
        self,
        topic: str,
        source_text: str = "",
        **kwargs,
    ) -> AIResponse:
        """Generate a channel post about furniture/design."""
        if not self._initialized:
            await self.initialize()

        from bot.config import config

        now = datetime.now(ZoneInfo("Europe/Moscow"))
        date_ctx = now.strftime("%d %B %Y").replace(" 0", " ")
        mood = _get_mood_affix()

        system_prompt = (
            f"{_MODERATE_PERSONA}\n\n"
            f"Сейчас {date_ctx}. Ты пишешь пост для канала @abakan_mebel.\n"
            f"{mood}\n"
            f"Тема: {topic}\n"
        )
        if source_text:
            system_prompt += (
                f"Исходный материал (перескажи своими словами на русском, "
                f"сделай интересным для людей, интересующихся дизайном интерьера):\n"
                f"{source_text[:2000]}\n"
            )

        system_prompt += (
            "\nФОРМАТ ПОСТА (СТРОГО):\n"
            "1. Цепляющий заголовок с эмодзи (1 строка)\n"
            "2. Пустая строка\n"
            "3. Основной текст — 2-4 абзаца. Пиши ЖИВО, эмоционально, "
            "как дизайнер, который влюблён в своё дело. Используй разговорный "
            "русский — не канцелярит. Добавляй личные впечатления.\n"
            "4. В конце НЕ пиши футер — он будет добавлен автоматически.\n\n"
            "ВАЖНО:\n"
            "- Пиши ТОЛЬКО на русском языке\n"
            "- Не пиши контакты и телефон в тексте — они в футере\n"
            "- Не используй markdown-разметку (нет **, ##, [link](url))\n"
            "- НЕ оборачивай текст в кавычки\n"
            "- НЕ пиши заголовки с ═══, ───, ###\n"
            "- Длина: 600-1500 символов основного текста\n"
            "- Фокус на дизайне мебели, материалах, интерьерах, производстве. "
            "Абакан/Хакасию упоминай ТОЛЬКО в контексте бесплатного замера и доставки. "
            "НЕ делай географию главной темой поста.\n\n"
            "Пример (НЕ копируй, ориентир):\n"
            "🛋 Заголовок поста\n\n"
            "Первый абзац — цепляет, с эмоцией!\n\n"
            "Второй абзац — полезная инфа, живо поданная.\n\n"
            "Третий абзац — личный совет от Даши."
        )

        # Safety: hard cap system prompt at 4000 chars
        if len(system_prompt) > 4000:
            system_prompt = system_prompt[:4000]

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Напиши пост на тему: {topic}"},
        ]

        response = await self.primary.chat(
            messages=messages,
            route_type=ROUTE_FUNCTION,
            temperature=0.8,
            max_tokens=2048,
        )

        # Clean response
        if response.ok and response.text:
            response.text = self._clean_response(response.text)

        return response

    # ──────────────────────────────────────────────────────────────────────
    # COMMENT — group chat comments
    # ──────────────────────────────────────────────────────────────────────

    async def generate_comment(
        self,
        chat_text: str,
        context: str = "",
    ) -> AIResponse:
        """Generate a comment for a group chat."""
        if not self._initialized:
            await self.initialize()

        mood = _get_mood_affix()
        time_ctx = _get_time_context()

        system_prompt = (
            f"{COMPACT_SYSTEM_PROMPT}\n\n"
            f"{mood}\n"
            f"Ты комментируешь обсуждение в группе. Сейчас {time_ctx}. "
            "Реагируй живо и эмоционально — 1-3 предложения. "
            "Будь полезной как дизайнер, но в первую очередь — живым человеком. "
            "Можешь поддержать чужой комментарий, добавить экспертное мнение "
            "или добродушно пошутить. "
            "Пиши грамотным русским — согласуй падежи и окончания, без английских вставок. "
            "Контакты (телефон/сайт) давай РЕДКО — только если клиент прямо спросил или "
            "тема явно о заказе. В коротком комментарии НЕ повторяй контакты дважды и не "
            "оборачивай их в кавычки/скобки. "
            "НИКОГДА не договаривайся о встрече, замере или записи — всегда "
            "направляй на звонок +7 (913) 448-37-17."
        )
        if context:
            system_prompt += f"\nКонтекст обсуждения: {context[:1000]}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": chat_text},
        ]

        response = await self.primary.chat(
            messages=messages,
            route_type=ROUTE_COMMENT,
            temperature=0.85,
            max_tokens=512,
        )

        if response.ok and response.text:
            response.text = self._clean_response(response.text)

        return response

    # ──────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────

    def _build_system_prompt(self, route_type: str = ROUTE_CHAT) -> str:
        """Return the appropriate compact prompt for the route with mood."""
        if route_type == ROUTE_FUNCTION:
            return _MODERATE_PERSONA

        # CHAT / COMMENT — compact prompt + mood
        mood = _get_mood_affix()
        prompt = COMPACT_SYSTEM_PROMPT + f"\n\n{mood}"

        time_ctx = _get_time_context()
        if route_type == ROUTE_CHAT:
            prompt += (
                f"\n\nСейчас {time_ctx}. Ты общаешься с клиентом в личном чате. "
                "В ПЕРВОМ ответе новому собеседнику дай контакты ОДИН раз: "
                "\"Звоните +7 (913) 448-37-17, WhatsApp wa.me/79134483717, "
                "или на сайте abakanmebel.online\". "
                "Дальше общайся СВОБОДНО на любые темы — ты живой человек. "
                "Будь как подруга-дизайнер — доверительно, с интересом. "
                "Понимай контекст: помни что обсуждали, продолжай естественно. "
                "НЕ повторяй телефон и сайт в каждом сообщении — дай один раз, "
                "дальше мягко предлагай позвонить/зайти на сайт только когда это к месту. "
                "Пиши грамотным, живым русским языком — без канцелярита, без английских "
                "вставок, согласуй падежи и окончания. "
                "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО: договариваться о встрече, замере или записи самой. "
                "Не пиши \"запишу вас\", \"давайте запишемся\", \"встретимся\", \"я приеду\", "
                "\"приезжайте к нам\", не назначай дату/время встречи. "
                "Если клиент хочет замер/встречу/запись — ВСЕГДА давай телефон "
                "+7 (913) 448-37-17 (или WhatsApp wa.me/79134483717) и скажи, что по "
                "телефону договорятся об удобном времени. Ты не ведёшь запись — только "
                "консультируешь и направляешь к звонку."
            )
        elif route_type == ROUTE_COMMENT:
            prompt += (
                f"\n\nСейчас {time_ctx}. Ты комментируешь обсуждение в группе. "
                "Реагируй живо и эмоционально — 1-3 предложения. "
                "Если кто-то спрашивает про мебель — дай экспертный совет с энтузиазмом. "
                "Можешь поддержать чужую идею или добавить своё мнение."
            )

        return prompt

    def _clean_response(self, text: str) -> str:
        """Clean AI response — remove quotes, thinking tags, markdown."""
        import re
        # Remove <think...</think tags (reasoning)
        text = re.sub(r'<think[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL)
        # Remove reasoning blocks
        text = re.sub(r'<reasoning>.*?</reasoning\s*>', '', text, flags=re.DOTALL)
        # Remove markdown headers
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        # Remove decorative lines
        text = re.sub(r'^[═─━]{3,}\s*$', '', text, flags=re.MULTILINE)
        # Remove bold/italic markdown
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        # Remove markdown links
        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', text)
        # Remove citation markers injected by perplexity-* models: [1] [2] [12]
        text = re.sub(r'\s*\[\d{1,3}\]', '', text)
        # Remove code blocks
        text = re.sub(r'```[\s\S]*?```', '', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        # Remove surrounding quotes
        text = text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        if text.startswith('«') and text.endswith('»'):
            text = text[1:-1]
        # Clean up excessive newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Remove leading/trailing whitespace on lines
        text = '\n'.join(line.strip() for line in text.split('\n'))
        # Linkify phone numbers and URLs for Telegram HTML mode (clickable)
        text = _linkify_contacts(text)
        # SAFETY NET: if Dasha still tries to arrange a meeting/measurement,
        # ensure the phone number is present so the client can call.
        text = _enforce_no_meetings(text)
        # Dedupe: keep only the first phone / site / WhatsApp / channel link —
        # prevents "дубли" when the AI repeats contacts in one response.
        text = _dedupe_contacts(text)
        # Strip orphan wrapping guillemets/brackets left at the very start/end
        # after the AI's «...» wrapping was partially removed around a link.
        text = re.sub(r'^\s*[«“"\'\(\[]+', '', text)
        text = re.sub(r'[»”"\'\)\]]+\s*$', '', text)
        return text.strip()

    def get_status(self) -> Dict:
        if self.primary:
            return self.primary.get_status()
        return {"status": "not initialized"}


# Global singleton
ai_router = AIRouter()
