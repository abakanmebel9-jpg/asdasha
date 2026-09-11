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
from bot.post_types import get_type_block, last_post_type
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

# Хештеги по ключевым словам темы (для постов без хештегов).
# Порядок важен: более специфичные темы — раньше общих.
_TOPIC_HASHTAGS = [
    (["столешниц"], "#столешницы"),
    (["фурнитур", "петл", "доводчик", "направляющ"], "#фурнитура"),
    (["гардероб"], "#гардеробная"),
    (["купе"], "#шкафыкупе"),
    (["шкаф"], "#шкафы"),
    (["кухн"], "#кухни"),
    (["прихож", "коридор"], "#прихожая"),
    (["влаж", "ванн", "уход", "мыть"], "#уход"),
    (["мдф", "лдсп", "массив", "материал", "кромк", "фасад"], "#материалы"),
    (["дизайн", "интерьер", "стил", "тренд", "цвет"], "#дизайнинтерьера"),
    (["освещ", "подсветк"], "#освещение"),
    (["хранен", "порядок", "организац"], "#хранение"),
]

# Городские теги для локального SEO: Абакан / Хакасия / Черногорск
_CITY_TAGS = ["#Абакан", "#мебельназаказ", "#мебельАбакан"]

# Анти-повтор: не показывать два раза подряд один и тот же набор тегов
_hashtag_state = {"last": frozenset()}

# Раунд 8: скользящее окно недавно использованных тегов (не только подряд).
# Персистентно: JSON в posted_news под ключом hashtag:recent (переживает рестарт).
import json as _json
from collections import deque as _deque
_HTAG_KEY = "hashtag:recent"
_HTAG_WINDOW = 12
_hashtag_recent = {"tags": _deque(maxlen=_HTAG_WINDOW), "loaded": False}


async def _load_hashtag_recent() -> None:
    if _hashtag_recent["loaded"]:
        return
    _hashtag_recent["loaded"] = True
    try:
        raw = await db.get_posted_title(_HTAG_KEY)
        if raw:
            for t in _json.loads(raw):
                if isinstance(t, str) and t not in _hashtag_recent["tags"]:
                    _hashtag_recent["tags"].append(t)
    except Exception as e:
        logger.debug(f"hashtag recent load failed: {e}")


async def _save_hashtag_recent() -> None:
    try:
        await db.mark_news_posted(_HTAG_KEY, _json.dumps(list(_hashtag_recent["tags"]), ensure_ascii=False))
    except Exception as e:
        logger.debug(f"hashtag recent save failed: {e}")


def _remember_tags(tags) -> None:
    for t in tags:
        if t and t not in _hashtag_recent["tags"]:
            _hashtag_recent["tags"].append(t)


async def _add_topic_hashtags(text: str, topic: str = "") -> str:
    """Добавляет 2-3 хештега: тематические + городской тег.

    Защита от однообразия (раунд 8): скользящее окно последних 12 тегов —
    при выборе тематических предпочитаем НЕ использованные недавно.
    Теги самого AI не трогаем, но запоминаем их в окно (чтобы системные
    теги следующих постов не дублировали их).
    """
    await _load_hashtag_recent()
    if "#" in text:
        _remember_tags(re.findall(r"#[\wа-яё]+", text, flags=re.IGNORECASE))
        await _save_hashtag_recent()
        return text
    combined = f"{topic} {text}".lower()
    matched = [tag for keywords, tag in _TOPIC_HASHTAGS if any(kw in combined for kw in keywords)]
    # 1) свежие теги (не из окна) — приоритет; 2) если их меньше двух — добираем из совпавших
    tags = [t for t in matched if t not in _hashtag_recent["tags"]][:2]
    if len(tags) < 2:
        for t in matched:
            if t not in tags:
                tags.append(t)
            if len(tags) >= 2:
                break
    # Городской тег — всегда (локальный поиск: «кухни Абакан»)
    tags.append("#Абакан")
    # Анти-повтор набора подряд
    if frozenset(tags) == _hashtag_state["last"]:
        # Пробуем другую тематическую пару из той же сферы или общий тег
        alt = [t for _, t in _TOPIC_HASHTAGS if t not in tags]
        replacement = alt[0] if alt else _CITY_TAGS[-1]
        if tags and tags[0] not in _CITY_TAGS:
            tags[0] = replacement
        else:
            tags.insert(0, replacement)
            tags = tags[:3]
    _hashtag_state["last"] = frozenset(tags)
    _remember_tags(tags)
    await _save_hashtag_recent()
    return text.rstrip() + "\n\n" + " ".join(tags[:3])

