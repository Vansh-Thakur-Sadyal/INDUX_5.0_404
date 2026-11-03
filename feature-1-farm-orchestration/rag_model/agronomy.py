"""Rule-based crop matching using the ranges already shown by the NPK Advisor."""

from __future__ import annotations

import math
import re
from typing import Any


# These are the app's reference ranges (N/P/K in mg/kg), not universal soil thresholds.
CROP_REFERENCE_MATRIX: dict[str, dict[str, Any]] = {
    "Tomato": {"N": (80, 120), "P": (50, 80), "K": (100, 150), "pH": (6.0, 7.0), "seasons": ("Rabi", "Zaid")},
    "Wheat": {"N": (60, 100), "P": (40, 70), "K": (80, 120), "pH": (6.0, 7.5), "seasons": ("Rabi",)},
    "Rice (Paddy)": {"N": (60, 100), "P": (30, 60), "K": (60, 100), "pH": (5.5, 6.5), "seasons": ("Kharif",)},
    "Cotton": {"N": (70, 110), "P": (40, 70), "K": (90, 130), "pH": (6.0, 7.5), "seasons": ("Kharif",)},
    "Maize (Corn)": {"N": (90, 130), "P": (50, 80), "K": (90, 130), "pH": (5.8, 7.0), "seasons": ("Kharif", "Rabi")},
    "Sugarcane": {"N": (80, 130), "P": (40, 70), "K": (120, 180), "pH": (6.0, 7.5), "seasons": ("Kharif", "Rabi")},
    "Potato": {"N": (70, 100), "P": (50, 80), "K": (120, 160), "pH": (5.5, 6.5), "seasons": ("Rabi",)},
    "Onion": {"N": (60, 90), "P": (40, 70), "K": (80, 120), "pH": (6.0, 7.0), "seasons": ("Kharif", "Rabi")},
    "Green Chilli": {"N": (70, 100), "P": (40, 70), "K": (80, 120), "pH": (6.0, 7.0), "seasons": ("Kharif", "Rabi")},
    "Soybean": {"N": (20, 40), "P": (40, 60), "K": (60, 100), "pH": (6.0, 7.0), "seasons": ("Kharif",)},
    "Groundnut": {"N": (20, 40), "P": (40, 70), "K": (70, 110), "pH": (5.5, 7.0), "seasons": ("Kharif", "Zaid")},
    "Chickpea (Chana)": {"N": (15, 35), "P": (40, 60), "K": (50, 90), "pH": (6.0, 8.0), "seasons": ("Rabi",)},
    "Mustard": {"N": (60, 100), "P": (30, 60), "K": (50, 90), "pH": (6.0, 7.5), "seasons": ("Rabi",)},
    "Brinjal (Eggplant)": {"N": (70, 100), "P": (40, 70), "K": (80, 120), "pH": (5.5, 6.8), "seasons": ("Kharif", "Rabi", "Zaid")},
    "Capsicum": {"N": (70, 100), "P": (40, 70), "K": (90, 130), "pH": (6.0, 7.0), "seasons": ("Rabi",)},
}

SEASONAL_FALLBACK_CROPS = {
    "Kharif": ("Maize", "Soybean", "Cotton"),
    "Rabi": ("Wheat", "Mustard", "Chickpea (Chana)"),
    "Zaid": ("Groundnut", "Maize", "Tomato"),
}
SEASONAL_FALLBACK_VEGETABLES = {
    "Kharif": ("Green Chilli", "Brinjal (Eggplant)", "Onion"),
    "Rabi": ("Potato", "Onion", "Capsicum"),
    "Zaid": ("Tomato", "Green Chilli", "Brinjal (Eggplant)"),
}
VEGETABLE_CROPS = frozenset(
    ("Tomato", "Potato", "Onion", "Green Chilli", "Brinjal (Eggplant)", "Capsicum")
)

CROP_ALIASES = {
    "corn": "Maize (Corn)",
    "maize": "Maize (Corn)",
    "paddy": "Rice (Paddy)",
    "rice": "Rice (Paddy)",
    "chana": "Chickpea (Chana)",
    "chickpea": "Chickpea (Chana)",
    "chilli": "Green Chilli",
    "chili": "Green Chilli",
    "eggplant": "Brinjal (Eggplant)",
    "brinjal": "Brinjal (Eggplant)",
    "tomato": "Tomato",
    "potato": "Potato",
    "onion": "Onion",
    "wheat": "Wheat",
    "cotton": "Cotton",
    "sugarcane": "Sugarcane",
    "soybean": "Soybean",
    "groundnut": "Groundnut",
    "mustard": "Mustard",
    "capsicum": "Capsicum",
}


