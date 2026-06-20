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


def _normalize_news_item(item: Dict) -> Optional[Dict]:
    title = item.get("title", "").strip()
    if not title or len(title) < 10:
        return None
    url = item.get("url", "").strip()
    if not url:
        return None
    summary = item.get("summary", "").strip() or item.get("description", "").strip()

    # Extract images
    image_urls = []
    for field in ["images", "image_urls", "photos"]:
        val = item.get(field, [])
        if isinstance(val, list):
            for img in val:
                if isinstance(img, str) and img.startswith("http"):
                    image_urls.append(html_unescape(img))

    single_image = item.get("image", "") or item.get("thumbnail", "")
    if single_image:
        if isinstance(single_image, str) and single_image.startswith("http"):
            image_urls.insert(0, html_unescape(single_image))

    # Dedup: strip query strings AND CDN size suffixes (e.g. -1280-80, -1920-80)
    # This prevents sending multi-resolution variants of the same photo
    seen = set()
    unique_images = []
    for img_url in image_urls:
        base_url = img_url.split('?')[0]
        # Normalize CDN size suffixes: -1280-80.png, -1920-80.png, -1600-80.png, etc.
        # Remove the size pattern to get a canonical key for dedup
        canonical = re.sub(r'-\d+-\d+(?=\.\w+$)', '', base_url)
        if canonical not in seen:
            seen.add(canonical)
            unique_images.append(img_url)

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
        "image_urls": unique_images[:10],
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
