"""
Маша — Kitchen Project Generator for @abakan_mebel channel.

Маша — это автономный генератор контента, который 2 раза в день (по расписанию,
задаётся в bot/main.py, по умолчанию 10:00 и 18:00 по Красноярскому времени)
создаёт проект-пост «кухня на заказ в Абакане»:
  1. Случайно выбирает стиль кухни из KITCHEN_STYLES (20 вариантов).
  2. Генерирует изображение интерьера через Pollinations
     (ai_router.primary.generate_image, 1024x768, ландшафтная ориентация).
  3. Генерирует текст поста через LLM (ai_router.primary.chat, ROUTE_FUNCTION) —
     по структуре похожий на пример поста канала (хук + ✨ + 3-4 ✅ буллита +
     📞 телефон + CTA @Abakan_mebel + хештеги), но с РАЗНЫМ содержанием каждый
     раз. Контакты (+7 913 448 3717 и @Abakan_mebel) ОСТАВЛЯЕМ в теле поста
     (в отличие от channel.py, который их вырезает) — это сделано намеренно,
     как в примере поста.
  4. Публикует фото+caption (или text-only при ошибке изображения) в канал
     @abakan_mebel с автоматически добавленным стандартным футером.
  5. Сохраняет пост в БД (add_channel_post, post_type="masha_kitchen").

Модуль НЕ имеет side-effect-ов при импорте — определяется только класс
MashaKitchenGenerator и module-level singleton `masha_generator`.
Бот подключается через set_bot() из bot/main.py.

Контракт:
    class MashaKitchenGenerator:
        def __init__(self): ...
        def set_bot(self, bot) -> None: ...
        async def generate_and_post(self) -> bool: ...   # True если опубликовано
    masha_generator = MashaKitchenGenerator()   # module-level singleton
"""

import logging
import os
import random
import re
import tempfile
from datetime import datetime
from typing import Dict, List, Optional

from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import FSInputFile

from ai.router import ai_router
from ai.providers.provider_manager import ROUTE_FUNCTION
from bot.config import config
from bot.database import add_channel_post
from channel import (
    _build_post_with_footer,
    _strip_footer,
    TELEGRAM_MEDIA_TEXT_LIMIT,
    POST_FOOTER,
    _download_and_validate_image,
)

logger = logging.getLogger("dasha.masha")


