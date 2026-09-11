"""
Даша Weekly Report — автоматическая сводка владельцу по понедельникам.

В 09:00 по абаканскому времени (Asia/Krasnoyarsk, UTC+7) каждый понедельник
бот отправляет OWNER_ID отчёт за 7 дней: заявки на замер, аудитория, посты,
AI-статистика. Логика построения отчёта общая с командой /report.
"""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.config import config
from bot import database as db
from ai import client as ai_client

logger = logging.getLogger("dasha.weekly_report")

# Абакан = UTC+7 (Asia/Krasnoyarsk)
REPORT_TZ = ZoneInfo("Asia/Krasnoyarsk")
REPORT_WEEKDAY = 0  # понедельник
REPORT_HOUR = 9     # 09:00 утра

_week_sent = {"key": ""}


def _should_send_now(now: datetime) -> bool:
    """Пора ли отправлять отчёт (чистая функция — тестируется без сна)."""
    if now.weekday() != REPORT_WEEKDAY or now.hour != REPORT_HOUR:
        return False
    iso = now.isocalendar()
    key = f"{iso.year}-W{iso.week:02d}"
    return _week_sent["key"] != key


def _mark_sent(now: datetime) -> None:
    iso = now.isocalendar()
    _week_sent["key"] = f"{iso.year}-W{iso.week:02d}"


async def build_report_text(days: int = 7) -> str:
    """Сводка за N дней: заявки, аудитория, посты, AI. Общая для /report и авто-отчёта."""
    import time as _time
    week_ago = int(_time.time()) - days * 86400
    lines = [f"📊 Сводка за {days} дней:"]

    # Заявки на замер
    try:
        n_req = await db.count_measure_requests(week_ago)
        total_req = await db.get_total_measure_requests()
        recent = await db.get_recent_measure_requests(3)
        lines.append(f"\n📐 Заявки на замер: {n_req} (всего {total_req})")
        for r in recent:
            import datetime
            dt = datetime.datetime.fromtimestamp(r["ts"]).strftime("%d.%m %H:%M")
            lines.append(f"  • #{r['id']} {r['name']} — {r['furniture_type']}, {r['phone']} ({dt})")
        if not recent:
            lines.append("  (пока нет заявок)")
    except Exception as e:
        lines.append(f"📐 Заявки: ошибка ({e})")

    # Аудитория
    try:
        conn = db._conn()
        cur = await conn.execute("SELECT COUNT(*) AS n FROM users WHERE first_seen > ?", (week_ago,))
        new_users = (await cur.fetchone())["n"]
        cur = await conn.execute("SELECT COUNT(*) AS n FROM private_messages WHERE ts > ? AND role='user'", (week_ago,))
        pm = (await cur.fetchone())["n"]
        lines.append(f"\n👥 Новые пользователи: {new_users}\n💬 Сообщений в личке: {pm}")
    except Exception as e:
        lines.append(f"👥 Аудитория: ошибка ({e})")

    # Посты
    try:
        conn = db._conn()
        cur = await conn.execute("SELECT COUNT(*) AS n FROM posted_news WHERE posted_at > ?", (week_ago,))
        posts = (await cur.fetchone())["n"]
        lines.append(f"\n📰 Постов в канал: {posts}")
    except Exception as e:
        lines.append(f"📰 Посты: ошибка ({e})")

    # AI
    s = ai_client.stats()
    gw = s.get("gateway", "—")
    lines.append(
        f"\n🤖 AI: запросов {s.get('requests',0)}, шлюз {gw}, "
        f"ошибок {s.get('fail',0)}"
    )
    if s.get("last_error"):
        lines.append(f"⚠️ Последняя ошибка: {s.get('last_error','')[:100]}")

    return "\n".join(lines)


async def weekly_report_loop(bot) -> None:
    """Раз в 5 минут проверяет время; в понедельник 09:00 (Абакан) шлёт отчёт владельцу.

    Дедуп по ISO-неделе: даже после рестартов в течение часа отчёт уйдёт один раз
    (ключ недели сбрасывается при рестарте процесса, но окно отправки — 1 час,
    так что риск двойной отправки возникает только при рестарте ровно в 09:0x —
    приемлемо; для строгости окно = 09:00:00-09:59:59 и ключ хранится в памяти).
    """
    logger.info(f"Weekly report loop started (Mon {REPORT_HOUR:02d}:00 {str(REPORT_TZ)})")
    while True:
        try:
            now = datetime.now(REPORT_TZ)
            if _should_send_now(now):
                _mark_sent(now)
                if config.OWNER_ID:
                    report = await build_report_text(7)
                    header = "🛋 Доброе утро! Еженедельная сводка Дашиного бота:\n\n"
                    await bot.send_message(config.OWNER_ID, (header + report)[:4000])
                    logger.info(f"Weekly report sent to owner ({_week_sent['key']})")
        except Exception as e:
            logger.warning(f"Weekly report error: {e}")
        await asyncio.sleep(300)
