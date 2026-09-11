"""
Даша Text Polish — Russian typography polishing.

Deterministic post-generation text polishing:
- `-` → `—` (em dash) in dialogue/separators
- `"..."` → `«...»` (guillemets)
- `...` → `…` (ellipsis character)
- `!!!` → `!`, `???` → `?` (normalize multiple punctuation)
- Space-punctuation normalization
- URL/domain protection (don't modify URLs)
- HTML attribute protection

Restored from pre-OpenClaw bot/text_polish.py.
"""

import re
import logging

logger = logging.getLogger("dasha.text_polish")


def polish_grammar(text: str) -> str:
    """Apply Russian typography rules to text.

    Args:
        text: AI-generated post text (may contain HTML tags)

    Returns:
        Polished text with correct Russian typography
    """
    if not text:
        return ""

    # Protect URLs and HTML attributes
    protected = []
    def _protect(m):
        protected.append(m.group(0))
        return f"\x00{len(protected) - 1}\x00"

    # Protect <a href="..."> and tel: and https://
    text = re.sub(r'<a\s+[^>]*>', _protect, text)
    text = re.sub(r'</a>', _protect, text)
    text = re.sub(r'https?://[^\s<>"\']+', _protect, text)
    text = re.sub(r'tel:\+?[\d\s\-()]+', _protect, text)
    # Protect domain names (word.tld patterns like abakanmebel.online, example.com)
    text = re.sub(r'\b[a-z0-9][a-z0-9\-]*\.(?:online|ru|com|net|org|io|me|pro|store|shop|dev|info|biz|su|рф)\b', _protect, text, flags=re.IGNORECASE)

    # 1. Ellipsis: ... → … (but not .... which stays as ….)
    text = re.sub(r'\.\.\.(?!\.)', '…', text)
    text = re.sub(r'\.{4,}', '…', text)

    # 2. Em dash: " - " → " — " (with spaces around), but not in URLs (protected)
    # Only replace standalone hyphens surrounded by spaces
    text = re.sub(r'\s+—\s+', ' — ', text)  # already em dash
    text = re.sub(r'\s+-\s+', ' — ', text)  # hyphen to em dash
    # Dialogue start: "^— " at beginning of line
    text = re.sub(r'^-\s+', '— ', text, flags=re.MULTILINE)

    # 3. Guillemets: "..." → «...»
    # Match paired double quotes
    def _replace_quotes(m):
        inner = m.group(1)
        return f"«{inner}»"
    # Straight quotes
    text = re.sub(r'"([^"]{1,200})"', _replace_quotes, text)
    # Smart quotes (already curly)
    text = re.sub(r'"([^"]{1,200})"', _replace_quotes, text)
    text = re.sub(r'"([^"]{1,200})"', _replace_quotes, text)

    # 4. Normalize multiple punctuation
    text = re.sub(r'!{2,}', '!', text)
    text = re.sub(r'\?{2,}', '?', text)
    text = re.sub(r'!{1,}\?{1,}', '?!', text)
    text = re.sub(r'\?{1,}!{1,}', '?!', text)

    # 5. Space-punctuation normalization
    # Remove space before ,.;:!? but not after
    text = re.sub(r'\s+([,.;:!?])', r'\1', text)
    # Ensure space after ,.;:!? (but not for decimals like 3,14 or URLs)
    text = re.sub(r'([,;:])([^\s\d\n.«»])', r'\1 \2', text)
    text = re.sub(r'([.!?])([^\s\d\n.«»«»])', r'\1 \2', text)

    # 6. Fix spacing around parentheses
    text = re.sub(r'\(\s+', '(', text)
    text = re.sub(r'\s+\)', ')', text)

    # 7. Квадратные/кубические метры: «кв.м», «м2» → «м²», «м3» → «м³»
    text = re.sub(r'\bкв\.\s*м\b', 'м²', text, flags=re.IGNORECASE)
    text = re.sub(r'(\d)\s?м2\b', r'\1 м²', text, flags=re.IGNORECASE)
    text = re.sub(r'(\d)\s?м3\b', r'\1 м³', text, flags=re.IGNORECASE)

    # 7.1 Неразрывный пробел между числом и единицей измерения
    # (не трогаем одиночную «м» — слишком многозначна: «3 месяца», «3 м»)
    text = re.sub(r'(\d)\s+(мм|см|дм|кг|км|м²|м³|лет)\b', '\\1\u00A0\\2', text)

    # 7.2 Числовые диапазоны: «15-25» → «15–25» (среднее тире).
    # Телефоны и даты защищены контекстом: правило требует отсутствия
    # дефисов/букв/цифр вокруг («448-37-17» не матчится, «2026-09-11» тоже).
    text = re.sub(r'(?<![\w\-\u00A0])(\d{1,3})-(\d{1,3})(?![\w\-\u00A0])', '\\1–\\2', text)

    # 8. Fix multiple spaces
    text = re.sub(r'[ \t]{2,}', ' ', text)

    # 9. Fix multiple newlines (max 2)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 10. Strip leading/trailing whitespace
    text = text.strip()

    # Restore protected content
    def _restore(m):
        idx = int(m.group(1))
        if 0 <= idx < len(protected):
            return protected[idx]
        return m.group(0)
    text = re.sub(r'\x00(\d+)\x00', _restore, text)

    return text


