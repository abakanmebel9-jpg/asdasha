"""Даша Channel Pin — закреплённый пост «Как заказать» в канале.

Новый подписчик сразу видит в шапке канала: что делаем, этапы, гарантию,
контакты и кнопки (замер / сайт / WhatsApp). Публикуется один раз (дедуп в
БД, ключ pin:info); повтор — админ-командой /pin_info (владелец).
"""
import logging

from bot import database as db
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger("dasha.channel_pin")

_PHONE = "+7 (913) 448-37-17"
_PHONE_DIGITS = "79134483717"

_PIN_KEY = "pin:info"


def build_pin_text() -> str:
    """Текст закрепа (чистая функция — тестируется без Telegram)."""
    import html as _h
    phone = _h.escape(_PHONE)
    return (
        "🛋 <b>Мебель на заказ в Абакане</b> — кухни, шкафы-купе, гардеробные, прихожие.\n\n"
        "📐 Замер и 3D-проект — <b>бесплатно</b>\n"
        "🏭 Собственное производство: массив, МДФ-эмаль, ЛДСП Е0,5\n"
        "🛡 Гарантия — <b>2 года</b>\n"
        "📅 Сроки: кухня 15–25 дней, шкаф 10–15\n\n"
        f"📞 <a href=\"tel:+{_PHONE_DIGITS}\">{phone}</a> | "
        "<a href=\"https://abakanmebel.online\">abakanmebel.online</a>\n"
        "Пишите — рассчитаю по вашим размерам 👇"
    )


def build_pin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📐 Бесплатный замер", callback_data="measure:start"),
         InlineKeyboardButton(text="🌐 Каталог и цены", url="https://abakanmebel.online")],
        [InlineKeyboardButton(text="💬 Написать в WhatsApp", url=f"https://wa.me/{_PHONE_DIGITS}")],
    ])


async def ensure_pinned_info(bot: Bot, channel_id: int, force: bool = False) -> bool:
    """Публикует и закрепляет инфо-пост. force=True — повторить даже если был.

    Возвращает True, если пост опубликован. Любая ошибка — тихо логируется
    (закреп — опция, не критерий запуска бота).
    """
    try:
        if not force and await db.get_posted_ts(_PIN_KEY) > 0:
            return False
        msg = await bot.send_message(
            channel_id,
            build_pin_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=build_pin_kb(),
            disable_notification=True,
        )
        try:
            await bot.pin_chat_message(channel_id, msg.message_id, disable_notification=True)
        except Exception as e:
            logger.warning(f"pin failed (post stays in feed): {e}")
        await db.mark_news_posted(_PIN_KEY, "channel-pin-info")
        logger.info(f"Channel pin posted (msg {msg.message_id}, force={force})")
        return True
    except Exception as e:
        logger.warning(f"ensure_pinned_info failed: {e}")
        return False
