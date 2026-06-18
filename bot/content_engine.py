"""
Content Engine v1.0 — Smart content selection for Dasha Bot.
Picks the best news items and provides AI generation context.
"""

import logging
import re
import random
import time
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger("dasha.content_engine")


# Topic registry — prevents covering same topic twice
_topic_registry: Dict[str, float] = {}
_TOPIC_EXPIRE_HOURS = 48


def _register_topic(topic: str) -> None:
    _topic_registry[topic.lower()] = time.time()


def _is_topic_covered(topic: str) -> bool:
    topic_lower = topic.lower()
    now = time.time()
    expire = _TOPIC_EXPIRE_HOURS * 3600

    # Exact match
    if topic_lower in _topic_registry:
        if now - _topic_registry[topic_lower] < expire:
            return True
        del _topic_registry[topic_lower]

    # Partial match (3+ words)
    words = set(topic_lower.split())
    for existing, ts in _topic_registry.items():
        if now - ts < expire:
            existing_words = set(existing.split())
            overlap = words & existing_words
            if len(overlap) >= 3:
                return True

    return False


def get_best_news_item(items: List[Dict], max_candidates: int = 5) -> Optional[Dict]:
    """Pick the best news item for posting. Filters out already-covered topics."""
    candidates = []
    for item in items:
        title = item.get("title", "")
        if _is_topic_covered(title):
            continue

        # Score based on length and keywords
        score = 0
        if len(title) > 30:
            score += 1
        if len(title) > 60:
            score += 1
        if item.get("image_urls"):
            score += 2  # Prefer items with images
        if any(kw in title.lower() for kw in [
            "дизайн", "интерьер", "мебель", "кухн", "тренд", "стил",
            "design", "furniture", "interior", "kitchen", "modern",
            "массив", "дерев", "фурнитур", "шкаф", "диван", "кроват",
        ]):
            score += 3  # Highly relevant topics

        candidates.append((item, score))

    if not candidates:
        return None

    # Sort by score, pick from top candidates
    candidates.sort(key=lambda x: x[1], reverse=True)
    top = candidates[:max_candidates]

    item, _ = random.choice(top)

    # Register topic
    _register_topic(item.get("title", ""))

    return item


def get_date_context() -> str:
    """Get current date context for AI prompts."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    months = {
        1: "января", 2: "февраля", 3: "марта", 4: "апреля",
        5: "мая", 6: "июня", 7: "июля", 8: "августа",
        9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
    }
    day = now.day
    month = months.get(now.month, "")
    year = now.year
    return f"{day} {month} {year}"