def linkify_contacts(text: str, phone: str = "+7 (913) 448-37-17",
                     site: str = "abakanmebel.online") -> str:
    """Add HTML links for phone number and website in text.

    Converts:
        +7 (913) 448-37-17 → <a href="tel:+79134483717">+7 (913) 448-37-17</a>
        abakanmebel.online → <a href="https://abakanmebel.online">abakanmebel.online</a>
    """
    if not text:
        return text

    # Linkify phone (various formats)
    phone_pattern = re.escape(phone)
    tel_link = phone.replace(" ", "").replace("(", "").replace(")", "").replace("-", "")
    text = re.sub(phone_pattern, f'<a href="tel:{tel_link}">{phone}</a>', text)

    # Linkify website (if not already in an <a> tag)
    # Skip if already inside href=""
    def _linkify_site(m):
        # Check if already inside a tag
        start = m.start()
        before = text[:start]
        if before.rfind('<a ') > before.rfind('</a>'):
            return m.group(0)  # already inside <a> tag
        return f'<a href="https://{site}">{site}</a>'

    site_pattern = re.escape(site)
    # Only linkify if not already in a link
    text = re.sub(f'(?<!["\'>=]){site_pattern}', _linkify_site, text)

    return text


def dedupe_contacts(text: str) -> str:
    """Remove duplicate contact info (phone/site appearing multiple times)."""
    if not text:
        return text

    # Find all <a> tags with tel: or https:
    links = re.findall(r'(<a\s+href="(?:tel:|https?://)[^"]*"[^>]*>[^<]*</a>)', text)
    seen = set()
    for link in links:
        # Extract the URL
        url_match = re.search(r'href="([^"]*)"', link)
        if url_match:
            url = url_match.group(1)
            if url in seen:
                # Remove duplicate (and surrounding whitespace/separators)
                text = text.replace(link, "", 1)
                # Clean up leftover separators
                text = re.sub(r'[|•·]\s*$', '', text, flags=re.MULTILINE)
            else:
                seen.add(url)

    return text


# ─── HTML-стилизация поста канала ────────────────────────────────────────────

