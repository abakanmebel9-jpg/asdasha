"""
Inline Handler — @asdasha_bot inline mode.
Users can type @asdasha_bot <query> in any chat to get furniture design advice.
"""

import logging
import hashlib
from typing import Optional, List

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    ChosenInlineResult,
)

from bot.config import config, persona
from ai.router import ai_router

logger = logging.getLogger("dasha.handlers.inline")

inline_router = Router()


def _detect_query_type(text: str) -> str:
    text_lower = text.lower().strip()
    if any(kw in text_lower for kw in ["материал", "мдф", "дерево", "массив", "фурнитур"]):
        return "materials"
    if any(kw in text_lower for kw in ["стил", "интерьер", "дизайн", "цвет", "отделк"]):
        return "style"
    if any(kw in text_lower for kw in ["размер", "высот", "ширин", "стандарт"]):
        return "sizes"
    if any(kw in text_lower for kw in ["кухн", "шкаф", "кровать", "диван", "стол"]):
        return "furniture"
    if any(kw in text_lower for kw in ["заказ", "доставк", "цен", "стоимост", "телефон"]):
        return "order"
    return "general"


@inline_router.inline_query()
async def handle_inline_query(inline_query: InlineQuery):
    query = inline_query.query.strip()

    if not query:
        results = [
            InlineQueryResultArticle(
                id="help",
                title="Даша — Дизайнер мебели",
                description="Задайте вопрос о дизайне, мебели, материалах, интерьерах",
                input_message_content=InputTextMessageContent(
                    message_text=(
                        "🛋 Даша — дизайнер мебели из Абакана!\n\n"
                        "Могу помочь с:\n"
                        "• Дизайном интерьера\n"
                        "• Выбором материалов и фурнитуры\n"
                        "• Подбором мебели по размерам\n"
                        "• Консультацией по заказу\n\n"
                        "Напишите @asdasha_bot и ваш вопрос!"
                    ),
                ),
            ),
        ]
        await inline_query.answer(results, cache_time=30)
        return

    query_type = _detect_query_type(query)

    try:
        import asyncio
        inline_user_id = -(hash(query) % 100000) - 1

        response = await asyncio.wait_for(
            ai_router.chat(
                user_id=inline_user_id,
                message=query,
                use_cache=True,
                save_history=False,
            ),
            timeout=20.0,
        )

        if response.error or not response.text:
            results = [
                InlineQueryResultArticle(
                    id="error",
                    title="Не удалось получить ответ",
                    description="Попробуйте переформулировать",
                    input_message_content=InputTextMessageContent(
                        message_text=f"🛋 Вопрос: {query}\n\n⚠️ Не удалось ответить. Напишите в личку @asdasha_bot",
                    ),
                ),
            ]
            await inline_query.answer(results, cache_time=10)
            return

        reply_text = _clean_markdown(response.text)
        if len(reply_text) > 4000:
            reply_text = reply_text[:3997] + "..."

        # Add footer with contact info
        footer = f"\n\n━━━━━━━━━━━━━━\n🛋 Дизайн и производство мебели\n🌐 {config.WEBSITE}\nАвтор @asdasha_bot"
        reply_text += footer

        main_id = hashlib.md5(f"main_{query}".encode()).hexdigest()[:16]
        results = [
            InlineQueryResultArticle(
                id=main_id,
                title=f"🛋 Ответ Даши",
                description=reply_text[:80],
                input_message_content=InputTextMessageContent(
                    message_text=reply_text,
                ),
            ),
        ]

        await inline_query.answer(results, cache_time=60, is_personal=True)

    except asyncio.TimeoutError:
        logger.warning(f"Inline query timed out: {query[:50]}")
        results = [
            InlineQueryResultArticle(
                id="timeout",
                title="Даша думает слишком долго",
                description="Напишите в личку @asdasha_bot",
                input_message_content=InputTextMessageContent(
                    message_text=f"🛋 Вопрос: {query}\n\n⏱️ Напишите в личку @asdasha_bot для быстрого ответа",
                ),
            ),
        ]
        await inline_query.answer(results, cache_time=10)
    except Exception as e:
        logger.error(f"Inline query error: {e}")
        results = [
            InlineQueryResultArticle(
                id="error",
                title="Ошибка",
                description="Попробуйте ещё раз",
                input_message_content=InputTextMessageContent(
                    message_text=f"🛋 Вопрос: {query}\n\n⚠️ Ошибка. Напишите @asdasha_bot",
                ),
            ),
        ]
        await inline_query.answer(results, cache_time=10)


@inline_router.chosen_inline_result()
async def handle_chosen_inline_result(chosen: ChosenInlineResult):
    logger.info(
        f"Inline result chosen: query='{chosen.query}', "
        f"result_id='{chosen.result_id}', user={chosen.from_user.id}"
    )


def _clean_markdown(text: str) -> str:
    import re
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'```[\s\S]*?```', lambda m: m.group(0).strip('`').strip(), text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    return text