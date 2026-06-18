"""
Admin Handler — Admin-only commands for managing Dasha Bot.
"""

import logging
from typing import Optional

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ChatAction

from bot.config import config
from bot.database import (
    is_user_admin, set_user_admin, block_user,
    get_stats, get_today_post_count, get_unposted_news,
)
from ai.router import ai_router
from channel import channel_manager

logger = logging.getLogger("dasha.handlers.admin")

admin_router = Router()


async def _is_admin(message: Message) -> bool:
    return await is_user_admin(message.from_user.id)


@admin_router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not await _is_admin(message):
        await message.answer("У вас нет прав администратора.")
        return

    stats = await get_stats()
    today_posts = await get_today_post_count()

    text = (
        f"🛠️ Панель администратора Dasha Bot\n\n"
        f"📊 Статистика:\n"
        f"  Пользователей: {stats['total_users']}\n"
        f"  Активных: {stats['active_users']}\n"
        f"  Новостей в базе: {stats['total_news']}\n"
        f"  Непостоянных: {stats['unposted_news']}\n"
        f"  Постов в канале: {stats['total_posts']}\n"
        f"  Сегодня постов: {today_posts}\n"
        f"  Кэшированных: {stats['cached_queries']}\n\n"
        f"Команды:\n"
        f"/status — статус бота\n"
        f"/post — создать пост в канал\n"
        f"/news — показать непостоянные новости\n"
        f"/addadmin <user_id> — добавить админа\n"
        f"/block <user_id> — заблокировать\n"
        f"/unblock <user_id> — разблокировать"
    )
    await message.answer(text)


@admin_router.message(Command("status"))
async def cmd_status(message: Message):
    if not await _is_admin(message):
        return

    from datetime import datetime
    from zoneinfo import ZoneInfo

    moscow_time = datetime.now(ZoneInfo("Europe/Moscow"))
    ai_status = "доступен" if ai_router.primary else "недоступен"
    unposted = await get_unposted_news(limit=1)

    text = (
        f"✅ Dasha Bot работает\n\n"
        f"🤖 AI: {ai_status}\n"
        f"📝 Непостоянных новостей: {len(unposted)}\n"
        f"⏰ Абакан (KRAST): {moscow_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"📡 Канал: {config.CHANNEL_USERNAME}"
    )
    await message.answer(text)


@admin_router.message(Command("post"))
async def cmd_post(message: Message):
    if not await _is_admin(message):
        return

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    args = message.text.split(maxsplit=1)
    topic = args[1] if len(args) > 1 else ""

    if not topic:
        news = await get_unposted_news(limit=5)
        if not news:
            await message.answer("Нет непостоянных новостей. Укажите тему: /post <тема>")
            return
        import random
        item = random.choice(news)
        topic = item["title"]

    response = await ai_router.generate_channel_post(topic=topic)

    if response.error or not response.text:
        await message.answer(f"Ошибка генерации поста: {response.error}")
        return

    preview = f"📝 Предпросмотр поста:\n\n{response.text}\n\nОтправить в канал? /send_post"
    await message.answer(preview)
    message.bot._pending_post = response.text


@admin_router.message(Command("send_post"))
async def cmd_send_post(message: Message):
    if not await _is_admin(message):
        return

    post_text = getattr(message.bot, "_pending_post", None)
    if not post_text:
        await message.answer("Нет поста для отправки. Сначала /post")
        return

    try:
        from channel import _build_footer
        post_text = post_text + "\n" + _build_footer()

        sent = await message.bot.send_message(
            chat_id=config.CHANNEL_ID,
            text=post_text,
        )
        from bot.database import add_channel_post
        await add_channel_post(
            content=post_text, message_id=sent.message_id,
            post_type="admin", source_url="",
        )
        await message.answer(f"✅ Пост опубликован в {config.CHANNEL_USERNAME}")
        message.bot._pending_post = None
    except Exception as e:
        logger.error(f"Error sending post: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@admin_router.message(Command("news"))
async def cmd_news(message: Message):
    if not await _is_admin(message):
        return

    news = await get_unposted_news(limit=10)
    if not news:
        await message.answer("Нет непостоянных новостей.")
        return

    lines = ["📰 Непостоянные новости:\n"]
    for i, item in enumerate(news[:10], 1):
        lines.append(f"{i}. {item['title']}")
        if item.get('source'):
            lines.append(f"   📌 {item['source']}")
        lines.append("")

    await message.answer("\n".join(lines))


@admin_router.message(Command("addadmin"))
async def cmd_addadmin(message: Message):
    if message.from_user.id != config.OWNER_ID:
        await message.answer("Только владелец может добавлять админов.")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /addadmin <user_id>")
        return

    try:
        target_id = int(args[1])
    except ValueError:
        await message.answer("Неверный user_id.")
        return

    await set_user_admin(target_id, True)
    await message.answer(f"✅ Пользователь {target_id} теперь админ.")


@admin_router.message(Command("block"))
async def cmd_block(message: Message):
    if not await _is_admin(message):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /block <user_id>")
        return
    try:
        target_id = int(args[1])
    except ValueError:
        await message.answer("Неверный user_id.")
        return
    await block_user(target_id, True)
    await message.answer(f"🚫 Пользователь {target_id} заблокирован.")


@admin_router.message(Command("unblock"))
async def cmd_unblock(message: Message):
    if not await _is_admin(message):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /unblock <user_id>")
        return
    try:
        target_id = int(args[1])
    except ValueError:
        await message.answer("Неверный user_id.")
        return
    await block_user(target_id, False)
    await message.answer(f"✅ Пользователь {target_id} разблокирован.")