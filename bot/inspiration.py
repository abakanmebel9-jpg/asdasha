"""Даша Inspiration — /inspiration: AI-подборка «для вдохновения».

Клиент выбирает помещение (кухня / шкаф-купе / гардеробная / прихожая) —
бот генерирует 2 фотореалистичных примера интерьера (Pollinations Image,
параллельно) и присылает медиагруппу с короткими советами дизайнера и
кнопками связи. Без AI-текста: только картинки + готовые подсказки.

Защита: cooldown 5 минут на пользователя, генерация опциональна — при
провале обеих картинок уходят текстовые советы (фича не ломает бот).
"""
import asyncio
import logging
import time

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger("dasha.inspiration")

inspiration_router = Router()

_COOLDOWN_SEC = 300  # 5 минут между генерациями на пользователя
_last_gen_ts = {}    # user_id → ts

_PHONE = "+7 (913) 448-37-17"
_PHONE_DIGITS = "79134483717"

# Помещение → (тема для визуала, [советы], подпись)
_ROOMS = {
    "kitchen": {
        "label": "🍳 Кухня",
        "topics": ["Современная угловая кухня с островом", "Кухня в скандинавском стиле с светлыми фасадами"],
        "tips": [
            "Рабочий треугольник: мойка — плита — холодильник не дальше 2,6 м по периметру.",
            "Мойка у окна — спорно: брызги на шторах. Под окном лучше рабочая зона.",
            "Выдвижные карго-секции у плиты — специи и масло под рукой.",
        ],
        "lead": "Два направления, которые чаще всего заказывают в Абакане 👇",
    },
    "wardrobe": {
        "label": "🚪 Шкаф-купе",
        "topics": ["Встроенный шкаф-купе с зеркальными дверями", "Шкаф-купе со светлыми фасадами и подсветкой"],
        "tips": [
            "Штанги эшелоном: верхняя одежда снизу, рубашки сверху — минус 40% места.",
            "Двери-купе с доводчиками не хлопают и служат в 2-3 раза дольше.",
            "Глубина 600 мм: вешалки входят вдоль, ничего не разворачивается боком.",
        ],
        "lead": "Смотрю, что сейчас в тренде у шкафов-купе 👇",
    },
    "closet": {
        "label": "👗 Гардеробная",
        "topics": ["Гардеробная комната с подсветкой и полками", "Компактная гардеробная 2 кв метра система хранения"],
        "tips": [
            "Гардеробная от 1,5 м²: глубина полок 40 см, проход 60 см — уже удобно.",
            "LED-подсветка в секциях — вещи видно сразу, без фонарика.",
            "Выдвижные брючницы и корзины экономят больше места, чем лишняя полка.",
        ],
        "lead": "Примеры, как гардеробная живёт даже в маленькой спальне 👇",
    },
    "hallway": {
        "label": "👟 Прихожая",
        "topics": ["Прихожая с обувницей и зеркалом светлый дуб", "Прихожая на заказ со шкафом и сиденьем"],
        "tips": [
            "Обувница-слейд: выдвигается на 30 см и прячет 12-16 пар обуви.",
            "Зеркало в рост + крючки на двух уровнях: и взрослым, и детям удобно.",
            "Сиденье с ящиком — обуваемся удобно, сезонка хранится внутри.",
        ],
        "lead": "Вдохновение для прихожей — от классики до минимализма 👇",
    },
}

_MENU_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text=r["label"], callback_data=f"insp:{key}")]
    for key, r in _ROOMS.items()
] + [[InlineKeyboardButton(text="📐 Бесплатный замер", callback_data="measure:start")]])


def _contacts_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📐 Бесплатный замер", callback_data="measure:start"),
         InlineKeyboardButton(text="🧮 Калькулятор цены", callback_data="calc:restart")],
        [InlineKeyboardButton(text="📞 Позвонить Даше", url=f"tel:+{_PHONE_DIGITS}"),
         InlineKeyboardButton(text="💬 WhatsApp", url=f"https://wa.me/{_PHONE_DIGITS}")],
    ])


