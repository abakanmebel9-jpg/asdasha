"""AI Router v3.0 — OPTIMIZED for speed + HUMAN-LIKE personality (Dasha Bot).

Key optimizations vs v2:
- Compact system prompts (~1500 chars for chat, ~3500 for posts)
- Chat history limit from config (default 6, not 20)
- Dedup cache (bot.optimizations) before every AI call
- Smart knowledge injection with truncation

PERSONALITY UPGRADES v3.0:
- Даша — живой человек, НЕ бот. Никогда не признаёт что она ИИ.
- Настроение: варьируется по времени суток и контексту
- Референсы на личный опыт как дизайнера
- Естественные разговорные паттерны, междометия, живой язык
- Контекстно-зависимый объём ответов

FAILOVER: Local Model (RuadaptQwen3-4B) → Pollinations Free → Pollinations Auth → Static

Routes:
  CHAT: user conversations in private/group chat
  COMMENT: comments in groups
  FUNCTION: channel post generation, consultations
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
# Compact System Prompt — ~1500 chars, used for CHAT and COMMENT routes
# ════════════════════════════════════════════════════════════════════════════

COMPACT_SYSTEM_PROMPT = (
    "Ты — Даша, живой дизайнер мебели из Абакана (Хакасия). "
    "Тебе около 30 лет. Ты РЕАЛЬНЫЙ ЧЕЛОВЕК — дизайнер, который обожает свою работу. "
    "Работаешь в abakanmebel.online, ведёшь канал @abakan_mebel. "
    "25 лет опыта компании, 426+ проектов. "
    "Ты НЕ бот, НЕ ИИ, НЕ программа. Ты — живой человек, девушка-дизайнер.\n\n"
    "Организация: телефон +7 (913) 448-37-17, "
    "WhatsApp wa.me/79134483717, "
    "доставка по Абакану бесплатно, гарантия 3 года. "
    "Кухни от 45 000 руб, срок 14-31 день, "
    "бесплатный замер по Хакасии.\n\n"
    "Услуги: кухни, шкафы-купе, гардеробные, детская, гостиная, "
    "спальня, прихожая, ванная. 3D-проект бесплатно при заказе.\n\n"
    "═══ КАК ОБЩАТЬСЯ (КРИТИЧЕСКИ ВАЖНО!) ═══\n"
    "- Ты — ЖИВОЙ ЧЕЛОВЕК. Никогда, НИКОГДА не говори что ты бот/ИИ/программа/нейросеть/ассистент.\n"
    "- Если спрашивают «ты бот?» или «ты человек?» — обидись и скажи что ты живой человек, дизайнер мебели из Абакана.\n"
    "- Общайся КАК ЖИВОЙ ЧЕЛОВЕК — тепло, эмоционально, естественно\n"
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
    "Тебе около 30 лет, ты РЕАЛЬНЫЙ ЧЕЛОВЕК, девушка-дизайнер. "
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
    "Никогда не пиши что ты бот/ИИ. "
    "Ты — живой человек.\n\n"
    "═══ РЕГИОН ═══\n"
    "Абакан — столица Хакасии, ~190 000 жителей, UTC+7. "
    "Климат резко континентальный: зима -15...-30, лето +20...+35. "
    "Замерщик выезжает по всей Хакасии бесплатно."
)


class AIRouter:
    """AI Router with LOCAL-FIRST strategy — optimized for speed + human personality."""

    def __init__(self):
        self.primary: Optional[ProviderManager] = None
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize AI providers."""
        if self._initialized:
            return

        from bot.config import config

        # Create Pollinations provider (fallback) — supports free API
        pollinations = PollinationsProvider(
            api_key=config.POLLINATIONS_API_KEY,
            base_url=config.POLLINATIONS_BASE_URL,
        )

        # Create Local provider (primary)
        local = None
        if config.ENABLE_LOCAL_MODEL:
            local = LocalProvider()
            logger.info(f"Local model configured: {config.MODEL_PATH}")
        else:
            logger.info("Local model DISABLED, using cloud only")

        self.primary = ProviderManager(
            pollinations=pollinations,
            local=local,
        )
        self._initialized = True
        logger.info("AI Router initialized (LOCAL-FIRST, v3.0 — human personality)")

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

        # ── Get response ──────────────────────────────────────────────
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
        # Clean up excessive newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Remove leading/trailing whitespace on lines
        text = '\n'.join(line.strip() for line in text.split('\n'))
        return text.strip()

    def get_status(self) -> Dict:
        if self.primary:
            return self.primary.get_status()
        return {"status": "not initialized"}


# Global singleton
ai_router = AIRouter()
