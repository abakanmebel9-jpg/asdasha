"""Local LLM Provider — RuadaptQwen3-4B via llama-cpp-python (GGUF).

CPU-only inference for Dasha Bot. Same model as Asya bot.
- RuadaptQwen3-4B-Instruct Q4_K_M (~2.5GB)
- Russian-optimized tokenizer
- Chat template formatting (Qwen3 ChatML)
- Model auto-download from HuggingFace
- Context window management with progressive truncation
- Circuit breaker for error resilience
- Thread-safe generation (prevents llama-cpp segfaults)
"""

import asyncio
import logging
import os
import re
import signal
import time
from typing import Optional, List, Dict

from ai.providers.base import BaseAIProvider, AIResponse
from bot.config import config

logger = logging.getLogger("dasha.ai.local")

# ── Qwen3 chat template ──
QWEN3_SYSTEM_START = "<|im_start|>system\n"
QWEN3_USER_START = "<|im_start|>user\n"
QWEN3_ASSISTANT_START = "<|im_start|>assistant\n"
QWEN3_END = "<|im_end|>\n"

# Russian text tokenization ratio for Qwen3 BPE tokenizer.
# Russian tokenizes at ~1.2-1.5 chars/token, using conservative 1.3.
CHARS_PER_TOKEN = 1.3