def season_for_month(month: int) -> str:
    """Return a broad India crop season; local sowing windows vary."""
    if month in (6, 7, 8, 9):
        return "Kharif"
    if month in (10, 11, 12, 1, 2, 3):
        return "Rabi"
    return "Zaid"


def _normalized_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()


def canonical_crop(name: str | None) -> str | None:
    if not name:
        return None
    normalized = _normalized_name(name)
    if normalized in CROP_ALIASES:
        return CROP_ALIASES[normalized]
    for crop in CROP_REFERENCE_MATRIX:
        if normalized == _normalized_name(crop):
            return crop
    return None


def exact_crop_reference(query: str, crop: str | None = None) -> dict[str, Any] | None:
    """Retrieve a known crop record only when its name is explicitly present."""
    candidates = [crop] if crop else list(CROP_ALIASES)
    query_normalized = _normalized_name(query)
    for candidate in candidates:
        canonical = canonical_crop(candidate)
        if not canonical:
            continue
        aliases = [alias for alias, target in CROP_ALIASES.items() if target == canonical]
        names = [canonical, *aliases]
        if any(
            re.search(rf"(?<![a-z0-9]){re.escape(_normalized_name(name))}(?![a-z0-9])", query_normalized)
            for name in names
        ):
            return {"crop": canonical, **CROP_REFERENCE_MATRIX[canonical], "similarity": 1.0}
    return None


def rank_crops(
    readings: dict[str, Any],
    current_crop: str | None,
    season: str | None,
    unit: str,
    limit: int = 5,
    vegetables_only: bool = False,
) -> list[dict[str, Any]]:
    """Rank app crops by distance from available readings; skip incompatible units."""
    if unit.casefold() not in {"mg/kg", "mgkg"}:
        return []

    canonical_current = canonical_crop(current_crop)
    usable: dict[str, float] = {}
    for key in ("nitrogen", "phosphorus", "potassium", "soil_ph"):
        value = readings.get(key)
        if isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            usable[key] = number
    if not usable:
        return []

    ranked = []
    for crop, reference in CROP_REFERENCE_MATRIX.items():
        if vegetables_only and crop not in VEGETABLE_CROPS:
            continue
        if crop == canonical_current:
            continue
        nutrient_score = 0.0
        evidence = []
        compared = 0
        for key, label in (
            ("nitrogen", "N"),
            ("phosphorus", "P"),
            ("potassium", "K"),
            ("soil_ph", "pH"),
        ):
            if key not in usable:
                continue
            lower, upper = reference[label]
            observed = usable[key]
            gap = max(lower - observed, observed - upper, 0)
            nutrient_score += gap / max(upper - lower, lower, 1)
            compared += 1
            if gap:
                direction = "below" if observed < lower else "above"
                unit_label = " mg/kg" if label in ("N", "P", "K") else ""
                evidence.append(f"{label} {gap:g}{unit_label} {direction} the app range")
            else:
                evidence.append(f"{label} within the app range")
        if not compared:
            continue
        score = nutrient_score / compared
        crop_seasons = reference["seasons"]
        season_match = bool(season and season in crop_seasons)
        if season and not season_match:
            score += 0.15
        ranked.append({
            "crop": crop,
            "score": score,
            "season_match": season_match,
            "reference_seasons": crop_seasons,
            "evidence": "; ".join(evidence),
        })
    ranked.sort(key=lambda result: (result["score"], not result["season_match"], result["crop"]))
    return ranked[:max(3, min(limit, 5))]


def seasonal_fallback(season: str, vegetables_only: bool = False) -> list[str]:
    if vegetables_only:
        return list(SEASONAL_FALLBACK_VEGETABLES.get(season, SEASONAL_FALLBACK_VEGETABLES["Rabi"]))
    return list(SEASONAL_FALLBACK_CROPS.get(season, SEASONAL_FALLBACK_CROPS["Rabi"]))