"""
Даша Mistakes — ошибки при заказе мебели (/mistakes).

4 комнаты (кухня, шкаф-купе, гардеробная, прихожая) × 5 реальных ошибок
из практики проектирования: «❌ как делают» → «✅ как правильно».
Навигация inline-кнопками, callback_data = mst:<room> — stateless,
переживает рестарты, БЕЗ AI-вызовов.

Контент согласован с базой знаний dasha.py, /storage и /faq.
Строго корпусная мебель.
"""

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command

logger = logging.getLogger("dasha.mistakes")

mistakes_router = Router()

# ─── Контент: ошибки по комнатам ─────────────────────────────────────────────

MISTAKE_ROOMS: dict = {
    "kitchen": ("Кухня", "🍳"),
    "wardrobe": ("Шкаф-купе", "🚪"),
    "closet": ("Гардеробная", "👗"),
    "hallway": ("Прихожая", "🧥"),
}

MISTAKES: dict = {
    "kitchen": [
        ("Розетки и подводка воды — «по факту, после мебели»",
         "План розеток до заказа: 63 см от пола в рабочей зоне, отдельные линии на варку и духовку. Перенос после установки — сверление по готовому корпусу."),
        ("Замер «по одной стенке» без диагоналей",
         "Стены не параллельны: меряем на трёх уровнях + диагонали, иначе фасады встают клином. Наш замерщик приезжает с лазером — бесплатно."),
        ("Экономия на фурнитуре при дорогих фасадах",
         "Петли и направляющие несут 80% нагрузки. Доводчики Blum/Hettich — тишина и 10+ лет службы за +5–8% бюджета."),
        ("Остров «потому что красиво»",
         "Сначала проходы: 90–120 см вокруг острова, иначе вдвоём не разойдётесь. Эргономика важнее картинки."),
        ("Мойка в глухом углу без доступа",
         "Рабочий треугольник: холодильник — мойка — плита в 2–3 шагах. Угловая мойка — только с каруселью и удобным подводом."),
    ],
    "wardrobe": [
        ("Ширина дверей «на глаз»",
         "Двери купе 60–100 см: уже 60 — теряется доступ к полкам, шире 100 — перегружены ролики."),
        ("Одна штанга на всю высоту",
         "Двухъярусные штанги: рубашки сверху, брюки снизу — вместимость почти вдвое. Верх — под несмятую одежду."),
        ("Не учли плинтус, трубы и розетки у стены",
         "Обходы проектируются на замере: вырезы задней стенки или отступ каркаса. Иначе шкаф «не находит» стену."),
        ("Глубина 45 см «чтобы не съедала комнату»",
         "Минимум 60 см: плечико 44–52 см разворачивается перпендикулярно двери, иначе одежда зажимается створками."),
        ("Профиль и ролики «эконом»",
         "Сталь с доводчиком: дверь не сходит с рельсы, не гремит и не люфтит через год. Это то, что слышно каждый день."),
    ],
    "closet": [
        ("Полки «с запасом» без списка вещей",
         "Считаем гардероб: длинное плечо, короткое, обувь, чемоданы — под каждую категорию своя зона. Порядок живёт годами."),
        ("Одна лампа на потолке",
         "LED-лента в секциях с датчиком движения: вещь видно сразу, поиск занимает секунды."),
        ("Забыли зеркало в полный рост",
         "Зеркало на двери или торце модуля: место не съедает, а примерка удобная."),
        ("Глубокие полки без выдвижных механизмов",
         "Всё глубже 40 см — только выдвижные корзины или ящики, иначе полка превращается в «чёрную дыру»."),
        ("Проход между секциями 50 см",
         "Минимум 70–80 см: нужно место, чтобы примеряться с выдвинутым ящиком за спиной."),
    ],
    "hallway": [
        ("Тумба для обуви глубиной 40 см в узком коридоре",
         "Обувница с полками под 45°: глубина всего 25 см, а вмещает 12–18 пар. Проход остаётся свободным."),
        ("Один верхний светильник",
         "Подсветка зоны крючков и зеркала: собрать сумку и проверить вид утром — минута вместо десяти."),
        ("Открытая вешалка «на всех членов семьи»",
         "Закрытый шкаф для сезонного + крючки «на один выход»: куртки не пачкаются, коридор выглядит чисто."),
        ("Не оставили место для пылесоса и коробок",
         "Секция 40–50 см с розеткой внутри: вертикальный пылесос прячется и всегда заряжен."),
        ("Распашные двери в тесном коридоре",
         "Купе или жалюзийные фасады: не съедают проход при открывании и дают вентиляцию обуви."),
    ],
}

_CLOSING = (
    "💡 Большинство ошибок исправляются на этапе проектирования — "
    "поэтому первый шаг всегда бесплатный замер с лазером 📐"
)

# ─── Клавиатуры ──────────────────────────────────────────────────────────────


def _rooms_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{emoji} {name}", callback_data=f"mst:{key}")]
        for key, (name, emoji) in MISTAKE_ROOMS.items()
    ]
    rows.append([
        InlineKeyboardButton(text="📐 Бесплатный замер", callback_data="measure:start"),
        InlineKeyboardButton(text="🧮 Калькулятор", callback_data="calc:restart"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Другая комната", callback_data="mst:menu")],
        [InlineKeyboardButton(text="📐 Бесплатный замер", callback_data="measure:start"),
         InlineKeyboardButton(text="🧮 Калькулятор", callback_data="calc:restart")],
        [InlineKeyboardButton(text="🗃 Идеи хранения", callback_data="storage:menu")],
    ])


# ─── Тексты ──────────────────────────────────────────────────────────────────

_INTRO = (
    "⚠️ <b>5 ошибок при заказе мебели</b>\n\n"
    "Реальные истории из практики проектирования. Выберите комнату — "
    "расскажу, где теряют деньги и как сделать правильно:"
)


def _room_text(room: str) -> str:
    name, emoji = MISTAKE_ROOMS[room]
    lines = [f"⚠️ <b>Ошибки при заказе — {name}</b> {emoji}\n"]
    for i, (bad, good) in enumerate(MISTAKES[room], 1):
        lines.append(f"<b>{i}. ❌ {bad}</b>")
        lines.append(f"✅ {good}\n")
    lines.append(_CLOSING)
    return "\n".join(lines)


# ─── Хендлеры ────────────────────────────────────────────────────────────────


@mistakes_router.message(Command("mistakes"))
async def cmd_mistakes(message: Message):
    """Меню комнат — личка и группы."""
    await message.reply(_INTRO, reply_markup=_rooms_keyboard())


@mistakes_router.callback_query(F.data == "mst:menu")
async def cb_mistakes_menu(cb: CallbackQuery):
    try:
        await cb.message.edit_text(_INTRO, reply_markup=_rooms_keyboard())
    except Exception:
        pass
    await cb.answer()


@mistakes_router.callback_query(F.data.startswith("mst:"))
async def cb_mistakes_show(cb: CallbackQuery):
    """Показ ошибок по комнате: callback_data = mst:<room> (stateless)."""
    try:
        room = cb.data.split(":")[1]
        if room not in MISTAKES:
            await cb.answer("Такой комнаты пока нет 🙂", show_alert=False)
            return
        text = _room_text(room)
        try:
            await cb.message.edit_text(text, reply_markup=_result_keyboard())
        except Exception:
            await cb.message.reply(text, reply_markup=_result_keyboard())
        await cb.answer()
    except Exception as e:
        logger.warning(f"mistakes callback failed: {e}")
        try:
            await cb.answer("Ошибка, попробуйте ещё раз 🙂", show_alert=False)
        except Exception:
            pass
