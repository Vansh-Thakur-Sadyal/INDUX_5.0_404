"""Remembering a farmer's voice, and asking them first.

Two things are tested here and they matter for different reasons.

**Consent is structural.** A voiceprint is biometric data about a person, and
nothing in this system may store one without a recorded yes. That is enforced
inside ``enrol`` rather than at each caller, and the test that proves it is the
one that tries to enrol without asking.

**The matcher has to actually work.** The first version of it scored a woman's
voice at 0.928 against a man's enrolled print, with the "strong match"
threshold at 0.86 - it could not tell two people apart at all, because every
feature was positive so every cosine was near one. These tests are the
measurement that catches that happening again.

The voices here are synthesised: a pitch, some formants, a little noise. That
makes them cleanly separable in a way two real brothers on a bad line will not
be, so the numbers below are a floor on the failure, not a promise about the
field.
"""
from __future__ import annotations

import io
import struct
import wave

import pytest

from app.core import voicefeatures, voiceprint

RATE = 16000

# Three distinct speakers, by the two things that separate real ones: the pitch
# of the vocal folds and the resonances of the vocal tract.
RAMESH = {"f0": 118, "formants": [(620, 80, 1.0), (1150, 110, 0.6), (2500, 180, 0.25)]}
SUNITA = {"f0": 215, "formants": [(780, 90, 1.0), (1700, 130, 0.7), (2900, 200, 0.3)]}
OTHER = {"f0": 142, "formants": [(500, 70, 1.0), (1500, 120, 0.55), (2600, 170, 0.3)]}

PHONE = "9800000042"


def voice(f0, formants, seconds=2.0, seed=0, noise=0.01):
    """A vowel-like clip for one speaker, as 16-bit PCM WAV."""
    import numpy as np

    rng = np.random.default_rng(seed)
    t = np.arange(int(RATE * seconds)) / RATE
    wobble = (1 + 0.02 * np.sin(2 * np.pi * 4 * t)
              + 0.01 * rng.standard_normal(len(t)).cumsum() / max(len(t) ** 0.5, 1))
    phase = 2 * np.pi * np.cumsum(f0 * wobble) / RATE
    signal = sum(np.sin(n * phase) / n for n in range(1, 25))

    spectrum = np.fft.rfft(signal)
    freqs = np.fft.rfftfreq(len(signal), 1 / RATE)
    shape = np.zeros_like(freqs)
    for centre, width, amplitude in formants:
        shape += amplitude / (1 + ((freqs - centre) / width) ** 2)
    signal = np.fft.irfft(spectrum * (0.05 + shape), n=len(signal))
    signal += noise * rng.standard_normal(len(signal))
    signal = signal / (np.abs(signal).max() or 1) * 0.8

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(RATE)
        clip.writeframes(struct.pack("<%dh" % len(signal),
                                     *(int(v * 32000) for v in signal)))
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def cepstral(monkeypatch):
    """Pinned to the cepstral description, because these voices are vowels.

    One sustained vowel-like buzz is not speech. ECAPA-TDNN was trained on
    7,205 people saying sentences, and on a synthesised vowel it finds no
    difference worth reporting - so the separation these tests measure cannot
    be measured through it. They cover the fallback path, which is what an
    installation without the model file uses, and `test_speaker_model.py`
    covers the embedding path on real speech.
    """
    monkeypatch.setattr(voiceprint.speakermodel, "available", lambda: False)
    monkeypatch.setattr(voiceprint.speakermodel, "embed",
                        lambda values, rate: None)


@pytest.fixture
def enrolled(client):
    """Ramesh, having been asked and having agreed."""
    voiceprint.ensure_schema()
    voiceprint.forget(PHONE)
    voiceprint.consent_given(PHONE, said="haan", language="hi")
    for seed in range(3):
        result = voiceprint.enrol(PHONE, voice(seed=seed, **RAMESH), language="hi")
        assert result["ok"], result
    return PHONE


# ------------------------------------------------------------------- consent
def test_nothing_is_stored_without_a_recorded_yes():
    """The rule this whole module is arranged around."""
    voiceprint.ensure_schema()
    voiceprint.forget(PHONE)
    result = voiceprint.enrol(PHONE, voice(seed=1, **RAMESH))
    assert result["ok"] is False
    assert result["reason"] == "no_consent"
    assert not voiceprint.enrolled(PHONE)


def test_the_farmers_own_words_are_kept_with_the_consent():
    """"They said haan on this date" is evidence. A boolean is not."""
    voiceprint.ensure_schema()
    voiceprint.forget(PHONE)
    voiceprint.consent_given(PHONE, said="haan ji theek hai", language="hi")
    record = voiceprint.get(PHONE)
    assert record["consent"] is True
    assert record["consent_said"] == "haan ji theek hai"
    assert record["consent_at"]


def test_consenting_twice_does_not_wipe_the_voice(enrolled):
    before = voiceprint.get(enrolled)["clips"]
    voiceprint.consent_given(enrolled, said="haan", language="hi")
    assert voiceprint.get(enrolled)["clips"] == before


