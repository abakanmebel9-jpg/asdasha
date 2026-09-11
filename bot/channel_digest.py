"""
Даша Channel Digest — еженедельный дайджест в канал @abakan_mebel.

Каждое ВОСКРЕСЕНЬЕ в 18:00 по Абакану (Asia/Krasnoyarsk, UTC+7) бот публикует
«Итоги недели»: темы постов недели (из posted_news — устойчиво к рестартам),
совет недели из базы ухода/знаний (детерминированно по номеру недели) и
вопрос аудитории. Без AI-вызовов — надёжно и бесплатно.

Дедуп: ключ digest:YYYY-Www в posted_news (переживает рестарты процесса).
"""

import asyncio
import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger("dasha.channel_digest")

DIGEST_TZ = ZoneInfo("Asia/Krasnoyarsk")
DIGEST_WEEKDAY = 6   # воскресенье
DIGEST_HOUR = 18     # 18:00

_last_check = {"week_key": ""}


def _week_key(now: datetime) -> str:
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _should_send_now(now: datetime, already_posted: bool) -> bool:
    """Пора ли публиковать дайджест (чистая функция — тестируется без сна)."""
    if now.weekday() != DIGEST_WEEKDAY or now.hour != DIGEST_HOUR:
        return False
    return not already_posted


def _shorten_title(title: str, max_len: int = 80) -> str:
    """Заголовок для буллета дайджеста: первое предложение, обрезка по слову."""
    t = re.sub(r"\s+", " ", (title or "").strip())
    # Берём первое предложение, если оно содержательное
    m = re.match(r"^(.{20,120}?[.!?…])(\s|$)", t)
    if m:
        t = m.group(1).rstrip(".!?…")
    if len(t) > max_len:
        cut = t[:max_len].rsplit(" ", 1)[0].rstrip(",;:")
        t = cut + "…"
    return t.strip()


def _tip_of_the_week(week_key: str) -> str:
    """Совет недели: детерминированный выбор из базы ухода (без повторов 8 недель)."""
    from bot.service_info import CARE_TIPS
    # номер недели из «2026-W37» → стабильный индекс
    try:
        n = int(week_key.split("W")[-1])
    except Exception:
        n = 0
    tip = CARE_TIPS[n % len(CARE_TIPS)]
    return f"{tip['title']}: {tip['text']}"


def build_digest_text(titles: list, week_key: str) -> str:
    """Текст дайджеста. Без HTML — публикуется plain-текстом (заголовки внешние)."""
    lines = ["🗓 <b>Итоги недели — мебельный дайджест</b>\n"]
    topics = [_shorten_title(t) for t in titles if t]
    topics = [t for t in topics if len(t) >= 15][:5]
    if topics:
        lines.append("Что разбирали на этой неделе:")
        for t in topics:
            lines.append(f"• {t}")
        lines.append("")
    lines.append(f"💡 <b>Совет недели от Даши</b>\n{_tip_of_the_week(week_key)}")
    lines.append("")
    lines.append("Что разобрать на следующей неделе? Напишите в комментарии 👇")
    lines.append("\n· · ·\n#дайджест #Абакан")
    return "\n".join(lines)


async def maybe_send_digest(bot, channel_id: int, now: datetime | None = None) -> bool:
    """Публикует дайджест, если сейчас воскресенье 18:00 и на этой неделе его ещё не было."""
    from bot import database as db
    from bot.post_utils import validate_post_text

    now = now or datetime.now(DIGEST_TZ)
    wk = _week_key(now)
    dedup_key = f"digest:{wk}"
    try:
        already = bool(await db.get_posted_ts(dedup_key))
    except Exception:
        already = False
    if not _should_send_now(now, already):
        return False

    try:
        week_ago = int(time.time()) - 7 * 86400
        titles = await db.get_posted_titles_since(week_ago, limit=12)
        text = build_digest_text(titles, wk)
        is_valid, reason = validate_post_text(re.sub(r"<[^>]+>", "", text))
        if not is_valid:
            logger.warning(f"Digest validation failed ({reason}) — skip this week")
            return False
        # Публикуем: текст с жирными блоками (parse_mode=HTML), без фото
        await bot.send_message(channel_id, text, parse_mode="HTML", disable_notification=False)
        await db.mark_news_posted(dedup_key, f"digest {wk}")
        logger.info(f"Channel digest posted ({wk}, {len(titles)} topics)")
        return True
    except Exception as e:
        logger.warning(f"Digest send failed: {e}")
        return False


async def digest_loop(bot, channel_id: int) -> None:
    """Проверка каждые 5 минут: воскресенье 18:00 (Абакан) → дайджест."""
    logger.info(f"Channel digest loop started (Sun {DIGEST_HOUR:02d}:00 {str(DIGEST_TZ)})")
    while True:
        try:
            await maybe_send_digest(bot, channel_id)
        except Exception as e:
            logger.warning(f"Digest loop error: {e}")
        await asyncio.sleep(300)
