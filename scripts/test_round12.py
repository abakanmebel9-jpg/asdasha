#!/usr/bin/env python3
"""
Раунд 12 — тесты: детект бюджетной ошибки Pollinations (HTTP 200 с текстом
«…reached its budget…» вместо ответа), ротация мёртвых ключей, анонимные
опросы в канале, честный лог fallback-цикла, фильтр тем дайджеста
(кириллица + мебель), скип-новости без title в БД.
Запуск: venv/bin/python scripts/test_round12.py
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


print("── 1. Детект бюджетной ошибки Pollinations ─────────────────────────")
from ai.client import (
    _is_pollinations_error_text,
    _mark_poll_key_dead,
    _get_pollinations_key,
    _POLL_KEY_DEAD,
    _POLL_KEY_DEAD_SEC,
)

BUDGET_MSG = (
    "The API key used for this request has reached its budget. Please raise "
    "the key budget (https://enter.pollinations.ai/edit-key?id=xxx), then try again."
)
check("Бюджетная ошибка детектится", _is_pollinations_error_text(BUDGET_MSG))
check("Квота OpenAI-стиля детектится", _is_pollinations_error_text("Error: insufficient quota"))
check("Rate limit детектится", _is_pollinations_error_text("Rate limit exceeded, slow down"))
check("Нормальный русский ответ НЕ ошибка", not _is_pollinations_error_text(
    "Кухня из МДФ-эмали прослужит 10-15 лет при нормальном уходе. 🛋"))
check("Пустая строка не ошибка", not _is_pollinations_error_text(""))
check("Пост про пылесос Honeywell не ложно детектится (нет маркеров)",
      not _is_pollinations_error_text("Honeywell — известный бренд пылесосов для дома"))

print("── 2. Ротация мёртвых ключей ───────────────────────────────────────")
import ai.client as ai_mod

orig_keys = list(ai_mod._POLLINATIONS_KEYS)
orig_idx = ai_mod._POLLINATIONS_KEY_IDX
orig_dead = dict(_POLL_KEY_DEAD)
try:
    ai_mod._POLLINATIONS_KEYS = ["keyA", "keyB", "keyC"]
    _POLL_KEY_DEAD.clear()
    ai_mod._POLLINATIONS_KEY_IDX = 0

    k1 = _get_pollinations_key()
    check("Первый ключ выдаётся", k1 == "keyA", f"got {k1!r}")
    _mark_poll_key_dead("keyA")
    k2 = _get_pollinations_key()
    check("Мёртвый keyA пропущен → keyB", k2 == "keyB", f"got {k2!r}")
    _mark_poll_key_dead("keyB")
    k3 = _get_pollinations_key()
    check("keyB тоже мёртв → keyC", k3 == "keyC", f"got {k3!r}")
    _mark_poll_key_dead("keyC")
    k4 = _get_pollinations_key()
    check("Все ключи мертвы → '' (анонимный тир)", k4 == "", f"got {k4!r}")

    # Восстановление через cooldown-время
    for k in list(_POLL_KEY_DEAD):
        _POLL_KEY_DEAD[k] = 0.0
    k5 = _get_pollinations_key()
    check("После сброса cooldown ключ снова жив", k5 in ("keyA", "keyB", "keyC"))
    check("Мёртвый ключ живёт ~час", 3500 <= _POLL_KEY_DEAD_SEC <= 3700)
finally:
    ai_mod._POLLINATIONS_KEYS = orig_keys
    ai_mod._POLLINATIONS_KEY_IDX = orig_idx
    _POLL_KEY_DEAD.clear()
    _POLL_KEY_DEAD.update(orig_dead)

print("── 3. _call_pollinations_direct: бюджет-ошибка → ротация, не ответ ─")
async def t_budget_flow():
    calls = []

    class FakeResp:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": BUDGET_MSG}}]}

    class FakeClient:
        async def post(self, url, **kw):
            calls.append(kw.get("headers", {}).get("Authorization", ""))
            return FakeResp()

    orig_keys2 = list(ai_mod._POLLINATIONS_KEYS)
    orig_client = ai_mod._client
    orig_skip = ai_mod._poll_skip_until
    try:
        ai_mod._POLLINATIONS_KEYS = ["keyA", "keyB"]
        _POLL_KEY_DEAD.clear()
        ai_mod._client = FakeClient()
        ai_mod._poll_skip_until = 0.0
        out = await ai_mod._call_pollinations_direct(
            [{"role": "user", "content": "тест"}], 100, retries=2)
        check("Бюджет-текст НЕ возвращён как ответ", out == "", f"got {out[:60]!r}")
        check("Оба ключа помечены мёртвыми", len(_POLL_KEY_DEAD) == 2)
        check("Была попытка анонимного тира после мёртвых ключей",
              "" in calls, f"headers seen: {calls}")
        check("Глобальный cooldown выставлен", ai_mod._poll_skip_until > 0)
    finally:
        ai_mod._POLLINATIONS_KEYS = orig_keys2
        ai_mod._client = orig_client
        _POLL_KEY_DEAD.clear()
        ai_mod._poll_skip_until = orig_skip

asyncio.run(t_budget_flow())

print("── 4. Опросы в канале: только анонимные ────────────────────────────")
import inspect
from bot import polls as polls_mod
src = inspect.getsource(polls_mod.maybe_send_poll)
check("is_anonymous=True в коде отправки опросов", "is_anonymous=True" in src)
check("is_anonymous=False больше нет", "is_anonymous=False" not in src)

print("── 5. Честный лог fallback-цикла (main.py) ─────────────────────────")
with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "bot", "main.py"), encoding="utf-8") as f:
    main_src = f.read()
check("fb_ok проверяется перед логом успеха", "if fb_ok:" in main_src)
check("Есть WARNING при провале fallback",
      "fallback topic FAILED" in main_src)
check("Результат _post_ai_topic сохраняется в fb_ok",
      "fb_ok = await self._post_ai_topic" in main_src)
check("Скип-новости помечаются с пустым title",
      'mark_news_posted(news_id, "")' in main_src)

print("── 6. Дайджест: фильтр кириллицы + мебельной релевантности ────────")
from bot.channel_digest import _cyrillic_ratio, _digest_topic_ok, build_digest_text

check("Русский текст → ratio 1.0", _cyrillic_ratio("Кухни на заказ в Абакане") > 0.99)
check("Английский текст → ratio 0.0", _cyrillic_ratio("Fiat 500 restomod") < 0.01)
check("Смешанный с эмодзи/цифрами", 0.5 < _cyrillic_ratio("Кухня 🛋 2024 года") <= 1.0)

check("Англ. автомобиль отсечён", not _digest_topic_ok(
    "Hot Lab and Garage Italia give the Fiat 500 Spiaggina restomod a nautical…"))
check("Англ. динозавр отсечён", not _digest_topic_ok(
    "She Cut a Toy Dinosaur in Half to Make It Look Like It's Walking Through Her…"))
check("Русская мебельная тема проходит", _digest_topic_ok(
    "Как выбрать ЛДСП для корпуса кухни: классы эмиссии и влагостойкость"))
check("Короткая тема отсечена", not _digest_topic_ok("Кухня"))
check("Русская немебельная тема отсечена", not _digest_topic_ok(
    "Футбольный матч закончился со счётом 3:1 в пользу гостей турнира"))

digest = build_digest_text(
    [
        "Hot Lab and Garage Italia give the Fiat 500 Spiaggina restomod a nautical twist",
        "Как выбрать ЛДСП для корпуса кухни: классы эмиссии и влагостойкость",
        "Петли с доводчиком: почему нельзя экономить на фурнитуре",
    ],
    "2026-W37",
)
check("Англ. заголовки НЕ попали в текст дайджеста", "Fiat" not in digest)
check("Русские мебельные темы в дайджесте", "ЛДСП" in digest and "Петли" in digest)

print("── 7. Регрессия: strip-ads и ключевые экспорты на месте ────────────")
from ai.client import _strip_pollinations_ads, chat, vision, transcribe_audio  # noqa: F401
from ai.client import stats as ai_stats  # noqa: F401
ads = "Текст про кухни.\n\n---\n\n**Support Pollinations.AI:**\n\n---\n\n🌸 **Ad** 🌸 Powered by..."
check("Ad-суффикс срезается", _strip_pollinations_ads(ads).startswith("Текст про кухни."))
check("Бюджет-текст после срезки рекламы всё ещё детектится",
      _is_pollinations_error_text(_strip_pollinations_ads(BUDGET_MSG + "\n\n---\n\n**Support Pollinations**")))

print()
print(f"ИТОГО: {PASS} ✅, {len(FAIL)} ❌")
if FAIL:
    print("ПРОВАЛЕНО:", *FAIL, sep="\n  - ")
    sys.exit(1)
print("ВСЕ ТЕСТЫ ЗЕЛЁНЫЕ 🎉")