def test_forgetting_removes_everything(enrolled):
    assert voiceprint.forget(enrolled) is True
    assert voiceprint.get(enrolled) is None
    assert not voiceprint.enrolled(enrolled)


def test_no_audio_is_kept_only_numbers(enrolled):
    """A stored print cannot be played back as a recording."""
    record = voiceprint.get(enrolled)
    assert record["vectors"], "nothing was stored"
    for vector in record["vectors"]:
        assert all(isinstance(v, float) for v in vector)
        # Far too few numbers to be audio: two seconds at 16 kHz is 32000
        # samples, and this is a few dozen summary statistics.
        assert len(vector) < 100


# ------------------------------------------------------------- the matching
def test_the_same_speaker_is_recognised(enrolled):
    for seed in (10, 11, 12):
        result = voiceprint.verify(enrolled, voice(seed=seed, **RAMESH))
        assert result["match"] == "strong", result


def test_a_tired_or_noisy_version_of_the_same_voice_still_matches(enrolled):
    result = voiceprint.verify(enrolled,
                               voice(seed=20, noise=0.05, seconds=1.5, **RAMESH))
    assert result["match"] in ("strong", "weak"), result


@pytest.mark.parametrize("other,label", [(SUNITA, "a very different voice"),
                                         (OTHER, "another man, similar age")])
def test_a_different_speaker_is_refused(enrolled, other, label):
    """The test that would have caught the first version.

    It scored a woman's voice at 0.928 against a man's print, with "strong"
    beginning at 0.86 - every feature was positive, so every cosine was near
    one. Pitch is now compared as a distance in Hz rather than as one dimension
    among thirty-five.
    """
    result = voiceprint.verify(enrolled, voice(seed=30, **other))
    assert result["match"] == "no", "%s matched: %s" % (label, result)


def test_there_is_real_daylight_between_same_and_different(enrolled):
    """A margin, not a coin toss - and measured where the margin actually is.

    The raw similarity never had one: an enrolled farmer scored 0.994 against
    himself and a different man with a pitch five hertz away scored 0.954. Four
    hundredths apart, with no threshold that could sit between them. The
    decision is made on the distance above the BACKGROUND now, and that is
    where the daylight is.
    """
    mine = min(voiceprint.verify(enrolled, voice(seed=s, **RAMESH))["z"]
               for s in (10, 11, 12))
    assert mine >= voiceprint.Z_ACCEPT, "the farmer was not recognised: %.2f" % mine
    for kit in (SUNITA, OTHER):
        verdict = voiceprint.verify(enrolled, voice(seed=30, **kit))
        if verdict["z"] is not None:
            assert verdict["z"] < voiceprint.Z_WEAK,                 "a stranger reached %.2f" % verdict["z"]


def test_pitch_is_measured_correctly():
    """A 118 Hz voice once measured as 21 Hz: the frame was not mean-centred,
    so the DC offset dominated the autocorrelation and it locked onto a
    sub-harmonic. Pitch is most of what separates two speakers, so this being
    wrong made the whole thing useless."""
    import numpy as np

    for kit in (RAMESH, SUNITA, OTHER):
        values, rate = voiceprint._samples(voice(seed=7, **kit))
        measured, _spread = voicefeatures.pitch_spread(np.asarray(values), rate)
        assert abs(measured - kit["f0"]) < 8, \
            "f0 %d measured as %.1f" % (kit["f0"], measured)


# ----------------------------------------------------- when it cannot tell
def test_a_clip_too_short_to_judge_says_unknown(enrolled):
    """And unknown must never be read as a refusal."""
    result = voiceprint.verify(enrolled, voice(seed=1, seconds=0.4, **RAMESH))
    assert result["match"] == "unknown"


def test_an_unenrolled_number_says_unknown():
    voiceprint.ensure_schema()
    voiceprint.forget("9800000099")
    result = voiceprint.verify("9800000099", voice(seed=1, **RAMESH))
    assert result["match"] == "unknown"
    assert result["enrolled"] is False


def test_rubbish_bytes_do_not_raise():
    assert voiceprint.features(b"not a wav at all") is None
    assert voiceprint.features(b"") is None


def test_only_the_last_few_clips_are_kept(enrolled):
    for seed in range(50, 56):
        voiceprint.enrol(enrolled, voice(seed=seed, **RAMESH))
    assert voiceprint.get(enrolled)["clips"] == voiceprint.MAX_CLIPS


# ------------------------------------------------------------------- the API
def test_the_endpoints_need_an_account(client):
    assert client.get("/api/voiceid/status").status_code in (401, 403)
    assert client.post("/api/voiceid/consent",
                       json={"agreed": True}).status_code in (401, 403)
    assert client.delete("/api/voiceid").status_code in (401, 403)


def test_the_status_says_plainly_what_it_is_for(client):
    """A person deciding whether to hand over their voice deserves to know."""
    from app.core import voiceprint as vp
    body = vp.status("9800000077")
    assert "not a password" in body["note"]
    assert "cannot be played back" in body["note"]
