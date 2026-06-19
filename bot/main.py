"""
Dasha Bot Main Entry Point — @asdasha_bot
Даша — Дизайнер мебели, ведёт канал @abakan_mebel, работает в abakanmebel.online

Features:
- aiogram 3.x Telegram Bot framework
- Local AI model (RuadaptQwen3-4B) as PRIMARY, Pollinations as fallback
- SQLite with aiosqlite for persistence
- Background tasks: news fetching, hourly channel posting
- Singleton lock to prevent duplicate instances
"""

import asyncio
import faulthandler
import logging
import os
import random
import signal
import sys
import time
import fcntl
from pathlib import Path

faulthandler.enable()

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import config
from bot.database import (
    init_db, cleanup_old_fingerprints, add_chat_message,
    run_periodic_cleanup,
)
from ai.router import ai_router
from news import run_news_cycle
from channel import channel_manager

# ── Logging setup ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("dasha.main")

for noisy in ["aiogram.event", "httpx", "httpcore", "aiosqlite"]:
    logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Singleton Lock ─────────────────────────────────────────────────────────────

class SingletonLock:
    def __init__(self, lock_file: str):
        self.lock_file = lock_file
        self._lock_fd = None

    def acquire(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.lock_file) or ".", exist_ok=True)
            self._lock_fd = open(self.lock_file, "w")
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd.write(str(os.getpid()))
            self._lock_fd.flush()
            return True
        except (IOError, OSError):
            if self._lock_fd:
                self._lock_fd.close()
                self._lock_fd = None
            return False

    def release(self) -> None:
        if self._lock_fd:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
                os.unlink(self.lock_file)
            except (IOError, OSError):
                pass
            self._lock_fd = None


# ── Background Tasks ───────────────────────────────────────────────────────────

class BackgroundTasks:
    def __init__(self, bot: Bot):
        self.bot = bot
        self._running = False
        self._tasks: list = []
        self._greeting_sent = False

    async def start(self) -> None:
        self._running = True
        self._tasks = [
            asyncio.create_task(self._morning_greeting(), name="morning_greeting"),
            asyncio.create_task(self._news_fetcher(), name="news_fetcher"),
            asyncio.create_task(self._channel_poster(), name="channel_poster"),
        ]
        logger.info("Background tasks started")

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        logger.info("Background tasks stopped")

    async def _morning_greeting(self) -> None:
        if self._greeting_sent:
            return
        await asyncio.sleep(15)
        self._greeting_sent = True

        try:
            cooldown_file = "/tmp/dasha_last_greeting"
            if os.path.exists(cooldown_file):
                with open(cooldown_file, "r") as f:
                    last_greeting_time = float(f.read().strip())
                if time.time() - last_greeting_time < 14400:
                    logger.info("Greeting cooldown active — skipping")
                    return
        except Exception:
            pass

        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            hour = datetime.now(ZoneInfo("Europe/Moscow")).hour

            if 5 <= hour < 12:
                greetings = [
                    "Утро! ☕☕ Пора думать об интерьере!",
                    "Доброе утро! ✨ Даша на связи, готова помочь с дизайном!",
                    "Проснулась! Свежие идеи по мебели уже готовы 🛋",
                ]
            elif 12 <= hour < 18:
                greetings = [
                    "Привет! 😊 Есть вопросы по мебели или дизайну?",
                    "День! Рада помочь с выбором мебели 🏠",
                    "На связи! Спрашивайте, что вас интересует ✨",
                ]
            elif 18 <= hour < 23:
                greetings = [
                    "Вечер! 🌆 Готова обсудить ваш будущий интерьер!",
                    "Привет! 🌙 Мечтаете о новом дизайне? Давайте обсудим!",
                ]
            else:
                greetings = [
                    "Ночной режим 🌙 Но если нужно — я тут!",
                ]

            greeting = random.choice(greetings)
            if config.OWNER_ID:
                await self.bot.send_message(config.OWNER_ID, greeting)
                try:
                    await add_chat_message(config.OWNER_ID, "assistant", greeting)
                except Exception:
                    pass
                try:
                    with open("/tmp/dasha_last_greeting", "w") as f:
                        f.write(str(time.time()))
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Morning greeting error: {e}")

    async def _news_fetcher(self) -> None:
        await asyncio.sleep(30)
        cycle_count = 0
        while self._running:
            try:
                count = await run_news_cycle()
                if count > 0:
                    logger.info(f"News fetcher: {count} new items")

                cycle_count += 1
                if cycle_count % 12 == 0:
                    removed = await cleanup_old_fingerprints(max_age_days=7)
                    if removed > 0:
                        logger.info(f"Cleaned up {removed} old post fingerprints")

                if cycle_count % 24 == 0:
                    try:
                        cleanup_results = await run_periodic_cleanup()
                        total_removed = sum(cleanup_results.values())
                        if total_removed > 0:
                            logger.info(f"Periodic cleanup: removed {total_removed} rows")
                    except Exception as e:
                        logger.warning(f"Periodic cleanup failed: {e}")
            except Exception as e:
                logger.error(f"News fetcher error: {e}")

            interval = config.NEWS_INTERVAL_MINUTES * 60
            for _ in range(interval):
                if not self._running:
                    break
                await asyncio.sleep(1)

    async def _channel_poster(self) -> None:
        await asyncio.sleep(120)

        interval_seconds = config.CHANNEL_POST_INTERVAL_MINUTES * 60
        logger.info(
            f"Channel poster started — interval {config.CHANNEL_POST_INTERVAL_MINUTES}min "
            f"(2 posts/hour, hourly limit={getattr(config, 'HOURLY_POST_LIMIT', 2)})"
        )

        consecutive_empty = 0

        while self._running:
            posted = False

            # Try news post
            try:
                result = await channel_manager.post_news()
                if result:
                    posted = True
                    logger.info("Channel poster: news post published")
            except Exception as e:
                logger.error(f"Channel poster error: {e}", exc_info=True)

            # If no news, try AI-generated post
            if not posted:
                try:
                    result = await channel_manager.post_ai_generated()
                    if result:
                        posted = True
                        logger.info("Channel poster: AI-generated post published")
                except Exception as e:
                    logger.error(f"AI post error: {e}", exc_info=True)

            if posted:
                consecutive_empty = 0
            else:
                consecutive_empty += 1
                logger.warning(f"No post this cycle ({consecutive_empty} consecutive)")
                if consecutive_empty >= 3 and self.bot:
                    try:
                        await self.bot.send_message(
                            chat_id=config.OWNER_ID,
                            text=f"⚠️ Даша: {consecutive_empty} циклов без постов. Проверь логи.",
                        )
                    except Exception:
                        pass

            logger.info(f"Waiting {config.CHANNEL_POST_INTERVAL_MINUTES}min until next cycle")
            for _ in range(interval_seconds):
                if not self._running:
                    break
                await asyncio.sleep(1)


