"""Product catalog, concern metadata, makeup looks, and the YouCam makeup-vto effect builder."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .config import settings

CONCERN_LABELS = {
    "wrinkle": "Fine lines & wrinkles", "pore": "Visible pores", "texture": "Uneven texture",
    "acne": "Breakouts", "oiliness": "Oiliness", "radiance": "Radiance / dullness",
    "eye_bag": "Under-eye puffiness", "age_spot": "Dark spots", "dark_circle_v2": "Dark circles",
    "droopy_upper_eyelid": "Upper-lid laxity", "droopy_lower_eyelid": "Lower-lid laxity",
    "firmness": "Firmness", "moisture": "Hydration", "redness": "Redness",
}

# Makeup looks the agent can choose from. Colours come from the selected catalogue SKUs so the
# try-on shows the exact products the shopper can add to the bag.
LOOKS = {
    "fresh-dewy": {"name": "Fresh Dewy", "vibe": "Everyday, skin-first glow; light coverage that calms redness.",
                   "smooth": 45, "foundation_intensity": 45, "blush_intensity": 45, "lip_intensity": 60,
                   "lip_texture": "satin"},
    "soft-glam": {"name": "Soft Glam", "vibe": "Evening-ready: fuller coverage, defined lips, sculpted flush.",
                  "smooth": 55, "foundation_intensity": 65, "blush_intensity": 60, "lip_intensity": 80,
                  "lip_texture": "matte"},
    "office-matte": {"name": "Office Matte", "vibe": "Shine-controlled, polished and long-wearing for oily T-zones.",
                     "smooth": 40, "foundation_intensity": 55, "blush_intensity": 35, "lip_intensity": 65,
                     "lip_texture": "matte"},
}


@lru_cache
def load_catalog() -> dict[str, Any]:
    with open(settings.catalog_path, encoding="utf-8") as f:
        return json.load(f)


def products() -> list[dict[str, Any]]:
    return load_catalog()["products"]


def get_product(pid: str) -> dict[str, Any] | None:
    return next((p for p in products() if p["id"] == pid), None)


def search_products(concern: str | None = None, category: str | None = None, max_price: float | None = None,
                    fragrance_free: bool | None = None, skin_type: str | None = None, limit: int = 6) -> list[dict]:
    out = []
    for p in products():
        if category and p["category"] != category:
            continue
        if concern and concern not in p["concerns"]:
            continue
        if max_price is not None and p["price"] > max_price:
            continue
        if fragrance_free and not p.get("fragrance_free", True):
            continue
        if skin_type and skin_type not in p["skin_types"]:
            continue
        out.append(p)
    return [compact(p) for p in out[:limit]]


def compact(p: dict) -> dict:
    keys = ["id", "name", "brand", "category", "price", "concerns", "key_ingredients", "use", "shade_hex", "texture"]
    return {k: p[k] for k in keys if k in p}


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def match_foundation(skin_hex: str | None, n: int = 2) -> list[dict]:
    shades = [p for p in products() if p["category"] == "foundation"]
    if not skin_hex:
        return [compact(p) for p in shades[3:3 + n]]
    r, g, b = _hex_to_rgb(skin_hex)

    def dist(p):
        r2, g2, b2 = _hex_to_rgb(p["shade_hex"])
        # weighted RGB distance (perceptually-ish)
        return 2 * (r - r2) ** 2 + 4 * (g - g2) ** 2 + 3 * (b - b2) ** 2

    return [compact(p) for p in sorted(shades, key=dist)[:n]]


def build_effects(look_id: str, foundation: dict | None, lip: dict | None, blush: dict | None) -> list[dict]:
    """Translate a look + chosen SKUs into the YouCam makeup-vto `effects` array (schema v1.0)."""
    look = LOOKS.get(look_id, LOOKS["fresh-dewy"])
    effects: list[dict] = [{"category": "skin_smooth", "skinSmoothStrength": look["smooth"],
                            "skinSmoothColorIntensity": 40}]
    if foundation and foundation.get("shade_hex"):
        effects.append({"category": "foundation", "palettes": [{
            "color": foundation["shade_hex"], "colorIntensity": look["foundation_intensity"],
            "glowIntensity": 60 if look_id == "fresh-dewy" else 25, "coverageIntensity": look["foundation_intensity"]}]})
    if blush and blush.get("shade_hex"):
        effects.append({"category": "blush", "pattern": {"name": "1color1"}, "palettes": [{
            "color": blush["shade_hex"], "texture": "satin", "glowStrength": 40,
            "colorIntensity": look["blush_intensity"]}]})
    if lip and lip.get("shade_hex"):
        tex = lip.get("texture") or look["lip_texture"]
        pal: dict[str, Any] = {"color": lip["shade_hex"], "texture": tex, "colorIntensity": look["lip_intensity"]}
        if tex in {"gloss", "sheer", "shimmer", "holographic", "metallic"}:
            pal["gloss"] = 60
        if tex in {"gloss", "sheer", "shimmer"}:
            pal["transparencyIntensity"] = 40
        if tex in {"shimmer", "holographic", "metallic"}:
            pal.update({"shimmerColor": "#ffffff", "shimmerIntensity": 40, "shimmerDensity": 40, "shimmerSize": 40})
        effects.append({"category": "lip_color", "shape": {"name": "original"}, "style": {"type": "full"},
                        "palettes": [pal]})
    return effects
