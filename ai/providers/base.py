"""Base AI Provider — Abstract interface for LLM providers."""

import abc
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field


@dataclass
class AIResponse:
    """Response from an AI provider."""
    text: str = ""
    model: str = ""
    provider: str = ""
    tokens_used: int = 0
    cached: bool = False
    error: Optional[str] = None
    error_message: str = ""
    image_url: Optional[str] = None
    image_b64: Optional[str] = None
    latency_ms: float = 0.0
    raw_response: Optional[Dict[str, Any]] = None

    @property
    def ok(self) -> bool:
        if self.error is not None and self.error is not False:
            return False
        if self.error_message and not self.text and not self.image_url and not self.image_b64:
            return False
        return bool(self.text or self.image_url or self.image_b64)

    def __bool__(self) -> bool:
        return self.ok


class BaseAIProvider(abc.ABC):
    """Abstract base class for AI providers."""

    name: str = "base"

    def __init__(self, name: str = "", api_key: str = "", base_url: str = "", **kwargs: Any):
        self.name = name or self.name
        self.api_key = api_key
        self.base_url = base_url
        self._available = True
        self._session: Any = None

    @abc.abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> AIResponse:
        pass

    @abc.abstractmethod
    async def is_available(self) -> bool:
        pass

    async def generate_image(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        model: str = "",
        **kwargs,
    ) -> AIResponse:
        return AIResponse(
            text="",
            model=model,
            provider=self.name,
            error="Image generation not supported",
        )

    async def close(self) -> None:
        if self._session and hasattr(self._session, "close"):
            await self._session.close()
            self._session = None

    def format_messages(
        self,
        system_prompt: str,
        history: List[Dict[str, Any]],
        user_message: str,
    ) -> List[Dict[str, str]]:
        messages = [{"role": "system", "content": system_prompt}]
        for msg in history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})
        return messages