# ── Main Entry Point ──────────────────────────────────────────────────────────

async def main():
    if not config.BOT_TOKEN:
        logger.critical("BOT_TOKEN not set! Exiting.")
        sys.exit(1)

    lock = SingletonLock(config.LOCK_FILE)
    if not lock.acquire():
        logger.warning("Another instance is running, exiting.")
        sys.exit(0)

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook deleted, polling mode ready")
    except Exception as e:
        logger.warning(f"Could not delete webhook: {e}")

    await init_db()
    logger.info("Database initialized")

    await ai_router.initialize()
    logger.info("AI Router initialized")

    channel_manager.set_bot(bot)

    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    try:
        from bot.handlers.chat import chat_router
        from bot.handlers.admin import admin_router
        from bot.handlers.inline import inline_router
        dp.include_router(chat_router)
        dp.include_router(admin_router)
        dp.include_router(inline_router)
        logger.info("Handler routers included")
    except Exception as e:
        logger.critical(f"Failed to include handler routers: {e}")
        raise

    bg_tasks = BackgroundTasks(bot)

    async def on_startup():
        await bg_tasks.start()

    async def on_shutdown():
        await bg_tasks.stop()

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    logger.info("=== Dasha Bot Starting — MULTI-PROVIDER FALLBACK v4.0 — @asdasha_bot ===")
    local_status = "enabled" if config.ENABLE_LOCAL_MODEL else "disabled"

    # Log configured cloud providers
    cloud_providers = []
    if config.GH_PAT_TOKEN:
        cloud_providers.append("GitHub Models")
    if config.GROQ_API_KEY:
        cloud_providers.append("Groq")
    if config.GEMINI_API_KEY:
        cloud_providers.append("Gemini")
    if config.OPENROUTER_API_KEY:
        cloud_providers.append("OpenRouter")
    if config.CEREBRAS_API_KEY:
        cloud_providers.append("Cerebras")
    cloud_providers.append("Pollinations (always)")

    logger.info(
        f"Chain: Local({local_status}) → {' → '.join(cloud_providers)}"
    )
    logger.info(
        f"Channel: {config.CHANNEL_USERNAME}, "
        f"Schedule: 2 posts/hour (every {config.CHANNEL_POST_INTERVAL_MINUTES}min), "
        f"Phone: {config.PHONE}"
    )

    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        await bg_tasks.stop()
        lock.release()
        try:
            await bot.session.close()
        except Exception:
            pass
        logger.info("=== Dasha Bot Stopped ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        sys.exit(code)
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        sys.exit(1)