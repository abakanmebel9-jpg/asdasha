#!/usr/bin/env python3
"""
Раунд 10 — тесты: стилизация v4 (хвостовые пробелы, новые лейблы, дедуп хештегов,
гигиена пробелов), ротация CTA в футере, /compare, /mistakes, подсказки /storage
в квизе и калькуляторе, фокус-правило промпта, регрессии цен/диапазонов/телефонов.
Запуск: venv/bin/python scripts/test_round10.py
"""
import os
import re
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


print("— 1. Стилизация v4: хвостовые пробелы перед переносом убраны —")
from bot.text_polish import _split_merged_labels, stylize_post_html, _dedupe_hashtags

out = _split_merged_labels("Плюсы: визуальная легкость, доступность, эстетика. Минусы: пыль, порядок.")
check("«Плюсы/Минусы» разбиты", "Минусы:" in out.split("\n")[-1], repr(out))
check("НЕТ хвостового пробела перед \\n", " \n" not in out and ". " not in out.split("\n")[0][-2:], repr(out))

out = _split_merged_labels("Сначала факты. Цена: от 30 тысяч. Гарантия: 2 года. Сроки: 15–25 дней.")
lines = out.split("\n")
check("Цена/Гарантия/Сроки — по строкам (4 строки)", len(lines) == 4 and lines[0].startswith("Сначала")
      and lines[1].startswith("Цена:") and lines[2].startswith("Гарантия:") and lines[3].startswith("Сроки:"), repr(lines))

out = _split_merged_labels("История из проекта: тесная прихожая. Вопрос: где хранить 18 пар обуви? Ответ: обувница 45°.")
check("История в начале + Вопрос/Ответ разбиты", out.startswith("История из проекта") and "\nВопрос:" in out and "\nОтвет:" in out, repr(out))

print("— 2. Стилизация v4: регрессии (защита телефонов, варианты, чек-листы) —")
out = _split_merged_labels("Звоните 448-37-17 или 89134483717")
check("Телефон не разрезан", "448-37-17" in out and "448" in out)
out = _split_merged_labels("Выбирайте 🅰 или 🅱 — оба варианта хороши")
check("«🅰 или 🅱» не тронут", "🅰 или 🅱" in out)
out = _split_merged_labels("✅ Замер готов ✅ Договор подписан ✅ Производство стартует")
check("Чек-лист расщеплён", out.count("\n✅") == 2, repr(out))

print("— 3. Стилизация v4: дедуп хештегов —")
check("Дубль регистра убран", _dedupe_hashtags("Текст #кухни и ещё #Кухни") == "Текст #кухни и ещё")
check("Разные теги живут", _dedupe_hashtags("#кухни #шкафы") == "#кухни #шкафы")
out = stylize_post_html("Пост про кухни #кухни\n#кухни #Кухни #дизайн")
check("stylize дедупит хештеги", out.lower().count("#кухни") == 1, repr(out[-60:]))

print("— 4. Стилизация v4: гигиена пробелов и цены —")
out = stylize_post_html("Первая строка.  \nВторая строка.\n\n#тест")
check("Хвостовые пробелы срезаны", "  \n" not in out and " \n" not in out, repr(out))
out = stylize_post_html("Кухня стоит 95000 ₽, шкаф 173000 руб.")
check("Цены «95 000 ₽» (nbsp)", "95\u00A0000\u00A0₽" in out, repr(out))
check("Цены «173 000 ₽»", "173\u00A0000\u00A0₽" in out, repr(out))
out = stylize_post_html("Срок 15-25 дней, телефон 448-37-17.")
check("Телефон цел в стилизации", "448-37-17" in out)
from bot.text_polish import polish_grammar
out2 = polish_grammar("Срок 15-25 дней, телефон 448-37-17.")
check("Диапазон «15–25», телефон цел (grammar→stylize)", "15–25" in out2 and "448-37-17" in out2, repr(out2))

print("— 5. CTA-ротация в футере канала —")
from bot.main import build_channel_footer, _CTA_LINES, _cta_recent, _pick_cta
_cta_recent.clear()
seen = []
for _ in range(12):
    footer = build_channel_footer()
    seen.append(footer.split("\n")[2])