# Структурные лейблы, которые выделяем жирным (текст уже HTML-escaped)
_BOLD_LABELS = [
    "Вариант 1", "Вариант 2", "Вариант А", "Вариант Б",
    "Миф:", "Правда:", "Откуда миф", "Почему так думают",
    "Плюсы:", "Минусы:", "Вывод:", "Совет:", "Важно:",
    "Мой фаворит", "Мой выбор", "Фаворит",
    "История из проекта", "Что было:", "Что сделали:", "Что получилось:",
    "До:", "После:", "Итог:", "Практика показывает",
    "Ошибка:", "Решение:", "Лайфхак:", "Правило:", "Запомните:",
    "Совет дизайнера:", "Вопрос:", "Ответ:",
    "Уход:", "Хранение:", "Срок:", "Бюджет:",
    "Материал:", "Материалы:", "Фурнитура:",
    # Раунд 7: расширение типографики
    "Пример:", "Результат:", "Альтернатива:", "Проверьте:",
    "Обратите внимание:", "Опыт показывает:", "Антипример:",
    "Заметка:", "Факт:", "Тренд:", "Рекомендация:", "Важно знать:",
    # Раунд 8: коммерческие и бытовые лейблы
    "Цена:", "Гарантия:", "Сроки:", "Кстати:", "Замер:", "Опыт клиента:",
    # Раунд 11: идеи и фавориты
    "Идея:", "Фаворит:",
]

_BOLD_LINE_PREFIXES = ("🅰", "🅱", "🅰️", "🅱️")

# «Шаг 1», «Шаг 2» — жирным (регэкспом, т.к. номер переменный)
_STEP_PAT = re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9])(Шаг \d{1,2})(?![0-9])")

# Нумерованный список в начале строки: «1. » / «2) » → жирный маркер
_NUM_PAT = re.compile(r"^(\s*)(\d{1,2})[.)]\s+")

# Варианты ответа «А) », «Б) » в постах-вопросах → жирная буква
_LETTER_PAT = re.compile(r"^(\s*)([АБВГД])[.)]\s+")

# Максимальная длина строки-хука, которую выделяем жирным целиком
_HOOK_MAX_LEN = 120


def _is_hook_line(stripped: str) -> bool:
    """Хук-вопрос (первая строка) — жирным целиком.

    Условия: короткая строка, содержит «?» (хук-вопрос из промпта),
    не является структурной лейбл-строкой («…:» в конце).
    """
    return (
        bool(stripped)
        and len(stripped) <= _HOOK_MAX_LEN
        and "?" in stripped
        and not stripped.endswith(":")
    )


# ─── Раунд 9: разбивка «слипшихся» структурных блоков ───────────────────────
# AI нередко пишет «Плюсы: … . Минусы: …» одной строкой или весь чек-лист
# «✅ … ✅ …» без переносов — теряется сканируемость. Разбиваем на строки.

# Лейблы, с которых начинается новый структурный блок (длинные — раньше
# коротких, чтобы «Совет дизайнера:» не разрезался на «Совет:»).
_SPLIT_LABELS = [
    "Что получилось:", "Что сделали:", "Что было:",
    "Правда:", "Миф:", "Откуда миф", "Почему так думают",
    "Плюсы:", "Минусы:", "Вывод:", "Итог:", "Мой фаворит", "Мой выбор",
    "Совет дизайнера:", "Совет:", "Решение:", "Ошибка:", "Лайфхак:",
    "Правило:", "Запомните:", "Результат:", "Пример:", "Антипример:",
    "Рекомендация:", "Заметка:", "Ответ:", "Важно:", "Факт:",
    # Раунд 10: блоки из дайджеста/ухода/коммерции — тоже слипаются в одну строку
    "История из проекта", "Вопрос:", "Тренд:",
    "Цена:", "Гарантия:", "Сроки:",
    "Уход:", "Хранение:", "Бюджет:",
    "Практика показывает", "Опыт показывает",
    "Обратите внимание:", "Важно знать:",
    # Раунд 11
    "Идея:", "Фаворит:",
]

# Маркеры списков: если в одной строке их 2+ — чек-лист слипся.
_LIST_MARKERS = ("✅", "❌", "📌", "🔹")


def _split_marker_line(line: str) -> str:
    """Чек-лист слипся в одну строку: каждый маркер (кроме первого) — с новой строки."""
    for m in _LIST_MARKERS:
        if line.count(m) >= 2:
            idxs = [mm.start() for mm in re.finditer(re.escape(m), line) if mm.start() > 0]
            for i in reversed(idxs):
                if i > 0 and line[i - 1] == " ":
                    line = line[:i - 1].rstrip() + "\n" + line[i:]
    return line


