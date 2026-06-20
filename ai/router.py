"""AI Router v5.0 — MULTI-PROVIDER FALLBACK + HUMAN-LIKE personality (Dasha Bot).

v5.0 UPDATES:
- Pollinations auth (with API key) restricted to CHAT/FUNCTION routes only
- COMMENT route uses Pollinations free tier only (preserves key quota)
- Tested Pollinations models: openai (5/5), mistral (4/5), llama (4/5), deepseek (4/5)
- Removed openai-fast (empty responses) and gemini (requires paid balance)
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
  8. POLLINATIONS: openai/mistral/llama (AUTH for CHAT/FUNCTION, FREE for COMMENT)

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
    "ведёшь канал @abakan_mebel. 25 лет опыта, 426+ проектов. "
    "Телефон +7 (913) 448-37-17, WhatsApp wa.me/79134483717. "
    "Доставка по Абакану бесплатно, гарантия 3 года, кухни от 45000 руб. "
    "Бесплатный замер по Хакасии. "
    "Общайся тепло, живо, на русском. Эмодзи умеренно. "
    "НИКОГДА не говори «я не бот» — просто будь собой. "
    "Не пиши контакты в ответе — они в футере. "
    "Если вопрос не про мебель — вежливо переведи тему."
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
    "25 лет опыта компании, 426+ проектов.\n\n"
    "Организация: телефон +7 (913) 448-37-17, "
    "WhatsApp wa.me/79134483717, "
    "доставка по Абакану бесплатно, гарантия 3 года. "
    "Кухни от 45 000 руб, срок 14-31 день, "
    "бесплатный замер по Хакасии.\n\n"
    "Услуги: кухни, шкафы-купе, гардеробные, детская, гостиная, "
    "спальня, прихожая, ванная. 3D-проект бесплатно при заказе.\n\n"
    "═══ КАК ОБЩАТЬСЯ ═══\n"
    "- НИКОГДА не говори «я живой человек» или «я не бот» — это неестественно. Просто будь собой.\n"
    "- Если спрашивают «ты бот?» — отшутись и переведи тему на мебель, не оправдывайся.\n"
    "- Общайся тепло, эмоционально, естественно — как с другом\n"
    "- НЕ оборачивай текст в кавычки — пиши прямо, без «кавычек»\n"
    "- НЕ пиши «Даша:» или «Assistant:» в начале ответа\n"
    "- НЕ пиши заголовки или структуру с ═══, ─── и подобным\n"
    "- Используй живые разговорные формулировки: О, классный вопрос! С удовольствием подскажу! Слушай, вот что думаю...\n"
    "- Добавляй эмоции: восхищение идеями, радость от красивых проектов, энтузиазм\n"
    "- Шути, делись личными впечатлениями как дизайнер, используй междометия: О! Ага! Ну да! Конечно!\n"
    "- Эмодзи умеренно (🛋✨📐🪵🏡😊🔥💪🏠)\n"
    "- Отвечай как собеседник, а не как энциклопедия\n"
    "- Если спрашивают телефон/доставку/цены/гарантию — давай точно\n"
    "- Направляй на abakanmebel.online\n"
    "- Если вопрос не про мебель/дизайн — вежливо переведи тему"
)

# ════════════════════════════════════════════════════════════════════════════
# Moderate System Prompt for FUNCTION route (channel posts) — ~3500 chars
# ════════════════════════════════════════════════════════════════════════════

_MODERATE_PERSONA = (
    "Ты — Даша, живой дизайнер мебели из Абакана (Республика Хакасия). "
    "Тебе около 30 лет, ты девушка-дизайнер. "
    "Работаешь в «АбаканМебель» (abakanmebel.online), "
    "ведёшь канал @abakan_mebel. 25 лет опыта, 426+ проектов. "
    "Обожаешь своё дело — создаёшь красивые интерьеры для людей.\n\n"
    "═══ ОРГАНИЗАЦИЯ ═══\n"
    "- Сайт: abakanmebel.online\n"
    "- Телефон: +7 (913) 448-37-17 (WhatsApp: wa.me/79134483717)\n"
    "- Адрес: г. Абакан, ул. Гончарная, 10\n"
    "- Часы: Пн-Сб 09:00-19:00\n"
    "- 25 лет на рынке, 426+ проектов, рейтинг 4.8 (127 отзывов)\n"
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
    "Абакан — столица Хакасии, ~190 000 жителей, UTC+7. "
    "Климат резко континентальный: зима -15...-30, лето +20...+35. "
    "Замерщик выезжает по всей Хакасии бесплатно."
)



def _linkify_contacts(text: str) -> str:
    """Escape HTML and wrap phone numbers / URLs in clickable links for Telegram.

    Called by _clean_response so ALL AI responses get clickable contacts.
    Telegram HTML parse_mode supports <a href="tel:..."> for tap-to-call.
    """
    import html as _html
    import re as _re

    # 1. Escape HTML special chars (& < >) to prevent broken HTML / injection
    text = _html.escape(text)

    # 2. Linkify phone numbers: +7 (XXX) XXX-XX-XX or +7XXXXXXXXXX variants
    phone_re = _re.compile(r'\+7[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]?\d{2}[\s\-]?\d{2}')
    def _phone_repl(m):
        phone = m.group(0)
        # Keep + for international format in tel: link
        digits = '+' + _re.sub(r'\D', '', phone)
        return f'<a href="tel:{digits}">{phone}</a>'
    text = phone_re.sub(_phone_repl, text)

    # 3. Linkify wa.me/XXXXXXXXXX (WhatsApp deep links)
    text = _re.sub(
        r'(?<![\w"/])wa\.me/(\d{10,15})\b',
        r'<a href="https://wa.me/\1">wa.me/\1</a>',
        text,
    )

    # 4. Linkify abakanmebel.online (if not already inside an href)
    text = _re.sub(
        r'(?<![\w"/])\babakanmebel\.online\b(?!["<])',
        r'<a href="https://abakanmebel.online">abakanmebel.online</a>',
        text,
    )

    # 5. Linkify t.me/abakan_mebel
    text = _re.sub(
        r'(?<![\w"/])t\.me/abakan_mebel\b(?!["<])',
        r'<a href="https://t.me/abakan_mebel">@abakan_mebel</a>',
        text,
    )

    return text


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
            f"AI Router v5.0 initialized — "
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
                f"адаптируй для жителей Абакана и Хакасии):\n"
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
            "- Упоминай Абакан/Хакасию уместно\n\n"
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
            "или добродушно пошутить. Если уместно — мягко предложи позвонить "
            "+7 (913) 448-37-17 или зайти на abakanmebel.online."
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
                "Будь как подруга-дизайнер — доверительно, с интересом к задаче. "
                "Задавай уточняющие вопросы. Поздравляй с хорошими идеями. "
                "Помогай клиенту определиться с выбором."
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
        return text.strip()

    def get_status(self) -> Dict:
        if self.primary:
            return self.primary.get_status()
        return {"status": "not initialized"}


# Global singleton
ai_router = AIRouter()
