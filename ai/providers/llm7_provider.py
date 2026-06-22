"""LLM7.io AI Provider — FREE, NO KEY NEEDED, excellent Russian models.

LLM7.io (api.llm7.io):
  Endpoint: https://api.llm7.io/v1/chat/completions
  Auth: NO API KEY REQUIRED — works immediately without registration!
        Optional: free API key for higher rate limits (120 RPM vs 30 RPM)
  Compatible: OpenAI Chat Completions format (drop-in replacement)

Available FREE models (2026, tested):
  - qwen3-235b       — BEST Russian quality (235B MoE), excellent grammar, contacts
  - devstral-small-2:24b — Code-focused, decent Russian, variable speed (fallback only)

⚠️ Previously available models NOW UNAVAILABLE (removed from API):
  qwen3-30b-a3b, deepseek-r1-0528, qwen3-32b, deepseek-v3-0324,
  gemma-3-27b, llama-4-scout, mistral-small-3.2

Rate Limits (free tier):
  - ~30 RPM without API key
  - ~120 RPM with free API key (register at llm7.io)
  - No credit card required
  - No registration required for basic use

KEY ADVANTAGE: Zero setup — no API key needed at all. Just send requests.
qwen3-235b is one of the BEST open-weight models for Russian language,
comparable to GPT-4 class quality.

⚠️ EXCLUDED models: minimax, minimax-m2.7 — HALLUCINATE fake phone numbers!

How to get optional API key (for higher rate limits):
  1. Go to llm7.io
  2. Sign up (free)
  3. Go to API Keys → Create Key
  4. Copy key to LLM7_API_KEY in .env

Reference: https://llm7.io
"""

import logging
import time
from typing import Optional, List, Dict, Any

import httpx

from ai.providers.base import BaseAIProvider, AIResponse

logger = logging.getLogger("dasha.ai.llm7")

# ── LLM7.io API (OpenAI-compatible) ──
LLM7_BASE_URL = "https://api.llm7.io/v1"
CHAT_URL = f"{LLM7_BASE_URL}/chat/completions"

# Models optimized for Russian (ranked by quality for Dasha bot)
# v7.2: Only 2 models currently available (7 removed from API)
RUSSIAN_MODELS = [
    "qwen3-235b",                  # BEST Russian (235B MoE), excellent grammar + contacts
    "devstral-small-2:24b",        # Code-focused, decent Russian, variable speed (fallback)
]

# Models that must NOT be used (hallucinate fake phone numbers)
_EXCLUDED_MODELS = {"minimax", "minimax-m2.7"}

DEFAULT_MODEL = "qwen3-235b"


class LLM7Provider(BaseAIProvider):
    """LLM7.io AI provider — FREE, no key needed, qwen3-235b for Russian."""

    name = "llm7"

    def __init__(self, api_key: str = "", **kwargs):
        super().__init__(
            name="llm7",
            api_key=api_key,  # Optional — works without key
            base_url=LLM7_BASE_URL,
            **kwargs,
        )
        self._success_count = 0
        self._fail_count = 0
        self._total_requests = 0
        # Track model availability
        self._model_cooldowns: Dict[str, float] = {}
        self._cooldown_duration = 300  # 5 min

    async def is_available(self) -> bool:
        """Always available — no API key required."""
        # Check if at least one model is not in cooldown
        now = time.time()
        for model in RUSSIAN_MODELS:
            if now >= self._model_cooldowns.get(model, 0):
                return True
        return False

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

        # Check excluded models
        if model in _EXCLUDED_MODELS:
            return AIResponse(
                text="", model=model, provider="llm7",
                error=f"Model {model} is excluded (hallucinates fake phone numbers)",
            )

        headers = {
            "Content-Type": "application/json",
        }
        # Add API key if available (higher rate limits)
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Build list of models to try, skip cooled-down ones
        now = time.time()
        if model and model != DEFAULT_MODEL:
            models_to_try = [model]
        else:
            models_to_try = []
            for m in RUSSIAN_MODELS:
                if m not in _EXCLUDED_MODELS and now >= self._model_cooldowns.get(m, 0):
                    models_to_try.append(m)
            if not models_to_try:
                models_to_try = [DEFAULT_MODEL]

        for try_model in models_to_try:
            payload: Dict[str, Any] = {
                "model": try_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }

            start = time.time()
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(
                        CHAT_URL,
                        headers=headers,
                        json=payload,
                    )

                    if response.status_code == 401:
                        # Auth error — key might be invalid, try without
                        if self.api_key:
                            logger.debug(f"LLM7: Auth failed with key, retrying without")
                            headers_no_auth = {"Content-Type": "application/json"}
                            response = await client.post(
                                CHAT_URL,
                                headers=headers_no_auth,
                                json=payload,
                            )
                        if response.status_code == 401:
                            return AIResponse(
                                text="", model=try_model, provider="llm7",
                                error="Unauthorized — check API key",
                            )

                    if response.status_code == 402:
                        self._model_cooldowns[try_model] = time.time() + self._cooldown_duration
                        logger.debug(f"LLM7: {try_model} payment required (402)")
                        continue
                    if response.status_code == 429:
                        self._model_cooldowns[try_model] = time.time() + 120
                        logger.debug(f"LLM7: {try_model} rate limited (429)")
                        continue
                    if response.status_code == 404:
                        self._model_cooldowns[try_model] = time.time() + 600
                        logger.debug(f"LLM7: {try_model} not found (404)")
                        continue
                    if response.status_code in (500, 502, 503):
                        self._model_cooldowns[try_model] = time.time() + 180
                        logger.debug(f"LLM7: {try_model} server error ({response.status_code})")
                        continue
                    if response.status_code != 200:
                        body = response.text[:200]
                        return AIResponse(
                            text="", model=try_model, provider="llm7",
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
                            model=try_model,
                            provider="llm7",
                            tokens_used=data.get("usage", {}).get("total_tokens", 0),
                            latency_ms=elapsed,
                        )
                    else:
                        logger.debug(f"LLM7: {try_model} returned empty response")
                        continue

            except httpx.TimeoutException:
                self._fail_count += 1
                self._model_cooldowns[try_model] = time.time() + 120
                logger.debug(f"LLM7: {try_model} timed out")
                continue
            except Exception as e:
                self._fail_count += 1
                logger.error(f"LLM7 error ({try_model}): {e}")
                continue

        # All models failed
        self._fail_count += 1
        return AIResponse(
            text="", model=model, provider="llm7",
            error=f"All LLM7 models failed (tried {len(models_to_try)})",
        )

    def get_status(self) -> Dict:
        now = time.time()
        available_models = [
            m for m in RUSSIAN_MODELS
            if now >= self._model_cooldowns.get(m, 0)
        ]
        return {
            "status": "available",
            "total_requests": self._total_requests,
            "success_count": self._success_count,
            "fail_count": self._fail_count,
            "has_key": bool(self.api_key),
            "default_model": DEFAULT_MODEL,
            "russian_models": RUSSIAN_MODELS,
            "available_models": available_models,
            "note": "No API key required — works immediately",
        }
