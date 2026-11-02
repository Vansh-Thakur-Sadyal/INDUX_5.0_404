"""Image screening: thresholding, calibration, graceful absence, agent wiring.

These tests never load the real ONNX file. They install a fake session with known
logits, which is what lets them assert the exact behaviour that protects a farmer:
below the confidence threshold the API must refuse to name a disease.
"""
from __future__ import annotations

import io
import json

import numpy as np
import pytest

from app.vision import classifier as vision


def _png_bytes(colour=(90, 140, 70), size=(320, 240)) -> bytes:
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeSession:
    """Returns fixed logits, so the maths around the model is what gets tested."""

    def __init__(self, logits):
        self.logits = np.array(logits, dtype=np.float32)
        self.calls = 0

    def run(self, _outputs, feeds):
        batch = feeds["image"].shape[0]
        self.calls += 1
        return [np.repeat(self.logits[None, :], batch, axis=0)]


CARD = {
    "name": "saarthi-leaf-disease",
    "architecture": "efficientnet_b0",
    "selected_run": "central",
    "classes": ["apple__scab", "apple__healthy", "tomato__late_blight"],
    "class_display": {"apple__scab": "Apple - scab", "apple__healthy": "Apple - healthy",
                      "tomato__late_blight": "Tomato - late blight"},
    "temperature": 1.0,
    "confidence_threshold": 0.70,
    "metrics": {"central": {"test_accuracy": 0.9, "test_macro_recall": 0.8}},
    "training_data": {"images": 39531},
    "limits": ["screening aid only"],
}


@pytest.fixture
def fake_model(monkeypatch):
    def install(logits, card=None, temperature=None):
        model_card = dict(CARD)
        if card:
            model_card.update(card)
        if temperature is not None:
            model_card["temperature"] = temperature
        session = _FakeSession(logits)
        monkeypatch.setattr(vision.classifier, "_session", session)
        monkeypatch.setattr(vision.classifier, "_card", model_card)
        monkeypatch.setattr(vision.classifier, "_loaded", True)
        return session
    return install


# ------------------------------------------------------------- unavailable
def test_missing_model_is_reported_not_faked(monkeypatch):
    monkeypatch.setattr(vision.classifier, "_session", None)
    monkeypatch.setattr(vision.classifier, "_loaded", True)
    monkeypatch.setattr(vision.classifier, "_load_error", "No trained model on disk.")
    result = vision.classifier.predict(_png_bytes())
    assert result.available is False
    assert result.top is None
    assert "No trained model" in result.reason


def test_empty_and_oversized_uploads_are_rejected(fake_model):
    fake_model([5.0, 0.0, 0.0])
    assert vision.classifier.predict(b"").available is False
    assert vision.classifier.predict(b"x" * (vision.MAX_BYTES + 1)).available is False


def test_unreadable_image_is_reported(fake_model):
    fake_model([5.0, 0.0, 0.0])
    result = vision.classifier.predict(b"this is not an image")
    assert result.available is False
    assert "Could not read" in result.reason


# ---------------------------------------------------------------- thresholds
def test_confident_prediction_is_returned_with_its_probability(fake_model):
    fake_model([8.0, 0.5, 0.0])                 # softmax ~0.999 on apple__scab
    result = vision.classifier.predict(_png_bytes())
    assert result.available and result.confident
    assert result.top.label == "apple__scab"
    assert result.top.display == "Apple - scab"
    assert result.top.crop == "apple" and result.top.condition == "scab"
    assert result.top.probability > 0.9
    assert result.top.note                      # farmer-facing note, not empty


def test_low_confidence_refuses_to_name_a_disease(fake_model):
    fake_model([1.0, 0.9, 0.8])                 # flat: top probability ~0.4
    result = vision.classifier.predict(_png_bytes())
    assert result.available is True
    assert result.confident is False
    assert result.top is None                   # the crucial part: no disease named
    assert "undecided" in result.reason.lower() or "below" in result.reason.lower()
    assert len(result.alternatives) >= 2        # what it was weighing is still shown


def test_threshold_comes_from_the_model_card(fake_model):
    fake_model([2.2, 0.0, 0.0], card={"confidence_threshold": 0.99})
    result = vision.classifier.predict(_png_bytes())
    assert result.threshold == 0.99
    assert result.confident is False            # same logits, stricter card


