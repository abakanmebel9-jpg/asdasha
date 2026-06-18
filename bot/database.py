"""
Dasha Bot Database — SQLite with aiosqlite
Tables: users, chat_history, news_items, channel_posts, post_fingerprints, posted_urls, ai_cache
"""

import aiosqlite
import hashlib
import json
import time
from typing import Optional, List, Dict, Any
from datetime import datetime

from bot.config import config

DB_PATH = config.DB_PATH

from contextlib import asynccontextmanager

_DB_BUSY_TIMEOUT = 10000
_DB_MAX_RETRIES = 3
_DB_RETRY_DELAY = 0.5

@asynccontextmanager
async def _connect_db():
    db = await aiosqlite.connect(DB_PATH)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute(f"PRAGMA busy_timeout={_DB_BUSY_TIMEOUT}")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA cache_size=-64000")
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '',
    first_name TEXT DEFAULT '',
    last_name TEXT DEFAULT '',
    is_admin INTEGER DEFAULT 0,
    is_blocked INTEGER DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    first_seen TEXT DEFAULT '',
    last_seen TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen);

CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_history_user ON chat_history(user_id, created_at);

CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    url TEXT UNIQUE,
    summary TEXT DEFAULT '',
    source TEXT DEFAULT '',
    category TEXT DEFAULT 'furniture',
    lang TEXT DEFAULT 'en',
    image_urls TEXT DEFAULT '[]',
    published TEXT DEFAULT '',
    posted INTEGER DEFAULT 0,
    fetched_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_news_posted ON news_items(posted);
CREATE INDEX IF NOT EXISTS idx_news_url ON news_items(url);

CREATE TABLE IF NOT EXISTS channel_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    message_id INTEGER DEFAULT 0,
    post_type TEXT DEFAULT 'news',
    source_url TEXT DEFAULT '',
    source_title TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_channel_posts_created ON channel_posts(created_at);