# ═══════════════════════════════════════════════════════════════════════════
# KITCHEN STYLES — 20 вариантов.
# Каждый стиль — словарь с:
#   image_prompt:    английский фрагмент промпта для Pollinations
#                    ( Pollinations лучше понимает английские промпты )
#   ru_description:  короткое описание стиля на русском —
#                    подставляется в LLM-промпт, чтобы текст поста
#                    соответствовал изображению
# ═══════════════════════════════════════════════════════════════════════════
KITCHEN_STYLES: List[Dict[str, str]] = [
    {
        "image_prompt": (
            "modern minimalist kitchen interior, white matte cabinets, "
            "quartz countertop, scandinavian LED strip lighting, oak floor, "
            "clean lines, airy atmosphere"
        ),
        "ru_description": "современный минимализм — белые матовые фасады, "
                          "кварцевая столешница, LED-подсветка, скандинавская "
                          "лёгкость",
    },
    {
        "image_prompt": (
            "industrial loft kitchen interior, dark wood cabinets, black metal "
            "frames, exposed concrete wall, red brick, hanging Edison bulbs, "
            "matte black faucet"
        ),
        "ru_description": "лофт-индустриальный — тёмное дерево, чёрный металл, "
                          "бетон и кирпичная кладка",
    },
    {
        "image_prompt": (
            "neoclassical kitchen interior, MDF enamel cabinets with molding, "
            "natural wood island, brass handles, marble backsplash, "
            "elegant chandelier"
        ),
        "ru_description": "неоклассика — МДФ-эмаль с молдингами, натуральное "
                          "дерево, латунные ручки",
    },
    {
        "image_prompt": (
            "scandinavian kitchen interior, light birch wood cabinets, white "
            "walls, hanging green plants, subway tile backsplash, oak "
            "countertop, cozy natural light"
        ),
        "ru_description": "скандинавский стиль — светлое дерево, белый, "
                          "зелёные растения, уют",
    },
    {
        "image_prompt": (
            "high-tech glossy kitchen interior, glossy white lacquer cabinets, "
            "chrome fixtures, LED strip under cabinets, glass backsplash, "
            "futuristic appliances, reflective floor"
        ),
        "ru_description": "хай-тек глянец — глянцевые белые фасады, хром, "
                          "LED-ленты",
    },
    {
        "image_prompt": (
            "provence style kitchen interior, lavender accents, distressed "
            "wood cabinets, pastel cream colors, vintage ceramic sink, "
            "floral curtains, rustic warmth"
        ),
        "ru_description": "прованс — лавандовые акценты, потёртое дерево, "
                          "пастельные тона",
    },
    {
        "image_prompt": (
            "dark modern kitchen interior, anthracite matte cabinets, black "
            "stone countertop, gold accents, dramatic pendant lights, "
            "moody atmospheric lighting"
        ),
        "ru_description": "тёмный модерн — антрацит матовый, чёрный, золотые "
                          "акценты",
    },
    {
        "image_prompt": (
            "eco-style kitchen interior, natural wood facades, stone "
            "countertop, hanging greenery, large window with garden view, "
            "organic materials, bamboo shelves"
        ),
        "ru_description": "эко-стиль — натуральное дерево, камень, зелень",
    },
    {
        "image_prompt": (
            "large u-shaped kitchen interior with island, white shaker "
            "cabinets, marble island, pendant lights, stainless steel "
            "appliances, open plan layout"
        ),
        "ru_description": "большая П-образная кухня с островом — простор и "
                          "функциональность",
    },
    {
        "image_prompt": (
            "small compact kitchen interior 6 sqm, space-saving cabinets, "
            "foldable table, white glossy surfaces, mirrored backsplash, "
            "smart storage solutions, bright lighting"
        ),
        "ru_description": "маленькая компактная кухня 6 кв.м — решения для "
                          "экономии пространства",
    },
    {
        "image_prompt": (
            "L-shaped corner kitchen interior with bar counter, dark wood "
            "cabinets, white quartz countertop, two bar stools, pendant "
            "lights, open shelving"
        ),
        "ru_description": "угловая Г-образная кухня с барной стойкой",
    },
    {
        "image_prompt": (
            "two-row galley kitchen interior, parallel cabinetry, navy blue "
            "matte cabinets, white countertop, subway tiles, efficient "
            "workflow, narrow layout"
        ),
        "ru_description": "двухрядная кухня-галерея — параллельные ряды, "
                          "эффективная эргономика",
    },
    {
        "image_prompt": (
            "wood facade kitchen interior with black counters, warm walnut "
            "cabinets, black granite countertop, matte black hardware, "
            "brass faucet, sophisticated contrast"
        ),
        "ru_description": "деревянные фасады с чёрными столешницами — "
                          "тёплое дерево и графитовый контраст",
    },
    {
        "image_prompt": (
            "pastel mint sage green kitchen interior, sage green matte "
            "cabinets, white marble countertop, brass handles, botanical "
            "wallpaper, fresh airy feel"
        ),
        "ru_description": "пастельная мятно-шалфейная кухня — зелёные "
                          "матовые фасады, латунь, свежесть",
    },
    {
        "image_prompt": (
            "modern classic kitchen interior, two-tone cabinets navy blue "
            "bottom and white top, brass hardware, white quartz countertop, "
            "subway tile backsplash, glass front upper cabinets, elegant "
            "pendant lighting"
        ),
        "ru_description": "современная классика двухцветная — синий низ и "
                          "белый верх, латунь, стекло",
    },
    {
        "image_prompt": (
            "warm cozy wooden kitchen interior, natural oak cabinets, butcher "
            "block countertop, terracotta tile floor, copper pots hanging, "
            "warm incandescent lighting, farmhouse sink, rustic charm"
        ),
        "ru_description": "тёплая деревянная кухня-ферма — дуб, столешница "
                          "из массива, медь, терракота",
    },
    {
        "image_prompt": (
            "luxury modern kitchen interior, gloss black cabinets, calacatta "
            "marble waterfall island, gold fixtures, integrated appliances, "
            "linear pendant lights, floor to ceiling windows, sophisticated "
            "ambiance"
        ),
        "ru_description": "люкс-модерн — чёрный глянец, мраморный остров-"
                          "водопад, золото, панорамные окна",
    },
    {
        "image_prompt": (
            "bright white Scandinavian-Asian fusion kitchen interior, white "
            "minimalist cabinets, light wood accents, integrated indoor "
            "garden herb wall, stone countertop, soft natural daylight, zen "
            "atmosphere"
        ),
        "ru_description": "светлая сканди-азиатская кухня — минимализм, "
                          "фитостена из зелени, дзен-атмосфера",
    },
    {
        "image_prompt": (
            "colorful modern kitchen interior, deep forest green matte "
            "cabinets, warm oak open shelving, white farmhouse sink, copper "
            "faucet, patterned cement tile floor, lively atmosphere"
        ),
        "ru_description": "цветная кухня в лесной зелени — матовая зелень, "
                          "дубовые полки, медь, узорный пол",
    },
    {
        "image_prompt": (
            "studio apartment kitchen interior, compact linear layout, "
            "white handleless cabinets, integrated appliances, black "
            "countertop, mirror backsplash, slim LED lighting, space "
            "efficient modern design"
        ),
        "ru_description": "кухня-студия линейная — безручечные фасады, "
                          "встроенная техника, эргономика малого пространства",
    },
]

