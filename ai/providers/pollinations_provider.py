"""Pollinations AI Provider v3.0 — TESTED FREE MODELS for Dasha Bot.

COMPLETELY TESTED free models (June 2025):

TRIPLE-ENDPOINT FALLBACK STRATEGY:
  1. AUTH API: gen.pollinations.ai/v1 (if POLLINATIONS_API_KEY configured)
     — 106 models (openai, mistral, deepseek, claude, gemini, llama, etc.)
     — Requires Pollinations API key from enter.pollinations.ai

  2. FREE JSON API: text.pollinations.ai/openai/chat/completions (ANONYMOUS)
     — Model: openai (gpt-oss-20b reasoning, 20B params)
     — Queue limit: 1 request per IP (MUST rate-limit 5+ seconds between requests)
     — Speed: 5-10 seconds typical
     — Russian quality: GOOD — natural language, proper grammar
     — Tier: anonymous

  3. FREE PLAIN API: text.pollinations.ai/ (ANONYMOUS, plain text response)
     — Same model, same queue limit
     — Returns raw text (not JSON) — parse carefully
     — Last resort if JSON endpoint fails

RATE LIMITING (CRITICAL for anonymous tier):
  - Queue limit: 1 request per IP address
  - If queue is full: HTTP 429 error
  - Solution: minimum 5 seconds between ANY Pollinations request
  - We track last request time and enforce this globally

TESTED & NOT FREE (require API keys):
  - Google Gemini: requires AI Studio API key
  - HuggingFace: requires HF_TOKEN
  - OpenRouter: requires auth
  - DeepSeek: requires API key
  - Together AI: requires API key
  - Cohere: requires API key
  - Groq: requires API key
  - Cloudflare Workers AI: requires account

LOCAL MODEL IS PRIMARY. Pollinations is FALLBACK ONLY.
"""

import asyncio
import logging
import random
import time
from typing import Optional, List, Dict, Any

import httpx

from ai.providers.base import BaseAIProvider, AIResponse

logger = logging.getLogger("dasha.ai.pollinations")

# ── Endpoints (tested and working) ──
AUTH_BASE_URL = "https://gen.pollinations.ai"
FREE_JSON_URL = "https://text.pollinations.ai/openai/chat/completions"
FREE_PLAIN_URL = "https://text.pollinations.ai"

# ── Models ──
DEFAULT_MODEL = "openai"
FREE_MODEL = "openai"  # Only model available on anonymous tier (gpt-oss-20b)

# Models available on auth endpoint (gen.pollinations.ai with API key)
AUTH_CHAT_MODELS = [
    "openai", "openai-fast", "openai-large",
    "mistral", "mistral-large", "mistral-small-3.2",
    "deepseek", "deepseek-pro",
    "llama", "llama-maverick", "llama-scout",
    "grok", "grok-large",
    "claude", "claude-fast", "claude-large",
    "gemini", "gemini-fast", "gemini-3-flash", "gemini-flash-lite-3.1", "gemini-large",
    "qwen-coder", "qwen-coder-large", "qwen-large",
    "polly", "kimi", "kimi-code",
    "gemma", "gemma-fast",
]

IMAGE_MODELS = ["flux", "flux-pro", "flux-realism", "turbo"]

# ── Rate limiting constants ──
# CRITICAL: Anonymous tier allows only 1 request in queue per IP.
# If we send a request while previous is still processing, we get HTTP 429.
# Minimum interval between requests must be sufficient for model to finish
# generation (typically 5-15 seconds). We use 5 seconds + jitter.
MIN_REQUEST_INTERVAL = 5.0  # Minimum seconds between requests
JITTER_RANGE = (0.0, 2.0)   # Random jitter to avoid periodic patterns


