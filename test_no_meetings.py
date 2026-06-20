#!/usr/bin/env python3
"""Standalone test for the _enforce_no_meetings safety filter and prompt prohibitions.

Run: python3 test_no_meetings.py

Verifies:
1. The safety filter catches all known meeting-arrangement phrases and appends the phone.
2. The safety filter does NOT touch responses that already contain the phone.
3. The safety filter does NOT touch clean responses (no meeting-arrangement intent).
4. All system prompts (COMPACT, LOCAL, CHAT route, COMMENT route, persona) contain
   an explicit prohibition against arranging meetings/measurements.
5. The old offending phrase "Запишу вас на бесплатный замер?" is no longer present
   as an instruction in any prompt.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai.router import (
    ai_router,
    COMPACT_SYSTEM_PROMPT,
    LOCAL_MODEL_SYSTEM_PROMPT,
    _enforce_no_meetings,
    _MEETING_COMPILED,
    _PHONE_REDIRECT_SUFFIX,
)
from bot.config import persona, config

PHONE = config.PHONE  # +7 (913) 448-37-17

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} {detail}")


print("=" * 70)
print("🧪 Dasha Bot — No-Meetings Safety Filter Test")
print("=" * 70)
print(f"Phone: {PHONE}")
print(f"Patterns compiled: {len(_MEETING_COMPILED)}")
print()

# ─── 1. PROMPT PROHIBITIONS ──────────────────────────────────────────────
print("── 1. PROMPT PROHIBITIONS ──")

check(
    "COMPACT_SYSTEM_PROMPT prohibits arranging meetings",
    "не договаривайся" in COMPACT_SYSTEM_PROMPT.lower(),
)
check(
    "COMPACT_SYSTEM_PROMPT no longer instructs 'Запишу вас на бесплатный замер?'",
    "Запишу вас на бесплатный замер?" not in COMPACT_SYSTEM_PROMPT,
)
check(
    "COMPACT_SYSTEM_PROMPT no longer instructs 'записаться на замер'",
    "записаться на замер" not in COMPACT_SYSTEM_PROMPT,
)

check(
    "LOCAL_MODEL_SYSTEM_PROMPT prohibits arranging meetings",
    "ЗАПРЕЩЕНО" in LOCAL_MODEL_SYSTEM_PROMPT
    and "договариваться" in LOCAL_MODEL_SYSTEM_PROMPT.lower(),
)

chat_prompt = ai_router._build_system_prompt("chat")
check(
    "CHAT route prompt has 'КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО'",
    "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО" in chat_prompt,
)
check(
    "CHAT route prompt no longer instructs 'Запишу на бесплатный замер?'",
    "Запишу на бесплатный замер?" not in chat_prompt,
)

comment_prompt = ai_router._build_system_prompt("comment")
check(
    "COMMENT route prompt prohibits arranging meetings",
    "не договаривайся" in comment_prompt.lower(),
)

check(
    "persona.system_prompt has 'КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО'",
    "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО" in persona["system_prompt"],
)
check(
    "persona.system_prompt no longer says 'записывайся на замер'",
    "записывайся на замер" not in persona["system_prompt"],
)
check(
    "persona.system_prompt no longer says 'записаться на бесплатный замер'",
    "записаться на бесплатный замер" not in persona["system_prompt"],
)
print()

# ─── 2. SAFETY FILTER — should ADD phone ─────────────────────────────────
print("── 2. SAFETY FILTER — meeting phrases WITHOUT phone → append phone ──")

bad_inputs = [
    "О, отличная кухня! Запишу вас на замер завтра в 15:00.",
    "Давайте запишемся на бесплатный замер на пятницу.",
    "Хорошо, встретимся завтра в 14:00 для обсуждения проекта.",
    "Давайте встретимся в нашем салоне на ул. Гончарной!",
    "Я приеду к вам завтра утром на замер.",
    "Приезжайте к нам в салон, всё покажем и расскажем.",
    "Заезжайте к нам на производство, проведём экскурсию.",
    "Я подъеду к вам в понедельник в 11:00.",
    "Назначим встречу на завтра в 16:00?",
    "Договорились о встрече в субботу.",
    "Давайте назначим удобное время для встречи.",
    "Завтра в 15:00 замерщик приедет к вам.",
    "Запишу тебя на замер на завтра.",
    "В среду в 12:00 я буду у вас с образцами материалов.",
]

for i, bad in enumerate(bad_inputs, 1):
    result = _enforce_no_meetings(bad)
    has_phone_after = (
        "+7 (913) 448-37-17" in result
        or "79134483717" in result
        or "tel:+79134483717" in result
    )
    check(
        f"bad input #{i} gets phone appended",
        has_phone_after,
        f"(input: {bad[:50]}...)",
    )
    # The redirect suffix must be present
    check(
        f"bad input #{i} has redirect suffix",
        _PHONE_REDIRECT_SUFFIX.strip() in result or "Позвоните" in result,
    )

print()

# ─── 3. SAFETY FILTER — should NOT modify (phone already present) ────────
print("── 3. SAFETY FILTER — meeting phrase WITH phone → no change ──")

good_with_phone = [
    "Запишу вас на замер! Позвоните +7 (913) 448-37-17, договоримся о времени.",
    "Давайте встретимся. Звоните 79134483717 — назначим время.",
    "Встретимся завтра? Запись по телефону +7 (913) 448-37-17.",
]
for i, text in enumerate(good_with_phone, 1):
    result = _enforce_no_meetings(text)
    check(
        f"with-phone #{i} unchanged (no duplicate suffix)",
        result == text,
        f"got: {result[:80]}...",
    )

print()

# ─── 4. SAFETY FILTER — should NOT touch clean responses ─────────────────
print("── 4. SAFETY FILTER — clean responses unchanged ──")

clean_inputs = [
    "Привет! 😊 Я Даша, дизайнер мебели. Чем могу помочь?",
    "Для кухни лучше всего подойдёт МДФ с ПВХ-плёнкой — влагостойкий и практичный. 🪵",
    "Дуб — премиальный материал, очень прочный и долговечный. Подойдёт для классического стиля. 🏡",
    "Кухни на заказ начинаются от 45 000 рублей. Срок изготовления 14-31 день.",
    "О, классный вопрос! Скандинавский стиль — светлый и уютный. 🌟",
    "Доставка по Абакану бесплатно, сборка включена в стоимость. 🚚",
    "",  # empty string
    "Короткий ответ без встречи.",
]
for i, text in enumerate(clean_inputs, 1):
    result = _enforce_no_meetings(text)
    check(
        f"clean #{i} unchanged",
        result == text,
        f"input: {text[:50]!r} → got: {result[:50]!r}",
    )

print()

# ─── 5. EDGE CASES ───────────────────────────────────────────────────────
print("── 5. EDGE CASES ──")

# "договоримся по телефону" is the desired phrasing — should not trigger
ok_text = "Звоните +7 (913) 448-37-17, договоримся по телефону об удобном времени."
result = _enforce_no_meetings(ok_text)
check(
    "'договоримся по телефону' with phone → unchanged",
    result == ok_text,
)

# "договоримся по телефону" WITHOUT phone → this is fine phrasing but still
# mentions arranging via phone — we want the phone present.
ok_text2 = "Позвоните, договоримся по телефону об удобном времени замера."
result2 = _enforce_no_meetings(ok_text2)
# "договоримся по телефону" matches a pattern; phone is missing → append
check(
    "'договоримся по телефону' WITHOUT phone → phone appended",
    "+7 (913) 448-37-17" in result2 or "79134483717" in result2,
)

print()

# ─── SUMMARY ─────────────────────────────────────────────────────────────
print("=" * 70)
print(f"📊 RESULTS: {passed} ✅  |  {failed} ❌")
print("=" * 70)
if failed == 0:
    print("🎉 ALL CHECKS PASSED — Dasha will never arrange meetings, always gives phone!")
else:
    print("⚠️  Some checks failed — review above.")
sys.exit(0 if failed == 0 else 1)
