"""Даша Private chat handler — мебельная консультация с памятью.

Бот общается ТОЛЬКО по теме корпусной мебели, материалов, фурнитуры
и дизайна интерьеров. Посторонние темы вежливо уводит к мебели.
"""
import asyncio, logging, random
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.enums import ChatAction
from aiogram.filters import Command
from bot.config import config
from bot.mood import update_mood_from_message, current_mood_descriptor
from bot.persona import PERSONA_PROMPT
from bot import database as db
from bot.context import build_private_context, build_user_profile, extract_and_store_facts
from bot.dasha import build_knowledge_context
from ai import client as ai_client

logger = logging.getLogger("dasha.chat")
chat_router = Router()
_MAX_HISTORY = 16

PHONE = config.PHONE or "+7 (913) 448-37-17"
PHONE_DIGITS = "".join(ch for ch in PHONE if ch.isdigit())


def _contacts_keyboard() -> InlineKeyboardMarkup:
    """Кнопки связи: звонок, WhatsApp, сайт, канал + лид-инструменты."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📐 Бесплатный замер", callback_data="measure:start"),
         InlineKeyboardButton(text="🧮 Калькулятор цены", callback_data="calc:restart")],
        [InlineKeyboardButton(text="📞 Позвонить Даше", url=f"tel:+{PHONE_DIGITS}"),
         InlineKeyboardButton(text="💬 WhatsApp", url=f"https://wa.me/{PHONE_DIGITS}")],
        [InlineKeyboardButton(text="🌐 abakanmebel.online", url="https://abakanmebel.online"),
         InlineKeyboardButton(text="📺 Канал @abakan_mebel", url="https://t.me/abakan_mebel")],
    ])

_HELP_TEXT = (
    "👋 Я Даша — дизайнер корпусной мебели из Абакана (abakanmebel.online).\n\n"
    "🛋 Чем помогу:\n"
    "• Кухни, шкафы-купе, прихожие, гардеробные на заказ\n"
    "• Подбор материалов: массив, МДФ, ЛДСП, фурнитура\n"
    "• Идеи и планировка интерьера, советы по стилям\n"
    "• Фото помещения — подскажу решения 📷\n"
    "• Голосовые — тоже понимаю 🎤\n\n"
    "Мои команды (работают и в группах):\n"
    "/consult — кнопки связи: звонок, WhatsApp, сайт\n"
    "/measure — заявка на бесплатный замер 📐\n"
    "/calc — калькулятор стоимости мебели 🧮\n"
    "/catalog — популярные решения с ценами\n"
    "/price — ориентиры по ценам\n"
    "/quiz — квиз «Подбери свою мебель» 🧩\n"
    "/inspiration — вдохновение: 2 примера под ваше помещение 🎨\n"
    "/reviews — отзывы клиентов ⭐\n"
    "/care — советы по уходу за мебелью 🧼\n"
    "/process — этапы работы: от заявки до гарантии 🔧\n"
    "/faq — частые вопросы ❓\n"
    "/terms — мебельный словарь: ЛДСП, МДФ, доводчики 📖\n"
    "/storage — идеи хранения по комнатам 🗃\n"
    "/about — о производстве и гарантиях 🏭\n"
    "/fact — интересный факт о мебели\n"
    "/clear — забыть историю чата\n"
    "/mood — моё настроение\n"
    "/whoami — что я о тебе помню\n\n"
    "📞 +7 (913) 448-37-17 | 🌐 abakanmebel.online | 📺 @abakan_mebel"
)

@chat_router.message(Command("start"), F.chat.type == "private")
async def cmd_start(message):
    u = message.from_user
    if u: await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    await message.reply(
        "Привет! Я Даша 😊 Дизайнер корпусной мебели из Абакана.\n\n"
        "Помогу с кухней, шкафом-купе, гардеробной — подберу материалы, "
        "спроектирую под ваши размеры. Расскажите, что планируете? 🛋"
    )

@chat_router.message(Command("help"))
async def cmd_help(message):
    await message.reply(_HELP_TEXT)

@chat_router.message(Command("clear"), F.chat.type == "private")
async def cmd_clear(message):
    n = await db.clear_private_history(message.from_user.id)
    await message.reply(f"Готово — забыла историю нашего разговора ({n} сообщений) 🧹")

@chat_router.message(Command("mood"), F.chat.type == "private")
async def cmd_mood(message):
    mood = await current_mood_descriptor()
    await message.reply(f"Сейчас я {mood} 😊")

@chat_router.message(Command("whoami"), F.chat.type == "private")
async def cmd_whoami(message):
    profile = await build_user_profile(message.from_user.id)
    if not profile: await message.reply("Пока ничего о тебе не знаю. Расскажи что-нибудь о себе 🙂")
    else: await message.reply(f"Вот что я о тебе помню:\n\n{profile}")

@chat_router.message(Command("consult"))
async def cmd_consult(message):
    """Кнопки связи — работает в личке и группах."""
    u = message.from_user
    if u and message.chat.type == "private":
        await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    await message.reply(
        f"🛋 Консультация и замер — бесплатно!\n\n"
        f"📞 {PHONE}\n"
        f"Замер по Абакану и Хакасии 🚗\n\n"
        f"Выбирай удобный способ связи:",
        reply_markup=_contacts_keyboard(),
    )

@chat_router.message(Command("catalog"))
async def cmd_catalog(message):
    """Популярные решения из каталога — в личке и группах."""
    from bot.site_content import _PRODUCTS
    lines = ["🛋 Популярные решения abakanmebel.online:\n"]
    for p in _PRODUCTS[:8]:
        lines.append(f"• {p['name']} — {p['from_price']}")
        lines.append(f"  {p['desc'][:70]}…")
    lines.append("\n📐 Точная цена — после бесплатного замера.")
    try:
        await message.reply("\n".join(lines)[:4000], reply_markup=_contacts_keyboard())
    except Exception:
        await message.reply("\n".join(lines)[:4000])

@chat_router.message(Command("price"))
async def cmd_price(message):
    """Ориентиры цен из базы знаний — личка и группы."""
    from bot.dasha import get_pricing_info
    info = get_pricing_info()
    text = f"💰 Ориентиры по ценам (корпусная мебель на заказ):\n\n{info}\n\n📐 Точная стоимость — после бесплатного замера:"
    try:
        await message.reply(text[:4000], reply_markup=_contacts_keyboard())
    except Exception:
        await message.reply(text[:4000])

@chat_router.message(Command("fact"))
async def cmd_fact(message):
    """Мебельный факт — работает и в личке, и в группах."""
    from bot.dasha import random_furniture_fact
    await message.reply(random_furniture_fact())

@chat_router.message(Command("reviews"))
async def cmd_reviews(message):
    """Отзывы клиентов — 3 шт. с ротацией, личка и группы."""
    from bot.reviews import format_reviews
    text = format_reviews(3)
    try:
        await message.reply(text[:4000], reply_markup=_contacts_keyboard())
    except Exception:
        await message.reply(text[:4000])

@chat_router.message(Command("care"))
async def cmd_care(message):
    """Совет по уходу за мебелью — 1 за вызов с ротацией, личка и группы."""
    from bot.service_info import format_care
    try:
        await message.reply(format_care()[:4000], reply_markup=_contacts_keyboard())
    except Exception:
        await message.reply(format_care()[:4000])

@chat_router.message(Command("process"))
async def cmd_process(message):
    """Этапы работы — личка и группы."""
    from bot.service_info import format_process
    try:
        await message.reply(format_process()[:4000], reply_markup=_contacts_keyboard())
    except Exception:
        await message.reply(format_process()[:4000])

@chat_router.message(Command("faq"))
async def cmd_faq(message):
    """Частые вопросы — 3 за показ с ротацией, личка и группы."""
    from bot.service_info import format_faq
    try:
        await message.reply(format_faq(3)[:4000], reply_markup=_contacts_keyboard())
    except Exception:
        await message.reply(format_faq(3)[:4000])

@chat_router.message(Command("terms"))
async def cmd_terms(message):
    """Мебельный словарь — 4 термина за показ с ротацией, личка и группы."""
    from bot.service_info import format_glossary
    try:
        await message.reply(format_glossary(4)[:4000], reply_markup=_contacts_keyboard())
    except Exception:
        await message.reply(format_glossary(4)[:4000])

@chat_router.message(Command("about"))
async def cmd_about(message):
    """О производстве и гарантиях — личка и группы."""
    from bot.service_info import format_about
    try:
        await message.reply(format_about()[:4000], reply_markup=_contacts_keyboard())
    except Exception:
        await message.reply(format_about()[:4000])

_FURNITURE_HINTS = [
    "кухн", "шкаф", "мебел", "стол", "столешниц", "фасад", "мдф", "лдсп",
    "массив", "фурнитур", "петл", "направляющ", "доводчик", "гардеробн",
    "прихож", "купе", "стенк", "комод", "тумб", "полк", "цоколь", "фартук",
    "столешница", "материал", "дизайн", "интерьер", "ремонт", "размер",
    "заказ", "цена", "стоимост", "скидк", "проект", "двери", "ящик",
]

def _is_furniture_topic(text: str) -> bool:
    t = (text or "").lower()
    return any(h in t for h in _FURNITURE_HINTS)

@chat_router.message(F.text, F.chat.type == "private")
async def handle_private_text(message):
    if message.chat.type != "private": return
    u = message.from_user
    if not u: return
    text = (message.text or "").strip()
    if not text or text.startswith("/"): return
    await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    # Нерабочее время: уведомляем об ожиданиях + ловим ночные заявки (1 раз / 6 ч)
    try:
        from bot.business_hours import maybe_off_hours_notice
        await maybe_off_hours_notice(message.bot, message.chat.id, u.id)
    except Exception as e:
        logger.debug(f"off-hours notice failed: {e}")
    # Мгновенные ответы на типовые запросы (цена/замер/сроки...) — без AI, с кнопками
    try:
        from bot.intents import try_instant_reply
        instant = await try_instant_reply(message.bot, message.chat.id, u.id, text)
        if instant:
            await db.add_private_message(u.id, "user", text)
            await db.add_private_message(u.id, "assistant", instant)
            return
    except Exception as e:
        logger.debug(f"intents check failed: {e}")
    await update_mood_from_message(text)
    mood = await current_mood_descriptor()
    name = u.first_name or u.username or ""
    try:
        for f in await extract_and_store_facts(u.id, name, text, message.chat.id): logger.info(f"FACT: {f}")
    except: pass
    history = await db.get_private_history(u.id, _MAX_HISTORY)
    await db.add_private_message(u.id, "user", text)
    user_profile = await build_user_profile(u.id)
    ctx = build_private_context(user_profile)
    system = PERSONA_PROMPT + f"\n\nТвоё текущее настроение: {mood}.\n{ctx}"
    # Knowledge base: материалы/стили/размеры по теме вопроса
    kb = build_knowledge_context(text)
    if kb:
        system += "\n\nСправка из базы знаний мебельного производства (используй, если уместно):\n" + kb
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        reply = await ai_client.chat(text, system=system, dialog_history=history, max_tokens=800, temperature=0.9, allow_static_fallback=True)
    except: reply = ""
    if not reply:
        await message.reply(random.choice([
            "Слушай, чет я зависла 🙈 Повтори?",
            "Не уловила мысль. Иначе?",
            "Секунду, туплю немного. Давай ещё раз?",
        ]))
        return
    await db.add_private_message(u.id, "assistant", reply)
    await message.reply(reply[:4000])

@chat_router.message(F.photo, F.chat.type == "private")
async def handle_private_photo(message):
    u = message.from_user
    if not u: return
    caption = (message.caption or "").strip()
    await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    reply = ""
    try:
        from bot.media_handler import download_photo_as_base64
        data_uri = await download_photo_as_base64(message.bot, message)
        if data_uri:
            vision_prompt = (
                "Пользователь прислал фото (скорее всего интерьера или мебели). "
                "Опиши что видишь (1-2 предложения), оцени с точки зрения дизайнера мебели и предложи 1-2 конкретных решения. "
                f"{'Подпись: ' + caption if caption else ''}"
            )
            mood = await current_mood_descriptor()
            system = PERSONA_PROMPT + f"\n\nТвоё текущее настроение: {mood}."
            reply = await asyncio.wait_for(ai_client.vision(vision_prompt, data_uri, system=system, max_tokens=400), timeout=30.0)
    except asyncio.TimeoutError: pass
    except Exception as e: logger.error(f"private vision error: {e}")
    if not reply and caption:
        try: reply = await ai_client.chat(caption, system=PERSONA_PROMPT, max_tokens=400)
        except: reply = ""
    if not reply: reply = "Прикольное фото 🙂 А что на нём?"
    if reply:
        await db.add_private_message(u.id, "assistant", reply)
        await message.reply(reply[:4000])

@chat_router.message(F.voice, F.chat.type == "private")
async def handle_private_voice(message):
    u = message.from_user
    if not u: return
    await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    transcribed = ""
    try:
        from bot.media_handler import download_voice_as_base64
        data_uri = await download_voice_as_base64(message.bot, message)
        if data_uri: transcribed = await asyncio.wait_for(ai_client.transcribe_audio(data_uri), timeout=30.0)
    except: pass
    if not transcribed:
        await message.reply("Не разобрала голосовое 🙈 Повтори текстом?")
        return
    await update_mood_from_message(transcribed)
    mood = await current_mood_descriptor()
    history = await db.get_private_history(u.id, 16)
    await db.add_private_message(u.id, "user", f"[голосовое]: {transcribed}")
    system = PERSONA_PROMPT + f"\n\nТвоё текущее настроение: {mood}."
    try: reply = await ai_client.chat(transcribed, system=system, dialog_history=history, max_tokens=800, allow_static_fallback=True)
    except: reply = ""
    if not reply: reply = "Услышала, но чет зависла 🙈 Повтори?"
    await db.add_private_message(u.id, "assistant", reply)
    await message.reply(f"🎤 «{transcribed[:200]}»\n\n{reply}"[:4000])

@chat_router.message(F.sticker, F.chat.type == "private")
async def handle_private_sticker(message):
    u = message.from_user
    if not u: return
    await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    sticker_emoji = (message.sticker.emoji or "🙂") if message.sticker else "🙂"
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    mood = await current_mood_descriptor()
    history = await db.get_private_history(u.id, 8)
    await db.add_private_message(u.id, "user", f"[стикер {sticker_emoji}]")
    system = PERSONA_PROMPT + f"\n\nТвоё текущее настроение: {mood}."
    prompt = f"Тебе прислали стикер с эмодзи {sticker_emoji}. Коротко отреагируй живо (1 предложение)."
    try: reply = await ai_client.chat(prompt, system=system, dialog_history=history, max_tokens=150, allow_static_fallback=True)
    except: reply = ""
    if not reply: reply = f"Прикольный стикер {sticker_emoji}"
    await db.add_private_message(u.id, "assistant", reply)
    await message.reply(reply[:4000])

@chat_router.message(F.chat.type == "private")
async def handle_private_catchall(message):
    u = message.from_user
    if not u: return
    await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    if message.video_note: label, emoji = "кружочек", "⭕"
    elif message.video: label, emoji = "видео", "🎥"
    elif message.document: label, emoji = "файл", "📄"
    elif message.dice: label, emoji = f"игральный кубик ({message.dice.emoji})", "🎲"
    elif message.contact: label, emoji = "контакт", "👤"
    elif message.location: label, emoji = "геолокацию", "📍"
    elif message.poll: label, emoji = "опрос", "📊"
    else: label, emoji = "что-то", "🤔"
    caption = (message.caption or "").strip()
    await db.add_private_message(u.id, "user", f"[{label}{': '+caption if caption else ''}]")
    reply = f"Интересный {label} {emoji}! Если по мебели — рассказывай, подскажу. Текстом удобнее 🙂"
    await db.add_private_message(u.id, "assistant", reply)
    await message.reply(reply)
