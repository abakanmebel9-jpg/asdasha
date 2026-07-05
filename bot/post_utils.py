"""
Даша Post Utilities — smart truncation, text cleaning, validation, dedup.

Furniture-specific. News is in Russian (no translation needed).
Includes furniture keywords for relevance validation.
"""

import re
import hashlib
import logging
from typing import Tuple

logger = logging.getLogger("dasha.post_utils")

# ─── Smart Truncation ───────────────────────────────────────────────────────

def smart_truncate(text: str, limit: int, footer_len: int = 0) -> str:
    """Truncate text at natural boundary (paragraph > sentence > word). Appends …"""
    if not text:
        return ""
    effective_limit = limit - footer_len - 3
    if effective_limit < 50:
        effective_limit = 50
    if len(text) <= effective_limit:
        return text

    for i in range(effective_limit, max(effective_limit - 200, 0), -1):
        if i < len(text) and text[i:i+2] == "\n\n":
            return text[:i].rstrip() + "…"

    for i in range(effective_limit, max(effective_limit - 200, 0), -1):
        if i < len(text) and text[i] in ".!?\n" and (i + 1 >= len(text) or text[i+1] in " \n\t"):
            return text[:i+1].rstrip() + "…"

    for i in range(effective_limit, max(effective_limit - 100, 0), -1):
        if i < len(text) and text[i] == "\n":
            return text[:i].rstrip() + "…"

    for i in range(effective_limit, max(effective_limit - 50, 0), -1):
        if i < len(text) and text[i] == " ":
            return text[:i].rstrip() + "…"

    return text[:effective_limit].rstrip() + "…"


def smart_truncate_html(text: str, limit: int, footer_len: int = 0) -> str:
    """Smart truncation that preserves HTML tags (closes unclosed <a> tags)."""
    if not text:
        return ""
    truncated = smart_truncate(text, limit, footer_len)
    # Count unclosed <a> tags
    open_count = truncated.count("<a ")
    close_count = truncated.count("</a>")
    if open_count > close_count:
        truncated += "</a>" * (open_count - close_count)
    return truncated


# ─── Text Cleaning ──────────────────────────────────────────────────────────

_BANNED_OPENINGS = ["даша:", "редакция:", "привет", "здравствуй", "всем привет"]

_PROMPT_LEAKAGE_PATTERNS = [
    r"^напиши\s+пост", r"^напиши\s+комментар", r"^стиль\s*[(:]",
    r"^заголовок\s+новости", r"^краткое\s+содержание",
    r"^не\s+копируй", r"^не\s+добавляй", r"^не\s+начинай",
    r"^женский\s+род", r"^по-русски", r"^-{2,}\s*$",
]

_MARKDOWN_PATTERNS = [
    (r"\*\*(.+?)\*\*", r"\1"), (r"__(.+?)__", r"\1"),
    (r"\*(.+?)\*", r"\1"), (r"_(.+?)_", r"\1"),
    (r"`(.+?)`", r"\1"), (r"^#{1,6}\s+", ""), (r"^>\s+", ""),
    (r"^[-*]\s+", "• "),
]

_DISCLAIMER_PATTERNS = [
    r"как\s+искусственный\s+интеллект",
    r"я\s+не\s+(?:могу|имею\s+доступ)",
    r"у\s+меня\s+нет\s+доступа",
    r"обратите\s+внимание.*?(?:источник|оригинал)",
    r"(?:данный|этот)\s+(?:текст|материал)\s+(?:является|представляет)",
]


def clean_post_text(text: str, bot_name: str = "Даша") -> str:
    """Clean AI-generated text: strip markdown, prompt leakage, disclaimers, name prefixes."""
    if not text:
        return ""

    lines = text.strip().split("\n")
    cleaned_lines = []

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            if cleaned_lines and cleaned_lines[-1]:
                cleaned_lines.append("")
            continue

        is_leakage = any(re.match(p, line_stripped, re.IGNORECASE) for p in _PROMPT_LEAKAGE_PATTERNS)
        if is_leakage:
            continue

        is_disclaimer = any(re.search(p, line_stripped, re.IGNORECASE) for p in _DISCLAIMER_PATTERNS)
        if is_disclaimer:
            continue

        for pattern, replacement in _MARKDOWN_PATTERNS:
            line_stripped = re.sub(pattern, replacement, line_stripped)
        cleaned_lines.append(line_stripped)

    text = "\n".join(cleaned_lines).strip()

    for opening in _BANNED_OPENINGS:
        if text.lower().startswith(opening):
            text = text[len(opening):].lstrip(" ,!.—-:")
            break

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,.;:!?])([^\s\d\n.])", r"\1 \2", text)
    return text.strip()


# ─── Validation ─────────────────────────────────────────────────────────────

_POLITICS_KEYWORDS = [
    "путин", "кремл", "госдум", "санкци", "сво", "мобилиз", "война",
    "зеленск", "байден", "трамп", "выборы", "парламент", "ракетн", "обстрел",
]

