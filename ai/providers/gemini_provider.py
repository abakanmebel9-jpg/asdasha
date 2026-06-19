"""Google Gemini AI Provider — Free tier via AI Studio.

Google Gemini API (free tier):
  Endpoint (OpenAI-compatible): https://generativelanguage.googleapis.com/v1beta/openai/chat/completions
  Endpoint (native): https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
  Auth: Free API key from aistudio.google.com

Available FREE models (2025-2026):
  - gemini-2.0-flash — Excellent Russian, fast, FREE
  - gemini-2.0-flash-lite — Good Russian, fastest, FREE
  - gemini-1.5-flash — Good Russian, FREE (legacy but still available)
  - gemini-1.5-flash-8b — OK Russian, small, FREE

Rate Limits (free tier):
  - ~15 RPM for flash models
  - ~1 million tokens per minute
  - ~1,500 requests per day (varies)
  - No credit card required

IMPORTANT (2026 changes):
  - Gemini 2.5 Pro removed from free tier (paid only)
  - Gemini 2.0 Flash still FREE
  - Free tier quota reduced by 50-80% since Dec 2025
  - But Flash models are still free and fast

How to get API key:
  1. Go to aistudio.google.com
  2. Click "Get API Key" → "Create API Key"
  3. Select existing Google Cloud project or create new
  4. Copy key to GEMINI_API_KEY in .env

Reference: https://ai.google.dev/gemini-api/docs/models
"""

import logging
import time
from typing import Optional, List, Dict, Any

import httpx

from ai.providers.base import BaseAIProvider, AIResponse

logger = logging.getLogger("dasha.ai.gemini")

# ── Gemini OpenAI-compatible endpoint ──
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
CHAT_URL = f"{GEMINI_BASE_URL}/chat/completions"

# Native endpoint (fallback)
GEMINI_NATIVE_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Models optimized for Russian (FREE tier, ranked by quality)
FREE_MODELS = [
    "gemini-2.0-flash",       # Best quality Russian (free)
    "gemini-2.0-flash-lite",  # Fastest, good Russian (free)
    "gemini-1.5-flash",       # Still free, good Russian
]

DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiProvider(BaseAIProvider):
    """Google Gemini free AI provider — OpenAI-compatible endpoint."""

    name = "gemini"

    def __init__(self, api_key: str = "", **kwargs):
        super().__init__(
            name="gemini",
            api_key=api_key,
            base_url=GEMINI_BASE_URL,
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
                text="", model=model, provider="gemini",
                error="No Gemini API key configured",
            )

        # Strategy 1: OpenAI-compatible endpoint
        result = await self._chat_openai_compat(messages, model, temperature, max_tokens)
        if result.ok:
            self._success_count += 1
            return result

        # Strategy 2: Native Gemini API (fallback)
        logger.warning(f"Gemini OpenAI-compat failed ({result.error}), trying native API")
        result = await self._chat_native(messages, model, temperature, max_tokens)
        if result.ok:
            self._success_count += 1
            return result

        self._fail_count += 1
        return result

    async def _chat_openai_compat(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AIResponse:
        """Chat via OpenAI-compatible endpoint."""
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
            async with httpx.AsyncClient(timeout=45.0) as client:
                url = f"{CHAT_URL}?key={self.api_key}"
                response = await client.post(url, headers=headers, json=payload)

                if response.status_code == 400:
                    return AIResponse(
                        text="", model=model, provider="gemini-openai",
                        error=f"Bad request: {response.text[:200]}",
                    )
                if response.status_code == 429:
                    return AIResponse(
                        text="", model=model, provider="gemini-openai",
                        error="Rate limited (429) — free tier daily quota exceeded",
                    )
                if response.status_code != 200:
                    return AIResponse(
                        text="", model=model, provider="gemini-openai",
                        error=f"HTTP {response.status_code}",
                    )

                data = response.json()
                elapsed = (time.time() - start) * 1000

                text = ""
                if "choices" in data and data["choices"]:
                    text = data["choices"][0].get("message", {}).get("content", "")

                if text:
                    return AIResponse(
                        text=text.strip(),
                        model=model,
                        provider="gemini-openai",
                        tokens_used=data.get("usage", {}).get("total_tokens", 0),
                        latency_ms=elapsed,
                    )

                return AIResponse(
                    text="", model=model, provider="gemini-openai",
                    error="Empty response",
                )
        except httpx.TimeoutException:
            return AIResponse(
                text="", model=model, provider="gemini-openai",
                error="Timeout",
            )
        except Exception as e:
            logger.error(f"Gemini OpenAI-compat error: {e}")
            return AIResponse(
                text="", model=model, provider="gemini-openai",
                error=str(e),
            )

    async def _chat_native(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AIResponse:
        """Chat via native Gemini API (generateContent)."""
        # Convert OpenAI messages format to Gemini format
        system_instruction = ""
        contents = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_instruction = content
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": content}]})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                url = f"{GEMINI_NATIVE_BASE}/models/{model}:generateContent?key={self.api_key}"
                response = await client.post(url, json=payload)

                if response.status_code != 200:
                    return AIResponse(
                        text="", model=model, provider="gemini-native",
                        error=f"HTTP {response.status_code}",
                    )

                data = response.json()
                elapsed = (time.time() - start) * 1000

                text = ""
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        text = parts[0].get("text", "")

                if text:
                    return AIResponse(
                        text=text.strip(),
                        model=model,
                        provider="gemini-native",
                        latency_ms=elapsed,
                    )

                return AIResponse(
                    text="", model=model, provider="gemini-native",
                    error="Empty response",
                )
        except Exception as e:
            logger.error(f"Gemini native error: {e}")
            return AIResponse(
                text="", model=model, provider="gemini-native",
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