def _split_merged_labels(text: str) -> str:
    """Разбивает строки, где несколько структурных блоков слиплись в прозу.

    1. «Плюсы: a. Минусы: b» → «Плюсы: a» + перенос + «Минусы: b»
       (лейбл после конца предложения/запятой начинает новый блок).
    2. «🅰 Вариант 1 … 🅱 Вариант 2» в одной строке → каждый с новой строки.
    3. Чек-лист «✅ … ✅ …» одной строкой → пункт на строку.
    """
    if not text:
        return text
    for lbl in _SPLIT_LABELS:
        text = re.sub(
            # Раунд 10: пробел НЕ сохраняем в группе — раньше получался
            # хвостовой пробел перед \n ("эстетика. \nМинусы:"), теперь "эстетика.\nМинусы:"
            r"([,.!?;…])\s+(" + re.escape(lbl) + r")(?![0-9А-Яа-яЁё])",
            r"\1\n\2",
            text,
        )
    # 🅰/🅱 посреди строки — только если дальше идёт «Вариант» (наш формат compare)
    text = re.sub(r"(?<=[^\n\s])\s+(?=[🅰🅱][️]?\s*Вариант)", "\n", text)
    # Чек-листные маркеры — построчно
    text = "\n".join(_split_marker_line(ln) for ln in text.split("\n"))
    return text


# Раунд 10: дедуп хештегов — AI иногда пишет «#кухни … #Кухни», а система
# может добавить тег, который уже есть в тексте. Первый вхождение сохраняем.
_HTAG_PAT = re.compile(r"#[\wа-яё]+", flags=re.IGNORECASE)