_NSFW_KEYWORDS = ["порн", "эрот", "секс", "18+", "nsfw"]

_FURNITURE_KEYWORDS = [
    "мебел", "дизайн", "интерьер", "кухн", "шкаф", "стол", "стул", "кресло",
    "диван", "кровать", "тумб", "комод", "полк", "фасад", "массив", "лдсп",
    "мдф", "дсп", "керамогранит", "стекло", "фурнитур", "ручк", "петель",
    "выдвижн", "распашн", "купе", "углов", "остров", "барн",
    "скандинавск", "лофт", "минимализм", "прованс", "классик", "хай-тек",
    "современ", "эко-стил", "ар-деко", "модерн",
    "цвет", "оттенок", "текстур", "фактур", "паттерн", "рисунок",
    "ремонт", "отделк", "потолок", "стен", "пол", "обои", "плитк",
    "свет", "люстр", "бра", "торшер", "подсветк",
    "абакан", "хакас", "сибирь",
    "проект", "заказ", "индивидуальн", "на заказ", "производств",
    "abakan", "mebel", "furniture", "interior", "design", "kitchen",
]


def validate_post_text(text: str, require_keywords: list = None) -> Tuple[bool, str]:
    """Validate post text. Returns (is_valid, reason)."""
    if not text or len(text) < 50:
        return False, "too_short"
    t = text.lower()

    for kw in _POLITICS_KEYWORDS:
        if kw in t:
            return False, f"politics:{kw}"
    for kw in _NSFW_KEYWORDS:
        if kw in t:
            return False, f"nsfw:{kw}"

    keywords = require_keywords or _FURNITURE_KEYWORDS
    if not any(kw.lower() in t for kw in keywords):
        return False, "not_furniture_relevant"
    return True, "ok"


def enforce_no_meetings(text: str) -> str:
    """Remove booking/meeting proposals (legal safety)."""
    # Remove patterns like "позвоните нам", "приходите к нам", "запишитесь"
    patterns = [
        r"(?:позвоните|приходите|заходите)\s+(?:нам|к нам|по адресу)[^.]*\.",
        r"запишитесь\s+(?:на|по)[^.]*\.",
        r"наш\s+адрес[^.]*\.",
        r"ждем\s+вас\s+по\s+адресу[^.]*\.",
    ]
    for p in patterns:
        text = re.sub(p, "", text, flags=re.IGNORECASE)
    return text


# ─── Image Validation ───────────────────────────────────────────────────────

_IMAGE_MAGIC_BYTES = {
    b"\xff\xd8\xff": "jpeg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"RIFF": "webp",
    b"GIF8": "gif",
}


def validate_image(content: bytes) -> bool:
    if not content or len(content) < 1024:
        return False
    for magic, fmt in _IMAGE_MAGIC_BYTES.items():
        if content[:len(magic)] == magic:
            if fmt == "gif":
                return False
            if fmt == "webp" and content[8:12] != b"WEBP":
                return False
            return True
    return False


# ─── Deduplication ──────────────────────────────────────────────────────────

def title_fingerprint(title: str) -> str:
    t = re.sub(r"[^\w\sа-яё]", "", (title or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    words = [w for w in t.split() if len(w) > 2][:5]
    return " ".join(words)


def text_fingerprint(text: str) -> str:
    t = re.sub(r"[^\w\sа-яё]", "", (text or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    return hashlib.md5(t.encode("utf-8")).hexdigest()


def url_normalize(url: str) -> str:
    if not url:
        return ""
    u = re.sub(r"^https?://", "", url.lower())
    u = re.sub(r"^www\.", "", u)
    u = u.split("?")[0].split("#")[0]
    return u.rstrip("/")


# ─── Date Context ───────────────────────────────────────────────────────────

from datetime import datetime, timezone, timedelta
_MOSCOW_TZ = timezone(timedelta(hours=3))

def date_context() -> str:
    now = datetime.now(_MOSCOW_TZ)
    months = ["января","февраля","марта","апреля","мая","июня",
              "июля","августа","сентября","октября","ноября","декабря"]
    weekdays = ["понедельник","вторник","среда","четверг","пятница","суббота","воскресенье"]
    return f"сегодня {now.day} {months[now.month-1]} {now.year} года, {weekdays[now.weekday()]}"


UNIQUIFICATION_RULES = """
УНИКАЛИЗАЦИЯ (обязательно):
1. Не копируй заголовок или текст новости — пиши СВОЙ комментарий
2. Используй другие слова, другую структуру предложений
3. Добавь личное мнение/анализ от лица дизайнера
4. Меняй порядок мыслей, добавляй контекст
5. Не используй прямые цитаты из новости
6. Добавляй детали о материалах (массив, ЛДСП, МДФ) и стилях
7. Меняй угол подачи (практичность, эстетика, тренды, сравнение)"""
