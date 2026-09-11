"""AI-визуалы для канала — генерация мебельных изображений через Pollinations Image API.

Зачем: AI-fallback темы и новости без фото выходили в канал «голым» текстом.
Теперь по теме поста генерируется фотореалистичный интерьер → пост с фото +
подписью (выглядит живее, выше вовлечённость).

API бесплатный и без ключа: https://image.pollinations.ai/prompt/{prompt}
Отказоустойчивость: ЛЮБАЯ ошибка → None → пост выходит текстом как раньше
(визуал — опция, а не точка отказа). Управляется флагом VISUALS_ENABLED.
"""
import logging
import random
from urllib.parse import quote

logger = logging.getLogger("dasha.visuals")

_IMAGE_URL = (
    "https://image.pollinations.ai/prompt/{prompt}"
    "?width=1280&height=854&nologo=true&model=flux&seed={seed}"
)

# Тема → готовый англ. промпт (flux лучше понимает английский).
# Ключи — основы слов (склонения «кухню/кухни» матчатся «кухн»).
_PROMPT_RULES = [
    (("кухн", "гарнитур", "столешниц", "фартук", "остров", "мойк"),
     "Modern custom kitchen interior, light wood facades and stone countertop, "
     "soft daylight from window, cozy dining corner, professional interior "
     "design photography, photorealistic, warm atmosphere, high detail"),
    (("шкаф-купе", "шкаф купе", "шкафы-купе", "двери-купе", "купе", "шкаф", "шкафы", "хранени", "стенк"),
     "Built-in sliding wardrobe with light wood doors and mirror in a modern "
     "apartment hallway, warm LED accent lighting, professional interior "
     "design photography, photorealistic, high detail"),
    (("гардеробн",),
     "Walk-in closet dressing room with light wooden shelves, drawers and "
     "organized clothes storage, warm LED strip lighting, professional "
     "interior design photography, photorealistic, high detail"),
    (("прихож", "коридор", "входн"),
     "Modern hallway furniture set, light wood corridor wardrobe with mirror "
     "and hooks, scandinavian style, soft natural light, professional "
     "interior design photography, photorealistic, high detail"),
    (("детск",),
     "Bright children room with light wooden wardrobe and desk, scandinavian "
     "style, soft daylight, professional interior design photography, "
     "photorealistic, cozy, high detail"),
    (("ванн", "влаг"),
     "Modern bathroom furniture, white moisture resistant vanity cabinet "
     "with wooden countertop, clean minimal style, professional interior "
     "design photography, photorealistic, high detail"),
    (("лдсп", "мдф", "массив", "материал", "фасад", "кромк", "эмаль", "плёнк", "пленк", "шипон"),
     "Close-up of premium wooden furniture facades and material samples on a "
     "workshop table, wood texture detail, soft studio light, professional "
     "product photography, photorealistic, high detail"),
    (("фурнитур", "петл", "направляющ", "доводчик", "ручк", "гола", "push", "карго", "бутылочниц"),
     "Macro detail of modern cabinet hardware, soft-close hinge and "
     "handle-less aluminum profile grip on wooden facade, workshop light, "
     "professional product photography, photorealistic, high detail"),
    (("лофт",),
     "Loft style kitchen interior, dark furniture facades with brick wall "
     "accent, warm pendant lights, professional interior design photography, "
     "photorealistic, moody atmosphere, high detail"),
    (("сканд",),
     "Scandinavian style living room with light wooden cabinet furniture, "
     "white walls, cozy textiles, bright natural light, professional "
     "interior design photography, photorealistic, airy atmosphere"),
    (("минимализ",),
     "Minimalist kitchen and storage interior, handle-less matte facades, "
     "clean lines, hidden appliances, soft diffused light, professional "
     "interior design photography, photorealistic, high detail"),
    (("цвет", "син", "зелен", "графит", " бел", "сер"),
     "Modern kitchen with colored matte facades, deep graphite and warm wood "
     "accent, designer lighting, professional interior design photography, "
     "photorealistic, stylish atmosphere, high detail"),
]

_DEFAULT_PROMPT = (
    "Modern custom cabinet furniture in a bright apartment interior, light "
    "wood and matte facades, cozy warm atmosphere, professional interior "
    "design photography, photorealistic, high detail"
)

_NEG_SUFFIX = ", no text, no watermark, no people, no logo"


def build_image_prompt(topic: str) -> str:
    """Подбирает визуальный промпт по ключевым словам темы (первое совпадение).

    Тема паддится пробелами — ключи с ведущим пробелом (например « бел»)
    не срабатывают внутри слов («мебельный»).
    """
    t = f" {(topic or '').lower()} "
    for keys, prompt in _PROMPT_RULES:
        if any(k in t for k in keys):
            return prompt + _NEG_SUFFIX
    return _DEFAULT_PROMPT + _NEG_SUFFIX


async def generate_furniture_image(topic: str, timeout: float = 55.0):
    """Генерирует изображение по теме. Возвращает bytes (JPEG) или None.

    1 попытка + 1 retry с другим seed. Подпись-заглушки отсеиваются
    validate_image (магические байты) + минимальный размер 15 КБ.
    Вотермарк «pollinations.ai» (нижний правый угол) обрезается по нижней
    полосе — PIL есть в requirements; без PIL фото уходит как есть.
    """
    import httpx
    from bot.post_utils import validate_image

    prompt = build_image_prompt(topic)
    for attempt in (1, 2):
        seed = random.randint(1, 10**9)
        url = _IMAGE_URL.format(prompt=quote(prompt, safe=""), seed=seed)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
            size = len(r.content or b"")
            if r.status_code == 200 and size > 15_000 and validate_image(r.content):
                cleaned = _crop_watermark(r.content)
                logger.info(f"Visual generated: {size} bytes (cleaned {len(cleaned)}), seed={seed}, topic={topic[:40]}")
                return cleaned
            logger.warning(f"Visual attempt {attempt}: HTTP {r.status_code}, {size} bytes")
        except Exception as e:
            logger.warning(f"Visual attempt {attempt} failed: {type(e).__name__}: {e}")
    return None


def _crop_watermark(img_bytes: bytes):
    """Обрезает нижние 10% изображения (зона вотермарка pollinations).

    Любая ошибка → исходные байты (обрезка — косметика, не критерий успеха).
    """
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))
        w, h = img.size
        img = img.crop((0, 0, w, int(h * 0.90)))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=90)
        return out.getvalue()
    except Exception:
        return img_bytes
