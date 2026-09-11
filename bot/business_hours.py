"""Даша Business Hours — автоуведомление о нерабочем времени в личке.

Механика: клиент пишет боту вне рабочего дня (Пн–Сб 09:00–20:00, Вс — выходной,
время Красноярска, UTC+7) → перед ответом бот присылает короткое уведомление:
«Сейчас нерабочее время, но я на связи. Даша-человек ответит после 9:00,
заявку на замер можно оставить прямо сейчас».

Бот продолжает отвечать (AI работает 24/7) — уведомление управляет ожиданиями
и конвертирует ночных посетителей в заявки. Анти-навязчивость: не чаще
одного раза в 6 часов на пользователя (дедуп через posted_news «oh:<uid>»,
переживает рестарты).

Только корпусная мебель: кухни, шкафы-купе, гардеробные, прихожие.
"""

import logging
import time
from datetime import datetime, timezone, timedelta

from bot.config import config
from bot import database as db

logger = logging.getLogger("dasha.business_hours")

KRASNOYARSK_TZ = timezone(timedelta(hours=7))
WORK_START = 9    # 09:00
WORK_END = 20     # 20:00
# Пн(0)…Сб(5) — рабочие, Вс(6) — выходной
WORK_WEEKDAYS = {0, 1, 2, 3, 4, 5}
NOTICE_COOLDOWN = 6 * 3600  # 6 часов между уведомлениями одному пользователю

_NOTICE_TEXT = (
    "🌙 Сейчас нерабочее время — обычный график Дашиного производства: "
    "Пн–Сб 09:00–20:00 (Красноярск), воскресенье — выходной.\n\n"
    "Но я на связи прямо сейчас! 🛋\n"
    "Расскажу про материалы, посчитаю стоимость, помогу с идеями — пишите.\n"
    "Заявку на бесплатный замер можно оставить хоть ночью — утром уже перезвоним 😊"
)


def is_working_hours(dt: datetime = None) -> bool:
    """Пн–Сб 09:00–20:00 по Красноярску — рабочее время."""
    now = dt.astimezone(KRASNOYARSK_TZ) if dt else datetime.now(KRASNOYARSK_TZ)
    if now.weekday() not in WORK_WEEKDAYS:
        return False
    return WORK_START <= now.hour < WORK_END


async def maybe_off_hours_notice(bot, chat_id: int, user_id: int) -> bool:
    """Отправляет уведомление о нерабочем времени (если уместно).

    Возвращает True, если уведомление отправлено. Условия:
    - флаг BUSINESS_HOURS_ENABLED включён;
    - сейчас НЕ рабочее время;
    - этому пользователю не отправляли уведомление последние 6 ч.
    """
    if not config.BUSINESS_HOURS_ENABLED:
        return False
    try:
        if is_working_hours():
            return False
        key = f"oh:{user_id}"
        last = await db.get_posted_ts(key)
        now_ts = int(time.time())
        if last and (now_ts - last) < NOTICE_COOLDOWN:
            return False
        try:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📐 Оставить заявку на замер", callback_data="measure:start"),
            ]])
            await bot.send_message(chat_id, _NOTICE_TEXT, reply_markup=kb)
        except Exception as e:
            logger.debug(f"off-hours notice send failed: {e}")
            return False
        await db.mark_news_posted(key, "off-hours notice")
        logger.info(f"Off-hours notice sent to user {user_id}")
        return True
    except Exception as e:
        logger.debug(f"off-hours check failed: {e}")
        return False
