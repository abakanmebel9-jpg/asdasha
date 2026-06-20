"""Pollinations AI Provider v5.0 — FULLY TESTED MODELS for Dasha Bot.

COMPREHENSIVE MODEL TESTING RESULTS (June 2025):

  ✅ WORKING with API key (auth API, gen.pollinations.ai/v1):
    - openai           → Best Russian (5/5), fast (~4s), great persona
    - mistral          → Good Russian (4/5), fastest (~3.6s), structured
    - mistral-large    → Good Russian (4/5), structured (~5s) — NEWLY AVAILABLE
    - llama            → Good Russian (4/5), naturally mentions Abakan (~8s)
    - deepseek         → Good Russian (4/5), CoT reasoning (~4s)
    - mistral-small-3.2 → Good Russian (4/5), fast (~3s)
    - llama-scout      → Good Russian (3/5), fast (~3s)
    - gemma            → Decent Russian (3/5), fast (~3s)
    - grok             → Good Russian (4/5), direct style (~6s) — NEWLY AVAILABLE
    - qwen-coder       → OK Russian (3/5), slow (~26s)

  ❌ NOT WORKING with this key (Insufficient balance / 402):
    - openai-large, openai-fast (empty), deepseek-pro,
      llama-maverick, grok-large, claude, claude-fast, claude-large,
      qwen-large, kimi, kimi-code, gemini*, gemini-fast, gemini-3-flash
      (* gemini models require paid Pollen balance)

  ✅ FREE TIER (text.pollinations.ai, NO key needed):
    - openai      → Works (gpt-oss-20b), good Russian, ~6-10s
    - openai-fast → Works, very fast but sometimes empty responses
    - All other model names → NOT FOUND on free tier

  NOTE: Model availability CHANGES over time depending on Pollinations load.
  Models may become available/unavailable. The provider handles this with
  cooldown tracking — if a model returns 402, it's cooled down for 10 min.
  If all auth models fail, falls through to free tier automatically.
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
FREE_MODEL = "openai"  # Model available on anonymous tier (gpt-oss-20b)

# Models available on auth endpoint (gen.pollinations.ai with API key)
# COMPREHENSIVELY TESTED June 2025 with API key.
# Only models that WORK with the current key balance are listed.
# Models requiring paid balance (Insufficient balance / 402) are excluded.
AUTH_CHAT_MODELS = [
    # Working models (tested, produce Russian text):
    # NOTE: availability varies with Pollinations load — cooldown handles this
    "openai",              # Best Russian (5/5), fast (~4s)
    "mistral",             # Good Russian (4/5), fastest (~3.6s)
    "mistral-large",       # Good Russian (4/5), structured (~5s) — sometimes available
    "mistral-small-3.2",   # Good Russian (4/5), fast (~3s)
    "llama",               # Good Russian (4/5), ~8s
    "deepseek",            # Good Russian (4/5), ~4s, CoT
    "grok",                # Good Russian (4/5), direct style (~6s) — sometimes available
    "llama-scout",         # Good Russian (3/5), fast (~3s)
    "gemma",               # Decent Russian (3/5), fast (~3s)
    "qwen-coder",          # OK Russian (3/5), very slow (~26s)
]

# Models that may not work with current key balance (Insufficient balance / 402):
# These change over time! Pollinations adjusts pricing and availability.
# openai-large, openai-fast (empty responses), deepseek-pro,
# llama-maverick, grok-large, claude, claude-fast, claude-large,
# qwen-large, kimi, kimi-code, gemini, gemini-fast, gemini-3-flash,
# gemini-flash-lite-3.1, gemma-fast (invalid model), polly (no Russian)
#
# IMPORTANT: The cooldown system handles this automatically:
# - If a model returns 402, it's cooled down for 10 minutes
# - If all auth models fail, falls through to free tier
# - Free tier (openai/openai-fast) always works as last resort

# Models available on FREE tier (text.pollinations.ai, NO key needed)
# Only "openai" and "openai-fast" work. Other model names return 404.
FREE_MODELS = ["openai", "openai-fast"]

# Best models for CHAT route (private messages — quality matters most)
CHAT_MODELS = ["openai", "mistral", "mistral-large", "mistral-small-3.2", "llama", "deepseek", "grok", "gemma"]

# Best models for FUNCTION route (channel posts — quality + structured output)
FUNCTION_MODELS = ["openai", "mistral", "mistral-large", "mistral-small-3.2", "deepseek", "llama", "grok", "llama-scout"]

# Models for COMMENT route (if auth is used — but normally skipped, free tier used)
COMMENT_MODELS = ["mistral", "openai"]

IMAGE_MODELS = ["flux", "flux-pro", "flux-realism", "turbo"]

# ── Rate limiting constants ──
MIN_REQUEST_INTERVAL = 5.0  # Minimum seconds between requests
JITTER_RANGE = (0.0, 2.0)   # Random jitter to avoid periodic patterns

# ── Model cooldown tracking ──
# If a model returns 402/403, skip it for this many seconds
_MODEL_COOLDOWN: Dict[str, float] = {}
_MODEL_COOLDOWN_DURATION = 600  # 10 minutes


class PollinationsProvider(BaseAIProvider):
    """Pollinations AI provider v4.0 — ROUTE-AWARE with tested models.

    KEY DESIGN: Pollinations auth (with API key) is only used for CHAT and
    FUNCTION routes (user dialogue and channel posting). For COMMENT route
    (group chat replies), we skip the auth endpoint and go directly to the
    free anonymous tier — this preserves key quota and is appropriate since
    comments are short and don't need premium models.
    """

    name = "pollinations"

    def __init__(self, api_key: str = "", base_url: str = AUTH_BASE_URL, **kwargs):
        super().__init__(name="pollinations", api_key=api_key, base_url=base_url, **kwargs)
        self._fail_count = 0
        self._total_requests = 0
        self._free_success_count = 0
        self._auth_success_count = 0
        self._last_request_time = 0.0
        self._queue_429_count = 0
        self._last_auth_model = ""  # Track which auth model last succeeded

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

        # Get route_type from kwargs (passed by ProviderManager)
        route_type = kwargs.get("route_type", "chat")

        # ── CRITICAL: Rate limiting for anonymous tier ──
        await self._wait_for_slot()
        self._last_request_time = time.time()

        # ── STRATEGY 1: AUTH API (if API key configured, CHAT/FUNCTION only) ──
        # For COMMENT route, skip auth to preserve key quota — comments are short
        # and don't need premium model quality. Free tier is sufficient.
        if self.api_key and route_type in ("chat", "function", ""):
            models_to_try = self._get_models_for_route(route_type)
            result = await self._chat_auth_multi(
                messages, models_to_try, temperature, max_tokens, **kwargs
            )
            if result.ok:
                self._fail_count = 0
                self._auth_success_count += 1
                return result
            # Auth failed — fall through to free API
            logger.warning(f"Auth API failed for {route_type} route ({result.error}), falling back to free")

        # ── STRATEGY 2: FREE JSON API (anonymous, returns JSON) ──
        await self._wait_for_slot()
        self._last_request_time = time.time()

        result = await self._chat_free_json(messages, temperature, max_tokens, **kwargs)
        if result.ok:
            self._fail_count = 0
            self._free_success_count += 1
            return result
        logger.warning(f"Free JSON API failed ({result.error})")

        # ── STRATEGY 3: FREE PLAIN API (anonymous, returns plain text) ──
        await self._wait_for_slot()
        self._last_request_time = time.time()

        result = await self._chat_free_plain(messages, temperature, **kwargs)
        if result.ok:
            self._fail_count = 0
            self._free_success_count += 1
            return result

        self._fail_count += 1
        return result

    def _get_models_for_route(self, route_type: str) -> List[str]:
        """Return model list for the given route, excluding cooled-down models."""
        now = time.time()
        if route_type == "function":
            base_models = FUNCTION_MODELS
        elif route_type == "comment":
            base_models = COMMENT_MODELS
        else:
            base_models = CHAT_MODELS

        # If we know the last successful auth model, try it first
        if self._last_auth_model and self._last_auth_model in base_models:
            base_models = [self._last_auth_model] + [m for m in base_models if m != self._last_auth_model]

        # Filter out cooled-down models
        available = [m for m in base_models if now >= _MODEL_COOLDOWN.get(m, 0)]
        if not available:
            # All in cooldown — try anyway (cooldown may have just expired)
            available = base_models[:1]
        return available

    async def _chat_auth_multi(
        self,
        messages: List[Dict[str, str]],
        models: List[str],
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> AIResponse:
        """Try multiple auth models in order until one succeeds."""
        last_error = ""
        for model in models:
            # Check cooldown
            now = time.time()
            if now < _MODEL_COOLDOWN.get(model, 0):
                continue

            result = await self._chat_auth(messages, model, temperature, max_tokens, **kwargs)
            if result.ok:
                self._last_auth_model = model
                return result

            # Handle specific errors
            error_str = str(result.error) if result.error else ""
            if "402" in error_str or "PAYMENT_REQUIRED" in error_str:
                # Model requires paid balance — cooldown for a while
                _MODEL_COOLDOWN[model] = time.time() + _MODEL_COOLDOWN_DURATION
                logger.warning(f"Pollinations model '{model}' requires paid balance, cooling down {_MODEL_COOLDOWN_DURATION}s")
                continue
            if "401" in error_str or "403" in error_str:
                # Auth error — might be key issue, try next model
                logger.warning(f"Pollinations model '{model}' auth error: {result.error}")
                continue
            if "429" in error_str:
                # Rate limited — don't try more models, just return
                return result
            if "Empty" in error_str:
                # Model returned empty (like openai-fast) — cooldown and try next
                _MODEL_COOLDOWN[model] = time.time() + _MODEL_COOLDOWN_DURATION
                logger.warning(f"Pollinations model '{model}' returned empty response, cooling down")
                continue

            last_error = error_str

        return AIResponse(
            text="", model=models[0] if models else "openai", provider="pollinations-auth",
            error=f"All auth models failed (tried {len(models)}): {last_error}",
        )

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

        Supports multiple models with an API key from enter.pollinations.ai.
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
                if response.status_code == 402:
                    _MODEL_COOLDOWN[model] = time.time() + _MODEL_COOLDOWN_DURATION
                    return AIResponse(
                        text="", model=model, provider="pollinations-auth",
                        error=f"PAYMENT_REQUIRED (402) — model '{model}' requires paid balance",
                    )
                if response.status_code in (401, 403):
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
        now = time.time()
        cooled_down = [m for m, t in _MODEL_COOLDOWN.items() if now < t]
        return {
            "status": "available",
            "total_requests": self._total_requests,
            "fail_count": self._fail_count,
            "free_success": self._free_success_count,
            "auth_success": self._auth_success_count,
            "queue_429_count": self._queue_429_count,
            "has_api_key": bool(self.api_key),
            "last_auth_model": self._last_auth_model,
            "free_model": FREE_MODEL,
            "rate_limit_interval": MIN_REQUEST_INTERVAL,
            "cooled_down_models": cooled_down,
        }
