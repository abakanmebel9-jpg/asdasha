"""Groq AI Provider — Ultra-fast free inference.

Groq free tier:
  Endpoint: https://api.groq.com/openai/v1/chat/completions
  Auth: Free API key from console.groq.com
  Compatible: OpenAI Chat Completions format

Available FREE models (2026):
  - llama-3.3-70b-versatile — Excellent Russian, 70B, VERY fast (~1-2s!)
  - llama-3.1-8b-instant — Good Russian, 8B, ULTRA fast (~0.5s)
  - llama3-70b-8192 — Good Russian, 70B
  - llama3-8b-8192 — OK Russian, 8B
  - mixtral-8x7b-32768 — Good Russian, 47B MoE
  - gemma2-9b-it — Good Russian, 9B
  - qwen-qwq-32b — Good Russian, reasoning (NEW 2026)

Rate Limits (free tier, 2026):
  - ~30 RPM (requests per minute)
  - ~6,000 tokens per minute
  - ~500,000 tokens per day
  - No credit card required

KEY ADVANTAGE: Groq is the FASTEST free LLM API — responses in 0.5-2 seconds!
Perfect for real-time chat bots.

Reference: https://console.groq.com/docs/models
"""

import logging
import time
from typing import Optional, List, Dict, Any

import httpx

from ai.providers.base import BaseAIProvider, AIResponse

logger = logging.getLogger("dasha.ai.groq")

# ── Groq API ──
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
CHAT_URL = f"{GROQ_BASE_URL}/chat/completions"

# Models optimized for Russian (ranked by quality/speed)
RUSSIAN_MODELS = [
    "llama-3.3-70b-versatile",   # Best quality Russian
    "llama3-70b-8192",            # Great Russian, slightly older
    "qwen-qwq-32b",              # Good Russian, reasoning (NEW 2026)
    "mixtral-8x7b-32768",         # Good Russian, MoE model
    "llama-3.1-8b-instant",       # Fastest, decent Russian
    "gemma2-9b-it",               # Good Russian, Google model
]

DEFAULT_MODEL = "llama-3.3-70b-versatile"


class GroqProvider(BaseAIProvider):
    """Groq AI provider — ultra-fast free inference."""

    name = "groq"

    def __init__(self, api_key: str = "", **kwargs):
        super().__init__(
            name="groq",
            api_key=api_key,
            base_url=GROQ_BASE_URL,
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
                text="", model=model, provider="groq",
                error="No Groq API key configured",
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
                        text="", model=model, provider="groq",
                        error="Unauthorized — check API key",
                    )
                if response.status_code == 403:
                    return AIResponse(
                        text="", model=model, provider="groq",
                        error="Forbidden — model not available or rate limit",
                    )
                if response.status_code == 429:
                    return AIResponse(
                        text="", model=model, provider="groq",
                        error="Rate limited (429)",
                    )
                if response.status_code != 200:
                    body = response.text[:300]
                    return AIResponse(
                        text="", model=model, provider="groq",
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
                        provider="groq",
                        tokens_used=data.get("usage", {}).get("total_tokens", 0),
                        latency_ms=elapsed,
                    )

                self._fail_count += 1
                return AIResponse(
                    text="", model=model, provider="groq",
                    error="Empty response",
                )

        except httpx.TimeoutException:
            self._fail_count += 1
            return AIResponse(
                text="", model=model, provider="groq",
                error="Timeout",
            )
        except Exception as e:
            self._fail_count += 1
            logger.error(f"Groq error: {e}")
            return AIResponse(
                text="", model=model, provider="groq",
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
