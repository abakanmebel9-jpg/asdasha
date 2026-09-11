"""
Даша Calculator — калькулятор ориентировочной стоимости корпусной мебели.

Инлайн-калькулятор без AI-вызовов: тип мебели → форма/размер → материалы
→ диапазон цены на основе прайс-базы abakanmebel.online (site_content,
PRICING_INFO из dasha.py). Детерминированный расчёт, мгновенный ответ.

Stateless: все шаги зашиты в callback_data («calc:kitchen:straight:3-4:mdf»)
— работает в личке и группах, переживает рестарты, не занимает память.

Строго корпусная мебель: кухни, шкафы-купе, гардеробные, прихожие.
"""

import logging
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
)
from aiogram.filters import Command
from bot.config import config

logger = logging.getLogger("dasha.calc")

calc_router = Router()

PHONE = config.PHONE or "+7 (913) 448-37-17"
PHONE_DIGITS = "".join(ch for ch in PHONE if ch.isdigit())

# ═══════════════════════════════════════════════════════════════════════════
# Прайс-модель (тыс. ₽). Источники: каталог abakanmebel.online + практика.
# ═══════════════════════════════════════════════════════════════════════════

# Кухня: цена за погонный метр по материалу фасадов (низкий–высокий ориентир)
_KITCHEN_PER_M = {
    "ldsp":  ("ЛДСП",              30, 38),
    "mdfpv": ("МДФ в плёнке",       36, 48),
    "mdfem": ("МДФ эмаль",         46, 62),
    "massiv":("Массив дерева",     70, 110),
}
_KITCHEN_SHAPE = {
    "straight": ("Прямая",  1.0),
    "corner":   ("Угловая", 1.25),
    "island":   ("С островом", 1.6),
}
_KITCHEN_SIZE = {
    "s24": ("до 2,4 м", 2.4),
    "s30": ("2,4–3 м",  3.0),
    "s40": ("3–4 м",    4.0),
    "s45": ("больше 4 м", 4.5),
}

# Шкаф-купе: цена за м² дверей/наполнения (высота ~2,5 м до потолка)
_WARDROBE_PER_M2 = {
    "ldsp":  ("ЛДСП",                  12, 16),
    "zerk":  ("Зеркальные двери",      15, 20),
    "kombi": ("Комби: ЛДСП + зеркало", 13, 18),
}
_WARDROBE_TYPE = {
    "vstro": ("Встроенный", 1.0),
    "korpus":("Корпусный",  1.15),
}
_WARDROBE_WIDTH = {
    "w20": ("до 2 м",   1.8),
    "w30": ("2–3 м",    2.5),
    "w35": ("больше 3 м", 3.5),
}

# Гардеробная: цена за м² площади под систему хранения
_WALKIN_PER_M2 = {
    "base":  ("Базовая: рейл + полки + обувница",      22, 30),
    "full":  ("Полная: выдвижные корзины + подсветка", 28, 40),
}
_WALKIN_AREA = {
    "a24": ("2–4 м²", 3.0),
    "a46": ("4–6 м²", 5.0),
    "a67": ("больше 6 м²", 7.0),
}

# Прихожая: цена за погонный метр
_HALLWAY_PER_M = {
    "base": ("Базовая: вешалка + обувница",              28, 38),
    "full": ("Полная: + зеркало, сиденье, антресоли",    35, 50),
}
_HALLWAY_LEN = {
    "l15": ("до 1,5 м",    1.2),
    "l25": ("1,5–2,5 м",   2.0),
    "l30": ("больше 2,5 м", 3.0),
}

# ═══════════════════════════════════════════════════════════════════════════
# Кнопки
# ═══════════════════════════════════════════════════════════════════════════

def _kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _contacts_row(prefix: str = "calc") -> list:
    return [
        InlineKeyboardButton(text="📐 Бесплатный замер", callback_data="measure:start"),
        InlineKeyboardButton(text="💬 WhatsApp", url=f"https://wa.me/{PHONE_DIGITS}"),
        InlineKeyboardButton(text="📞 Позвонить", url=f"tel:+{PHONE_DIGITS}"),
    ]


def _type_keyboard() -> InlineKeyboardMarkup:
    return _kb([[
        InlineKeyboardButton(text="🍳 Кухня", callback_data="calc:kitchen"),
        InlineKeyboardButton(text="🚪 Шкаф-купе", callback_data="calc:wardrobe"),
    ], [
        InlineKeyboardButton(text="👗 Гардеробная", callback_data="calc:walkin"),
        InlineKeyboardButton(text="👟 Прихожая", callback_data="calc:hallway"),
    ]])


