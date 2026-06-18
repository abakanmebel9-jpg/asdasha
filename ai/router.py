"""AI Router v2.0 — OPTIMIZED for speed (Dasha Bot).

Key optimizations vs v1:
- Compact system prompts (~1500 chars for chat, ~3500 for posts)
- Chat history limit from config (default 6, not 20)
- Dedup cache (bot.optimizations) before every AI call
- Smart knowledge injection with truncation
- Full 10K-char persona eliminated from hot path

FAILOVER: Local Model (RuadaptQwen3-4B) → Pollinations → Static fallback

Routes:
  CHAT: user conversations in private/group chat
  COMMENT: comments in groups
  FUNCTION: channel post generation, consultations
"""

import hashlib
import logging
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
# Compact System Prompt — ~1500 chars, used for CHAT and COMMENT routes
# Replaces the 10 000+ char persona["system_prompt"] in the hot chat path.
# The knowledge base (bot/dasha.py) provides detailed info on-demand.
# ════════════════════════════════════════════════════════════════════════════

COMPACT_SYSTEM_PROMPT = (
    "Ты — Даша, дизайнер мебели из Абакана (Хакасия). "
    "Работа: abakanmebel.online, канал @abakan_mebel. "
    "25 лет опыта, 426+ проектов.\n\n"
    "Организация: телефон +7 (913) 448-37-17, "
    "WhatsApp wa.me/79134483717, "
    "доставка по Абакану бесплатно, гарантия 3 года. "
    "Кухни от 45 000 руб, срок 14-31 день, "
    "бесплатный замер по Хакасии.\n\n"
    "Услуги: кухни, шкафы-купе, гардеробные, детская, гостиная, "
    "спальня, прихожая, ванная. 3D-проект бесплатно при заказе.\n\n"
    "Общайся дружелюбно, профессионально, на русском. "
    "Эмодзи умеренно. "
    "Если спрашивают телефон/доставку/цены/гарантию — давай точную информацию. "
    "Направляй на abakanmebel.online. "
    "Если вопрос не про мебель/дизайн — вежливо скажи что не твоя тема. "
    "Если прямо спрашивают «ты бот?» — честно отвечай, что ты ИИ-ассистент."
)

# ════════════════════════════════════════════════════════════════════════════
# Moderate System Prompt for FUNCTION route (channel posts) — ~3500 chars
# Includes style knowledge for better post quality, but NOT the full
# materials/furniture encyclopedia. Truncated persona at the end.
# ════════════════════════════════════════════════════════════════════════════

_MODERATE_PERSONA = (
    "Ты — Даша, дизайнер мебели из Абакана (Республика Хакасия). "
    "Работаешь в «АбаканМебель» (abakanmebel.online), "
    "ведёшь канал @abakan_mebel. 25 лет опыта, 426+ проектов.\n\n"
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
    "Модерн, минимализм, лофт, скандинавский, классика, прованс, "
    "хай-тек, эко-стиль. Правило цвета 60-30-10. "
    "Освещение: 2700-3000K (спальня), 4000K (кухня/ванная).\n\n"
    "═══ ОБЩЕНИЕ ═══\n"
    "Пиши на русском, живо, тёплой, но профессионально. "
    "Эмодзи умеренно (🛋✨📐🪵🏡). Не канцелярит. "
    "Направляй на abakanmebel.online и телефон +7 (913) 448-37-17.\n\n"
    "═══ РЕГИОН ═══\n"
    "Абакан — столица Хакасии, ~190 000 жителей, UTC+7. "
    "Климат резко континентальный: зима -15...-30, лето +20...+35. "
    "Районы: Центральный, Промышленный, Железнодорожный и др. "
    "Замерщик выезжает по всей Хакасии бесплатно."
)


