"""
Channel Manager — Posts to @abakan_mebel with proper formatting.
Handles news posts, AI-generated content, and proper footer.

РАСПИСАНИЕ: 2 поста в час (интервал 30 минут).

Every post footer (STRICT):
  Кухни на заказ 📞 +7 (913) 448-37-17 🌐 abakanmebel.online
"""

import logging
import time
import random
import asyncio
import hashlib
import re
from typing import Optional, List, Dict
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaPhoto

from bot.config import config
from bot.database import (
    add_channel_post, get_today_post_count, get_hourly_post_count,
    get_unposted_news, mark_news_posted, add_post_fingerprint,
    is_url_already_posted, save_posted_url,
)
from ai.router import ai_router

logger = logging.getLogger("dasha.channel")

# Post footer — attached to EVERY channel post
# Строго: Кухни на заказ 📞 +7 (913) 448-37-17 🌐 abakanmebel.online
POST_FOOTER = "Кухни на заказ 📞 +7 (913) 448-37-17 🌐 abakanmebel.online"

# Max characters: 4096 without media, 1024 with media (Telegram limits)
TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_MEDIA_TEXT_LIMIT = 1024


def _build_footer() -> str:
    """Build the standard post footer — fixed text."""
    return POST_FOOTER


def _truncate_body(text: str, has_media: bool = False) -> str:
    """Truncate the POST BODY to fit Telegram limits (footer added separately).

    Does NOT append the footer — use _build_post_with_footer() for that.
    Telegram limits:
      - 4096 chars for text-only messages
      - 1024 chars for messages with media (photo/video)
    We reserve space for the footer + separators.
    """
    limit = TELEGRAM_MEDIA_TEXT_LIMIT if has_media else TELEGRAM_TEXT_LIMIT
    footer = _build_footer()
    footer_len = len(footer)
    # Reserve: footer + 10 chars for "\n" separators
    max_body = limit - footer_len - 10

    if max_body < 200:
        # Edge case — footer too big relative to limit
        logger.warning(f"Footer too long ({footer_len}) for limit {limit}")
        max_body = max(limit - footer_len - 10, 200)

    if len(text) > max_body:
        # Truncate at word boundary
        truncated = text[:max_body]
        last_space = truncated.rfind(' ')
        if last_space > max_body - 200:
            text = truncated[:last_space] + "…"
        else:
            text = truncated + "…"
        logger.info(f"Truncated post body to {len(text)} chars (limit {max_body})")

    return text


def _build_post_with_footer(body: str, has_media: bool = False) -> str:
    """Truncate body and append the standard footer ONCE.

    Call this exactly ONCE per post to avoid footer duplication.
    """
    body = _truncate_body(body, has_media=has_media)
    return body + "\n" + _build_footer()


def _strip_footer(text: str) -> str:
    """Remove the footer (and trailing separator) from text if present."""
    footer = _build_footer()
    if text.endswith(footer):
        text = text[: -len(footer)]
    # Remove trailing newlines/whitespace left behind
    text = re.sub(r"[\n\s]+$", "", text)
    return text


# Backward-compat alias for any external callers (e.g. admin.py send_post)
def _truncate_for_channel(text: str, has_media: bool = False) -> str:
    """Legacy alias — truncates body AND appends footer.

    DEPRECATED: use _build_post_with_footer() to avoid double-footer bugs.
    Kept for backward compatibility with admin.py's _build_footer() import.
    """
    return _build_post_with_footer(text, has_media=has_media)


