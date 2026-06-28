"""
News Engine v1.0 — Furniture News Fetcher for Dasha Bot.
Fetches pre-parsed furniture news from abakanmebel9-jpg/par repository.

ARCHITECTURE:
  External source: abakanmebel9-jpg/par/data/furniture-news.json (GitHub Raw)
  → This module fetches JSON → normalizes → stores in DB
  → Channel manager picks best items and generates posts
"""

import httpx
import json
import logging
import re
import asyncio
from html import unescape as html_unescape
from typing import List, Dict, Optional
from datetime import datetime
from collections import OrderedDict

from bot.config import config
from bot.database import add_news_item, is_duplicate_post

logger = logging.getLogger("dasha.news")

# Single news source — furniture news from par repo
NEWS_JSON_URL = "https://raw.githubusercontent.com/abakanmebel9-jpg/par/main/data/furniture-news.json"
FETCH_TIMEOUT = 60.0
FETCH_RETRY_DELAY = 5.0
MAX_NEWS_PER_CYCLE = 2000

# Fingerprint dedup
_recent_fingerprints: OrderedDict = OrderedDict()


def _compute_fingerprint(title: str) -> str:
    cleaned = re.sub(
        r'\b(в|на|с|о|у|по|из|за|от|до|к|не|и|но|а|что|как|это|тот|этот|для|при|через|между|после|перед|без|под|над|об|со|the|a|an|is|are|was|were|in|on|at|to|for|of|with|by|from|and|or|but|not|no)\b',
        '', title.lower()
    )
    cleaned = re.sub(r'[^a-zа-яё0-9]', ' ', cleaned)
    words = cleaned.split()
    return ' '.join(words[:5])


def _fingerprint_matches_existing(fingerprint: str) -> bool:
    fp_words = fingerprint.split()[:5]
    if len(fp_words) < 2:
        return False
    for existing in _recent_fingerprints:
        ex_words = existing.split()[:5]
        matches = sum(1 for w in fp_words if w in ex_words)
        if matches >= 5 and len(fp_words) >= 5:
            return True
    return False


def _detect_language(title: str) -> str:
    russian_chars = len(re.findall(r'[а-яёА-ЯЁ]', title))
    total_chars = len(re.findall(r'[a-zA-Zа-яёА-ЯЁ]', title))
    if total_chars == 0:
        return "en"
    return "ru" if russian_chars / total_chars > 0.3 else "en"


async def fetch_news_json() -> Optional[List[Dict]]:
    """Fetch furniture news JSON from the par repository."""
    all_items = []
    seen_urls = set()

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(
                timeout=FETCH_TIMEOUT,
                follow_redirects=True,
                headers={
                    "User-Agent": "DashaBot/1.0 NewsFetcher",
                    "Accept": "application/json",
                    "Cache-Control": "no-cache",
                },
            ) as client:
                logger.info(f"Fetching furniture news from {NEWS_JSON_URL} (attempt {attempt+1}/2)")
                response = await client.get(NEWS_JSON_URL)
                if response.status_code == 200:
                    data = response.json()
                    items = []
                    if isinstance(data, list):
                        items = data
                    elif isinstance(data, dict):
                        if "items" in data:
                            items = data["items"]
                            logger.info(f"Metadata: {data.get('total_items', len(items))} items, "
                                        f"{data.get('sources_count', 0)} sources")
                        elif "news" in data:
                            items = data["news"]

                    for item in items:
                        item_url = item.get("url", "")
                        if item_url and item_url not in seen_urls:
                            seen_urls.add(item_url)
                            all_items.append(item)

                    logger.info(f"Fetched {len(items)} items, {len(all_items)} unique after dedup")
                    break
                else:
                    logger.warning(f"HTTP {response.status_code} (attempt {attempt+1}/2)")
                    if attempt == 0:
                        await asyncio.sleep(FETCH_RETRY_DELAY)
        except httpx.TimeoutException:
            logger.warning(f"Timeout (attempt {attempt+1}/2, timeout={FETCH_TIMEOUT}s)")
            if attempt == 0:
                await asyncio.sleep(FETCH_RETRY_DELAY)
        except Exception as e:
            logger.error(f"Fetch error: {e}")
            if attempt == 0:
                await asyncio.sleep(FETCH_RETRY_DELAY)

    if not all_items:
        logger.error("News fetch failed. Will retry next cycle.")
        return None

    return all_items


# Расширения/признаки, которые НЕ являются фото и не подходят для send_photo /
# send_media_group (Telegram принимает только jpg/jpeg/png/webp как фото).
# GIF → анимация, SVG → не растр, data: URI — не URL.
_NON_PHOTO_URL_MARKERS = (
    ".svg", ".gif", ".webm", ".mp4", ".mov", ".avi", ".pdf", ".html",
    ".htm", ".php", ".aspx",
)


def _is_likely_photo_url(url: str) -> bool:
    """Грубая фильтрация: оставить только URL, похожие на фото.

    Пропускает URL без расширения (CDN часто отдаёт jpg без .jpg в пути —
    реальный тип проверим по content-type при скачивании в channel.py).
    """
    if not url:
        return False
    low = url.lower().split('?')[0].split('#')[0]
    if low.startswith("data:"):
        return False
    if low.startswith("blob:"):
        return False
    # Явно не-фото расширения — отбрасываем
    for marker in _NON_PHOTO_URL_MARKERS:
        if low.endswith(marker):
            return False
    return True


