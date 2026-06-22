"""Provider Manager v7.1 — MULTI-PROVIDER FALLBACK for Dasha Bot.

COMPLETE FALLBACK CHAIN (tested & integrated):
  1. LOCAL:        RuadaptQwen3-4B (primary, no internet needed) — CHAT/FUNCTION only
  2. GITHUB:       GitHub Models via PAT (free, GPT-4.1-mini)
  3. DEEPINFRA:    Qwen3-32B (free, excellent Russian, 32B)
  4. HUGGINGFACE:  HF Inference via HF_TOKEN (free, Qwen2.5/Llama-3.1/Mistral)
  5. GROQ:         Groq free tier (free, ULTRA FAST Llama-3.3-70B)
  6. GEMINI:       Google Gemini free (free, Gemini-2.0-Flash)
  7. OPENROUTER:   OpenRouter free models (free, 20+ models)
  8. CEREBRAS:     Cerebras free tier (free, ultra-fast Llama-3.3-70B)
  9. LLM7:         LLM7.io (FREE, NO key needed, qwen3-235b GPT-4 class!) ⭐ v7.1
  10. CHUTES:      Chutes AI (free, DeepSeek-V3/Qwen3-235B) ⭐ v7.1
  11. POLLINATIONS: Pollinations v7.1 (35 auth models: 9 always + 6 balance + 8 premium-sometimes + 12 premium-402)

NOTE: Local model is BYPASSED for COMMENT route (group messages) because it
takes ~85s per response — too slow for real-time chat. See LOCAL_FOR_COMMENTS.

NOTE: LLM7.io is the ONLY provider that requires NO API key at all —
qwen3-235b gives GPT-4 class Russian quality for free, no registration.
Placed before Pollinations in the chain for better quality fallback.

NOTE: Pollinations v7.1 — 35 auth models tiered by availability:
  - 9 ALWAYS working (openai, mistral, gemma, nova-fast, llama-scout, qwen-coder,
    mistral-small-3.2, mistral-small ⭐, llama-3.3 ⭐)
  - 6 BALANCE-DEPENDENT (gpt-5.4-mini, llama, perplexity-fast, deepseek, perplexity-deep, openai-fast ⭐)
  - 8 PREMIUM-SOMETIMES (grok, grok-large, mistral-large, nova, qwen-vision, qwen-vision-pro, step-3.5-flash, step-flash)
  - 12 PREMIUM-ALWAYS-402 (kept as best-effort fallbacks)
For COMMENT route, Pollinations skips auth and uses the free anonymous tier.

All providers are OpenAI-compatible except local (llama-cpp).
Each provider is only tried if it has an API key configured.
LLM7 and Pollinations are always available (no key needed).

ROUTE STRATEGY:
  CHAT     → Local → GitHub → DeepInfra → HuggingFace → Groq → Gemini → OpenRouter → Cerebras → LLM7 → Chutes → Pollinations (auth)
  COMMENT  → GitHub → DeepInfra → HuggingFace → Groq → Gemini → OpenRouter → Cerebras → LLM7 → Pollinations (free only, NO local, NO auth)
  FUNCTION → Local → GitHub → DeepInfra → HuggingFace → Groq → Gemini → OpenRouter → Cerebras → LLM7 → Chutes → Pollinations (auth)
"""

from __future__ import annotations
import logging
from typing import Any, Optional, List, Dict

from .base import AIResponse, BaseAIProvider
from .local_provider import LocalProvider
from .pollinations_provider import PollinationsProvider
from .github_provider import GitHubModelsProvider
from .huggingface_provider import HuggingFaceProvider
from .groq_provider import GroqProvider
from .gemini_provider import GeminiProvider
from .openrouter_provider import OpenRouterProvider
from .cerebras_provider import CerebrasProvider
from .deepinfra_provider import DeepInfraProvider
from .llm7_provider import LLM7Provider
from .chutes_provider import ChutesProvider

logger = logging.getLogger("dasha.ai.provider_manager")

ROUTE_CHAT = "chat"
ROUTE_COMMENT = "comment"
ROUTE_FUNCTION = "function"
ROUTE_VISION = "vision"
ROUTE_IMAGE = "image"


