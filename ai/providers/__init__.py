"""AI Providers — All available LLM providers for Dasha Bot.

Provider fallback chain:
  1. LocalProvider          — RuadaptQwen3-4B (llama-cpp-python, CPU)
  2. GitHubModelsProvider    — GitHub Models free tier (PAT)
  3. HuggingFaceProvider     — HuggingFace Inference free tier (HF_TOKEN)
  4. GroqProvider            — Groq free tier (API key)
  5. GeminiProvider          — Google Gemini free tier (API key)
  6. OpenRouterProvider      — OpenRouter free models (API key)
  7. CerebrasProvider        — Cerebras free tier (API key)
  8. PollinationsProvider    — Pollinations free (NO KEY NEEDED)
"""

from .base import BaseAIProvider, AIResponse
from .local_provider import LocalProvider
from .pollinations_provider import PollinationsProvider
from .github_provider import GitHubModelsProvider
from .groq_provider import GroqProvider
from .gemini_provider import GeminiProvider
from .openrouter_provider import OpenRouterProvider
from .cerebras_provider import CerebrasProvider
from .huggingface_provider import HuggingFaceProvider
from .provider_manager import ProviderManager
