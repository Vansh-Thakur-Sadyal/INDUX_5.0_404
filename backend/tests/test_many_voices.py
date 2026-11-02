"""More than one voice on an account, each with a name - and a switch.

A farm is worked by more than one person. The first version stored exactly one
voiceprint per phone number, so a father and son sharing an account had to
choose which of them Saarthi would recognise, and the other was refused on
their own farm.

Two rules this file exists to hold:

* **consent belongs to the person, not the account.** An account holder cannot
  agree on behalf of their son. Each voice carries its own agreement and its
  own date, and enrolment refuses without it.
* **the switch deletes nothing.** Turning the voice check off has to be
  instant and reversible, or nobody will risk turning it on.
"""
from __future__ import annotations

import pytest

from app.core import voiceprint

PHONE = "9800000911"
OTHER = "9800000912"


@pytest.fixture(autouse=True)
def _cepstral(monkeypatch):
    """The synthetic clips below are tones, not speech.

    ECAPA cannot tell tones apart - it rates every one of them alike - so these
    tests pin the describer the way the other voice tests do. What is under
    test here is the STORE: several voices, their names, their consent and the
    switch. The matching itself is measured on real speech elsewhere.
    """
    from app.core import speakermodel

    monkeypatch.setattr(speakermodel, "available", lambda: False)
    monkeypatch.setattr(speakermodel, "is_embedding", lambda v: False)


def _clip(seed: int, seconds: float = 2.0) -> bytes:
    """A tone, as a WAV. Different seeds are different 'voices'."""
    import io
    import math
    import struct
    import wave

    rate = 16000
    frames = int(rate * seconds)
    base = 90 + seed * 17
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        samples = []
        for n in range(frames):
            t = n / rate
            value = (0.45 * math.sin(2 * math.pi * base * t)
                     + 0.25 * math.sin(2 * math.pi * base * 2 * t)
                     + 0.12 * math.sin(2 * math.pi * base * 3.7 * t))
            samples.append(struct.pack("<h", int(value * 24000)))
        handle.writeframes(b"".join(samples))
    return buffer.getvalue()


def _register(phone: str, seed: int, label: str = "") -> str:
    """Consent plus the three clips, and the voice id it produced."""
    made = voiceprint.add_voice(phone, label)
    voice_id = made["voice_id"]
    voiceprint.consent_given(phone, said="haan", voice_id=voice_id)
    for clip in range(voiceprint.MAX_CLIPS):
        voiceprint.enrol(phone, _clip(seed, 2.0 + clip * 0.1), voice_id=voice_id)
    return voice_id


# ------------------------------------------------------------- several voices
def test_two_people_can_register_on_one_account():
    first = _register(PHONE, seed=1, label="Owner 1")
    second = _register(PHONE, seed=9, label="Owner 2")
    assert first != second

    body = voiceprint.status(PHONE)
    assert body["voice_count"] == 2
    assert [v["label"] for v in body["voices"]] == ["Owner 1", "Owner 2"]
    assert all(v["registered"] for v in body["voices"])


def test_either_of_them_is_recognised():
    """The whole point. Refusing the son because the father registered first
    would be the same bug as before, one person further along."""
    first = _register(PHONE, seed=1, label="Owner 1")
    second = _register(PHONE, seed=9, label="Owner 2")

    one = voiceprint.verify(PHONE, _clip(1, 2.4))
    two = voiceprint.verify(PHONE, _clip(9, 2.4))

    assert one["match"] in ("strong", "weak"), one
    assert two["match"] in ("strong", "weak"), two
    assert one["voice_id"] == first
    assert two["voice_id"] == second


def test_the_result_says_which_voice_matched():
    _register(PHONE, seed=1, label="Ramesh")
    verdict = voiceprint.verify(PHONE, _clip(1, 2.4))
    assert verdict["label"] == "Ramesh"
    assert verdict["voices"] == 1


def test_a_stranger_is_still_refused_with_two_registered():
    """Two voices must not mean a wider net: adding people to an account cannot
    turn the check into a formality."""
    _register(PHONE, seed=1)
    _register(PHONE, seed=9)
    verdict = voiceprint.verify(PHONE, _clip(30, 2.4))
    assert verdict["match"] == "no", verdict


def test_the_account_is_capped():
    for _ in range(voiceprint.MAX_VOICES):
        voiceprint.add_voice(PHONE)
    refused = voiceprint.add_voice(PHONE)
    assert refused["ok"] is False
    assert refused["reason"] == "too_many"


# -------------------------------------------------------------------- naming
def test_a_voice_can_be_renamed():
    voice_id = _register(PHONE, seed=1, label="Owner 1")
    voiceprint.rename(PHONE, voice_id, "  Ramesh Kumar  ")
    assert voiceprint.get(PHONE, voice_id)["label"] == "Ramesh Kumar"


def test_an_empty_name_is_refused_rather_than_stored():
    voice_id = _register(PHONE, seed=1, label="Owner 1")
    assert voiceprint.rename(PHONE, voice_id, "   ")["ok"] is False
    assert voiceprint.get(PHONE, voice_id)["label"] == "Owner 1"