def _sub_keyboard(kind: str) -> InlineKeyboardMarkup:
    if kind == "kitchen":
        rows = [[
            InlineKeyboardButton(text=n, callback_data=f"calc:kitchen:{k}")
        ] for k, (n, _) in _KITCHEN_SHAPE.items()]
    elif kind == "wardrobe":
        rows = [[
            InlineKeyboardButton(text=n, callback_data=f"calc:wardrobe:{k}")
        ] for k, (n, _) in _WARDROBE_TYPE.items()]
    elif kind == "walkin":
        rows = [[
            InlineKeyboardButton(text=n, callback_data=f"calc:walkin:{k}")
        ] for k, (n, _) in _WALKIN_PER_M2.items()]
    else:  # hallway
        rows = [[
            InlineKeyboardButton(text=n, callback_data=f"calc:hallway:{k}")
        ] for k, (n, _) in _HALLWAY_PER_M.items()]
    rows.append([InlineKeyboardButton(text="⬅️ С самого начала", callback_data="calc:restart")])
    return _kb(rows)


def _size_keyboard(kind: str, sub: str) -> InlineKeyboardMarkup:
    if kind == "kitchen":
        rows = [[InlineKeyboardButton(text=n, callback_data=f"calc:kitchen:{sub}:{k}")]
                for k, (n, _) in _KITCHEN_SIZE.items()]
    elif kind == "wardrobe":
        rows = [[InlineKeyboardButton(text=n, callback_data=f"calc:wardrobe:{sub}:{k}")]
                for k, (n, _) in _WARDROBE_WIDTH.items()]
    elif kind == "walkin":
        rows = [[InlineKeyboardButton(text=n, callback_data=f"calc:walkin:{sub}:{k}")]
                for k, (n, _) in _WALKIN_AREA.items()]
    else:
        rows = [[InlineKeyboardButton(text=n, callback_data=f"calc:hallway:{sub}:{k}")]
                for k, (n, _) in _HALLWAY_LEN.items()]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"calc:{kind}")])
    return _kb(rows)


def _material_keyboard(kind: str, sub: str, size: str) -> InlineKeyboardMarkup:
    if kind == "kitchen":
        rows = [[InlineKeyboardButton(text=n, callback_data=f"calc:kitchen:{sub}:{size}:{k}")]
                for k, (n, _, _) in _KITCHEN_PER_M.items()]
    elif kind == "wardrobe":
        rows = [[InlineKeyboardButton(text=n, callback_data=f"calc:wardrobe:{sub}:{size}:{k}")]
                for k, (n, _, _) in _WARDROBE_PER_M2.items()]
    else:  # walkin / hallway — материал уже выбран (sub), это шаг «готово» не нужен
        rows = []
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"calc:{kind}:{sub}")])
    return _kb(rows)

# ═══════════════════════════════════════════════════════════════════════════
# Расчёт
# ═══════════════════════════════════════════════════════════════════════════

def _fmt_range(low: float, high: float) -> str:
    """96 000–132 000 ₽ (low/high в рублях, округление half-up до тысячи)."""
    lo = max(1000, int(low / 1000.0 + 0.5) * 1000)
    hi = max(lo + 1000, int(high / 1000.0 + 0.5) * 1000)
    def _th(n):
        return f"{n:,}".replace(",", " ")
    return f"{_th(lo)}–{_th(hi)} ₽"


def calculate(kind: str, sub: str, size: str, material: str = "") -> str:
    """Детерминированный расчёт диапазона цены. Возвращает готовый текст.

    Прайс-модель — в ТЫСЯЧАХ ₽; в _fmt_range передаём рубли (×1000).
    """
    if kind == "kitchen":
        shape_name, shape_mult = _KITCHEN_SHAPE.get(sub, _KITCHEN_SHAPE["straight"])
        size_name, size_m = _KITCHEN_SIZE.get(size, _KITCHEN_SIZE["s30"])
        if material not in _KITCHEN_PER_M:
            return ""
        mat_name, lo_m, hi_m = _KITCHEN_PER_M[material]
        low = size_m * lo_m * shape_mult
        high = size_m * hi_m * shape_mult
        head = f"🍳 Кухня {shape_name.lower()}, {size_name}, фасады — {mat_name.lower()}"
        note = (
            "В стоимость уже входит: корпус ЛДСП, фурнитура с доводчиками, "
            "столешница постформинг. Камень, подсветка и техника — отдельно."
        )
    elif kind == "wardrobe":
        type_name, type_mult = _WARDROBE_TYPE.get(sub, _WARDROBE_TYPE["vstro"])
        width_name, width_m = _WARDROBE_WIDTH.get(size, _WARDROBE_WIDTH["w30"])
        if material not in _WARDROBE_PER_M2:
            return ""
        mat_name, lo_m, hi_m = _WARDROBE_PER_M2[material]
        area = width_m * 2.5  # высота до потолка
        low = area * lo_m * type_mult
        high = area * hi_m * type_mult
        head = (f"🚪 Шкаф-купе {type_name.lower()}, ширина {width_name} "
                f"(~{area:.1f} м² дверей), фасады — {mat_name.lower()}")
        note = ("Наполнение (штанги, полки, корзины) подберём под ваш гардероб "
                "на бесплатном замере — оно влияет на итог ±15%.")
    elif kind == "walkin":
        if sub not in _WALKIN_PER_M2:
            return ""
        mat_name, lo_m, hi_m = _WALKIN_PER_M2[sub]
        size_name, area = _WALKIN_AREA.get(size, _WALKIN_AREA["a24"])
        low = area * lo_m
        high = area * hi_m
        head = f"👗 Гардеробная {size_name}, {mat_name.lower()}"
        note = ("Система хранения проектируется под ваши вещи: длинное/короткое "
                "плечо, обувь, чемоданы. Расчёт — после замера.")
    elif kind == "hallway":
        if sub not in _HALLWAY_PER_M:
            return ""
        mat_name, lo_m, hi_m = _HALLWAY_PER_M[sub]
        size_name, length_m = _HALLWAY_LEN.get(size, _HALLWAY_LEN["l25"])
        low = length_m * lo_m
        high = length_m * hi_m
        head = f"👟 Прихожая, длина {size_name}, {mat_name.lower()}"
        note = ("Добавим зеркало и закрытые секции под верхнюю одежду — "
                "в маленьком коридоре это меняет всё.")
    else:
        return ""

    return (
        f"{head}\n\n"
        f"💰 Ориентировочно: <b>{_fmt_range(low * 1000, high * 1000)}</b>\n\n"
        f"📌 {note}\n\n"
        f"🗃 Идеи наполнения и хранения для этой мебели — команда /storage.\n\n"
        f"📐 Точную цену назовём после бесплатного замера — "
        f"замер ни к чему не обязывает."
    )