def test_temperature_scaling_is_applied(fake_model):
    fake_model([4.0, 1.0, 0.0], temperature=1.0)
    sharp = vision.classifier.predict(_png_bytes()).alternatives
    fake_model([4.0, 1.0, 0.0], temperature=4.0)
    soft = vision.classifier.predict(_png_bytes())
    # A higher temperature must flatten the distribution, not change the ranking.
    assert soft.alternatives[0].probability < sharp[0].probability or not soft.confident


def test_tta_runs_the_flip_and_can_be_switched_off(fake_model):
    session = fake_model([6.0, 0.0, 0.0])
    vision.classifier.predict(_png_bytes(), use_tta=True)
    assert session.calls == 1                   # one run call, batch of 2
    result = vision.classifier.predict(_png_bytes(), use_tta=False)
    assert result.confident is True


def test_probabilities_are_a_distribution(fake_model):
    fake_model([3.0, 2.0, 1.0])
    result = vision.classifier.predict(_png_bytes(), top_k=3)
    probs = [p.probability for p in (([result.top] if result.top else []) + result.alternatives)]
    assert abs(sum(probs) - 1.0) < 1e-4
    assert probs == sorted(probs, reverse=True)


def test_preprocessing_produces_the_expected_tensor(fake_model):
    fake_model([1.0, 0.0, 0.0])
    tensor = vision.classifier._preprocess(_png_bytes(size=(800, 600)))
    assert tensor.shape == (3, vision.IMAGE_SIZE, vision.IMAGE_SIZE)
    assert tensor.dtype == np.float32
    assert -3.0 < float(tensor.mean()) < 3.0    # normalised, not raw 0-255


# ----------------------------------------------------------------- endpoints
def test_status_endpoint_without_a_model(client, monkeypatch):
    monkeypatch.setattr(vision.classifier, "_session", None)
    monkeypatch.setattr(vision.classifier, "_loaded", True)
    monkeypatch.setattr(vision.classifier, "_load_error", "No trained model on disk.")
    body = client.get("/api/vision/status").json()
    assert body["available"] is False and body["reason"]


def test_status_endpoint_reports_the_model_card(client, fake_model):
    fake_model([1.0, 0.0, 0.0])
    body = client.get("/api/vision/status").json()
    assert body["available"] is True
    assert body["classes"] == 3
    assert body["confidence_threshold"] == 0.70
    assert body["test_accuracy"] == 0.9
    assert body["limits"]


def test_diagnose_runs_the_model_and_the_agents(client, fake_model):
    fake_model([9.0, 0.0, 0.0])
    response = client.post(
        "/api/vision/diagnose",
        files={"image": ("leaf.png", _png_bytes(), "image/png")},
        data={"symptoms": "brown spots on the lower leaves", "language": "en"},
    )
    assert response.status_code == 200
    body = response.json()
    screening = body["screening"]
    assert screening["confident"] is True
    assert screening["prediction"]["label"] == "apple__scab"

    agents = {r["agent_id"]: r for r in body["advisory"]["agent_results"]}
    assert 8 in agents, "the pest agent must run on an uploaded photo"
    pest = agents[8]
    assert pest["data"]["image_model_label"] == "apple__scab"
    assert any("image model" in f.lower() for f in pest["findings"])
    # The model is evidence, never the verdict. That is a claim about the CONTENT of
    # the result, so it is checked there: the photo label never becomes the single
    # answer, the caveat travels with it, and the agent is still asking what would
    # confirm it. The status is "ok" because the agent did answer - reporting a
    # finished result as still-incomplete read, to the farmer, as their photo having
    # been ignored.
    assert any("screening aid" in w.lower() or "not a diagnosis" in w.lower()
               for w in pest["warnings"])
    assert len(pest["data"]["candidate_causes"]) > 1, "one cause reads as a diagnosis"
    assert pest["questions"], "it must still ask what would confirm this"
    # A photo earns more confidence than a typed description, and 0.75 is what the
    # agent claims for one. What must not happen is it being presented as certain.
    assert pest["confidence"] <= 0.8


