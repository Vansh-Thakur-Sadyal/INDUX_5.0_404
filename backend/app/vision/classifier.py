"""Leaf disease screening from a photo, with onnxruntime.

The model is an EfficientNet-B0 trained in ml/ on PlantDoc + Cassava + Plant
Pathology 2021 and exported to ONNX, so the server needs a 5 MB graph and a
~50 MB runtime instead of a 2 GB PyTorch install.

Two rules decide what the farmer is told:

* **Calibrated probabilities.** The raw network is over-confident, so the logits
  are divided by a temperature fitted on the validation split before softmax.
  The number shown is meant to be believable, not flattering.
* **A measured threshold.** Below the confidence cut-off chosen in
  ml/export_onnx.py (the lowest cut-off that still gets >=90% of shown
  predictions right), the API returns "not confident" instead of a class. For a
  farmer, silence is cheaper than a wrong disease name.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from ..core.config import PROJECT_ROOT

log = logging.getLogger("saarthi.vision")

MODEL_DIR = PROJECT_ROOT / "data" / "models"
ONNX_PATH = MODEL_DIR / "leaf_disease.onnx"
CARD_PATH = MODEL_DIR / "leaf_disease.json"

IMAGE_SIZE = 224
# 255, not 256: training evaluated with Resize(int(224 * 1.14)) = 255 then a centre
# crop. ml/validate_app_inference.py re-measures accuracy through this file and fails
# if the shipped path drifts from the training measurement, so keep them identical.
RESIZE_TO = int(IMAGE_SIZE * 1.14)
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)
MAX_BYTES = 8 * 1024 * 1024


@dataclass
class Prediction:
    label: str
    display: str
    crop: str
    condition: str
    probability: float
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label, "display": self.display, "crop": self.crop,
            "condition": self.condition, "probability": round(self.probability, 4),
            "note": self.note,
        }


# The farmer picks the crop before uploading, so a photo of a different plant can be
# caught instead of being forced into one of the 32 classes. The gate is the
# probability mass the model puts on the selected crop's classes; 0.50 sits in the
# middle of a very wide measured gap - see ml/calibrate_crop_gate.py:
#   right crop selected: 0.905 at the 5th percentile
#   wrong crop selected: 0.037 at the 99th percentile
# At 0.50 it keeps 98.8% of correct selections and catches 99.9% of wrong ones.
CROP_GATE = 0.50


@dataclass
class ScreeningResult:
    available: bool
    confident: bool = False
    reason: str = ""
    top: Optional[Prediction] = None
    alternatives: List[Prediction] = field(default_factory=list)
    threshold: float = 0.0
    inference_ms: int = 0
    model: Dict[str, Any] = field(default_factory=dict)
    # "ok" | "undecided" | "crop_mismatch" | "unknown_crop" | "error"
    verdict: str = "ok"
    crop_check: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "confident": self.confident,
            "verdict": self.verdict,
            "reason": self.reason,
            "prediction": self.top.as_dict() if self.top else None,
            "alternatives": [p.as_dict() for p in self.alternatives],
            "crop_check": self.crop_check,
            "threshold": self.threshold,
            "inference_ms": self.inference_ms,
            "model": self.model,
        }


class LeafClassifier:
    """Thread-safe lazy wrapper around the exported ONNX model."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session = None
        self._card: Dict[str, Any] = {}
        self._loaded = False
        self._load_error = ""

    # ----------------------------------------------------------- lifecycle
    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return self._session is not None
        with self._lock:
            if self._loaded:
                return self._session is not None
            self._loaded = True
            if not ONNX_PATH.exists() or not CARD_PATH.exists():
                self._load_error = (
                    "No trained model on disk. Train it with ml/train_central.py and "
                    "export it with ml/export_onnx.py.")
                log.info("Leaf model not present at %s", ONNX_PATH)
                return False
            try:
                import onnxruntime as ort
                self._card = json.loads(CARD_PATH.read_text(encoding="utf-8"))
                options = ort.SessionOptions()
                options.intra_op_num_threads = 2          # the VPS shares its CPU
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._session = ort.InferenceSession(
                    str(ONNX_PATH), options, providers=["CPUExecutionProvider"])
                log.info("Leaf disease model loaded: %d classes, threshold %.2f",
                         len(self._card.get("classes", [])),
                         self._card.get("confidence_threshold", 0))
            except Exception as exc:                      # noqa: BLE001
                self._session = None
                self._load_error = "Could not load the model: %s" % str(exc)[:160]
                log.warning("Leaf model failed to load: %s", exc)
            return self._session is not None

    @property
    def available(self) -> bool:
        return self._ensure_loaded()

    @property
    def threshold(self) -> float:
        self._ensure_loaded()
        return float(self._card.get("confidence_threshold", 0.75))

    def status(self) -> Dict[str, Any]:
        loaded = self._ensure_loaded()
        if not loaded:
            return {"available": False, "reason": self._load_error,
                    "expected_path": str(ONNX_PATH)}
        card = self._card
        metrics = card.get("metrics", {}).get(card.get("selected_run", ""), {})
        return {
            "available": True,
            "name": card.get("name"),
            "architecture": card.get("architecture"),
            "selected_run": card.get("selected_run"),
            "classes": len(card.get("classes", [])),
            "class_list": card.get("classes", []),
            "confidence_threshold": card.get("confidence_threshold"),
            "threshold_rationale": card.get("threshold_rationale", {}),
            "test_accuracy": metrics.get("test_accuracy"),
            "test_macro_recall": metrics.get("test_macro_recall"),
            "all_runs": card.get("metrics", {}),
            "training_data": card.get("training_data", {}),
            "exported_at": card.get("exported_at"),
            "limits": card.get("limits", []),
        }

    # ------------------------------------------------------- preprocessing
    def _preprocess(self, image_bytes: bytes) -> np.ndarray:
        from PIL import Image, ImageOps
        with Image.open(BytesIO(image_bytes)) as raw:
            image = ImageOps.exif_transpose(raw).convert("RGB")
            width, height = image.size
            scale = RESIZE_TO / min(width, height)
            image = image.resize((max(1, round(width * scale)), max(1, round(height * scale))),
                                 Image.BILINEAR)
            width, height = image.size
            left = (width - IMAGE_SIZE) // 2
            top = (height - IMAGE_SIZE) // 2
            image = image.crop((left, top, left + IMAGE_SIZE, top + IMAGE_SIZE))
            array = np.asarray(image, dtype=np.float32) / 255.0
        array = np.transpose(array, (2, 0, 1))
        array = (array - MEAN) / STD
        return array.astype(np.float32)

    # ------------------------------------------------------------ predict
    def probabilities(self, image_bytes: bytes, *, use_tta: bool = True):
        """Calibrated probability vector over all classes, or None if unavailable.

        Exposed so the crop gate can be calibrated against held-out data
        (ml/calibrate_crop_gate.py) using exactly the serving maths.
        """
        if not self._ensure_loaded():
            return None
        try:
            tensor = self._preprocess(image_bytes)
        except Exception:                                 # noqa: BLE001
            return None
        batch = np.stack([tensor, tensor[:, :, ::-1]]) if use_tta else tensor[None]
        logits = self._session.run(["logits"], {"image": np.ascontiguousarray(batch)})[0]
        logits = logits.mean(axis=0)
        temperature = float(self._card.get("temperature", 1.0)) or 1.0
        scaled = logits / temperature
        scaled = scaled - scaled.max()
        probabilities = np.exp(scaled)
        return probabilities / probabilities.sum()

    def crops(self) -> List[str]:
        """The crops the model can actually screen, for the UI's crop picker."""
        self._ensure_loaded()
        return sorted({c.split("__")[0] for c in self._card.get("classes", [])})

    def predict(self, image_bytes: bytes, *, top_k: int = 3, use_tta: bool = True,
                selected_crop: Optional[str] = None) -> ScreeningResult:
        """Screen one photo, optionally against the crop the farmer selected.

        With a crop selected the result can come back as `crop_mismatch`: the photo
        does not look like that crop, so no disease is reported. Without the gate the
        model would force any image into one of its 32 classes - a mango leaf, a hand,
        or a wheat field would all come back as some apple or cassava disease.
        """
        if not image_bytes:
            return ScreeningResult(False, reason="No image was received.", verdict="error")
        if len(image_bytes) > MAX_BYTES:
            return ScreeningResult(False, reason="Image is larger than 8 MB.", verdict="error")
        if not self._ensure_loaded():
            return ScreeningResult(False, reason=self._load_error, verdict="error")

        started = time.perf_counter()
        probabilities = self.probabilities(image_bytes, use_tta=use_tta)
        if probabilities is None:
            return ScreeningResult(False, verdict="error",
                                   reason="Could not read that image file.")

        classes: List[str] = self._card.get("classes", [])
        display: Dict[str, str] = self._card.get("class_display", {})
        known_crops = self.crops()
        threshold = self.threshold
        elapsed = int((time.perf_counter() - started) * 1000)

        def make(index: int) -> Prediction:
            label = classes[index]
            return Prediction(
                label=label,
                display=display.get(label, label),
                crop=label.split("__")[0],
                condition=label.split("__", 1)[-1],
                probability=float(probabilities[index]),
                note=_note_for(label),
            )

        ranked = [make(i) for i in np.argsort(-probabilities)[:max(top_k, 1)]]
        model_crop = ranked[0].crop
        crop = (selected_crop or "").strip().lower().replace(" ", "_") or None

        base = {
            "selected_crop": crop,
            "model_crop": model_crop,
            "known_crops": known_crops,
            "gate": CROP_GATE,
        }

        # ---- the farmer named a crop the model was never trained on ----
        if crop and crop not in known_crops:
            return ScreeningResult(
                available=True, confident=False, verdict="unknown_crop",
                reason=("This model was not trained on %s. It only knows %s, so it cannot "
                        "screen this photo - describe the symptom instead and the agents "
                        "will still help."
                        % (crop.replace("_", " "), ", ".join(c.replace("_", " ")
                                                             for c in known_crops))),
                alternatives=[], threshold=threshold, inference_ms=elapsed,
                crop_check=dict(base, crop_probability=0.0, matches=False),
                model=self._model_meta(len(classes)),
            )

        # ---- does the photo actually look like the selected crop? ----
        if crop:
            indices = [i for i, name in enumerate(classes) if name.startswith(crop + "__")]
            crop_probability = float(probabilities[indices].sum())
            base["crop_probability"] = round(crop_probability, 4)
            if crop_probability < CROP_GATE:
                return ScreeningResult(
                    available=True, confident=False, verdict="crop_mismatch",
                    reason=("This photo does not look like %s. The model puts only %.1f%% "
                            "of its confidence on %s classes and %.0f%% on %s. Either a "
                            "different crop was selected, or the photo is not a leaf this "
                            "model knows - no disease is being reported."
                            % (crop.replace("_", " "), crop_probability * 100,
                               crop.replace("_", " "), ranked[0].probability * 100,
                               model_crop.replace("_", " "))),
                    alternatives=ranked, threshold=threshold, inference_ms=elapsed,
                    crop_check=dict(base, matches=False),
                    model=self._model_meta(len(classes)),
                )
            # Rank within the selected crop, so the answer can never name a disease of
            # a different plant than the one the farmer said they are growing.
            best_in_crop = max(indices, key=lambda i: probabilities[i])
            top = make(best_in_crop)
            others = [p for p in ranked if p.label != top.label][:max(top_k - 1, 1)]
            confident = top.probability >= threshold
            return ScreeningResult(
                available=True, confident=confident,
                verdict="ok" if confident else "undecided",
                reason="" if confident else (
                    "The model is only %.0f%% sure of %s, below the %.0f%% threshold it "
                    "needs to be right about 9 times in 10. Treat it as undecided and send "
                    "a clearer close-up of one affected leaf."
                    % (top.probability * 100, top.display, threshold * 100)),
                top=top if confident else None,
                alternatives=([top] + others) if not confident else others,
                threshold=threshold, inference_ms=elapsed,
                crop_check=dict(base, matches=True),
                model=self._model_meta(len(classes)),
            )

        # ---- no crop selected: plain top-1 against the threshold ----
        confident = ranked[0].probability >= threshold
        return ScreeningResult(
            available=True, confident=confident,
            verdict="ok" if confident else "undecided",
            reason="" if confident else (
                "The model is only %.0f%% sure, below the %.0f%% threshold it needs to be "
                "right about 9 times in 10. Treat it as undecided, pick the crop and send "
                "a clearer close-up of one affected leaf."
                % (ranked[0].probability * 100, threshold * 100)),
            top=ranked[0] if confident else None,
            alternatives=ranked if not confident else ranked[1:],
            threshold=threshold, inference_ms=elapsed,
            crop_check=dict(base, crop_probability=None, matches=None),
            model=self._model_meta(len(classes)),
        )

    def _model_meta(self, class_count: int) -> Dict[str, Any]:
        run = self._card.get("selected_run", "")
        return {
            "name": self._card.get("name"),
            "selected_run": run,
            "test_accuracy": self._card.get("metrics", {}).get(run, {}).get("test_accuracy"),
            "classes": class_count,
        }


# Condition notes mirror ml/labels.py. Kept here so the backend does not import
# from the training package, which is not shipped to the server.
_CONDITION_NOTES = {
    "healthy": "No disease signs detected in this image. Keep scouting weekly.",
    "scab": "Scab is favoured by cool wet spells. Remove fallen leaves and improve airflow.",
    "rust": "Rust spreads in humid weather. Check leaf undersides and nearby alternate hosts.",
    "frog_eye_leaf_spot": "Frog-eye leaf spot often follows wet periods and weak trees.",
    "powdery_mildew": "Powdery mildew likes warm days with cool humid nights and poor airflow.",
    "bacterial_blight": "Bacterial blight spreads with splashing water and infected cuttings.",
    "brown_streak_disease": "Brown streak is virus-borne, spread by whitefly and cuttings.",
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


def _note_for(label: str) -> str:
    condition = label.split("__", 1)[-1]
    return _CONDITION_NOTES.get(condition,
                                "Confirm this screening result with a field inspection.")


classifier = LeafClassifier()
