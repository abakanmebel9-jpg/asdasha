"""Pollinations AI Provider v7.1 — LIVE-TESTED MODELS for Dasha Bot.

COMPREHENSIVE LIVE TEST RESULTS (v7.1 — 2026-03, 42 text-output models tested
against the API key sk_... with a Russian chat prompt + a brief comment prompt):

  ✅ WORKS_RU_GOOD (15 models — perfect Russian, fast, ALWAYS available):
    - openai           → q5, ~1.3s  — best overall (structured, perfect RU)
    - mistral          → q5, ~1.4s  — fast, reasoning
    - gemma            → q5, ~1.5s  — reasoning, solid
    - nova-fast        → q5, ~1.0s  — solid, fast
    - llama-scout      → q5, ~1.1s  — fast, good RU
    - qwen-coder       → q5, ~1.2s  — great RU despite "coder" name
    - mistral-small-3.2 → q5, ~1.1s — solid fallback
    - mistral-small    → q5, ~2.6s  — Mistral Small 2603 (Venice), excellent RU ⭐ v7.1
    - llama-3.3        → q5, ~2.7s  — Llama-3.3-70B-Instruct, excellent RU ⭐ v7.1
    - gpt-5.4-mini     → q5, ~1.1s  — GPT-5.4 (balance-dependent)
    - llama            → q5, ~1.4s  — fast (balance-dependent)
    - perplexity-fast  → q5, ~1.1s  — ⚠️ injects [1][2] citations (stripped)
    - deepseek         → q5, ~1.0s  — reasoning (balance-dependent)
    - perplexity-deep  → q5, ~1.1s  — ⚠️ citations stripped
    - openai-fast      → q5, ~10s   — gpt-5-nano (balance-dependent) ⭐ v7.1

  ✅ PREMIUM_SOMETIMES_WORKS (8 models — q5 RU, work when balance permits):
    - grok             → q5, ~1.2s  — excellent RU
    - grok-large       → q5, ~1.1s  — reasoning
    - mistral-large    → q5, ~1.6s  — long posts
    - nova             → q5, ~1.1s  — structured output
    - qwen-vision      → q5, ~1.0s  — vision-capable, text works
    - qwen-vision-pro  → q5, ~1.1s  — reasoning, vision
    - step-3.5-flash   → q5, ~0.9s  — reasoning, fast
    - step-flash       → q4, ~1.1s  — sometimes misses contacts

  ❌ FAIL_402_ALWAYS (12 premium models — always insufficient balance):
    deepseek-pro, glm, gpt-5.4, grok-4-20-reasoning, kimi, kimi-code,
    openai-large, perplexity, perplexity-reasoning, qwen-large,
    openai-audio, openai-audio-large
    (Kept as best-effort fallbacks — work when balance is topped up.)

  ❌ ENGLISH_ONLY: polly (returns English error text — useless for Russian)
  ❌ NOT_CHAT_MODELS: qwen-safety (classifier, not chat), midijourney (music notation)
  ⚠️  EXCLUDED: minimax, minimax-m2.7 — HALLUCINATE fake phone numbers
      ("+7 (923) 000-00-00") or REFUSE to roleplay. Too dangerous.

  ALIAS NOTE (v7.1): Several model names are aliases for the same backend:
    grok-4 = grok, qwen-vl = qwen-vision, qwen3-vl = qwen-vision,
    qwen3-coder = qwen-coder, llama-4-scout = llama-scout,
    openai-mini = gpt-5.4-mini, gpt-5-mini = gpt-5.4-mini
    mistral-small ≠ mistral-small-3.2 (DIFFERENT models! small=2603, small-3.2=3.2)

  ✅ FREE TIER (text.pollinations.ai, NO key needed):
    - openai → Works (gpt-oss-20b), good Russian, ~6-10s (last resort)

  MODEL COUNT: 15 always+balance + 8 premium-sometimes + 12 premium-always-402 = 35 auth models
  Verified-working models are tried FIRST so 402s rarely waste time.

  NOTE: Model availability CHANGES over time depending on Pollinations balance.
  The cooldown system handles this — if a model returns 402, it's cooled down
  for 10 min. If all auth models fail, falls through to free tier automatically.
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

# All auth models known to the API (v7.1 — 35 text-output models).
# Ordering = verified-always-working first, then premium-sometimes, then premium-best-effort.
AUTH_CHAT_MODELS = [
    # ── VERIFIED ALWAYS WORKING (9) — excellent Russian, always available ──
    "openai",             # ~1.3s, q5, best overall (structured, perfect RU)
    "mistral",            # ~1.4s, q5, fast, reasoning
    "gemma",              # ~1.5s, q5, reasoning, solid
    "nova-fast",          # ~1.0s, q5, solid, fast
    "llama-scout",        # ~1.1s, q5, fast, good RU
    "qwen-coder",         # ~1.2s, q5, great RU despite "coder" name
    "mistral-small-3.2",  # ~1.1s, q5, solid fallback
    "mistral-small",      # ~2.6s, q5, Mistral Small 2603 Venice, excellent RU ⭐ v7.1
    "llama-3.3",          # ~2.7s, q5, Llama-3.3-70B, excellent RU ⭐ v7.1
    # ── BALANCE-DEPENDENT (6) — work when balance permits, sometimes 402 ──
    "gpt-5.4-mini",       # ~1.1s, q5, GPT-5.4
    "llama",              # ~1.4s, q5, fast
    "perplexity-fast",    # ~1.1s, q5, ⚠️ citations stripped
    "deepseek",           # ~1.0s, q5, reasoning
    "perplexity-deep",    # ~1.1s, q5, ⚠️ citations stripped
    "openai-fast",        # ~10s, q5, gpt-5-nano (balance-dependent) ⭐ v7.1
    # ── PREMIUM SOMETIMES WORKS (8) — q5 RU when balance allows, 402 otherwise ──
    "grok",               # ~1.2s, q5, excellent RU
    "grok-large",         # ~1.1s, q5, reasoning
    "mistral-large",      # ~1.6s, q5, long posts
    "nova",               # ~1.1s, q5, structured output
    "qwen-vision",        # ~1.0s, q5, vision-capable
    "qwen-vision-pro",    # ~1.1s, q5, reasoning, vision
    "step-3.5-flash",     # ~0.9s, q5, reasoning, fast
    "step-flash",         # ~1.1s, q4, sometimes misses contacts
    # ── PREMIUM ALWAYS 402 (12) — rarely work, but kept for when balance is topped up ──
    "gpt-5.4",            # premium, reasoning, 1M context
    "openai-large",       # premium, reasoning
    "deepseek-pro",       # premium, reasoning
    "glm",                # premium, reasoning
    "kimi",               # premium, reasoning, vision
    "kimi-code",          # premium, reasoning, vision
    "perplexity",         # premium, ⚠️ citations
    "perplexity-reasoning",  # premium, reasoning, ⚠️ citations
    "qwen-large",         # premium, reasoning, vision
    "grok-4-20-reasoning",  # premium, reasoning, vision
]

# Models that do NOT work / must NOT be used:
# 402 (premium): see PREMIUM ALWAYS 402 list above (cooldown handles automatically)
# 400 (Invalid model): openai-3-large, openai-3-small, universal-2, universal-3-pro,
#   mistral-medium, deepseek-r1, qwen, qwen-2.5, gemma-fast, phi, command-r,
#   qwen3, phi-4, command-r-plus, gemma-3, deepseek-v3, claude-3.5-haiku,
#   yi-1.5, yi-lightning, codestral, llama-4, mistral-nemo, mistral-tiny
# English-only: polly (returns English error text)
# NOT chat models: qwen-safety (classifier), midijourney (music notation)
# HALLUCINATION RISK: minimax, minimax-m2.7 (fabricate fake phone or refuse roleplay — EXCLUDED)
#
# ALIAS NOTE (v7.1): These names map to the same backend model:
#   grok-4 = grok, qwen-vl = qwen-vision, qwen3-vl = qwen-vision,
#   qwen3-coder = qwen-coder, llama-4-scout = llama-scout,
#   openai-mini = gpt-5.4-mini, gpt-5-mini = gpt-5.4-mini
#   mistral-small ≠ mistral-small-3.2 (DIFFERENT models!)
#
# IMPORTANT: The cooldown system handles 402 automatically:
# - If a model returns 402, it's cooled down for 10 minutes
# - Verified-always-working models are tried FIRST, so 402s rarely waste time
# - Premium-sometimes models are tried SECOND, then premium-always-402 last
# - Free tier (openai) always works as last resort

# Models available on FREE tier (text.pollinations.ai, NO key needed)
# Only "openai" works reliably. "openai-fast" returns EMPTY (broken).
FREE_MODELS = ["openai"]

# Best models for CHAT route (private messages — quality matters most)
# Verified-working 15 first (ordered by speed × quality from live test),
# then premium best-effort fallbacks.
CHAT_MODELS = [
    # ── VERIFIED ALWAYS WORKING — fastest high-quality first ──
    "openai",             # ~1.3s, q5 — best overall
    "mistral",            # ~1.4s, q5 — fast
    "gemma",              # ~1.5s, q5 — solid
    "nova-fast",          # ~1.0s, q5 — fast
    "llama-scout",        # ~1.1s, q5 — fast
    "qwen-coder",         # ~1.2s, q5 — great RU
    "mistral-small-3.2",  # ~1.1s, q5 — solid fallback
    "mistral-small",      # ~2.6s, q5 — Mistral Small 2603, excellent RU ⭐ v7.1
    "llama-3.3",          # ~2.7s, q5 — Llama-3.3-70B, excellent RU ⭐ v7.1
    # ── BALANCE-DEPENDENT — work when balance permits ──
    "gpt-5.4-mini",       # ~1.1s, q5 — GPT-5.4
    "llama",              # ~1.4s, q5 — fast
    "perplexity-fast",    # ~1.1s, q5 (citations stripped)
    "deepseek",           # ~1.0s, q5 — reasoning
    "perplexity-deep",    # ~1.1s, q5 (citations stripped)
    "openai-fast",        # ~10s, q5 — gpt-5-nano ⭐ v7.1
    # ── PREMIUM SOMETIMES WORKS (q5 RU when balance allows) ──
    "grok",
    "grok-large",
    "mistral-large",
    "nova",
    "qwen-vision",
    "qwen-vision-pro",
    "step-3.5-flash",
    "step-flash",
    # ── PREMIUM ALWAYS 402 (rarely work, kept for when balance is topped up) ──
    "gpt-5.4",
    "openai-large",
    "deepseek-pro",
    "glm",
    "kimi",
    "kimi-code",
    "perplexity",
    "perplexity-reasoning",
    "qwen-large",
    "grok-4-20-reasoning",
]

# Best models for FUNCTION route (channel posts — quality + structured output)
# Prioritize models with long, well-structured Russian output.
FUNCTION_MODELS = [
    # ── VERIFIED ALWAYS WORKING — best long structured Russian first ──
    "openai",             # q5, ~1.3s — best structured
    "mistral",            # q5, ~1.4s — fast
    "gemma",              # q5, ~1.5s — solid, long output
    "llama-scout",        # q5, ~1.1s — fast, long output
    "qwen-coder",         # q5, ~1.2s — great RU
    "llama-3.3",          # q5, ~2.7s — Llama-3.3-70B, excellent for posts ⭐ v7.1
    "mistral-small",      # q5, ~2.6s — Mistral Small 2603, long output ⭐ v7.1
    "nova-fast",          # q5, ~1.0s — fast
    "mistral-small-3.2",  # q5, ~1.1s — solid fallback
    # ── BALANCE-DEPENDENT ──
    "gpt-5.4-mini",       # q5, ~1.1s
    "llama",              # q5, ~1.4s
    "perplexity-fast",    # q5 (citations stripped)
    "deepseek",           # q5 — thorough
    "perplexity-deep",    # q5 (citations stripped)
    "openai-fast",        # q5, ~10s — gpt-5-nano ⭐ v7.1
    # ── PREMIUM SOMETIMES WORKS (long structured output when available) ──
    "mistral-large",      # best for long posts
    "grok",
    "grok-large",
    "nova",               # structured output
    "qwen-vision-pro",
    "step-3.5-flash",
    "qwen-vision",
    "step-flash",
    # ── PREMIUM ALWAYS 402 ──
    "gpt-5.4",
    "openai-large",
    "deepseek-pro",
    "glm",
    "kimi",
    "kimi-code",
    "perplexity",
    "perplexity-reasoning",
    "qwen-large",
    "grok-4-20-reasoning",
]

# Models for COMMENT route (group chat — fastest decent Russian, real-time)
# COMMENT route normally uses the FREE tier (no key) to preserve quota, but if
# auth is used (e.g. free tier rate-limited), these are the fastest decent models.
COMMENT_MODELS = [
    # ── VERIFIED ALWAYS WORKING — fastest brief Russian ──
    "openai",             # ~1.3s
    "llama-scout",        # ~1.1s
    "mistral",            # ~1.4s
    "nova-fast",          # ~1.0s
    "gemma",              # ~1.5s
    "qwen-coder",         # ~1.2s
    "mistral-small-3.2",  # ~1.1s
    # ── Slower always-working (avoid for real-time unless needed) ──
    # mistral-small, llama-3.3 — ~2.6-2.7s, too slow for comments
    # ── BALANCE-DEPENDENT ──
    "gpt-5.4-mini",       # ~1.1s
    "llama",              # ~1.4s
    "perplexity-fast",    # ~1.1s (citations stripped)
    "deepseek",           # ~1.0s
    "perplexity-deep",    # ~1.1s (citations stripped)
    # openai-fast — ~10s, too slow for comments
    # ── PREMIUM SOMETIMES WORKS (fast when available) ──
    "grok",
    "grok-large",
    "mistral-large",
    "nova",
    "qwen-vision",
    "step-3.5-flash",
    "step-flash",
    # ── PREMIUM ALWAYS 402 ──
    "gpt-5.4",
    "openai-large",
    "deepseek-pro",
    "glm",
    "kimi",
    "kimi-code",
    "perplexity",
    "perplexity-reasoning",
    "qwen-large",
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
