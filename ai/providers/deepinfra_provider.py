"""DeepInfra AI Provider — Free tier with powerful models.

DeepInfra (deepinfra.com):
  Endpoint: https://api.deepinfra.com/v1/openai/chat/completions
  Auth: Free API key from deepinfra.com
  Compatible: OpenAI Chat Completions format

Available FREE models (2026, excellent for Russian):
  - Qwen/Qwen3-32B — Excellent Russian, 32B, best quality
  - Qwen/Qwen3.7-Max — Excellent Russian, latest Qwen
  - meta-llama/Meta-Llama-3.1-8B-Instruct — Good Russian, fast
  - meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo — Good Russian, ultra-fast
  - mistralai/Mistral-Small-24B-Instruct-2501 — Good Russian

Rate Limits (free tier, 2026):
  - ~10-20 RPM depending on model
  - Requires captcha for anonymous use (API key bypasses this)
  - Free credits on signup
  - No credit card required

How to get API key:
  1. Go to deepinfra.com
  2. Sign up (free, Google/GitHub auth)
  3. Go to API Keys → Create Key
  4. Copy key to DEEPINFRA_API_KEY in .env

KEY ADVANTAGE: DeepInfra has the Qwen3-32B model — one of the best
open-weight models for Russian language, comparable to GPT-4 class quality.

Reference: https://deepinfra.com/docs
"""

import logging
import time
from typing import Optional, List, Dict, Any

import httpx

from ai.providers.base import BaseAIProvider, AIResponse

logger = logging.getLogger("dasha.ai.deepinfra")

# ── DeepInfra API ──
DEEPINFRA_BASE_URL = "https://api.deepinfra.com/v1/openai"
CHAT_URL = f"{DEEPINFRA_BASE_URL}/chat/completions"

# Models optimized for Russian (ranked by quality)
RUSSIAN_MODELS = [
    "Qwen/Qwen3-32B",                                # Best Russian quality (32B)
    "Qwen/Qwen3.7-Max",                              # Latest Qwen, excellent RU
    "mistralai/Mistral-Small-24B-Instruct-2501",     # Good Russian, fast
    "meta-llama/Meta-Llama-3.1-8B-Instruct",         # Good Russian, fast
]

DEFAULT_MODEL = "Qwen/Qwen3-32B"


class DeepInfraProvider(BaseAIProvider):
    """DeepInfra AI provider — free tier with powerful models (Qwen3-32B etc)."""

    name = "deepinfra"

    def __init__(self, api_key: str = "", **kwargs):
        super().__init__(
            name="deepinfra",
            api_key=api_key,
            base_url=DEEPINFRA_BASE_URL,
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
                text="", model=model, provider="deepinfra",
                error="No DeepInfra API key configured",
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
                async with httpx.AsyncClient(timeout=45.0) as client:
                    response = await client.post(
                        CHAT_URL,
                        headers=headers,
                        json=payload,
                    )

                    if response.status_code == 401:
                        return AIResponse(
                            text="", model=try_model, provider="deepinfra",
                            error="Unauthorized — check API key",
                        )
                    if response.status_code == 402:
                        self._model_cooldowns[try_model] = time.time() + self._cooldown_duration
                        logger.debug(f"DeepInfra: {try_model} payment required (402)")
                        continue
                    if response.status_code == 429:
                        self._model_cooldowns[try_model] = time.time() + 120
                        logger.debug(f"DeepInfra: {try_model} rate limited (429)")
                        continue
                    if response.status_code in (500, 502, 503):
                        self._model_cooldowns[try_model] = time.time() + 180
                        logger.debug(f"DeepInfra: {try_model} server error ({response.status_code})")
                        continue
                    if response.status_code != 200:
                        body = response.text[:200]
                        return AIResponse(
                            text="", model=try_model, provider="deepinfra",
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
                            provider="deepinfra",
                            tokens_used=data.get("usage", {}).get("total_tokens", 0),
                            latency_ms=elapsed,
                        )
                    else:
                        logger.debug(f"DeepInfra: {try_model} returned empty response")
                        continue

            except httpx.TimeoutException:
                self._fail_count += 1
                self._model_cooldowns[try_model] = time.time() + 120
                logger.debug(f"DeepInfra: {try_model} timed out")
                continue
            except Exception as e:
                self._fail_count += 1
                logger.error(f"DeepInfra error ({try_model}): {e}")
                continue

        self._fail_count += 1
        return AIResponse(
            text="", model=model, provider="deepinfra",
            error=f"All DeepInfra models failed (tried {len(models_to_try)})",
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
