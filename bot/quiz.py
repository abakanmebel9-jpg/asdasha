"""
Даша Quiz — интерактивный подбор мебели (лид-магнит).

3 вопроса с inline-кнопками: помещение → стиль → бюджет/материалы.
Итог: конкретная рекомендация из каталога abakanmebel.online + совет
из практики + кнопки связи (звонок/WhatsApp/сайт).

Только корпусная мебель. Без AI-вызовов — мгновенный детерминированный ответ.
Работает в личке и группах (как /consult и /fact).
"""

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from bot.config import config

logger = logging.getLogger("dasha.quiz")

quiz_router = Router()

PHONE = config.PHONE or "+7 (913) 448-37-17"
PHONE_DIGITS = "".join(ch for ch in PHONE if ch.isdigit())

# ═══════════════════════════════════════════════════════════════════════════
# Вопросы квиза
# ═══════════════════════════════════════════════════════════════════════════

Q1 = {
    "text": "📐 <b>Шаг 1 из 3.</b> Что обустраиваем?",
    "options": [
        ("Кухню", "kitchen"),
        ("Шкаф-купе / гардеробную", "wardrobe"),
        ("Прихожую", "hallway"),
        ("Детскую или другую комнату", "kids"),
    ],
}

Q2 = {
    "text": "🎨 <b>Шаг 2 из 3.</b> Какой стиль вам ближе?",
    "options": [
        ("Минимализм / скандинавский", "minimal"),
        ("Классика / неоклассика", "classic"),
        ("Лофт / индустриальный", "loft"),
        ("Подскажите сами", "any"),
    ],
}

Q3 = {
    "text": "💰 <b>Шаг 3 из 3.</b> Материалы и бюджет?",
    "options": [
        ("Рационально: ЛДСП", "ldsp"),
        ("Оптимум: МДФ / эмаль", "mdf"),
        ("Премиум: массив дерева", "massiv"),
        ("Нужна консультация", "consult"),
    ],
}

# ═══════════════════════════════════════════════════════════════════════════
# Рекомендации по помещениям (категории каталога site_content)
# ═══════════════════════════════════════════════════════════════════════════

_ROOM_ADVICE = {
    "kitchen": (
        "🍳 <b>Кухня на заказ</b> — наш профиль №1.\n"
        "• Подберём эргономичную «рабочую треугольницу»: мойка–плита–холодильник\n"
        "• Фартук и столешница в одном материале — практично и цельно\n"
        "• Выдвижные корзины и доводчики Blum — в базовой комплектации",
        ["кухн"],
    ),
    "wardrobe": (
        "🚪 <b>Шкаф-купе или гардеробная</b> — до потолка, без пыльных зазоров.\n"
        "• Внутренняя начинка проектируется под ваш гардероб (штанги, полки, обувницы)\n"
        "• Зеркальные двери визуально удваивают комнату\n"
        "• Алюминиевый профиль — тихий и долговечный ход дверей",
        ["шкаф", "гардероб"],
    ),
    "hallway": (
        "👟 <b>Прихожая</b> — первое, что видят гости.\n"
        "• Закрытая вешалка прячет сезонную верхнюю одежду\n"
        "• Обувница с сиденьем — удобно обуваться\n"
        "• Зеркальная вставка + подсветка — простор и свет",
        ["прихож"],
    ),
    "kids": (
        "🧸 <b>Детская</b> — безопасная и «растущая».\n"
        "• ЛДСП класса Е0,5, кромка ABS, скруглённые углы\n"
        "• Кровать-чердак освобождает пол для игр\n"
        "• Стеллажи по росту ребёнка — приучает к порядку",
        ["детск", "чердак"],
    ),
}

_STYLE_ADVICE = {
    "minimal": "Минимализм любит матовые фасады без ручек (push-to-open), скрытую подсветку и спокойную палитру «тёплый серый + дерево».",
    "classic": "Классика — фрезерованные фасады МДФ, патина или эмаль, фурнитура «под бронзу», столешница под камень.",
    "loft": "Лофт — сочетание ЛДСП «под бетон/дерево» с металлическим профилем, открытые стеллажи, чёрная фурнитура.",
    "any": "Не знаете, что выбрать? Подберу под вашу квартиру и освещение — на бесплатном замере покажу образцы материалов вживую.",
}

_MAT_ADVICE = {
    "ldsp": "ЛДСП — рациональный выбор: кромка ABS, класс эмиссии Е0,5, гарантия производителя. Идеально для корпуса и детской.",
    "mdf": "МДФ/эмаль — гладкая моющаяся поверхность, любые цвета по RAL, фасады без ограничений по фрезеровке. Оптимум цены и вида.",
    "massiv": "Массив — благородная текстура, ремонтопригодность на десятилетия. Рекомендую дуб/ясень + масло-воск OSMO.",
    "consult": "Приеду на бесплатный замер с образцами всех материалов — сравните ЛДСП, МДФ и массив вживую при вашем освещении.",
}

# Состояние квиза: key → {"q": 1..3, "room": str, "style": str, "mat": str}
_quiz_state: dict = {}


def _key(cb: CallbackQuery) -> str:
    u = cb.from_user
    return f"{cb.message.chat.id}:{u.id if u else 0}"


def _contacts_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 Позвонить Даше", url=f"tel:+{PHONE_DIGITS}"),
         InlineKeyboardButton(text="💬 WhatsApp", url=f"https://wa.me/{PHONE_DIGITS}")],
        [InlineKeyboardButton(text="🌐 abakanmebel.online", url="https://abakanmebel.online")],
    ])


