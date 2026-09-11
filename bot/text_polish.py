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

    # 7. Non-breaking space before units (л.с., км/ч, мм, см, м, кг)
    # text = re.sub(r'\s+(л\.с\.|км/ч|мм|см|кг|м²|м2)', r'\u00A0\1', text)

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
]

_BOLD_LINE_PREFIXES = ("🅰", "🅱", "🅰️", "🅱️")


def stylize_post_html(text: str) -> str:
    """Детерминированная HTML-стилизация поста канала (текст уже escaped, без тегов).

    1. Жирные структурные лейблы: «Плюсы:», «Миф:», «Вариант 1:» → <b>…</b>
       (все вхождения в строке, только на границе слова)
    2. Строки-варианты (🅰/🅱) — вся строка жирным
    3. Разделитель «· · ·» перед блоком хештегов
    4. Дефис-маркеры в начале строки → «• »
    Возвращает текст с тегами <b> (Telegram parse_mode=HTML).
    """
    if not text:
        return text

    lines = text.split("\n")
    out = []
    # Регэкспы лейблов: граница слова слева + необязательное «:» справа
    label_patterns = [
        (re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9])(" + re.escape(lbl) + r":?)"), lbl)
        for lbl in _BOLD_LABELS
    ]
    for line in lines:
        stripped = line.strip()
        # Строки-варианты сравнения — целиком жирным
        if stripped.startswith(_BOLD_LINE_PREFIXES) and len(stripped) > 2:
            indent = line[:len(line) - len(line.lstrip())]
            out.append(f"{indent}<b>{stripped}</b>")
            continue
        # Жирные лейблы (все вхождения в строке)
        styled = line
        for pat, _lbl in label_patterns:
            styled = pat.sub(r"<b>\1</b>", styled)
        # Дефис-маркер списка → bullet
        if styled.lstrip().startswith("- "):
            indent = styled[:len(styled) - len(styled.lstrip())]
            styled = indent + "• " + styled.lstrip()[2:]
        out.append(styled)

    result = "\n".join(out)

    # Разделитель перед блоком хештегов (строка из одних #тегов)
    result = re.sub(r"\n\n(#[\wа-яё])", r"\n\n· · ·\n\1", result, flags=re.IGNORECASE)
    return result
