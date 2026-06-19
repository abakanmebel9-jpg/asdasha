"""OpenRouter AI Provider — Free models via unified API.

OpenRouter free tier:
  Endpoint: https://openrouter.ai/api/v1/chat/completions
  Auth: Free API key from openrouter.ai
  Compatible: OpenAI Chat Completions format

Available FREE models (2026, 20+ models):
  KEY ADVANTAGE: One API key gives access to many free models from
  different providers (Meta, Mistral, Google, etc.)

Recommended FREE models for Russian:
  - meta-llama/llama-3.3-70b-instruct:free — Excellent Russian
  - mistralai/mistral-small-24b-instruct-2501:free — Good Russian
  - google/gemma-2-9b-it:free — Good Russian
  - qwen/qwen-2.5-7b-instruct:free — DECENT Russian (Qwen is good at Russian!)
  - google/gemma-2-2b-it:free — OK Russian, very fast

Rate Limits (free tier):
  - ~20 RPM per model
  - ~200,000 tokens per day (varies by model)
  - No credit card required
  - Some models have "with context" pricing (free with limits)

How to get API key:
  1. Go to openrouter.ai
  2. Sign up (free, Google/GitHub auth)
  3. Go to Keys → Create Key
  4. Copy key to OPENROUTER_API_KEY in .env

Reference: https://openrouter.ai/docs/models
"""

import logging
import time
from typing import Optional, List, Dict, Any

import httpx

from ai.providers.base import BaseAIProvider, AIResponse

logger = logging.getLogger("dasha.ai.openrouter")

# ── OpenRouter API ──
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
CHAT_URL = f"{OPENROUTER_BASE_URL}/chat/completions"

# Free models optimized for Russian (ranked by quality)
FREE_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "mistralai/mistral-small-24b-instruct-2501:free",
    "google/gemma-2-9b-it:free",
    "qwen/qwen-2.5-7b-instruct:free",
    "google/gemma-2-2b-it:free",
]

DEFAULT_MODEL = "meta-llama/llama-3.3-70b-instruct:free"


class OpenRouterProvider(BaseAIProvider):
    """OpenRouter AI provider — 20+ free models via unified API."""

    name = "openrouter"

    def __init__(self, api_key: str = "", **kwargs):
        super().__init__(
            name="openrouter",
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            **kwargs,
        )
        self._success_count = 0
        self._fail_count = 0
        self._total_requests = 0

    async def is_available(self) -> bool:
        return bool(self.api_key)

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

        if not self.api_key:
            return AIResponse(
                text="", model=model, provider="openrouter",
                error="No OpenRouter API key configured",
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://t.me/abakan_mebel",  # Helps OpenRouter stats
            "X-Title": "Dasha Bot (AbakanMebel)",
        }

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    CHAT_URL,
                    headers=headers,
                    json=payload,
                )

                if response.status_code == 401:
                    return AIResponse(
                        text="", model=model, provider="openrouter",
                        error="Unauthorized — check API key",
                    )
                if response.status_code == 402:
                    return AIResponse(
                        text="", model=model, provider="openrouter",
                        error="Insufficient credits — free quota exhausted",
                    )
                if response.status_code == 429:
                    return AIResponse(
                        text="", model=model, provider="openrouter",
                        error="Rate limited (429)",
                    )
                if response.status_code != 200:
                    body = response.text[:300]
                    return AIResponse(
                        text="", model=model, provider="openrouter",
                        error=f"HTTP {response.status_code}: {body}",
                    )

                data = response.json()
                elapsed = (time.time() - start) * 1000

                text = ""
                if "choices" in data and data["choices"]:
                    text = data["choices"][0].get("message", {}).get("content", "")

                if text:
                    self._success_count += 1
                    return AIResponse(
                        text=text.strip(),
                        model=model,
                        provider="openrouter",
                        tokens_used=data.get("usage", {}).get("total_tokens", 0),
                        latency_ms=elapsed,
                    )

                self._fail_count += 1
                return AIResponse(
                    text="", model=model, provider="openrouter",
                    error="Empty response",
                )

        except httpx.TimeoutException:
            self._fail_count += 1
            return AIResponse(
                text="", model=model, provider="openrouter",
                error="Timeout",
            )
        except Exception as e:
            self._fail_count += 1
            logger.error(f"OpenRouter error: {e}")
            return AIResponse(
                text="", model=model, provider="openrouter",
                error=str(e),
            )

    def get_status(self) -> Dict:
        return {
            "status": "available" if self.api_key else "no_key",
            "total_requests": self._total_requests,
            "success_count": self._success_count,
            "fail_count": self._fail_count,
            "has_key": bool(self.api_key),
            "default_model": DEFAULT_MODEL,
            "free_models": FREE_MODELS,
        }