# ═══════════════════════════════════════════════════════════════════════════
# Хендлеры
# ═══════════════════════════════════════════════════════════════════════════

_INTRO = (
    "🧮 <b>Калькулятор мебели</b>\n\n"
    "Посчитаю ориентировочную стоимость за 3 шага.\n"
    "Что считаем?"
)


@calc_router.message(Command("calc"))
async def cmd_calc(message: Message):
    """Старт калькулятора — личка и группы."""
    u = message.from_user
    if u and message.chat.type == "private":
        from bot import database as db
        await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    await message.reply(_INTRO, reply_markup=_type_keyboard())


@calc_router.callback_query(F.data == "calc:restart")
async def cb_restart(cb: CallbackQuery):
    await cb.message.edit_text(_INTRO, reply_markup=_type_keyboard())
    await cb.answer()


@calc_router.callback_query(F.data.regexp(r"^calc:(kitchen|wardrobe|walkin|hallway)$"))
async def cb_type(cb: CallbackQuery):
    kind = cb.data.split(":")[1]
    if kind == "kitchen":
        text = "🍳 <b>Кухня.</b> Какая планировка?"
    elif kind == "wardrobe":
        text = "🚪 <b>Шкаф-купе.</b> Конструкция?"
    elif kind == "walkin":
        text = "👗 <b>Гардеробная.</b> Начинка?"
    else:
        text = "👟 <b>Прихожая.</b> Комплектация?"
    await cb.message.edit_text(text, reply_markup=_sub_keyboard(kind))
    await cb.answer()


@calc_router.callback_query(F.data.regexp(r"^calc:(kitchen|wardrobe|walkin|hallway):(\w+)$"))
async def cb_sub(cb: CallbackQuery):
    _, kind, sub = cb.data.split(":")
    if kind == "kitchen":
        text = "🍳 <b>Кухня.</b> Какая длина по стене?"
    elif kind == "wardrobe":
        text = "🚪 <b>Шкаф-купе.</b> Ширина?"
    elif kind == "walkin":
        text = "👗 <b>Гардеробная.</b> Площадь помещения?"
    else:
        text = "👟 <b>Прихожая.</b> Длина коридора?"
    await cb.message.edit_text(text, reply_markup=_size_keyboard(kind, sub))
    await cb.answer()


@calc_router.callback_query(F.data.regexp(r"^calc:(kitchen|wardrobe):(\w+):(\w+)$"))
async def cb_size_needs_material(cb: CallbackQuery):
    """Кухня и шкаф-купе: после размера выбираем материал."""
    _, kind, sub, size = cb.data.split(":")
    if kind == "kitchen":
        text = "🍳 <b>Кухня.</b> Материал фасадов?"
    else:
        text = "🚪 <b>Шкаф-купе.</b> Двери?"
    await cb.message.edit_text(text, reply_markup=_material_keyboard(kind, sub, size))
    await cb.answer()


@calc_router.callback_query(F.data.regexp(r"^calc:(walkin|hallway):(\w+):(\w+)$"))
async def cb_size_two_step(cb: CallbackQuery):
    """Гардеробная/прихожая: материал выбран на шаге 2 — считаем."""
    _, kind, sub, size = cb.data.split(":")
    result = calculate(kind, sub, size)
    if not result:
        await cb.answer("Не получилось — начните заново", show_alert=True)
        return
    await cb.message.edit_text(result, reply_markup=_kb([_contacts_row()]))
    await cb.answer()


@calc_router.callback_query(F.data.regexp(r"^calc:(kitchen|wardrobe):(\w+):(\w+):(\w+)$"))
async def cb_full(cb: CallbackQuery):
    _, kind, sub, size, material = cb.data.split(":")
    result = calculate(kind, sub, size, material)
    if not result:
        await cb.answer("Не получилось — начните заново", show_alert=True)
        return
    await cb.message.edit_text(result, reply_markup=_kb([_contacts_row()]))
    await cb.answer()
