"""Pollinations AI Provider v6.1 — LIVE-TESTED MODELS for Dasha Bot.

COMPREHENSIVE LIVE TEST RESULTS (v6.1 — 2026, all 34 text-output models tested
against the API key sk_... with a Russian chat prompt + a brief comment prompt):

  ✅ WORKS_RU_GOOD (10 models — perfect Russian, fast):
    - openai           → q5, ~1.3s  — best overall (structured, perfect RU)
    - mistral          → q4-5, ~0.9s — fastest, reasoning
    - gpt-5.4-mini     → q5, ~1.0s  — GPT-5.4
    - llama            → q5, ~0.8s  — fast
    - nova-fast        → q5, ~2.3s  — solid
    - perplexity-fast  → q5, ~3.0s  — ⚠️ injects [1][2] citations (stripped)
    - llama-scout      → q4, ~1.8s  — fast on brief
    - qwen-coder       → q5, ~3.3s  — great RU despite "coder" name
    - deepseek         → q5, ~9.8s  — reasoning, slow but thorough
    - gemma            → q5, ~3.3s  — reasoning, very slow on long prompts

  ✅ WORKS_RU_OK (2 models — usable fallback):
    - perplexity-deep  → q3, ~3.5s  — ⚠️ citations stripped ⭐ NEW
    - mistral-small-3.2 → q3, ~3.1s — decent

  ❌ FAIL_402 (20 premium models — insufficient balance, auto-cooled down 10 min):
    deepseek-pro, glm, gpt-5.4, grok, grok-4-20-reasoning, grok-large, kimi,
    kimi-code, minimax, minimax-m2.7, mistral-large, nova, openai-large,
    perplexity, perplexity-reasoning, qwen-large, qwen-vision, qwen-vision-pro,
    step-3.5-flash, step-flash
    (These are kept in the lists as best-effort fallbacks — they work when the
     Pollinations pollen balance is topped up. The cooldown system skips them
     automatically on 402 so they don't waste time.)

  ❌ FAIL_EMPTY: openai-fast (returns content:"" for any prompt — BROKEN, removed)
  ❌ ENGLISH_ONLY: polly (returns English error text — useless for Russian)
  ❌ DOES_NOT_EXIST: mistral-small (only mistral-small-3.2 exists — removed)
  ⚠️  EXCLUDED: minimax, minimax-m2.7 — HALLUCINATE a fake phone number
      ("+7 (923) 000-00-00") instead of the real contact. Too dangerous for a
      bot whose purpose is giving the correct +7 (913) 448-37-17.

  ✅ FREE TIER (text.pollinations.ai, NO key needed):
    - openai → Works (gpt-oss-20b), good Russian, ~6-10s (last resort)

  MODEL COUNT: 12 verified-working + 18 premium best-effort = 30 auth models
  (expanded from the original 19). Verified-working models are tried FIRST so
  402s rarely waste time; the cooldown system handles the rest.

  NOTE: Model availability CHANGES over time depending on Pollinations load.
  The provider handles this with cooldown tracking — if a model returns 402,
  it's cooled down for 10 min. If all auth models fail, falls through to
  free tier automatically.
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

# ════════════════════════════════════════════════════════════════════════════
# LIVE TEST RESULTS (v6.1 — 2026, all 34 text models tested against the key)
#   Test A: chat/quality (RU grammar + contacts), Test B: brief comment (RU).
#   ✅ WORKS_RU_GOOD (12): openai, mistral, gpt-5.4-mini, llama, nova-fast,
#      perplexity-fast, llama-scout, qwen-coder, deepseek, gemma,
#      mistral-small-3.2, perplexity-deep (NEW)
#   ⚠️  perplexity-* inject [1][2] citation markers — stripped in _clean_response.
#   ❌ FAIL_402 (20, premium — insufficient balance, cooldown handles):
#      deepseek-pro, glm, gpt-5.4, grok, grok-4-20-reasoning, grok-large, kimi,
#      kimi-code, minimax, minimax-m2.7, mistral-large, nova, openai-large,
#      perplexity, perplexity-reasoning, qwen-large, qwen-vision,
#      qwen-vision-pro, step-3.5-flash, step-flash
#   ❌ FAIL_EMPTY: openai-fast (returns content:"" for any prompt — BROKEN)
#   ❌ ENGLISH_ONLY: polly (returns English error text — useless for Russian)
#   ❌ DOES_NOT_EXIST: mistral-small (only mistral-small-3.2 exists in API)
#   ⚠️  EXCLUDED: minimax & minimax-m2.7 — even when they work, they HALLUCINATE
#      a FAKE phone number ("+7 (923) 000-00-00") instead of the real contact.
#      Too dangerous for a bot whose purpose is giving the correct contact.
# ════════════════════════════════════════════════════════════════════════════

# All auth models known to the API (documentation — 34 text-output models).
# Ordering = verified-working first, then premium best-effort (402 cooldown).
AUTH_CHAT_MODELS = [
    # ── VERIFIED WORKING (12) — excellent/good Russian, tested live ──
    "openai",             # ~1.3-1.6s, q5, best overall (structured, perfect RU)
    "mistral",            # ~0.9-2.1s, q4-5, fastest, reasoning
    "gpt-5.4-mini",       # ~1.0-2.6s, q5, GPT-5.4
    "llama",              # ~0.8-2.9s, q5, fast
    "nova-fast",          # ~2.3-3.2s, q5, solid
    "perplexity-fast",    # ~3.0-4.3s, q5, ⚠️ citations stripped
    "llama-scout",        # ~1.8-6.4s, q4, fast on brief
    "qwen-coder",         # ~3.3-10.3s, q5, great RU despite "coder" name
    "deepseek",           # ~9.8-11.2s, q5, reasoning, slow
    "gemma",              # ~3.3-25.9s, q5, reasoning, very slow Test A
    "mistral-small-3.2",  # ~3.1-6.6s, q3, decent fallback
    "perplexity-deep",    # ~3.5-5.3s, q3, ⚠️ citations stripped ⭐ NEW
    # ── PREMIUM BEST-EFFORT (402 — cooled down 10 min, tried when balance permits) ──
    "nova",               # premium, long structured output when available
    "grok",               # premium, q5 RU when available (worked Test A)
    "grok-large",         # premium, reasoning
    "mistral-large",      # premium, long posts
    "step-3.5-flash",     # premium, reasoning
    "gpt-5.4",            # premium, reasoning, 1M context
    "openai-large",       # premium, reasoning
    "deepseek-pro",       # premium, reasoning
    "glm",                # premium, reasoning
    "kimi",               # premium, reasoning, vision
    "kimi-code",          # premium, reasoning, vision
    "perplexity",         # premium, ⚠️ citations
    "perplexity-reasoning",  # premium, reasoning, ⚠️ citations
    "qwen-large",         # premium, reasoning, vision
    "qwen-vision",        # premium, vision-capable (text works)
    "qwen-vision-pro",    # premium, reasoning, vision
    "step-flash",         # premium, reasoning, vision
    "grok-4-20-reasoning",  # premium, reasoning, vision
]

# Models that do NOT work / must NOT be used:
# 402 (premium): see PREMIUM BEST-EFFORT list above (cooldown handles automatically)
# 400 (Invalid model): openai-3-large, openai-3-small, universal-2, universal-3-pro,
#   mistral-medium, deepseek-r1, qwen, gemma-fast, phi, command-r
# EMPTY response: openai-fast (gpt-5-nano burns token budget on hidden reasoning)
# English-only: polly (returns English error text)
# Does not exist: mistral-small (only mistral-small-3.2 exists)
# HALLUCINATION RISK: minimax, minimax-m2.7 (fabricate fake phone — EXCLUDED)
#
# IMPORTANT: The cooldown system handles 402 automatically:
# - If a model returns 402, it's cooled down for 10 minutes
# - Verified-working models are tried FIRST, so 402s rarely waste time
# - Free tier (openai) always works as last resort

# Models available on FREE tier (text.pollinations.ai, NO key needed)
# Only "openai" works reliably. "openai-fast" returns EMPTY (broken).
FREE_MODELS = ["openai"]

# Best models for CHAT route (private messages — quality matters most)
# Verified-working 12 first (ordered by speed × quality from live test),
# then premium best-effort fallbacks.
CHAT_MODELS = [
    # ── VERIFIED WORKING — fastest high-quality first ──
    "openai",             # ~1.3s, q5 — best overall
    "mistral",            # ~0.9s, q4-5 — fastest
    "gpt-5.4-mini",       # ~1.0s, q5 — GPT-5.4
    "llama",              # ~0.8s, q5 — fast
    "nova-fast",          # ~2.3s, q5
    "perplexity-fast",    # ~3.0s, q5 (citations stripped)
    "llama-scout",        # ~1.8s, q4
    "qwen-coder",         # ~3.3s, q5
    "deepseek",           # ~9.8s, q5 — slow but thorough
    "gemma",              # ~3.3s, q5 — slow Test A
    "mistral-small-3.2",  # ~3.1s, q3
    "perplexity-deep",    # ~3.5s, q3 (citations stripped) ⭐ NEW
    # ── PREMIUM BEST-EFFORT (402 → cooldown 10 min) ──
    "nova",
    "grok",
    "grok-large",
    "mistral-large",
    "step-3.5-flash",
    "gpt-5.4",
    "openai-large",
    "deepseek-pro",
    "glm",
    "kimi",
    "kimi-code",
    "perplexity",
    "perplexity-reasoning",
    "qwen-large",
    "qwen-vision",
    "qwen-vision-pro",
    "step-flash",
    "grok-4-20-reasoning",
]

# Best models for FUNCTION route (channel posts — quality + structured output)
# Prioritize models with long, well-structured Russian output (Test A length).
FUNCTION_MODELS = [
    # ── VERIFIED WORKING — best long structured Russian first ──
    "openai",             # 1134ch, q5, ~1.4s — best structured
    "mistral",            # 942ch, q4, ~1.5s
    "gpt-5.4-mini",       # 730ch, q5, ~1.8s
    "llama",              # 828ch, q5, ~1.8s
    "nova-fast",          # 626ch, q5, ~2.8s
    "perplexity-fast",    # 565ch, q5, ~3.7s (citations stripped)
    "llama-scout",        # 967ch, q4, ~4.1s
    "qwen-coder",         # 673ch, q5, ~6.8s
    "gemma",              # 1032ch, q5, ~14.6s — slow but long
    "deepseek",           # 365ch, q5, ~10.5s — thorough
    "mistral-small-3.2",  # decent fallback
    "perplexity-deep",    # ⭐ NEW (citations stripped)
    # ── PREMIUM BEST-EFFORT ──
    "nova",
    "grok",
    "mistral-large",
    "grok-large",
    "step-3.5-flash",
    "gpt-5.4",
    "openai-large",
    "deepseek-pro",
    "glm",
    "kimi",
    "kimi-code",
    "perplexity",
    "perplexity-reasoning",
    "qwen-large",
    "qwen-vision",
    "qwen-vision-pro",
    "step-flash",
    "grok-4-20-reasoning",
]

# Models for COMMENT route (group chat — fastest decent Russian, real-time)
# COMMENT route normally uses the FREE tier (no key) to preserve quota, but if
# auth is used (e.g. free tier rate-limited), these are the fastest decent models.
COMMENT_MODELS = [
    # ── VERIFIED WORKING — fastest brief Russian (Test B latency) ──
    "openai",             # ~1.6s
    "llama-scout",        # ~1.8s
    "mistral",            # ~2.1s
    "nova-fast",          # ~2.3s
    "gpt-5.4-mini",       # ~2.6s
    "llama",              # ~2.9s
    "perplexity-fast",    # ~3.1s (citations stripped)
    "mistral-small-3.2",  # ~3.1s
    "gemma",              # ~3.3s
    "qwen-coder",         # ~3.3s
    "perplexity-deep",    # ~3.5s (citations stripped) ⭐ NEW
    "deepseek",           # ~9.8s — slow, last resort
    # ── PREMIUM BEST-EFFORT (rarely hit for comments) ──
    "nova",
    "grok",
    "mistral-large",
    "grok-large",
    "step-3.5-flash",
    "gpt-5.4",
    "openai-large",
    "deepseek-pro",
    "glm",
    "kimi",
    "kimi-code",
    "perplexity",
    "perplexity-reasoning",
    "qwen-large",
    "qwen-vision",
    "qwen-vision-pro",
    "step-flash",
    "grok-4-20-reasoning",
]

IMAGE_MODELS = ["flux", "flux-pro", "flux-realism", "turbo"]

# ── Rate limiting constants ──
MIN_REQUEST_INTERVAL = 5.0  # Minimum seconds between requests
JITTER_RANGE = (0.0, 2.0)   # Random jitter to avoid periodic patterns

# ── Model cooldown tracking ──
# If a model returns 402/403, skip it for this many seconds
_MODEL_COOLDOWN: Dict[str, float] = {}
_MODEL_COOLDOWN_DURATION = 600  # 10 minutes


class PollinationsProvider(BaseAIProvider):
    """Pollinations AI provider v6.0 — ROUTE-AWARE with 19 tested models.

    KEY DESIGN: Pollinations auth (with API key) is only used for CHAT and
    FUNCTION routes (user dialogue and channel posting). For COMMENT route
    (group chat replies), we skip the auth endpoint and go directly to the
    free anonymous tier — this preserves key quota and is appropriate since
    comments are short and don't need premium models.

    v6.0 NEW MODELS: gpt-5.4-mini, nova, nova-fast, minimax, minimax-m2.7,
    perplexity-fast, step-3.5-flash, grok-large — all excellent Russian.
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
            "auth_models_count": len(AUTH_CHAT_MODELS),
            "chat_models": CHAT_MODELS,
            "function_models": FUNCTION_MODELS,
        }