def _extract_hashtags(text: str) -> str:
    """Извлекает хештеги из текста одной строкой (для повторной приклейки)."""
    tags = re.findall(r"#[\wа-яё]+", text or "", flags=re.IGNORECASE)
    return " ".join(dict.fromkeys(tags))


def _visible_len(html_text: str) -> int:
    """Длина видимого текста (Telegram считает лимиты после парсинга HTML)."""
    import html as _h
    return len(_h.unescape(re.sub(r"<[^>]+>", "", html_text or "")))


def _is_structurally_incomplete(text: str, post_type: str = "") -> bool:
    """Оборван/размыт ли структурный тип поста посреди формата.

    Два кейса:
    1) Провайдер срезал ответ: «Вариант 1» есть, «Вариант 2» не появился.
    2) AI проигнорировал формат: сравнение написано сплошной прозой БЕЗ
       обязательных лейблов — стилизация нечего выделить, пост теряет структуру.
    """
    t = text or ""
    has_v1 = ("Вариант 1" in t) or ("🅰" in t)
    has_v2 = ("Вариант 2" in t) or ("🅱" in t)
    # Обрыв: первый вариант есть, второго нет
    if has_v1 and not has_v2:
        return True
    if "Миф:" in t and "Правда:" not in t:
        return True
    if "Что было:" in t and "Что получилось:" not in t:
        return True
    # Игнор формата по типу поста: обязательные лейблы отсутствуют ЦЕЛИКОМ
    if post_type == "compare" and not (has_v1 or has_v2):
        return True
    if post_type == "myth" and ("Миф" not in t or "Правда" not in t):
        return True
    if post_type == "transformation" and "Что было" not in t:
        return True
    if post_type == "checklist" and not any(m in t for m in ("✅", "❌", "📌")):
        return True
    return False


_RETRY_SUFFIX = (
    "\n\nКОНТРОЛЬ ДЛИНЫ (важно): твой предыдущий ответ был оборван. "
    "Пиши МАКСИМУМ 700 знаков: сжато, без воды, но СО ВСЕМИ структурными элементами формата "
    "(лейблы «Вариант 1:»/«Вариант 2:», «Миф:»/«Правда:» и т.п. — каждый с новой строки)."
)

# Требование соблюдения формата типа поста (добавляется в оба постовых промпта)
_FORMAT_COMPLIANCE = (
    "\n\nФОРМАТ (обязательно): если в типе поста указаны строки-лейблы "
    "(«🅰 Вариант 1:» и «🅱 Вариант 2:», «Миф:» и «Правда:», «📸 Что было:», «🔧 Что сделали:», "
    "«✨ Что получилось:», пункты чек-листа ✅/❌/📌) — пиши эти лейблы ДОСЛОВНО, каждый блок С НОВОЙ строки. "
    "Не превращай структуру в сплошную прозу."
)


def _clean_pipeline(raw: str) -> str:
    """Единая чистка AI-ответа: markdown/утечки → встречи → типографика → ремонт обрывов."""
    t = clean_post_text(raw, "Даша")
    t = enforce_no_meetings(t)
    t = polish_grammar(t)
    t = finish_sentences(t)
    return t


# ─── Анти-повтор хуков (раунд 8) ────────────────────────────────────────────
# AI штампует однотипные зачины («Мечтаете о…», «Может ли…» — по 2-3 поста
# подряд в истории канала). Запоминаем первые слова последних 8 хуков;
# при совпадении с любым из них первое предложение срезается целиком —
# пост начинается с сути, а не с клона.

from collections import deque as _hook_deque
_hook_recent = _hook_deque(maxlen=8)
_HOOK_EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")


