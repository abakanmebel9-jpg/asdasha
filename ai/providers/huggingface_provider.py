"""HuggingFace Inference AI Provider — Free LLM inference via HF_TOKEN.

HuggingFace Inference API (free tier):
  Endpoint (OpenAI-compatible): https://router.huggingface.co/v1/chat/completions
  Auth: HuggingFace token from huggingface.co/settings/tokens
  Compatible: OpenAI Chat Completions format (drop-in replacement)

KEY ADVANTAGE: Uses the HF_TOKEN already configured in repo secrets (for model
download). No additional API key needed — HuggingFace inference is free for
many open-source models with a registered account token.

Available FREE models (router.huggingface.co, OpenAI-compatible, 2025-2026):
  Russian-friendly (ranked by quality for Dasha):
    - Qwen/Qwen2.5-7B-Instruct        — Excellent Russian, fast (RECOMMENDED)
    - Qwen/Qwen2.5-14B-Instruct       — Better Russian, slower
    - meta-llama/Llama-3.1-8B-Instruct — Good Russian, fast
    - mistralai/Mistral-7B-Instruct-v0.3 — Good Russian, fast
    - meta-llama/Meta-Llama-3.1-70B-Instruct — Best Russian (may be rate-limited)
    - 01-ai/Yi-1.5-9B-Chat            — Decent Russian

Rate Limits (free tier):
  - Varies by model popularity and server load
  - Cold start: first request may take 10-20s (model loading)
  - ~100-1000 requests/day depending on model
  - No credit card required
  - Models may be temporarily unavailable (auto-fallback handles this)

How to get HF_TOKEN:
  1. Go to huggingface.co → Sign up (free)
  2. Settings → Access Tokens → Create new token
  3. Role: "Read" (sufficient for inference + model download)
  4. Copy token to HF_TOKEN in .env / repo secrets

Reference: https://huggingface.co/docs/inference-providers
"""

import logging
import time
from typing import Optional, List, Dict, Any

import httpx

from ai.providers.base import BaseAIProvider, AIResponse

logger = logging.getLogger("dasha.ai.huggingface")

# ── HuggingFace Inference API (OpenAI-compatible router) ──
HF_BASE_URL = "https://router.huggingface.co/v1"
CHAT_URL = f"{HF_BASE_URL}/chat/completions"

# Native fallback endpoint (older API, per-model)
HF_NATIVE_BASE = "https://api-inference.huggingface.co/models"

# Models optimized for Russian (ranked by quality for Dasha bot)
# Try each in order if previous returns error/unavailable
RUSSIAN_MODELS = [
    "Qwen/Qwen2.5-7B-Instruct",           # Excellent Russian, fast, reliable
    "meta-llama/Llama-3.1-8B-Instruct",    # Good Russian, fast
    "mistralai/Mistral-7B-Instruct-v0.3",  # Good Russian, fast
    "Qwen/Qwen2.5-14B-Instruct",           # Better Russian, slower
]

DEFAULT_MODEL = "Qwen/Qwen2.5-7B-Instruct"


class HuggingFaceProvider(BaseAIProvider):
    """HuggingFace Inference free AI provider — OpenAI-compatible via HF_TOKEN.

    Uses the same HF_TOKEN already in repo secrets (for model download),
    so no additional configuration needed. Provides access to Qwen, Llama,
    Mistral and other open-source models with good Russian support.
    """

    name = "huggingface"

    def __init__(self, api_key: str = "", **kwargs):
        super().__init__(
            name="huggingface",
            api_key=api_key,
            base_url=HF_BASE_URL,
            **kwargs,
        )
        self._success_count = 0
        self._fail_count = 0
        self._total_requests = 0
        # Track which models are currently unavailable (cold start / load)
        # to skip them for a while instead of retrying on every message.
        self._model_cooldowns: Dict[str, float] = {}
        _COOLDOWN = 300  # 5 min cooldown for unavailable models

    async def is_available(self) -> bool:
        if not self.api_key:
            return False
        # Check if at least one model is not in cooldown
        now = time.time()
        for model in RUSSIAN_MODELS:
            if now >= self._model_cooldowns.get(model, 0):
                return True
        return False  # All models in cooldown

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
                text="", model=model, provider="huggingface",
                error="No HF_TOKEN configured",
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        # Build list of models to try: specified model first, then fallback list
        # Skip models that are in cooldown
        now = time.time()
        if model and model != DEFAULT_MODEL:
            models_to_try = [model]
        else:
            models_to_try = []
            for m in RUSSIAN_MODELS:
                if now >= self._model_cooldowns.get(m, 0):
                    models_to_try.append(m)
            if not models_to_try:
                # All in cooldown — try the default anyway (cooldown may have just expired)
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
                            text="", model=try_model, provider="huggingface",
                            error="Unauthorized — check HF_TOKEN",
                        )
                    if response.status_code == 402 or response.status_code == 403:
                        # Payment required or forbidden — model may need paid plan
                        body = response.text[:200]
                        logger.debug(f"HuggingFace: {try_model} returned {response.status_code}: {body}")
                        self._model_cooldowns[try_model] = time.time() + 300
                        continue
                    if response.status_code == 404:
                        # Model not found on router — try next
                        logger.debug(f"HuggingFace: {try_model} not found (404), trying next")
                        self._model_cooldowns[try_model] = time.time() + 600
                        continue
                    if response.status_code == 429:
                        # Rate limited — cooldown this model and try next
                        logger.debug(f"HuggingFace: {try_model} rate limited (429)")
                        self._model_cooldowns[try_model] = time.time() + 120
                        continue
                    if response.status_code in (500, 502, 503):
                        # Model loading (cold start) or server error — try next
                        body = response.text[:200]
                        logger.debug(f"HuggingFace: {try_model} returned {response.status_code} (loading?): {body}")
                        self._model_cooldowns[try_model] = time.time() + 180
                        continue
                    if response.status_code != 200:
                        body = response.text[:200]
                        return AIResponse(
                            text="", model=try_model, provider="huggingface",
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
                            provider="huggingface",
                            tokens_used=data.get("usage", {}).get("total_tokens", 0),
                            latency_ms=elapsed,
                        )
                    else:
                        logger.debug(f"HuggingFace: {try_model} returned empty response")
                        continue

            except httpx.TimeoutException:
                self._fail_count += 1
                logger.debug(f"HuggingFace: {try_model} timed out, trying next")
                self._model_cooldowns[try_model] = time.time() + 120
                continue
            except Exception as e:
                self._fail_count += 1
                logger.error(f"HuggingFace error ({try_model}): {e}")
                continue

        # All models failed
        self._fail_count += 1
        return AIResponse(
            text="", model=model, provider="huggingface",
            error=f"All HuggingFace models failed (tried {len(models_to_try)})",
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
            "has_token": bool(self.api_key),
            "default_model": DEFAULT_MODEL,
            "russian_models": RUSSIAN_MODELS,
            "available_models": available_models,
            "models_in_cooldown": len(RUSSIAN_MODELS) - len(available_models),
        }
