"""GitHub Models AI Provider — Free LLM inference via GitHub PAT.

GitHub Models API (free tier):
  Endpoint: https://models.github.ai/inference/chat/completions
  Auth: GitHub PAT with models:read scope (classic) OR fine-grained PAT with
        "Models" account permission enabled.
  Compatible: OpenAI Chat Completions format
  Catalog:    https://models.github.ai/catalog/models

Available FREE models (catalog verified, 37 models total, tiers: low/high/custom):
  LOW tier (higher rate limits — best for 24/7 bots):
    - openai/gpt-4.1-mini      — Excellent Russian, fast (RECOMMENDED)
    - openai/gpt-4o-mini       — Good Russian, fast
    - openai/gpt-4.1-nano      — Good Russian, fastest OpenAI
    - mistral-ai/mistral-medium-2505 — Good Russian, multimodal
    - mistral-ai/mistral-small-2503  — Good Russian
    - microsoft/phi-4          — Good Russian, strong reasoning
    - microsoft/phi-4-mini-instruct — Fast, decent Russian
    - cohere/cohere-command-a  — Decent Russian
    - meta/meta-llama-3.1-8b-instruct — Fast, OK Russian
  HIGH tier (lower rate limits, higher quality):
    - meta/llama-3.3-70b-instruct — Excellent Russian, 70B
    - deepseek/deepseek-v3-0324    — Good Russian

Rate Limits (free tier, low tier models):
  - ~15 requests per minute (per PAT)
  - ~150,000 tokens per day
  - No credit card required

IMPORTANT — PAT permission:
  Classic PAT:  needs "models:read" scope (checkbox in token settings).
  Fine-grained PAT: needs "Models" permission under Account permissions → Read.
  Without this, ALL models return HTTP 403 "No access to model: ...".
  The provider auto-detects this and disables itself for 30 minutes (circuit
  breaker) to avoid wasting time on every message.

How to get PAT:
  1. Go to github.com/settings/tokens (classic) or fine-grained tokens
  2. Generate new token
  3. Classic: select "models:read" scope
     Fine-grained: enable "Models" account permission (Read-only)
  4. Copy token to GH_PAT_TOKEN in .env / repo secrets

Reference: https://docs.github.com/en/github-models
"""

import logging
import time
from typing import Optional, List, Dict, Any

import httpx

from ai.providers.base import BaseAIProvider, AIResponse

logger = logging.getLogger("dasha.ai.github")

# ── GitHub Models API ──
GITHUB_MODELS_BASE = "https://models.github.ai/inference"
CHAT_URL = f"{GITHUB_MODELS_BASE}/chat/completions"

# Models optimized for Russian (catalog-verified IDs, ranked by quality for Dasha).
# NOTE: IDs must match the catalog exactly (https://models.github.ai/catalog/models).
# Order matters: try each model if previous returns 403/429/404.
RUSSIAN_MODELS = [
    "openai/gpt-4.1-mini",                # Best Russian quality (low tier), fast
    "openai/gpt-4o-mini",                  # Good Russian (low tier), fast
    "openai/gpt-4.1-nano",                 # Good Russian (low tier), fastest OpenAI
    "mistral-ai/mistral-medium-2505",      # Good Russian (low tier), multimodal
    "microsoft/phi-4",                     # Good Russian (low tier), strong reasoning
    "microsoft/phi-4-mini-instruct",       # Fast, decent Russian (low tier)
    "meta/llama-3.3-70b-instruct",         # Excellent Russian (high tier), 70B
    "deepseek/deepseek-v3-0324",           # Good Russian (high tier)
]

DEFAULT_MODEL = "openai/gpt-4.1-mini"

# If the PAT lacks Models permission, disable provider for this long (seconds).
# Permission issues don't resolve quickly, so a long cooldown is appropriate.
_NO_ACCESS_COOLDOWN = 1800  # 30 minutes


