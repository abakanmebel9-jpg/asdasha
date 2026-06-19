"""GitHub Models AI Provider — Free LLM inference via GitHub PAT.

GitHub Models API (free tier):
  Endpoint: https://models.github.ai/inference/chat/completions
  Auth: GitHub PAT with models:read scope
  Compatible: OpenAI Chat Completions format

Available FREE models (tested June 2025):
  - openai/gpt-4o-mini — Excellent Russian, fast, ~3-5s
  - openai/gpt-4.1-mini — Good Russian, fast
  - meta-llama/Llama-3.3-70B-Instruct — Good Russian
  - mistralai/Mistral-Small-24B-Base-2501 — Decent Russian
  - microsoft/Phi-3.5-mini-instruct — OK Russian (small model)

Rate Limits (free tier):
  - ~15 requests per minute
  - ~150,000 tokens per day
  - No credit card required

How to get PAT:
  1. Go to github.com/settings/tokens
  2. Generate new token (classic)
  3. Set expiration (no expiration recommended)
  4. Select "models:read" scope
  5. Copy token to GH_PAT_TOKEN in .env

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

# Models optimized for Russian (tested & ranked by quality)
RUSSIAN_MODELS = [
    "openai/gpt-4o-mini",           # Best Russian quality, fast
    "openai/gpt-4.1-mini",           # Good Russian, newer
    "meta-llama/Llama-3.3-70B-Instruct",  # Decent Russian
    "mistralai/Mistral-Small-24B-Instruct-2501",  # OK Russian
]

DEFAULT_MODEL = "openai/gpt-4o-mini"


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

    async def is_available(self) -> bool:
        return bool(self.api_key)

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

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        payload: Dict[str, Any] = {
            "model": model,
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
                        text="", model=model, provider="github-models",
                        error="Unauthorized — check PAT and models:read scope",
                    )
                if response.status_code == 403:
                    return AIResponse(
                        text="", model=model, provider="github-models",
                        error="Forbidden — model not available or rate limit",
                    )
                if response.status_code == 429:
                    return AIResponse(
                        text="", model=model, provider="github-models",
                        error="Rate limited (429)",
                    )
                if response.status_code != 200:
                    body = response.text[:300]
                    return AIResponse(
                        text="", model=model, provider="github-models",
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
                        model=model,
                        provider="github-models",
                        tokens_used=data.get("usage", {}).get("total_tokens", 0),
                        latency_ms=elapsed,
                    )

                self._fail_count += 1
                return AIResponse(
                    text="", model=model, provider="github-models",
                    error="Empty response",
                )

        except httpx.TimeoutException:
            self._fail_count += 1
            return AIResponse(
                text="", model=model, provider="github-models",
                error="Timeout",
            )
        except Exception as e:
            self._fail_count += 1
            logger.error(f"GitHub Models error: {e}")
            return AIResponse(
                text="", model=model, provider="github-models",
                error=str(e),
            )

    def get_status(self) -> Dict:
        return {
            "status": "available" if self.api_key else "no_pat",
            "total_requests": self._total_requests,
            "success_count": self._success_count,
            "fail_count": self._fail_count,
            "has_pat": bool(self.api_key),
            "default_model": DEFAULT_MODEL,
            "russian_models": RUSSIAN_MODELS,
        }
