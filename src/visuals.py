"""Keyless visual sourcing: Wikimedia Commons first, then Picsum, then a generated card."""

from __future__ import annotations

import hashlib
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

USER_AGENT = "youtube-automation/1.0 (personal project)"
TIMEOUT = 30

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
PICSUM_URL = "https://picsum.photos/seed/{seed}/{width}/{height}"


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def fetch_wikimedia(query: str, out_path: Path, size: tuple[int, int]) -> bool:
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}",
        "gsrlimit": 8,
        "gsrnamespace": 6,
        "prop": "imageinfo",
        "iiprop": "url|size",
        "iiurlwidth": size[0] * 2,
    }
    try:
        response = _session().get(WIKIMEDIA_API, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        pages = (response.json().get("query") or {}).get("pages") or {}
    except (requests.RequestException, ValueError):
        return False

    # Search results come back as a dict; prefer the biggest sensible landscape image.
    candidates = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url")
        width, height = info.get("width", 0), info.get("height", 0)
        if not url or width < 800 or height == 0:
            continue
        ratio = width / height
        if not 0.6 <= ratio <= 2.6:
            continue
        candidates.append((width * height, url))
    if not candidates:
        return False

    candidates.sort(reverse=True)
    for _, url in candidates[:3]:
        try:
            data = _session().get(url, timeout=TIMEOUT).content
            out_path.write_bytes(data)
            with Image.open(out_path) as probe:
                probe.verify()
            return True
        except Exception:
            continue
    return False


def fetch_picsum(query: str, out_path: Path, size: tuple[int, int]) -> bool:
    seed = hashlib.sha1(query.encode("utf-8")).hexdigest()[:12]
    url = PICSUM_URL.format(seed=seed, width=size[0], height=size[1])
    try:
        response = _session().get(url, timeout=TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        out_path.write_bytes(response.content)
        with Image.open(out_path) as probe:
            probe.verify()
        return True
    except Exception:
        return False


def _load_font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_card(query: str, out_path: Path, size: tuple[int, int], cfg: dict) -> bool:
    """Last-resort visual: a deep gradient card with the scene's subject written on it."""
    width, height = size
    image = Image.new("RGB", size, cfg.get("card_color", "#101828"))
    draw = ImageDraw.Draw(image)

    for y in range(height):
        blend = y / max(height - 1, 1)
        draw.line([(0, y), (width, y)], fill=tuple(int(16 + 24 * blend) for _ in range(3)))

    accent = cfg.get("card_accent", "#3B82F6")
    draw.rectangle([0, height - 14, width, height], fill=accent)

    font = _load_font(int(height * 0.075))
    text = query.strip().title()
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= width * 0.78:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    line_height = int(height * 0.10)
    top = (height - len(lines) * line_height) // 2
    for index, line in enumerate(lines):
        text_width = draw.textlength(line, font=font)
        draw.text(
            ((width - text_width) / 2, top + index * line_height),
            line,
            font=font,
            fill="#F8FAFC",
        )

    image.save(out_path, "PNG")
    return True


def _fit(image_path: Path, size: tuple[int, int]) -> None:
    """Centre-crop to the exact target aspect and size so ffmpeg gets uniform input."""
    width, height = size
    with Image.open(image_path) as source:
        image = source.convert("RGB")
        scale = max(width / image.width, height / image.height)
        resized = image.resize(
            (max(width, int(image.width * scale)), max(height, int(image.height * scale))),
            Image.LANCZOS,
        )
        left = (resized.width - width) // 2
        top = (resized.height - height) // 2
        resized.crop((left, top, left + width, top + height)).save(image_path, "PNG")


def resolve(query: str, out_path: Path, cfg: dict) -> str:
    """Fetch a visual for the query and normalise it. Returns the source name used."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    width = cfg["video"]["width"]
    height = cfg["video"]["height"]
    size = (width, height)

    handlers = {
        "wikimedia": fetch_wikimedia,
        "picsum": fetch_picsum,
        "card": lambda q, o, s: make_card(q, o, s, cfg["visuals"]),
    }
    for source in cfg["visuals"]["sources"]:
        handler = handlers.get(source)
        if handler is None:
            continue
        if handler(query, out_path, size):
            _fit(out_path, size)
            return source

    # Nothing matched config; the card generator cannot fail.
    make_card(query, out_path, size, cfg["visuals"])
    _fit(out_path, size)
    return "card"