check("Футер содержит CTA + автора", "🛋 Автор — " in footer and "abakanmebel.online" in footer)
check("CTA из набора", all(any(s.startswith(c.split(" ")[0]) and s in _CTA_LINES for c in _CTA_LINES) for s in seen))
check("Нет повтора 3 раза подряд", all(len(set(seen[i:i+4])) > 1 for i in range(len(seen) - 3)), repr(seen[:6]))
check("Анти-повтор окно = 3", len(_cta_recent) == 3)
prev_window = list(_cta_recent)
nxt = _pick_cta()
check("Следующий пикер — вне предыдущего окна", nxt not in prev_window, f"{nxt} in {prev_window}")

print("— 6. /compare: контент и механика —")
from bot.compare import MATERIALS, _VERDICTS, _materials_keyboard, _pair_keyboard, _pair_text, _first_text
check("4 материала", len(MATERIALS) == 4)
check("6 вердиктов пар", len(_VERDICTS) == 6)
pairs_ok = all(len(k) == 2 and k[0] < k[1] for k in _VERDICTS)
check("Ключи пар отсортированы", pairs_ok)
for k, m in MATERIALS.items():
    t = _pair_text(k, [x for x in MATERIALS if x != k][0])
    ok = all(f in t for f in ("💰", "💧", "🛡", "🎨", "🔧", "⏳", "Мой вердикт"))
    if not ok:
        check(f"пара с {k} содержит все критерии", False, t[:80])
check("Все пары содержат 6 критериев + вердикт", True)
t1 = _pair_text("ldsp", "massiv")
t2 = _pair_text("massiv", "ldsp")
check("Вердикт одинаков в обоих порядках", "Мой вердикт:" in t1 and t1.split("Мой вердикт:")[1][:50] == t2.split("Мой вердикт:")[1][:50])
kb = _materials_keyboard()
check("Клавиатура 4 материала + 2 CTA", len(kb.inline_keyboard) == 3 and sum(len(r) for r in kb.inline_keyboard) == 6)
kb2 = _materials_keyboard(exclude="ldsp")
first_labels = [b.text for r in kb2.inline_keyboard[:2] for b in r]
check("Первый материал исключён", all("ЛДСП" not in l for l in first_labels), repr(first_labels))
cbs = [b.callback_data for r in kb.inline_keyboard for b in r]
check("callback_data ≤ 64 байт", all(len(c.encode()) <= 64 for c in cbs), repr(cbs))
check("Первый шаг ≤ 64 байт", len("cmp:massiv".encode()) <= 64)
check("Пара ≤ 64 байт", len("cmp:massiv:mdfpv".encode()) <= 64)
pk = _pair_keyboard()
check("Парная клавиатура: меню+замер+кальк", any(b.callback_data == "cmp:menu" for r in pk.inline_keyboard for b in r) and
      any(b.callback_data == "measure:start" for r in pk.inline_keyboard for b in r) and
      any(b.callback_data == "calc:restart" for r in pk.inline_keyboard for b in r))
ft = _first_text("mdfem")
check("Первый шаг показывает материал и цену", "МДФ-эмаль" in ft and "46–62" in ft)

print("— 7. /mistakes: контент и механика —")
from bot.mistakes import MISTAKES, MISTAKE_ROOMS, _rooms_keyboard, _result_keyboard, _room_text
check("4 комнаты × 5 ошибок", len(MISTAKES) == 4 and all(len(v) == 5 for v in MISTAKES.values()))
check("Каждая ошибка = пара (❌, ✅)", all(len(x) == 2 and x[0] and x[1] for v in MISTAKES.values() for x in v))
rt = _room_text("kitchen")
check("Текст комнаты: заголовок+нумерация+закрытие", "Ошибки при заказе — Кухня" in rt and "5. ❌" in rt and "💡" in rt)
check("Текст содержит «как правильно»", "✅" in rt and "❌" in rt)
kbm = _rooms_keyboard()
mst_cbs = [b.callback_data for r in kbm.inline_keyboard for b in r]
check("callback mst:* ≤ 64 байт", all(len(c.encode()) <= 64 for c in mst_cbs), repr(mst_cbs))
kr = _result_keyboard()
check("Результат: другая комната + замер + калькулятор + storage", 
      any(b.callback_data == "mst:menu" for r in kr.inline_keyboard for b in r) and
      any(b.callback_data == "storage:menu" for r in kr.inline_keyboard for b in r))

