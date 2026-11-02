"""One label space across three datasets that disagree about everything.

PlantDoc uses folder names like "Tomato Early blight leaf", Cassava uses integer
ids with a JSON map, Plant Pathology 2021 uses space-separated multi-labels on
apple leaves. They are merged into a single `crop__condition` vocabulary so one
model can serve a farmer who grows tomato, potato, maize or cassava.

Rules that matter:
* Nothing is mapped by guessing. Every raw label must appear in a table here, and
  `map_plantdoc` raises on anything unknown rather than silently inventing a class.
* Plant Pathology rows with several labels at once are dropped, not flattened: an
  image that is both scab and rust teaches a single-label classifier the wrong thing.
* "complex" (PP2021's "multiple or unclear disease") is dropped for the same reason -
  it is not an actionable answer to give a farmer.
"""
from __future__ import annotations

from typing import Dict, List, Optional

HEALTHY = "healthy"

# ---------------------------------------------------------------- Cassava
# From the competition's label_num_to_disease_map.json.
CASSAVA = {
    0: "cassava__bacterial_blight",
    1: "cassava__brown_streak_disease",
    2: "cassava__green_mottle",
    3: "cassava__mosaic_disease",
    4: "cassava__healthy",
}

# ------------------------------------------------- Plant Pathology 2021 (apple)
PLANT_PATHOLOGY = {
    "healthy": "apple__healthy",
    "scab": "apple__scab",
    "rust": "apple__rust",
    "frog_eye_leaf_spot": "apple__frog_eye_leaf_spot",
    "powdery_mildew": "apple__powdery_mildew",
    # "complex" is deliberately absent - see the module docstring.
}

# --------------------------------------------------------------- PlantDoc
# Folder names exactly as they appear in PlantDoc-Dataset-master/{train,test}.
PLANTDOC = {
    "Apple Scab Leaf": "apple__scab",
    "Apple leaf": "apple__healthy",
    "Apple rust leaf": "apple__rust",
    "Bell_pepper leaf": "bell_pepper__healthy",
    "Bell_pepper leaf spot": "bell_pepper__bacterial_spot",
    "Blueberry leaf": "blueberry__healthy",
    "Cherry leaf": "cherry__healthy",
    "Corn Gray leaf spot": "maize__gray_leaf_spot",
    "Corn leaf blight": "maize__leaf_blight",
    "Corn rust leaf": "maize__rust",
    "Peach leaf": "peach__healthy",
    "Potato leaf": "potato__healthy",
    "Potato leaf early blight": "potato__early_blight",
    "Potato leaf late blight": "potato__late_blight",
    "Raspberry leaf": "raspberry__healthy",
    "Soyabean leaf": "soybean__healthy",
    "Soybean leaf": "soybean__healthy",
    "Squash Powdery mildew leaf": "squash__powdery_mildew",
    "Strawberry leaf": "strawberry__healthy",
    "Tomato Early blight leaf": "tomato__early_blight",
    "Tomato Septoria leaf spot": "tomato__septoria_leaf_spot",
    "Tomato leaf": "tomato__healthy",
    "Tomato leaf bacterial spot": "tomato__bacterial_spot",
    "Tomato leaf late blight": "tomato__late_blight",
    "Tomato leaf mosaic virus": "tomato__mosaic_virus",
    "Tomato leaf yellow virus": "tomato__yellow_leaf_curl_virus",
    "Tomato mold leaf": "tomato__leaf_mold",
    "Tomato two spotted spider mites leaf": "tomato__spider_mites",
    "grape leaf": "grape__healthy",
    "grape leaf black rot": "grape__black_rot",
}


class UnknownLabel(KeyError):
    """Raised instead of guessing when a dataset contains a label we never mapped."""


def map_plantdoc(folder: str) -> str:
    key = folder.strip()
    if key not in PLANTDOC:
        raise UnknownLabel(
            "PlantDoc folder %r is not in the mapping table. Add it to ml/labels.py "
            "rather than letting it fall into the wrong class." % folder)
    return PLANTDOC[key]


def map_cassava(label_id: int) -> str:
    if int(label_id) not in CASSAVA:
        raise UnknownLabel("Cassava label id %r is unknown." % label_id)
    return CASSAVA[int(label_id)]


def map_plant_pathology(labels: str) -> Optional[str]:
    """Return a single class, or None when the row should be dropped."""
    parts = [p for p in str(labels).strip().split() if p]
    if len(parts) != 1:
        return None                       # multi-label: not usable for this model
    return PLANT_PATHOLOGY.get(parts[0])  # None for "complex"


def pretty(label: str) -> str:
    """'tomato__early_blight' -> 'Tomato - early blight' for the dashboard."""
    crop, _, condition = label.partition("__")
    return "%s - %s" % (crop.replace("_", " ").title(), condition.replace("_", " "))


def crop_of(label: str) -> str:
    return label.split("__")[0]


def condition_of(label: str) -> str:
    return label.split("__", 1)[1] if "__" in label else label


def is_healthy(label: str) -> bool:
    return condition_of(label) == HEALTHY


def all_known_labels() -> List[str]:
    labels = set(CASSAVA.values()) | set(PLANT_PATHOLOGY.values()) | set(PLANTDOC.values())
    return sorted(labels)


# Advisory text shown with a prediction. Deliberately short and non-prescriptive:
# the model screens an image, Agent 11 still refuses to name a product or a dose.
CONDITION_NOTES: Dict[str, str] = {
    "healthy": "No disease signs detected in this image. Keep scouting weekly.",
    "scab": "Scab is favoured by cool wet spells. Remove fallen leaves and improve airflow.",
    "rust": "Rust spreads in humid weather. Check the underside of leaves and nearby alternate hosts.",
    "frog_eye_leaf_spot": "Frog-eye leaf spot often follows wet periods and weak trees.",
    "powdery_mildew": "Powdery mildew likes warm days with cool humid nights and poor airflow.",
    "bacterial_blight": "Bacterial blight spreads with splashing water and infected cuttings.",
    "brown_streak_disease": "Brown streak is virus-borne, spread by whitefly and infected cuttings.",
    "green_mottle": "Green mottle is viral; use clean planting material.",
    "mosaic_disease": "Cassava mosaic is viral, spread by whitefly and infected stems.",
    "early_blight": "Early blight starts on older leaves; avoid overhead watering.",
    "late_blight": "Late blight moves fast in cool humid weather and needs urgent expert advice.",
    "bacterial_spot": "Bacterial spot spreads in warm wet weather and on wet foliage.",
    "septoria_leaf_spot": "Septoria starts low on the plant; remove affected lower leaves.",
    "leaf_mold": "Leaf mould builds up in humid, poorly ventilated conditions.",
    "mosaic_virus": "Mosaic viruses spread by handling and by sucking pests.",
    "yellow_leaf_curl_virus": "Yellow leaf curl is spread by whitefly; manage the vector.",
    "spider_mites": "Spider mites thrive in hot dry conditions; check leaf undersides.",
    "gray_leaf_spot": "Gray leaf spot is favoured by humid weather and heavy residue.",
    "leaf_blight": "Leaf blight develops in warm humid spells.",
    "black_rot": "Black rot overwinters in mummified fruit and cankers; sanitation matters.",
}


def note_for(label: str) -> str:
    return CONDITION_NOTES.get(condition_of(label),
                               "Confirm this screening result with a field inspection.")