class GitHubModelsProvider(BaseAIProvider):
    """GitHub Models free AI provider — OpenAI-compatible with PAT."""

    name = "github-models"

    def __init__(self, api_key: str = "", **kwargs):
        super().__init__(
            name="github-models",
            api_key=api_key,
            base_url=GITHUB_MODELS_BASE,
            **kwargs,
        )
        self._success_count = 0
        self._fail_count = 0
        self._total_requests = 0
        # Circuit breaker: if PAT lacks Models permission (403 "no_access"),
        # disable provider until this timestamp. Avoids retrying on every message.
        self._disabled_until: float = 0.0

    async def is_available(self) -> bool:
        if not self.api_key:
            return False
        # Circuit breaker: skip if disabled due to no_access
        if time.time() < self._disabled_until:
            return False
        return True

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
                text="", model=model, provider="github-models",
                error="No GitHub PAT configured",
            )

        # Circuit breaker check
        if time.time() < self._disabled_until:
            return AIResponse(
                text="", model=model, provider="github-models",
                error="Disabled (no_access cooldown)",
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        # Try models in order — if one returns 403/429, try next
        models_to_try = RUSSIAN_MODELS if not model or model == DEFAULT_MODEL else [model]

        saw_no_access = False  # 403 "no_access" → PAT lacks Models permission
        for try_model in models_to_try:
            payload: Dict[str, Any] = {
                "model": try_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }

            start = time.time()
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        CHAT_URL,
                        headers=headers,
                        json=payload,
                    )

                    if response.status_code == 403:
                        body_text = response.text[:300]
                        # "No access to model" → PAT lacks Models permission.
                        # All models will fail identically → stop trying + cooldown.
                        if "no_access" in body_text.lower() or "no access" in body_text.lower():
                            saw_no_access = True
                            logger.warning(
                                f"GitHub Models: PAT lacks Models permission "
                                f"(no_access for {try_model}). Disabling provider "
                                f"for {_NO_ACCESS_COOLDOWN}s."
                            )
                            break  # stop trying remaining models
                        # Other 403 (e.g., specific model forbidden) → try next
                        logger.debug(f"GitHub Models: {try_model} returned 403, trying next model")
                        continue
                    if response.status_code == 404:
                        # Model not found — try next
                        logger.debug(f"GitHub Models: {try_model} returned 404, trying next model")
                        continue
                    if response.status_code == 401:
                        return AIResponse(
                            text="", model=try_model, provider="github-models",
                            error="Unauthorized — check PAT and models:read scope",
                        )
                    if response.status_code == 429:
                        # Rate limited — don't try more models, just return
                        return AIResponse(
                            text="", model=try_model, provider="github-models",
                            error="Rate limited (429)",
                        )
                    if response.status_code != 200:
                        body = response.text[:300]
                        return AIResponse(
                            text="", model=try_model, provider="github-models",
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
                            provider="github-models",
                            tokens_used=data.get("usage", {}).get("total_tokens", 0),
                            latency_ms=elapsed,
                        )

            except httpx.TimeoutException:
                self._fail_count += 1
                continue  # Try next model on timeout
            except Exception as e:
                self._fail_count += 1
                logger.error(f"GitHub Models error ({try_model}): {e}")
                continue

        # If we saw "no_access", enable the cooldown circuit breaker
        if saw_no_access:
            self._disabled_until = time.time() + _NO_ACCESS_COOLDOWN
            return AIResponse(
                text="", model=model, provider="github-models",
                error="PAT lacks Models permission (no_access) — provider disabled 30min",
            )

        # All models failed (404/timeout/etc.)
        self._fail_count += 1
        return AIResponse(
            text="", model=model, provider="github-models",
            error=f"All GitHub models failed (tried {len(models_to_try)})",
        )

    def get_status(self) -> Dict:
        disabled = time.time() < self._disabled_until
        return {
            "status": "disabled_no_access" if disabled else ("available" if self.api_key else "no_pat"),
            "total_requests": self._total_requests,
            "success_count": self._success_count,
            "fail_count": self._fail_count,
            "has_pat": bool(self.api_key),
            "default_model": DEFAULT_MODEL,
            "russian_models": RUSSIAN_MODELS,
            "disabled_until": self._disabled_until if disabled else 0,
        }
