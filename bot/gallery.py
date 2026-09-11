"""
Даша Gallery — /gallery: галерея стилей корпусной мебели с AI-визуалами.

Клиент выбирает стиль (лофт / сканди / минимализм / неоклассика / прованс /
хай-тек) — бот генерирует 2 фотореалистичных примера (Pollinations Image,
параллельно) и присылает медиагруппу с советами по стилю и кнопками связи.

Ось /inspiration — ПОМЕЩЕНИЕ (кухня/шкаф/гардеробная/прихожая),
ось /gallery — СТИЛЬ. Вместе закрывают оба вопроса клиента: «что» и «как».

Защита: cooldown 5 минут на пользователя, при провале обеих картинок —
текстовые советы (фича не ломает бот).
"""
import asyncio
import logging
import time

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger("dasha.gallery")

gallery_router = Router()

_COOLDOWN_SEC = 300  # 5 минут между генерациями на пользователя
_last_gen_ts = {}    # user_id → ts

_PHONE = "+7 (913) 448-37-17"
_PHONE_DIGITS = "79134483717"

# Стиль → (лейбл, [темы для визуала], [советы], подпись-подача)
STYLES = {
    "loft": {
        "label": "🏗 Лофт",
        "topics": ["Кухня в стиле лофт кирпичная стена тёмные матовые фасады", "Гардеробная в стиле лофт металлический каркас открытое дерево"],
        "tips": [
            "Фасады: матовый графит или шпон грубого дуба — глянец ломает стилистику.",
            "Смешение фактур важнее цвета: дерево + металл + кирпич.",
            "Свет тёплый 2700–3000K на подвесах и рейлах — без него лофт «мертвый».",
        ],
        "lead": "Лофт: кирпич, металл и открытое дерево 👇",
    },
    "scandi": {
        "label": "🌲 Сканди",
        "topics": ["Кухня в скандинавском стиле белые фасады светлое дерево", "Прихожая в скандинавском стиле шкаф с крючками и обувницей"],
        "tips": [
            "База — белый и светлое дерево, акцент — один тёплый: терракота, горчица.",
            "Фасады без патин и фрезеровки: чистая геометрия и простые линии.",
            "Текстуры вместо декора: дерево, лён, камень.",
        ],
        "lead": "Скандинавский стиль: свет и простые линии 👇",
    },
    "minimal": {
        "label": "◻️ Минимализм",
        "topics": ["Минималистичная кухня без ручек фасады гола матовые", "Встроенный шкаф в стиле минимализм со скрытой подсветкой"],
        "tips": [
            "Ручек нет: профиль-гола или push-to-open — линии чистые.",
            "Палитра: 2–3 цвета максимум, один из них акцентный.",
            "Всё скрыто: техника, вытяжка, даже розетки — за фасадами.",
        ],
        "lead": "Минимализм: ничего лишнего, всё скрыто 👇",
    },
    "neoclassic": {
        "label": "🏛 Неоклассика",
        "topics": ["Неоклассическая кухня фрезерованные фасады МДФ эмаль", "Прихожая в неоклассическом стиле с молдингами и зеркалом"],
        "tips": [
            "Фасады: МДФ-эмаль с мягкой фрезеровкой, без позолоты.",
            "Цвета: серо-бежевые, пудровые, акцент — глубокий зелёный или синий.",
            "Фурнитура латунь или бронза — но в одном тоне по всей комнате.",
        ],
        "lead": "Неоклассика: спокойная классика без пафоса 👇",
    },
    "provence": {
        "label": "🌸 Прованс",
        "topics": ["Кухня в стиле прованс пастельные фасады открытые полки", "Гардеробная в стиле прованс с патиной и льняным текстилем"],
        "tips": [
            "Пастель: оливковый, лаванда, кремовый — чисто белый глянец не в тему.",
            "Фасады с патиной или фрезеровкой «под старину».",
            "Открытые полки с красивой посудой — часть стиля, а не беспорядок.",
        ],
        "lead": "Прованс: деревенская нежность 👇",
    },
    "hitech": {
        "label": "⚙️ Хай-тек",
        "topics": ["Кухня в стиле хай-тек глянцевые фасады LED подсветка", "Шкаф-купе в стиле хай-тек со стеклянными фасадами"],
        "tips": [
            "Глянец + алюминиевый профиль + LED-подсветка — три кита стиля.",
            "Палитра холодная: серый, белый, чёрный и один яркий акцент.",
            "Техника интегрированная, на виду — сталь и стекло.",
        ],
        "lead": "Хай-тек: глянец, сталь и подсветка 👇",
    },
}

