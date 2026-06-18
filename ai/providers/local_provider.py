"""Local LLM Provider — RuadaptQwen3-4B via llama-cpp-python (GGUF).

CPU-only inference for Dasha Bot. Same model as Asya bot.
- RuadaptQwen3-4B-Instruct Q4_K_M (~2.5GB)
- Russian-optimized tokenizer
- Chat template formatting (Qwen3 ChatML)
"""

import asyncio
import logging
import os
import signal
import time
from typing import Optional, List, Dict

from ai.providers.base import BaseAIProvider, AIResponse

logger = logging.getLogger("dasha.ai.local")

QWEN3_SYSTEM_START = "<|im_start|>system\n"
QWEN3_USER_START = "<|im_start|>user\n"
QWEN3_ASSISTANT_START = "<|im_start|>assistant\n"
QWEN3_END = "<|im_end|>\n"


class LocalProvider(BaseAIProvider):
    """Local LLM provider using llama-cpp-python."""

    name = "local"

    def __init__(self, model_path: str = "", n_ctx: int = 8192,
                 n_threads: int = 3, max_tokens: int = 1024, **kwargs):
        super().__init__(name="local", **kwargs)
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self.default_max_tokens = max_tokens
        self._model = None
        self._lock = asyncio.Lock()
        self._load_attempts = 0
        self._loaded = False
        self._load_error = ""

    def _load_model(self) -> bool:
        """Load the GGUF model synchronously."""
        if self._model is not None:
            return True
        if self._load_attempts >= 3:
            return False

        self._load_attempts += 1
        try:
            from llama_cpp import Llama
            logger.info(
                f"Loading local model: {self.model_path} "
                f"(n_ctx={self.n_ctx}, n_threads={self.n_threads})"
            )
            # Ignore SIGINT during model loading (llama-cpp can be slow)
            old_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                self._model = Llama(
                    model_path=self.model_path,
                    n_ctx=self.n_ctx,
                    n_threads=self.n_threads,
                    verbose=False,
                    n_batch=512,
                    n_ubatch=256,
                )
            finally:
                signal.signal(signal.SIGINT, old_handler)

            self._loaded = True
            logger.info("Local model loaded successfully")
            return True
        except ImportError:
            self._load_error = "llama-cpp-python not installed"
            logger.error("llama-cpp-python not installed. Install with: pip install llama-cpp-python")
            return False
        except Exception as e:
            self._load_error = str(e)
            logger.error(f"Failed to load local model: {e}")
            return False

    async def is_available(self) -> bool:
        if self._model is not None:
            return True
        return self._load_model()

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> AIResponse:
        async with self._lock:
            return await self._chat_inner(messages, model, temperature, max_tokens, **kwargs)

    async def _chat_inner(self, messages, model, temperature, max_tokens, **kwargs):
        if not self._load_model():
            return AIResponse(
                text="",
                model="local-qwen3-4b",
                provider="local",
                error=f"Local model not available: {self._load_error}",
            )

        actual_max = min(max_tokens, self.default_max_tokens or 2048)

        # Format messages into Qwen3 chat template
        prompt = ""
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                prompt += f"{QWEN3_SYSTEM_START}{content}{QWEN3_END}"
            elif role == "user":
                prompt += f"{QWEN3_USER_START}{content}{QWEN3_END}"
            elif role == "assistant":
                prompt += f"{QWEN3_ASSISTANT_START}{content}{QWEN3_END}"
        prompt += QWEN3_ASSISTANT_START

        try:
            start = time.time()
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._model(
                    prompt,
                    max_tokens=actual_max,
                    temperature=temperature,
                    top_p=0.9,
                    top_k=40,
                    repeat_penalty=1.1,
                    stop=["<|im_end|>", "<|im_start|>"],
                ),
            )
            elapsed = (time.time() - start) * 1000

            text = ""
            if hasattr(response, "choices") and response.choices:
                text = response.choices[0].text.strip()
            elif isinstance(response, dict) and "choices" in response:
                text = response["choices"][0].get("text", "").strip()
            else:
                text = str(response).strip()

            # Clean up Qwen3 think tags if present
            if " " in text and " " in text:
                think_end = text.find(" ") + len(" ")
                text = text[think_end:].strip()

            if text:
                return AIResponse(
                    text=text,
                    model=model or "local-qwen3-4b",
                    provider="local",
                    latency_ms=elapsed,
                )

            return AIResponse(
                text="",
                model=model or "local-qwen3-4b",
                provider="local",
                error="Empty response from local model",
                latency_ms=elapsed,
            )
        except Exception as e:
            logger.error(f"Local model inference error: {e}")
            return AIResponse(
                text="",
                model=model or "local-qwen3-4b",
                provider="local",
                error=f"Inference error: {e}",
            )

    def get_status(self) -> Dict:
        return {
            "status": "loaded" if self._model else "not_loaded",
            "model_path": self.model_path,
            "load_attempts": self._load_attempts,
            "load_error": self._load_error,
            "n_ctx": self.n_ctx,
            "n_threads": self.n_threads,
        }