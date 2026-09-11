"""Даша Main — starts OpenClaw gateway (optional) + aiogram bot + furniture channel scheduler."""
import asyncio, logging, os, re, signal, subprocess, sys, time, random
from pathlib import Path
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from bot.config import config
from bot import database as db
from bot.mood import mood_loop, current_mood_descriptor
from ai import client as ai_client
from bot.post_utils import (
    smart_truncate, smart_truncate_html, clean_post_text, validate_post_text,
    enforce_no_meetings, validate_image, title_fingerprint,
    text_fingerprint, url_normalize, date_context, UNIQUIFICATION_RULES,
    finish_sentences,
)
from bot.text_polish import polish_grammar, linkify_contacts, dedupe_contacts, stylize_post_html
from bot.post_types import get_type_block
from bot.post_context import time_of_day_profile, seasonal_context

logging.basicConfig(level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("dasha.main")
for noisy in ["aiogram.event", "httpx", "httpcore", "aiosqlite"]: logging.getLogger(noisy).setLevel(logging.WARNING)

# Ключевые слова мебельной релевантности, ДВА УРОВНЯ:
# strong — тема точно мебельная (кухни, шкафы, фурнитура, производство мебели)
_NEWS_STRONG_KEYWORDS = [
    "мебел", "кухн", "шкаф", "гардероб", "прихож", "комод", "тумб",
    "стол", "стул", "кресл", "диван", "кроват", "фасад", "фурнитур",
    "лдсп", "мдф", "дсп", "массив", "столешниц", "петл", "направляющ",
    "доводчик", "купо", "купе", "полк", "цоколь", "фартук", "мойк",
    "furniture", "kitchen", "cabine", "mebel",
]
# weak — интерьер/ремонт контекст (допускаем, только если нет чужих тем)
_NEWS_WEAK_KEYWORDS = [
    "интерьер", "ремонт", "планировк", "хранение", "квартир", "спальн",
    "гостин", "детск", "ванн", "санузл", "коридор", "кладов", "ниш",
    "дизайн проект", "дизайн-проект", "дизайнер интерьер",
]
# Чужие темы — никогда не постим, даже если в тексте мелькает «дизайн»
_NEWS_EXCLUDE_KEYWORDS = [
    "спорт", "футбол", "хоккей", "баскетбол", "шахмат", "олимпиад",
    "одежд", "модн", "показ", "бутик", "ткан", "обув", "косметик",
    "автомобил", "дилер", "шиномонтаж", "ресторан", "отель", "кофейн",
    "украшен", "ювелир", "эзотерик", "астролог", "гороскоп", "нумеролог",
]

def _is_furniture_news(title: str, summary: str = "") -> bool:
    """Проверяет, что новость относится к мебели/интерьеру/ремонту.

    Строже прежнего: «дизайн/стиль/тренд» без мебельного контекста больше
    не пропускаем (были посты про спортивные магазины и моду).
    """
    t = f"{title} {summary}".lower()
    if any(kw in t for kw in _NEWS_EXCLUDE_KEYWORDS):
        return False
    if any(kw in t for kw in _NEWS_STRONG_KEYWORDS):
        return True
    return any(kw in t for kw in _NEWS_WEAK_KEYWORDS)

def build_channel_footer() -> str:
    """Единый HTML-футер канала: кликабельные телефон и сайт."""
    phone = config.PHONE or "+7 (913) 448-37-17"
    tel_digits = re.sub(r"[^\d+]", "", phone)
    return (
        f'\n\n🛋 Автор — <a href="https://t.me/asdasha_bot">Даша</a> | Кухни на заказ в Абакане\n'
        f'📞 <a href="tel:{tel_digits}">{phone}</a> | '
        f'🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>'
    )

# Хештеги по ключевым словам темы (для постов без хештегов)
_TOPIC_HASHTAGS = [
    (["кухн"], "#кухни"),
    (["шкаф", "купе", "гардероб"], "#шкафы"),
    (["прихож", "коридор"], "#прихожая"),
    (["мдф", "лдсп", "массив", "материал", "кромк"], "#материалы"),
    (["дизайн", "интерьер", "стил", "тренд", "цвет"], "#дизайнинтерьера"),
    (["фурнитур", "петл", "доводчик", "направляющ"], "#фурнитура"),
    (["столешниц"], "#столешницы"),
    (["влаж", "ванн", "уход", "мыть"], "#уход"),
]

def _add_topic_hashtags(text: str, topic: str = "") -> str:
    """Добавляет до 2 релевантных хештега, если в тексте их ещё нет."""
    if "#" in text:
        return text
    combined = f"{topic} {text}".lower()
    tags = []
    for keywords, tag in _TOPIC_HASHTAGS:
        if any(kw in combined for kw in keywords):
            tags.append(tag)
        if len(tags) >= 2:
            break
    if not tags:
        tags = ["#мебельназаказ"]
    return text.rstrip() + "\n\n" + " ".join(tags)

def _extract_hashtags(text: str) -> str:
    """Извлекает хештеги из текста одной строкой (для повторной приклейки)."""
    tags = re.findall(r"#[\wа-яё]+", text or "", flags=re.IGNORECASE)
    return " ".join(dict.fromkeys(tags))


def _visible_len(html_text: str) -> int:
    """Длина видимого текста (Telegram считает лимиты после парсинга HTML)."""
    import html as _h
    return len(_h.unescape(re.sub(r"<[^>]+>", "", html_text or "")))


def _is_structurally_incomplete(text: str) -> bool:
    """Оборван ли структурный тип поста (сравнение/миф/чек-лист) посреди формата.

    Провайдер иногда срезает ответ даже при max_tokens=1200 — тогда
    «Вариант 1» есть, а «Вариант 2» не успел появиться. Такой пост
    выглядит целым (finish_sentences), но сравнение неполное.
    """
    t = text or ""
    has_v1 = ("Вариант 1" in t) or ("🅰" in t)
    has_v2 = ("Вариант 2" in t) or ("🅱" in t)
    if has_v1 and not has_v2:
        return True
    if "Миф:" in t and "Правда:" not in t:
        return True
    if "Что было:" in t and "Что получилось:" not in t:
        return True
    return False


_RETRY_SUFFIX = (
    "\n\nКОНТРОЛЬ ДЛИНЫ (важно): твой предыдущий ответ был оборван. "
    "Пиши МАКСИМУМ 550 знаков: сжато, без воды, но СО ВСЕМИ структурными элементами формата."
)


def _clean_pipeline(raw: str) -> str:
    """Единая чистка AI-ответа: markdown/утечки → встречи → типографика → ремонт обрывов."""
    t = clean_post_text(raw, "Даша")
    t = enforce_no_meetings(t)
    t = polish_grammar(t)
    t = finish_sentences(t)
    return t


async def _generate_channel_post(prompt: str, channel_prompt: str) -> str:
    """Генерация поста канала с 1 retry: если структура оборвана — повтор с компактным лимитом.

    Возвращает ГОТОВЫЙ чистый текст (clean→enforce→polish→finish) или "".
    """
    first = ""
    for attempt in range(2):
        suffix = "" if attempt == 0 else _RETRY_SUFFIX
        raw = await ai_client.chat(
            prompt + suffix, system=channel_prompt,
            max_tokens=1200, temperature=0.9, allow_static_fallback=False, prefer_pollinations=True,
        )
        if not raw:
            logger.warning(f"Post generation attempt {attempt+1}: empty response")
            continue
        text = _clean_pipeline(raw)
        if len(text) >= 250 and not _is_structurally_incomplete(text):
            return text
        if attempt == 0:
            logger.info(f"Post structurally incomplete (len={len(text)}) — retrying compact")
            first = text if len(text) > len(first) else first
        else:
            return text if len(text) >= 250 else (first if len(first) >= 250 else "")
    return first if len(first) >= 250 else ""



def _style_channel_post(ai_text: str) -> str:
    """Финальный конвейер качества тела поста: escape → хештеги → стилизация.

    ВАЖНО: escape ДО стилизации (стилизация добавляет <b>), хештеги до стилизации
    (стилизация ставит разделитель перед ними).
    """
    import html as _h
    escaped = _h.escape(ai_text)
    return stylize_post_html(escaped)


def _furniture_knowledge_block(text: str) -> str:
    """Блок знаний из базы dasha.py по теме (материалы/стили/размеры)."""
    try:
        from bot.dasha import build_knowledge_context
        kb = build_knowledge_context(text)
        if kb:
            return "\n\nСправка из базы знаний мебельного производства (используй факты, вплети своими словами):\n" + kb
    except Exception as e:
        logger.debug(f"knowledge block error: {e}")
    return ""

from bot.handlers.chat import chat_router
from bot.handlers.groups import group_router
from bot.handlers.channels import channel_router
from bot.handlers.admin import admin_router
from bot.handlers.inline import inline_router
from bot.quiz import quiz_router

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
        self.dp.include_router(quiz_router)
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
        logger.info("=== Даша (корпусная мебель, Абакан) стартует ===")
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
        """Background task: post furniture news to @abakan_mebel every ~30 min.

        Full pipeline: fetch → dedup → AI generate → clean → polish → validate →
        min-quality check → smart truncate → post with photo/media_group/text + reactions.
        Posts 1 item per cycle. HTML parse mode for clickable footer with phone/site.
        """
        from bot.persona import CHANNEL_POST_PROMPT
        from bot.post_utils import topic_fingerprint
        from aiogram.enums import ParseMode
        await asyncio.sleep(30)  # fast first post

        NEWS_URL = config.NEWS_URL

        # Темы для AI-генерации, когда все новости исчерпаны.
        # Строго корпусная мебель: кухни, шкафы, материалы, фурнитура, производство.
        furniture_topics = [
            "Кухни из массива дуба: плюсы и минусы",
            "Скандинавский стиль в интерьере кухни",
            "Как выбрать ЛДСП для корпуса кухни",
            "Угловые кухни: планировка для маленькой кухни",
            "Керамогранит против плитки: фартук на годы",
            "МДФ фасады: уход и эксплуатация",
            "Кухонный остров: за и против",
            "Хранение на кухне: 5 лайфхаков дизайнера",
            "Освещение кухни: правила и тренды",
            "Цвет кухни 2026: тренды и сочетания",
            "Барная стойка вместо обеденного стола",
            "Интеграция техники в кухонный гарнитур",
            "Выдвижные системы: организация хранения в шкафах",
            "Кухни в стиле лофт: характерные черты",
            "Минимализм на кухне: меньше деталей, больше пространства",
            "Шкаф-купе или распашной шкаф: что выбрать",
            "Гардеробная комната за 2 квадратных метра: реально ли",
            "Петли с доводчиком: почему нельзя экономить на фурнитуре",
            "Столешница из массива: как выбрать породу дерева",
            "Прихожая на заказ: 6 идей для маленького коридора",
            "Ошибки планировки кухни: рабочий треугольник",
            "Мебель в ванную: какие материалы выдержат влагу",
            "Матовые или глянцевые фасады: сравнение от практика",
            "Кромка фасадов: ПВХ против ABS — в чём разница",
            "Кухня без ручек: push-to-open и профиль-гола",
            "Наполнение шкафа-купе: штанги, полки, выдвижные корзины",
            "Как рассчитать бюджет кухни на заказ: из чего складывается цена",
            "Фальшпанель vs общий фартук: разбираем детали",
            "Детская мебель: безопасность материалов класса Е0,5",
            "Открытые полки или закрытые шкафы: мнения дизайнера",
            "Тренд тёплого минимализма в мебели 2026",
            "Как подготовить стены к установке корпусной мебели",
            "Уход за фасадами МДФ: чем мыть и чего бояться",
            "Спальные места с подъёмным механизмом: плюсы и минусы",
            "Мебель в студию: зонирование корпусной мебелью",
            "Антивандальные покрытия для детской и прихожей",
            "Что такое столешница из HPL и почему она практична",
            "Замер перед заказом кухни: что важно не упустить",
            "Карго-секции и бутылочницы: стоит ли переплачивать",
            "Встроенная техника: ошибки при заказе под неё шкафов",
            "Цветные кухни: как сочетать фасады со столешницей",
            "Тумба под раковину: как выбрать влагостойкую",
            "Топ-5 вопросов клиентов перед заказом гардеробной",
            "Как проверить качество собранной мебели: чек-лист",
            "Мебельные ножки и цоколь: на что обратить внимание",
        ]
        _topic_cycle = {"i": 0, "order": random.sample(furniture_topics, len(furniture_topics))}

        def _next_topic() -> str:
            order = _topic_cycle["order"]
            topic = order[_topic_cycle["i"] % len(order)]
            _topic_cycle["i"] += 1
            if _topic_cycle["i"] % len(order) == 0:
                _topic_cycle["order"] = random.sample(furniture_topics, len(furniture_topics))
            return topic

        while True:
            try:
                channel_id = int(config.CHANNEL_ID)
                mood = await current_mood_descriptor()

                # 1. Fetch furniture-news.json
                import httpx
                news_items = []
                try:
                    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                        resp = await client.get(NEWS_URL, headers={"User-Agent": "DashaBot/1.0"})
                    if resp.status_code == 200:
                        news_data = resp.json()
                        news_items = news_data.get("items", [])
                        logger.info(f"Fetched {len(news_items)} furniture news items")
                    else:
                        logger.warning(f"News fetch failed: HTTP {resp.status_code}")
                except Exception as e:
                    logger.warning(f"News fetch error: {e}")

                # 2. Find up to 4 candidate news items (retry if AI empty/validation fail)
                candidates = []
                for item in news_items:
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
                    # Фильтр релевантности: тема должна быть про мебель/интерьер/ремонт
                    if not _is_furniture_news(title, item.get("summary", "")):
                        logger.info(f"News not furniture-relevant — skip: {title[:40]}")
                        continue
                    candidates.append(item)
                    if len(candidates) >= 4:
                        break

                if not candidates:
                    # AI-generated fallback: мебельные темы (без повторов циклом)
                    topic = _next_topic()
                    tfp = title_fingerprint(topic)
                    if tfp and await db.is_news_posted(f"fb:{tfp}"):
                        topic = _next_topic()
                        tfp = title_fingerprint(topic)
                    logger.info(f"No fresh furniture news — AI-generated topic: {topic}")
                    await self._post_ai_topic(topic, mood, channel_id)

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
                if not posted and not candidates:
                    logger.info("Cycle complete: fallback topic posted")
                elif not posted:
                    logger.info(f"Cycle complete: no posts from {len(candidates)} candidates")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Channel scheduler error: {e}")

            # Ждём планового интервала (27–36 мин, джиттер) ИЛИ форс-поста (/post_now)
            from bot.scheduler_control import wait_interval_or_force
            forced = await wait_interval_or_force(1620 + random.randint(0, 540))
            if forced:
                logger.info("Scheduler: force post triggered — next cycle immediately")

    async def _post_ai_topic(self, topic: str, mood: str, channel_id: int):
        """Генерирует и публикует пост на заданную мебельную тему (fallback без новости).

        Пайплайн: AI → clean → polish → validate → min-quality → hashtags →
        HTML-пост с футером → реакции. Пометки fb: в БД против повторов.
        """
        from bot.persona import CHANNEL_POST_PROMPT
        from bot.post_utils import clean_post_text, title_fingerprint
        from bot.post_types import get_type_block
        from bot.post_context import time_of_day_profile, seasonal_context
        from aiogram.enums import ParseMode

        kb_block = _furniture_knowledge_block(topic)
        type_block = get_type_block()
        tod_label, tod_block = time_of_day_profile()
        season = seasonal_context()
        season_block = f"\nСезонный контекст: {season}." if season else ""
        prompt = (
            f"Напиши пост для канала @abakan_mebel на тему: {topic}.\n\n"
            f"{type_block}\n\n"
            f"{tod_block}{season_block}\n"
            f"Контекст: {date_context()}, настроение: {mood}\n"
            f"Даша — дизайнер корпусной мебели из Абакана. Личный опыт, конкретика: "
            f"материалы (массив, ЛДСП, МДФ), фурнитура, размеры, ошибки клиентов.{kb_block}\n\n"
            f"Требования: 600-900 знаков, живо, с эмодзи в тему, 1-2 хештега в конце, "
            f"вопрос аудитории в самом конце. Женский род. Только по-русски, БЕЗ английских слов."
        )
        ai_text = await _generate_channel_post(prompt, CHANNEL_POST_PROMPT)
        if not ai_text:
            logger.warning("AI fallback topic: empty/incomplete response")
            return False
        is_valid, reason = validate_post_text(ai_text)
        if not is_valid or len(ai_text) < 250:
            logger.warning(f"AI fallback topic validation FAILED ({reason}, len={len(ai_text)}) — {topic[:40]}")
            return False
        ai_text = _add_topic_hashtags(ai_text, topic)
        ai_text = _style_channel_post(ai_text)

        footer = build_channel_footer()
        text_body = smart_truncate_html(ai_text, 3860, len(footer), append_ellipsis=False)
        # Если при обрезке потерялись хештеги — приклеим заново
        if "#" in ai_text and "#" not in text_body:
            tags = _extract_hashtags(ai_text)
            if tags:
                text_body = (text_body.rstrip() + "\n\n· · ·\n" + tags)[:3860]
        text_full = text_body + footer
        try:
            msg = await self.bot.send_message(channel_id, text_full, parse_mode=ParseMode.HTML)
            await self._react_to_own_post(channel_id, msg.message_id, text_full[:200])
            logger.info(f"Channel: posted AI FALLBACK ({len(text_full)} chars) — {topic[:40]}")
        except Exception as e:
            logger.error(f"Channel fallback post failed (HTML): {e}")
            try:
                import html as _html
                plain = _html.unescape(re.sub(r'<[^>]+>', '', text_full))[:4096]
                msg = await self.bot.send_message(channel_id, plain)
                await self._react_to_own_post(channel_id, msg.message_id, plain[:200])
                logger.info(f"Channel: posted AI FALLBACK plain ({len(plain)} chars)")
            except Exception as e2:
                logger.error(f"Channel fallback post failed (plain): {e2}")
                return False
        # Пометка против повтора темы после рестартов
        tfp = title_fingerprint(topic)
        if tfp:
            await db.mark_news_posted(f"fb:{tfp}", topic)
        return True

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

        # URL dedup
        if url:
            url_key = url_normalize(url)
            if url_key and await db.is_news_posted(url_key):
                logger.info(f"URL already posted — skip: {url_key[:50]}")
                return False

        logger.info(f"Selected furniture news: {title[:60]} (imgs: {len(all_images)})")

        # Адаптивная длина: с фото подпись ≤1024 видимых символов — просим короче;
        # чисто текстовый пост может быть длиннее
        has_media = len(all_images) >= 1
        length_req = "до 680 знаков (коротко и ёмко)" if has_media else "800-1000 знаков"

        # Generate AI commentary (NO translation — furniture news is already in Russian)
        kb_block = _furniture_knowledge_block(f"{title} {summary}")
        type_block = get_type_block()
        tod_label, tod_block = time_of_day_profile()
        season = seasonal_context()
        season_block = f"\nСезонный контекст: {season}." if season else ""
        prompt = (
            f"Напиши пост для канала @abakan_mebel с комментарием на эту новость о мебели/интерьере.\n\n"
            f"{type_block}\n\n"
            f"{tod_block}{season_block}\n"
            f"Контекст: {date_context()}, настроение: {mood}\n\n"
            f"Заголовок новости: {title}\n"
            f"Краткое содержание: {summary[:500]}\n"
            f"{kb_block}"
            f"\n\n{UNIQUIFICATION_RULES}\n\n"
            f"ОБЯЗАТЕЛЬНО:\n"
            f"1. Хук — вопрос/интригующее утверждение по теме новости (НЕ «Сегодня хочу поделиться» и не «Сегодня я расскажу»)\n"
            f"2. Экспертный разбор: {length_req} от первого лица, личный опыт\n"
            f"3. Вывод-совет + вопрос аудитории + 1-2 хештега ОТДЕЛЬНОЙ строкой в самом конце\n\n"
            f"СТИЛЬ (как пишет Даша):\n"
            f"- Даша — дизайнер корпусной мебели из Абакана: 'Как дизайнер, я всегда...'\n"
            f"- Материалы: массив, ЛДСП, МДФ, керамогранит, стекло; фурнитура\n"
            f"- Стили: скандинавский, лофт, минимализм, классика\n"
            f"- Личный опыт: 'В моих проектах...', 'Я всегда задумываюсь...'\n"
            f"- Эмодзи умеренно и в тему\n"
            f"- Женский род, ТОЛЬКО по-русски, без английских слов и вкраплений, БЕЗ грамматических ошибок\n"
            f"- НЕ добавляй ссылки, НЕ пиши 'Источник'\n"
            f"- НЕ начинай с 'Даша:'\n"
            f"- НЕ предлагай звонки/встречи/записи (это добавит редакция отдельно)"
        )
        ai_commentary = await _generate_channel_post(prompt, channel_prompt)

        if not ai_commentary:
            logger.warning("AI commentary empty/incomplete — will retry this news next cycle")
            return False

        # Чистка (markdown/утечки/встречи/типографика/ремонт обрывов) уже внутри
        ai_text = ai_commentary

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

        # Минимальное качество: короткие ответы AI отклоняем и берём следующую новость
        if len(ai_text) < 250:
            logger.warning(f"Post too short for quality ({len(ai_text)} < 250 chars) — trying next candidate")
            return False

        # Хештеги по теме, если AI их не добавил
        ai_text = _add_topic_hashtags(ai_text, title)

        # Text fingerprint dedup (по видимому тексту, до HTML-стилизации)
        fp = text_fingerprint(ai_text)
        if await db.is_news_posted(f"fp:{fp}"):
            logger.info(f"Text fingerprint already posted — skip: {fp[:16]}")
            return False

        # HTML-escape тела + жирные структурные лейблы + разделитель перед хештегами
        ai_text = _style_channel_post(ai_text)

        # Единый HTML-футер с кликабельным телефоном и сайтом
        FOOTER = build_channel_footer()

        # Smart truncate (HTML-safe, reserves footer space, БЕЗ «…» перед футером).
        # Бюджеты по RAW-длине консервативнее видимой (виз ≤ raw), поэтому лимиты
        # Telegram (1024 подпись / 4096 текст) гарантированно не нарушаются.
        caption_body = smart_truncate_html(ai_text, 780, len(FOOTER), append_ellipsis=False)
        text_body = smart_truncate_html(ai_text, 3860, len(FOOTER), append_ellipsis=False)
        # Если при обрезке потерялись хештеги — приклеиваем заново (они короткие, помещаются)
        if "#" in ai_text and "#" not in caption_body:
            tags = _extract_hashtags(ai_text)
            if tags:
                caption_body = (caption_body.rstrip() + "\n\n· · ·\n" + tags)[:860]
        if "#" in ai_text and "#" not in text_body:
            tags = _extract_hashtags(ai_text)
            if tags:
                text_body = (text_body.rstrip() + "\n\n· · ·\n" + tags)[:3860]
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
                    msg = await self.bot.send_photo(channel_id, photo_file, caption=caption_full, parse_mode=ParseMode.HTML)
                    posted = True
                    if msg:
                        await self._react_to_own_post(channel_id, msg.message_id, caption_full[:200])
                    logger.info(f"Channel: posted NEWS+photo (caption vis={_visible_len(caption_full)}) — {title[:40]}")
                else:
                    logger.warning(f"Image validation failed: HTTP {img_resp.status_code}, {len(img_resp.content)} bytes")
            except Exception as e:
                logger.warning(f"Image download failed: {e}")

        # Case C: no image → send_message (HTML)
        if not posted:
            try:
                msg = await self.bot.send_message(channel_id, text_full, parse_mode=ParseMode.HTML)
                posted = True
                await self._react_to_own_post(channel_id, msg.message_id, text_full[:200])
                logger.info(f"Channel: posted NEWS text-only ({len(text_full)} chars) — {title[:40]}")
            except Exception as e:
                logger.error(f"Channel post failed (HTML): {e}")
                # Fallback: plain text (strip HTML tags)
                try:
                    import html as _html
                    plain = _html.unescape(re.sub(r'<[^>]+>', '', text_full))[:4096]
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
                            media.append(InputMediaPhoto(media=buf, caption=caption_full, parse_mode=ParseMode.HTML))
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
        gw = "OpenClaw" if ai_client.gateway_available() else "прямые провайдеры"
        try:
            await self.bot.send_message(config.OWNER_ID, f"Я на связи 🛋 Даша (корпусная мебель, Абакан), сейчас я {mood}. AI: {gw}. Провайдеры: {config.providers_status()}. Канал: @{config.CHANNEL_USERNAME}. Телефон: {config.PHONE}. Пиши или добавь в группу 💬")
        except: pass

async def main():
    global _openclaw_proc
    # OpenClaw gateway — опциональный: если не завёлся, бот работает через
    # прямые провайдеры (Pollinations/Cloudflare/Groq и др.). Это критично
    # для стабильности: шлюз не должен ронять весь бот-процесс.
    gateway_ready = False
    try:
        cfg_path = _generate_openclaw_config()
        _openclaw_proc = _start_openclaw_gateway(cfg_path)
        gateway_ready = await _wait_for_gateway(60.0)
    except Exception as e:
        logger.warning(f"OpenClaw gateway setup failed: {e}")
    if gateway_ready:
        logger.info("OpenClaw gateway ready")
    else:
        logger.warning("OpenClaw gateway NOT ready — продолжаю через прямые провайдеры")
        ai_client.disable_gateway()
        _stop_openclaw_gateway()
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
