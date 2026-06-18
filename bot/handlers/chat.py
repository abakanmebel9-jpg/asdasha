"""
Chat Handler — Main user interaction with Dasha AI.
Handles private chats, group chats, comments, photo analysis.
Dasha is a furniture designer — consults on design, materials, orders.
"""

import re
import random
import logging
from typing import Optional

from aiogram import Router, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, PhotoSize
from aiogram.enums import ChatAction

from bot.config import config, persona
from bot.database import (
    get_or_create_user, is_user_blocked, add_chat_message,
    clear_chat_history, check_rate_limit,
)
from bot.dasha import (
    DASHA_PHRASES, identify_furniture_topic, get_material_advice,
    get_room_recommendations, get_style_info, detect_phone_request,
    detect_delivery_interest, detect_price_interest, build_knowledge_context,
    FURNITURE_MATERIALS, FURNITURE_STYLES, STANDARD_SIZES,
    ABAKAN_KNOWLEDGE, PRODUCTION_PROCESS,
)
from ai.router import ai_router

logger = logging.getLogger("dasha.handlers.chat")

chat_router = Router()


# ── Rate limiting ──────────────────────────────────────────────────────────────

_user_last_message: dict = {}

def _check_message_rate(user_id: int, min_interval: float = 2.0) -> bool:
    now = time.time() if 'time' in dir() else __import__('time').time()
    last = _user_last_message.get(user_id, 0)
    if now - last < min_interval:
        return False
    _user_last_message[user_id] = now
    return True


import time


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
        "Команды:\n"
        "/start — начать\n"
        "/help — эта справка\n"
        "/clear — очистить историю чата\n"
        "/about — о Даше\n"
        "/phone — телефон компании\n"
        "/order — как заказать мебель\n"
    )
    await message.answer(help_text)


# ── /about ─────────────────────────────────────────────────────────────────────

@chat_router.message(Command("about"))
async def cmd_about(message: Message):
    about = (
        f"{''.join(random.choice(DASHA_PHRASES['about_self']))}\n\n"
        f"📌 Веду канал: {config.CHANNEL_USERNAME}\n"
        f"🌐 Сайт: {config.WEBSITE}\n"
        f"📍 Абакан, Республика Хакасия\n"
        f"🤖 Бот: {config.BOT_USERNAME}"
    )
    await message.answer(about)


# ── /phone ─────────────────────────────────────────────────────────────────────

@chat_router.message(Command("phone"))
async def cmd_phone(message: Message):
    phone = config.PHONE
    if phone:
        text = f"📞 Телефон компании:\n<b>{phone}</b>\n\nПозвоните или напишите — проконсультирую! 😊"
    else:
        text = f"📞 Свяжитесь с нами:\n🌐 {config.WEBSITE}\n\nИли напишите сюда — помогу с выбором! 😊"
    await message.answer(text)


# ── /order ─────────────────────────────────────────────────────────────────────