def _hook_fingerprint(text: str) -> str:
    """Первые 2 слова первой строки поста (lowercase, без эмодзи/пунктуации)."""
    if not text:
        return ""
    first_line = next((ln.strip() for ln in text.split("\n") if ln.strip()), "")
    clean = _HOOK_EMOJI_RE.sub("", first_line)
    clean = re.sub(r"[^\w\sа-яё]", "", clean.lower(), flags=re.IGNORECASE).strip()
    words = [w for w in clean.split() if len(w) > 1]
    return " ".join(words[:2])


def _strip_first_sentence(text: str) -> str:
    """Срезает первое предложение целиком (механика banned openings)."""
    for i, ch in enumerate(text[:400]):
        if ch in ".!?" and (i + 1 >= len(text) or text[i + 1] in " \n"):
            stripped = text[i + 1:].lstrip(" \n")
            if len(stripped) >= 250:
                return stripped
            break  # срез сделает пост короче минимума — оставляем как есть
    return text


def _dedupe_hook(text: str) -> str:
    """Если хук-зачин повторяет недавний — срезаем его; в любом случае запоминаем."""
    if not text:
        return text
    fp = _hook_fingerprint(text)
    if not fp:
        return text
    if fp in _hook_recent:
        before = text
        text = _strip_first_sentence(text)
        if text != before:
            logger.info(f"Hook dedup: repeat opening «{fp}» — first sentence stripped")
    fp2 = _hook_fingerprint(text)
    if fp2 and fp2 not in _hook_recent:
        _hook_recent.append(fp2)
    return text


