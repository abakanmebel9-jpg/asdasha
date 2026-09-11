"""
Даша Lead Forms — заявка на бесплатный замер (/measure).

Мини-воронка на FSM (MemoryStorage уже в Dispatcher):
имя → тип мебели → адрес/район → телефон → удобное время → сохранение в БД
+ мгновенное уведомление владельцу (OWNER_ID/ADMIN_IDS).

Кнопки «Бесплатный замер» из /calc и /consult заводят сюда же
(callback «measure:start»). Работает в личке и группах (в группах бот
просит написать в личку, чтобы телефон не увидели чужие).

Строго корпусная мебель.
"""

import logging
import re
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
)
from aiogram.filters import Command
from bot.config import config

logger = logging.getLogger("dasha.leads")

lead_router = Router()

PHONE = config.PHONE or "+7 (913) 448-37-17"
PHONE_DIGITS = "".join(ch for ch in PHONE if ch.isdigit())


class MeasureForm(StatesGroup):
    name = State()
    furniture_type = State()
    address = State()
    phone = State()
    time = State()


def _kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


_CANCEL_ROW = [InlineKeyboardButton(text="✖️ Отменить заявку", callback_data="measure:cancel")]


def _furniture_keyboard() -> InlineKeyboardMarkup:
    return _kb([
        [InlineKeyboardButton(text="🍳 Кухня", callback_data="mtype:Кухня")],
        [InlineKeyboardButton(text="🚪 Шкаф-купе", callback_data="mtype:Шкаф-купе")],
        [InlineKeyboardButton(text="👗 Гардеробная", callback_data="mtype:Гардеробная")],
        [InlineKeyboardButton(text="👟 Прихожая", callback_data="mtype:Прихожая")],
        [InlineKeyboardButton(text="🛋 Другое (стенка, детская, ванная…)", callback_data="mtype:Другое")],
        _CANCEL_ROW,
    ])


def _skip_address_keyboard() -> InlineKeyboardMarkup:
    return _kb([
        [InlineKeyboardButton(text="⏭ Пропустить — скажу при звонке", callback_data="maddr:skip")],
        _CANCEL_ROW,
    ])


def _time_keyboard() -> InlineKeyboardMarkup:
    return _kb([
        [InlineKeyboardButton(text="🌅 Сегодня-завтра", callback_data="mtime:Сегодня-завтра")],
        [InlineKeyboardButton(text="📅 На этой неделе", callback_data="mtime:На этой неделе")],
        [InlineKeyboardButton(text="🗓 В выходные", callback_data="mtime:В выходные")],
        _CANCEL_ROW,
    ])


def _phone_keyboard() -> InlineKeyboardMarkup:
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )


_STEP_EMOJI = {"name": "1️⃣", "type": "2️⃣", "address": "3️⃣", "phone": "4️⃣", "time": "5️⃣"}


def _progress(done: list) -> str:
    """Прогресс-полоска: ✅ шаги, ⬜ будущие."""
    order = ["name", "type", "address", "phone", "time"]
    return "".join("✅" if k in done else "⬜" for k in order)


