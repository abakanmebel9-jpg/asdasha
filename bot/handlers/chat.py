"""
Chat Handler — Main user interaction with Dasha AI.
Handles private chats, group chats, comments, photo analysis.
Даша — дизайнер мебели: консультирует по дизайну, материалам, заказам.

РЕЖИМЫ:
  - Личный чат (private): полная консультация, история диалога
  - Группа/супергруппа: отвечает на упоминания @asdasha_bot и на сообщения
    с мебельной/дизайнерской тематикой (кратко, как комментарий)
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

from bot.config import config, persona
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
from bot.optimizations import adaptive_max_chars, chat_type_context

logger = logging.getLogger("dasha.handlers.chat")

chat_router = Router()


# ── Rate limiting ──────────────────────────────────────────────────────────────

_user_last_message: dict = {}


def _check_message_rate(user_id: int, min_interval: float = 2.0) -> bool:
    now = time.time()
    last = _user_last_message.get(user_id, 0)
    if now - last < min_interval:
        return False
    _user_last_message[user_id] = now
    return True


# ── Group trigger detection ───────────────────────────────────────────────────
# В группах Даша отвечает только когда:
#   1) Её упомянули (@asdasha_bot или "даша")
#   2) Сообщение содержит мебельную/дизайнерскую тематику
#   3) Это reply на её сообщение

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


def _is_reply_to_bot(message: Message) -> bool:
    """Проверяет, является ли сообщение ответом на сообщение бота."""
    if not message.reply_to_message:
        return False
    return message.reply_to_message.from_user and message.reply_to_message.from_user.is_bot


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
        f"📞 +7 (913) 448-37-17\n"
        f"🌐 abakanmebel.online"
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
        f"Работаю в компании «АбаканМебель» — 25 лет опыта, 426+ проектов "
        f"по Хакасии. Помогаю подобрать мебель, спроектировать интерьер, "
        f"выбрать материалы и фурнитуру.\n\n"
        f"📌 Веду канал: {config.CHANNEL_USERNAME}\n"
        f"🌐 Сайт: {config.WEBSITE}\n"
        f"📞 Телефон: {config.PHONE}\n"
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
        f"📞 <b>Телефон компании «АбаканМебель»:</b>\n"
        f"<b>{phone}</b>\n\n"
        f"💬 WhatsApp: wa.me/79134483717\n"
        f"🌐 Сайт: {config.WEBSITE}\n"
        f"📍 Адрес: {config.ADDRESS}\n"
        f"🕐 Часы работы: {config.WORKING_HOURS}\n\n"
        f"Позвоните или напишите — проконсультирую! 😊"
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
    text += f"\n📞 Позвоните: <b>{phone}</b>"
    text += f"\n🌐 Или напишите на {config.WEBSITE}"
    text += f"\n💬 WhatsApp: wa.me/79134483717"

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
        f"📞 {config.PHONE}\n"
        f"🌐 {config.WEBSITE}\n"
        f"📍 Бесплатный замер по всей Хакасии"
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
        f"📞 {config.PHONE}\n"
        f"🌐 {config.WEBSITE}"
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

    chat_type = message.chat.type

    # ═══ GROUP / SUPERGROUP LOGIC ═══
    # В группах Даша отвечает только на:
    #   - упоминания (@asdasha_bot / "даша")
    #   - сообщения с мебельной/дизайнерской тематикой
    #   - replies на свои сообщения
    if chat_type in ("group", "supergroup"):
        should_respond = (
            _is_mentioned(message)
            or _has_furniture_topic(text)
            or _is_reply_to_bot(message)
        )
        if not should_respond:
            return  # Молча игнорируем нерелевантные сообщения в группах

        # В группе используем COMMENT route — краткие ответы
        route_type = "comment"
        # Адаптивный лимит символов для группы
        max_chars = adaptive_max_chars(chat_type)
    else:
        # Private chat — полная консультация
        route_type = "chat"
        max_chars = 4000

    # Show typing
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    # Build enhanced context from knowledge base
    knowledge_context = build_knowledge_context(text)

    # Enhance system prompt with knowledge if found
    system_prompt = persona["system_prompt"]
    if knowledge_context:
        system_prompt += f"\n\nРелевантные знания из твоей базы:\n{knowledge_context}"

    # Add group context if in group
    if chat_type in ("group", "supergroup"):
        system_prompt += chat_type_context(message)
        system_prompt += (
            "\n\nТы комментируешь в группе. Отвечай КРАТКО (1-5 предложений), "
            "по делу, как полезный комментарий. Не пиши длинных лекций. "
            "Если уместно — предложи позвонить или зайти на сайт."
        )

    # Add phone info to system prompt if user asks
    phone = config.PHONE
    if phone and detect_phone_request(text):
        system_prompt += f"\n\nВАЖНО: Пользователь спрашивает телефон. Дай номер: {phone} (WhatsApp: wa.me/79134483717)"

    # Add delivery info if relevant
    if detect_delivery_interest(text):
        system_prompt += "\n\nINFO: Доставка по Абакану — БЕСПЛАТНО. По Хакасии (Черногорск, Саяногорск и др.) — по договорённости. Сборка включена."

    # Add price info context
    if detect_price_interest(text):
        system_prompt += "\n\nINFO: Кухни от 45 000 руб. Для точного расчёта нужен бесплатный замер (по всей Хакасии). Направь клиента на сайт abakanmebel.online или предложи вызвать замерщика."

    # Direct phone requests — answer immediately (skip AI for speed)
    if detect_phone_request(text) and phone:
        await add_chat_message(message.from_user.id, "user", text)
        phone_response = random.choice(DASHA_PHRASES["phone_request"]).format(phone=phone)
        # Add WhatsApp and site info
        phone_response += "\n\n💬 WhatsApp: wa.me/79134483717\n🌐 abakanmebel.online"
        await message.answer(phone_response[:max_chars])
        await add_chat_message(message.from_user.id, "assistant", phone_response)
        return

    # Get AI response
    response = await ai_router.chat(
        user_id=message.from_user.id,
        message=text,
        system_prompt=system_prompt,
        route_type=route_type,
        save_history=(chat_type == "private"),
    )

    # Handle response
    if response.ok and response.text:
        reply = response.text.strip()
        # Truncate if too long for this chat type
        if len(reply) > max_chars:
            reply = reply[:max_chars - 3] + "…"
            logger.info(f"Truncated response to {max_chars} chars for {chat_type}")
        await message.answer(reply)
    elif response.error and "rate limit" not in str(response.error).lower():
        # Static fallback
        fallback = _get_static_response(text)
        if fallback:
            await message.answer(fallback[:max_chars])
        else:
            logger.error(f"AI error for user {message.from_user.id}: {response.error}")
            # Last-resort message
            await message.answer(
                "Извините, не смогла обработать запрос прямо сейчас 😔 "
                f"Позвоните нам: {config.PHONE} или напишите на {config.WEBSITE}"
            )


# ── Photo messages ─────────────────────────────────────────────────────────────

@chat_router.message(F.photo)
async def handle_photo(message: Message):
    # In groups, only respond to photos if mentioned or replied to
    chat_type = message.chat.type
    if chat_type in ("group", "supergroup"):
        if not (_is_mentioned(message) or _is_reply_to_bot(message)):
            return

    await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )

    caption = message.caption or ""
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    # For now, respond with text about the photo
    prompt = f"Пользователь прислал фото. {'Подпись: ' + caption if caption else 'Без подписи.'}"

    response = await ai_router.chat(
        user_id=message.from_user.id,
        message=prompt,
        route_type="chat",
    )

    if response.ok and response.text:
        max_chars = adaptive_max_chars(chat_type)
        await message.answer(response.text.strip()[:max_chars])
    else:
        await message.answer(
            "Спасибо за фото! 📸 Если это ваш интерьер — опишите что хотите изменить, "
            "и я предложу варианты мебели и дизайна! 😊\n\n"
            f"📞 {config.PHONE}\n🌐 abakanmebel.online"
        )


# ── Voice messages ────────────────────────────────────────────────────────────

@chat_router.message(F.voice)
async def handle_voice(message: Message):
    await message.answer(
        "🎤 Голосовые пока не поддерживаю — напишите текстом, и я с удовольствием помогу! 😊\n\n"
        f"📞 Или позвоните: {config.PHONE}"
    )


# ── Static Fallback Responses ──────────────────────────────────────────────────

def _get_static_response(text: str) -> Optional[str]:
    """Provide static responses when AI is unavailable."""
    text_lower = text.lower()

    # Phone request with known phone
    if detect_phone_request(text) and config.PHONE:
        resp = random.choice(DASHA_PHRASES["phone_request"]).format(phone=config.PHONE)
        return resp + "\n\n💬 WhatsApp: wa.me/79134483717\n🌐 abakanmebel.online"

    # About Dasha
    if any(kw in text_lower for kw in ["кто ты", "о себе", "что ты умеешь", "расскажи о себе"]):
        return (
            "👋 Я Даша — дизайнер мебели из Абакана. Помогаю подобрать мебель, "
            "спроектировать интерьер, выбрать материалы. "
            "Работаю в abakanmebel.online 🏠\n\n"
            f"📞 {config.PHONE}\n"
            f"🌐 {config.WEBSITE}"
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
            f"📞 {config.PHONE}\n"
            f"🌐 {config.WEBSITE}"
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
            f"📞 {config.PHONE}\n"
            f"🌐 {config.WEBSITE}"
        )

    # Prices
    if detect_price_interest(text):
        return (
            "💰 Ориентировочные цены:\n"
            "🛋 Кухни — от 45 000 руб\n"
            "🚪 Шкафы-купе — от 25 000 руб\n"
            "🛏 Кровати — от 18 000 руб\n\n"
            "Точная стоимость — после бесплатного замера.\n\n"
            f"📞 {config.PHONE}\n🌐 {config.WEBSITE}"
        )

    return None