async def _generate_channel_post(prompt: str, channel_prompt: str, post_type: str = "") -> str:
    """Генерация поста канала с 1 retry: если структура оборвана/размыта — повтор с компактным лимитом.

    Возвращает ГОТОВЫЙ чистый текст (clean→enforce→polish→finish→hook-dedupe) или "".
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
        text = _dedupe_hook(_clean_pipeline(raw))
        if len(text) >= 250 and not _is_structurally_incomplete(text, post_type):
            return text
        if attempt == 0:
            logger.info(f"Post structurally incomplete (len={len(text)}, type={post_type or '?'}) — retrying compact")
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
from bot.calculator import calc_router
from bot.lead_forms import lead_router
from bot.inspiration import inspiration_router
from bot.storage import storage_router

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
        self.dp.include_router(lead_router)
        self.dp.include_router(quiz_router)
        self.dp.include_router(calc_router)
        self.dp.include_router(inspiration_router)
        self.dp.include_router(storage_router)
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
        # Еженедельная сводка владельцу — понедельник 09:00 по Абакану
        try:
            from bot.weekly_report import weekly_report_loop
            asyncio.create_task(weekly_report_loop(self.bot), name="weekly_report_loop")
            logger.info("Weekly report loop enabled (Mon 09:00 Asia/Krasnoyarsk)")
        except Exception as e: logger.warning(f"Weekly report loop failed: {e}")
        # Follow-up заявкам на замер (24 ч, окно 10–20 по Абакану)
        try:
            from bot.lead_followup import lead_followup_loop
            asyncio.create_task(lead_followup_loop(self.bot), name="lead_followup_loop")
            logger.info("Lead follow-up loop enabled (24h check-in)")
        except Exception as e: logger.warning(f"Lead follow-up loop failed: {e}")
        # Еженедельный дайджест в канал — воскресенье 18:00 по Абакану
        if config.CHANNEL_DIGEST_ENABLED and config.CHANNEL_ID:
            try:
                from bot.channel_digest import digest_loop
                asyncio.create_task(
                    digest_loop(self.bot, int(config.CHANNEL_ID)),
                    name="channel_digest_loop",
                )
                logger.info("Channel digest loop enabled (Sun 18:00 Asia/Krasnoyarsk)")
            except Exception as e: logger.warning(f"Channel digest loop failed: {e}")
        # Furniture Channel scheduler — Даша posts to @abakan_mebel
        if config.CHANNEL_ID:
            asyncio.create_task(self._channel_scheduler(), name="channel_scheduler")
            logger.info(f"Channel scheduler enabled (@{config.CHANNEL_USERNAME})")
            # Закреплённый пост «Как заказать» (однократно; повтор — /pin_info)
            try:
                from bot.channel_pin import ensure_pinned_info
                await ensure_pinned_info(self.bot, int(config.CHANNEL_ID))
            except Exception as e:
                logger.warning(f"Channel pin failed: {e}")
        await self._notify_owner()
        try: await self.bot.delete_webhook(drop_pending_updates=True)
        except: pass
        # Меню команд в UI Telegram (личка: полный список; группы: публичные)
        try:
            from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats
            public_cmds = [
                BotCommand(command="consult", description="Контакты и связь 📞"),
                BotCommand(command="measure", description="Заявка на бесплатный замер 📐"),
                BotCommand(command="calc", description="Калькулятор стоимости 🧮"),
                BotCommand(command="catalog", description="Каталог решений с ценами 🛋"),
                BotCommand(command="price", description="Ориентиры по ценам 💰"),
                BotCommand(command="quiz", description="Квиз «Подбери мебель» 🧩"),
                BotCommand(command="inspiration", description="Вдохновение с примерами 🎨"),
                BotCommand(command="reviews", description="Отзывы клиентов ⭐"),
                BotCommand(command="faq", description="Частые вопросы ❓"),
                BotCommand(command="process", description="Этапы работы 🔧"),
                BotCommand(command="care", description="Уход за мебелью 🧼"),
                BotCommand(command="terms", description="Мебельный словарь 📖"),
                BotCommand(command="storage", description="Идеи хранения 🗃"),
                BotCommand(command="about", description="О производстве 🏭"),
                BotCommand(command="fact", description="Факт о мебели 💡"),
            ]
            private_cmds = public_cmds + [
                BotCommand(command="help", description="Что я умею"),
                BotCommand(command="clear", description="Забыть историю чата"),
                BotCommand(command="mood", description="Моё настроение"),
                BotCommand(command="whoami", description="Что я о тебе помню"),
            ]
            await self.bot.set_my_commands(private_cmds, scope=BotCommandScopeAllPrivateChats())
            await self.bot.set_my_commands(public_cmds, scope=BotCommandScopeAllGroupChats())
            logger.info("Bot commands menu registered (private + groups)")
        except Exception as e:
            logger.warning(f"set_my_commands failed: {e}")
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

                # Опрос «Вопрос дня» (не чаще ~раза в 4 ч, шанс 40%) — после поста
                if config.CHANNEL_POLLS_ENABLED:
                    try:
                        from bot import polls
                        await polls.maybe_send_poll(self.bot, channel_id)
                    except Exception as e:
                        logger.warning(f"Poll step failed: {e}")

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
        визуал по теме (AI-генерация, если включён) → фото-пост с подписью
        ИЛИ текст-пост с футером → реакции. Пометки fb: в БД против повторов.
        """
        from bot.persona import CHANNEL_POST_PROMPT
        from bot.post_utils import clean_post_text, title_fingerprint, smart_truncate_html
        from bot.post_types import get_type_block, last_post_type
        from bot.post_context import time_of_day_profile, seasonal_context
        from aiogram.enums import ParseMode

        kb_block = _furniture_knowledge_block(topic)
        type_block = get_type_block()
        ptype = last_post_type()
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
            f"вопрос аудитории в самом конце. Женский род. Только по-русски, БЕЗ английских слов. "
            f"ПЕРВАЯ строка-хук — строго о теме «{topic[:80]}», без посторонних клише. "
            f"НЕ начинай с «Мечтаете о…» и «Может ли…» — это заезженные шаблоны; "
            f"варьируй: интригующее утверждение, неожиданный факт/цифра, мини-история из проекта."
            f"{_FORMAT_COMPLIANCE}"
        )
        ai_text = await _generate_channel_post(prompt, CHANNEL_POST_PROMPT, ptype)
        if not ai_text:
            logger.warning("AI fallback topic: empty/incomplete response")
            return False
        is_valid, reason = validate_post_text(ai_text)
        if not is_valid or len(ai_text) < 250:
            logger.warning(f"AI fallback topic validation FAILED ({reason}, len={len(ai_text)}) — {topic[:40]}")
            return False
        ai_text = await _add_topic_hashtags(ai_text, topic)
        ai_text = _style_channel_post(ai_text)

        footer = build_channel_footer()
        text_body = smart_truncate_html(ai_text, 3860, len(footer), append_ellipsis=False)
        # Если при обрезке потерялись хештеги — приклеим заново
        if "#" in ai_text and "#" not in text_body:
            tags = _extract_hashtags(ai_text)
            if tags:
                text_body = (text_body.rstrip() + "\n\n· · ·\n" + tags)[:3860]
        text_full = text_body + footer

        # Подпись для фото-поста (бюджет caption 1024): те же правила, что у новостей
        caption_body = smart_truncate_html(ai_text, 780, len(footer), append_ellipsis=False)
        if "#" in ai_text and "#" not in caption_body:
            tags = _extract_hashtags(ai_text)
            if tags:
                caption_body = (caption_body.rstrip() + "\n\n· · ·\n" + tags)[:860]
        caption_full = caption_body + footer

        # Визуал по теме (опция): пост с фото живее текстового
        photo_bytes = None
        if config.VISUALS_ENABLED:
            try:
                from bot.visuals import generate_furniture_image
                photo_bytes = await generate_furniture_image(topic)
            except Exception as e:
                logger.debug(f"visual generation failed: {e}")

        msg = None
        if photo_bytes:
            try:
                from aiogram.types import BufferedInputFile
                photo_file = BufferedInputFile(photo_bytes, filename="topic.jpg")
                msg = await self.bot.send_photo(channel_id, photo_file, caption=caption_full, parse_mode=ParseMode.HTML)
                logger.info(f"Channel: posted AI FALLBACK+visual (caption vis={_visible_len(caption_full)}) — {topic[:40]}")
            except Exception as e:
                logger.warning(f"Channel fallback photo post failed: {e}")
        if msg is None:
            try:
                msg = await self.bot.send_message(channel_id, text_full, parse_mode=ParseMode.HTML)
                logger.info(f"Channel: posted AI FALLBACK ({len(text_full)} chars) — {topic[:40]}")
            except Exception as e:
                logger.error(f"Channel fallback post failed (HTML): {e}")
                try:
                    import html as _html
                    plain = _html.unescape(re.sub(r'<[^>]+>', '', text_full))[:4096]
                    msg = await self.bot.send_message(channel_id, plain)
                    logger.info(f"Channel: posted AI FALLBACK plain ({len(plain)} chars)")
                except Exception as e2:
                    logger.error(f"Channel fallback post failed (plain): {e2}")
                    return False
        if msg is not None:
            await self._react_to_own_post(channel_id, msg.message_id, (caption_full or text_full)[:200])
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
        ptype = last_post_type()
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
            f"1. Хук — вопрос/интригующее утверждение СТРОГО по теме новости «{title[:80]}» (НЕ «Сегодня хочу поделиться», не «Сегодня я расскажу», не «Мечтаете о…», не «Может ли…» — это заезженные шаблоны; варьируй: факт, цифра, мини-история, провокационное утверждение)\n"
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
            f"{_FORMAT_COMPLIANCE}"
        )
        ai_commentary = await _generate_channel_post(prompt, channel_prompt, ptype)

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
        ai_text = await _add_topic_hashtags(ai_text, title)

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

        # Case C: no image → пробуем сгенерировать визуал по теме, иначе send_message (HTML)
        if not posted:
            if config.VISUALS_ENABLED:
                try:
                    from bot.visuals import generate_furniture_image
                    photo_bytes = await generate_furniture_image(f"{title} {summary[:200]}")
                    if photo_bytes:
                        from aiogram.types import BufferedInputFile
                        photo_file = BufferedInputFile(photo_bytes, filename="topic.jpg")
                        msg = await self.bot.send_photo(channel_id, photo_file, caption=caption_full, parse_mode=ParseMode.HTML)
                        posted = True
                        if msg:
                            await self._react_to_own_post(channel_id, msg.message_id, caption_full[:200])
                        logger.info(f"Channel: posted NEWS+generated visual (caption vis={_visible_len(caption_full)}) — {title[:40]}")
                except Exception as e:
                    logger.warning(f"Generated visual post failed: {e}")
                    posted = False
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