def _tips_caption(room_key: str, idx: int) -> str:
    """Подпись к фото: подача + 1-2 совета + контакты (бюджет caption 1024)."""
    r = _ROOMS[room_key]
    tips = r["tips"][idx * 2: idx * 2 + 2] or r["tips"][:2]
    lines = [f"✨ {r['lead']}", ""]
    lines += [f"💡 {t}" for t in tips]
    lines += ["", "Спроектирую под ваши размеры — замер бесплатно.",
              f"📞 {_PHONE} | 🌐 abakanmebel.online"]
    return "\n".join(lines)[:1024]


@inspiration_router.message(Command("inspiration"))
async def cmd_inspiration(message: Message):
    """/inspiration — меню выбора помещения (личка и группы)."""
    u = message.from_user
    if u:
        from bot import database as db
        await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot,
                             in_private=message.chat.type == "private")
    await message.reply(
        "🎨 Вдохновение от Даши!\n\n"
        "Выберите помещение — соберу для вас 2 визуальных примера "
        "с советами дизайнера (займёт до минуты):",
        reply_markup=_MENU_KB,
    )


@inspiration_router.callback_query(F.data.startswith("insp:"))
async def cb_inspiration(cb: CallbackQuery):
    """Выбор помещения → генерация 2 картинок параллельно → медиагруппа."""
    key = (cb.data or "").split(":", 1)[1]
    room = _ROOMS.get(key)
    if not room:
        await cb.answer("Неизвестное помещение")
        return
    u = cb.from_user
    now = time.time()
    if now - _last_gen_ts.get(u.id, 0) < _COOLDOWN_SEC:
        await cb.answer("⏳ Только что генерировала — дайте мне минут 5 🙂", show_alert=True)
        return
    _last_gen_ts[u.id] = now
    await cb.answer("Генерирую примеры — до минуты ✨")

    placeholder = None
    try:
        placeholder = await cb.bot.send_message(
            cb.message.chat.id,
            f"⏳ Подбираю вдохновение: {room['label']}… одна минута ☕",
        )
    except Exception:
        pass

    # Параллельная генерация двух картинок
    from bot.visuals import generate_furniture_image
    results = await asyncio.gather(
        generate_furniture_image(room["topics"][0], timeout=50),
        generate_furniture_image(room["topics"][1], timeout=50),
        return_exceptions=True,
    )
    images = [r for r in results if isinstance(r, bytes)]

    try:
        if placeholder:
            await placeholder.delete()
    except Exception:
        pass

    try:
        if len(images) >= 2:
            from aiogram.types import InputMediaPhoto, BufferedInputFile
            from aiogram.enums import ParseMode
            media = [
                InputMediaPhoto(media=BufferedInputFile(images[i], filename=f"insp{i}.jpg"),
                                caption=_tips_caption(key, i))
                for i in range(2)
            ]
            await cb.bot.send_media_group(cb.message.chat.id, media)
        elif len(images) == 1:
            from aiogram.types import BufferedInputFile
            from aiogram.enums import ParseMode
            await cb.bot.send_photo(cb.message.chat.id, BufferedInputFile(images[0], filename="insp.jpg"),
                                    caption=_tips_caption(key, 0))
        else:
            raise RuntimeError("no images generated")
    except Exception as e:
        logger.warning(f"inspiration media failed: {e} — text fallback")
        r = _ROOMS[key]
        text = "✨ " + r["lead"] + "\n\n" + "\n\n".join(f"💡 {t}" for t in r["tips"])
        text += f"\n\n📞 {_PHONE} | 🌐 abakanmebel.online"
        try:
            await cb.bot.send_message(cb.message.chat.id, text[:4000], reply_markup=_contacts_kb())
        except Exception as e2:
            logger.warning(f"inspiration text fallback failed: {e2}")
        return

    # Кнопки связи отдельным сообщением (медиагруппа не носит клавиатуру)
    try:
        await cb.bot.send_message(
            cb.message.chat.id,
            "Понравилось направление? Спроектирую под ваши размеры 👇",
            reply_markup=_contacts_kb(),
        )
    except Exception:
        pass