def test_a_voice_on_another_account_cannot_be_renamed():
    mine = _register(PHONE, seed=1)
    assert voiceprint.rename(OTHER, mine, "theirs")["ok"] is False
    assert voiceprint.get(PHONE, mine)["label"] == "Owner 1"


def test_unnamed_voices_are_numbered():
    voiceprint.add_voice(PHONE)
    voiceprint.add_voice(PHONE)
    assert [v["label"] for v in voiceprint.voices_for(PHONE)] == ["Owner 1", "Owner 2"]


# ------------------------------------------------------------------- consent
def test_a_new_voice_starts_without_consent():
    """The second voice belongs to a second person. The account holder cannot
    agree on their behalf."""
    first = _register(PHONE, seed=1)
    second = voiceprint.add_voice(PHONE, "Owner 2")["voice_id"]

    assert voiceprint.get(PHONE, second)["consent"] is False
    refused = voiceprint.enrol(PHONE, _clip(9), voice_id=second)
    assert refused["ok"] is False
    assert refused["reason"] == "no_consent"
    # And the first voice is untouched by any of it.
    assert voiceprint.get(PHONE, first)["done"] is True


def test_each_voice_keeps_its_own_agreement():
    first = _register(PHONE, seed=1)
    second = _register(PHONE, seed=9)
    assert voiceprint.get(PHONE, first)["consent_said"] == "haan"
    assert voiceprint.get(PHONE, second)["consent_at"] is not None


# -------------------------------------------------------------------- removal
def test_one_voice_can_be_removed_without_the_other():
    first = _register(PHONE, seed=1, label="Owner 1")
    second = _register(PHONE, seed=9, label="Owner 2")

    assert voiceprint.forget(PHONE, voice_id=second) is True
    left = voiceprint.voices_for(PHONE)
    assert [v["voice_id"] for v in left] == [first]
    assert voiceprint.verify(PHONE, _clip(1, 2.4))["match"] in ("strong", "weak")


def test_removing_everything_still_works():
    _register(PHONE, seed=1)
    _register(PHONE, seed=9)
    assert voiceprint.forget(PHONE) is True
    assert voiceprint.voices_for(PHONE) == []
    assert voiceprint.enrolled(PHONE) is False


def test_one_account_cannot_delete_another_accounts_voice():
    mine = _register(PHONE, seed=1)
    assert voiceprint.forget(OTHER, voice_id=mine) is False
    assert voiceprint.get(PHONE, mine) is not None


# --------------------------------------------------------------- the switch
def test_the_check_is_on_by_default():
    """A farmer who went to the trouble of registering meant it to be used."""
    assert voiceprint.auth_enabled(PHONE) is True


def test_turning_it_off_allows_everything_and_deletes_nothing():
    _register(PHONE, seed=1)
    stranger = _clip(30, 2.4)
    assert voiceprint.gate(PHONE, stranger)["allow"] is False

    voiceprint.set_auth(PHONE, False)
    opened = voiceprint.gate(PHONE, stranger)
    assert opened["allow"] is True
    assert opened["checked"] is False
    assert "switched off" in opened["reason"]

    # Nothing was deleted: switching it back on works immediately.
    assert voiceprint.enrolled(PHONE) is True
    voiceprint.set_auth(PHONE, True)
    assert voiceprint.gate(PHONE, stranger)["allow"] is False


def test_the_switch_is_per_account():
    _register(PHONE, seed=1)
    _register(OTHER, seed=1)
    voiceprint.set_auth(PHONE, False)
    assert voiceprint.auth_enabled(PHONE) is False
    assert voiceprint.auth_enabled(OTHER) is True


def test_the_status_reports_the_switch_before_anyone_registers():
    """The toggle is shown on a screen with no voices on it yet, so the state
    has to be readable there."""
    body = voiceprint.status(PHONE)
    assert body["auth_enabled"] is True
    assert body["voices"] == []
    assert body["room_for_more"] == voiceprint.MAX_VOICES


# ------------------------------------------------------------- the old shape
def test_a_single_print_from_before_is_carried_across():
    """`voiceprints` held one row per phone. Those registrations are migrated
    into the new table rather than being lost or asked for again."""
    from app.core.storage import cursor

    voiceprint.ensure_schema()
    now = "2026-10-01T00:00:00+00:00"
    with cursor() as cur:
        cur.execute(
            "INSERT INTO voiceprints (phone, vectors_json, clips, consent,"
            " consent_at, consent_said, language, created_at, updated_at,"
            " tightness, done, vector_version, enrol_z)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("9800000999", "[[1.0, 2.0], [1.1, 2.1]]", 2, 1, now, "haan", "hi",
             now, now, 0.9, 1, voiceprint.vector_version(), 0.0))

    voiceprint.ensure_schema()        # the migration runs here
    carried = voiceprint.voices_for("9800000999")
    assert len(carried) == 1
    assert carried[0]["label"] == voiceprint.FIRST_LABEL
    assert carried[0]["consent"] is True
    assert carried[0]["clips"] == 2

    # And it is not carried twice.
    voiceprint.ensure_schema()
    assert len(voiceprint.voices_for("9800000999")) == 1