def _kb_for(question: dict, prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for i, (label, value) in enumerate(question["options"]):
        rows.append([InlineKeyboardButton(text=label, callback_data=f"{prefix}:{i}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pick_product(room_value: str) -> str:
    """Ищет подходящий товар из каталога site_content по категории помещения."""
    try:
        from bot.site_content import _PRODUCTS
        cat_map = {"kitchen": "кухня", "wardrobe": ("шкаф", "гардеробная"), "hallway": "прихожая", "kids": "детская"}
        wanted = cat_map.get(room_value, ())
        if isinstance(wanted, str):
            wanted = (wanted,)
        for p in _PRODUCTS:
            if p.get("category") in wanted:
                return f"{p['name']} ({p['sizes']}, {p['from_price']})"
    except Exception as e:
        logger.debug(f"quiz product pick error: {e}")
    return ""


def _build_result(state: dict) -> str:
    room = state.get("room", "kitchen")
    style = state.get("style", "any")
    mat = state.get("mat", "consult")
    room_text, _ = _ROOM_ADVICE.get(room, _ROOM_ADVICE["kitchen"])
    style_text = _STYLE_ADVICE.get(style, _STYLE_ADVICE["any"])
    mat_text = _MAT_ADVICE.get(mat, _MAT_ADVICE["consult"])
    product = _pick_product(room)

    parts = ["✅ <b>Ваш подбор готов!</b>\n", room_text, f"\n🎨 <b>По стилю:</b> {style_text}",
             f"\n🪵 <b>По материалам:</b> {mat_text}"]
    if product:
        parts.append(f"\n🛋 <b>Из каталога подходит:</b> {product}")
    parts.append("\n🗃 <b>Бонус:</b> конкретные идеи хранения для этой комнаты — команда /storage")
    parts.append("\n\n📐 Точная стоимость — после бесплатного замера. Замер по Абакану и Хакасии 🚗")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# Хендлеры
# ═══════════════════════════════════════════════════════════════════════════

@quiz_router.message(Command("quiz"))
async def cmd_quiz(message: Message):
    """Старт квиза: /quiz или /quiz@botname в группе."""
    intro = (
        "🛋 <b>Квиз «Подбери свою мебель»</b>\n\n"
        "3 коротких вопроса — и я подскажу решение, материалы и ориентировочную цену "
        "под вашу задачу. Займёт полминуты 😊"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Начать", callback_data="quiz:start")],
        [InlineKeyboardButton(text="📞 Сразу связаться", url=f"tel:+{PHONE_DIGITS}")],
    ])
    try:
        await message.reply(intro, reply_markup=kb)
    except Exception:
        await message.reply(intro.replace("<b>", "").replace("</b>", ""))


@quiz_router.callback_query(F.data == "quiz:start")
async def cb_quiz_start(cb: CallbackQuery):
    _quiz_state[_key(cb)] = {"q": 1}
    await cb.message.edit_text(Q1["text"], reply_markup=_kb_for(Q1, "quiz:q1"))
    await cb.answer()


@quiz_router.callback_query(F.data.startswith("quiz:q1:"))
async def cb_quiz_q1(cb: CallbackQuery):
    try:
        idx = int(cb.data.rsplit(":", 1)[1])
        value = Q1["options"][idx][1]
    except (ValueError, IndexError):
        await cb.answer("Что-то пошло не так, начните заново: /quiz", show_alert=True)
        return
    st = _quiz_state.setdefault(_key(cb), {})
    st.update({"room": value, "q": 2})
    await cb.message.edit_text(Q2["text"], reply_markup=_kb_for(Q2, "quiz:q2"))
    await cb.answer()


@quiz_router.callback_query(F.data.startswith("quiz:q2:"))
async def cb_quiz_q2(cb: CallbackQuery):
    try:
        idx = int(cb.data.rsplit(":", 1)[1])
        value = Q2["options"][idx][1]
    except (ValueError, IndexError):
        await cb.answer("Что-то пошло не так, начните заново: /quiz", show_alert=True)
        return
    st = _quiz_state.setdefault(_key(cb), {})
    st.update({"style": value, "q": 3})
    await cb.message.edit_text(Q3["text"], reply_markup=_kb_for(Q3, "quiz:q3"))
    await cb.answer()


@quiz_router.callback_query(F.data.startswith("quiz:q3:"))
async def cb_quiz_q3(cb: CallbackQuery):
    try:
        idx = int(cb.data.rsplit(":", 1)[1])
        value = Q3["options"][idx][1]
    except (ValueError, IndexError):
        await cb.answer("Что-то пошло не так, начните заново: /quiz", show_alert=True)
        return
    st = _quiz_state.setdefault(_key(cb), {})
    st.update({"mat": value, "q": 0})
    result = _build_result(st)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Пройти ещё раз", callback_data="quiz:start")],
        [InlineKeyboardButton(text="📞 Бесплатный замер", url=f"tel:+{PHONE_DIGITS}"),
         InlineKeyboardButton(text="💬 WhatsApp", url=f"https://wa.me/{PHONE_DIGITS}")],
        [InlineKeyboardButton(text="🌐 abakanmebel.online", url="https://abakanmebel.online")],
    ])
    try:
        await cb.message.edit_text(result, reply_markup=kb)
    except Exception:
        await cb.message.reply(_strip_html(result), reply_markup=kb)
    await cb.answer()
    logger.info(f"Quiz completed: room={st.get('room')} style={st.get('style')} mat={st.get('mat')}")


def _strip_html(text: str) -> str:
    for tag in ("<b>", "</b>"):
        text = text.replace(tag, "")
    return text
