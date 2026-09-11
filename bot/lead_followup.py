"""Даша Lead Follow-up — вежливый чек-ин клиенту через 24 ч после заявки на замер.

Заявка «висит» со статусом 'new' больше 24 часов → бот сам пишет клиенту:
«Удалось выбрать время? Планы изменились — скажите». Однократно на заявку
(статус → 'followed_up'), только в окне 10:00–20:00 по Абакану, чтобы не
дёргать людей ночью. Цикл проверяет каждые 10 минут.
"""
import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.config import config
from bot import database as db
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger("dasha.lead_followup")

_TZ_ABAKAN = ZoneInfo("Asia/Krasnoyarsk")
_AGE_SEC = 24 * 3600          # заявке больше суток
_MAX_AGE_SEC = 7 * 24 * 3600  # старше недели — уже не трогаем
_SEND_HOUR_FROM = 10
_SEND_HOUR_TO = 20
_CHECK_EVERY_SEC = 600        # 10 минут


def get_followup_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📅 Записаться на замер", callback_data="measure:start"),
    ], [
        InlineKeyboardButton(text="📞 Позвонить Даше", url="tel:+79134483717"),
        InlineKeyboardButton(text="💬 WhatsApp", url="https://wa.me/79134483717"),
    ]])


def build_followup_text(req: dict) -> str:
    """Текст follow-up (чистая функция — тестируется без Telegram)."""
    name = (req.get("name") or "").strip()
    ftype = (req.get("furniture_type") or "мебель").strip()
    hello = f"{name}, здравствуйте! " if name else "Здравствуйте! "
    return (
        f"{hello}Вчера вы оставляли заявку на бесплатный замер ({ftype}). "
        f"Удалось выбрать удобное время? 📅\n\n"
        f"Если планы изменились или остались вопросы — просто ответьте здесь, "
        f"я на связи 🙂"
    )[:4000]


async def get_stale_requests():
    """Заявки старше 24 ч (но младше недели) со статусом 'new'."""
    now = int(time.time())
    cur = await db._conn().execute(
        "SELECT * FROM measure_requests WHERE status='new' AND ts <= ? AND ts >= ? ORDER BY id LIMIT 5",
        (now - _AGE_SEC, now - _MAX_AGE_SEC),
    )
    return [dict(r) for r in await cur.fetchall()]


async def process_followups(bot: Bot) -> int:
    """Отправляет follow-up по «зависшим» заявкам. Возвращает число отправленных.

    Окно 10:00–20:00 по Абакану: вне окна — пропуск (заявка подождёт следующий цикл).
    """
    hour = datetime.now(_TZ_ABAKAN).hour
    if not (_SEND_HOUR_FROM <= hour < _SEND_HOUR_TO):
        return 0
    sent = 0
    for req in await get_stale_requests():
        try:
            await bot.send_message(
                req["user_id"],
                build_followup_text(req),
                reply_markup=get_followup_kb(),
            )
            await db._conn().execute(
                "UPDATE measure_requests SET status='followed_up' WHERE id=?", (req["id"],)
            )
            await db._conn().commit()
            sent += 1
            logger.info(f"Follow-up sent for request #{req['id']} ({req.get('name', '')})")
        except Exception as e:
            # Пользователь мог заблокировать бота — помечаем, чтобы не пытаться снова
            logger.warning(f"Follow-up for #{req['id']} failed: {e}")
            try:
                await db._conn().execute(
                    "UPDATE measure_requests SET status='followup_failed' WHERE id=?", (req["id"],)
                )
                await db._conn().commit()
            except Exception:
                pass
    return sent


async def lead_followup_loop(bot: Bot):
    """Фоновый цикл: проверка раз в 10 минут, восстановление после ошибок."""
    while True:
        try:
            await process_followups(bot)
        except Exception as e:
            logger.error(f"lead_followup_loop error: {e}")
        await asyncio.sleep(_CHECK_EVERY_SEC)
