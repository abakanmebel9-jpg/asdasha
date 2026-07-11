"""Даша Main — starts OpenClaw gateway + aiogram bot + furniture channel scheduler."""
import asyncio, logging, os, re, signal, subprocess, sys, time, random
from pathlib import Path
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from bot.config import config
from bot import database as db
from bot.mood import mood_loop, current_mood_descriptor
from bot.partners import partner_manager
from ai import client as ai_client
from bot.post_utils import (
    smart_truncate, smart_truncate_html, clean_post_text, validate_post_text,
    enforce_no_meetings, validate_image, title_fingerprint,
    text_fingerprint, url_normalize, date_context, UNIQUIFICATION_RULES,
)
from bot.text_polish import polish_grammar, linkify_contacts, dedupe_contacts

logging.basicConfig(level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("dasha.main")
for noisy in ["aiogram.event", "httpx", "httpcore", "aiosqlite"]: logging.getLogger(noisy).setLevel(logging.WARNING)

from bot.handlers.chat import chat_router
from bot.handlers.groups import group_router
from bot.handlers.channels import channel_router
from bot.handlers.admin import admin_router
from bot.handlers.inline import inline_router

OPENCLAW_STATE_DIR = os.getenv("OPENCLAW_STATE_DIR", str(Path.cwd() / ".openclaw-state"))
_openclaw_proc = None

def _generate_openclaw_config():
    state_dir = OPENCLAW_STATE_DIR
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    out = str(Path(state_dir) / "openclaw.json")
    gen = str(Path(__file__).resolve().parent.parent / "scripts" / "gen_openclaw_config.py")
    env = os.environ.copy(); env["OPENCLAW_STATE_DIR"] = state_dir
    r = subprocess.run([sys.executable, gen, "--out", out, "--state-dir", state_dir], env=env)
    if r.returncode != 0: raise RuntimeError(f"OpenClaw config generation failed (code {r.returncode})")
    return out

def _start_openclaw_gateway(config_path):
    env = os.environ.copy()
    env["OPENCLAW_STATE_DIR"] = OPENCLAW_STATE_DIR
    env["OPENCLAW_CONFIG_PATH"] = config_path
    npm_global = os.path.expanduser("~/.npm-global/bin")
    env["PATH"] = npm_global + ":" + env.get("PATH", "")
    cmd = [config.OPENCLAW_BIN, "gateway", "--port", str(config.OPENCLAW_PORT), "--auth", "none", "--bind", "loopback", "--allow-unconfigured"]
    log_path = str(Path(OPENCLAW_STATE_DIR) / "gateway.log")
    logger.info(f"Starting OpenClaw Gateway: {' '.join(cmd)}")
    log_f = open(log_path, "a", buffering=1)
    return subprocess.Popen(cmd, env=env, stdout=log_f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)

async def _wait_for_gateway(timeout=120.0):
    import httpx
    url = f"{config.OPENCLAW_URL}/v1/models"
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(url, timeout=5.0)
                if r.status_code == 200: return True
        except: pass
        if _openclaw_proc is not None and _openclaw_proc.poll() is not None: return False
        await asyncio.sleep(2.0)
    return False

def _stop_openclaw_gateway():
    global _openclaw_proc
    if _openclaw_proc is not None:
        try:
            _openclaw_proc.terminate()
            try: _openclaw_proc.wait(timeout=10)
            except: _openclaw_proc.kill()
        except: pass
        _openclaw_proc = None

class DashaBot:
    def __init__(self):
        if not config.BOT_TOKEN: raise RuntimeError("BOT_TOKEN not set")
        self.bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=None))
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp.include_router(admin_router)
        self.dp.include_router(chat_router)
        self.dp.include_router(group_router)
        self.dp.include_router(channel_router)
        self.dp.include_router(inline_router)
        from aiogram.types import ErrorEvent
        @self.dp.error()
        async def on_error(event: ErrorEvent):
            try:
                exc = event.exception
                from aiogram.exceptions import TelegramRetryAfter
                if isinstance(exc, TelegramRetryAfter): logger.warning(f"Flood control (RetryAfter {exc.retry_after}s)")
                else: logger.error(f"Handler error (suppressed): {type(exc).__name__}: {exc}", exc_info=False)
            except: pass

    async def start(self):
        logger.info("=== Даша (OpenClaw) стартует ===")
        try:
            me = await self.bot.get_me()
            config.BOT_ID = me.id
            config.BOT_USERNAME = (me.username or config.BOT_USERNAME or "").lstrip("@")
            logger.info(f"Bot: @{config.BOT_USERNAME} (id={config.BOT_ID}) «{me.first_name or ''}», owner={config.OWNER_ID}")
        except Exception as e: logger.warning(f"get_me failed: {e}")
        await db.init_db()
        logger.info("DB initialized")
        # Load posted_news from file backup (prevents duplicates after restart)
        try:
            await db.load_posted_news_from_file()
        except Exception as e:
            logger.warning(f"load_posted_news_from_file failed: {e}")
        try:
            await partner_manager.load()
            logger.info(f"Partners loaded: {len(partner_manager.campaigns)} campaigns")
        except: pass
        await ai_client.initialize()
        logger.info(f"AI client ready — {config.providers_status()}")
        asyncio.create_task(mood_loop(), name="mood_loop")
        asyncio.create_task(db.run_periodic_cleanup(), name="cleanup_loop")
        try:
            from bot.proactive import proactive_loop, summary_loop, set_bot
            set_bot(self.bot)
            asyncio.create_task(proactive_loop(), name="proactive_loop")
            asyncio.create_task(summary_loop(), name="summary_loop")
            logger.info("Proactive + summary loops enabled")
        except Exception as e: logger.warning(f"Proactive failed: {e}")
        # Furniture Channel scheduler — Даша posts to @abakan_mebel
        if config.CHANNEL_ID:
            asyncio.create_task(self._channel_scheduler(), name="channel_scheduler")
            logger.info(f"Channel scheduler enabled (@{config.CHANNEL_USERNAME})")
        await self._notify_owner()
        try: await self.bot.delete_webhook(drop_pending_updates=True)
        except: pass
        allowed = ["message", "edited_message", "channel_post", "edited_channel_post", "inline_query", "chosen_inline_result"]
        logger.info("=== Даша в сети — слушаю сообщения ===")
        polling_retries = 0
        while True:
            try:
                await self.dp.start_polling(self.bot, allowed_updates=allowed)
                break
            except Exception as e:
                polling_retries += 1
                logger.error(f"Polling error (attempt {polling_retries}): {type(e).__name__}: {e}")
                if polling_retries > 50: break
                await asyncio.sleep(5 if polling_retries <= 5 else 10)
        try: await ai_client.close()
        except: pass

    async def _channel_scheduler(self):
        """Background task: post furniture news to @abakan_mebel every 30 min.

        Full pipeline: fetch → dedup → AI generate → clean → polish → validate → smart truncate → post.
        Posts 1 item per cycle. HTML parse mode for clickable footer with phone/site.
        """
        from bot.persona import CHANNEL_POST_PROMPT
        from bot.post_utils import topic_fingerprint
        from aiogram.enums import ParseMode
        await asyncio.sleep(30)  # was 120 — faster first post
        post_interval = 1800  # 30 min (restored to pre-OpenClaw schedule)
        NEWS_URL = "https://raw.githubusercontent.com/abakanmebel9-jpg/par/main/data/furniture-news.json"

        while True:
            try:
                channel_id = int(config.CHANNEL_ID)
                mood = await current_mood_descriptor()

                # 1. Fetch furniture-news.json
                import httpx
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    resp = await client.get(NEWS_URL, headers={"User-Agent": "DashaBot/1.0"})
                if resp.status_code != 200:
                    logger.warning(f"News fetch failed: HTTP {resp.status_code}")
                    await asyncio.sleep(post_interval)
                    continue

                news_data = resp.json()
                all_items = news_data.get("items", [])
                if not all_items:
                    logger.warning("No news items in furniture-news.json")
                    await asyncio.sleep(post_interval)
                    continue

                logger.info(f"Fetched {len(all_items)} furniture news items")

                # 2. Find up to 4 candidate news items (retry if AI empty/validation fail)
                candidates = []
                for item in all_items:
                    news_id = item.get("id", "")
                    item_url = item.get("url", "")
                    title = item.get("title", "")
                    if news_id and await db.is_news_posted(news_id):
                        continue
                    if item_url and await db.is_news_posted(url_normalize(item_url)):
                        continue
                    tf = title_fingerprint(title)
                    if tf and await db.is_news_posted(f"tf:{tf}"):
                        continue
                    topic = topic_fingerprint(title, item.get("summary", ""))
                    if topic and len(topic.split()) >= 2 and await db.is_news_posted(f"topic:{topic}"):
                        logger.info(f"Topic already posted — skip: {topic[:40]}")
                        continue
                    candidates.append(item)
                    if len(candidates) >= 4:
                        break

                if not candidates:
                    # AI-generated fallback: furniture topics (restored from pre-OpenClaw)
                    logger.info("All furniture news posted — AI-generated furniture topic")
                    furniture_topics = [
                        "Кухни из массива дуба: плюсы и минусы",
                        "Скандинавский стиль в интерьере кухни",
                        "Как выбрать ЛДСП для кухни",
                        "Угловые кухни: планировка для маленькой кухни",
                        "Керамогранит vs плитка для фартука",
                        "МДФ фасады: уход и эксплуатация",
                        "Кухонный остров: за и против",
                        "Хранение на кухне: 5 лайфхаков дизайнера",
                        "Освещение кухни: правила и тренды",
                        "Цвет кухни 2026: тренды и сочетания",
                        "Барная стойка вместо обеденного стола",
                        "Интеграция техники в кухонный гарнитур",
                        "Выдвижные системы: организация хранения",
                        "Кухни в стиле лофт: характерные черты",
                        "Минимализм на кухне: меньше деталей, больше пространства",
                    ]
                    topic = random.choice(furniture_topics)
                    mood = await current_mood_descriptor()
                    prompt = (
                        f"Напиши пост для канала @abakan_mebel на тему: {topic}.\n\n"
                        f"Контекст: {date_context()}, настроение: {mood}\n"
                        f"Даша — дизайнер мебели из Абакана. Личный опыт, материалы (массив, ЛДСП, МДФ).\n"
                        f"500-800 символов, живо, с эмодзи. SEO: ключевые слова в начале, 1-2 хештега, вопрос в конце. Женский род. По-русски."
                    )
                    from bot.persona import CHANNEL_POST_PROMPT
                    post = await ai_client.chat(prompt, system=CHANNEL_POST_PROMPT, max_tokens=600, allow_static_fallback=False, prefer_pollinations=True)
                    if post:
                        from bot.post_utils import clean_post_text, smart_truncate
                        from bot.text_polish import polish_grammar
                        ai_text = clean_post_text(post, "Даша")
                        ai_text = polish_grammar(ai_text)
                        phone = getattr(config, 'PHONE', '+7 (913) 448-37-17')
                        tel_digits = phone.replace(" ", "").replace("(", "").replace(")", "").replace("-", "")
                        FOOTER = (
                            f'\n\nАвтор <a href="https://t.me/asdasha_bot">@asdasha_bot</a> Кухни на заказ '
                            f'📞 {phone} '
                            f'🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>'
                        )
                        from aiogram.enums import ParseMode
                        text_body = smart_truncate(ai_text, 4096 - len(FOOTER) - 10, 0)
                        text_full = text_body + FOOTER
                        try:
                            msg = await self.bot.send_message(channel_id, text_full[:4096], parse_mode=ParseMode.HTML)
                            await self._react_to_own_post(channel_id, msg.message_id, text_full[:200])
                            logger.info(f"Channel: posted AI FALLBACK ({len(text_full)} chars) — {topic[:40]}")
                        except Exception as e:
                            # Plain fallback
                            import re
                            plain = re.sub(r'<[^>]+>', '', text_full)[:4096]
                            msg = await self.bot.send_message(channel_id, plain)
                            await self._react_to_own_post(channel_id, msg.message_id, plain[:200])
                            logger.info(f"Channel: posted AI FALLBACK plain ({len(plain)} chars)")
                    # Don't continue — fall through to sleep at end of loop

                # 3. Try candidates until we post 1 (or exhaust candidates)
                posted = False
                for news_item in candidates:
                    try:
                        posted = await self._post_news_item(news_item, mood, channel_id, CHANNEL_POST_PROMPT)
                        if posted:
                            logger.info(f"Cycle: posted furniture news — {news_item.get('title','')[:40]}")
                            break
                        else:
                            logger.info(f"News skipped (AI empty or validation) — trying next candidate")
                    except Exception as e:
                        logger.error(f"Post news item error: {e}")
                if not posted:
                    logger.info(f"Cycle complete: no posts from {len(candidates)} candidates")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Channel scheduler error: {e}")

            await asyncio.sleep(post_interval)

    async def _post_news_item(self, news_item, mood, channel_id, channel_prompt):
        """Post a single furniture news item. Returns True if posted.

        Full pipeline: AI generate → clean → polish (Russian typography) → validate →
        smart truncate (HTML-safe) → post with photo/media_group/text.
        Uses HTML parse mode for clickable footer (phone + website links).
        """
        import httpx
        from aiogram.enums import ParseMode
        from bot.post_utils import (smart_truncate, smart_truncate_html, clean_post_text,
            validate_post_text, enforce_no_meetings, validate_image, text_fingerprint,
            url_normalize, date_context, UNIQUIFICATION_RULES, topic_fingerprint)
        from bot.text_polish import polish_grammar, linkify_contacts

        title = news_item.get("title", "")
        summary = news_item.get("summary", "")
        url = news_item.get("url", "")
        image_url = news_item.get("image", "")
        images_list = news_item.get("images", []) or []
        all_images = list(dict.fromkeys([image_url] + images_list)) if image_url else list(images_list)
        all_images = [u for u in all_images if u][:10]
        news_id = news_item.get("id", "")
        phone = getattr(config, 'PHONE', '+7 (913) 448-37-17')

        # URL dedup
        if url:
            url_key = url_normalize(url)
            if url_key and await db.is_news_posted(url_key):
                logger.info(f"URL already posted — skip: {url_key[:50]}")
                return False

        logger.info(f"Selected furniture news: {title[:60]} (imgs: {len(all_images)})")

        # Generate AI commentary (NO translation — furniture news is already in Russian)
        prompt = (
            f"Напиши пост для канала @abakan_mebel с комментарием на эту новость о мебели/интерьере.\n\n"
            f"Контекст: {date_context()}, настроение: {mood}\n\n"
            f"Заголовок новости: {title}\n"
            f"Краткое содержание: {summary[:500]}\n"
            f"\n{UNIQUIFICATION_RULES}\n\n"
            f"СТИЛЬ (как раньше писала Даша):\n"
            f"- 800-1050 символов, живой экспертный разбор от первого лица\n"
            f"- Даша — дизайнер мебели из Абакана: 'Как дизайнер, я всегда...'\n"
            f"- Материалы: массив, ЛДСП, МДФ, керамогранит, стекло\n"
            f"- Стили: скандинавский, лофт, минимализм, прованс\n"
            f"- Личный опыт: 'В моих проектах...', 'Я всегда задумываюсь...'\n"
            f"- Эмодзи: \U0001f6cb\u2728\U0001f3e8\U0001f3a8\U0001f4d0\U0001fab5\U0001f525 естественно\n"
            f"- Женский род, по-русски, БЕЗ грамматических ошибок\n"
            f"- НЕ добавляй ссылки, НЕ пиши 'Источник'\n"
            f"- НЕ начинай с 'Даша:'\n"
            f"- НЕ предлагай звонки/встречи/записи (это добавит редакция отдельно)"
        )
        ai_commentary = await ai_client.chat(
            prompt, system=channel_prompt,
            max_tokens=800, temperature=0.9, allow_static_fallback=False, prefer_pollinations=True
        )

        if not ai_commentary:
            logger.warning("AI commentary empty — will retry this news next cycle")
            return False

        # Clean AI output
        ai_text = clean_post_text(ai_commentary, "Даша")

        # Safety: remove meeting/booking proposals
        ai_text = enforce_no_meetings(ai_text)

        # Russian typography polish
        ai_text = polish_grammar(ai_text)

        # Validate (politics/NSFW/furniture-relevance)
        is_valid, reason = validate_post_text(ai_text)
        if not is_valid:
            logger.warning(f"Post validation FAILED ({reason}) — marking as skipped: {title[:40]}")
            # Mark as posted so scheduler moves to next news
            if news_id:
                await db.mark_news_posted(news_id, title)
            if url:
                await db.mark_news_posted(url_normalize(url), title)
            return False

        # Text fingerprint dedup
        fp = text_fingerprint(ai_text)
        if await db.is_news_posted(f"fp:{fp}"):
            logger.info(f"Text fingerprint already posted — skip: {fp[:16]}")
            return False

        # HTML footer with clickable links
        tel_digits = phone.replace(" ", "").replace("(", "").replace(")", "").replace("-", "")
        FOOTER = (
            f'\n\nАвтор <a href="https://t.me/asdasha_bot">@asdasha_bot</a> Кухни на заказ '
            f'📞 {phone} '
            f'🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>'
        )

        # Smart truncate (HTML-safe, reserves footer space)
        caption_body = smart_truncate_html(ai_text, 1024, len(FOOTER))
        text_body = smart_truncate_html(ai_text, 4096, len(FOOTER))
        caption_full = caption_body + FOOTER
        text_full = text_body + FOOTER

        posted = False

        # Case A: 2+ images → send_media_group
        if len(all_images) >= 2:
            try:
                media_group = await self._build_media_group(all_images, caption_full)
                if media_group:
                    msgs = await self.bot.send_media_group(channel_id, media_group)
                    posted = True
                    if msgs:
                        await self._react_to_own_post(channel_id, msgs[0].message_id, caption_full[:200])
                    logger.info(f"Channel: posted NEWS media_group ({len(media_group)} photos) — {title[:40]}")
            except Exception as e:
                logger.warning(f"send_media_group failed: {e}")

        # Case B: exactly 1 image → send_photo
        if not posted and len(all_images) == 1:
            try:
                async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as img_client:
                    img_resp = await img_client.get(all_images[0], headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                if img_resp.status_code == 200 and validate_image(img_resp.content):
                    from aiogram.types import BufferedInputFile
                    photo_file = BufferedInputFile(img_resp.content, filename="news.jpg")
                    await self.bot.send_photo(channel_id, photo_file, caption=caption_full[:1024], parse_mode=ParseMode.HTML)
                    posted = True
                    logger.info(f"Channel: posted NEWS+photo (caption {len(caption_full[:1024])}) — {title[:40]}")
                else:
                    logger.warning(f"Image validation failed: HTTP {img_resp.status_code}, {len(img_resp.content)} bytes")
            except Exception as e:
                logger.warning(f"Image download failed: {e}")

        # Case C: no image → send_message (HTML)
        if not posted:
            try:
                msg = await self.bot.send_message(channel_id, text_full[:4096], parse_mode=ParseMode.HTML)
                posted = True
                await self._react_to_own_post(channel_id, msg.message_id, text_full[:200])
                logger.info(f"Channel: posted NEWS text-only ({len(text_full[:4096])} chars) — {title[:40]}")
            except Exception as e:
                logger.error(f"Channel post failed (HTML): {e}")
                # Fallback: plain text (strip HTML tags)
                try:
                    plain = re.sub(r'<[^>]+>', '', text_full)[:4096]
                    await self.bot.send_message(channel_id, plain)
                    posted = True
                    logger.info(f"Channel: posted NEWS text-only PLAIN fallback — {title[:40]}")
                except Exception as e2:
                    logger.error(f"Channel post failed (plain): {e2}")

        # Mark as posted (news_id + URL + title fingerprint + text fingerprint)
        if posted:
            if news_id:
                await db.mark_news_posted(news_id, title)
            if url:
                await db.mark_news_posted(url_normalize(url), title)
            tf = title_fingerprint(title)
            if tf:
                await db.mark_news_posted(f"tf:{tf}", title)
            # Mark topic fingerprint
            topic = topic_fingerprint(title, news_item.get("summary", ""))
            if topic and len(topic.split()) >= 2:
                await db.mark_news_posted(f"topic:{topic}", title)
            await db.mark_news_posted(f"fp:{fp}", title)
        return posted

    async def _build_media_group(self, image_urls, caption_full):
        """Download up to 10 images and build a media group (caption on first).
        Validates each image by magic bytes. Uses HTML parse mode for caption."""
        import httpx
        from aiogram.types import InputMediaPhoto, BufferedInputFile
        from aiogram.enums import ParseMode
        from bot.post_utils import validate_image
        media = []
        first = True
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            for url in image_urls[:10]:
                try:
                    r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                    if r.status_code == 200 and validate_image(r.content):
                        buf = BufferedInputFile(r.content, filename="news.jpg")
                        if first:
                            media.append(InputMediaPhoto(media=buf, caption=caption_full[:1024], parse_mode=ParseMode.HTML))
                            first = False
                        else:
                            media.append(InputMediaPhoto(media=buf))
                except Exception as e:
                    logger.warning(f"media group img fetch failed ({url[:50]}): {e}")
        return media
    async def _react_to_own_post(self, channel_id: int, message_id: int, text: str = ""):
        """Set 3 positive reactions on own channel post with fallback to 1."""
        try:
            import random
            from aiogram.types import ReactionTypeEmoji
            # Only guaranteed Telegram-supported reaction emojis (no ❤️ variation selector)
            pool = ["👍", "❤", "🔥", "😄", "👏", "🎉"]
            emojis = random.sample(pool, 3)
            reaction_types = [ReactionTypeEmoji(type="emoji", emoji=e) for e in emojis]
            await self.bot.set_message_reaction(channel_id, message_id, reaction_types)
            logger.info(f"Reacted to own post (3): {channel_id}/{message_id} with {emojis}")
        except Exception as e:
            msg = str(e)
            if "REACTIONS_TOO_MANY" in msg or "REACTION_INVALID" in msg:
                try:
                    import random as _r
                    single_emoji = _r.choice(["👍", "❤", "🔥"])
                    single = [ReactionTypeEmoji(type="emoji", emoji=single_emoji)]
                    await self.bot.set_message_reaction(channel_id, message_id, single)
                    logger.info(f"Reacted to own post (1 fallback): {channel_id}/{message_id} with {single_emoji}")
                    return
                except Exception as e2:
                    logger.warning(f"React to own post fallback failed: {e2}")
            logger.warning(f"React to own post failed: {e}")

    async def _notify_owner(self):
        mood = await current_mood_descriptor()
        try:
            await self.bot.send_message(config.OWNER_ID, f"Я на связи 🛋 Даша, сейчас я {mood}. OpenClaw: {config.OPENCLAW_URL}. Провайдеры: {config.providers_status()}. Канал: @{config.CHANNEL_USERNAME}. Телефон: {config.PHONE}. Пиши или добавь в группу 💬")
        except: pass

async def main():
    global _openclaw_proc
    cfg_path = _generate_openclaw_config()
    _openclaw_proc = _start_openclaw_gateway(cfg_path)
    ready = await _wait_for_gateway(120.0)
    if not ready:
        logger.error("OpenClaw Gateway did not become ready — exiting")
        _stop_openclaw_gateway()
        sys.exit(1)
    bot = DashaBot()
    def _sig(*_): asyncio.create_task(bot.dp.stop_polling())
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: asyncio.get_running_loop().add_signal_handler(sig, _sig)
        except: pass
    try: await bot.start()
    finally: _stop_openclaw_gateway()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
    except Exception as e:
        logger.exception(f"Fatal: {e}")
        _stop_openclaw_gateway()
        sys.exit(1)