def _canonical_image_key(url: str) -> str:
    """Канонический ключ для дедупликации фото.

    Нормализация:
      - схема http/https приводится к общей (https)
      - www. отбрасывается
      - query-string отбрасывается
      - CDN size-суффиксы (-1280-80.png, -1920-80.jpg) отбрасываются
      - trailing slash отбрасывается
      - всё в нижнем регистре

    Это устраняет дубликаты одного изображения, отличающиеся схемой
    (http://i.archi.ru/...jpg vs https://i.archi.ru/...jpg) или разрешением.
    """
    base = url.split('?')[0].split('#')[0]
    # Нормализуем схему
    if base.startswith("https://"):
        base = "https://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    # Убираем www.
    base = re.sub(r'^https://www\.', 'https://', base)
    # Убираем CDN size-суффиксы: -1280-80.png, -1920-80.jpg и т.п.
    base = re.sub(r'-\d+-\d+(?=\.\w+$)', '', base)
    # Нижний регистр + убираем trailing slash
    return base.lower().rstrip('/')


def _normalize_news_item(item: Dict) -> Optional[Dict]:
    title = item.get("title", "").strip()
    if not title or len(title) < 10:
        return None
    url = item.get("url", "").strip()
    if not url:
        return None
    summary = item.get("summary", "").strip() or item.get("description", "").strip()

    # ── Сбор фото из всех возможных полей источника ──
    # Источник (par/data/furniture-news.json) отдаёт и `image` (single) и
    # `images` (list). `image` почти всегда дублирует первый элемент `images`,
    # поэтому ставим его первым, а дедупликация ниже уберёт повторы.
    raw_images: List[str] = []

    # Списковые поля
    for field in ["images", "image_urls", "photos"]:
        val = item.get(field, [])
        if isinstance(val, list):
            for img in val:
                if isinstance(img, str) and img.startswith("http"):
                    raw_images.append(html_unescape(img))
        elif isinstance(val, str) and val.startswith("http"):
            # На случай если поле вдруг строка, а не список
            raw_images.append(html_unescape(val))

    # Одиночное поле — ставим в начало (обычно это «обложка» новости)
    single_image = item.get("image", "") or item.get("thumbnail", "")
    if isinstance(single_image, str) and single_image.startswith("http"):
        raw_images.insert(0, html_unescape(single_image))

    # ── Дедупликация по каноническому ключу + фильтр не-фото ──
    seen_keys: set = set()
    unique_images: List[str] = []
    for img_url in raw_images:
        if not _is_likely_photo_url(img_url):
            logger.debug(f"Skipping non-photo URL: {img_url[:80]}")
            continue
        key = _canonical_image_key(img_url)
        if key in seen_keys:
            logger.debug(f"Dedup image (scheme/size variant): {img_url[:80]}")
            continue
        seen_keys.add(key)
        unique_images.append(img_url)

    # Telegram send_media_group: максимум 10 элементов
    unique_images = unique_images[:10]

    lang = item.get("lang", "") or _detect_language(title)
    source = item.get("source", "") or "unknown"
    published = item.get("published", "") or item.get("date", "")

    return {
        "title": title,
        "url": url,
        "summary": summary,
        "source": source,
        "category": "furniture",
        "lang": lang,
        "image_urls": unique_images,
        "published": published,
        "id": item.get("id", ""),
    }


async def run_news_cycle() -> int:
    """Fetch news and store in DB. Returns count of new items.

    Источник: abakanmebel9-jpg/par/data/furniture-news.json
    Новости англоязычные — Даша адаптирует их на русский для канала.
    """
    logger.info("Starting news cycle — fetching furniture news from par repo")

    raw_items = await fetch_news_json()
    if not raw_items:
        logger.warning("No news items fetched")
        return 0

    new_count = 0
    skipped = 0
    duplicates = 0

    items = []
    for raw in raw_items:
        normalized = _normalize_news_item(raw)
        if normalized:
            items.append(normalized)

    items.sort(key=lambda item: item.get("published", ""), reverse=True)
    items = items[:MAX_NEWS_PER_CYCLE]

    for item in items:
        title = item["title"]
        url = item["url"]

        try:
            if await is_duplicate_post(title, hours=48):
                duplicates += 1
                continue
        except Exception:
            pass

        fingerprint = _compute_fingerprint(title)
        if _fingerprint_matches_existing(fingerprint):
            duplicates += 1
            continue

        try:
            await add_news_item(
                title=title, url=url,
                summary=item.get("summary", ""),
                source=item.get("source", "unknown"),
                category=item.get("category", "furniture"),
                lang=item.get("lang", "en"),
                image_urls=item.get("image_urls", []),
                published=item.get("published", ""),
            )
            _recent_fingerprints[fingerprint] = True
            if len(_recent_fingerprints) > 1000:
                for _ in range(len(_recent_fingerprints) - 800):
                    _recent_fingerprints.popitem(last=False)
            new_count += 1
        except Exception as e:
            if "UNIQUE constraint" in str(e) or "duplicate" in str(e).lower():
                skipped += 1
            else:
                logger.error(f"Error adding news item: {e}")
                skipped += 1

    logger.info(f"News cycle: {new_count} new, {duplicates} duplicates, {skipped} skipped")
    return new_count
