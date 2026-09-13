#!/usr/bin/env python3
"""
Раунд 13 — тесты: статик-банк постов (последний эшелон при полностью мёртвом
AI), англ. ключевые слова фильтра новостей, диагностика Cloudflare,
взаимодействие get_static_post с дедупом posted_news.
Запуск: venv/bin/python scripts/test_round13.py
"""
import asyncio
import os
import sys

os.environ.setdefault("BOT_TOKEN", "123:TEST")
os.environ.setdefault("CHANNEL_ID", "-1001234567890")
os.environ.setdefault("OWNER_ID", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = 0, []


def check(name, cond, extra=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL.append(name)
        print(f"  ❌ {name} {extra}")


print("── 1. Статик-банк: объём и качество ────────────────────────────────")
from bot.static_posts import (
    STATIC_POSTS, get_static_post, mark_key_for_topic, bank_size, min_static_len,
)

check("В банке достаточно постов (≥20)", bank_size() >= 20, f"got {bank_size()}")
check("Все посты ≥300 знаков", min_static_len() >= 300, f"min={min_static_len()}")

# Все посты проходят валидацию канала (та же, что у AI-постов)
from bot.main import validate_post_text, _is_furniture_news
bad = []
for topic, text in STATIC_POSTS:
    ok, reason = validate_post_text(text)
    if not ok or len(text) < 250:
        bad.append((topic[:30], reason))
check("Все статик-посты проходят validate_post_text", not bad, extra=str(bad[:3]))

fps = [mark_key_for_topic(t) for t, _ in STATIC_POSTS]
check("Все отпечатки тем уникальны", len(set(fps)) == len(fps))

# Никакой эзотерики/астрологии — тотальный запрет заказчика
# Точный список (без ложных: «астр» ловится в «кастрюли», «таро» — в «старому»)
mystic = ["астролог", "гороскоп", "нумеролог", "матрица судьбы", "знак зодиака",
          "таро ", " эзотерик", "фен-шуй", "фэншуй"]
mystic_hits = [t[:30] for t, txt in STATIC_POSTS
               if any(m in (t + " " + txt).lower() for m in mystic)]
check("Ноль эзотерики в статик-банке", not mystic_hits, extra=str(mystic_hits))

print("── 2. get_static_post: ротация и дедуп ─────────────────────────────")


class MockDB:
    def __init__(self, posted_keys=None):
        self.posted = set(posted_keys or [])
        self.deleted = []

    async def is_news_posted(self, key):
        return key in self.posted

    async def delete_posted_keys(self, prefix, exclude=""):
        gone = [k for k in self.posted if k.startswith(prefix) and k != exclude]
        self.deleted.append((prefix, len(gone)))
        self.posted -= set(gone)
        return len(gone)


async def t_rotation():
    db = MockDB()
    seen = []
    for _ in range(3):
        item = await get_static_post(db)
        assert item is not None
        topic, text = item
        seen.append(mark_key_for_topic(topic))
        db.posted.add(mark_key_for_topic(topic))
    check("3 вызова — 3 разных поста", len(set(seen)) == 3)

    # Все ключи «уже опубликованы» → банк исчерпан → сброс дедупа
    db2 = MockDB(posted_keys=set(fps))
    item = await get_static_post(db2)
    check("Банк исчерпан → сброс дедупа и пост выдаётся",
          item is not None and db2.deleted and db2.deleted[0][1] == len(fps))

asyncio.run(t_rotation())

print("── 3. Фильтр новостей: англ. мебельные проходят, мусор — нет ───────")
check("IKEA storage проходит", _is_furniture_news(
    "The New IKEA Storage Find That Makes Any Bedroom Feel Bigger", ""))
check("Bed frame проходит", _is_furniture_news(
    "The '80s Chrome Bed Frame We Wish IKEA Would Reissue", ""))
check("Sofa проходит", _is_furniture_news("A modular sofa for small apartments", ""))
check("Living room makeover проходит (weak)", _is_furniture_news(
    "A Dark Living Room Gets a Dramatic Makeover", ""))
check("Fiat 500 restomod отсечён", not _is_furniture_news(
    "Hot Lab and Garage Italia give the Fiat 500 Spiaggina restomod a nautical twist", ""))
check("cars отсечён", not _is_furniture_news("Best classic cars of 2026", ""))
check("Fashion отсечён", not _is_furniture_news("Paris fashion week highlights", ""))
check("Русская мебельная по-прежнему проходит", _is_furniture_news(
    "Новые фасады для кухонь из МДФ", "ЛДСП и фурнитура"))
check("Русский гороскоп по-прежнему отсечён", not _is_furniture_news(
    "Гороскоп на неделю", "звёзды советуют"))
check("Ложное срабатывание «car» не режет «scarf...» кейс с мебелью",
      _is_furniture_news("A hand-carved walnut cabinet", ""))

print("── 4. Диагностика Cloudflare и интеграция статик-банка ─────────────")
with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "ai", "client.py"), encoding="utf-8") as f:
    ai_src = f.read()
check("Cloudflare логирует HTTP-статус при отказе",
      'Cloudflare HTTP {r.status_code}' in ai_src)
check("Cloudflare детектит 200 без контента",
      "без контента" in ai_src)

with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "bot", "main.py"), encoding="utf-8") as f:
    main_src = f.read()
check("main.py использует статик-банк при пустом AI",
      "posting STATIC fallback" in main_src)
check("Статик-подстановка меняет topic для дедупа sp:",
      "topic = s_topic" in main_src)
check("Ветка «кандидаты были, но AI упал» уходит в фолбэк-тему",
      "All {len(candidates)} news candidates failed" in main_src)

print("── 5. Регрессия: пайплайн стилизации на статик-тексте ──────────────")
from bot.main import _style_channel_post, _add_topic_hashtags

async def t_full_static_pipeline():
    topic, text = STATIC_POSTS[1]  # чек-лист с буллетами «• Фасады: …»
    text2 = await _add_topic_hashtags(text, topic)
    check("Хештеги не дублируются (уже в тексте)",
          text2.count("#") <= text.count("#") + 2)
    styled = _style_channel_post(text2.replace("&", "&amp;").replace("<", "&lt;"))
    check("Стилизация сохраняет хештеги", "#чеклист" in styled or "#" in styled)
    check("Буллет-лейбл «• Фасады:» выделен жирным",
          "<b>• Фасады:</b>" in styled, extra=styled[styled.find("Фасады")-30:styled.find("Фасады")+40])
    # Миф-пост содержит структурный лейбл «Правда:»
    _, myth_text = STATIC_POSTS[2]
    styled_myth = _style_channel_post(myth_text)
    check("Лейбл «Правда:» выделен жирным", "<b>Правда:</b>" in styled_myth)

asyncio.run(t_full_static_pipeline())

print()
print(f"ИТОГО: {PASS} ✅, {len(FAIL)} ❌")
if FAIL:
    print("ПРОВАЛЕНО:", *FAIL, sep="\n  - ")
    sys.exit(1)
print("ВСЕ ТЕСТЫ ЗЕЛЁНЫЕ 🎉")
