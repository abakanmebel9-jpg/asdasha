"""
Chat Handler — Main user interaction with Dasha AI.
Handles private chats, group chats, comments, photo analysis.
Даша — дизайнер мебели: консультирует по дизайну, материалам, заказам.

РЕЖИМЫ:
  - Личный чат (private): полная консультация, история диалога, живое общение
  - Группа/супергруппа: АКТИВНО участвует! Отвечает на ВСЕ сообщения —
    на упоминания @asdasha_bot / "даша" развёрнуто,
    на мебельную тематику — как эксперт,
    на короткие фразы — живо, 
    на общие разговорные — иногда реагирует (20%),
    на replies на свои сообщения — всегда.
"""

import re
import random
import logging
import time
from typing import Optional

from aiogram import Router, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, PhotoSize
from aiogram.enums import ChatAction

from bot.config import config
from bot.database import (
    get_or_create_user, is_user_blocked, add_chat_message,
    clear_chat_history,
)
from bot.dasha import (
    DASHA_PHRASES, identify_furniture_topic, get_material_advice,
    get_room_recommendations, get_style_info, detect_phone_request,
    detect_delivery_interest, detect_price_interest, build_knowledge_context,
    FURNITURE_MATERIALS, FURNITURE_STYLES, STANDARD_SIZES,
    ABAKAN_KNOWLEDGE, PRODUCTION_PROCESS,
)
from ai.router import ai_router
from bot.optimizations import adaptive_max_chars, chat_type_context, dedup_check, dedup_store

logger = logging.getLogger("dasha.handlers.chat")

chat_router = Router()


# ── Telegram message character limits ──────────────────────────────────────────
TELEGRAM_MSG_LIMIT = 4096  # Maximum characters per Telegram message


def _safe_truncate(text: str, max_len: int) -> str:
    """Truncate HTML text to max_len characters WITHOUT breaking HTML tags.

    Critical for Telegram messages — cutting through an <a href="...">
    tag would break the message and cause a Telegram API error.
    Also closes any unclosed HTML tags after truncation.
    """
    if len(text) <= max_len:
        return text

    # Reserve 4 chars for "…" suffix
    target_len = max_len - 4
    if target_len < 50:
        target_len = 50

    truncated = text[:target_len]

    # Don't split inside an HTML tag
    last_open = truncated.rfind("<")
    last_close = truncated.rfind(">")
    if last_open > last_close:
        truncated = text[:last_open]

    # Try to break at a word boundary
    last_space = truncated.rfind(" ")
    last_newline = truncated.rfind("\n")
    break_point = max(last_space, last_newline)
    if break_point > len(truncated) - 200:
        truncated = truncated[:break_point]

    # Close any unclosed HTML tags (Telegram supports: b, i, u, s, a, code, pre)
    opened_tags = re.findall(r"<(a|b|i|s|u|code|pre|strong|em|span)\b", truncated)
    closed_tags = re.findall(r"</(a|b|i|s|u|code|pre|strong|em|span)>", truncated)
    tags_to_close = []
    for tag in reversed(opened_tags):
        if tag in closed_tags:
            closed_tags.remove(tag)
        else:
            tags_to_close.append(tag)

    for tag in tags_to_close:
        truncated += f"</{tag}>"

    truncated += "…"
    return truncated


# ── Rate limiting ──────────────────────────────────────────────────────────────

_user_last_message: dict = {}


def _check_message_rate(user_id: int, min_interval: float = 2.0) -> bool:
    now = time.time()
    last = _user_last_message.get(user_id, 0)
    if now - last < min_interval:
        return False
    _user_last_message[user_id] = now
    return True


# ── Per-chat cooldown for non-priority group messages ─────────────────────────
# Предотвращает флуд в очень активных чатах: упоминания/replies/мебель обходят
# cooldown и отвечают всегда. Для остальных — не чаще чем раз в N секунд на чат.

_chat_last_response: dict = {}


