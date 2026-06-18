"""Pollinations AI Provider — Cloud fallback for Dasha Bot.

Uses gen.pollinations.ai API (OpenAI-compatible).
Primary for Dasha: local model. Pollinations is fallback only.
"""

import hashlib
import json
import logging
import random
import time
from typing import Optional, List, Dict, Any

import httpx

from ai.providers.base import BaseAIProvider, AIResponse

logger = logging.getLogger("dasha.ai.pollinations")

DEFAULT_MODEL = "openai"
BASE_URL = "https://gen.pollinations.ai"

MODELS = [
    "openai", "mistral", "mistral-large", "mistral-small",
    "llama", "deepseek", "deepseek-r1", "qwen-coder",
    "searchgpt", "openai-large", "claude-hybridspace",
]

CHAT_MODELS = ["openai", "mistral", "mistral-large", "llama", "deepseek"]
CONTENT_MODELS = ["openai", "mistral", "openai-large", "claude-hybridspace"]
IMAGE_MODELS = ["flux", "flux-pro", "flux-realism", "turbo"]


class PollinationsProvider(BaseAIProvider):
    """Pollinations AI provider — OpenAI-compatible API."""

    name = "pollinations"

    def __init__(self, api_key: str = "", base_url: str = BASE_URL, **kwargs):
        super().__init__(name="pollinations", api_key=api_key, base_url=base_url, **kwargs)
        self._fail_count = 0
        self._total_requests = 0

    async def is_available(self) -> bool:
        return True  # Always available (free API)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> AIResponse:
        self._total_requests += 1
        model = model or DEFAULT_MODEL

        # Rotate models on failures
        if self._fail_count > 3:
            model = random.choice(CHAT_MODELS)

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if "seed" in kwargs:
            payload["seed"] = kwargs["seed"]

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )

                if response.status_code == 429:
                    self._fail_count += 1
                    logger.warning("Pollinations rate limited (429)")
                    return AIResponse(
                        text="", model=model, provider="pollinations",
                        error="Rate limited (429)",
                    )

                if response.status_code != 200:
                    self._fail_count += 1
                    logger.warning(f"Pollinations HTTP {response.status_code}")
                    return AIResponse(
                        text="", model=model, provider="pollinations",
                        error=f"HTTP {response.status_code}",
                    )

                data = response.json()
                self._fail_count = 0  # Reset on success
                elapsed = (time.time() - start) * 1000

                text = ""
                if "choices" in data and data["choices"]:
                    text = data["choices"][0].get("message", {}).get("content", "")

                if text:
                    return AIResponse(
                        text=text.strip(),
                        model=model,
                        provider="pollinations",
                        tokens_used=data.get("usage", {}).get("total_tokens", 0),
                        latency_ms=elapsed,
                    )

                return AIResponse(
                    text="", model=model, provider="pollinations",
                    error="Empty response from Pollinations",
                )
        except httpx.TimeoutException:
            self._fail_count += 1
            return AIResponse(
                text="", model=model, provider="pollinations",
                error="Timeout",
            )
        except Exception as e:
            self._fail_count += 1
            logger.error(f"Pollinations error: {e}")
            return AIResponse(
                text="", model=model, provider="pollinations",
                error=str(e),
            )

    async def generate_image(
        self, prompt: str, width: int = 1024, height: int = 1024,
        model: str = "", **kwargs,
    ) -> AIResponse:
        model = model or random.choice(IMAGE_MODELS)
        try:
            url = f"{self.base_url}/prompt/{prompt}"
            params = {
                "width": width,
                "height": height,
                "model": model,
                "nologo": "true",
                "seed": random.randint(1, 999999),
            }
            if kwargs.get("enhance"):
                params["enhance"] = "true"

            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get(url, params=params, follow_redirects=True)
                if response.status_code == 200:
                    # Pollinations returns the image directly
                    return AIResponse(
                        text="",
                        model=model,
                        provider="pollinations",
                        image_url=str(response.url),
                    )
        except Exception as e:
            logger.error(f"Pollinations image error: {e}")
        return AIResponse(
            text="", model=model, provider="pollinations",
            error="Image generation failed",
        )

    def get_status(self) -> Dict:
        return {
            "status": "available",
            "total_requests": self._total_requests,
            "fail_count": self._fail_count,
            "base_url": self.base_url,
        }