class ChannelManager:
    """Manages posting to @abakan_mebel channel.

    Расписание: 2 поста в час (интервал 30 минут).
    """

    def __init__(self):
        self._bot: Optional[Bot] = None
        self._hourly_counts: Dict[int, int] = {}  # hour -> count

    def set_bot(self, bot: Bot) -> None:
        self._bot = bot

    async def post_news(self) -> bool:
        """Pick best unposted news item, generate post, publish to channel."""
        if not self._bot:
            logger.error("Bot not set on channel manager")
            return False

        # Check daily rate limit (2 posts/hour × 24 = 48 max)
        today_count = await get_today_post_count()
        daily_limit = getattr(config, "DAILY_POST_LIMIT", 48)
        if today_count >= daily_limit:
            logger.info(f"Daily post limit reached ({today_count}/{daily_limit})")
            return False

        # Check hourly limit (2 posts per hour by default)
        hourly = await get_hourly_post_count()
        hourly_limit = getattr(config, "HOURLY_POST_LIMIT", 2)
        if hourly >= hourly_limit:
            logger.info(f"Hourly post limit reached ({hourly}/{hourly_limit})")
            return False

        # Get unposted news
        news_items = await get_unposted_news(limit=20)
        if not news_items:
            logger.info("No unposted news items")
            return False

        # Pick a random item from top candidates
        item = random.choice(news_items[:5])

        # Check URL dedup
        if await is_url_already_posted(item["url"]):
            logger.info(f"URL already posted: {item['url'][:60]}")
            await mark_news_posted(item["url"])
            return False

        # Generate post using AI (local model — PRIMARY)
        response = await ai_router.generate_channel_post(
            topic=item["title"],
            source_text=item.get("summary", ""),
        )

        if response.error or not response.text:
            logger.error(f"AI post generation failed: {response.error}")
            return False

        # Clean and format post
        post_text = response.text.strip()

        # Remove ANY existing contact info / footer from AI text
        # AI sometimes generates its own footer — we must remove ALL of them
        footer_patterns = [
            r"Кухни на заказ.*?abakanmebel\.online",
            r"🛋.*?abakanmebel\.online",
            r"📞.*?\+7.*?37-17",
            r"wa\.me/79134483717",
            r"@abakan_mebel",
            r"@asdasha_bot",
            r"АбаканМебель.*?$",
            r"abakanmebel\.online",
            r"━━━+",
            r"═══+",
            r"───+",
        ]
        for pattern in footer_patterns:
            post_text = re.sub(pattern, "", post_text, flags=re.MULTILINE | re.IGNORECASE)
        post_text = re.sub(r'\n{3,}', '\n\n', post_text).strip()

        # Add standard footer ONCE (with proper truncation for Telegram limits)
        has_media = bool(item.get("image_urls"))
        post_text = _build_post_with_footer(post_text, has_media=has_media)

        # Verify final text fits Telegram limit (final safety check)
        final_limit = TELEGRAM_MEDIA_TEXT_LIMIT if has_media else TELEGRAM_TEXT_LIMIT
        if len(post_text) > final_limit:
            logger.warning(
                f"Post still too long after truncation: {len(post_text)} > {final_limit}, "
                f"force truncating"
            )
            post_text = post_text[:final_limit - 3] + "…"

        # Check if this post is too similar to recent posts
        fingerprint = hashlib.md5(post_text.encode()).hexdigest()
        if await self._is_recent_fingerprint(fingerprint):
            logger.info("Post too similar to recent post, skipping")
            return False

        # Try to send with image
        sent = False
        image_url = ""
        if item.get("image_urls"):
            image_url = item["image_urls"][0]
            try:
                sent = await self._send_with_image(
                    chat_id=config.CHANNEL_ID,
                    text=post_text,
                    image_url=image_url,
                )
            except Exception as e:
                logger.warning(f"Failed to send with image: {e}")

        # Fallback: text only
        if not sent:
            try:
                # If the post was prepared for media (1024 limit) but we're sending
                # text-only (4096 limit), rebuild with the larger text-only limit
                # so we can include more of the original body. Strip the existing
                # footer first, then re-append it once.
                if has_media:
                    body = _strip_footer(post_text)
                    post_text = _build_post_with_footer(body, has_media=False)
                msg = await self._bot.send_message(
                    chat_id=config.CHANNEL_ID,
                    text=post_text,
                    disable_notification=True,
                )
                sent = True
                logger.info(f"Text-only post sent: msg_id={msg.message_id}")
            except Exception as e:
                logger.error(f"Failed to send post: {e}")

        if sent:
            # Save to DB
            await add_channel_post(
                content=post_text,
                message_id=0,
                post_type="news",
                source_url=item["url"],
            )
            await mark_news_posted(item["url"])
            await save_posted_url(item["url"])
            await add_post_fingerprint(fingerprint)
            logger.info(f"Post published: {item['title'][:60]}")
            return True

        return False

    async def post_ai_generated(self, topic: str = "") -> bool:
        """Generate and post AI content without a news source."""
        if not self._bot:
            return False

        hourly = await get_hourly_post_count()
        hourly_limit = getattr(config, "HOURLY_POST_LIMIT", 2)
        if hourly >= hourly_limit:
            return False

        if not topic:
            # Pick a furniture design topic relevant to Abakan furniture company
            topics = [
                "Современные тренды дизайна кухни 2025",
                "Как выбрать матрас для здорового сна",
                "Цветовые сочетания для гостиной",
                "Эргономика рабочего места дома",
                "Скандинавский стиль в интерьере",
                "Мебель из массива дерева — плюсы и минусы",
                "Как организовать хранение в маленькой квартире",
                "Тренды мебельного дизайна 2025",
                "Как выбрать кухонный гарнитур",
                "Дизайн прихожей — первое впечатление",
                "Мебель для детской комнаты — безопасность и комфорт",
                "Лофт стиль — как создать дома",
                "Освещение в интерьере — правила и советы",
                "МДФ или массив — что выбрать",
                "Идеи для балкона и лоджии",
                "Шкаф-купе: как выбрать наполнение",
                "Гардеробная: планировка и организация",
                "Кухонный остров: за и против",
                "Фурнитура Blum — почему её выбирают",
                "Эргономика кухни: рабочий треугольник",
                "Дизайн спальни: как создать атмосферу уюта",
                "Мебель для ванной: влагостойкие материалы",
                "Угловой диван: как выбрать",
                "Цветовая психология в интерьере",
                "Минимализм в интерьере: меньше — значит больше",
                "Неоклассика: современная классика в интерьере",
                "Кухни на заказ: почему это выгодно",
                "Как выбрать столешницу для кухни",
                "Деревянная мебель: уход и реставрация",
                "Эко-стиль: природа в интерьере",
            ]
            topic = random.choice(topics)

        response = await ai_router.generate_channel_post(topic=topic)
        if response.error or not response.text:
            return False

        post_text = response.text.strip()
        # Remove ANY existing contact info / footer from AI text
        footer_patterns = [
            r"Кухни на заказ.*?abakanmebel\.online",
            r"📞.*?\+7.*?37-17",
            r"wa\.me/79134483717",
            r"@abakan_mebel",
            r"@asdasha_bot",
            r"АбаканМебель.*?$",
            r"abakanmebel\.online",
            r"━━━+", r"═══+", r"───+",
        ]
        for pattern in footer_patterns:
            post_text = re.sub(pattern, "", post_text, flags=re.MULTILINE | re.IGNORECASE)
        post_text = re.sub(r'\n{3,}', '\n\n', post_text).strip()

        post_text = _build_post_with_footer(post_text, has_media=False)

        # Final safety check
        if len(post_text) > TELEGRAM_TEXT_LIMIT:
            post_text = post_text[:TELEGRAM_TEXT_LIMIT - 3] + "…"

        try:
            msg = await self._bot.send_message(
                chat_id=config.CHANNEL_ID,
                text=post_text,
                disable_notification=True,
            )
            await add_channel_post(
                content=post_text, message_id=msg.message_id,
                post_type="ai_generated", source_url="",
            )
            logger.info(f"AI post published: {topic[:40]}")
            return True
        except Exception as e:
            logger.error(f"Failed to send AI post: {e}")
            return False

    async def _send_with_image(
        self, chat_id: str, text: str, image_url: str,
    ) -> bool:
        """Send a post with an image from URL.

        NOTE: `text` must already include the footer (built by
        _build_post_with_footer). This method does NOT re-append the footer
        to avoid duplication. If the text exceeds the media caption limit,
        the body is re-truncated while preserving exactly one footer.
        """
        if not self._bot:
            return False

        # If text exceeds the media caption limit (1024), re-truncate the BODY
        # and re-append the footer ONCE. This prevents the double-footer bug
        # that occurred when _truncate_for_channel was called here on text
        # that already had the footer appended.
        if len(text) > TELEGRAM_MEDIA_TEXT_LIMIT:
            body = _strip_footer(text)
            text = _build_post_with_footer(body, has_media=True)

        # Download image
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                img_response = await client.get(image_url)
                if img_response.status_code != 200:
                    return False

                # Determine extension
                content_type = img_response.headers.get("content-type", "")
                ext = ".jpg"
                if "png" in content_type:
                    ext = ".png"
                elif "webp" in content_type:
                    ext = ".webp"

                import tempfile
                import os
                tmp_dir = tempfile.mkdtemp()
                img_path = os.path.join(tmp_dir, f"post_img{ext}")
                with open(img_path, "wb") as f:
                    f.write(img_response.content)

                photo = FSInputFile(img_path)
                await self._bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=text,
                    disable_notification=True,
                )

                # Cleanup
                os.unlink(img_path)
                os.rmdir(tmp_dir)
                logger.info("Post with image sent successfully")
                return True
        except Exception as e:
            logger.warning(f"Image download/send failed: {e}")
            return False

    async def _is_recent_fingerprint(self, fingerprint: str) -> bool:
        """Check if a similar post was recently published."""
        # Simple check — if same fingerprint was used in last 24h
        try:
            from bot.database import is_duplicate_post
            return await is_duplicate_post(fingerprint, hours=24)
        except Exception:
            return False

    async def load_recent_data(self) -> None:
        """Load any needed data on startup."""
        logger.info("Channel manager ready — 2 posts/hour schedule")


# Global singleton
channel_manager = ChannelManager()
