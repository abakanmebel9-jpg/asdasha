"""Pollinations AI Provider v2.0 — FREE API + AUTH API FALLBACK for Dasha Bot.

DUAL-ENDPOINT STRATEGY:
  1. Primary: gen.pollinations.ai (requires API key — if configured)
  2. Free fallback: text.pollinations.ai/openai/chat/completions (anonymous, NO API key)
     - Only supports "openai" model (maps to gpt-oss-20b internally)
     - Queue limit: 1 request per IP — rate limiting required
     - Reliable and always free

  3. Ultra-free fallback: text.pollinations.ai/ (plain text response)
     - Same as above but returns plain text instead of JSON
     - Used as last resort if /openai/ endpoint fails

LOCAL MODEL IS PRIMARY. Pollinations is FALLBACK ONLY — used when
the local model is physically unavailable (file missing, load error, etc.)

FREE MODELS (tested and working on text.pollinations.ai):
  - openai (gpt-oss-20b) — best quality, good Russian support
"""

import logging
import random
import time
from typing import Optional, List, Dict, Any

import httpx

from ai.providers.base import BaseAIProvider, AIResponse

logger = logging.getLogger("dasha.ai.pollinations")

# ── Endpoints ──
AUTH_BASE_URL = "https://gen.pollinations.ai"
FREE_BASE_URL = "https://text.pollinations.ai/openai"
ULTRA_FREE_URL = "https://text.pollinations.ai"

# ── Models ──
DEFAULT_MODEL = "openai"
FREE_MODEL = "openai"  # Only model available on free endpoint

# Models available on auth endpoint (gen.pollinations.ai with API key)
AUTH_CHAT_MODELS = [
    "openai", "openai-fast", "openai-large", "mistral", "mistral-large",
    "mistral-small-3.2", "deepseek", "deepseek-pro", "llama", "grok",
    "claude", "claude-fast", "claude-large", "gemini", "gemini-fast",
    "gemini-3-flash", "qwen-large", "polly", "kimi",
]

AUTH_CONTENT_MODELS = [
    "openai", "openai-large", "mistral-large", "deepseek-pro",
    "claude-large", "gemini", "qwen-large",
]

IMAGE_MODELS = ["flux", "flux-pro", "flux-realism", "turbo"]