def _chat_cooldown_active(chat_id: int, is_priority: bool) -> bool:
    """Вернёт True, если сообщение следует ПРОПУСТИТЬ из-за per-chat cooldown.

    Приоритетные сообщения (упоминания, replies, мебельная тематика) НИКОГДА не
    пропускаются — на них Даша отвечает всегда.
    """
    if is_priority:
        return False
    now = time.time()
    last = _chat_last_response.get(chat_id, 0.0)
    cooldown = config.GROUP_COOLDOWN_SECONDS
    if cooldown <= 0:
        return False
    if now - last < cooldown:
        return True  # cooldown активен — пропустить
    return False


def _mark_chat_responded(chat_id: int) -> None:
    _chat_last_response[chat_id] = time.time()


# ── Group trigger detection ───────────────────────────────────────────────────
# Даша АКТИВНО участвует во ВСЕХ чатах и группах — отвечает на КАЖДОЕ событие:
#   1) Упоминания (@asdasha_bot / "даша") — ВСЕГДА развёрнутый ответ
#   2) Replies на сообщения Даши — ВСЕГДА ответ
#   3) Мебельная/дизайнерская тематика — ВСЕГДА экспертный комментарий
#   4) Разговорные фразы (привет, спасибо, класс) — ВСЕГДА живая реакция
#   5) Любое другое сообщение — ВСЕГДА короткое участие в беседе
# Per-chat cooldown (#GROUP_COOLDOWN_SECONDS) защищает от флуда в активных чатах,
# но НЕ применяется к приоритетным сообщениям (1-3).

# Ключевые слова мебельной/дизайнерской тематики для групп
_FURNITURE_TRIGGER_KEYWORDS = [
    # Мебель
    "кухн", "шкаф", "купе", "гардероб", "кроват", "диван", "кресл", "стол",
    "стул", "тумб", "комод", "полк", "стеллаж", "прихож", "фасад",
    # Материалы
    "мдф", "лдсп", "массив", "дуб", "бук", "ясен", "орех", "шпон", "фурнитур",
    "петл", "направляющ", "ручк", "доводчик", "столешниц", "blum", "hettich",
    # Дизайн/интерьер
    "дизайн", "интерьер", "стил", "лофт", "скандинавск", "минимализм",
    "классик", "прованс", "хай-тек", "эко-стил", "неоклассик",
    "цвет", "отделк", "ремонт", "освещен",
    # Заказ/услуги
    "заказ", "замер", "доставк", "сборк", "установк", "гаранти",
    "стоимост", "цен", "рубл", "абаканмебел", "abakanmebel",
    # Регион
    "абакан", "хакаси", "черногорск", "саяногорск",
]


def _is_mentioned(message: Message) -> bool:
    """Проверяет, упомянут ли бот в сообщении."""
    text = (message.text or message.caption or "").lower()
    # Проверка @asdasha_bot
    if "@asdasha_bot" in text or "@asdasha" in text:
        return True
    # Проверка по имени "даша"
    if re.search(r'\bдаш[ауе]\b', text):
        return True
    # Проверка entities (mention)
    if message.entities:
        for ent in message.entities:
            if ent.type == "mention":
                mention_text = (message.text or "")[ent.offset:ent.offset + ent.length]
                if "asdasha" in mention_text.lower():
                    return True
    return False