@chat_router.message(Command("order"))
async def cmd_order(message: Message):
    steps = PRODUCTION_PROCESS["steps"]
    text = "📋 <b>Как заказать мебель:</b>\n\n"
    for step in steps:
        text += f"<b>{step['step']}. {step['name']}</b>\n{step['description']}\n"
        if step.get('duration'):
            text += f"⏱ {step['duration']}\n"
        text += "\n"

    text += "\n<b>Наши преимущества:</b>\n"
    for adv in PRODUCTION_PROCESS["advantages"][:4]:
        text += f"✅ {adv}\n"

    phone = config.PHONE
    if phone:
        text += f"\n📞 Позвоните: <b>{phone}</b>"
    text += f"\n🌐 Или напишите на {config.WEBSITE}"

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

    # Show typing
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    # Build enhanced context from knowledge base
    knowledge_context = build_knowledge_context(text)

    # Enhance system prompt with knowledge if found
    system_prompt = persona["system_prompt"]
    if knowledge_context:
        system_prompt += f"\n\nРелевантные знания из твоей базы:\n{knowledge_context}"

    # Add phone info to system prompt if user asks
    phone = config.PHONE
    if phone and detect_phone_request(text):
        system_prompt += f"\n\nВАЖНО: Пользователь спрашивает телефон. Дай номер: {phone}"

    # Add delivery info if relevant
    if detect_delivery_interest(text):
        system_prompt += "\n\nINFO: Доставка по Абакану — БЕСПЛАТНО. По Хакасии — по договорённости. Сборка включена."

    # Add price info context
    if detect_price_interest(text):
        system_prompt += "\n\nINFO: Для точного расчёта стоимости нужен замер. Направь клиента на сайт abakanmebel.online или предложи вызвать бесплатного замерщика."

    # Direct phone requests — answer immediately
    if detect_phone_request(text) and phone:
        await add_chat_message(message.from_user.id, "user", text)
        phone_response = random.choice(DASHA_PHRASES["phone_request"]).format(phone=phone)
        await message.answer(phone_response)
        await add_chat_message(message.from_user.id, "assistant", phone_response)
        return

    # Determine route type
    chat_type = message.chat.type
    route_type = "chat" if chat_type == "private" else "comment"

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
        # Truncate if too long
        if len(reply) > 4000:
            reply = reply[:3997] + "..."
        await message.answer(reply)
    elif response.error and "rate limit" not in str(response.error).lower():
        # Static fallback
        fallback = _get_static_response(text)
        if fallback:
            await message.answer(fallback)
        else:
            logger.error(f"AI error for user {message.from_user.id}: {response.error}")


# ── Photo messages ─────────────────────────────────────────────────────────────

@chat_router.message(F.photo)
async def handle_photo(message: Message):
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
        await message.answer(response.text.strip()[:4000])
    else:
        await message.answer(
            "Спасибо за фото! 📸 Если это ваш интерьер — описать что хотите изменить, "
            "и я предложу варианты мебели и дизайна! 😊"
        )


# ── Voice messages ────────────────────────────────────────────────────────────

@chat_router.message(F.voice)
async def handle_voice(message: Message):
    await message.answer(
        "🎤 Голосовые пока не поддерживаю — напишите текстом, и я с удовольствием помогу! 😊"
    )


# ── Static Fallback Responses ──────────────────────────────────────────────────

def _get_static_response(text: str) -> Optional[str]:
    """Provide static responses when AI is unavailable."""
    text_lower = text.lower()

    # Phone request with known phone
    if detect_phone_request(text) and config.PHONE:
        return random.choice(DASHA_PHRASES["phone_request"]).format(phone=config.PHONE)

    # About Dasha
    if any(kw in text_lower for kw in ["кто ты", "о себе", "что ты умеешь", "расскажи о себе"]):
        return random.choice(DASHA_PHRASES["about_self"])

    # Order process
    if any(kw in text_lower for kw in ["как заказать", "заказать мебель", "процесс заказа"]):
        return (
            "📋 Заказ мебели — просто!\n\n"
            "1️⃣ Позвоните или напишите нам\n"
            "2️⃣ Бесплатный замер в Абакане\n"
            "3️⃣ 3D-дизайн проекта\n"
            "4️⃣ Производство 7-21 день\n"
            "5️⃣ Доставка и сборка\n\n"
            f"📞 {config.PHONE or config.WEBSITE}"
        )

    # Greetings
    if any(kw in text_lower for kw in ["привет", "здравствуй", "добрый день", "хей", "хай"]):
        return random.choice(DASHA_PHRASES["greetings"])

    # Delivery
    if detect_delivery_interest(text):
        return (
            "🚚 Доставка мебели:\n\n"
            "✅ По Абакану — БЕСПЛАТНО\n"
            "✅ По Хакасии — по договорённости\n"
            "✅ Профессиональная сборка включена\n\n"
            f"📞 Позвоните: {config.PHONE or config.WEBSITE}"
        )

    return None