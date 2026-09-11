#!/usr/bin/env python3
"""
Раунд 11 — тесты: анти-клише «Как дизайнер…» (banned openings + промпты),
стилевые хештеги, CTA на /sizes и /gallery, тематические дни недели,
/sizes (5 комнат × стандарты), /gallery (6 стилей), новые лейблы,
фолбэк-пикер с фокусом дня, регрессии v4 (телефоны, варианты, цены).
Запуск: venv/bin/python scripts/test_round11.py
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


print("— 1. Анти-клише «Как дизайнер…»: banned openings —")
from bot.post_utils import clean_post_text

out = clean_post_text("Как дизайнер, я всегда задаюсь вопросом: можно ли совместить? Кухня без ручек выглядит чище и стоит практично.")
check("Зачин «Как дизайнер…» срезан", not out.lower().startswith("как дизайнер"), repr(out[:60]))
check("Пост начинается с сути", out.startswith("Кухня без ручек"), repr(out[:40]))
out2 = clean_post_text("Мечтаете о новой кухне? Шкаф-купе решает проблему хранения.")
check("Регрессия «Мечтаете» работает", not out2.lower().startswith("мечтаете"), repr(out2[:40]))
out4 = clean_post_text("Может ли кухня с неудачным цветом стать стильной? Да — замена фасадов и столешницы меняет всё.")
check("Прод-кейс 9235: «Может ли…» срезан", not out4.lower().startswith("может ли"), repr(out4[:40]))
check("Прод-кейс 9235: суть осталась", "замена фасадов" in out4, repr(out4[:60]))
out3 = clean_post_text("ЛДСП Е0,5 — безопасный материал для детской.")
check("Обычный пост не тронут", out3.startswith("ЛДСП"), repr(out3[:40]))

print("— 2. Анти-клише: промпты не учат шаблону —")
from bot.persona import CHANNEL_POST_PROMPT
from bot.post_types import POST_TYPES

check(" persona: нет примера «'Как дизайнер, я всегда...'»", "'Как дизайнер, я всегда...'" not in CHANNEL_POST_PROMPT)
check(" persona: блок АНТИ-ШТАМП присутствует", "АНТИ-ШТАМП" in CHANNEL_POST_PROMPT)
check(" expert-тип: нет формулы-примера", "«Как дизайнер, я всегда…»), конкретика" not in POST_TYPES["expert"])
check(" expert-тип: есть явный запрет шаблона", "Без шаблона «Как дизайнер, я всегда…»" in POST_TYPES["expert"])
import bot.main as m
src_main = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot", "main.py"), encoding="utf-8").read()
check(" main: АНТИ-ШТАМП в новостном промпте", "АНТИ-ШТАМП: НЕ используй «Как дизайнер, я всегда...»" in src_main)
check(" main: анти-шаблон в промпте AI-темы", "«Как дизайнер, я всегда…»" in src_main)

print("— 3. Стилевые хештеги —")
check(" #лофт в карте тегов", any("#лофт" == tag for _, tag in m._TOPIC_HASHTAGS))
check(" #сканди в карте тегов", any("#сканди" == tag for _, tag in m._TOPIC_HASHTAGS))
check(" #минимализм в карте тегов", any("#минимализм" == tag for _, tag in m._TOPIC_HASHTAGS))
check(" #классика в карте тегов", any("#классика" == tag for _, tag in m._TOPIC_HASHTAGS))
check(" #детская в карте тегов", any("#детская" == tag for _, tag in m._TOPIC_HASHTAGS))
# Порядок: стилевые ДО общего #дизайнинтерьера
idx_style = min(i for i, (_, t) in enumerate(m._TOPIC_HASHTAGS) if t == "#лофт")
idx_generic = max(i for i, (_, t) in enumerate(m._TOPIC_HASHTAGS) if t == "#дизайнинтерьера")
check(" #лофт стоит раньше #дизайнинтерьера", idx_style < idx_generic, (idx_style, idx_generic))

# Живой прогон _add_topic_hashtags с моками БД
async def _tags_for(topic):
    from unittest.mock import patch
    m._hashtag_recent["loaded"] = True
    m._hashtag_recent["tags"].clear()
    m._hashtag_state["last"] = frozenset()
    async def fake_get(key):
        return None
    async def fake_mark(key, title):
        return None
    with patch.object(m.db, "get_posted_title", fake_get), patch.object(m.db, "mark_news_posted", fake_mark):
        return await m._add_topic_hashtags("Текст поста про кухни без тегов.", topic)

out = asyncio.run(_tags_for("Кухни в стиле лофт: характерные черты"))
check(" лофт-тема получает #лофт", "#лофт" in out, repr(out))
check(" город #Абакан добавлен", "#Абакан" in out)
out = asyncio.run(_tags_for("Скандинавский стиль в интерьере кухни"))
check(" сканди-тема получает #сканди", "#сканди" in out, repr(out))
out = asyncio.run(_tags_for("Детская мебель: безопасность Е0,5"))
check(" детская-тема получает #детская", "#детская" in out, repr(out))

print("— 4. CTA-ротация: новые строки —")
check(" CTA стало 8", len(m._CTA_LINES) == 8, len(m._CTA_LINES))
check(" CTA /sizes присутствует", any("/sizes" in c for c in m._CTA_LINES))
check(" CTA /gallery присутствует", any("/gallery" in c for c in m._CTA_LINES))
picked = {m._pick_cta() for _ in range(24)}
check(" ротация достаёт до новых CTA", any("/sizes" in c or "/gallery" in c for c in picked))

print("— 5. Тематические дни недели —")
from bot.post_context import _WEEKDAY_THEMES, weekday_theme, weekday_keywords, topic_matches_weekday

check(" 7 дней описаны", len(_WEEKDAY_THEMES) == 7)
check(" Метки дней уникальны", len({v[0] for v in _WEEKDAY_THEMES.values()}) == 7)
name, block = weekday_theme()
check(" weekday_theme возвращает непустые метку и блок", bool(name) and len(block) > 30, (name, block[:40]))
check(" weekday_keywords непустые", len(weekday_keywords()) >= 5)

# Дата-независимо: ключевые слова каждого дня матчатся ≥3 тем из банка
BANK = [
    "Уход за фасадами МДФ: чем мыть и чего бояться", "Матовые или глянцевые фасады: сравнение от практика",
    "Как выбрать ЛДСП для корпуса кухни", "Кромка фасадов: ПВХ против ABS — в чём разница",
    "Наполнение шкафа-купе: штанги, полки, выдвижные корзины", "Гардеробная комната за 2 квадратных метра: реально ли",
    "Кухни в стиле лофт: характерные черты", "Цвет кухни 2026: тренды и сочетания",
    "Как рассчитать бюджет кухни на заказ: из чего складывается цена", "Карго-секции и бутылочницы: стоит ли переплачивать",
    "Мифы о ЛДСП", "Как проверить качество собранной мебели: чек-лист",
    "Прихожая на заказ: 6 идей для маленького коридора", "Ошибки планировки кухни: рабочий треугольник",
    # Раунд 11: добор тем по фокусам среды/четверга/пятницы (реальные из банка main.py)
    "Выдвижные системы: организация хранения в шкафах", "Хранение на кухне: 5 лайфхаков дизайнера",
    "Открытые полки или закрытые шкафы: мнения дизайнера", "Скандинавский стиль в интерьере кухни",
    "Минимализм на кухне: меньше деталей, больше пространства", "Тренд тёплого минимализма в мебели 2026",
    "Сроки производства: от замера до сборки", "Как сэкономить на кухне без потери качества",
]
for day, (dname, _b, kws) in _WEEKDAY_THEMES.items():
    hits = sum(1 for t in BANK if any(k in t.lower() for k in kws))
    check(f" {dname}: ≥3 тем из банка подходят", hits >= 3, f"hits={hits}")
check(" topic_matches_weekday работает (текущий день)", isinstance(topic_matches_weekday("Кухни в стиле лофт"), bool))

print("— 6. Фолбэк-пикер с фокусом дня —")


async def _pick_with(seed, prob_force=True):
    from unittest.mock import patch
    topics = BANK
    async def fake_posted(key):
        return False
    import random as _r
    _r.seed(seed)
    with patch.object(m.db, "is_news_posted", fake_posted):
        if prob_force:
            with patch.object(m.random, "random", lambda: 0.0):  # форс ветку weekday
                return await m.DashaBot._pick_fallback_topic(None, topics, lambda: topics[0])
        return await m.DashaBot._pick_fallback_topic(None, topics, lambda: topics[0])


t = asyncio.run(_pick_with(42))
check(" Пикер вернул тему из банка", t in BANK, t)
check(" При форсе дня — тема под фокус дня", topic_matches_weekday(t), t)
t2 = asyncio.run(_pick_with(7, prob_force=False))
check(" Пикер без форса вернул тему из банка", t2 in BANK, t2)

print("— 7. /sizes: контент и клавиатуры —")
from bot.sizes import SIZE_STANDARDS, SIZE_ROOMS, _rooms_keyboard, _result_keyboard, _room_text, sizes_router

check(" 5 комнат", len(SIZE_STANDARDS) == 5 and len(SIZE_ROOMS) == 5)
for room, items in SIZE_STANDARDS.items():
    check(f" {room}: ≥6 стандартов", len(items) >= 6, len(items))
    with_dims = sum(1 for t, _ in items if any(ch.isdigit() for ch in t))
    check(f" {room}: ≥4 заголовков с цифрами", with_dims >= 4, with_dims)
for room in SIZE_STANDARDS:
    text = _room_text(room)
    check(f" текст {room} короче 4096", len(text) <= 4096, len(text))
    check(f" текст {room} содержит название", f"Стандартные размеры — {SIZE_ROOMS[room][0]}" in text)
cb = [b.callback_data for row in _rooms_keyboard().inline_keyboard for b in row]
check(" callback_data ≤64 байт", all(len(c) <= 64 for c in cb), cb[:3])
check(" sz:<room> в клавиатуре", "sz:kitchen" in cb and "sz:kids" in cb)
check(" result-KB ведёт в /mistakes и /storage", "mst:menu" in [b.callback_data for row in _result_keyboard().inline_keyboard for b in row]
      and "storage:menu" in [b.callback_data for row in _result_keyboard().inline_keyboard for b in row])

print("— 8. /gallery: стили, подписи, клавиатуры —")
from bot.gallery import STYLES, _MENU_KB, _contacts_kb, _tips_caption, gallery_router

check(" 6 стилей", len(STYLES) == 6, list(STYLES))
for key, s in STYLES.items():
    check(f" {key}: 2 темы + 3 совета", len(s["topics"]) == 2 and len(s["tips"]) == 3)
    for i in (0, 1):
        cap = _tips_caption(key, i)
        check(f" {key} caption[{i}] ≤1024", len(cap) <= 1024, len(cap))
        check(f" {key} caption[{i}] содержит совет", "💡" in cap)
cb = [b.callback_data for row in _MENU_KB.inline_keyboard for b in row]
check(" gal:<style> в меню", all(any(f"gal:{k}" == c for c in cb) for k in STYLES))
check(" callback_data ≤64 байт", all(len(c) <= 64 for c in cb))

print("— 9. text_polish: новые лейблы Идея/Фаворит —")
from bot.text_polish import stylize_post_html, _split_merged_labels

out = _split_merged_labels("Порядок наводим так. Идея: карго у плиты. Фаворит: выдвижная секция.")
check(" «Идея:» на новой строке", "\nИдея:" in out, repr(out))
check(" «Фаворит:» на новой строке", "\nФаворит:" in out, repr(out))
out = stylize_post_html("Сравнение завершено. Идея: пенал вместо полок.")
check(" «Идея:» жирным", "<b>Идея:</b>" in out, repr(out))
out = stylize_post_html("Мой фаворит: эмаль.")
check(" «Мой фаворит:» жирным (регрессия)", "<b>Мой фаворит:</b>" in out or "<b>Фаворит:</b>" in out, repr(out))

print("— 9a. Регрессии v4 через полный пайплайн (polish → stylize) —")
from bot.text_polish import polish_grammar
full = stylize_post_html(polish_grammar("Кухня 3 метра стоит от 95000 ₽. Диапазон 15-25 дней."))
check(" Цена с nbsp", "95\xa0000\xa0₽" in full, repr(full))
check(" Диапазон с тире", "15–25" in full, repr(full))
check(" Год 2026 без разрядки", "2 026" not in full)
out = _split_merged_labels("Звоните 448-37-17 или пишите")
check(" РЕГРЕСС: телефон не разрезан", "448-37-17" in out)
out = _split_merged_labels("Выбирайте 🅰 или 🅱 — оба хороши")
check(" РЕГРЕСС: «🅰 или 🅱» не тронут", "🅰 или 🅱" in out)
out = stylize_post_html(polish_grammar("Кухня 3 метра стоит от 95000 ₽. Диапазон 15-25 дней."))
check(" РЕГРЕСС: цена с nbsp", "95\xa0000\xa0₽" in out, repr(out))
check(" РЕГРЕСС: диапазон с тире", "15–25" in out, repr(out))

print("— 10. Dispatcher: 14 роутеров в порядке main.py —")
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from bot import database as db
from bot import config as cfg

dp = Dispatcher(storage=MemoryStorage())
routers = [m.admin_router, m.lead_router, m.quiz_router, m.calc_router,
           m.inspiration_router, m.storage_router, m.compare_router,
           m.mistakes_router, m.sizes_router, m.gallery_router,
           m.chat_router, m.group_router, m.channel_router, m.inline_router]
for r in routers:
    dp.include_router(r)
check(" Dispatcher собрался с 14 роутерами", len(dp.sub_routers) == 14, len(dp.sub_routers))
check(" sizes_router ДО chat_router", routers.index(m.sizes_router) < routers.index(m.chat_router))
check(" gallery_router ДО chat_router", routers.index(m.gallery_router) < routers.index(m.chat_router))

print("— 11. Компиляция всех файлов бота —")
import py_compile
bot_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
comp_ok = True
for root, _dirs, files in os.walk(bot_dir):
    if "__pycache__" in root:
        continue
    for f in files:
        if f.endswith(".py"):
            try:
                py_compile.compile(os.path.join(root, f), doraise=True)
            except py_compile.PyCompileError as e:
                comp_ok = False
                print(f"    COMPILE ERROR: {f}: {e}")
check(" Все .py компилируются", comp_ok)

print("— 12. Меню команд содержит /sizes и /gallery —")
check(" /sizes в меню (main.py)", 'BotCommand(command="sizes"' in src_main)
check(" /gallery в меню (main.py)", 'BotCommand(command="gallery"' in src_main)
help_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot", "handlers", "chat.py"), encoding="utf-8").read()
check(" /sizes в /help", "/sizes" in help_src)
check(" /gallery в /help", "/gallery" in help_src)

print("— 13a. Штампы внутри первого абзаца (прод-кейс 9229) —")
prod = ("Часто ли вы замечали, как ножки мебели в вашем интерьере становятся тем самым акцентом? 🛋✨\n\n"
        "В своих проектах я не раз сталкивалась с ситуациями, когда открытая или скрытая конструкция мебели "
        "играла решающую роль в восприятии всего пространства. Сегодня хочу поделиться с вами чек-листом, "
        "который поможет избежать распространенных ошибок при работе с видимой или скрытой рамой мебели.\n\n"
        "Чек-лист дизайнера: проверьте материал ножек, глубину цоколя и скрытие креплений — детали решают всё, "
        "а фурнитура с доводчиками продлит жизнь фасадам на годы вперёд.")
out = clean_post_text(prod)
check(" «Сегодня хочу поделиться…» срезано", "поделиться" not in out, repr(out[:120]))
check(" Хук цел", out.startswith("Часто ли вы замечали"), repr(out[:40]))
check(" Чек-лист-часть цела", "Чек-лист дизайнера" in out)
check(" Длина достаточная", len(out) >= 250, len(out))
ok_post = ("Матовые фасады практичнее глянца: на них не видно капель и отпечатков.\n\n"
           "В моих проектах для семей с детьми я почти всегда предлагаю матовые МДФ-эмали — "
           "они моются любым мягким средством и не боятся ежедневных протираний, а царапины "
           "на глянце со временем становятся заметны и портят вид всей кухни.")
out = clean_post_text(ok_post)
check(" Пост без штампов не тронут", out.startswith("Матовые фасады") and "не раз" not in out[:0] and len(out) > 250, repr(out[:60]))
mid = ("Шкаф-купе с зеркалом расширяет прихожую. Хочу поделиться с вами приёмом: зеркало на средней двери, "
       "а не на крайней — отражение освещает весь коридор равномерно и не слепит у входа.\n\n"
       "Для узких коридоров берём глубину 600 мм и двери 700–900 мм: ролики живут дольше, доступ к полкам удобнее, "
       "а доводчики исключают хлопки в ночное время — тишина, которую отмечают все клиенты.\n\n"
       "Верхнюю зону отдаём под антресоли для сезонных вещей: шляпы, коробки с зимней обувью, чемоданы — "
       "всё то, что достают пару раз в год, поднимаем наверх и освобождаем нижние полки для повседневного.")
out = clean_post_text(mid)
check(" «Хочу поделиться…» в середине абзаца срезано", "поделиться" not in out, repr(out[:80]))
check(" Хук цел", out.startswith("Шкаф-купе с зеркалом расширяет прихожую"), repr(out[:50]))
check(" Хвост поста цел", "антресоли для сезонных вещей" in out)
short = ("Кухни на заказ. Сегодня хочу поделиться идеей матовых фасадов.")
out = clean_post_text(short + " " + "Допишем немного текста, чтобы проверить защиту от слишком короткого результата после среза штампа.")
check(" Защита: короткий пост после среза остаётся как есть", "Сегодня хочу поделиться" in out, repr(out[:60]))

print("— 13. Caption-пайплайн с новым футером (регрессия) —")
from bot.main import build_channel_footer, _visible_len, _style_channel_post
from bot.post_utils import smart_truncate_html, clean_post_text

raw = ("Хук про шкаф-купе с фактурой дуба?\n\n"
       "Плюсы: вместимость, тихие доводчики, зеркало внутри.\n"
       "Минусы: рольики нужно чистить раз в год. Цена: от 45 000 ₽.\n\n"
       "Вывод: для спальни это самый практичный вариант хранения. #шкафы #спальня")
styled = _style_channel_post(clean_post_text(raw))
footer = build_channel_footer()
cap = smart_truncate_html(styled, 780, len(footer), append_ellipsis=False) + footer
check(" Видимая длина caption ≤1024", _visible_len(cap) <= 1024, _visible_len(cap))
check(" Футер содержит CTA-строку", any(x in cap for x in ("/calc", "/storage", "/sizes", "/gallery", "Замер и 3D-проект", "материал под ваш бюджет", "/process", "/faq")))
check(" «· · ·» присутствует", "· · ·" in cap)
check(" Плюсы/Минусы на строках", "\nМинусы:" in cap or "\n<b>Минусы:" in cap, repr(cap[-200:]))

print()
print(f"ИТОГО: {PASS} ✅, {len(FAIL)} ❌")
if FAIL:
    print("ПРОВАЛЕНО:", *FAIL, sep="\n  - ")
    sys.exit(1)
print("ВСЕ ТЕСТЫ ЗЕЛЁНЫЕ 🎉")
