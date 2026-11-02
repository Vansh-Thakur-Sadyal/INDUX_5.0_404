"""Who is allowed to operate an account by voice.

The rule the whole thing turns on is what happens when it CANNOT tell, and the
answer is: carry on. A voiceprint built from band statistics and a pitch
estimate is a convenience filter. One that locks a farmer out of their own
helpline because they had a cold, or because the clip was half a second long,
is worse than no filter at all - they would turn it off, and then it protects
nobody.

So only a clear mismatch refuses, and a refusal cancels the operation rather
than doing it anyway with a warning.
"""
from __future__ import annotations

import math
import struct

import pytest

from app.core import voiceprint

PHONE = "9876500202"


def _tone(hz: float, seconds: float = 1.2, rate: int = 16000) -> bytes:
    """A WAV of one steady tone - two different ones stand in for two voices."""
    frames = int(rate * seconds)
    body = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * hz * i / rate)
                              + 2500 * math.sin(2 * math.pi * hz * 3 * i / rate)))
        for i in range(frames))
    header = (b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVEfmt "
              + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
              + b"data" + struct.pack("<I", len(body)))
    return header + body


@pytest.fixture(autouse=True)
def schema(monkeypatch):
    """Pinned to the cepstral description: the fixtures here are pure TONES.

    A tone is not speech. ECAPA-TDNN, trained on 7,205 human speakers, has
    nothing to say about two sine waves and scores them as nearly identical -
    so the refusal these tests exist to prove could not be tested through it.
    The rules being tested are the same on both paths; the embedding path is
    measured on real speech in `test_speaker_model.py`.
    """
    monkeypatch.setattr(voiceprint.speakermodel, "available", lambda: False)
    monkeypatch.setattr(voiceprint.speakermodel, "embed",
                        lambda values, rate: None)
    voiceprint.ensure_schema()


def _enrol_for(phone: str, hz: float = 118.0):
    voiceprint.consent_given(phone, said="haan, meri aawaz register kar lijiye",
                             language="hi")
    for _ in range(3):
        voiceprint.enrol(phone, _tone(hz), language="hi")


def _enrol(hz: float = 118.0):
    _enrol_for(PHONE, hz)


# ------------------------------------------------------- it carries on by default
def test_nobody_enrolled_is_allowed():
    """The commonest case by far, and a refusal here would lock out every farmer
    who never registered a voice."""
    verdict = voiceprint.gate(PHONE, _tone(118.0))
    assert verdict["allow"] is True
    assert verdict["match"] == "unknown"
    assert verdict["reason"] == "nothing to compare against"


def test_a_clip_too_short_to_compare_is_allowed():
    _enrol()
    verdict = voiceprint.gate(PHONE, _tone(118.0, seconds=0.05))
    assert verdict["allow"] is True
    assert verdict["match"] == "unknown"


def test_the_enrolled_voice_is_allowed():
    _enrol()
    verdict = voiceprint.gate(PHONE, _tone(118.0))
    assert verdict["allow"] is True
    assert verdict["match"] in ("strong", "weak")


# ---------------------------------------------------- and it refuses a mismatch
def test_a_different_voice_is_refused():
    _enrol(hz=118.0)
    verdict = voiceprint.gate(PHONE, _tone(232.0))
    assert verdict["allow"] is False, verdict
    assert verdict["match"] == "no"
    assert verdict["reason"] == "voice did not match"


def test_a_refusal_carries_words_to_say_in_both_languages():
    _enrol(hz=118.0)
    verdict = voiceprint.gate(PHONE, _tone(232.0))
    spoken = verdict["spoken"]
    for language in ("en", "hi"):
        assert spoken[language]
        # It must not accuse anybody, and it must say what to do next.
        assert "stop" in spoken[language].lower() or "rok" in spoken[language]
    assert "voice lock" in spoken["en"]


# ------------------------------------------------------------------ the endpoint
def test_the_endpoint_refuses_a_stranger(client, account):
    """The account holder enrolled; somebody else is speaking. The turn stops."""
    _enrol_for(account.phone, hz=118.0)
    body = client.post("/api/voiceid/gate",
                       files={"audio": ("clip.wav", _tone(232.0), "audio/wav")},
                       data={"language": "hi"}, headers=account.headers).json()

    assert body["allow"] is False, body
    assert body["say"], "a refusal with nothing to say leaves the farmer guessing"
    assert "rok" in body["say"], body["say"]
    assert "spoken" not in body, "the whole table should not go over the wire"


def test_the_endpoint_allows_the_owner(client, account):
    _enrol_for(account.phone, hz=118.0)
    body = client.post("/api/voiceid/gate",
                       files={"audio": ("clip.wav", _tone(118.0), "audio/wav")},
                       headers=account.headers).json()
    assert body["allow"] is True


def test_the_endpoint_allows_an_account_with_no_voice_registered(client, account):
    body = client.post("/api/voiceid/gate",
                       files={"audio": ("clip.wav", _tone(232.0), "audio/wav")},
                       headers=account.headers).json()
    assert body["allow"] is True
    assert body["enrolled"] is False