class ProviderManager:
    """Manages AI providers with LOCAL-FIRST multi-provider fallback."""

    def __init__(
        self,
        pollinations: PollinationsProvider,
        local: Optional[LocalProvider] = None,
        github: Optional[GitHubModelsProvider] = None,
        deepinfra: Optional[DeepInfraProvider] = None,
        huggingface: Optional[HuggingFaceProvider] = None,
        groq: Optional[GroqProvider] = None,
        gemini: Optional[GeminiProvider] = None,
        openrouter: Optional[OpenRouterProvider] = None,
        cerebras: Optional[CerebrasProvider] = None,
        llm7: Optional[LLM7Provider] = None,
        chutes: Optional[ChutesProvider] = None,
        local_system_prompt: str = "",
    ) -> None:
        self.pollinations = pollinations
        self.local = local
        self.github = github
        self.deepinfra = deepinfra
        self.huggingface = huggingface
        self.groq = groq
        self.gemini = gemini
        self.openrouter = openrouter
        self.cerebras = cerebras
        self.llm7 = llm7
        self.chutes = chutes
        # Compact system prompt for the 4B local model (long prompts degrade
        # quality on small models). Empty = use the full prompt from messages.
        self.local_system_prompt = local_system_prompt

        # Stats
        self._counts: Dict[str, int] = {}
        self._total_requests = 0
        self._last_provider: str = ""

    def _count(self, name: str) -> None:
        self._counts[name] = self._counts.get(name, 0) + 1
        self._last_provider = name

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        route_type: str = ROUTE_CHAT,
        **kwargs: Any,
    ) -> AIResponse:
        self._total_requests += 1

        # Build ordered list of providers to try
        providers = self._build_fallback_chain()

        # ── DECIDE: should we try the local model for this route? ──
        # Local model (RuadaptQwen3-4B on CPU) takes ~85s per response — too slow
        # for real-time group comments. Skip it for COMMENT route unless explicitly
        # enabled via LOCAL_FOR_COMMENTS=true. Keep local for CHAT (private) and
        # FUNCTION (channel posts) where quality matters and latency is acceptable.
        try:
            from bot.config import config
            use_local = config.LOCAL_FOR_COMMENTS or route_type != ROUTE_COMMENT
        except Exception:
            use_local = True  # Fallback to old behavior if config unavailable

        # ── STEP 1: TRY LOCAL MODEL (only for CHAT/FUNCTION, or if enabled) ──
        if self.local and use_local:
            # For the 4B local model, use a compact system prompt to improve
            # quality (long prompts degrade quality on small models).
            local_messages = self._build_local_messages(messages, route_type)
            # Use shorter max_tokens for comments to speed up local model
            local_max = self._local_max(route_type, max_tokens)
            result = await self._try_provider(
                self.local, "local", local_messages, model="local-qwen3-4b",
                temperature=temperature, max_tokens=local_max,
                route_type=route_type, **kwargs,
            )
            if result.ok:
                return result
            # If local is busy/error — skip immediately, go to cloud
            logger.info(f"Local model skipped ({result.error}), trying cloud providers...")
        elif self.local and not use_local:
            logger.debug(f"Local model bypassed for {route_type} route (cloud-only for speed)")

        # ── STEP 2-N: TRY CLOUD PROVIDERS IN ORDER ──
        cloud_providers = providers  # All non-local providers
        for provider in cloud_providers:
            result = await self._try_provider(
                provider, provider.name, messages, model=model,
                temperature=temperature, max_tokens=max_tokens,
                route_type=route_type, **kwargs,
            )
            if result.ok:
                return result

        # ── ALL PROVIDERS FAILED ──
        logger.error("ALL providers failed (Local=%s, %d cloud providers)", use_local, len(cloud_providers))
        return AIResponse(
            text="", model=model, provider="none",
            error="All AI providers failed",
        )

    def _build_local_messages(
        self, messages: List[Dict[str, str]], route_type: str,
    ) -> List[Dict[str, str]]:
        """Build messages with compact system prompt for the 4B local model.

        4B models (RuadaptQwen3-4B) need short, direct instructions — long
        prompts (1500+ chars) degrade quality and waste context. For CHAT and
        COMMENT routes, replace the system message with the compact
        LOCAL_MODEL_SYSTEM_PROMPT (~500 chars). For FUNCTION (channel posts)
        keep the full prompt since quality matters most there and latency is
        acceptable (background task).
        """
        if not self.local_system_prompt or not messages:
            return messages
        if route_type == ROUTE_FUNCTION:
            return messages  # Keep full prompt for channel post quality

        result = []
        for msg in messages:
            if msg.get("role") == "system":
                result.append({"role": "system", "content": self.local_system_prompt})
            else:
                result.append(msg)
        return result

    async def _try_provider(
        self,
        provider: BaseAIProvider,
        name: str,
        messages: List[Dict[str, str]],
        **kwargs: Any,
    ) -> AIResponse:
        """Try a single provider, return result (ok or error).

        Passes route_type to providers that support it (e.g. Pollinations
        uses it to decide whether to use auth or free tier).
        """
        try:
            available = await provider.is_available()
        except Exception:
            available = False

        if not available:
            logger.debug(f"Provider '{name}' not available, skipping")
            return AIResponse(
                text="", model="", provider=name,
                error="Not available",
            )

        try:
            # Pass route_type to providers that support it (e.g. Pollinations)
            result = await provider.chat(messages=messages, **kwargs)
            if result.ok:
                self._count(name)
                logger.info(f"✅ Provider '{name}' succeeded ({result.latency_ms:.0f}ms)")
                return result
            else:
                logger.warning(
                    f"Provider '{name}' returned error: {result.error}"
                )
                return result
        except Exception as exc:
            logger.error(f"Provider '{name}' exception: {exc}")
            return AIResponse(
                text="", model="", provider=name,
                error=str(exc),
            )

    def _build_fallback_chain(self) -> List[BaseAIProvider]:
        """Build ordered list of cloud providers (with keys configured).

        Order: GitHub → DeepInfra → HuggingFace → Groq → Gemini → OpenRouter → Cerebras → LLM7 → Chutes → Pollinations
        LLM7 and Pollinations are always available (no key needed).
        """
        chain: List[BaseAIProvider] = []

        if self.github:
            chain.append(self.github)
        if self.deepinfra:
            chain.append(self.deepinfra)
        if self.huggingface:
            chain.append(self.huggingface)
        if self.groq:
            chain.append(self.groq)
        if self.gemini:
            chain.append(self.gemini)
        if self.openrouter:
            chain.append(self.openrouter)
        if self.cerebras:
            chain.append(self.cerebras)

        # LLM7 — FREE, no key needed, qwen3-235b (GPT-4 class Russian)
        if self.llm7:
            chain.append(self.llm7)

        # Chutes — free with key, DeepSeek-V3 and Qwen3-235B
        if self.chutes:
            chain.append(self.chutes)

        # Pollinations — always available, absolute last resort
        chain.append(self.pollinations)

        logger.debug(
            f"Cloud fallback chain: "
            + " → ".join(p.name for p in chain)
        )
        return chain

    @staticmethod
    def _local_max(route_type: str, default_max: int) -> int:
        """Return max_tokens for local model based on route type."""
        if route_type == ROUTE_CHAT:
            return min(default_max, 2048)
        elif route_type == ROUTE_COMMENT:
            return min(default_max, 512)
        else:  # FUNCTION
            return min(default_max, 2048)

    async def generate_image(
        self, prompt: str, width: int = 1024, height: int = 1024,
        model: str = "", **kwargs,
    ) -> AIResponse:
        self._total_requests += 1
        result = await self.pollinations.generate_image(
            prompt=prompt, width=width, height=height, model=model, **kwargs,
        )
        if result.ok:
            self._count("pollinations-image")
        return result

    async def chat_local_only(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> AIResponse:
        """Generate using LOCAL model ONLY — no cloud fallback."""
        self._total_requests += 1

        if not self.local:
            return AIResponse(
                text="", model="local", provider="none",
                error="chat_local_only called but no local provider configured",
            )

        try:
            local_avail = await self.local.is_available()
        except Exception:
            local_avail = False

        if not local_avail:
            return AIResponse(
                text="", model="local", provider="none",
                error="Local model not available",
            )

        try:
            result = await self.local.chat(
                messages=messages, model="local-qwen3-4b",
                temperature=temperature, max_tokens=min(max_tokens, 2048),
                **kwargs,
            )
            if result.ok:
                self._count("local")
            return result
        except Exception as exc:
            logger.error(f"chat_local_only exception: {exc}")
            return AIResponse(
                text="", model="local", provider="none",
                error=f"Local model exception: {exc}",
            )

    def is_available(self) -> bool:
        return True

    async def close(self) -> None:
        for p in [self.local, self.pollinations, self.github, self.deepinfra,
                   self.huggingface, self.groq, self.gemini, self.openrouter,
                   self.cerebras, self.llm7, self.chutes]:
            if p:
                await p.close()

    def get_status(self) -> Dict[str, Any]:
        status: Dict[str, Any] = {
            "total_requests": self._total_requests,
            "last_provider": self._last_provider,
            "counts": dict(self._counts),
            "providers": {},
        }
        for name, p in [
            ("local", self.local),
            ("github", self.github),
            ("deepinfra", self.deepinfra),
            ("huggingface", self.huggingface),
            ("groq", self.groq),
            ("gemini", self.gemini),
            ("openrouter", self.openrouter),
            ("cerebras", self.cerebras),
            ("llm7", self.llm7),
            ("chutes", self.chutes),
            ("pollinations", self.pollinations),
        ]:
            if p:
                status["providers"][name] = p.get_status()
        return status