def _format_phone(raw: str) -> str:
    """Приводит любой номер к красивому виду +7 XXX XXX-XX-XX (по возможности)."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] in "78":
        return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    if len(digits) == 10:
        return f"+7 {digits[0:3]} {digits[3:6]}-{digits[6:8]}-{digits[8:10]}"
    return raw.strip()


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return digits


async def _notify_owner(bot, req_id: int, data: dict, from_user) -> str:
    """Уведомляет владельца о новой заявке. Возвращает краткий статус для подтверждения."""
    uname = f"@{data['username']}" if data.get("username") else "—"
    text = (
        f"🔔 <b>Новая заявка на замер #{req_id}</b>\n\n"
        f"👤 {data.get('name', '—')} ({uname}, id <code>{data.get('user_id', '')}</code>)\n"
        f"🛋 {data.get('furniture_type', '—')}\n"
        f"📍 {data.get('address') or 'не указан'}\n"
        f"📞 {data.get('phone', '—')}\n"
        f"🕐 {data.get('preferred_time', '—')}\n\n"
        f"💬 <a href=\"tg://user?id={data.get('user_id', '')}\">Написать клиенту</a>"
    )
    targets = [config.OWNER_ID] + list(config.ADMIN_IDS)
    sent_any = False
    for tid in dict.fromkeys(t for t in targets if t):
        try:
            await bot.send_message(tid, text, parse_mode="HTML", disable_web_page_preview=True)
            sent_any = True
        except Exception as e:
            logger.warning(f"owner notify failed ({tid}): {e}")
    return "владелец уведомлён 🔔" if sent_any else "(уведомление не доставлено — свяжитесь по телефону)"


# ═══════════════════════════════════════════════════════════════════════════
# Команды и старты
# ═══════════════════════════════════════════════════════════════════════════

_INTRO = (
    "📐 <b>Бесплатный замер</b>\n\n"
    "Замерщик приедет, измерит стены с учётом всех неровностей, "
    "обсудим планировку и материалы. Это бесплатно и ни к чему не обязывает.\n\n"
    "Займёт 1 минуту — отвечу на 5 коротких вопросов.\n\n"
    "❓ <b>Как вас зовут?</b>"
)


@lead_router.message(Command("measure"))
async def cmd_measure(message: Message, state: FSMContext):
    """Заявка на замер — в личке полная форма, в группах мягкая подсказка."""
    if message.chat.type != "private":
        u = message.from_user
        await message.reply(
            f"📐 Замер бесплатный! Чтобы телефон остался только у нас, "
            f"напишите мне в личку @{config.BOT_USERNAME} — заполним за минуту 🙂"
        )
        return
    u = message.from_user
    if u:
        from bot import database as db
        await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    await state.set_state(MeasureForm.name)
    await state.update_data(user_id=message.from_user.id if message.from_user else 0,
                            username=message.from_user.username if message.from_user else "",
                            first_name=message.from_user.first_name if message.from_user else "")
    await message.reply(_INTRO, reply_markup=_kb([_CANCEL_ROW]))


@lead_router.callback_query(F.data == "measure:start")
async def cb_measure_start(cb: CallbackQuery, state: FSMContext):
    """Кнопка «Бесплатный замер» из калькулятора/консультаций."""
    if cb.message.chat.type != "private":
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.reply(
            f"📐 Замер бесплатный! Заполним заявку в личке @{config.BOT_USERNAME} — за минуту 🙂"
        )
        await cb.answer()
        return
    u = cb.from_user
    if u:
        from bot import database as db
        await db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "", u.is_bot, in_private=True)
    await state.set_state(MeasureForm.name)
    await state.update_data(user_id=cb.from_user.id,
                            username=cb.from_user.username or "",
                            first_name=cb.from_user.first_name or "")
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.reply(_INTRO, reply_markup=_kb([_CANCEL_ROW]))
    await cb.answer()


@lead_router.callback_query(F.data == "measure:cancel")
async def cb_measure_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    try:
        from aiogram.types import ReplyKeyboardRemove
        await cb.message.reply("Заявку отменила. Если передумаете — /measure или просто напишите 🙌",
                               reply_markup=ReplyKeyboardRemove())
    except Exception:
        await cb.message.reply("Заявку отменила. Если передумаете — /measure 🙌")
    await cb.answer()

# ═══════════════════════════════════════════════════════════════════════════
# Шаги формы
# ═══════════════════════════════════════════════════════════════════════════

@lead_router.message(MeasureForm.name, F.text)
async def step_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()[:60]
    if len(name) < 2:
        await message.reply("Напишите имя — хотя бы пару букв 🙂")
        return
    await state.update_data(name=name)
    await state.set_state(MeasureForm.furniture_type)
    await message.reply(
        f"Приятно познакомиться, {name}! {_progress(['name'])}\n\n"
        f"2️⃣ ❓ <b>Какая мебель нужна?</b>",
        reply_markup=_furniture_keyboard(),
    )


@lead_router.callback_query(MeasureForm.furniture_type, F.data.startswith("mtype:"))
async def step_type(cb: CallbackQuery, state: FSMContext):
    ftype = cb.data.split(":", 1)[1]
    await state.update_data(furniture_type=ftype)
    await state.set_state(MeasureForm.address)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.reply(
        f"«{ftype}» — отличный выбор! {_progress(['name', 'type'])}\n\n"
        f"3️⃣ ❓ <b>Район или адрес</b> (например, «Абакан, Покровка»)?\n"
        f"Если пока не готовы назвать — пропустите.",
        reply_markup=_skip_address_keyboard(),
    )
    await cb.answer()


@lead_router.message(MeasureForm.address, F.text)
async def step_address_text(message: Message, state: FSMContext):
    await state.update_data(address=(message.text or "").strip()[:120])
    await _ask_phone(message, state)


@lead_router.callback_query(MeasureForm.address, F.data == "maddr:skip")
async def step_address_skip(cb: CallbackQuery, state: FSMContext):
    await state.update_data(address="")
    await cb.message.edit_reply_markup(reply_markup=None)
    # message.reply недоступен у callback — используем bot.send_message
    await _ask_phone_message(cb.message, state)
    await cb.answer()


async def _ask_phone_message(msg: Message, state: FSMContext):
    data = await state.get_data()
    await state.set_state(MeasureForm.phone)
    await msg.reply(
        f"{_progress(['name', 'type', 'address'])}\n\n"
        f"4️⃣ ❓ <b>Телефон для согласования времени</b>?\n"
        f"Можно кнопкой или напишите номер.",
        reply_markup=_phone_keyboard(),
    )


async def _ask_phone(message: Message, state: FSMContext):
    await _ask_phone_message(message, state)


@lead_router.message(MeasureForm.phone, F.contact)
async def step_phone_contact(message: Message, state: FSMContext):
    raw = message.contact.phone_number or ""
    await _process_phone(message, state, raw)


@lead_router.message(MeasureForm.phone, F.text)
async def step_phone_text(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 10:
        await message.reply("Похоже, в номере не хватает цифр 🤔 Пример: +7 913 448-37-17")
        return
    await _process_phone(message, state, raw)


async def _process_phone(message: Message, state: FSMContext, raw: str):
    pretty = _format_phone(raw)
    await state.update_data(phone=pretty, phone_digits=_normalize_phone(raw))
    try:
        from aiogram.types import ReplyKeyboardRemove
        await message.reply("Записала ✅", reply_markup=ReplyKeyboardRemove())
    except Exception:
        pass
    await state.set_state(MeasureForm.time)
    data = await state.get_data()
    done = ["name", "type", "address"] if data.get("address") else ["name", "type"]
    done += ["phone"]
    await message.reply(
        f"{_progress(done)}\n\n"
        f"5️⃣ ❓ <b>Когда удобно принять замерщика?</b>",
        reply_markup=_time_keyboard(),
    )


_MEASURE_CHECKLIST = (
    "📋 <b>Как подготовиться к замеру</b> (5 минут сейчас — сэкономят час потом):\n\n"
    "1️⃣ Освободите зону будущей мебели — достаточно доступа к стенам\n"
    "2️⃣ Для кухни: запишите размеры крупной техники (холодильник, духовка, ПММ)\n"
    "3️⃣ Сохраните фото интерьеров, которые нравятся — покажете дизайнеру\n"
    "4️⃣ Вспомните, что раздражает в старой мебели — исправим на новом проекте\n"
    "5️⃣ Определитесь с бюджетом — подберём материалы под него без сюрпризов\n\n"
    "Замер с 3D-проектом — бесплатный и ни к чему не обязывает 🛋"
)


@lead_router.callback_query(MeasureForm.time, F.data.startswith("mtime:"))
async def step_time(cb: CallbackQuery, state: FSMContext):
    await state.update_data(preferred_time=cb.data.split(":", 1)[1])
    data = await state.get_data()
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)

    from bot import database as db
    req_id = await db.add_measure_request(
        user_id=data.get("user_id", 0),
        name=data.get("name", ""),
        furniture_type=data.get("furniture_type", ""),
        address=data.get("address", ""),
        phone=data.get("phone", ""),
        preferred_time=data.get("preferred_time", ""),
        username=data.get("username", ""),
        first_name=data.get("first_name", ""),
        chat_title="",
    )
    logger.info(f"Measure request #{req_id} from user {data.get('user_id')}: "
                f"{data.get('furniture_type')}, {data.get('phone')}")
    notify_status = await _notify_owner(cb.message.bot, req_id, data, cb.from_user)
    await cb.message.reply(
        f"🎉 <b>Заявка #{req_id} принята!</b>\n\n"
        f"👤 {data.get('name', '')}\n"
        f"🛋 {data.get('furniture_type', '')}\n"
        f"📞 {data.get('phone', '')}\n"
        f"🕐 {data.get('preferred_time', '')}\n\n"
        f"Перезвоню в течение рабочего дня и согласуем точное время замера. {notify_status}\n\n"
        f"📞 {PHONE} · 🌐 abakanmebel.online",
        reply_markup=_kb([[
            InlineKeyboardButton(text="🧮 Посчитать стоимость", callback_data="calc:restart"),
            InlineKeyboardButton(text="📺 Канал", url="https://t.me/abakan_mebel"),
        ]]),
    )
    # Лид-магнит (раунд 8): чек-лист подготовки — подогревает и повышает ценность
    try:
        await cb.message.reply(_MEASURE_CHECKLIST)
    except Exception as e:
        logger.debug(f"checklist send failed: {e}")
    await cb.answer("Готово!")
