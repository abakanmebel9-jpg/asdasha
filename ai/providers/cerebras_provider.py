"""Cerebras AI Provider — Ultra-fast free inference on wafer-scale chips.

Cerebras free tier:
  Endpoint: https://api.cerebras.ai/v1/chat/completions
  Auth: Free API key from cloud.cerebras.ai
  Compatible: OpenAI Chat Completions format

Available FREE models (2025):
  - llama-3.3-70b — Excellent Russian, 70B, ULTRA fast (~0.3s!)
  - llama3-8b — Good Russian, 8B, INSTANT (~0.2s!)

Rate Limits (free tier):
  - ~10 RPM
  - ~2,000 tokens per minute
  - No credit card required

KEY ADVANTAGE: Cerebras uses wafer-scale engine (WSE) chips —
the FASTEST inference in the world. Responses in 0.2-0.5 seconds!

How to get API key:
  1. Go to cloud.cerebras.ai
  2. Sign up (free)
  3. Go to API Keys → Create API Key
  4. Copy key to CEREBRAS_API_KEY in .env

Reference: https://cloud.cerebras.ai/docs
"""

import logging
import time
from typing import Optional, List, Dict, Any

import httpx

from ai.providers.base import BaseAIProvider, AIResponse

logger = logging.getLogger("dasha.ai.cerebras")

# ── Cerebras API ──
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
CHAT_URL = f"{CEREBRAS_BASE_URL}/chat/completions"

# Models
RUSSIAN_MODELS = [
    "llama-3.3-70b",  # Best quality Russian
    "llama3-8b",       # Fastest
]

DEFAULT_MODEL = "llama-3.3-70b"


class CerebrasProvider(BaseAIProvider):
    """Cerebras AI provider — ultra-fast inference on WSE chips."""

    name = "cerebras"

    def __init__(self, api_key: str = "", **kwargs):
        super().__init__(
            name="cerebras",
            api_key=api_key,
            base_url=CEREBRAS_BASE_URL,
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
                text="", model=model, provider="cerebras",
                error="No Cerebras API key configured",
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    CHAT_URL,
                    headers=headers,
                    json=payload,
                )

                if response.status_code == 401:
                    return AIResponse(
                        text="", model=model, provider="cerebras",
                        error="Unauthorized — check API key",
                    )
                if response.status_code == 403:
                    return AIResponse(
                        text="", model=model, provider="cerebras",
                        error="Forbidden — rate limit or model not available",
                    )
                if response.status_code == 429:
                    return AIResponse(
                        text="", model=model, provider="cerebras",
                        error="Rate limited (429)",
                    )
                if response.status_code != 200:
                    body = response.text[:300]
                    return AIResponse(
                        text="", model=model, provider="cerebras",
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
                        provider="cerebras",
                        tokens_used=data.get("usage", {}).get("total_tokens", 0),
                        latency_ms=elapsed,
                    )

                self._fail_count += 1
                return AIResponse(
                    text="", model=model, provider="cerebras",
                    error="Empty response",
                )

        except httpx.TimeoutException:
            self._fail_count += 1
            return AIResponse(
                text="", model=model, provider="cerebras",
                error="Timeout",
            )
        except Exception as e:
            self._fail_count += 1
            logger.error(f"Cerebras error: {e}")
            return AIResponse(
                text="", model=model, provider="cerebras",
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
            "russian_models": RUSSIAN_MODELS,
        }
