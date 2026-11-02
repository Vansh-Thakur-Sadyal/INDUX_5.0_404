"""The ECAPA-TDNN speaker embedding, measured on real speech.

Why this file exists, in one number: **0.896**. That is what a different man,
five hertz away in pitch, scored against an enrolled farmer under the cepstral
description, over an accept threshold of 0.84. The wrong person was let in, and
that is the only failure this feature has - a farmer reported it as "it is
catching another person's voice also".

`speechbrain/spkrec-ecapa-voxceleb` is the model that fixes it: ECAPA-TDNN,
VoxCeleb 1 + 2, 7,205 speakers, 0.80% published EER. Exported to ONNX by
`ml/export_speaker_model.py`, which verifies the graph reproduces PyTorch
before writing it.

The clips are eight machine-synthesised sentences, two voices, four sentences
each - see `fixtures/voices/README.md` for what they are and what they are not.
They are REAL SPEECH, which the buzz and tone fixtures used elsewhere are not,
and that is the whole reason they are here: ECAPA cannot tell two buzzes apart,
so the other voice tests pin the cepstral description and this one does not.

Everything is skipped, not failed, when the 84 MB model file is absent. A
checkout without it is a valid installation that falls back to the cepstral
description, and `test_voice_registration.py` is what covers that.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core import speakermodel, voiceprint

VOICES = Path(__file__).parent / "fixtures" / "voices"
PHONE = "9876500909"

pytestmark = pytest.mark.skipif(
    not speakermodel.available(),
    reason="no speaker_ecapa.onnx; run ml/export_speaker_model.py")


def clip(name: str) -> bytes:
    return (VOICES / (name + ".wav")).read_bytes()


# Four sentences each. The enrolment uses three and the fourth is held back,
# because a clip that was enrolled from is not a test of anything.
A_ENROL = ["speaker-a-0", "speaker-a-1", "speaker-a-2"]
A_HELD_BACK = "speaker-a-3"
B_ENROL = ["speaker-b-0", "speaker-b-1", "speaker-b-2"]
B_HELD_BACK = "speaker-b-3"


@pytest.fixture(autouse=True)
def schema():
    voiceprint.ensure_schema()
    voiceprint.forget(PHONE)
    yield
    voiceprint.forget(PHONE)


@pytest.fixture
def registered():
    voiceprint.consent_given(PHONE, said="haan", language="hi")
    last = {}
    for name in A_ENROL:
        last = voiceprint.enrol(PHONE, clip(name), language="hi")
        assert last["ok"], last
    assert last["done"] is True, last
    return last


# ------------------------------------------------------------ the embedding
def test_the_embedding_is_192_numbers_on_the_unit_sphere():
    import numpy as np

    values, rate = voiceprint._samples(clip(A_HELD_BACK))
    vector = speakermodel.embed(values, rate)

    assert vector is not None
    assert len(vector) == speakermodel.EMBEDDING_DIMS == 192
    # Normalised at the source, so a cosine is a dot product and a stored
    # print cannot be half normalised.
    assert abs(float(np.linalg.norm(vector)) - 1.0) < 1e-6


def test_the_same_clip_twice_gives_the_same_embedding():
    """Not a tautology: a graph with a time-dependent op, or one left in
    training mode, quietly fails this and nothing else notices."""
    values, rate = voiceprint._samples(clip(A_HELD_BACK))
    first = speakermodel.embed(values, rate)
    second = speakermodel.embed(values, rate)

    assert speakermodel.compare(first, second) > 0.99999


def test_a_clip_too_short_to_describe_gets_no_embedding():
    values, rate = voiceprint._samples(clip(A_HELD_BACK))
    assert speakermodel.embed(values[:4000], rate) is None
    assert speakermodel.embed([], 16000) is None


def test_a_telephone_clip_is_resampled_rather_than_refused():
    """Twilio hands over 8 kHz. Refusing it would mean the phone line, which
    is where most of this is used, could not use it at all."""
    values, rate = voiceprint._samples(clip(A_HELD_BACK))
    eight = values[::2]

    at_eight = speakermodel.embed(eight, 8000)
    at_sixteen = speakermodel.embed(values, 16000)

    assert at_eight is not None
    # The same person through a narrower pipe: close, not identical, and well
    # above the same-speaker point.
    assert speakermodel.compare(at_eight, at_sixteen) > voiceprint.EMBEDDING_STRONG


# ------------------------------------------------- the separation it gives us
def test_two_speakers_are_separated_by_a_wide_measured_margin():
    """The measurement the thresholds are placed from, re-run every test run.

    If a future change to the export, the resampling or the normalisation
    narrows this gap, this is where it shows up - not in the field."""
    def vector(name):
        values, rate = voiceprint._samples(clip(name))
        return speakermodel.embed(values, rate)

    a = [vector(n) for n in A_ENROL + [A_HELD_BACK]]
    b = [vector(n) for n in B_ENROL + [B_HELD_BACK]]

    same = [speakermodel.compare(a[i], a[j])
            for i in range(len(a)) for j in range(i + 1, len(a))]
    same += [speakermodel.compare(b[i], b[j])
             for i in range(len(b)) for j in range(i + 1, len(b))]
    different = [speakermodel.compare(one, two) for one in a for two in b]

    assert min(same) > max(different), \
        "same-speaker and different-speaker scores overlap"
    # Measured at 0.774 against 0.276 - half a point of daylight. Asserted
    # loosely so a different ONNX runtime version is not a failure, and
    # tightly enough that losing the margin is.
    assert min(same) - max(different) > 0.35, (min(same), max(different))
    # And the thresholds sit inside the gap, which is the point of having them.
    assert max(different) < voiceprint.EMBEDDING_SAME < min(same)
    assert max(different) < voiceprint.EMBEDDING_STRONG < min(same)


# ---------------------------------------------------------- end to end
def test_three_clips_of_one_speaker_register(registered):
    status = voiceprint.status(PHONE)

    assert status["registered"] is True
    assert status["describer"] == "ecapa-tdnn"
    assert status["clips"] == 3
    assert status["agreement"] >= voiceprint.EMBEDDING_CONSISTENT
    assert status["quality"] in ("good", "fair")


def test_the_registered_speaker_is_recognised_on_a_sentence_they_never_enrolled(registered):
    verdict = voiceprint.verify(PHONE, clip(A_HELD_BACK))

    assert verdict["match"] == "strong", verdict
    assert verdict["basis"] == "ecapa-tdnn"
    assert verdict["score"] > voiceprint.EMBEDDING_STRONG


def test_a_different_speaker_is_refused(registered):
    """The failure that started all of this, on the new model."""
    for name in B_ENROL + [B_HELD_BACK]:
        verdict = voiceprint.verify(PHONE, clip(name))
        assert verdict["match"] == "no", (name, verdict)
        assert verdict["score"] < voiceprint.EMBEDDING_SAME, (name, verdict)


def test_the_gate_lets_the_farmer_through_and_stops_the_stranger(registered):
    assert voiceprint.gate(PHONE, clip(A_HELD_BACK))["allow"] is True

    refused = voiceprint.gate(PHONE, clip(B_HELD_BACK))
    assert refused["allow"] is False
    # And it says something, in both languages, rather than going silent.
    assert refused.get("spoken", {}).get("hi")
    assert refused.get("spoken", {}).get("en")


def test_three_clips_of_two_different_people_are_thrown_away():
    """What a farmer's registration must not become: two of them and one of
    somebody else, kept because the average looked acceptable."""
    voiceprint.consent_given(PHONE, said="haan", language="hi")
    voiceprint.enrol(PHONE, clip("speaker-a-0"), language="hi")
    voiceprint.enrol(PHONE, clip("speaker-a-1"), language="hi")
    result = voiceprint.enrol(PHONE, clip("speaker-b-0"), language="hi")

    assert result["ok"] is False
    assert result["reason"] == "inconsistent"
    assert result["clips"] == 0, "a mixed set was kept"
    assert voiceprint.status(PHONE)["registered"] is False


# ------------------------------------------------------- the two descriptions
def test_the_two_descriptions_are_never_compared_with_each_other():
    """A cepstral print and an embedding are both lists of floats, and
    comparing them would return a plausible-looking number for two things that
    mean nothing to each other."""
    values, rate = voiceprint._samples(clip(A_HELD_BACK))
    embedding = speakermodel.embed(values, rate)
    from app.core import voicefeatures
    import numpy as np

    cepstral = voicefeatures.describe(np.asarray(values, dtype=np.float64), rate)

    assert len(embedding) != len(cepstral)
    assert voiceprint.similarity(embedding, cepstral) == 0.0
    assert voiceprint.similarity(cepstral, embedding) == 0.0


def test_a_print_made_by_the_other_description_counts_as_unregistered(registered):
    """Which is what the version number is for. The farmer is asked to
    register again rather than being matched against numbers that mean
    something else."""
    from app.core.storage import cursor

    with cursor() as cur:
        cur.execute("UPDATE voices SET vector_version = ? WHERE phone = ?",
                    (voiceprint.CEPSTRAL_VERSION, PHONE))

    assert voiceprint.status(PHONE)["stale"] is True
    assert voiceprint.status(PHONE)["registered"] is False
    assert voiceprint.verify(PHONE, clip(A_HELD_BACK))["enrolled"] is False
    # Unknown means carry on. A model upgrade must not lock anybody out.
    assert voiceprint.gate(PHONE, clip(A_HELD_BACK))["allow"] is True


def test_the_current_version_says_which_description_is_in_use():
    assert voiceprint.vector_version() == voiceprint.EMBEDDING_VERSION
    assert voiceprint.describer() == "ecapa-tdnn"


def test_the_shipped_card_records_what_was_exported():
    card = speakermodel.card()

    assert card.get("model") == "speechbrain/spkrec-ecapa-voxceleb"
    assert card.get("embedding_dims") == 192
    # The export refuses to write the file unless the ONNX graph matches
    # PyTorch, and the card is where that check is recorded.
    assert card.get("onnx_vs_pytorch_cosine", 0) >= 0.9999
    # And it states its own limits, because a model card that does not is worse
    # than no card.
    assert "limitation" in card