# Случайные дескрипторы освещения/ракурса — добавляются в конец промпта
# для вариативности генерации (разные «сиды» каждый раз).
_LIGHTING_VARIATIONS: List[str] = [
    "morning natural sunlight through window",
    "golden hour warm sunset light",
    "soft diffused daylight",
    "evening ambient lighting with warm glow",
    "bright noon sunlight",
    "overcast soft cloudy day light",
    "twilight with pendant lights on",
    "wide angle architectural photography",
    "eye-level interior shot",
    "three-quarter angle composition",
]


def _build_image_prompt(style: Dict[str, str]) -> str:
    """Собрать полный английский промпт для Pollinations.

    Всегда включает ключевые слова "kitchen interior" и "photorealistic",
    добавляет случайную вариацию освещения/ракурса и стандартные
    «фотореалистичные» суффиксы.
    """
    base = style["image_prompt"]
    variation = random.choice(_LIGHTING_VARIATIONS)
    return (
        f"{base}, {variation}, "
        "professional interior photography, photorealistic, 8k, "
        "natural lighting, architectural digest style, "
        "high detail, sharp focus, no people"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Пример поста (передаётся в LLM как образец СТРУКТУРЫ — не содержания)
# ═══════════════════════════════════════════════════════════════════════════
_EXAMPLE_POST = """Кухни на заказ в Абакане!
✨ Ищете современную кухню по индивидуальным размерам? Мы предлагаем: 
✅ Эксклюзивный дизайн 
✅ Качественные материалы 
✅ Изготовление по вашим эскизам 
📞 +7 913 448 3717

Закажите бесплатный дизайн-проект! ➡️ @Abakan_mebel

#мебельабакан #кухниабакан #мебельназаказ #дизайнабакан #кухниназаказ #абакан
#абаканмебель"""


def _build_system_prompt() -> str:
    """Построить system-промпт для LLM-генератора постов Маши.

    Описывает роль, точный образец структуры, требования к вариативности,
    обязательные элементы (телефон +7 913 448 3717, @Abakan_mebel) и
    запреты (markdown, футер, кавычки, разделители).
    """
    return f"""Ты — Маша, генератор постов для канала @abakan_mebel (кухни на заказ в Абакане).

Твоя задача — написать рекламный пост о кухнях на заказ. Каждый пост должен быть УНИКАЛЬНЫМ, с разными формулировками и комбинациями преимуществ, но ОБЯЗАТЕЛЬНО следовать одной и той же структуре (как в образце ниже).

═══ ОБРАЗЕЦ СТРУКТУРЫ (НЕ копируй текст 1:1 — варьируй!) ═══
{_EXAMPLE_POST}

═══ СТРУКТУРА (точно такая же, слово-в-слово по структуре) ═══
1. Первая строка — короткий «хук»-заголовок про кухни на заказ в Абакане (с 1 эмодзи в начале). ВАРИРУЙ: «Кухни на заказ в Абакане!», «Идеальная кухня — в Абакане!», «Кухня мечты в Абакане?», «Проектируем кухни в Абакане!» и т.п.
2. Пустая строка.
3. Строка с ✨ и вопросом/предложением про индивидуальные размеры/современную кухню/кухню мечты.
4. 3-4 строки с ✅ — преимущества. ВЫБИРАЙ РАЗНЫЕ из списка (каждый раз новая комбинация):
   ✅ Эксклюзивный дизайн
   ✅ Качественные материалы (МДФ, ЛДСП, массив)
   ✅ Изготовление по вашим эскизам
   ✅ Фурнитура Blum (до 5 лет гарантии)
   ✅ Бесплатный замер по Абакану и Хакасии
   ✅ Бесплатная доставка по Абакану
   ✅ Гарантия 3 года
   ✅ 3D-визуализация проекта
   ✅ Срок изготовления 14-31 день
   ✅ Влагостойкие материалы для кухни
   ✅ Собственное производство в Абакане
   ✅ Цены от 45 000 руб
5. Строка с 📞 и телефоном: 📞 +7 913 448 3717 (именно в этом формате, с пробелами).
6. Пустая строка.
7. Строка CTA: «Закажите бесплатный дизайн-проект! ➡️ @Abakan_mebel» (можно слегка варьировать формулировку CTA, но @Abakan_mebel и «бесплатный дизайн-проект» обязательны).
8. Пустая строка.
9. 6-8 хештегов (каждый с новой строки ИЛИ через пробел — как в образце, через пробел в одну-две строки). ВЫБИРАЙ РАЗНЫЕ комбинации из:
   #мебельабакан #кухниабакан #мебельназаказ #дизайнабакан #кухниназаказ #абакан #абаканмебель #кухни #дизайнкухни #интерьерабакан #мебельхакасия #кухниназаказабакан

═══ СТИЛЬ КУХНИ ═══
В тексте поста ОЧЕНЬ ЖЕЛАТЕЛЬНО (но не жёстко) упомянуть выбранный стиль кухни — фразой в хуке или в ✨-строке. Стиль будет указан в user-сообщении. Это создаёт связь между текстом и сгенерированным изображением.

═══ ЖЁСТКИЕ ОГРАНИЧЕНИЯ ═══
- ТОЛЬКО русский язык.
- БЕЗ markdown: никаких **, ##, [], (), _, `, |.
- БЕЗ стандартного футера канала («Автор @asdasha_bot...», «abakanmebel.online») — он будет добавлен автоматически.
- БЕЗ кавычек вокруг всего текста.
- БЕЗ разделителей вроде ───, ═══, ━━━, ━━, ▓▓, ══.
- Длина: 350-700 символов.
- Телефон +7 913 448 3717 должен быть в тексте РОВНО в этом формате.
- @Abakan_mebel должен быть в тексте (в CTA-строке).
- НЕ добавляй другие контакты (WhatsApp, сайт) — только телефон и @Abakan_mebel.
- НЕ пиши «закажите по телефону» — CTA должна быть про бесплатный дизайн-проект.

═══ ГРАМОТНОСТЬ (критично — лицо канала) ═══
- ТИРЕ в пояснительных конструкциях — длинное «—» (не дефис «-»).
- КАВЫЧКИ — только «ёлочки», не прямые " ".
- ОРФОГРАММЫ (союзы/наречия — слитно): «чтобы», «также», «тоже», «поэтому»,
  «зато», «причём», «несмотря на». Раздельно — только при контрасте:
  «что бы посоветовать?», «так же хорош, как…».
- ПУНКТУАЦИЯ: запятые перед «что», «когда», «потому что», «если».
  Обособляй обращения и вводные слова.
- НИКАКОГО канцелярита: «осуществляется», «в данном случае», «является».
  Пиши живо, как говоришь.
- НИКАКИХ тавтологий: не повторяй одно слово в соседних строках.
- Согласуй падежи и окончания. Без английских вставок.

Верни ТОЛЬКО готовый текст поста, без пояснений, без «Вот пост:», без markdown-обёртки."""


def _clean_post_text(text: str) -> str:
    """Очистить сгенерированный текст поста.

    - Убирает leading/trailing whitespace.
    - Срезает markdown-разметку (**, ##, [], (), `, _, |).
    - Удаляет разделители (───, ═══, ━━━).
    - Схлопывает 3+ пустых строки в 2.
    - НЕ трогает телефон +7 913 448 3717 и @Abakan_mebel
      (Маша намеренно оставляет их в теле поста, как в образце).
    """
    if not text:
        return ""
    # Убираем кавычки-обёртки вокруг всего текста
    text = text.strip().strip('"').strip("'").strip("«»").strip()
    # Markdown
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Разделители
    text = re.sub(r"[─━═▓]{3,}", "", text)
    # Схлопываем 3+ пустых строк в 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Типографика и пунктуация (тире —, кавычки «», пробелы, …, !/??)
    try:
        from bot.text_polish import polish_grammar
        text = polish_grammar(text)
    except Exception:
        pass
    return text.strip()


def _fallback_text(ru_description: str) -> str:
    """Built-in генератор поста, если LLM недоступен.

    Возвращает валидный пост, следующий той же структуре, что и образец.
    Случайно выбирает преимущества и хештеги, чтобы посты различались.
    """
    hooks = [
        "Кухни на заказ в Абакане! 🍳",
        "Идеальная кухня — в Абакане! ✨",
        "Кухня мечты в Абакане? 🏡",
        "Проектируем кухни в Абакане! 📐",
        "Ваша новая кухня — в Абакане! 🪵",
        "Кухни под ваш интерьер — Абакан! 🛋",
        "Собственная кухня от 45 000 ₽ — Абакан! 💎",
        "Кухня на заказ в Хакасии! 🏠",
        "Новая кухня — новое настроение! Абакан 🌿",
        "Кухня, которая работает на вас — Абакан! ⚙️",
    ]
    sparkle_lines = [
        "✨ Хотите кухню по индивидуальным размерам? Мы предлагаем:",
        "✨ Ищете современную кухню в своём стиле? Мы делаем:",
        "✨ Нужна кухня, которая идеально впишется в интерьер? Мы предлагаем:",
        "✨ Мечтаете о кухне, как с картинки? Мы реализуем:",
        "✨ Готовы обновить кухню под ваши размеры? Мы предлагаем:",
        "✨ Кухня, собранная на собственном производстве в Абакане. Мы предлагаем:",
        "✨ Подберём стиль, материалы и фурнитуру под ваш бюджет. Мы делаем:",
        "✨ Проектируем кухни с учётом эргономики и вашего роста. Мы предлагаем:",
    ]
    all_benefits = [
        "✅ Эксклюзивный дизайн",
        "✅ Качественные материалы (МДФ, ЛДСП, массив)",
        "✅ Изготовление по вашим эскизам",
        "✅ Фурнитура Blum (до 5 лет гарантии)",
        "✅ Бесплатный замер по Абакану и Хакасии",
        "✅ Бесплатная доставка по Абакану",
        "✅ Гарантия 3 года",
        "✅ 3D-визуализация проекта",
        "✅ Срок изготовления 14-31 день",
        "✅ Влагостойкие материалы для кухни",
        "✅ Собственное производство в Абакане",
        "✅ Цены от 45 000 руб",
        "✅ Профессиональная сборка включена",
        "✅ Доводчики на всех ящиках",
        "✅ Индивидуальные размеры до миллиметра",
        "✅ Выезд замерщика по всей Хакасии",
    ]
    ctas = [
        "Закажите бесплатный дизайн-проект! ➡️ @Abakan_mebel",
        "Оставьте заявку на бесплатный дизайн-проект! ➡️ @Abakan_mebel",
        "Получите бесплатный 3D-проект кухни! ➡️ @Abakan_mebel",
        "Закажите бесплатный замер и проект! ➡️ @Abakan_mebel",
        "Напишите нам и получите бесплатный проект кухни! ➡️ @Abakan_mebel",
        "Закажите бесплатный замер и 3D-визуализацию! ➡️ @Abakan_mebel",
    ]
    all_hashtags = [
        "#мебельабакан", "#кухниабакан", "#мебельназаказ", "#дизайнабакан",
        "#кухниназаказ", "#абакан", "#абаканмебель", "#кухни",
        "#дизайнкухни", "#интерьерабакан", "#мебельхакасия", "#кухниназаказабакан",
    ]

    # Упоминание стиля в хуке (берём первое слово до тире/запятой)
    style_short = ""
    if ru_description:
        style_short = re.split(r"\s*[-,—]\s*", ru_description, maxsplit=1)[0].strip()

    base_hook = random.choice(hooks)
    if style_short:
        style_word = random.choice(
            ["современный", "функциональный", "уютный", "стильный"]
        )
        # Заменяем завершающий «!» на « — <стиль> стиль (<style_short>)!»
        hook = base_hook.replace(
            "!", f" — {style_word} стиль ({style_short.lower()})!", 1
        )
    else:
        hook = base_hook

    benefits = random.sample(all_benefits, k=random.randint(3, 4))
    cta = random.choice(ctas)
    hashtags = random.sample(all_hashtags, k=random.randint(6, 8))
    sparkle = random.choice(sparkle_lines)

    lines = [
        hook,
        "",
        sparkle,
        *benefits,
        "📞 +7 913 448 3717",
        "",
        cta,
        "",
        " ".join(hashtags),
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# Основной класс
# ═══════════════════════════════════════════════════════════════════════════
class MashaKitchenGenerator:
    """Генератор кухонных постов для канала @abakan_mebel (2 поста/день).

    Использование:
        masha_generator.set_bot(bot)
        await masha_generator.generate_and_post()
    """

    def __init__(self) -> None:
        self._bot: Optional[Bot] = None

    def set_bot(self, bot: Bot) -> None:
        """Подключить экземпляр aiogram.Bot (вызывается из bot/main.py)."""
        self._bot = bot

    async def generate_and_post(self) -> bool:
        """Сгенерировать кухонный пост и опубликовать в канал.

        Returns:
            True  — пост успешно опубликован (фото+caption ИЛИ text-only).
            False — публикация не удалась (нет бота, все AI-вызовы упали,
                    отправка в канал упала).
        """
        if self._bot is None:
            logger.error("Маша: bot не задан (set_bot не вызывался) — пост не опубликован")
            return False

        # ── 1. Выбор стиля кухни ──────────────────────────────────────────
        style = random.choice(KITCHEN_STYLES)
        ru_description = style["ru_description"]
        image_prompt = _build_image_prompt(style)
        logger.info(f"Маша: выбран стиль — {ru_description[:60]}")

        # ── 2. Генерация изображения (Pollinations, 1024x768) ─────────────
        image_url: Optional[str] = None
        try:
            resp = await ai_router.primary.generate_image(
                prompt=image_prompt, width=1024, height=768,
            )
            if resp and resp.image_url and not resp.error:
                image_url = resp.image_url
                logger.info(f"Маша: изображение сгенерировано ({image_url[:80]}...)")
            else:
                err = resp.error if resp else "no response"
                logger.warning(f"Маша: generate_image не вернул URL ({err}) — "
                               f"перейду к text-only fallback")
        except Exception as e:
            logger.warning(f"Маша: ошибка generate_image: {e} — text-only fallback")

        # ── 3. Генерация текста поста через LLM ───────────────────────────
        text = await self._generate_text(ru_description)
        if not text:
            logger.error("Маша: не удалось получить текст поста (LLM+fallback упали)")
            return False

        # ── 4. Очистка текста ─────────────────────────────────────────────
        text = _clean_post_text(text)
        if not text:
            logger.error("Маша: после очистки текст пуст")
            return False

        # ── 5. Публикация в канал ─────────────────────────────────────────
        try:
            today = datetime.now(ZoneInfo("Asia/Krasnoyarsk")).strftime("%d.%m.%Y")
        except Exception:
            today = datetime.now().strftime("%d.%m.%Y")

        sent_ok = False
        if image_url:
            sent_ok = await self._send_with_image(image_url, text)
            if not sent_ok:
                logger.warning("Маша: отправка с изображением не удалась — пробую text-only")

        if not sent_ok:
            # text-only: перестраиваем caption под лимит 4096 (без медиа)
            sent_ok = await self._send_text_only(text)

        if not sent_ok:
            logger.error("Маша: не удалось отправить пост в канал")
            return False

        # ── 6. Сохранение в БД ────────────────────────────────────────────
        try:
            # caption, который реально был отправлен (восстанавливаем для лога/БД)
            if image_url:
                # Был отправлен фото+caption → собираем как для медиа
                saved_caption = _build_post_with_footer(text, has_media=True)
            else:
                saved_caption = _build_post_with_footer(text, has_media=False)

            await add_channel_post(
                content=saved_caption,
                message_id=0,
                post_type="masha_kitchen",
                source_url=image_url or "",
            )
            logger.info(f"Маша: пост сохранён в БД (image={'да' if image_url else 'нет'}, "
                        f"дата={today})")
        except Exception as e:
            # Пост уже опубликован — не страшно, если БД-запись упала
            logger.warning(f"Маша: не удалось сохранить пост в БД: {e}")

        logger.info("Маша: пост успешно опубликован в @abakan_mebel")
        return True

    # ──────────────────────────────────────────────────────────────────────
    # Внутренние хелперы
    # ──────────────────────────────────────────────────────────────────────

    async def _generate_text(self, ru_description: str) -> str:
        """Сгенерировать текст поста через LLM; fallback на встроенные шаблоны."""
        system_prompt = _build_system_prompt()
        try:
            today = datetime.now(ZoneInfo("Asia/Krasnoyarsk")).strftime("%d.%m.%Y")
        except Exception:
            today = datetime.now().strftime("%d.%m.%Y")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"Напиши пост. Стиль кухни: {ru_description}. Дата: {today}."
            )},
        ]

        try:
            text_resp = await ai_router.primary.chat(
                messages=messages,
                route_type=ROUTE_FUNCTION,
                temperature=0.9,
                max_tokens=900,
            )
            if text_resp and text_resp.text and not text_resp.error:
                logger.info("Маша: текст поста сгенерирован через LLM")
                return text_resp.text
            err = text_resp.error if text_resp else "no response"
            logger.warning(f"Маша: LLM не вернул текст ({err}) — fallback на шаблоны")
        except Exception as e:
            logger.warning(f"Маша: ошибка LLM chat: {e} — fallback на шаблоны")

        # Built-in fallback
        return _fallback_text(ru_description)

    async def _send_with_image(self, image_url: str, text: str) -> bool:
        """Скачать изображение и отправить фото+caption в канал.

        caption строится под медиа-лимит (1024) с одним футером.
        Скачивание и валидация — через общий helper channel._download_and_validate_image
        (content-type + magic bytes), чтобы защитить отправку от битых/не-фото ответов.
        """
        if not self._bot:
            return False

        # Финальный caption под медиа-лимит (с футером один раз)
        caption = _build_post_with_footer(text, has_media=True)

        tmp_path: Optional[str] = None
        try:
            import tempfile as _tempfile
            tmp_dir = _tempfile.mkdtemp(prefix="masha_kitchen_")
            try:
                tmp_path = await _download_and_validate_image(
                    client_url=image_url, tmp_dir=tmp_dir, idx=0,
                )
            finally:
                # tmp_dir cleaned below; helper writes file inside it
                pass

            if not tmp_path:
                logger.warning(f"Маша: image download/validation failed: {image_url[:80]}")
                # Удалим пустую tmp_dir
                try:
                    os.rmdir(tmp_dir)
                except Exception:
                    pass
                return False

            photo = FSInputFile(tmp_path)
            await self._bot.send_photo(
                chat_id=config.CHANNEL_ID,
                photo=photo,
                caption=caption,
                disable_notification=True,
            )
            logger.info("Маша: фото+caption отправлены в канал")
            return True

        except Exception as e:
            logger.warning(f"Маша: не удалось отправить фото: {e}")
            return False
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                # Удалим родительский tmp_dir, если он пуст
                try:
                    parent = os.path.dirname(tmp_path)
                    os.rmdir(parent)
                except Exception:
                    pass

    async def _send_text_only(self, text: str) -> bool:
        """Отправить текстовый пост без изображения (лимит 4096)."""
        if not self._bot:
            return False
        caption = _build_post_with_footer(text, has_media=False)
        try:
            await self._bot.send_message(
                chat_id=config.CHANNEL_ID,
                text=caption,
                disable_notification=True,
            )
            logger.info("Маша: text-only пост отправлен в канал")
            return True
        except Exception as e:
            logger.error(f"Маша: не удалось отправить text-only пост: {e}")
            return False


# ═══════════════════════════════════════════════════════════════════════════
# Module-level singleton (no side effects at import time)
# ═══════════════════════════════════════════════════════════════════════════
masha_generator = MashaKitchenGenerator()
