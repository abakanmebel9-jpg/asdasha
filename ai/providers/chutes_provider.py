"""Chutes AI Provider — Free open-source models via chutes.ai.

Chutes AI (llm.chutes.ai):
  Endpoint: https://llm.chutes.ai/v1/chat/completions
  Auth: Free API key from chutes.ai (registration required)
  Compatible: OpenAI Chat Completions format (drop-in replacement)

Available FREE models (2026, excellent for Russian):
  - deepseek-ai/DeepSeek-V3      — Excellent Russian, 685B MoE, top quality
  - Qwen/Qwen3-32B               — Excellent Russian, 32B, fast
  - google/gemma-4-31b-it         — Good Russian, Google model
  - THUDM/GLM-5.1-0414           — Good Russian, reasoning
  - moonshotai/Kimi-K2.6          — Good Russian, reasoning
  - Qwen/Qwen3-235B-A22B         — Best Russian, 235B MoE (may be rate-limited)

Rate Limits (free tier):
  - ~20 RPM per model
  - Requires free API key (register at chutes.ai)
  - No credit card required
  - Models may be temporarily unavailable (auto-fallback handles this)

KEY ADVANTAGE: Access to DeepSeek-V3 and Qwen3-235B — among the best
open-weight models for Russian language, comparable to GPT-4 class quality.

How to get API key:
  1. Go to chutes.ai
  2. Sign up (free)
  3. Go to API Keys → Create Key (starts with cpk_)
  4. Copy key to CHUTES_API_KEY in .env

Reference: https://chutes.ai
"""

import logging
import time
from typing import Optional, List, Dict, Any

import httpx

from ai.providers.base import BaseAIProvider, AIResponse

logger = logging.getLogger("dasha.ai.chutes")

# ── Chutes AI API (OpenAI-compatible) ──
CHUTES_BASE_URL = "https://llm.chutes.ai/v1"
CHAT_URL = f"{CHUTES_BASE_URL}/chat/completions"

# Models optimized for Russian (ranked by quality for Dasha bot)
RUSSIAN_MODELS = [
    "deepseek-ai/DeepSeek-V3",       # Best Russian, 685B MoE, top quality
    "Qwen/Qwen3-32B",                # Excellent Russian, 32B
    "Qwen/Qwen3-235B-A22B",          # Best Russian, 235B MoE (may be slow)
    "google/gemma-4-31b-it",          # Good Russian, Google
    "THUDM/GLM-5.1-0414",            # Good Russian, reasoning
    "moonshotai/Kimi-K2.6",          # Good Russian, reasoning
]

DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3"


class ChutesProvider(BaseAIProvider):
    """Chutes AI provider — free models including DeepSeek-V3 and Qwen3-235B."""

    name = "chutes"

    def __init__(self, api_key: str = "", **kwargs):
        super().__init__(
            name="chutes",
            api_key=api_key,
            base_url=CHUTES_BASE_URL,
            **kwargs,
        )
        self._success_count = 0
        self._fail_count = 0
        self._total_requests = 0
        # Track model availability
        self._model_cooldowns: Dict[str, float] = {}
        self._cooldown_duration = 300  # 5 min

    async def is_available(self) -> bool:
        if not self.api_key:
            return False
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

        if not self.api_key:
            return AIResponse(
                text="", model=model, provider="chutes",
                error="No Chutes API key configured",
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        # Build list of models to try, skip cooled-down ones
        now = time.time()
        if model and model != DEFAULT_MODEL:
            models_to_try = [model]
        else:
            models_to_try = []
            for m in RUSSIAN_MODELS:
                if now >= self._model_cooldowns.get(m, 0):
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
                        return AIResponse(
                            text="", model=try_model, provider="chutes",
                            error="Unauthorized — check API key",
                        )
                    if response.status_code == 402:
                        self._model_cooldowns[try_model] = time.time() + self._cooldown_duration
                        logger.debug(f"Chutes: {try_model} payment required (402)")
                        continue
                    if response.status_code == 429:
                        self._model_cooldowns[try_model] = time.time() + 120
                        logger.debug(f"Chutes: {try_model} rate limited (429)")
                        continue
                    if response.status_code == 404:
                        self._model_cooldowns[try_model] = time.time() + 600
                        logger.debug(f"Chutes: {try_model} not found (404)")
                        continue
                    if response.status_code in (500, 502, 503):
                        self._model_cooldowns[try_model] = time.time() + 180
                        logger.debug(f"Chutes: {try_model} server error ({response.status_code})")
                        continue
                    if response.status_code != 200:
                        body = response.text[:200]
                        return AIResponse(
                            text="", model=try_model, provider="chutes",
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
                            provider="chutes",
                            tokens_used=data.get("usage", {}).get("total_tokens", 0),
                            latency_ms=elapsed,
                        )
                    else:
                        logger.debug(f"Chutes: {try_model} returned empty response")
                        continue

            except httpx.TimeoutException:
                self._fail_count += 1
                self._model_cooldowns[try_model] = time.time() + 120
                logger.debug(f"Chutes: {try_model} timed out")
                continue
            except Exception as e:
                self._fail_count += 1
                logger.error(f"Chutes error ({try_model}): {e}")
                continue

        # All models failed
        self._fail_count += 1
        return AIResponse(
            text="", model=model, provider="chutes",
            error=f"All Chutes models failed (tried {len(models_to_try)})",
        )

    def get_status(self) -> Dict:
        now = time.time()
        available_models = [
            m for m in RUSSIAN_MODELS
            if now >= self._model_cooldowns.get(m, 0)
        ]
        return {
            "status": "available" if (self.api_key and available_models) else "no_key",
            "total_requests": self._total_requests,
            "success_count": self._success_count,
            "fail_count": self._fail_count,
            "has_key": bool(self.api_key),
            "default_model": DEFAULT_MODEL,
            "russian_models": RUSSIAN_MODELS,
            "available_models": available_models,
        }