CREATE TABLE IF NOT EXISTS post_fingerprints (
    fingerprint TEXT PRIMARY KEY,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS posted_urls (
    url TEXT PRIMARY KEY,
    posted_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ai_cache (
    cache_key TEXT PRIMARY KEY,
    response TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    hit_count INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_ai_cache_created ON ai_cache(created_at);
"""


async def init_db() -> None:
    async with _connect_db() as db:
        await db.executescript(SCHEMA)
        await db.commit()
    import os
    os.makedirs(os.path.dirname(DB_PATH) or "data", exist_ok=True)


# ── Users ─────────────────────────────────────────────────────────────────────

async def get_or_create_user(user_id: int, username: str = "", first_name: str = "", last_name: str = "") -> Dict:
    async with _connect_db() as db:
        user = await db.execute_fetchall("SELECT * FROM users WHERE user_id = ?", (user_id,))
        if user:
            user = dict(user[0])
            # Update last seen and username
            now = datetime.utcnow().isoformat()
            await db.execute(
                "UPDATE users SET last_seen = ?, username = ?, first_name = ?, last_name = ?, message_count = message_count + 1 WHERE user_id = ?",
                (now, username or user.get("username", ""), first_name or user.get("first_name", ""), last_name or user.get("last_name", ""), user_id),
            )
            await db.commit()
            return user

        now = datetime.utcnow().isoformat()
        await db.execute(
            "INSERT INTO users (user_id, username, first_name, last_name, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, first_name, last_name, now, now),
        )
        await db.commit()
        return {
            "user_id": user_id, "username": username, "first_name": first_name,
            "last_name": last_name, "is_admin": 0, "is_blocked": 0,
            "message_count": 1, "first_seen": now, "last_seen": now,
        }


async def is_user_blocked(user_id: int) -> bool:
    async with _connect_db() as db:
        result = await db.execute_fetchall("SELECT is_blocked FROM users WHERE user_id = ?", (user_id,))
        if result:
            return bool(result[0][0])
        return False


async def is_user_admin(user_id: int) -> bool:
    if user_id == config.OWNER_ID:
        return True
    async with _connect_db() as db:
        result = await db.execute_fetchall("SELECT is_admin FROM users WHERE user_id = ?", (user_id,))
        if result:
            return bool(result[0][0])
        return False


async def set_user_admin(user_id: int, is_admin: bool) -> None:
    async with _connect_db() as db:
        await db.execute("UPDATE users SET is_admin = ? WHERE user_id = ?", (int(is_admin), user_id))
        await db.commit()


async def block_user(user_id: int, blocked: bool) -> None:
    async with _connect_db() as db:
        await db.execute("UPDATE users SET is_blocked = ? WHERE user_id = ?", (int(blocked), user_id))
        await db.commit()


# ── Chat History ──────────────────────────────────────────────────────────────

async def add_chat_message(user_id: int, role: str, content: str) -> None:
    async with _connect_db() as db:
        await db.execute(
            "INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        await db.commit()


async def get_chat_history(user_id: int, limit: int = 20) -> List[Dict]:
    async with _connect_db() as db:
        rows = await db.execute_fetchall(
            "SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        return [{"role": row[0], "content": row[1]} for row in reversed(rows)]


async def clear_chat_history(user_id: int) -> int:
    async with _connect_db() as db:
        cursor = await db.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
        await db.commit()
        return cursor.rowcount


# ── News Items ────────────────────────────────────────────────────────────────

async def add_news_item(
    title: str, url: str, summary: str = "", source: str = "",
    category: str = "furniture", lang: str = "en",
    image_urls: List[str] = None, published: str = "",
) -> None:
    if image_urls is None:
        image_urls = []
    async with _connect_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO news_items (title, url, summary, source, category, lang, image_urls, published) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title, url, summary, source, category, lang, json.dumps(image_urls), published),
        )
        await db.commit()


async def get_unposted_news(limit: int = 20) -> List[Dict]:
    async with _connect_db() as db:
        rows = await db.execute_fetchall(
            "SELECT * FROM news_items WHERE posted = 0 ORDER BY fetched_at DESC LIMIT ?",
            (limit,),
        )
        results = []
        for row in rows:
            item = dict(row)
            if isinstance(item.get("image_urls"), str):
                try:
                    item["image_urls"] = json.loads(item["image_urls"])
                except Exception:
                    item["image_urls"] = []
            results.append(item)
        return results


async def mark_news_posted(url: str) -> None:
    async with _connect_db() as db:
        await db.execute("UPDATE news_items SET posted = 1 WHERE url = ?", (url,))
        await db.commit()


async def is_duplicate_post(title: str, hours: int = 48) -> bool:
    async with _connect_db() as db:
        rows = await db.execute_fetchall(
            "SELECT id FROM news_items WHERE title = ? AND fetched_at > datetime('now', ?) LIMIT 1",
            (title, f"-{hours} hours"),
        )
        return len(rows) > 0


# ── Channel Posts ─────────────────────────────────────────────────────────────

async def add_channel_post(
    content: str, message_id: int = 0, post_type: str = "news", source_url: str = "",
) -> None:
    async with _connect_db() as db:
        await db.execute(
            "INSERT INTO channel_posts (content, message_id, post_type, source_url) VALUES (?, ?, ?, ?)",
            (content, message_id, post_type, source_url),
        )
        await db.commit()


async def get_today_post_count() -> int:
    async with _connect_db() as db:
        rows = await db.execute_fetchall(
            "SELECT COUNT(*) FROM channel_posts WHERE created_at > datetime('now', '-1 day')"
        )
        return rows[0][0] if rows else 0


async def get_hourly_post_count() -> int:
    async with _connect_db() as db:
        rows = await db.execute_fetchall(
            "SELECT COUNT(*) FROM channel_posts WHERE created_at > datetime('now', '-1 hour')"
        )
        return rows[0][0] if rows else 0


# ── Post Fingerprints (dedup) ─────────────────────────────────────────────────

async def add_post_fingerprint(fingerprint: str) -> None:
    async with _connect_db() as db:
        await db.execute("INSERT OR REPLACE INTO post_fingerprints (fingerprint) VALUES (?)", (fingerprint,))
        await db.commit()


async def cleanup_old_fingerprints(max_age_days: int = 7) -> int:
    async with _connect_db() as db:
        cursor = await db.execute(
            "DELETE FROM post_fingerprints WHERE created_at < datetime('now', ?)",
            (f"-{max_age_days} days",),
        )
        await db.commit()
        return cursor.rowcount


# ── Posted URLs ───────────────────────────────────────────────────────────────

async def is_url_already_posted(url: str) -> bool:
    async with _connect_db() as db:
        rows = await db.execute_fetchall("SELECT 1 FROM posted_urls WHERE url = ?", (url,))
        return len(rows) > 0


async def save_posted_url(url: str) -> None:
    async with _connect_db() as db:
        await db.execute("INSERT OR REPLACE INTO posted_urls (url) VALUES (?)", (url,))
        await db.commit()


# ── AI Cache ──────────────────────────────────────────────────────────────────

async def get_cached_response(cache_key: str) -> Optional[str]:
    async with _connect_db() as db:
        rows = await db.execute_fetchall(
            "SELECT response FROM ai_cache WHERE cache_key = ?", (cache_key,)
        )
        if rows:
            await db.execute(
                "UPDATE ai_cache SET hit_count = hit_count + 1 WHERE cache_key = ?", (cache_key,)
            )
            await db.commit()
            return rows[0][0]
        return None


async def cache_response(cache_key: str, response: str) -> None:
    async with _connect_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO ai_cache (cache_key, response) VALUES (?, ?)",
            (cache_key, response),
        )
        await db.commit()


# ── Stats ─────────────────────────────────────────────────────────────────────

async def get_stats() -> Dict[str, int]:
    async with _connect_db() as db:
        total_users = (await db.execute_fetchall("SELECT COUNT(*) FROM users"))[0][0]
        active_users = (await db.execute_fetchall(
            "SELECT COUNT(*) FROM users WHERE last_seen > datetime('now', '-7 days')"
        ))[0][0]
        total_news = (await db.execute_fetchall("SELECT COUNT(*) FROM news_items"))[0][0]
        unposted = (await db.execute_fetchall("SELECT COUNT(*) FROM news_items WHERE posted = 0"))[0][0]
        total_posts = (await db.execute_fetchall("SELECT COUNT(*) FROM channel_posts"))[0][0]
        cached = (await db.execute_fetchall("SELECT COUNT(*) FROM ai_cache"))[0][0]

        return {
            "total_users": total_users,
            "active_users": active_users,
            "total_news": total_news,
            "unposted_news": unposted,
            "total_posts": total_posts,
            "cached_queries": cached,
        }


# ── Periodic Cleanup ──────────────────────────────────────────────────────────

async def run_periodic_cleanup() -> Dict[str, int]:
    results = {}
    async with _connect_db() as db:
        # Chat history older than 30 days
        cursor = await db.execute("DELETE FROM chat_history WHERE created_at < datetime('now', '-30 days')")
        results["chat_history"] = cursor.rowcount
        # AI cache older than 7 days
        cursor = await db.execute("DELETE FROM ai_cache WHERE created_at < datetime('now', '-7 days')")
        results["ai_cache"] = cursor.rowcount
        # Posted news older than 7 days
        cursor = await db.execute("DELETE FROM news_items WHERE posted = 1 AND fetched_at < datetime('now', '-7 days')")
        results["old_news"] = cursor.rowcount
        # Posted URLs older than 30 days
        cursor = await db.execute("DELETE FROM posted_urls WHERE posted_at < datetime('now', '-30 days')")
        results["posted_urls"] = cursor.rowcount
        # Post fingerprints older than 3 days
        cursor = await db.execute("DELETE FROM post_fingerprints WHERE created_at < datetime('now', '-3 days')")
        results["fingerprints"] = cursor.rowcount
        # Channel posts older than 90 days
        cursor = await db.execute("DELETE FROM channel_posts WHERE created_at < datetime('now', '-90 days')")
        results["channel_posts"] = cursor.rowcount

        await db.commit()
    return results