def _dedupe_hashtags(text: str) -> str:
    if not text or "#" not in text:
        return text
    seen: set = set()
    out, last = [], 0
    for m in _HTAG_PAT.finditer(text):
        key = m.group(0).lower()
        out.append(text[last:m.start()])
        if key in seen:
            # Дубль: убираем вместе с одним пробелом перед ним (если есть)
            if out and out[-1].endswith(" "):
                out[-1] = out[-1][:-1]
        else:
            seen.add(key)
            out.append(m.group(0))
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def stylize_post_html(text: str) -> str:
    """Детерминированная HTML-стилизация поста канала (текст уже escaped, без тегов).

    1. Хук (первая строка до 120 знаков) — жирным целиком
    2. Жирные структурные лейблы: «Плюсы:», «Миф:», «Вариант 1:» → <b>…</b>
       (все вхождения в строке, только на границе слова)
    3. Строки-варианты (🅰/🅱) — вся строка жирным
    4. «Шаг N», нумерованные списки «1.», варианты «А)» — жирные маркеры
    5. Цены: «95 000руб» / «95 000 ₽» → «95 000 ₽»
    6. Разделитель «· · ·» перед блоком хештегов
    7. Дефис-маркеры в начале строки → «• »
    8. Раунд 10: гигиена пробелов (хвостовые перед \n), дедуп хештегов
    Возвращает текст с тегами <b> (Telegram parse_mode=HTML).
    """
    if not text:
        return text

    # Раунд 10: повторяющиеся хештеги (в т.ч. в разных регистрах) — оставляем первый
    text = _dedupe_hashtags(text)

    # Раунд 9: слипшиеся структурные блоки → каждый на своей строке
    text = _split_merged_labels(text)

    lines = text.split("\n")
    out = []
    hook_done = False
    # Регэкспы лейблов: граница слова слева + необязательное «:» + НЕ буква/цифра
    # справа («Вариант 12» не превращается в «Вариант 1»+«2», «Фаворит» не ловится
    # внутри «фаворитом»; при этом «Вариант 1:» / «Фаворит» в конце строки — ловятся)
    label_patterns = [
        (re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9])(" + re.escape(lbl) + r":?)(?![0-9А-Яа-яЁё])"), lbl)
        for lbl in _BOLD_LABELS
    ]
    for line in lines:
        stripped = line.strip()
        # Хук — первая содержательная строка-вопрос: жирным целиком
        if not hook_done and _is_hook_line(stripped) \
                and not stripped.startswith(_BOLD_LINE_PREFIXES):
            indent = line[:len(line) - len(line.lstrip())]
            out.append(f"{indent}<b>{stripped}</b>")
            hook_done = True
            continue
        if stripped:
            hook_done = True
        # Строки-варианты сравнения — целиком жирным
        if stripped.startswith(_BOLD_LINE_PREFIXES) and len(stripped) > 2:
            indent = line[:len(line) - len(line.lstrip())]
            out.append(f"{indent}<b>{stripped}</b>")
            continue
        # Жирные лейблы (все вхождения в строке)
        styled = line
        for pat, _lbl in label_patterns:
            styled = pat.sub(r"<b>\1</b>", styled)
        # «Шаг 1» жирным
        styled = _STEP_PAT.sub(r"<b>\1</b>", styled)
        # Маркеры списков
        m = _NUM_PAT.match(styled)
        if m:
            styled = _NUM_PAT.sub(lambda mm: f"{mm.group(1)}<b>{mm.group(2)}.</b> ", styled)
        else:
            m2 = _LETTER_PAT.match(styled)
            if m2:
                styled = _LETTER_PAT.sub(lambda mm: f"{mm.group(1)}<b>{mm.group(2)})</b> ", styled)
        # Дефис-маркер списка → bullet
        if styled.lstrip().startswith("- "):
            indent = styled[:len(styled) - len(styled.lstrip())]
            styled = indent + "• " + styled.lstrip()[2:]
        out.append(styled)

    result = "\n".join(out)

    # Цены к единому виду: «95 000руб», «95 000 руб.», «95 000₽» → «95 000 ₽»
    result = re.sub(r"(\d)\s?(?:руб\.?|₽)", r"\1 ₽", result)

    # Раунд 9: цены к «журнальному» виду — разряды + неразрывные пробелы.
    # «95000 ₽» / «173000 руб» → «95 000 ₽» (nbsp), «95 000 ₽» → nbsp внутри числа.
    # Только числа непосредственно перед ₽ (после нормализации руб→₽ выше):
    # годы и прочие числа без валюты не трогаются.
    def _fmt_price(m):
        num = m.group(1).replace(" ", "\u00A0")
        num = re.sub(r"\B(?=(?:\d{3})+(?!\d))", "\u00A0", num)
        return num + "\u00A0₽"

    result = re.sub(r"(\d[\d\u00A0 ]{0,15}\d)\s?₽", _fmt_price, result)

    # Раунд 10: гигиена — хвостовые пробелы/табы перед переносом строки
    result = re.sub(r"[ \t]+\n", "\n", result)

    # Раунд 10: хештеги, прилипшие к концу текстовой строки («… от 95 000 ₽. #кухни»),
    # переезжают на свою строку — тогда разделитель «· · ·» встаёт перед ними.
    result = _detach_trailing_hashtags(result)

    # Разделитель перед блоком хештегов (строка из одних #тегов)
    result = re.sub(r"\n\n(#[\wа-яё])", r"\n\n· · ·\n\1", result, flags=re.IGNORECASE)
    return result


def _detach_trailing_hashtags(text: str) -> str:
    """Если последняя строка — «текст … текст #тег1 #тег2», хештеги уходят вниз."""
    if not text or "#" not in text:
        return text
    lines = text.rstrip().split("\n")
    if not lines:
        return text
    last = lines[-1]
    if last.lstrip().startswith("#"):
        return text  # уже отдельная строка хештегов
    m = re.match(
        r"^(.+?)[ \t]+((?:#[\wа-яё]+)(?:[ \t]+#[\wа-яё]+)*)[ \t]*$",
        last, flags=re.IGNORECASE,
    )
    if m:
        # \n\n (пустая строка) — чтобы сработал разделитель «· · ·»
        lines[-1] = m.group(1).rstrip() + "\n\n" + m.group(2)
        return "\n".join(lines)
    return text
