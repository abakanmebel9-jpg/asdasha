"""AI Router v1.0 — LOCAL-FIRST for Dasha Bot (Furniture Designer).

FAILOVER: Local Model (RuadaptQwen3-4B) → Pollinations → Static fallback

Routes:
  CHAT: user conversations in private/group chat
  COMMENT: comments in groups
  FUNCTION: channel post generation, consultations
"""

import hashlib
import logging
import random
import re
import time
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


class AIRouter:
    """AI Router with LOCAL-FIRST strategy for Dasha Bot."""

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
            local = LocalProvider(
                model_path=config.MODEL_PATH,
                n_ctx=config.MODEL_N_CTX,
                n_threads=config.MODEL_N_THREADS,
                max_tokens=config.MODEL_MAX_TOKENS,
            )
            logger.info(f"Local model configured: {config.MODEL_PATH}")
        else:
            logger.info("Local model DISABLED, using cloud only")

        self.primary = ProviderManager(
            pollinations=pollinations,
            local=local,
        )
        self._initialized = True
        logger.info("AI Router initialized (LOCAL-FIRST)")

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

        from bot.config import config, persona
        from bot.database import get_chat_history, add_chat_message, get_cached_response, cache_response

        # Build system prompt
        if not system_prompt:
            system_prompt = self._build_system_prompt(route_type)

        # Check cache
        if use_cache:
            cache_key = hashlib.md5(f"{user_id}:{message}".encode()).hexdigest()
            cached = await get_cached_response(cache_key)
            if cached:
                return AIResponse(
                    text=cached, model="cached", provider="cache", cached=True,
                )

        # Get chat history
        history = []
        if save_history:
            history = await get_chat_history(user_id, limit=20)

        # Format messages
        messages = self.primary.pollinations.format_messages(
            system_prompt=system_prompt,
            history=history,
            user_message=message,
        )

        # Get response
        response = await self.primary.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            route_type=route_type,
        )

        # Save to cache
        if response.ok and use_cache and response.text:
            await cache_response(cache_key, response.text)

        # Save to chat history
        if save_history and response.ok:
            await add_chat_message(user_id, "user", message)
            await add_chat_message(user_id, "assistant", response.text)

        return response

    async def generate_channel_post(
        self,
        topic: str,
        source_text: str = "",
        **kwargs,
    ) -> AIResponse:
        """Generate a channel post about furniture/design."""
        if not self._initialized:
            await self.initialize()

        from bot.config import persona

        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Europe/Moscow"))
        date_ctx = now.strftime("%d %B %Y").replace(" 0", " ")

        system_prompt = (
            f"{persona['system_prompt']}\n\n"
            f"Сейчас {date_ctx}. Ты пишешь пост для канала @abakan_mebel.\n"
            f"Тема: {topic}\n"
        )
        if source_text:
            system_prompt += f"Исходный материал:\n{source_text[:2000]}\n"

        system_prompt += (
            "\nФОРМАТ ПОСТА:\n"
            "1. Интересный заголовок (эмодзи)\n"
            "2. Основной текст (2-4 абзаца, полезный и увлекательный)\n"
            "3. Футер (ОБЯЗАТЕЛЬНО в каждом посте):\n"
            "   ━━━━━━━━━━━━━━\n"
            "   🛋 Мебель на заказ — дизайн и производство\n"
            "   📞 [телефон с сайта]\n"
            "   🌐 abakanmebel.online\n"
            "   Автор @asdasha_bot\n"
            "━━━━━━━━━━━━━━\n"
            "Пиши на русском. Будь профессиональной но дружелюбной."
        )

        messages = [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": f"Напиши пост на тему: {topic}"}]

        return await self.primary.chat(
            messages=messages, route_type=ROUTE_FUNCTION,
            temperature=0.8, max_tokens=2048,
        )

    async def generate_comment(
        self,
        chat_text: str,
        context: str = "",
    ) -> AIResponse:
        """Generate a comment for a group chat."""
        if not self._initialized:
            await self.initialize()

        from bot.config import persona

        system_prompt = (
            f"{persona['system_prompt']}\n\n"
            f"Ты комментируешь обсуждение в группе. Кратко, 1-3 предложения.\n"
            f"Будь полезной как дизайнер мебели."
        )
        if context:
            system_prompt += f"\nКонтекст: {context[:1000]}"

        messages = [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": chat_text}]

        return await self.primary.chat(
            messages=messages, route_type=ROUTE_COMMENT,
            temperature=0.8, max_tokens=512,
        )

    def _build_system_prompt(self, route_type: str = ROUTE_CHAT) -> str:
        """Build system prompt based on route type."""
        from bot.config import persona

        prompt = persona.get("system_prompt", "Ты Даша — дизайнер мебели из Абакана.")

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