class PollinationsProvider(BaseAIProvider):
    """Pollinations AI provider — TRIPLE FALLBACK with tested free models."""

    name = "pollinations"

    def __init__(self, api_key: str = "", base_url: str = AUTH_BASE_URL, **kwargs):
        super().__init__(name="pollinations", api_key=api_key, base_url=base_url, **kwargs)
        self._fail_count = 0
        self._total_requests = 0
        self._free_success_count = 0
        self._auth_success_count = 0
        self._last_request_time = 0.0
        self._queue_429_count = 0

    async def is_available(self) -> bool:
        return True  # Always available (free anonymous tier)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> AIResponse:
        self._total_requests += 1

        # ── CRITICAL: Rate limiting for anonymous tier ──
        # Anonymous queue allows only 1 concurrent request per IP.
        # We must wait for previous request to finish + buffer.
        await self._wait_for_slot()

        self._last_request_time = time.time()

        # ── STRATEGY 1: AUTH API (if API key configured) ──
        if self.api_key:
            model = model or DEFAULT_MODEL
            result = await self._chat_auth(messages, model, temperature, max_tokens, **kwargs)
            if result.ok:
                self._fail_count = 0
                self._auth_success_count += 1
                return result
            # Auth failed — fall through to free API
            logger.warning(f"Auth API failed ({result.error}), falling back to free")

        # ── STRATEGY 2: FREE JSON API (anonymous, returns JSON) ──
        result = await self._chat_free_json(messages, temperature, max_tokens, **kwargs)
        if result.ok:
            self._fail_count = 0
            self._free_success_count += 1
            return result
        logger.warning(f"Free JSON API failed ({result.error})")

        # ── STRATEGY 3: FREE PLAIN API (anonymous, returns plain text) ──
        # Add extra delay since previous request might still be in queue
        await self._wait_for_slot()
        self._last_request_time = time.time()

        result = await self._chat_free_plain(messages, temperature, **kwargs)
        if result.ok:
            self._fail_count = 0
            self._free_success_count += 1
            return result

        self._fail_count += 1
        return result

    async def _wait_for_slot(self) -> None:
        """Wait for the rate limit slot to be available.

        Anonymous Pollinations allows only 1 request in queue per IP.
        If we send while a request is pending, we get HTTP 429.
        Solution: wait at least MIN_REQUEST_INTERVAL since last request.
        """
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            wait_time = MIN_REQUEST_INTERVAL - elapsed + random.uniform(*JITTER_RANGE)
            logger.debug(f"Rate limiting: waiting {wait_time:.1f}s for Pollinations slot")
            await asyncio.sleep(wait_time)

    async def _chat_auth(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> AIResponse:
        """Chat via AUTHENTICATED API (gen.pollinations.ai/v1).

        Supports 106+ models with an API key from enter.pollinations.ai.
        """
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
        if "seed" in kwargs:
            payload["seed"] = kwargs["seed"]

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
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
                if response.status_code in (401, 402, 403):
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
        except httpx.TimeoutException:
            return AIResponse(
                text="", model=model, provider="pollinations-auth",
                error="Timeout",
            )
        except Exception as e:
            logger.error(f"Auth API error: {e}")
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
        """Chat via FREE JSON API (text.pollinations.ai/openai/chat/completions).

        Model: openai (gpt-oss-20b reasoning, 20B parameters).
        Tested: 5-10 seconds response time, good Russian quality.
        Queue limit: 1 request per IP (enforced by _wait_for_slot).
        """
        headers = {"Content-Type": "application/json"}
        payload: Dict[str, Any] = {
            "model": FREE_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "seed": kwargs.get("seed", random.randint(1, 999999)),
        }

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    FREE_JSON_URL,
                    headers=headers,
                    json=payload,
                )

                if response.status_code == 429:
                    self._queue_429_count += 1
                    logger.warning(
                        f"Pollinations free queue full (429), "
                        f"total 429s: {self._queue_429_count}"
                    )
                    return AIResponse(
                        text="", model=FREE_MODEL, provider="pollinations-free-json",
                        error="Queue full (429)",
                    )
                if response.status_code != 200:
                    return AIResponse(
                        text="", model=FREE_MODEL, provider="pollinations-free-json",
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
                        provider="pollinations-free-json",
                        tokens_used=data.get("usage", {}).get("total_tokens", 0),
                        latency_ms=elapsed,
                    )
                return AIResponse(
                    text="", model=FREE_MODEL, provider="pollinations-free-json",
                    error="Empty response",
                )
        except httpx.TimeoutException:
            return AIResponse(
                text="", model=FREE_MODEL, provider="pollinations-free-json",
                error="Timeout",
            )
        except Exception as e:
            logger.error(f"Free JSON API error: {e}")
            return AIResponse(
                text="", model=FREE_MODEL, provider="pollinations-free-json",
                error=str(e),
            )

    async def _chat_free_plain(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        **kwargs,
    ) -> AIResponse:
        """Chat via FREE PLAIN API (text.pollinations.ai/).

        Same gpt-oss-20b model, returns raw text (not JSON).
        Last resort — if JSON endpoint fails.
        """
        headers = {"Content-Type": "application/json"}
        payload: Dict[str, Any] = {
            "model": FREE_MODEL,
            "messages": messages,
            "temperature": temperature,
            "seed": kwargs.get("seed", random.randint(1, 999999)),
        }

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    FREE_PLAIN_URL,
                    headers=headers,
                    json=payload,
                )

                if response.status_code == 429:
                    self._queue_429_count += 1
                    return AIResponse(
                        text="", model=FREE_MODEL, provider="pollinations-free-plain",
                        error="Queue full (429)",
                    )
                if response.status_code != 200:
                    return AIResponse(
                        text="", model=FREE_MODEL, provider="pollinations-free-plain",
                        error=f"HTTP {response.status_code}",
                    )

                text = response.text.strip()
                elapsed = (time.time() - start) * 1000

                # Filter out error JSON responses
                if text and not text.startswith("{") and not text.startswith('"error"') and len(text) > 5:
                    return AIResponse(
                        text=text,
                        model=FREE_MODEL,
                        provider="pollinations-free-plain",
                        latency_ms=elapsed,
                    )
                return AIResponse(
                    text="", model=FREE_MODEL, provider="pollinations-free-plain",
                    error="Empty or error response",
                )
        except Exception as e:
            logger.error(f"Free plain API error: {e}")
            return AIResponse(
                text="", model=FREE_MODEL, provider="pollinations-free-plain",
                error=str(e),
            )

    async def generate_image(
        self, prompt: str, width: int = 1024, height: int = 1024,
        model: str = "", **kwargs,
    ) -> AIResponse:
        """Generate image via Pollinations image API (always free)."""
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
                        provider="pollinations-image",
                        image_url=str(response.url),
                    )
        except Exception as e:
            logger.error(f"Pollinations image error: {e}")
        return AIResponse(
            text="", model=model, provider="pollinations-image",
            error="Image generation failed",
        )

    def get_status(self) -> Dict:
        return {
            "status": "available",
            "total_requests": self._total_requests,
            "fail_count": self._fail_count,
            "free_success": self._free_success_count,
            "auth_success": self._auth_success_count,
            "queue_429_count": self._queue_429_count,
            "has_api_key": bool(self.api_key),
            "free_model": FREE_MODEL,
            "rate_limit_interval": MIN_REQUEST_INTERVAL,
        }
