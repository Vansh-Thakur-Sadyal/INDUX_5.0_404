"""Crop reference table used by Agents 3, 4, 6, 7 and 10.

These are widely published general agronomic ranges for Indian conditions. They
are starting points for screening and scheduling, not a substitute for the
package of practices of the local state agricultural university. Every agent
that uses a value from here states that assumption in its output.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# duration_days, season, water_need_mm (season total), ph (min, best_min, best_max, max),
# ec_tolerance_ds_m (yield loss threshold), seed_rate_kg_ha, row_spacing_cm, depth_cm
CROP_PROFILES: Dict[str, Dict[str, Any]] = {
    "wheat": {
        "label": "Wheat",
        "seasons": ["rabi"],
        "duration_days": [110, 145],
        "water_need_mm": [400, 550],
        "ph": [5.5, 6.0, 7.5, 8.5],
        "ec_tolerance": 2.0,
        "seed_rate_kg_ha": [100, 125],
        "row_spacing_cm": [20, 22.5],
        "sowing_depth_cm": [5, 6],
        "sowing_window": "late October to late November in north India",
        "stages": [
            # Runs through emergence, not just the day of sowing. Crown root
            # initiation starts at 18, so days 8-17 used to belong to no stage at
            # all and a field sown a fortnight ago was asked for its sowing date
            # again - a question the farmer's own record had already answered.
            {"name": "sowing", "start": 0, "end": 17, "kc": 0.4,
             "note": "Sow into moist soil at uniform depth."},
            {"name": "crown_root_initiation", "start": 18, "end": 28, "kc": 0.7,
             "note": "The most critical irrigation of the whole crop."},
            {"name": "tillering", "start": 29, "end": 50, "kc": 0.9,
             "note": "Second irrigation and the main nitrogen top dress."},
            {"name": "jointing", "start": 51, "end": 70, "kc": 1.1,
             "note": "Rapid growth; high water demand."},
            {"name": "booting_heading", "start": 71, "end": 85, "kc": 1.15,
             "note": "Watch for yellow rust in cool humid weather."},
            {"name": "flowering", "start": 86, "end": 97, "kc": 1.15,
             "note": "Heat or moisture stress here cuts grain number directly."},
            {"name": "grain_filling", "start": 98, "end": 120, "kc": 0.9,
             "note": "Late stress shrivels grain; avoid water shortage."},
            {"name": "maturity", "start": 121, "end": 145, "kc": 0.4,
             "note": "Stop irrigation; prepare for harvest."},
        ],
    },
    "rice": {
        "label": "Rice (paddy)",
        # Kharif only here. Boro rice is a genuine rabi crop in the eastern states,
        # but it has its own calendar and this profile's sowing window is the kharif
        # one - so the card contradicted its own season tag.
        "seasons": ["kharif"],
        "duration_days": [110, 150],
        "water_need_mm": [900, 1400],
        "ph": [4.5, 5.5, 7.0, 8.0],
        "ec_tolerance": 3.0,
        "seed_rate_kg_ha": [20, 40],
        "row_spacing_cm": [20, 20],
        "sowing_depth_cm": [2, 3],
        "sowing_window": "nursery in June, transplanting through July",
        "stages": [
            {"name": "nursery", "start": 0, "end": 25, "kc": 1.05,
             "note": "Raise healthy seedlings for 21 to 30 days."},
            {"name": "transplanting", "start": 26, "end": 35, "kc": 1.05,
             "note": "Keep shallow standing water during recovery."},
            {"name": "tillering", "start": 36, "end": 60, "kc": 1.1,
             "note": "Alternate wetting and drying can start here."},
            {"name": "panicle_initiation", "start": 61, "end": 80, "kc": 1.2,
             "note": "Keep standing water; do not let the field dry."},
            {"name": "flowering", "start": 81, "end": 100, "kc": 1.2,
             "note": "The most water-sensitive stage."},
            {"name": "grain_filling", "start": 101, "end": 125, "kc": 1.0,
             "note": "Maintain moisture, then begin to drain."},
            {"name": "maturity", "start": 126, "end": 150, "kc": 0.7,
             "note": "Drain about 10 days before harvest."},
        ],
    },
    "mustard": {
        "label": "Mustard",
        "seasons": ["rabi"],
        "duration_days": [110, 140],
        "water_need_mm": [240, 400],
        "ph": [5.5, 6.0, 8.0, 9.0],
        "ec_tolerance": 3.5,
        "seed_rate_kg_ha": [4, 6],
        "row_spacing_cm": [30, 45],
        "sowing_depth_cm": [2, 3],
        "sowing_window": "October to early November",
        "stages": [
            {"name": "sowing", "start": 0, "end": 10, "kc": 0.4, "note": "Fine seedbed; shallow sowing."},
            {"name": "vegetative", "start": 11, "end": 40, "kc": 0.75, "note": "Thin to spacing; first weeding."},
            {"name": "pre_flowering", "start": 41, "end": 60, "kc": 1.0,
             "note": "The most important irrigation for mustard."},
            {"name": "flowering", "start": 61, "end": 85, "kc": 1.1,
             "note": "Scout for aphids in warm humid spells."},
            {"name": "siliqua_filling", "start": 86, "end": 110, "kc": 0.9, "note": "Pod filling."},
            {"name": "maturity", "start": 111, "end": 140, "kc": 0.4, "note": "Harvest before pods shatter."},
        ],
    },
    "chickpea": {
        "label": "Chickpea (gram)",
        "seasons": ["rabi"],
        "duration_days": [95, 130],
        "water_need_mm": [200, 350],
        "ph": [5.5, 6.0, 8.0, 8.8],
        "ec_tolerance": 1.5,
        "seed_rate_kg_ha": [60, 80],
        "row_spacing_cm": [30, 40],
        "sowing_depth_cm": [5, 8],
        "sowing_window": "late October to mid November",
        "stages": [
            {"name": "sowing", "start": 0, "end": 10, "kc": 0.4, "note": "Sow on residual moisture where possible."},
            {"name": "vegetative", "start": 11, "end": 45, "kc": 0.7, "note": "Low nitrogen need; nodules fix nitrogen."},
            {"name": "flowering", "start": 46, "end": 75, "kc": 1.0, "note": "Avoid excess water; it causes flower drop."},
            {"name": "pod_development", "start": 76, "end": 105, "kc": 0.9, "note": "Scout for pod borer."},
            {"name": "maturity", "start": 106, "end": 130, "kc": 0.35, "note": "Harvest when pods rattle."},
        ],
    },
    "tomato": {
        "label": "Tomato",
        # Kharif and summer. Not rabi: listing it there put it in front of a farmer
        # deciding what to sow in October.
        "seasons": ["kharif", "zaid"],
        "duration_days": [75, 120],
        "water_need_mm": [400, 600],
        "ph": [5.5, 6.0, 7.0, 7.8],
        "ec_tolerance": 2.5,
        "seed_rate_kg_ha": [0.3, 0.5],
        "row_spacing_cm": [60, 75],
        "sowing_depth_cm": [1, 2],
        "sowing_window": "nursery in May to June for kharif, January to February "
                         "for summer; transplant 25-30 days later",
        "stages": [
            {"name": "nursery", "start": 0, "end": 25, "kc": 0.5,
             "note": "Raise on a bed; drench against damping off."},
            {"name": "transplant_establishment", "start": 26, "end": 40, "kc": 0.6,
             "note": "Light frequent irrigation until the plants take."},
            {"name": "vegetative", "start": 41, "end": 60, "kc": 0.9,
             "note": "Stake or train; keep the canopy off the soil."},
            {"name": "flowering", "start": 61, "end": 80, "kc": 1.15,
             "note": "Water stress here drops flowers; irregular watering splits fruit."},
            {"name": "fruiting", "start": 81, "end": 105, "kc": 1.15,
             "note": "Steady moisture prevents blossom end rot."},
            {"name": "harvest", "start": 106, "end": 120, "kc": 0.8,
             "note": "Pick at breaker stage for a distant market."},
        ],
    },
    "onion": {
        "label": "Onion",
        "seasons": ["rabi"],
        "duration_days": [120, 150],
        "water_need_mm": [350, 550],
        "ph": [5.8, 6.0, 7.5, 8.0],
        "ec_tolerance": 1.2,
        "seed_rate_kg_ha": [8, 10],
        "row_spacing_cm": [15, 20],
        "sowing_depth_cm": [1, 2],
        "sowing_window": "nursery October to November, transplant after 6-8 weeks",
        "stages": [
            {"name": "nursery", "start": 0, "end": 45, "kc": 0.5,
             "note": "Thin seedlings; avoid waterlogging."},
            {"name": "transplant_establishment", "start": 46, "end": 65, "kc": 0.7,
             "note": "Shallow roots - little and often rather than heavy flooding."},
            {"name": "bulb_initiation", "start": 66, "end": 100, "kc": 1.05,
             "note": "The stage that sets bulb size; do not let it dry out."},
            {"name": "bulb_development", "start": 101, "end": 130, "kc": 1.0,
             "note": "Onion is salt sensitive; watch EC on irrigated land."},
            {"name": "maturity", "start": 131, "end": 150, "kc": 0.6,
             "note": "Stop irrigation when the necks fall; cure before storing."},
        ],
    },
    "lentil": {
        "label": "Lentil (masur)",
        "seasons": ["rabi"],
        "duration_days": [110, 140],
        "water_need_mm": [180, 300],
        "ph": [5.5, 6.0, 7.8, 8.5],
        "ec_tolerance": 1.5,
        "seed_rate_kg_ha": [35, 45],
        "row_spacing_cm": [25, 30],
        "sowing_depth_cm": [4, 6],
        "sowing_window": "late October to mid November",
        "stages": [
            {"name": "sowing", "start": 0, "end": 10, "kc": 0.4,
             "note": "Sow on residual moisture; it needs very little water."},
            {"name": "vegetative", "start": 11, "end": 50, "kc": 0.7,
             "note": "Nodules fix nitrogen; a heavy nitrogen dose only grows leaf."},
            {"name": "flowering", "start": 51, "end": 85, "kc": 1.0,
             "note": "One irrigation here if the soil is dry pays for itself."},
            {"name": "pod_filling", "start": 86, "end": 115, "kc": 0.8,
             "note": "Scout for rust and wilt in humid spells."},
            {"name": "maturity", "start": 116, "end": 140, "kc": 0.3,
             "note": "Harvest when the lower pods turn brown."},
        ],
    },
    "pea": {
        "label": "Field pea (matar)",
        "seasons": ["rabi"],
        "duration_days": [90, 130],
        "water_need_mm": [250, 400],
        "ph": [5.5, 6.0, 7.5, 8.2],
        "ec_tolerance": 1.6,
        "seed_rate_kg_ha": [70, 100],
        "row_spacing_cm": [30, 45],
        "sowing_depth_cm": [5, 7],
        "sowing_window": "late October to late November",
        "stages": [
            {"name": "sowing", "start": 0, "end": 10, "kc": 0.4,
             "note": "Cool weather at sowing gives the best stand."},
            {"name": "vegetative", "start": 11, "end": 45, "kc": 0.8,
             "note": "Keep weeds down in the first month."},
            {"name": "flowering", "start": 46, "end": 75, "kc": 1.1,
             "note": "Powdery mildew appears in warm dry spells."},
            {"name": "pod_filling", "start": 76, "end": 110, "kc": 0.9,
             "note": "Moisture now decides pod fill."},
            {"name": "maturity", "start": 111, "end": 130, "kc": 0.4,
             "note": "Pick green for the market, or let it dry for grain."},
        ],
    },
    "garlic": {
        "label": "Garlic",
        "seasons": ["rabi"],
        "duration_days": [140, 170],
        "water_need_mm": [350, 500],
        "ph": [6.0, 6.5, 7.5, 8.0],
        "ec_tolerance": 1.2,
        "seed_rate_kg_ha": [500, 600],
        "row_spacing_cm": [15, 20],
        "sowing_depth_cm": [4, 5],
        "sowing_window": "October to early November",
        "stages": [
            {"name": "planting", "start": 0, "end": 15, "kc": 0.5,
             "note": "Plant single cloves, flat end down, into moist soil."},
            {"name": "vegetative", "start": 16, "end": 70, "kc": 0.8,
             "note": "Shallow roots; water little and often."},
            {"name": "bulb_initiation", "start": 71, "end": 110, "kc": 1.05,
             "note": "The stage that sets clove number and bulb size."},
            {"name": "bulb_development", "start": 111, "end": 150, "kc": 0.9,
             "note": "Salt sensitive; watch EC on irrigated land."},
            {"name": "maturity", "start": 151, "end": 170, "kc": 0.5,
             "note": "Stop watering when the tops bend over; cure before storing."},
        ],
    },
    "coriander": {
        "label": "Coriander (dhania)",
        "seasons": ["rabi"],
        "duration_days": [90, 110],
        "water_need_mm": [300, 400],
        "ph": [6.0, 6.5, 7.5, 8.0],
        "ec_tolerance": 1.8,
        "seed_rate_kg_ha": [15, 20],
        "row_spacing_cm": [25, 30],
        "sowing_depth_cm": [2, 3],
        "sowing_window": "late October to mid November",
        "stages": [
            {"name": "sowing", "start": 0, "end": 12, "kc": 0.35,
             "note": "Split the seed before sowing so both halves germinate."},
            {"name": "vegetative", "start": 13, "end": 45, "kc": 0.7,
             "note": "Cut leaf twice here if you are selling greens as well."},
            {"name": "flowering", "start": 46, "end": 70, "kc": 1.0,
             "note": "Do not let it dry out now; the umbels set here."},
            {"name": "seed_setting", "start": 71, "end": 95, "kc": 0.8,
             "note": "Watch for powdery mildew in warm, still weather."},
            {"name": "maturity", "start": 96, "end": 110, "kc": 0.3,
             "note": "Harvest when the seed turns yellow-brown, before it shatters."},
        ],
    },
    "fenugreek": {
        "label": "Fenugreek (methi)",
        "seasons": ["rabi"],
        "duration_days": [100, 130],
        "water_need_mm": [250, 350],
        "ph": [6.0, 6.5, 7.5, 8.2],
        "ec_tolerance": 1.8,
        "seed_rate_kg_ha": [20, 25],
        "row_spacing_cm": [20, 25],
        "sowing_depth_cm": [3, 4],
        "sowing_window": "late October to November",
        "stages": [
            {"name": "sowing", "start": 0, "end": 10, "kc": 0.35,
             "note": "Soak the seed overnight for an even stand."},
            {"name": "vegetative", "start": 11, "end": 45, "kc": 0.7,
             "note": "It fixes its own nitrogen; a heavy urea dose only grows leaf."},
            {"name": "flowering", "start": 46, "end": 75, "kc": 1.0,
             "note": "Irrigate if the soil is dry, or pod set suffers."},
            {"name": "pod_filling", "start": 76, "end": 105, "kc": 0.8,
             "note": "Scout for downy mildew and aphids."},
            {"name": "maturity", "start": 106, "end": 130, "kc": 0.3,
             "note": "Harvest when the lower pods yellow; cut in the morning."},
        ],
    },
    "watermelon": {
        "label": "Watermelon (tarbooz)",
        "seasons": ["zaid"],
        "duration_days": [90, 110],
        "water_need_mm": [400, 600],
        "ph": [6.0, 6.5, 7.5, 8.0],
        "ec_tolerance": 1.5,
        "seed_rate_kg_ha": [3, 5],
        "row_spacing_cm": [150, 200],
        "sowing_depth_cm": [2, 3],
        "sowing_window": "January to March, on river beds or in pits",
        "stages": [
            {"name": "sowing", "start": 0, "end": 12, "kc": 0.4,
             "note": "Sow on the ridge, two or three seeds a pit."},
            {"name": "vine_growth", "start": 13, "end": 40, "kc": 0.75,
             "note": "Let the vines run; keep the basin weed free."},
            {"name": "flowering", "start": 41, "end": 60, "kc": 0.95,
             "note": "Bees do the pollination - do not spray in the morning."},
            {"name": "fruit_development", "start": 61, "end": 90, "kc": 1.0,
             "note": "The thirstiest stage; irregular water splits the fruit."},
            {"name": "maturity", "start": 91, "end": 110, "kc": 0.7,
             "note": "Cut back the water a week before picking for sweeter fruit."},
        ],
    },
    "muskmelon": {
        "label": "Muskmelon (kharbooja)",
        "seasons": ["zaid"],
        "duration_days": [80, 100],
        "water_need_mm": [350, 500],
        "ph": [6.0, 6.5, 7.5, 8.0],
        "ec_tolerance": 1.6,
        "seed_rate_kg_ha": [2, 3],
        "row_spacing_cm": [120, 180],
        "sowing_depth_cm": [2, 3],
        "sowing_window": "January to March",
        "stages": [
            {"name": "sowing", "start": 0, "end": 12, "kc": 0.4,
             "note": "Needs warm soil; sowing too early gives a patchy stand."},
            {"name": "vine_growth", "start": 13, "end": 38, "kc": 0.75,
             "note": "Train the vines away from the irrigation channel."},
            {"name": "flowering", "start": 39, "end": 55, "kc": 0.95,
             "note": "Avoid insecticide while the bees are working."},
            {"name": "fruit_development", "start": 56, "end": 82, "kc": 1.0,
             "note": "Keep moisture steady; swings here crack the rind."},
            {"name": "maturity", "start": 83, "end": 100, "kc": 0.65,
             "note": "Stop irrigating as the netting sets, or the fruit goes bland."},
        ],
    },
    "cucumber": {
        "label": "Cucumber (kheera)",
        "seasons": ["zaid"],
        "duration_days": [60, 80],
        "water_need_mm": [350, 500],
        "ph": [6.0, 6.5, 7.5, 8.0],
        "ec_tolerance": 1.3,
        "seed_rate_kg_ha": [2, 3],
        "row_spacing_cm": [100, 150],
        "sowing_depth_cm": [2, 3],
        "sowing_window": "February to March",
        "stages": [
            {"name": "sowing", "start": 0, "end": 10, "kc": 0.4,
             "note": "Shallow roots - keep the top of the soil moist."},
            {"name": "vine_growth", "start": 11, "end": 30, "kc": 0.8,
             "note": "A trellis keeps the fruit clean and straight."},
            {"name": "flowering", "start": 31, "end": 45, "kc": 0.95,
             "note": "Salt sensitive; watch EC if the water is hard."},
            {"name": "fruiting", "start": 46, "end": 70, "kc": 1.0,
             "note": "Pick every two days - a missed fruit stops the next one."},
            {"name": "maturity", "start": 71, "end": 80, "kc": 0.75,
             "note": "Yield tails off; clear the field for the next crop."},
        ],
    },
    "maize": {
        "label": "Maize",
        "seasons": ["kharif", "rabi"],
        "duration_days": [90, 120],
        "water_need_mm": [500, 700],
        "ph": [5.0, 5.8, 7.5, 8.2],
        "ec_tolerance": 1.8,
        "seed_rate_kg_ha": [18, 22],
        "row_spacing_cm": [60, 75],
        "sowing_depth_cm": [4, 5],
        "sowing_window": "June to July for kharif, October for rabi",
        "stages": [
            {"name": "sowing", "start": 0, "end": 7, "kc": 0.4, "note": "Ensure even emergence."},
            {"name": "vegetative", "start": 8, "end": 40, "kc": 0.8, "note": "First nitrogen top dress at knee height."},
            {"name": "tasseling_silking", "start": 41, "end": 65, "kc": 1.2,
             "note": "The most water-sensitive stage in maize."},
            {"name": "grain_filling", "start": 66, "end": 95, "kc": 1.0, "note": "Maintain moisture."},
            {"name": "maturity", "start": 96, "end": 120, "kc": 0.5, "note": "Black layer at the kernel base."},
        ],
    },
    "cotton": {
        "label": "Cotton",
        "seasons": ["kharif"],
        "duration_days": [150, 210],
        "water_need_mm": [700, 1000],
        "ph": [5.8, 6.5, 8.0, 8.5],
        "ec_tolerance": 5.0,
        "seed_rate_kg_ha": [1.5, 2.5],
        "row_spacing_cm": [90, 120],
        "sowing_depth_cm": [3, 5],
        "sowing_window": "April to June depending on zone",
        "stages": [
            {"name": "sowing", "start": 0, "end": 10, "kc": 0.4, "note": "Warm soil needed for germination."},
            {"name": "vegetative", "start": 11, "end": 55, "kc": 0.8, "note": "Scout for sucking pests."},
            {"name": "squaring", "start": 56, "end": 85, "kc": 1.1, "note": "Avoid water stress."},
            {"name": "flowering_boll", "start": 86, "end": 140, "kc": 1.15, "note": "Peak demand; bollworm scouting."},
            {"name": "maturity", "start": 141, "end": 210, "kc": 0.6, "note": "Picking in multiple rounds."},
        ],
    },
    "barley": {
        "label": "Barley",
        "seasons": ["rabi"],
        "duration_days": [100, 130],
        "water_need_mm": [280, 400],
        "ph": [5.5, 6.0, 8.5, 9.2],
        "ec_tolerance": 8.0,
        "seed_rate_kg_ha": [75, 100],
        "row_spacing_cm": [22, 25],
        "sowing_depth_cm": [4, 5],
        "sowing_window": "late October to November",
        "stages": [
            {"name": "sowing", "start": 0, "end": 7, "kc": 0.4, "note": "Tolerates poorer soils than wheat."},
            {"name": "tillering", "start": 8, "end": 45, "kc": 0.9, "note": "First irrigation if available."},
            {"name": "jointing", "start": 46, "end": 70, "kc": 1.05, "note": "Growth peak."},
            {"name": "grain_filling", "start": 71, "end": 105, "kc": 0.9, "note": "Avoid late stress."},
            {"name": "maturity", "start": 106, "end": 130, "kc": 0.35, "note": "Harvest when dry."},
        ],
    },
    "potato": {
        "label": "Potato",
        "seasons": ["rabi"],
        "duration_days": [75, 120],
        "water_need_mm": [450, 600],
        "ph": [4.8, 5.2, 6.5, 7.5],
        "ec_tolerance": 1.1,
        "seed_rate_kg_ha": [1800, 2500],
        "row_spacing_cm": [50, 60],
        "sowing_depth_cm": [5, 8],
        "sowing_window": "October to November",
        "stages": [
            {"name": "planting", "start": 0, "end": 12, "kc": 0.45, "note": "Use healthy sprouted tubers."},
            {"name": "vegetative", "start": 13, "end": 40, "kc": 0.8, "note": "Earthing up and weed control."},
            {"name": "tuber_initiation", "start": 41, "end": 60, "kc": 1.1,
             "note": "Keep soil moisture steady; avoid wide swings."},
            {"name": "tuber_bulking", "start": 61, "end": 90, "kc": 1.15,
             "note": "Watch for late blight in cool humid weather."},
            {"name": "maturity", "start": 91, "end": 120, "kc": 0.75, "note": "Dehaulm before harvest."},
        ],
    },
}

SEASON_MONTHS = {
    "kharif": [6, 7, 8, 9, 10],
    "rabi": [10, 11, 12, 1, 2, 3],
    "zaid": [3, 4, 5, 6],
}


def profile(crop: Optional[str]) -> Optional[Dict[str, Any]]:
    if not crop:
        return None
    key = str(crop).strip().lower()
    if key == "paddy":
        key = "rice"
    if key in ("gram", "chana"):
        key = "chickpea"
    return CROP_PROFILES.get(key)


def crops_for_season(season: str) -> List[str]:
    season = (season or "").lower()
    return [name for name, spec in CROP_PROFILES.items() if season in spec["seasons"]]


def stage_for_days(crop: str, days_after_sowing: int) -> Optional[Dict[str, Any]]:
    spec = profile(crop)
    if not spec:
        return None
    for stage in spec["stages"]:
        if stage["start"] <= days_after_sowing <= stage["end"]:
            return stage
    return spec["stages"][-1] if days_after_sowing > spec["stages"][-1]["end"] else None


def stage_by_name(crop: str, name: str) -> Optional[Dict[str, Any]]:
    spec = profile(crop)
    if not spec or not name:
        return None
    key = str(name).strip().lower()
    for stage in spec["stages"]:
        if key == stage["name"] or key in stage["name"]:
            return stage
    return None


def current_season(month: int) -> str:
    """Which season a month belongs to, and in an overlap the one it OPENS.

    October is in kharif's list and rabi's, March is in rabi's and zaid's, and
    dict order was deciding it - so in late October, when every farmer in north
    India is sowing rabi, the advisor called it kharif and offered kharif crops
    with a warning that it was the wrong season for them. The month a season
    STARTS in is the month its sowing advice is wanted.
    """
    for season, months in SEASON_MONTHS.items():
        if months and month == months[0]:
            return season
    for season, months in SEASON_MONTHS.items():
        if month in months:
            return season
    return "rabi"
