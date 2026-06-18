"""Provider Manager — LOCAL-ONLY for Dasha Bot.

Даша работает ТОЛЬКО на локальной модели RuadaptQwen3-4B-Instruct Q4_K_M.
Pollinations отключён / используется только как аварийный fallback, когда
локальная модель физически недоступна (например, файл не скачался).

ROUTE STRATEGY (по требованию владельца — только локальная модель):
  CHAT     → Local (Pollinations — крайний fallback)
  COMMENT  → Local (Pollinations — крайний fallback)
  FUNCTION → Local (Pollinations — крайний fallback)
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

        # ── ЛОКАЛЬНАЯ МОДЕЛЬ — ПЕРВИЧНАЯ ДЛЯ ВСЕХ РЕЖИМОВ ──
        # По требованию владельца: Даша работает ТОЛЬКО на локальной
        # RuadaptQwen3-4B-Instruct Q4_K_M. Pollinations — только аварийный
        # fallback, если локальная модель физически недоступна.
        if self.local:
            try:
                local_avail = await self.local.is_available()
            except Exception:
                local_avail = False

            if local_avail:
                # Лимиты токенов по типу маршрута
                if route_type == ROUTE_CHAT:
                    local_max = min(max_tokens, 2048)
                elif route_type == ROUTE_COMMENT:
                    local_max = min(max_tokens, 512)
                else:  # FUNCTION (посты канала) — больше токенов для длинных постов
                    local_max = min(max_tokens, 2048)

                try:
                    result = await self.local.chat(
                        messages=messages, model="local-qwen3-4b",
                        temperature=temperature, max_tokens=local_max, **kwargs,
                    )
                    if result.ok:
                        self._last_provider = "local"
                        self._local_count += 1
                        return result
                    else:
                        logger.warning(
                            f"Local model returned error (route={route_type}): "
                            f"{result.error}"
                        )
                except Exception as exc:
                    logger.error(f"Local model exception: {exc}")

        # ── АВАРИЙНЫЙ FALLBACK: Pollinations ──
        # Срабатывает ТОЛЬКО если локальная модель недоступна
        # (файл не скачался, ошибка загрузки и т.п.)
        logger.warning(
            f"Local model unavailable — using Pollinations fallback "
            f"(route={route_type}). THIS SHOULD NOT HAPPEN IN NORMAL OPERATION."
        )
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
            logger.error(f"Pollinations fallback error: {exc}")

        return AIResponse(
            text="", model=model, provider="none",
            error="All AI providers failed (Local + Pollinations fallback)",
        )

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