"""
Даша Scheduler Control — управление шедулером канала без циклических импортов.

Позволяет админ-командой /post_now разбудить шедулер: пост уйдёт немедленно,
не дожидаясь планового интервала (27–36 мин).
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("dasha.scheduler_control")

_MOSCOW_TZ = timezone(timedelta(hours=3))

_force_event: asyncio.Event = asyncio.Event()
_last_force: float = 0.0


def request_force_post() -> bool:
    """Запрашивает немедленный пост. False — если запрос был менее 60 сек назад (антифлуд)."""
    import time
    global _last_force
    now = time.time()
    if now - _last_force < 60:
        return False
    _last_force = now
    _force_event.set()
    logger.info("Force post requested by admin")
    return True


def consume_force_signal():
    """Сбрасывает сигнал (вызывает шедулер после пробуждения)."""
    if _force_event.is_set():
        _force_event.clear()
        return True
    return False


async def wait_interval_or_force(timeout_seconds: float):
    """Ждёт таймаут ИЛИ сигнал форс-поста (что раньше)."""
    try:
        await asyncio.wait_for(_force_event.wait(), timeout=timeout_seconds)
        _force_event.clear()
        return True  # форс
    except asyncio.TimeoutError:
        return False  # плановый интервал


def now_msk_str() -> str:
    return datetime.now(_MOSCOW_TZ).strftime("%H:%M")