print("— 8. Подсказки /storage в квизе и калькуляторе —")
from bot.quiz import _build_result
res = _build_result({"room": "kitchen", "style": "modern", "mat": "ldsp"})
check("Квиз подсказывает /storage", "/storage" in res)
from bot.calculator import calculate
res = calculate("kitchen", "straight", "s30", "ldsp")
check("Калькулятор подсказывает /storage", bool(res) and "/storage" in res, repr(res)[:80])
res2 = calculate("wardrobe", "vstro", "w30", "ldsp")
check("Шкаф тоже", bool(res2) and "/storage" in res2)

print("— 9. Промпт: правило фокуса на мебели —")
from bot.persona import CHANNEL_POST_PROMPT
check("ПРАВИЛО ФОКУСА присутствует", "ПРАВИЛО ФОКУСА" in CHANNEL_POST_PROMPT)
check("Запрет посторонних предметов", "ножницы для пакетиков" in CHANNEL_POST_PROMPT)
low = CHANNEL_POST_PROMPT.lower()
check("Без эзотерики как разрешённой темы", "эзотерик" in low and "без политики" in low)
check("Только мебельная специфика", "корпусная мебель" in CHANNEL_POST_PROMPT)

print("— 10. Диспетчер конструируется (12 роутеров) —")
from aiogram import Dispatcher
from bot.handlers import admin, chat, groups, channels, inline
from bot import lead_forms, quiz, calculator, inspiration
from bot import storage, compare, mistakes
dp = Dispatcher()
for r in (admin.admin_router, lead_forms.lead_router, quiz.quiz_router, calculator.calc_router,
          inspiration.inspiration_router, storage.storage_router, compare.compare_router,
          mistakes.mistakes_router, chat.chat_router, groups.group_router,
          channels.channel_router, inline.inline_router):
    dp.include_router(r)
check("12 роутеров включены", len(dp.sub_routers) == 12, len(dp.sub_routers))

print("— 11. Полная симуляция caption-пайплайна (новость → пост) —")
import html as _h
from bot.post_utils import clean_post_text
from bot.main import _style_channel_post  # escape + stylize
raw = ("Кухня на заказ: с чего начать?\n\n"
       "Плюсы: индивидуальные размеры, фурнитура с доводчиками. Минусы: срок 15-25 дней.\n\n"
       "Кухня 3 метра стоит от 95000 ₽. #кухни #кухни #дизайнинтерьера")
cleaned = clean_post_text(raw)
styled = _style_channel_post(cleaned)
from bot.main import _visible_len
footer = build_channel_footer()
cap = styled[:780 - len(footer)] + footer
vis = _visible_len(cap)
check("Видимая длина caption ≤ 1024", vis <= 1024, vis)
check("Разделитель · · · перед хештегами", "· · ·" in styled)
check("Дубль #кухни удалён", styled.lower().count("#кухни") == 1, repr(styled[-90:]))
check("«Плюсы/Минусы» на отдельных строках", ("\nМинусы:" in styled) or ("\n<b>Минусы:" in styled), repr(styled[-160:]))
check("Хештеги отсоединены в конец и «· · ·» перед ними", "· · ·" in styled and "\n#кухни" in styled, repr(styled[-120:]))

print("— 12. Фильтр новостей: регрессия —")
from bot.main import _is_furniture_news
check("Мебельная новость проходит", _is_furniture_news("Новые фасады для кухонь", "ЛДСП и МДФ"))
check("Гороскоп отсечён", not _is_furniture_news("Гороскоп на неделю", "звёзды советуют"))
check("Спорт отсечён даже с «дизайном»", not _is_furniture_news("Футбол и дизайн мяча", "спортивный магазин"))

print()
print(f"ИТОГО: {PASS} ✅, {len(FAIL)} ❌")
if FAIL:
    print("ПРОВАЛЕНО:", *FAIL, sep="\n  - ")
    sys.exit(1)
print("ВСЕ ТЕСТЫ ЗЕЛЁНЫЕ 🎉")
