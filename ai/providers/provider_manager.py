"""Provider Manager — LOCAL-FIRST multi-provider for Dasha Bot.

Dasha uses LOCAL model as primary (same as Asya), Pollinations as fallback.

ROUTE STRATEGY:
  CHAT    → Local → Pollinations → Static
  COMMENT → Local → Pollinations → Static
  FUNCTION → Pollinations → Local → Static
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Optional, List, Dict

from .base import AIResponse, BaseAIProvider
from .local_provider import LocalProvider
from .pollinations_provider import PollinationsProvider

logger = logging.getLogger("dasha.ai.provider_manager")

ROUTE_CHAT = "chat"
ROUTE_COMMENT = "comment"
ROUTE_FUNCTION = "function"
ROUTE_VISION = "vision"
ROUTE_IMAGE = "image"


class ProviderManager:
    """Manages AI providers with LOCAL-FIRST failover."""

    def __init__(
        self,
        pollinations: PollinationsProvider,
        local: Optional[LocalProvider] = None,
    ) -> None:
        self.pollinations = pollinations
        self.local = local
        self._local_count = 0
        self._pollinations_count = 0
        self._total_requests = 0
        self._last_provider: str = ""

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        route_type: str = ROUTE_CHAT,
        **kwargs: Any,
    ) -> AIResponse:
        self._total_requests += 1

        # CHAT / COMMENT: Local FIRST
        if route_type in (ROUTE_CHAT, ROUTE_COMMENT):
            if self.local:
                try:
                    local_avail = await self.local.is_available()
                except Exception:
                    local_avail = False
                if local_avail:
                    try:
                        local_max = min(max_tokens, 2048) if route_type == ROUTE_CHAT else min(max_tokens, 512)
                        result = await self.local.chat(
                            messages=messages, model="local-qwen3-4b",
                            temperature=temperature, max_tokens=local_max, **kwargs,
                        )
                        if result.ok:
                            self._last_provider = "local"
                            self._local_count += 1
                            return result
                    except Exception as exc:
                        logger.debug(f"Local model error: {exc}")

        # FUNCTION route: Cloud FIRST, Local as fallback
        if route_type == ROUTE_FUNCTION:
            # Try Pollinations first for function routes
            try:
                result = await self.pollinations.chat(
                    messages=messages, model=model,
                    temperature=temperature, max_tokens=max_tokens, **kwargs,
                )
                if result.ok:
                    self._last_provider = "pollinations"
                    self._pollinations_count += 1
                    return result
            except Exception as exc:
                logger.debug(f"Pollinations error (function): {exc}")

            # Fallback to local
            if self.local:
                try:
                    result = await self.local.chat(
                        messages=messages, model="local-qwen3-4b",
                        temperature=temperature, max_tokens=min(max_tokens, 2048), **kwargs,
                    )
                    if result.ok:
                        self._last_provider = "local"
                        self._local_count += 1
                        return result
                except Exception:
                    pass

            return AIResponse(text="", model=model, provider="none",
                              error="All providers failed (function route)")
        else:
            # Cloud fallback for chat/comment
            try:
                result = await self.pollinations.chat(
                    messages=messages, model=model,
                    temperature=temperature, max_tokens=max_tokens, **kwargs,
                )
                if result.ok:
                    self._last_provider = "pollinations"
                    self._pollinations_count += 1
                    return result
            except Exception as exc:
                logger.debug(f"Pollinations fallback error: {exc}")

        return AIResponse(text="", model=model, provider="none",
                          error="All AI providers failed (Local + Pollinations)")

    async def generate_image(
        self, prompt: str, width: int = 1024, height: int = 1024,
        model: str = "", **kwargs,
    ) -> AIResponse:
        self._total_requests += 1
        result = await self.pollinations.generate_image(
            prompt=prompt, width=width, height=height, model=model, **kwargs,
        )
        if result.ok:
            self._last_provider = "pollinations"
            return result
        return AIResponse(text="", model=model, provider="none",
                          error="Image generation failed")

    def is_available(self) -> bool:
        return True

    async def close(self) -> None:
        await self.pollinations.close()

    def get_status(self) -> Dict[str, Any]:
        status = {
            "total_requests": self._total_requests,
            "local_count": self._local_count,
            "pollinations_count": self._pollinations_count,
            "last_provider": self._last_provider,
            "pollinations": self.pollinations.get_status(),
        }
        if self.local:
            status["local"] = self.local.get_status()
        else:
            status["local"] = {"status": "not configured"}
        return status