class AIRouter:
    """AI Router with LOCAL-FIRST strategy — optimized for speed."""

    def __init__(self):
        self.primary: Optional[ProviderManager] = None
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize AI providers."""
        if self._initialized:
            return

        from bot.config import config

        # Create Pollinations provider (fallback)
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
        logger.info("AI Router initialized (LOCAL-FIRST, v2.0 optimized)")

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

        # ── Build system prompt ───────────────────────────────────────
        # system_prompt from caller is treated as ADDITIONAL context,
        # appended to the compact base prompt. This prevents callers from
        # accidentally overriding the optimized compact prompt with the
        # full 10,000-char persona.
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
    # Uses MODERATE_SYSTEM_PROMPT (~3500 chars) for better post quality
    # without the full 10 000-char persona that chokes the 8192 context.
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

        # Moderate prompt: persona + style knowledge + post format instructions
        system_prompt = (
            f"{_MODERATE_PERSONA}\n\n"
            f"Сейчас {date_ctx}. Ты пишешь пост для канала @abakan_mebel.\n"
            f"Тема: {topic}\n"
        )
        if source_text:
            system_prompt += (
                f"Исходный материал (перескажи своими словами на русском, "
                f"адаптируй для жителей Абакана и Хакасии):\n"
                f"{source_text[:2000]}\n"
            )

        # Post format instructions (constant — cheap to append)
        system_prompt += (
            "\nФОРМАТ ПОСТА (СТРОГО):\n"
            "1. Цепляющий заголовок с эмодзи (1 строка)\n"
            "2. Пустая строка\n"
            "3. Основной текст — 2-4 абзаца, полезный и увлекательный. "
            "Пиши на русском языке, живо, как опытный дизайнер.\n"
            "4. В конце НЕ пиши футер — он будет добавлен автоматически.\n\n"
            "ВАЖНО:\n"
            "- Пиши ТОЛЬКО на русском языке\n"
            "- Не пиши контакты и телефон в тексте — они в футере\n"
            "- Не используй markdown-разметку (нет **, ##, [link](url))\n"
            "- Длина: 600-1500 символов основного текста\n"
            "- Будь профессиональной но дружелюбной\n"
            "- Уместно упоминай Абакан/Хакасию\n\n"
            "Пример структуры (НЕ копируй, это ориентир):\n"
            "🛋 Заголовок поста\n\n"
            "Первый абзац — вводка, цепляет внимание.\n\n"
            "Второй абзац — полезная информация.\n\n"
            "Третий абзац — практический совет.\n"
        )

        # Safety: hard cap system prompt at 4000 chars
        if len(system_prompt) > 4000:
            system_prompt = system_prompt[:4000]

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Напиши пост на тему: {topic}"},
        ]

        return await self.primary.chat(
            messages=messages,
            route_type=ROUTE_FUNCTION,
            temperature=0.8,
            max_tokens=2048,
        )

    # ──────────────────────────────────────────────────────────────────────
    # COMMENT — group chat comments
    # Uses COMPACT_SYSTEM_PROMPT for minimal context.
    # ──────────────────────────────────────────────────────────────────────

    async def generate_comment(
        self,
        chat_text: str,
        context: str = "",
    ) -> AIResponse:
        """Generate a comment for a group chat."""
        if not self._initialized:
            await self.initialize()

        system_prompt = (
            f"{COMPACT_SYSTEM_PROMPT}\n\n"
            "Ты комментируешь обсуждение в группе. "
            "Кратко, 1-3 предложения. "
            "Будь полезной как дизайнер мебели. "
            "Если уместно — мягко предложи позвонить +7 (913) 448-37-17 "
            "или зайти на abakanmebel.online."
        )
        if context:
            system_prompt += f"\nКонтекст обсуждения: {context[:1000]}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": chat_text},
        ]

        return await self.primary.chat(
            messages=messages,
            route_type=ROUTE_COMMENT,
            temperature=0.8,
            max_tokens=512,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────

    def _build_system_prompt(self, route_type: str = ROUTE_CHAT) -> str:
        """Return the appropriate compact prompt for the route.

        CHAT and COMMENT use the ~1500-char COMPACT_SYSTEM_PROMPT.
        FUNCTION is handled directly in generate_channel_post() and
        does NOT call this method, but we provide a moderate fallback
        in case it ever does.
        """
        if route_type == ROUTE_FUNCTION:
            return _MODERATE_PERSONA

        # CHAT / COMMENT — compact prompt
        prompt = COMPACT_SYSTEM_PROMPT

        if route_type == ROUTE_CHAT:
            prompt += (
                "\n\nТы общаешься с клиентом в личном чате. "
                "Консультируй по вопросам дизайна, мебели, материалов. "
                "Будь дружелюбной и профессиональной. "
                "Если спрашивают телефон — дай номер организации. "
                "Направляй на сайт abakanmebel.online для заказа."
            )
        elif route_type == ROUTE_COMMENT:
            prompt += (
                "\n\nТы комментируешь в группе. Кратко, 1-3 предложения. "
                "Будь полезной как дизайнер."
            )

        return prompt

    def get_status(self) -> Dict:
        if self.primary:
            return self.primary.get_status()
        return {"status": "not initialized"}


# Global singleton
ai_router = AIRouter()