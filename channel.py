"""
Channel Manager — Posts to @abakan_mebel with proper formatting.
Handles news posts, AI-generated content, and proper footer.

Every post footer includes:
  - Services: дизайн и производство мебели
  - Phone from config
  - Website: abakanmebel.online
  - Signature: Автор @asdasha_bot
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
POST_FOOTER_TEMPLATE = """
━━━━━━━━━━━━━━
🛋 Мебель на заказ — дизайн и производство в Абакане
📞 {phone}
🌐 abakanmebel.online
Автор @asdasha_bot
━━━━━━━━━━━━━━"""

# Max characters: 4096 without media, 1024 with media
TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_MEDIA_TEXT_LIMIT = 1024


def _build_footer() -> str:
    """Build the standard post footer with phone from config."""
    phone = getattr(config, "PHONE", "")
    if not phone:
        phone = "свяжитесь через бота @asdasha_bot"
    return POST_FOOTER_TEMPLATE.format(phone=phone)


def _truncate_for_channel(text: str, has_media: bool = False) -> str:
    """Truncate text for Telegram channel limits."""
    limit = TELEGRAM_MEDIA_TEXT_LIMIT if has_media else TELEGRAM_TEXT_LIMIT
    footer = _build_footer()
    footer_len = len(footer)
    max_body = limit - footer_len - 10  # 10 for separators

    if len(text) > max_body:
        text = text[:max_body].rsplit(' ', 1)[0] + "..."

    return text + "\n" + footer


class ChannelManager:
    """Manages posting to @abakan_mebel channel."""

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

        # Check hourly rate limit (1 post per hour as requested)
        now = datetime.now(ZoneInfo("Europe/Moscow"))
        current_hour = now.hour
        today_count = await get_today_post_count()
        
        # Allow max 24 posts per day (1 per hour)
        if today_count >= 24:
            logger.info(f"Daily post limit reached ({today_count}/24)")
            return False

        # Check hourly limit
        hourly = await get_hourly_post_count()
        if hourly >= 1:
            logger.info(f"Hourly post limit reached ({hourly}/1)")
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

        # Generate post using AI
        response = await ai_router.generate_channel_post(
            topic=item["title"],
            source_text=item.get("summary", ""),
        )

        if response.error or not response.text:
            logger.error(f"AI post generation failed: {response.error}")
            return False

        # Clean and format post
        post_text = response.text.strip()
        
        # Remove any existing footer (AI might generate one)
        footer_lines = ["Автор @asdasha_bot", "abakanmebel.online", "Мебель на заказ"]
        for line in footer_lines:
            post_text = post_text.replace(line, "")
        post_text = re.sub(r'━+', '', post_text).strip()
        post_text = re.sub(r'\n{3,}', '\n\n', post_text).strip()

        # Add standard footer
        has_media = bool(item.get("image_urls"))
        post_text = _truncate_for_channel(post_text, has_media=has_media)

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
        if hourly >= 1:
            return False

        if not topic:
            # Pick a furniture design topic
            topics = [
                "Современные тренды дизайна кухни",
                "Как выбрать матрас для здорового сна",
                "Цветовые сочетания для гостиной",
                "Эргономика рабочего места дома",
                "Скандинавский стиль в интерьере",
                "Мебель из массива дерева — плюсы и минусы",
                "Как организовать хранение в маленькой квартире",
                "Тренды мебельного дизайна 2025",
                "Как выбрать кухонный гарнитур",
                "Дизайн прихожей — первая впечатления",
                "Мебель для детской комнаты — безопасность и комфорт",
                "Лофт стиль — как создать дома",
                "Освещение в интерьере — правила и советы",
                "МДФ или массив — что выбрать",
                "Идеи для балкона и лоджии",
            ]
            topic = random.choice(topics)

        response = await ai_router.generate_channel_post(topic=topic)
        if response.error or not response.text:
            return False

        post_text = response.text.strip()
        post_text = _truncate_for_channel(post_text, has_media=False)

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
        """Send a post with an image from URL."""
        if not self._bot:
            return False

        # Truncate text for media posts
        text = _truncate_for_channel(text, has_media=True)

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
        logger.info("Channel manager ready")


# Global singleton
channel_manager = ChannelManager()