def test_diagnose_with_an_undecided_model_still_helps(client, fake_model):
    fake_model([1.0, 0.95, 0.9])
    body = client.post(
        "/api/vision/diagnose",
        files={"image": ("leaf.png", _png_bytes(), "image/png")},
        data={"symptoms": "yellow spots on leaves"},
    ).json()
    assert body["screening"]["confident"] is False
    assert body["screening"]["prediction"] is None
    agents = {r["agent_id"]: r for r in body["advisory"]["agent_results"]}
    assert 8 in agents
    assert agents[8]["data"]["image_model_label"] == ""


def test_diagnose_rejects_an_empty_upload(client, fake_model):
    fake_model([1.0, 0.0, 0.0])
    response = client.post("/api/vision/diagnose",
                           files={"image": ("empty.png", b"", "image/png")})
    assert response.status_code == 400


# ------------------------------------------------------- crop selection gate
# Class order in CARD: 0 apple__scab, 1 apple__healthy, 2 tomato__late_blight.

def test_selected_crop_that_matches_the_photo_is_screened_normally(fake_model):
    fake_model([9.0, 0.0, 0.0])                  # all mass on apple
    result = vision.classifier.predict(_png_bytes(), selected_crop="apple")
    assert result.verdict == "ok" and result.confident
    assert result.top.crop == "apple"
    assert result.crop_check["matches"] is True
    assert result.crop_check["crop_probability"] > 0.9


def test_wrong_crop_selection_is_caught_and_no_disease_is_named(fake_model):
    fake_model([9.0, 0.0, 0.0])                  # photo is apple...
    result = vision.classifier.predict(_png_bytes(), selected_crop="tomato")
    assert result.verdict == "crop_mismatch"
    assert result.confident is False
    assert result.top is None                    # nothing is claimed
    assert result.crop_check["matches"] is False
    assert result.crop_check["model_crop"] == "apple"
    assert "does not look like tomato" in result.reason
    assert result.alternatives                   # what it saw instead is still shown


def test_a_crop_the_model_never_learned_is_refused(fake_model):
    fake_model([5.0, 1.0, 0.0])
    result = vision.classifier.predict(_png_bytes(), selected_crop="wheat")
    assert result.verdict == "unknown_crop"
    assert result.top is None
    assert "not trained on wheat" in result.reason
    assert "apple" in result.reason              # tells the farmer what it does know


def test_prediction_is_ranked_inside_the_selected_crop(fake_model):
    # Highest overall is tomato, but the farmer said apple and the apple mass clears
    # the gate, so the answer must be an apple class - never a different plant.
    fake_model([2.0, 1.6, 2.1])
    result = vision.classifier.predict(_png_bytes(), selected_crop="apple")
    assert result.crop_check["matches"] is True
    reported = result.top or result.alternatives[0]
    assert reported.crop == "apple"


def test_crop_gate_constant_matches_the_calibration(fake_model):
    fake_model([1.0, 0.0, 0.0])
    assert vision.CROP_GATE == 0.50
    result = vision.classifier.predict(_png_bytes(), selected_crop="apple")
    assert result.crop_check["gate"] == 0.50


def test_diagnose_endpoint_reports_a_crop_mismatch(client, fake_model):
    fake_model([9.0, 0.0, 0.0])                  # apple photo, tomato selected
    body = client.post(
        "/api/vision/diagnose",
        files={"image": ("leaf.png", _png_bytes(), "image/png")},
        data={"crop": "tomato", "symptoms": "spots on leaves"},
    ).json()
    assert body["screening"]["verdict"] == "crop_mismatch"
    assert body["screening"]["prediction"] is None

    agents = {r["agent_id"]: r for r in body["advisory"]["agent_results"]}
    pest = agents[8]
    assert pest["data"]["image_model_verdict"] == "crop_mismatch"
    assert pest["data"]["image_model_label"] == ""          # nothing claimed
    assert any("does not match the crop" in f for f in pest["findings"])
    assert any("wrong crop was picked" in w for w in pest["warnings"])


def test_status_lists_the_crops_for_the_picker(client, fake_model):
    fake_model([1.0, 0.0, 0.0])
    body = client.get("/api/vision/status").json()
    assert body["crops"] == ["apple", "tomato"]
