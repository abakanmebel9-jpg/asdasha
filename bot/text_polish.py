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
