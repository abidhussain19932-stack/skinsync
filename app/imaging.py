"""Image prep: fix orientation, convert to JPEG and fit each YouCam feature's documented size limits."""
from __future__ import annotations

import io

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

# Documented limits:
#  skin-analysis SD: long side <= 4096, short side >= 480, < 10 MB
#  makeup-vto:       width < 1920, height < 1080, face width >= 100
#  skin-tone-analysis: same family as skin analysis


def load(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    return im.convert("RGB")


def to_jpeg(im: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def fit(im: Image.Image, max_w: int, max_h: int, min_short: int = 0) -> Image.Image:
    w, h = im.size
    scale = min(1.0, max_w / w, max_h / h)
    if min_short and min(w, h) * scale < min_short:
        scale = min_short / min(w, h)
    if abs(scale - 1.0) > 1e-3:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    return im


def prep_for_skin(data: bytes) -> bytes:
    return to_jpeg(fit(load(data), 2048, 2048, min_short=480))


def prep_for_vto(data: bytes) -> bytes:
    return to_jpeg(fit(load(data), 1900, 1070))


def demo_tryon(data: bytes, lip_hex: str | None, blush_hex: str | None) -> bytes:
    """Offline approximation used only in DEMO_MODE for user-uploaded photos: soft smoothing + warm glow.
    (Real makeup placement comes from YouCam makeup-vto when a key is configured.)"""
    im = load(data)
    smooth = im.filter(ImageFilter.GaussianBlur(2.2))
    im = Image.blend(im, smooth, 0.45)
    im = ImageEnhance.Brightness(im).enhance(1.05)
    im = ImageEnhance.Color(im).enhance(1.08)
    tint = Image.new("RGB", im.size, blush_hex or "#E8A08A")
    im = Image.blend(im, tint, 0.06)
    return to_jpeg(im)


def mean_skin_hex(data: bytes) -> str:
    """Rough cheek-region average colour (DEMO_MODE stand-in for YouCam skin-tone-analysis)."""
    im = load(data)
    w, h = im.size
    box = (int(w * 0.25), int(h * 0.42), int(w * 0.40), int(h * 0.55))
    r, g, b = im.crop(box).resize((1, 1), Image.BOX).getpixel((0, 0))
    return f"#{r:02X}{g:02X}{b:02X}"