class PollinationsProvider(BaseAIProvider):
    """Pollinations AI provider — FREE API + AUTH API fallback."""

    name = "pollinations"

    def __init__(self, api_key: str = "", base_url: str = AUTH_BASE_URL, **kwargs):
        super().__init__(name="pollinations", api_key=api_key, base_url=base_url, **kwargs)
        self._fail_count = 0
        self._total_requests = 0
        self._free_success_count = 0
        self._auth_success_count = 0
        self._last_request_time = 0.0
        self._min_interval = 2.0  # Min 2s between requests (free queue limit)

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

        # ── Rate limiting for free endpoint (1 req per IP queue) ──
        now = time.time()
        elapsed_since_last = now - self._last_request_time
        if elapsed_since_last < self._min_interval:
            wait = self._min_interval - elapsed_since_last + 0.5
            await asyncio_sleep(wait)

        self._last_request_time = time.time()

        # ── STRATEGY: Try auth API first (if key available), then free ──
        if self.api_key:
            # Use the AUTHENTICATED endpoint with specified model
            model = model or DEFAULT_MODEL
            result = await self._chat_auth(messages, model, temperature, max_tokens, **kwargs)
            if result.ok:
                self._fail_count = 0
                self._auth_success_count += 1
                return result
            logger.warning(f"Auth API failed: {result.error}, falling back to free API")

        # ── FREE FALLBACK: text.pollinations.ai/openai/chat/completions ──
        result = await self._chat_free_json(messages, temperature, max_tokens, **kwargs)
        if result.ok:
            self._fail_count = 0
            self._free_success_count += 1
            return result
        logger.warning(f"Free JSON API failed: {result.error}")

        # ── ULTRA-FREE FALLBACK: text.pollinations.ai/ (plain text) ──
        result = await self._chat_free_plain(messages, temperature, **kwargs)
        if result.ok:
            self._fail_count = 0
            self._free_success_count += 1
            return result

        self._fail_count += 1
        return result

    async def _chat_auth(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> AIResponse:
        """Chat via AUTHENTICATED API (gen.pollinations.ai/v1)."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
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
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    f"{AUTH_BASE_URL}/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )

                if response.status_code == 429:
                    return AIResponse(
                        text="", model=model, provider="pollinations-auth",
                        error="Rate limited (429)",
                    )
                if response.status_code == 401 or response.status_code == 402:
                    return AIResponse(
                        text="", model=model, provider="pollinations-auth",
                        error=f"Auth error ({response.status_code})",
                    )
                if response.status_code != 200:
                    return AIResponse(
                        text="", model=model, provider="pollinations-auth",
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
                        provider="pollinations-auth",
                        tokens_used=data.get("usage", {}).get("total_tokens", 0),
                        latency_ms=elapsed,
                    )
                return AIResponse(
                    text="", model=model, provider="pollinations-auth",
                    error="Empty response",
                )
        except Exception as e:
            return AIResponse(
                text="", model=model, provider="pollinations-auth",
                error=str(e),
            )

    async def _chat_free_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> AIResponse:
        """Chat via FREE API (text.pollinations.ai/openai/chat/completions).

        Returns JSON, model is always "openai" (gpt-oss-20b).
        Queue limit: 1 request per IP.
        """
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": FREE_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if "seed" in kwargs:
            payload["seed"] = kwargs["seed"]
        else:
            payload["seed"] = random.randint(1, 999999)

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    f"{FREE_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                )

                if response.status_code == 429:
                    return AIResponse(
                        text="", model=FREE_MODEL, provider="pollinations-free",
                        error="Queue full (429)",
                    )
                if response.status_code != 200:
                    return AIResponse(
                        text="", model=FREE_MODEL, provider="pollinations-free",
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
                        model=FREE_MODEL,
                        provider="pollinations-free",
                        tokens_used=data.get("usage", {}).get("total_tokens", 0),
                        latency_ms=elapsed,
                    )
                return AIResponse(
                    text="", model=FREE_MODEL, provider="pollinations-free",
                    error="Empty response from free API",
                )
        except httpx.TimeoutException:
            return AIResponse(
                text="", model=FREE_MODEL, provider="pollinations-free",
                error="Timeout",
            )
        except Exception as e:
            return AIResponse(
                text="", model=FREE_MODEL, provider="pollinations-free",
                error=str(e),
            )

    async def _chat_free_plain(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        **kwargs,
    ) -> AIResponse:
        """Chat via ULTRA-FREE API (text.pollinations.ai/ — plain text response).

        Last resort — returns plain text, not JSON.
        """
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": FREE_MODEL,
            "messages": messages,
            "temperature": temperature,
        }
        if "seed" in kwargs:
            payload["seed"] = kwargs["seed"]
        else:
            payload["seed"] = random.randint(1, 999999)

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    ULTRA_FREE_URL,
                    headers=headers,
                    json=payload,
                )

                if response.status_code == 429:
                    return AIResponse(
                        text="", model=FREE_MODEL, provider="pollinations-ultrafree",
                        error="Queue full (429)",
                    )
                if response.status_code != 200:
                    return AIResponse(
                        text="", model=FREE_MODEL, provider="pollinations-ultrafree",
                        error=f"HTTP {response.status_code}",
                    )

                text = response.text.strip()
                elapsed = (time.time() - start) * 1000

                # Filter out error messages in JSON
                if text and not text.startswith("{") and not text.startswith('"error"'):
                    return AIResponse(
                        text=text,
                        model=FREE_MODEL,
                        provider="pollinations-ultrafree",
                        latency_ms=elapsed,
                    )
                return AIResponse(
                    text="", model=FREE_MODEL, provider="pollinations-ultrafree",
                    error="Empty or error response",
                )
        except Exception as e:
            return AIResponse(
                text="", model=FREE_MODEL, provider="pollinations-ultrafree",
                error=str(e),
            )

    async def generate_image(
        self, prompt: str, width: int = 1024, height: int = 1024,
        model: str = "", **kwargs,
    ) -> AIResponse:
        model = model or random.choice(IMAGE_MODELS)
        try:
            url = f"https://image.pollinations.ai/prompt/{prompt}"
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
            "free_success": self._free_success_count,
            "auth_success": self._auth_success_count,
            "has_api_key": bool(self.api_key),
            "base_url": self.base_url,
        }


# ── Helper (can't import asyncio at module level in some contexts) ──
async def asyncio_sleep(seconds: float):
    import asyncio
    await asyncio.sleep(seconds)