_MENU_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text=STYLES[k]["label"], callback_data=f"gal:{k}"),
     InlineKeyboardButton(text=STYLES[k2]["label"], callback_data=f"gal:{k2}")]
    for k, k2 in (("loft", "scandi"), ("minimal", "neoclassic"), ("provence", "hitech"))
] + [[InlineKeyboardButton(text="📐 Бесплатный замер", callback_data="measure:start")]])


def _contacts_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📐 Бесплатный замер", callback_data="measure:start"),
         InlineKeyboardButton(text="🧮 Калькулятор цены", callback_data="calc:restart")],
        [InlineKeyboardButton(text="📞 Позвонить Даше", url=f"tel:+{_PHONE_DIGITS}"),
         InlineKeyboardButton(text="💬 WhatsApp", url=f"https://wa.me/{_PHONE_DIGITS}")],
    ])


def _tips_caption(style_key: str, idx: int) -> str:
    """Подпись к фото: подача + 1-2 совета + контакты (бюджет caption 1024)."""
    s = STYLES[style_key]
    tips = s["tips"][idx * 2: idx * 2 + 2] or s["tips"][:2]
    lines = [f"✨ {s['lead']}", ""]
    lines += [f"💡 {t}" for t in tips]
    lines += ["", "Спроектирую ваш стиль под ваши размеры — замер бесплатно.",
              f"📞 {_PHONE} | 🌐 abakanmebel.online"]
    return "\n".join(lines)[:1024]


@gallery_router.message(Command("gallery"))
async def cmd_gallery(message: Message):
    """/gallery — меню выбора стиля (личка и группы)."""
    u = message.from_user
    if u:
        from bot import database as db
        await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot,
                             in_private=message.chat.type == "private")
    await message.reply(
        "🎨 Галерея стилей от Даши!\n\n"
        "Выберите стиль — сгенерирую 2 визуальных примера с советами дизайнера "
        "(займёт до минуты):",
        reply_markup=_MENU_KB,
    )


@gallery_router.callback_query(F.data.startswith("gal:"))
async def cb_gallery(cb: CallbackQuery):
    """Выбор стиля → генерация 2 картинок параллельно → медиагруппа."""
    key = (cb.data or "").split(":", 1)[1]
    style = STYLES.get(key)
    if not style:
        await cb.answer("Такой стиль пока не в галерее 🙂")
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
            f"⏳ Собираю галерею: {style['label']}… одна минута ☕",
        )
    except Exception:
        pass

    # Параллельная генерация двух картинок
    from bot.visuals import generate_furniture_image
    results = await asyncio.gather(
        generate_furniture_image(style["topics"][0], timeout=50),
        generate_furniture_image(style["topics"][1], timeout=50),
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
            media = [
                InputMediaPhoto(media=BufferedInputFile(images[i], filename=f"gal{i}.jpg"),
                                caption=_tips_caption(key, i))
                for i in range(2)
            ]
            await cb.bot.send_media_group(cb.message.chat.id, media)
        elif len(images) == 1:
            from aiogram.types import BufferedInputFile
            await cb.bot.send_photo(cb.message.chat.id, BufferedInputFile(images[0], filename="gal.jpg"),
                                    caption=_tips_caption(key, 0))
        else:
            raise RuntimeError("no images generated")
    except Exception as e:
        logger.warning(f"gallery media failed: {e} — text fallback")
        s = STYLES[key]
        text = "✨ " + s["lead"] + "\n\n" + "\n\n".join(f"💡 {t}" for t in s["tips"])
        text += f"\n\n📞 {_PHONE} | 🌐 abakanmebel.online"
        try:
            await cb.bot.send_message(cb.message.chat.id, text[:4000], reply_markup=_contacts_kb())
        except Exception as e2:
            logger.warning(f"gallery text fallback failed: {e2}")
        return

    # Кнопки связи отдельным сообщением (медиагруппа не носит клавиатуру)
    try:
        await cb.bot.send_message(
            cb.message.chat.id,
            "Понравился стиль? Спроектирую его под ваши размеры 👇",
            reply_markup=_contacts_kb(),
        )
    except Exception:
        pass
