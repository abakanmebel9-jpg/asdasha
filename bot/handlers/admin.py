"""Даша Admin handler — owner commands."""
import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from bot.config import config
from bot import database as db
from ai import client as ai_client

logger = logging.getLogger("dasha.admin")
admin_router = Router()

def _is_admin(message):
    uid = message.from_user.id if message.from_user else 0
    return uid == config.OWNER_ID or uid in config.ADMIN_IDS

def _is_admin_id(uid):
    return uid == config.OWNER_ID or uid in config.ADMIN_IDS

@admin_router.message(Command("stats"))
async def cmd_stats(message):
    if not _is_admin(message): return
    s = ai_client.stats()
    await message.reply(f"📊 Статистика AI:\nЗапросов: {s.get('requests',0)}\nOpenClaw: {s.get('openclaw_ok',0)}\nPollinations: {s.get('pollinations_backup',0)}\nStatic: {s.get('static_fallback',0)}\nОшибок: {s.get('fail',0)}\nШлюз: {s.get('gateway','—')}\nПоследняя ошибка: {s.get('last_error','—')[:80]}")

@admin_router.message(Command("providers"))
async def cmd_providers(message):
    if not _is_admin(message): return
    await message.reply(f"🔌 Провайдеры:\n{config.providers_status()}")

@admin_router.message(Command("models"))
async def cmd_models(message):
    if not _is_admin(message): return
    import httpx
    s = ai_client.stats()
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get("https://text.pollinations.ai/models")
        models = r.json() if r.status_code == 200 else []
    except: models = []
    lines = ["🤖 Модели Pollinations:"]
    for m in models: lines.append(f"  • {m.get('name','?')} — {m.get('description','')[:50]}")
    lines.append(f"\n📊 AI stats:\n  Запросов: {s.get('requests',0)}\n  OpenClaw: {s.get('openclaw_ok',0)}\n  Pollinations: {s.get('pollinations_backup',0)}\n  Ошибок: {s.get('fail',0)}")
    await message.reply("\n".join(lines))

@admin_router.message(Command("diag"))
async def cmd_diag(message):
    if not _is_admin(message): return
    c = message.chat
    u = message.from_user
    info = [f"🔧 Диагностика:", f"Бот: @{config.BOT_USERNAME} (id={config.BOT_ID})", f"Чат: id={c.id}, тип={c.type}, title={c.title or '—'}", f"Ты: {u.first_name} (id={u.id})", f"Провайдеры: {config.providers_status()}"]
    try:
        recent = await db.get_recent_group_messages(c.id, limit=5)
        info.append(f"\nЛог сообщений ({len(recent)}):")
        if not recent: info.append("  (пусто)")
        else:
            for m in recent[-5:]:
                who = m.get("first_name") or "?"
                if m.get("user_id") == config.BOT_ID: who = "Даша"
                info.append(f"  {who}: {(m.get('content') or '')[:50]}")
    except: pass
    try: await message.reply("\n".join(info))
    except: pass

@admin_router.message(Command("channel_on"))
async def cmd_channel_on(message):
    if not _is_admin(message): return
    parts = (message.text or "").split()
    if len(parts) < 2: await message.reply("Использование: /channel_on <chat_id>"); return
    try: chat_id = int(parts[1])
    except: await message.reply("chat_id должен быть числом"); return
    await db.set_channel_enabled(chat_id, True)
    await message.reply(f"✅ Реакции для канала {chat_id} включены")

@admin_router.message(Command("channel_off"))
async def cmd_channel_off(message):
    if not _is_admin(message): return
    parts = (message.text or "").split()
    if len(parts) < 2: await message.reply("Использование: /channel_off <chat_id>"); return
    try: chat_id = int(parts[1])
    except: await message.reply("chat_id должен быть числом"); return
    await db.set_channel_enabled(chat_id, False)
    await message.reply(f"🚫 Реакции для канала {chat_id} выключены")

@admin_router.message(Command("broadcast"))
async def cmd_broadcast(message):
    """Рассылка. /broadcast <текст> — всем пользователям бота (с подтверждением).
    /broadcast <chat_id> <текст> — одиночная отправка (старый синтаксис)."""
    if not _is_admin(message): return
    raw = message.text or ""
    # Legacy: одиночная отправка по chat_id
    import re as _re
    m = _re.match(r"/broadcast\s+(-?\d+)\s+(.+)", raw, _re.DOTALL)
    if m:
        try:
            await message.bot.send_message(int(m.group(1)), m.group(2))
            await message.reply("✅ Отправлено")
        except Exception as e:
            await message.reply(f"❌ Ошибка: {e}")
        return
    parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply(
            "Использование: /broadcast <текст>\n\n"
            "Отправит сообщение всем, кто писал боту в личку. "
            "Перед отправкой спросит подтверждение."
        )
        return
    payload = parts[1].strip()[:3900]
    n = len(await db.get_broadcast_users())
    if n == 0:
        await message.reply("Пока некому рассылать — в базе нет пользователей, писавших боту.")
        return
    _pending_broadcasts[message.from_user.id] = payload
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Отправить всем ({n})", callback_data="broadcast:go"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast:no"),
    ]])
    await message.reply(
        f"📤 Рассылка для {n} пользователей:\n\n{payload[:500]}{'…' if len(payload) > 500 else ''}\n\nОтправляем?",
        reply_markup=kb,
    )

_pending_broadcasts = {}  # admin_id → текст рассылки

@admin_router.callback_query(F.data.startswith("broadcast:"))
async def cb_broadcast(callback):
    if not _is_admin_id(callback.from_user.id):
        await callback.answer("Только для админа", show_alert=True); return
    action = (callback.data or ":").split(":", 1)[1]
    payload = _pending_broadcasts.pop(callback.from_user.id, "")
    if action != "go" or not payload:
        try: await callback.message.edit_text("❌ Рассылка отменена.")  # type: ignore
        except Exception: pass
        await callback.answer()
        return
    await callback.answer("🚀 Запускаю рассылку…")
    users = await db.get_broadcast_users()
    status = await callback.message.edit_text(f"📤 Начинаю рассылку: {len(users)} получателей…")  # type: ignore
    sent = failed = 0
    import asyncio as _a
    for i, uid in enumerate(users, 1):
        try:
            await callback.bot.send_message(uid, payload)
            sent += 1
        except Exception:
            failed += 1
        if i % 10 == 0:
            try: await status.edit_text(f"📤 Отправлено {i}/{len(users)} (✅ {sent}, ❌ {failed})")
            except Exception: pass
        await _a.sleep(0.08)  # ~12 сообщ/сек — безопасно под лимитом 30/с
    try:
        await status.edit_text(f"✅ Рассылка завершена: доставлено {sent}, не доставлено {failed}.")
    except Exception: pass

@admin_router.message(Command("post_now"))
async def cmd_post_now(message):
    """Форс-пост в канал: будит шедулер немедленно (антифлуд 60 сек)."""
    if not _is_admin(message): return
    from bot.scheduler_control import request_force_post
    if request_force_post():
        await message.reply("🚀 Форс-пост запрошен — шедулер проснётся и опубликует пост в течение ~30 секунд. Проверь @abakan_mebel.")
    else:
        await message.reply("⏳ Запрос уже был меньше минуты назад — подожди немного.")

@admin_router.message(Command("report"))
async def cmd_report(message):
    """Сводка по боту за 7 дней: заявки на замер, аудитория, посты, AI."""
    if not _is_admin(message): return
    from bot.weekly_report import build_report_text
    report = await build_report_text(7)
    await message.reply(report[:4000])
