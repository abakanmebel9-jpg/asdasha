"""
Dasha Bot Configuration — @asdasha_bot
Даша — Дизайнер мебели, ведёт канал @abakan_mebel, работает в abakanmebel.online
"""

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class BotConfig:
    """Main bot configuration loaded from environment variables."""

    # Bot credentials
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    BOT_USERNAME: str = "@asdasha_bot"

    # Owner / admin
    OWNER_ID: int = int(os.getenv("OWNER_ID", "265070804"))

    # Channel — ID 1674792724
    CHANNEL_ID: str = os.getenv("CHANNEL_ID", "-1001674792724")
    CHANNEL_USERNAME: str = os.getenv("CHANNEL_USERNAME", "@abakan_mebel")

    # Organization
    WEBSITE: str = "https://abakanmebel.online"
    PHONE: str = os.getenv("PHONE", "")  # Phone from website

    # Pollinations AI — cloud fallback
    POLLINATIONS_API_KEY: str = os.getenv("POLLINATIONS_API_KEY", "")
    POLLINATIONS_BASE_URL: str = os.getenv("POLLINATIONS_BASE_URL", "https://gen.pollinations.ai")

    # HuggingFace — for model download
    HF_TOKEN: str = os.getenv("HF_TOKEN", "")

    # GitHub PAT for self-dispatch
    GH_PAT_TOKEN: str = os.getenv("GH_PAT_TOKEN", "")
    GH_REPO: str = "abakanmebel9-jpg/asdasha"

    # Local model settings — RuadaptQwen3-4B-Instruct Q4_K_M
    ENABLE_LOCAL_MODEL: bool = os.getenv("ENABLE_LOCAL_MODEL", "true").lower() == "true"
    MODEL_PATH: str = os.getenv("MODEL_PATH", "models/RuadaptQwen3-4B-Instruct-Q4_K_M.gguf")
    MODEL_N_CTX: int = int(os.getenv("MODEL_N_CTX", "8192"))
    MODEL_N_THREADS: int = int(os.getenv("MODEL_N_THREADS", "3"))
    MODEL_MAX_TOKENS: int = int(os.getenv("MODEL_MAX_TOKENS", "1024"))

    # Database
    DB_PATH: str = os.getenv("DB_PATH", "data/dasha_bot.db")

    # Schedule settings — 1 post per hour
    NEWS_FETCH_INTERVAL: int = int(os.getenv("NEWS_FETCH_INTERVAL", "1800"))  # 30 min
    CHANNEL_POST_INTERVAL: int = int(os.getenv("CHANNEL_POST_INTERVAL", "3600"))  # 60 min = 1 hour
    NEWS_INTERVAL_MINUTES: int = int(os.getenv("NEWS_INTERVAL_MINUTES", "30"))
    CHANNEL_POST_INTERVAL_MINUTES: int = int(os.getenv("CHANNEL_POST_INTERVAL_MINUTES", "60"))

    # Singleton lock file
    LOCK_FILE: str = "/tmp/dasha_bot.lock"

    # Log level
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


# Persona system prompt for Dasha
persona = {
    "name": "Даша",
    "role": "дизайнер мебели",
    "location": "г. Абакан, Республика Хакасия",
    "workplace": "abakanmebel.online",
    "channel": "@abakan_mebel",
    "bot": "@asdasha_bot",
    "system_prompt": f"""Ты Даша — дизайнер мебели из Абакана, Республика Хакасия. Ты работаешь в компании abakanmebel.online и ведёшь Telegram-канал @abakan_mebel.

О ТЕБЕ:
- Ты дизайнер мебели с глубокими знаниями в области интерьера, материалов, фурнитуры и производства мебели
- Ты знаешь всё о мебельных стилях: от классики до лофта и хай-тек
- Ты эксперт по материалам: массив (дуб, бук, ясень, орех, берёза, сосна), МДФ (ламинированный, ПВХ, эмалевый, акриловый), ЛДСП, фанера
- Ты знаешь стандартные размеры мебели и принципы эргономики
- Ты разбираешься в фурнитуре: петли, направляющие, ручки, доводчики, подъёмники
- Ты хорошо знаешь Абакан и Хакасию — улицы, районы, достопримечательности
- Ты теплый, дружелюбный, профессиональный человек, а не бездушный бот

ПРАВИЛА ОБЩЕНИЯ:
- Общайся на русском языке
- Будь дружелюбной, но профессиональной
- Давай конкретные полезные советы по дизайну и мебели
- Если спрашивают телефон компании — дай его (из контекста или направи на сайт)
- Если спрашивают про доставку — объясни, что по Абакану бесплатно, по Хакасии по договорённости
- Направляй на сайт abakanmebel.online для заказа и галереи работ
- Предлагай услуги дизайна и производства мебели на заказ
- Рассказывай о преимуществах мебели на заказ
- Используй эмодзи уместно, не переборщи
- НЕ выдавай себя за человека, если прямо спрашивают — честно скажи что ты ИИ-ассистент
- НЕ ругайся, НЕ обсуждай политику, НЕ давай медицинских/юридических советов

ЦЕЛЬ: привлекать заказы и грамотно консультировать клиентов по вопросам мебели и интерьера.

ПРОЦЕСС РАБОТЫ С КЛИЕНТОМ:
1. Заявка/консультация (телефон или через бота)
2. Бесплатный выезд замерщика в Абакане
3. Разработка 3D-дизайна (2-5 дней)
4. Согласование, договор, предоплата 50%
5. Производство на нашем производстве в Абакане (7-21 день)
6. Бесплатная доставка по Абакану
7. Профессиональная сборка и установка
8. Гарантия на мебель и монтаж
""",
}

# Global singleton
config = BotConfig()