"""
Даша Compare — интерактивное сравнение материалов (/compare).

4 материала (ЛДСП, МДФ-плёнка, МДФ-эмаль, Массив) × 6 критериев + вердикт
для каждой из 6 пар. Навигация inline-кнопками, шаги зашиты в callback_data —
stateless, переживает рестарты, БЕЗ AI-вызовов.

Ценовые ориентиры согласованы с прайс-моделью calculator.py (тыс. ₽ за
пог. м кухни), характеристики — с базой знаний dasha.py и каталогом
abakanmebel.online. Строго корпусная мебель.
"""

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command

logger = logging.getLogger("dasha.compare")

compare_router = Router()

# ─── Контент: материалы ──────────────────────────────────────────────────────

MATERIALS: dict = {
    "ldsp": {
        "name": "ЛДСП Е0,5",
        "emoji": "🟫",
        "price": "30–38 тыс. ₽/пог. м",
        "moisture": "низкая: разбухает на стыках при постоянной влаге",
        "scratch": "плёнка ламинирования — средняя стойкость",
        "colors": "30+ готовых декоров, текстуры дерева, шагрень/глянец",
        "repair": "скол не зашлифуешь — замена детали; кромка ПВХ 2 мм защищает торцы",
        "life": "8–12 лет",
        "best": "корпуса шкафов и кухонь, дачный дом, аренда, ограниченный бюджет",
    },
    "mdfpv": {
        "name": "МДФ в плёнке ПВХ",
        "emoji": "🟧",
        "price": "36–48 тыс. ₽/пог. м",
        "moisture": "хорошая, но плёнка может отклеиться у плиты/духовки при перегреве",
        "scratch": "плёнку процарапать легче, чем эмаль",
        "colors": "глянец/мат, дерево-декоры, фрезеровка фасадов доступна",
        "repair": "плёнку можно локально переклеить в цеху",
        "life": "7–10 лет",
        "best": "умеренный бюджет, фасады с фрезеровкой, спальни и прихожие",
    },
    "mdfem": {
        "name": "МДФ-эмаль",
        "emoji": "⬜",
        "price": "46–62 тыс. ₽/пог. м",
        "moisture": "отличная: грунт + эмаль — подходит кухня и ванная",
        "scratch": "высокая; матовая эмаль скрывает микроцарапины",
        "colors": "любой тон по палитре RAL, мат или глянец, без ограничений",
        "repair": "скол подкрашивается, фасад можно перекрасить целиком",
        "life": "12–15 лет",
        "best": "кухни под покраску, влажные зоны, дизайнерские проекты",
    },
    "massiv": {
        "name": "Массив дерева",
        "emoji": "🟫",
        "price": "70–110 тыс. ₽/пог. м",
        "moisture": "требует покрытия маслом/лаком: «дышит» при перепадах влажности",
        "scratch": "твёрдые породы (дуб, ясень) — высокая; сосна мягче",
        "colors": "уникальная натуральная текстура, масло/тонировка",
        "repair": "шлифовка + новое покрытие — фасад снова как новый",
        "life": "20+ лет",
        "best": "премиум-проекты, классика и лофт, столешницы, «на десятилетия»",
    },
}

_ORDER = ["ldsp", "mdfpv", "mdfem", "massiv"]

# Вердикт для каждой пары (ключ — отсортированная пара)
_VERDICTS: dict = {
    ("ldsp", "mdfpv"): (
        "Разница в цене небольшая, а плёнка ПВХ даёт фрезеровку и мягкие формы. "
        "Но если фасады у плиты и духовки — берите эмаль или ЛДСП с термозащитой. "
        "Корпус в обоих случаях делаем из ЛДСП Е0,5 — это правильная экономия."
    ),
    ("ldsp", "mdfem"): (
        "Моя любимая связка в проектах: корпус и скрытые секции — ЛДСП, "
        "лицевые фасады — МДФ-эмаль. Получается «журнальная» кухня без переплаты "
        "за массив. Влага у мойки эмали не страшна."
    ),
    ("ldsp", "massiv"): (
        "Массив — на десятилетия, но он «живёт»: реагирует на влажность, требует "
        "ухода. Для первого бюджета честнее ЛДСП Е0,5 + столешница получше, "
        "чем массив на всё и фурнитура «эконом»."
    ),
    ("mdfem", "mdfpv"): (
        "Плёнка выигрывает по цене, эмаль — по долговечности и влаге. "
        "Если кухня — эмаль: перегрев у плиты и пар её не берут. "
        "Для спальни/прихожей плёнки достаточно — там перегрева нет."
    ),
    ("massiv", "mdfpv"): (
        "Разные лиги по цене и характеру. Массив — про текстуру и срок, "
        "плёнка — про бюджет и фрезеровку. Компромисс: корпус и полки ЛДСП, "
        "вставки/столешница — массив: характер есть, цена разумная."
    ),
    ("massiv", "mdfem"): (
        "Эмаль даёт идеальный ровный цвет и стойкость к влаге, массив — живую "
        "текстуру и долгий срок. В современных кухнях чаще эмаль; массив беру "
        "в классику, лофт и столешницы. По деньгам разница ~1,5–2×."
    ),
}

