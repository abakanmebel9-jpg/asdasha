"""
Dasha Bot Optimizations v1.0 — Utility module.

Utilities for performance, stability, and UX.
"""

import hashlib
import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("dasha.optimizations")


# ════════════════════════════════════════════════════════════════════════════
# Request Dedup Cache — prevents duplicate AI calls for same message
# ════════════════════════════════════════════════════════════════════════════

_DEDUP_CACHE: "OrderedDict[str, Tuple[float, str]]" = OrderedDict()
_DEDUP_TTL = 30  # 30 seconds
_DEDUP_MAX = 128
_dedup_lock = threading.Lock()


def dedup_check(user_id: int, message: str) -> Optional[str]:
    """Check if this exact message was recently processed. Returns cached response or None."""
    key = f"{user_id}:{message.strip().lower()[:100]}"
    now = time.time()

    with _dedup_lock:
        if key in _DEDUP_CACHE:
            cached_at, cached_response = _DEDUP_CACHE[key]
            if now - cached_at < _DEDUP_TTL:
                return cached_response
            else:
                del _DEDUP_CACHE[key]
    return None


def dedup_store(user_id: int, message: str, response: str) -> None:
    """Store a response for dedup cache."""
    key = f"{user_id}:{message.strip().lower()[:100]}"
    now = time.time()

    with _dedup_lock:
        _DEDUP_CACHE[key] = (now, response)
        if len(_DEDUP_CACHE) > _DEDUP_MAX:
            _DEDUP_CACHE.popitem(last=False)


# ════════════════════════════════════════════════════════════════════════════
# URL Detection
# ════════════════════════════════════════════════════════════════════════════

_URL_PATTERN = re.compile(r'https?://[^\s<>"\']+')


def find_urls(text: str) -> list:
    return _URL_PATTERN.findall(text)


# ════════════════════════════════════════════════════════════════════════════
# Text utilities
# ════════════════════════════════════════════════════════════════════════════

def adaptive_max_chars(chat_type: str) -> int:
    """Return appropriate max response length based on chat type."""
    if chat_type == "private":
        return 4000
    elif chat_type == "group":
        return 2000
    elif chat_type == "supergroup":
        return 1500
    return 1000


def chat_type_context(message) -> str:
    """Return context string based on chat type."""
    ct = message.chat.type
    if ct == "private":
        return ""
    chat_name = message.chat.title or message.chat.first_name or "группа"
    return f"\n[Контекст: ты в чате '{chat_name}', отвечай кратко и по делу]"