class LocalProvider(BaseAIProvider):
    """Local LLM provider using llama-cpp-python for GGUF models.

    Supports RuadaptQwen3-4B-Instruct with ChatML template formatting.
    CPU-only, designed for VPS deployment.
    """

    name = "local"

    def __init__(self):
        super().__init__(
            name="local",
            api_key="",
            base_url="",
        )
        self._llm = None
        self._model_loaded = False
        self._model_path = config.MODEL_PATH
        self._n_ctx = config.MODEL_N_CTX
        self._n_threads = config.MODEL_N_THREADS
        self._max_tokens = config.MODEL_MAX_TOKENS
        self._history_limit = getattr(config, "MODEL_HISTORY_LIMIT", 20)
        self._total_requests = 0
        self._total_errors = 0
        self._last_error_time = 0.0
        self._consecutive_errors = 0
        self._available = False

        # CRITICAL: Mutex lock to prevent concurrent llama-cpp access.
        # llama-cpp-python is NOT thread-safe — simultaneous calls from
        # different asyncio tasks cause GGML_ASSERT(buffer) failures
        # and segmentation faults (exit code 139).
        self._generation_lock = asyncio.Lock()

        # CRITICAL: Track if a generation is running in the thread pool.
        # Even after asyncio task cancellation, the thread pool continues running.
        # Setting this flag prevents new generations from starting while one is
        # still executing in the background, which would cause segfaults.
        self._generating = False
        self._generation_done = asyncio.Event()
        self._generation_done.set()  # Initially "done" (no generation running)

    # ── Model download ──

    def _download_model(self) -> bool:
        """Download the GGUF model from HuggingFace if auto-download is enabled.

        Uses MODEL_DOWNLOAD_URL from config. Supports authenticated downloads
        via HF_TOKEN environment variable (required for gated models).
        Returns True if download succeeded or file already exists.
        """
        if not self._model_path:
            logger.warning("Local model: MODEL_PATH not set, cannot download")
            return False

        # Already exists
        if os.path.exists(self._model_path):
            size_mb = os.path.getsize(self._model_path) / (1024 * 1024)
            logger.info(f"Model file already exists: {self._model_path} ({size_mb:.1f} MB)")
            return True

        if not getattr(config, "MODEL_AUTO_DOWNLOAD", False):
            logger.info("Auto-download disabled (MODEL_AUTO_DOWNLOAD=false)")
            return False

        download_url = getattr(config, "MODEL_DOWNLOAD_URL", "")
        hf_token = config.HF_TOKEN or os.getenv("HF_TOKEN", "")

        try:
            # Create models directory
            model_dir = os.path.dirname(self._model_path)
            if model_dir:
                os.makedirs(model_dir, exist_ok=True)

            # Method 1: Use huggingface_hub if available and HF_TOKEN is set
            if hf_token:
                try:
                    from huggingface_hub import hf_hub_download
                    logger.info("Downloading model via huggingface_hub (authenticated)...")

                    # Parse repo and filename from URL
                    # URL format: https://huggingface.co/{repo_id}/resolve/main/{filename}
                    if "huggingface.co/" in download_url:
                        parts = download_url.split("huggingface.co/")[1]
                        path_parts = parts.split("/resolve/")
                        if len(path_parts) >= 2:
                            repo_id = path_parts[0]
                            filename = path_parts[1].split("/", 1)[-1]

                            start_time = time.time()
                            downloaded_path = hf_hub_download(
                                repo_id=repo_id,
                                filename=filename,
                                token=hf_token,
                                local_dir=model_dir or ".",
                            )
                            elapsed = time.time() - start_time

                            # hf_hub_download may save to a different path — move if needed
                            if downloaded_path != self._model_path and os.path.exists(downloaded_path):
                                import shutil
                                shutil.move(downloaded_path, self._model_path)

                            if os.path.exists(self._model_path):
                                size_mb = os.path.getsize(self._model_path) / (1024 * 1024)
                                if size_mb > 100:
                                    logger.info(f"Model downloaded via HF hub: {size_mb:.1f} MB in {elapsed:.1f}s")
                                    return True

                    logger.warning("Could not parse HuggingFace URL, falling back to direct download")
                except ImportError:
                    logger.info("huggingface_hub not installed, falling back to direct download")
                except Exception as e:
                    logger.warning(f"HF hub download failed: {e}, falling back to direct download")

            # Method 2: Direct download via urllib
            if not download_url:
                logger.warning("MODEL_DOWNLOAD_URL not set, cannot download")
                return False

            import urllib.request

            logger.info(f"Downloading model from {download_url}")
            logger.info(f"Target: {self._model_path}")

            # Add HF token as authorization header if available
            if hf_token:
                logger.info("Using HF_TOKEN for authenticated download")
                opener = urllib.request.build_opener()
                request = urllib.request.Request(download_url)
                request.add_header("Authorization", f"Bearer {hf_token}")
                response = opener.open(request)
                with open(self._model_path, "wb") as f:
                    total_size = int(response.headers.get("content-length", 0))
                    downloaded = 0
                    block_size = 8192
                    last_report = 0
                    while True:
                        chunk = response.read(block_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if total_size > 0 and (
                            downloaded * 100 // total_size > last_report + 10
                            or downloaded - last_report * total_size // 100 > 256 * 1024 * 1024
                        ):
                            pct = downloaded * 100 // total_size
                            logger.info(
                                f"  Download: {pct}% ({downloaded // 1048576}/{total_size // 1048576} MB)"
                            )
                            last_report = pct
            else:
                # No token — try unauthenticated download
                def report_progress(block_num, block_size, total_size):
                    downloaded = block_num * block_size
                    if total_size > 0:
                        percent = min(100, downloaded * 100 / total_size)
                        if block_num % 50 == 0:
                            logger.info(
                                f"  Download: {percent:.0f}% ({downloaded // 1048576}/{total_size // 1048576} MB)"
                            )

                start_time = time.time()
                urllib.request.urlretrieve(download_url, self._model_path, reporthook=report_progress)

            # Verify download
            if not os.path.exists(self._model_path):
                logger.error("Download completed but file not found!")
                return False

            size_mb = os.path.getsize(self._model_path) / (1024 * 1024)
            if size_mb < 100:  # Sanity check — model should be ~2.5GB
                logger.error(f"Downloaded file too small ({size_mb:.1f} MB), likely corrupted. Removing.")
                os.remove(self._model_path)
                return False

            logger.info(f"Model downloaded: {size_mb:.1f} MB")
            return True

        except Exception as e:
            logger.error(f"Failed to download model: {e}")
            # Clean up partial download
            if os.path.exists(self._model_path):
                try:
                    os.remove(self._model_path)
                except Exception:
                    pass
            return False

    # ── Model loading ──

    def _load_model(self) -> bool:
        """Load the GGUF model using llama-cpp-python.

        Automatically downloads model if file not found and MODEL_AUTO_DOWNLOAD=true.
        """
        if self._model_loaded and self._llm is not None:
            return True

        if not config.ENABLE_LOCAL_MODEL:
            logger.info("Local model DISABLED by config (ENABLE_LOCAL_MODEL=false)")
            return False

        if not self._model_path:
            logger.warning("Local model: MODEL_PATH not set")
            return False

        # Auto-download model if not found
        if not os.path.exists(self._model_path):
            logger.info(f"Model file not found at {self._model_path}, attempting auto-download...")
            if not self._download_model():
                logger.warning("Local model unavailable: file not found and download failed")
                return False

        try:
            from llama_cpp import Llama

            logger.info(
                f"Loading local model: {self._model_path} "
                f"(n_ctx={self._n_ctx}, n_threads={self._n_threads})"
            )

            start_time = time.time()

            # n_batch: Larger = faster prompt processing, more peak memory.
            # 1024 is good balance for Q4_K_M model on VPS.
            n_batch = 1024

            # Ignore SIGINT during model loading (llama-cpp can be slow)
            old_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                self._llm = Llama(
                    model_path=self._model_path,
                    n_ctx=self._n_ctx,
                    n_batch=n_batch,
                    n_threads=self._n_threads,
                    n_threads_batch=self._n_threads,  # Parallel prompt processing
                    verbose=False,
                    use_mlock=False,  # Don't lock memory — saves RAM
                    use_mmap=True,    # Memory-mapped file — faster loading
                    seed=42,          # Deterministic by default, temperature handles randomness
                )
            finally:
                signal.signal(signal.SIGINT, old_handler)

            elapsed = time.time() - start_time
            self._model_loaded = True
            self._available = True

            logger.info(
                f"Local model loaded in {elapsed:.1f}s "
                f"(RuadaptQwen3-4B Q4_K_M, ctx={self._n_ctx}, threads={self._n_threads})"
            )
            return True

        except ImportError:
            logger.error(
                "llama-cpp-python not installed! "
                "Install with: pip install llama-cpp-python"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to load local model: {e}")
            self._llm = None
            self._model_loaded = False
            self._available = False
            return False

    # ── ChatML formatting ──

    def _format_messages_chatml(self, messages: List[Dict[str, str]]) -> str:
        """Format messages using ChatML template (Qwen3 format).

        Limits conversation history to MODEL_HISTORY_LIMIT exchanges.
        """
        # Limit history to reduce context length
        if len(messages) > self._history_limit + 1:  # +1 for system prompt
            # Keep system prompt + last N messages
            system_msgs = [m for m in messages if m.get("role") == "system"]
            non_system = [m for m in messages if m.get("role") != "system"]
            limited_non_system = non_system[-self._history_limit:]
            messages = system_msgs + limited_non_system

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

        # Add assistant prefix for generation
        prompt += f"{QWEN3_ASSISTANT_START}"

        return prompt

    # ── Context window management ──

    def _truncate_prompt_if_needed(
        self, messages: List[Dict[str, str]], prompt: str, max_tokens: int
    ) -> str:
        """Progressively truncate prompt to fit within context window.

        Uses CHARS_PER_TOKEN=1.3 for Russian text estimation.
        Truncation strategy:
          1. Keep system + last 2 messages
          2. Keep system + last 1 message
          3. Keep system only
          4. Truncate system message to fit
          5. Hard-truncate raw prompt (last resort)
        """
        max_input_tokens = self._n_ctx - max_tokens - 64  # 64 token safety margin
        estimated_tokens = int(len(prompt) / CHARS_PER_TOKEN)

        if estimated_tokens <= max_input_tokens:
            return prompt

        logger.warning(
            f"Prompt too long ({estimated_tokens} est. tokens, "
            f"max_input={max_input_tokens}, ctx={self._n_ctx}), "
            f"truncating history"
        )

        # Progressive truncation: try keeping fewer messages each time
        for keep_count in [2, 1, 0]:
            if keep_count == 0:
                truncated_messages = [messages[0]]  # System only
            else:
                truncated_messages = [messages[0]] + messages[-keep_count:]
            prompt = self._format_messages_chatml(truncated_messages)
            new_est = int(len(prompt) / CHARS_PER_TOKEN)
            if new_est <= max_input_tokens:
                logger.info(f"Truncated to {keep_count} history messages ({new_est} est. tokens)")
                break

        # Final safety check — if even system-only prompt is too long,
        # truncate the system message itself to fit
        final_est = int(len(prompt) / CHARS_PER_TOKEN)
        if final_est > max_input_tokens:
            max_system_chars = int(max_input_tokens * CHARS_PER_TOKEN)
            system_content = messages[0].get("content", "")[:max_system_chars]
            truncated_messages = [{"role": "system", "content": system_content}]
            prompt = self._format_messages_chatml(truncated_messages)
            logger.warning(
                f"System prompt truncated to {max_system_chars} chars "
                f"({int(len(prompt) / CHARS_PER_TOKEN)} est. tokens) to fit context"
            )

        # ABSOLUTE SAFETY: After all truncation, if estimated tokens still exceed
        # context window, hard-truncate the raw prompt string to guaranteed max chars.
        max_prompt_chars = int(max_input_tokens * CHARS_PER_TOKEN)
        if len(prompt) > max_prompt_chars:
            prompt = prompt[:max_prompt_chars]
            # Ensure we don't cut in the middle of a ChatML tag
            last_end = prompt.rfind(QWEN3_END)
            if last_end > len(prompt) // 2:
                prompt = prompt[:last_end + len(QWEN3_END)]
            # Add assistant prefix for generation
            prompt += f"{QWEN3_ASSISTANT_START}"
            logger.warning(
                f"HARD TRUNCATION: prompt cut to {len(prompt)} chars "
                f"({int(len(prompt) / CHARS_PER_TOKEN)} est. tokens)"
            )

        return prompt

    # ── Main chat method ──

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 0,
        **kwargs,
    ) -> AIResponse:
        """Generate a chat completion using the local model.

        Uses ChatML formatting with progressive context truncation.
        Dasha's model DOES produce thinking blocks — cleaned automatically.
        Thread-safe: prevents concurrent llama-cpp calls that cause segfaults.
        """
        if not self._load_model():
            return AIResponse(
                text="",
                model="local-qwen3-4b",
                provider=self.name,
                error="Local model not available (not loaded or not enabled)",
                error_message="Local model not available (not loaded or not enabled)",
            )

        # Circuit breaker: if too many consecutive errors, pause briefly
        if self._consecutive_errors >= 5:
            elapsed_since_error = time.time() - self._last_error_time
            if elapsed_since_error < 120:  # 2-minute cooldown
                return AIResponse(
                    text="",
                    model="local-qwen3-4b",
                    provider=self.name,
                    error=f"Local model in cooldown ({self._consecutive_errors} consecutive errors)",
                    error_message=f"Local model in cooldown ({self._consecutive_errors} consecutive errors)",
                )
            else:
                self._consecutive_errors = 0  # Reset after cooldown

        max_tokens = max_tokens or self._max_tokens

        try:
            # Format prompt using ChatML
            prompt = self._format_messages_chatml(messages)

            # Truncate if needed to fit context window
            prompt = self._truncate_prompt_if_needed(messages, prompt, max_tokens)

            start_time = time.time()

            # CRITICAL: Use async lock + _generating flag to serialize llama-cpp-python calls.
            # Without this, concurrent calls in the thread pool cause segfaults.
            #
            # The _generating flag is the KEY addition. When a generation is
            # cancelled at the asyncio level, the thread pool executor CONTINUES running.
            # The lock is released when the except clause runs, but the thread is still going.
            # Without the flag, a new request could start a SECOND generation
            # while the first is still running → segfault.

            # If a generation is still running in the thread pool, wait for it.
            if self._generating:
                logger.warning("Local model: previous generation still running in thread pool, waiting...")
                try:
                    # 15s wait — if local model is busy, fail fast
                    await asyncio.wait_for(self._generation_done.wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    logger.error("Local model: timed out waiting for previous generation (15s)")
                    self._consecutive_errors += 1
                    self._last_error_time = time.time()
                    return AIResponse(
                        text="",
                        model="local-qwen3-4b",
                        provider=self.name,
                        error="Local model busy (previous generation still running)",
                        error_message="Local model busy (previous generation still running)",
                    )

            async with self._generation_lock:
                self._generating = True
                self._generation_done.clear()

                # Run inference in thread pool to avoid blocking event loop.
                # CRITICAL: We NEVER let CancelledError interrupt llama-cpp.
                # The generation runs to completion in the thread pool no matter what.
                loop = asyncio.get_running_loop()
                fut = loop.run_in_executor(
                    None,
                    self._generate,
                    prompt,
                    max_tokens,
                    temperature,
                )

                # Wait for the thread to complete — CANNOT be cancelled.
                # This ensures llama-cpp always finishes cleanly.
                cancelled = False
                while True:
                    try:
                        result = await asyncio.shield(fut)
                        break
                    except asyncio.CancelledError:
                        # Caller cancelled — but we MUST wait for the C thread
                        # to finish before allowing any new generation.
                        if not cancelled:
                            cancelled = True
                            logger.warning(
                                "Local generation was cancelled — waiting for "
                                "thread to complete safely (preventing segfault)"
                            )
                        # Poll the future with short sleeps
                        if fut.done():
                            result = fut.result()
                            break
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        logger.error(f"Local generation error: {e}")
                        self._consecutive_errors += 1
                        self._last_error_time = time.time()
                        self._generating = False
                        self._generation_done.set()
                        if cancelled:
                            raise asyncio.CancelledError()
                        raise

                # Mark generation as done AFTER the thread pool finishes
                self._generating = False
                self._generation_done.set()

                # If we were cancelled but completed safely, return error
                if cancelled:
                    logger.info("Local generation completed safely after cancellation — discarding result")
                    self._consecutive_errors += 1
                    self._last_error_time = time.time()
                    return AIResponse(
                        text="",
                        model="local-qwen3-4b",
                        provider=self.name,
                        error="Generation cancelled (completed safely, result discarded)",
                        error_message="Generation cancelled (completed safely, result discarded)",
                    )

            elapsed_ms = (time.time() - start_time) * 1000

            text = result

            if not text or len(text.strip()) < 3:
                self._consecutive_errors += 1
                self._last_error_time = time.time()
                return AIResponse(
                    text="",
                    model="local-qwen3-4b",
                    provider="local",
                    error="Empty or too short response from local model",
                    error_message="Empty or too short response from local model",
                    latency_ms=elapsed_ms,
                )

            # Clean response (strip thinking blocks, artifacts)
            text = self._clean_response(text)

            if not text:
                self._consecutive_errors += 1
                self._last_error_time = time.time()
                return AIResponse(
                    text="",
                    model="local-qwen3-4b",
                    provider="local",
                    error="Model returned empty answer after cleanup (only think block?)",
                    error_message="Model returned empty answer after cleanup (only think block?)",
                    latency_ms=elapsed_ms,
                )

            # Reset error tracking on success
            self._consecutive_errors = 0
            self._total_requests += 1

            logger.info(
                f"Local model response: {len(text)} chars, "
                f"{elapsed_ms:.0f}ms, tokens={max_tokens}"
            )

            return AIResponse(
                text=text,
                model="local-qwen3-4b",
                provider="local",
                latency_ms=elapsed_ms,
            )

        except Exception as e:
            self._consecutive_errors += 1
            self._last_error_time = time.time()
            self._total_errors += 1
            logger.error(f"Local model error: {e}")
            return AIResponse(
                text="",
                model="local-qwen3-4b",
                provider="local",
                error=f"Inference error: {e}",
                error_message=f"Inference error: {e}",
            )

    # ── Synchronous generation (runs in thread pool) ──

    def _generate(self, prompt: str, max_tokens: int, temperature: float) -> str:
        """Synchronous generation call (runs in thread pool)."""
        result = self._llm(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=0.9,
            top_k=40,
            repeat_penalty=1.1,
            stop=["<|im_end|>", "https://", "<|im_start|>"],
        )

        # Log generation stats for monitoring
        if isinstance(result, dict):
            usage = result.get("usage", {})
            gen_tokens = usage.get("completion_tokens", 0)
            prompt_tokens = usage.get("prompt_tokens", 0)
            if gen_tokens > 0:
                logger.info(
                    f"Local model tokens: prompt={prompt_tokens}, "
                    f"generated={gen_tokens}, max={max_tokens}"
                )

        # Extract text from result
        if isinstance(result, dict):
            choices = result.get("choices", [])
            if choices:
                text = choices[0].get("text", "")
                return text
        elif isinstance(result, str):
            return result

        return ""

    # ── Response cleanup ──

    def _clean_response(self, text: str) -> str:
        """Clean local model response artifacts.

        Dasha's model DOES produce thinking blocks (<tool_call>/给 tags),
        so we strip them aggressively.
        """
        if not text:
            return ""

        # Remove XML-style think tags (e.g. <think>...</think>)
        text = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<thinking\b[^>]*>.*?</thinking\s*>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"</?think[^>]*>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"</?thinking[^>]*>", "", text, flags=re.IGNORECASE)

        # Remove Unicode thinking blocks: ⁣💭...给 (Qwen3 thinking delimiters)
        # Dasha's model sometimes uses these instead of XML tags
        if "⁣💭" in text or "给" in text:
            think_start = text.find("⁣💭")
            think_end = text.find("给")
            if think_start != -1:
                if think_end != -1 and think_end > think_start:
                    # Remove the entire block including the delimiters
                    text = (text[:think_start] + text[think_end + len("给"):]).strip()
                else:
                    # No closing tag — model didn't finish thinking
                    text = text[:think_start].strip()

        # Remove /no_think and /think prefixes (in case model echoes them)
        text = re.sub(r"^/no_think\s*", "", text)
        text = re.sub(r"^/think\s*", "", text)

        # Remove ChatML artifacts
        text = text.replace("<|im_end|>", "")
        text = text.replace("⁣", "")
        text = text.replace("<|im_start|>", "")

        # Remove common AI name prefixes
        for prefix in ["Даша:", "Даша :", "Dasha:", "Assistant:", "Ответ Даши:",
                        "Даша,", "Dasha,", "ДАША:", "даша:"]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        # Strip surrounding quotes (single, double, smart, guillemets)
        for open_q, close_q in [('"', '"'), ('"', '"'), ('«', '»'),
                                ('"', '"'), ("'", "'"), ('"', '"')]:
            if text.startswith(open_q) and text.endswith(close_q) and len(text) > 4:
                text = text[1:-1].strip()

        # Remove inline quotes wrapping entire first sentence
        text = re.sub(r'^["«][\s]?(.*?)[\s]?["»]', r'\1', text)

        # Strip markdown bold/italic (model sometimes wraps entire response)
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\*([^*]+)\*", r"\1", text)

        # Remove excessive newline+quote patterns (model quoting its own text)
        text = re.sub(r'\n[""][^""]*[""]\n', '\n', text)
        text = re.sub(r'\n["«][^"»]*["»]\n', '\n', text)

        # Clean up excessive whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        return text

    # ── Availability ──

    async def is_available(self) -> bool:
        """Check if local model is available."""
        if not config.ENABLE_LOCAL_MODEL:
            return False

        if self._consecutive_errors >= 5:
            elapsed = time.time() - self._last_error_time
            if elapsed < 120:
                return False

        # Try to load if not loaded
        if not self._model_loaded:
            return self._load_model()

        return self._model_loaded and self._llm is not None

    # ── Status ──

    def get_status(self) -> Dict:
        """Get detailed status dict for monitoring."""
        status = {
            "status": "loaded" if self._model_loaded else "not_loaded",
            "model_path": self._model_path,
            "n_ctx": self._n_ctx,
            "n_threads": self._n_threads,
            "max_tokens": self._max_tokens,
            "history_limit": self._history_limit,
            "total_requests": self._total_requests,
            "total_errors": self._total_errors,
            "consecutive_errors": self._consecutive_errors,
            "generating": self._generating,
        }

        if self._consecutive_errors > 0:
            elapsed = time.time() - self._last_error_time
            status["last_error_seconds_ago"] = round(elapsed, 1)
            if self._consecutive_errors >= 5 and elapsed < 120:
                status["circuit_breaker"] = "OPEN (cooldown)"
            else:
                status["circuit_breaker"] = "CLOSED"

        if not config.ENABLE_LOCAL_MODEL:
            status["status"] = "DISABLED"

        return status

    # ── Cleanup ──

    def unload(self) -> None:
        """Unload model to free memory."""
        if self._llm is not None:
            del self._llm
            self._llm = None
            self._model_loaded = False
            self._available = False
            logger.info("Local model unloaded (memory freed)")