def _has_furniture_topic(text: str) -> bool:
    """Проверяет, содержит ли текст мебельную/дизайнерскую тематику."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in _FURNITURE_TRIGGER_KEYWORDS)


def _estimate_response_length(text: str, chat_type: str, is_mentioned: bool) -> int:
    """Estimate optimal response length based on context.

    В личке — максимум места. В группе — зависит от контекста:
    - Короткий вопрос → краткий ответ (1-3 предложения)
    - Развернутый вопрос с деталями → средний ответ (3-6 предложений)
    - Прямое упоминание → более развернутый ответ
    """
    if chat_type == "private":
        return 4000

    text_lower = text.lower()
    text_len = len(text)

    # Mentioned — can be more verbose
    if is_mentioned:
        if text_len > 100:
            return 1500  # Подробный вопрос с упоминанием — развернутый ответ
        return 800  # Краткое упоминание — средний ответ

    # Long message with details — medium response
    if text_len > 150:
        return 1000

    # Short message — brief response
    if text_len < 30:
        return 400  # Одно слово — один короткий ответ

    return 600  # Default for groups


def _is_conversational(text: str) -> bool:
    """Check if message is casual conversation (greetings, reactions, etc.)"""
    text_lower = text.lower().strip()
    conversational = [
        "привет", "здравствуй", "хай", "хей", "добрый день", "доброе утро",
        "добрый вечер", "спасибо", "спс", "благодарю", "класс", "круто",
        "отлично", "супер", "здорово", "понятно", "ясно", "ладно",
        "ок", "окей", "ага", "да", "нет", "ну да", "конечно", "точно",
        "хорошо", "пойдёт", "согласен", "согласна", "поддерживаю",
        "красиво", "нравится", "круто выглядет", "вайб", "имба",
        "мешать не буду", "не мешаю", "прохожу мимо", "просто так",
    ]
    return any(text_lower == word or text_lower.startswith(word + " ") or text_lower.startswith(word + "!") for word in conversational)


def _is_reply_to_bot(message: Message) -> bool:
    """Проверяет, является ли сообщение ответом на сообщение бота."""
    if not message.reply_to_message:
        return False
    return message.reply_to_message.from_user and message.reply_to_message.from_user.is_bot


def _is_bot_question(text: str) -> bool:
    """Проверяет, спрашивает ли пользователь про бота/ИИ."""
    text_lower = text.lower()
    bot_keywords = [
        "ты бот", "вы бот", "ты ботя", "ты искуственн", "ты ии",
        "ты нейросет", "ты программ", "ты робот", "ты машина",
        "ты искусствен", "ты автомат", "это бот", "это ии",
        "ты искусственный интеллект", "ты чат-бот", "ты чатбот",
        "ты текстовый бот", "вы искусствен", "ты скрипт",
        "ты гпт", "ты gpt", "ты chatgpt", "ты нейро",
        "ты симуляци", "ты язык", "bot?",
    ]
    # Must be a question or direct statement
    if any(kw in text_lower for kw in bot_keywords):
        return True
    # Questions like "человек или бот?", "бот или человек?"
    if re.search(r'бот|и\.?и\.?|робот|нейросет', text_lower) and re.search(r'\?', text):
        return True
    return False


# ── Reaction emojis for groups ──
_REACTION_EMOJIS = ["👍", "❤️", "🔥", "✨", "😅", "👏", "🤔", "😊", "💯", "💪"]


async def _try_add_reaction(message: Message) -> None:
    """Try to add a random reaction emoji to a message (groups only)."""
    if message.chat.type not in ("group", "supergroup"):
        return
    prob = config.GROUP_REACTION_PROBABILITY
    if random.random() > prob:
        return
    try:
        emoji = random.choice(_REACTION_EMOJIS)
        await message.react([emoji])
    except Exception:
        pass  # Not all groups allow reactions


# ── /start ─────────────────────────────────────────────────────────────────────

@chat_router.message(CommandStart())
async def cmd_start(message: Message):
    await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
    )

    greeting = random.choice(DASHA_PHRASES["greetings"])
    welcome = (
        f"{greeting}\n\n"
        f"Я дизайнер мебели, работаю в abakanmebel.online 🏠\n"
        f"Могу помочь с:\n"
        f"• Дизайном интерьера и подбором мебели 📐\n"
        f"• Выбором материалов и фурнитуры 🪵\n"
        f"• Расчётом стоимости и сроков 💰\n"
        f"• Бесплатным замером в Абакане 📏\n\n"
        f"Просто напишите что вас интересует!\n"
        f'📞 <a href="tel:+79134483717">+7 (913) 448-37-17</a>\n'
        f'🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>'
    )
    await message.answer(welcome)


# ── /help ──────────────────────────────────────────────────────────────────────

@chat_router.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "🛋 <b>Что умеет Даша:</b>\n\n"
        "• Консультация по дизайну интерьера\n"
        "• Подбор мебели и материалов\n"
        "• Расчёт размеров и эргономика\n"
        "• Выбор стиля для вашего дома\n"
        "• Информация о заказе и доставке\n"
        "• Знания об Абакане и Хакасии\n\n"
        "<b>Команды:</b>\n"
        "/start — начать\n"
        "/help — эта справка\n"
        "/clear — очистить историю чата\n"
        "/about — о Даше\n"
        "/phone — телефон компании\n"
        "/order — как заказать мебель\n"
        "/prices — ориентировочные цены\n"
        "/delivery — доставка и сборка\n"
    )
    await message.answer(help_text)


# ── /about ─────────────────────────────────────────────────────────────────────

@chat_router.message(Command("about"))
async def cmd_about(message: Message):
    about = (
        f"👋 Я Даша — дизайнер мебели из Абакана 🏠\n\n"
        f"Работаю в компании «АбаканМебель» — 25 лет опыта, тысячи проектов "
        f"по Хакасии. Помогаю подобрать мебель, спроектировать интерьер, "
        f"выбрать материалы и фурнитуру.\n\n"
        f"📌 Веду канал: {config.CHANNEL_USERNAME}\n"
        f'🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>\n'
        f'📞 <a href="tel:+79134483717">{config.PHONE}</a>\n'
        f"📍 Абакан, Республика Хакасия (ул. Гончарная, 10)\n"
        f"🛠 Гарантия: {config.WARRANTY}\n"
        f"🤖 Бот: {config.BOT_USERNAME}"
    )
    await message.answer(about)


# ── /phone ─────────────────────────────────────────────────────────────────────

@chat_router.message(Command("phone"))
async def cmd_phone(message: Message):
    phone = config.PHONE
    text = (
        f'📞 <b>Телефон компании «АбаканМебель»:</b>\n'
        f'<b><a href="tel:+79134483717">{phone}</a></b>\n\n'
        f'💬 <a href="https://wa.me/79134483717">WhatsApp</a>\n'
        f'🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>\n'
        f'📍 Адрес: {config.ADDRESS}\n'
        f'🕐 Часы работы: {config.WORKING_HOURS}\n\n'
        f'Позвоните или напишите — проконсультирую! 😊'
    )
    await message.answer(text)


# ── /order ─────────────────────────────────────────────────────────────────────

@chat_router.message(Command("order"))
async def cmd_order(message: Message):
    steps = PRODUCTION_PROCESS["steps"]
    text = "📋 <b>Как заказать мебель в «АбаканМебель»:</b>\n\n"
    for step in steps:
        text += f"<b>{step['step']}. {step['name']}</b>\n{step['description']}\n"
        if step.get('duration'):
            text += f"⏱ {step['duration']}\n"
        text += "\n"

    text += "\n<b>Наши преимущества:</b>\n"
    for adv in PRODUCTION_PROCESS["advantages"][:4]:
        text += f"✅ {adv}\n"

    phone = config.PHONE
    text += f'\n📞 Позвоните: <b><a href="tel:+79134483717">{phone}</a></b>'
    text += f'\n🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>'
    text += f'\n💬 <a href="https://wa.me/79134483717">WhatsApp</a>'

    await message.answer(text)


# ── /prices ────────────────────────────────────────────────────────────────────

@chat_router.message(Command("prices"))
async def cmd_prices(message: Message):
    text = (
        "💰 <b>Ориентировочные цены:</b>\n\n"
        "🛋 Кухни на заказ — от 45 000 руб\n"
        "🚪 Шкафы-купе — от 25 000 руб\n"
        "🛏 Кровати — от 18 000 руб\n"
        "🪑 Детская мебель — от 15 000 руб\n"
        "🏠 Прихожие — от 12 000 руб\n\n"
        "<i>Точная стоимость рассчитывается после бесплатного замера.</i>\n\n"
        f'📞 <a href="tel:+79134483717">{config.PHONE}</a>\n'
        f'🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>\n'
        f'📍 Бесплатный замер по всей Хакасии'
    )
    await message.answer(text)


# ── /delivery ──────────────────────────────────────────────────────────────────

@chat_router.message(Command("delivery"))
async def cmd_delivery(message: Message):
    text = (
        "🚚 <b>Доставка и сборка:</b>\n\n"
        "✅ По Абакану — <b>БЕСПЛАТНО</b>\n"
        "✅ По Хакасии (Черногорск, Саяногорск и др.) — по договорённости\n"
        "✅ Профессиональная сборка — включена в стоимость\n"
        "✅ Установка фурнитуры и настройка механизмов\n\n"
        f'📞 <a href="tel:+79134483717">{config.PHONE}</a>\n'
        f'🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>'
    )
    await message.answer(text)


# ── /clear ─────────────────────────────────────────────────────────────────────

@chat_router.message(Command("clear"))
async def cmd_clear(message: Message):
    count = await clear_chat_history(message.from_user.id)
    await message.answer(f"🗑 История чата очищена ({count} сообщений удалено). Начнём сначала! 😊")


# ── Text messages — main chat handler ─────────────────────────────────────────

@chat_router.message(F.text)
async def handle_text_message(message: Message):
    # ── Защита от циклов: игнорировать сообщения от других ботов ──
    if config.GROUP_IGNORE_BOTS and message.from_user and message.from_user.is_bot:
        return

    # Rate check
    if not _check_message_rate(message.from_user.id):
        return

    # Block check
    if await is_user_blocked(message.from_user.id):
        return

    # Update user
    await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
    )

    text = message.text.strip()
    if not text:
        return

    # ── FAST PATH: "ты бот?" detection — respond IMMEDIATELY ──
    if _is_bot_question(text):
        # Respond directly without AI to avoid model accidentally admitting
        from bot.dasha import DASHA_PHRASES
        response_text = random.choice(DASHA_PHRASES["not_a_bot"])
        await add_chat_message(message.from_user.id, "user", text)
        await add_chat_message(message.from_user.id, "assistant", response_text)
        await message.answer(response_text)
        return

    # Dedup check — skip if same message was recently processed
    cached_reply = dedup_check(message.from_user.id, text)
    if cached_reply:
        await message.answer(cached_reply)
        return

    chat_type = message.chat.type
    is_mentioned = _is_mentioned(message)

    # ═══ GROUP / SUPERGROUP LOGIC ═══
    # Даша отвечает на ВСЕ события во ВСЕХ чатах и группах (бесплатная реклама,
    # бот не загружен). Per-chat cooldown защищает от флуда только для
    # неприоритетных сообщений — упоминания/replies/мебель отвечают ВСЕГДА.
    if chat_type in ("group", "supergroup"):
        is_furniture = _has_furniture_topic(text)
        is_conversational_msg = _is_conversational(text)
        is_priority = is_mentioned or _is_reply_to_bot(message) or is_furniture

        if config.GROUP_RESPOND_ALL:
            # Отвечать на КАЖДОЕ событие. Приоритетные — всегда.
            # Неприоритетные — с per-chat cooldown (защита от флуда в активных чатах).
            if _chat_cooldown_active(message.chat.id, is_priority):
                # Cooldown активен — только реакция-эмодзи на мебельные сообщения
                if is_furniture:
                    await _try_add_reaction(message)
                return
            should_respond = True
        else:
            # Старый режим (вероятностный) — на случай если GROUP_RESPOND_ALL=false
            if is_mentioned or _is_reply_to_bot(message):
                should_respond = True
            elif is_furniture:
                should_respond = random.random() < 0.70
            elif is_conversational_msg:
                should_respond = random.random() < 0.30
            else:
                should_respond = random.random() < 0.15

            if not should_respond:
                if is_furniture:
                    await _try_add_reaction(message)
                return

        # В группе используем COMMENT route
        route_type = "comment"
        # Адаптивный лимит символов — зависит от контекста
        max_chars = _estimate_response_length(text, chat_type, is_mentioned)
    else:
        # Private chat — полная консультация
        route_type = "chat"
        max_chars = 4000

    # Show typing
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    # Build enhanced context from knowledge base
    knowledge_context = build_knowledge_context(text)
    # Limit knowledge context to prevent prompt bloat
    if knowledge_context and len(knowledge_context) > 500:
        knowledge_context = knowledge_context[:500] + "..."

    # Build dynamic context additions (NOT the full persona — router uses compact prompt)
    # These additions are APPENDED to the router's compact system prompt
    context_additions = ""

    # ── USER MEMORY: tell Dasha who she's talking to ──
    user_name = message.from_user.first_name or "человек"
    user_username = message.from_user.username or ""
    user_id = message.from_user.id
    # Count previous messages for context
    from bot.database import get_chat_history
    user_history = await get_chat_history(user_id, limit=6)
    msg_count = len(user_history) // 2  # rough estimate of exchanges

    if chat_type == "private":
        # In private chat — Dasha knows the user personally
        user_identity = f"{user_name}"
        if user_username:
            user_identity += f" (@{user_username})"
        if msg_count > 10:
            context_additions += f"\n\nЭто {user_identity} — ваш постоянный клиент (уже {msg_count} сообщений в чате). "
            if user_history:
                # Extract last topic from recent messages
                last_topics = [m[2] for m in user_history[-4:] if m[1] == "user" and len(m[2]) > 5]
                if last_topics:
                    context_additions += "Последние темы: " + ", ".join(t[:50] for t in last_topics[-2:])
        elif msg_count > 0:
            context_additions += f"\n\nВы общаетесь с {user_identity}. "
        else:
            context_additions += f"\n\nНовый собеседник — {user_identity}. Будь дружелюбной! "
    else:
        # In group — Dasha knows the user by name
        context_additions += chat_type_context(message)
        chat_name = message.chat.title or "группа"
        user_identity = f"{user_name}"
        if user_username:
            user_identity += f" (@{user_username})"
        context_additions += f"\n\nТы в чате '{chat_name}'. Пишет {user_identity}."

        # More detailed instructions for mentioned
        if is_mentioned:
            context_additions += (
                " Тебя УПОМЯНУЛИ — отвечай развёрнуто, как эксперт-дизайнер. "
                "Будь живой, покажи интерес к вопросу."
            )
        elif _is_reply_to_bot(message):
            context_additions += (
                " Это ответ на твоё сообщение — продолжи диалог естественно."
            )
        elif _is_conversational(text):
            context_additions += (
                " Короткая разговорная фраза — отреагируй живо. 1-2 предложения."
            )
        else:
            context_additions += (
                " Комментируешь по делу, но живо — 1-3 предложения. "
                "Добавь экспертное мнение как дизайнер."
            )

    # Add knowledge context for both private and group
    if knowledge_context:
        context_additions += f"\n\nРелевантные знания:\n{knowledge_context}"

    # Add phone info if user asks
    phone = config.PHONE
    if phone and detect_phone_request(text):
        context_additions += f"\n\nВАЖНО: Пользователь спрашивает телефон. Дай номер: {phone} (WhatsApp: wa.me/79134483717)"

    # Add delivery info if relevant
    if detect_delivery_interest(text):
        context_additions += "\n\nINFO: Доставка по Абакану — БЕСПЛАТНО. По Хакасии — по договорённости. Сборка включена."

    # Add price info context
    if detect_price_interest(text):
        context_additions += "\n\nINFO: Кухни от 45 000 руб. Для точного расчёта нужен бесплатный замер. Направь на abakanmebel.online."

    # Direct phone requests — answer immediately (skip AI for speed)
    if detect_phone_request(text) and phone:
        await add_chat_message(message.from_user.id, "user", text)
        phone_response = random.choice(DASHA_PHRASES["phone_request"]).format(phone=phone)
        # Add WhatsApp and site info
        phone_response += '\n\n💬 <a href="https://wa.me/79134483717">WhatsApp</a>\n🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>'
        await message.answer(phone_response[:max_chars])
        await add_chat_message(message.from_user.id, "assistant", phone_response)
        return

    # Get AI response (router uses compact system prompt + our additions)
    response = await ai_router.chat(
        user_id=message.from_user.id,
        message=text,
        system_prompt=context_additions,  # Appended to compact prompt by router
        route_type=route_type,
        save_history=(chat_type == "private"),
    )

    # Handle response
    if response.ok and response.text:
        reply = response.text.strip()
        dedup_store(message.from_user.id, text, reply)
        # Truncate if too long for this chat type (HTML-safe)
        if len(reply) > max_chars:
            reply = _safe_truncate(reply, max_chars)
            logger.info(f"Truncated response to {len(reply)} chars for {chat_type}")
        await message.answer(reply)
        # Отметить, что Даша ответила в этом чате (для per-chat cooldown)
        if chat_type in ("group", "supergroup"):
            _mark_chat_responded(message.chat.id)
        # Add reaction to original message in groups
        await _try_add_reaction(message)
    elif response.error and "rate limit" not in str(response.error).lower():
        # Static fallback
        fallback = _get_static_response(text)
        if fallback:
            await message.answer(fallback[:max_chars])
            if chat_type in ("group", "supergroup"):
                _mark_chat_responded(message.chat.id)
        else:
            logger.error(f"AI error for user {message.from_user.id}: {response.error}")
            # Last-resort message — в группе не отправляем (чтобы не спамить)
            if chat_type == "private":
                await message.answer(
                    "Извините, не смогла обработать запрос прямо сейчас 😔 "
                    f"Позвоните нам: {config.PHONE} или напишите на {config.WEBSITE}"
                )
            else:
                # В группе — тихо реагируем эмодзи, не спамим ошибками
                await _try_add_reaction(message)
                _mark_chat_responded(message.chat.id)


# ── Photo messages ─────────────────────────────────────────────────────────────

@chat_router.message(F.photo)
async def handle_photo(message: Message):
    # Защита от ботов
    if config.GROUP_IGNORE_BOTS and message.from_user and message.from_user.is_bot:
        return

    chat_type = message.chat.type
    caption = message.caption or ""
    is_mentioned = _is_mentioned(message)
    is_reply = _is_reply_to_bot(message)
    is_furniture = _has_furniture_topic(caption) if caption else False

    # In groups: respond to photos if mentioned, replied, furniture-related caption,
    # or (when GROUP_RESPOND_ALL) any photo subject to per-chat cooldown.
    if chat_type in ("group", "supergroup"):
        is_priority = is_mentioned or is_reply or is_furniture
        if config.GROUP_RESPOND_ALL:
            if _chat_cooldown_active(message.chat.id, is_priority):
                if is_furniture:
                    await _try_add_reaction(message)
                return
        else:
            if not is_priority:
                return

    await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    # Build prompt from caption
    if caption:
        prompt = f"Пользователь прислал фото с подписью: «{caption}». Отреагируй и помоги."
    else:
        prompt = "Пользователь прислал фото без подписи. Отреагируй дружелюбно и спроси, что хотелось бы изменить в интерьере."

    response = await ai_router.chat(
        user_id=message.from_user.id,
        message=prompt,
        route_type="comment" if chat_type in ("group", "supergroup") else "chat",
    )

    if response.ok and response.text:
        max_chars = adaptive_max_chars(chat_type) if chat_type != "private" else 4000
        reply = _safe_truncate(response.text.strip(), max_chars)
        await message.answer(reply)
        if chat_type in ("group", "supergroup"):
            _mark_chat_responded(message.chat.id)
        await _try_add_reaction(message)
    else:
        if chat_type == "private":
            await message.answer(
                "Спасибо за фото! 📸 Если это ваш интерьер — опишите что хотите изменить, "
                "и я предложу варианты мебели и дизайна! 😊\n\n"
                f'📞 <a href="tel:+79134483717">{config.PHONE}</a>\n🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>'
            )
        else:
            # В группе — тихая реакция
            await _try_add_reaction(message)
            _mark_chat_responded(message.chat.id)


# ── Stickers / animations / other media ───────────────────────────────────────

@chat_router.message(F.sticker | F.animation | F.video | F.document)
async def handle_media(message: Message):
    """Даша реагирует на стикеры, гифки, видео и документы в группах (бесплатная реклама)."""
    if config.GROUP_IGNORE_BOTS and message.from_user and message.from_user.is_bot:
        return

    chat_type = message.chat.type
    if chat_type not in ("group", "supergroup"):
        # В личке — короткий ответ
        if message.from_user:
            await get_or_create_user(
                user_id=message.from_user.id,
                username=message.from_user.username or "",
                first_name=message.from_user.first_name or "",
            )
        await message.answer(
            "Классный медиафайл! 😊 Напишите текстом — что вас интересует по мебели или дизайну? "
            f'Или позвоните: <a href="tel:+79134483717">{config.PHONE}</a>'
        )
        return

    # В группе — реагируем эмодзи (если позволяет cooldown), иногда комментируем
    is_mentioned = _is_mentioned(message)
    is_reply = _is_reply_to_bot(message)
    is_priority = is_mentioned or is_reply

    if config.GROUP_RESPOND_ALL:
        if _chat_cooldown_active(message.chat.id, is_priority):
            await _try_add_reaction(message)
            return
    else:
        if not is_priority:
            await _try_add_reaction(message)
            return

    # На стикер/медиа — короткий живой комментарий (30% chance, иначе только реакция)
    if is_priority or random.random() < 0.30:
        caption = message.caption or ""
        media_type = "стикер" if message.sticker else ("гифку" if message.animation else ("видео" if message.video else "документ"))
        prompt = f"В группе прислали {media_type}. Реагируй коротко и живо (1 предложение)." + (f" Подпись: «{caption}»." if caption else "")
        response = await ai_router.chat(
            user_id=message.from_user.id,
            message=prompt,
            route_type="comment",
        )
        if response.ok and response.text:
            await message.answer(_safe_truncate(response.text.strip(), adaptive_max_chars(chat_type)))
            _mark_chat_responded(message.chat.id)
        await _try_add_reaction(message)
    else:
        await _try_add_reaction(message)


# ── Voice messages ────────────────────────────────────────────────────────────

@chat_router.message(F.voice)
async def handle_voice(message: Message):
    if config.GROUP_IGNORE_BOTS and message.from_user and message.from_user.is_bot:
        return
    chat_type = message.chat.type
    if chat_type == "private":
        await message.answer(
            "🎤 Голосовые пока не поддерживаю — напишите текстом, и я с удовольствием помогу! 😊\n\n"
            f'📞 Или позвоните: <a href="tel:+79134483717">{config.PHONE}</a>'
        )
    else:
        # В группе — тихая реакция, не спамим сообщением про голосовые
        await _try_add_reaction(message)


# ── Static Fallback Responses ──────────────────────────────────────────────────

def _get_static_response(text: str) -> Optional[str]:
    """Provide static responses when AI is unavailable."""
    text_lower = text.lower()

    # Phone request with known phone
    if detect_phone_request(text) and config.PHONE:
        resp = random.choice(DASHA_PHRASES["phone_request"]).format(phone=config.PHONE)
        return resp + '\n\n💬 <a href="https://wa.me/79134483717">WhatsApp</a>\n🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>'

    # About Dasha
    if any(kw in text_lower for kw in ["кто ты", "о себе", "что ты умеешь", "расскажи о себе"]):
        return (
            "👋 Я Даша — дизайнер мебели из Абакана. Помогаю подобрать мебель, "
            "спроектировать интерьер, выбрать материалы. "
            "Работаю в abakanmebel.online 🏠\n\n"
            f'📞 <a href="tel:+79134483717">{config.PHONE}</a>\n'
            f'🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>'
        )

    # Order process
    if any(kw in text_lower for kw in ["как заказать", "заказать мебель", "процесс заказа"]):
        return (
            "📋 Заказ мебели — просто!\n\n"
            "1️⃣ Позвоните или напишите нам\n"
            "2️⃣ Бесплатный замер по Хакасии\n"
            "3️⃣ 3D-дизайн проекта (2-5 дней)\n"
            "4️⃣ Производство 14-31 день\n"
            "5️⃣ Доставка и сборка\n\n"
            f'📞 <a href="tel:+79134483717">{config.PHONE}</a>\n'
            f'🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>'
        )

    # Greetings
    if any(kw in text_lower for kw in ["привет", "здравствуй", "добрый день", "хей", "хай"]):
        return random.choice(DASHA_PHRASES["greetings"])

    # Delivery
    if detect_delivery_interest(text):
        return (
            "🚚 Доставка мебели:\n\n"
            "✅ По Абакану — БЕСПЛАТНО\n"
            "✅ По Хакасии (Черногорск, Саяногорск и др.) — по договорённости\n"
            "✅ Профессиональная сборка включена\n\n"
            f'📞 <a href="tel:+79134483717">{config.PHONE}</a>\n'
            f'🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>'
        )

    # Prices
    if detect_price_interest(text):
        return (
            "💰 Ориентировочные цены:\n"
            "🛋 Кухни — от 45 000 руб\n"
            "🚪 Шкафы-купе — от 25 000 руб\n"
            "🛏 Кровати — от 18 000 руб\n\n"
            "Точная стоимость — после бесплатного замера.\n\n"
            f'📞 <a href="tel:+79134483717">{config.PHONE}</a>\n🌐 <a href="https://abakanmebel.online">abakanmebel.online</a>'
        )

    return None