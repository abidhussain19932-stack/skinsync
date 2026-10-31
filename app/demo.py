"""DEMO_MODE: realistic stand-ins for YouCam responses so the prototype runs without an API key.

The shapes mirror the documented v2 `format=json` skin-analysis output (type / ui_score / raw_score /
mask_urls) and the skin-tone-analysis `results.color` object, then go through the same normaliser
as live responses.
"""
from __future__ import annotations

import hashlib
import random

from . import imaging
from .youcam import SD_SKIN_ACTIONS, normalize_skin_results

# Hand-tuned for the bundled sample selfie (visible cheek redness, chin breakouts, shiny T-zone,
# mild dark circles, visible pores).
SAMPLE_SCORES = {
    "wrinkle": (84, 78.2), "pore": (63, 41.5), "texture": (66, 48.9), "acne": (58, 36.4),
    "oiliness": (61, 44.0), "radiance": (69, 55.3), "eye_bag": (78, 71.0), "age_spot": (74, 66.8),
    "dark_circle_v2": (64, 47.7), "droopy_upper_eyelid": (86, 82.1), "droopy_lower_eyelid": (85, 80.4),
    "firmness": (88, 85.9), "moisture": (67, 50.2), "redness": (56, 34.8),
}


def skin_analysis(image: bytes, is_sample: bool) -> dict:
    if is_sample:
        scores = SAMPLE_SCORES
    else:
        seed = int(hashlib.sha256(image[:200_000]).hexdigest()[:8], 16)
        rnd = random.Random(seed)
        scores = {}
        for a in SD_SKIN_ACTIONS:
            ui = rnd.randint(55, 90)
            scores[a] = (ui, round(ui * 0.9 - rnd.uniform(0, 8), 1))
    raw = {"output": [{"type": k, "ui_score": v[0], "raw_score": v[1], "mask_urls": []} for k, v in scores.items()]}
    out = normalize_skin_results(raw)
    out["skin_age"] = 29 if is_sample else None
    return out


def skin_tone(image: bytes, is_sample: bool) -> dict:
    hexv = "#C69C7E" if is_sample else imaging.mean_skin_hex(image)
    return {"skin_color": hexv, "eye_color_name": "Dark Brown" if is_sample else None,
            "hair_color_name": "Black" if is_sample else None, "lip_color": None,
            "face_quality": {"has_face": True, "area": "good", "frontal": "good", "lighting": "good"}}