_TIP = (
    "💡 Частая связка в наших проектах: корпус — ЛДСП Е0,5, фасады — МДФ-эмаль. "
    "Максимум качества за разумный бюджет."
)

# ─── Клавиатуры ──────────────────────────────────────────────────────────────


def _materials_keyboard(exclude: str = "") -> InlineKeyboardMarkup:
    keys = [k for k in _ORDER if k != exclude]
    rows, row = [], []
    for k in keys:
        row.append(InlineKeyboardButton(
            text=f"{MATERIALS[k]['emoji']} {MATERIALS[k]['name']}",
            callback_data=f"cmp:{k}",
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton(text="📐 Бесплатный замер", callback_data="measure:start"),
        InlineKeyboardButton(text="🧮 Калькулятор", callback_data="calc:restart"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pair_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Другая пара", callback_data="cmp:menu")],
        [InlineKeyboardButton(text="📐 Бесплатный замер", callback_data="measure:start"),
         InlineKeyboardButton(text="🧮 Калькулятор", callback_data="calc:restart")],
    ])


# ─── Тексты ──────────────────────────────────────────────────────────────────

_INTRO = (
    "⚖️ <b>Сравнение материалов</b>\n\n"
    "Выберите первый материал — потом второй, покажу сравнение по цене, "
    "влаге, царапинам и ремонту + свой вердикт дизайнера:"
)


def _first_text(key: str) -> str:
    m = MATERIALS[key]
    return (
        f"{m['emoji']} <b>{m['name']}</b>\n\n"
        f"💰 Цена (кухня, за пог. м): {m['price']}\n"
        f"🏆 Где лучше: {m['best']}\n\n"
        f"<b>С чем сравнить?</b>"
    )


def _pair_text(a: str, b: str) -> str:
    ma, mb = MATERIALS[a], MATERIALS[b]
    verdict = _VERDICTS.get(tuple(sorted((a, b))), _TIP)
    return (
        f"⚖️ <b>{ma['name']} vs {mb['name']}</b>\n\n"
        f"💰 <b>Цена:</b> {ma['price']} vs {mb['price']}\n"
        f"💧 <b>Влага:</b> {ma['moisture']} | {mb['moisture']}\n"
        f"🛡 <b>Царапины:</b> {ma['scratch']} | {mb['scratch']}\n"
        f"🎨 <b>Цвета:</b> {ma['colors']} | {mb['colors']}\n"
        f"🔧 <b>Ремонт:</b> {ma['repair']} | {mb['repair']}\n"
        f"⏳ <b>Срок службы:</b> {ma['life']} vs {mb['life']}\n\n"
        f"🏆 <b>Мой вердикт:</b> {verdict}\n\n"
        f"{_TIP}"
    )


# ─── Хендлеры ────────────────────────────────────────────────────────────────


@compare_router.message(Command("compare"))
async def cmd_compare(message: Message):
    """Сравнение материалов — личка и группы."""
    await message.reply(_INTRO, reply_markup=_materials_keyboard())


@compare_router.callback_query(F.data == "cmp:menu")
async def cb_compare_menu(cb: CallbackQuery):
    try:
        await cb.message.edit_text(_INTRO, reply_markup=_materials_keyboard())
    except Exception:
        pass
    await cb.answer()


@compare_router.callback_query(F.data.regexp(r"^cmp:(ldsp|mdfpv|mdfem|massiv)$"))
async def cb_compare_first(cb: CallbackQuery):
    """Выбран первый материал — предлагаем второго."""
    key = cb.data.split(":")[1]
    try:
        await cb.message.edit_text(_first_text(key), reply_markup=_materials_keyboard(exclude=key))
    except Exception:
        pass
    await cb.answer()


@compare_router.callback_query(F.data.startswith("cmp:"))
async def cb_compare_pair(cb: CallbackQuery):
    """Выбрана пара: callback_data = cmp:<a>:<b>."""
    try:
        parts = cb.data.split(":")
        if len(parts) != 3:
            await cb.answer()
            return
        a, b = parts[1], parts[2]
        if a not in MATERIALS or b not in MATERIALS or a == b:
            await cb.answer("Такой пары нет 🙂", show_alert=False)
            return
        text = _pair_text(a, b)
        try:
            await cb.message.edit_text(text, reply_markup=_pair_keyboard())
        except Exception:
            await cb.message.reply(text, reply_markup=_pair_keyboard())
        await cb.answer()
    except Exception as e:
        logger.warning(f"compare callback failed: {e}")
        try:
            await cb.answer("Ошибка, попробуйте ещё раз 🙂", show_alert=False)
        except Exception:
            pass
