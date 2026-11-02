"""Registering a voice, properly.

What was wrong with the first version, in one sentence: **one clip of anything
counted as a finished registration**. Press the button, say nothing useful, get
thanked, and end up with a voiceprint that then decided whether you were allowed
to speak to your own account.

Everything here protects one of the four rules that fixes:

1. a clip is INSPECTED before it is learnt from, and a refusal says why
2. registration is finished only when three good clips are in
3. the three clips have to sound like the same person, or the set is thrown away
4. the accept threshold is calibrated from those three clips, not fixed - a
   farmer whose own clips agree at 0.80 can never reach a global 0.86, so a
   fixed threshold refuses the one person it exists to recognise

The voices below are synthesised from a pitch and a formant pattern, which is
closer to speech than the pure tones the first thresholds were chosen on. It is
still not real phone audio, and that limit is stated in the module docstring of
`voiceprint.py` rather than hidden here.
"""
from __future__ import annotations

import io
import math
import random
import struct
import wave

import pytest

from app.core import voiceprint

PHONE = "9876500303"


def _wav(samples, rate=16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(b"".join(
            struct.pack("<h", max(-32767, min(32767, int(v * 26000))))
            for v in samples))
    return buffer.getvalue()


def voice(pitch: float, formants, seconds: float = 3.0, rate: int = 16000,
          seed: int = 1, loudness: float = 1.0):
    """A crude synthetic speaker: a buzz at `pitch` through fixed formants.

    Not speech, but it has the two things the comparison actually looks at - a
    fundamental and a spectral envelope - and two "speakers" built this way are
    as different from each other as the features can see.
    """
    rng = random.Random(seed)
    out = []
    frames = int(rate * seconds)
    for i in range(frames):
        t = i / rate
        # A sawtooth-ish glottal buzz: harmonics for the formants to shape.
        value = sum(math.sin(2 * math.pi * pitch * h * t) / h for h in range(1, 12))
        shaped = sum(math.sin(2 * math.pi * f * t) * a for f, a in formants)
        # A little jitter, so three clips of one speaker are not identical.
        out.append(loudness * 0.45 * (value * 0.6 + shaped * 0.4)
                   + 0.01 * rng.uniform(-1, 1))
    return out


# Two speakers. The first is a low voice with low formants, the second a higher
# voice with higher ones - about as far apart as a man and a woman.
RAMESH = dict(pitch=112.0, formants=[(520, .5), (1180, .32), (2500, .14)])
SUNITA = dict(pitch=212.0, formants=[(780, .5), (1850, .32), (3100, .14)])


def clip_of(who, seed=1, **kwargs):
    return _wav(voice(seed=seed, **{**who, **kwargs}))


def silence(seconds=3.0, rate=16000):
    return _wav([0.0005 * random.uniform(-1, 1) for _ in range(int(rate * seconds))])


@pytest.fixture(autouse=True)
def schema(monkeypatch):
    """Every test here runs against the CEPSTRAL description, deliberately.

    The voices above are a glottal buzz through fixed formants. That is enough
    to exercise cepstral statistics, and ECAPA-TDNN - trained on 7,205 human
    speakers - cannot tell two buzzes apart at all: it scores one buzz against
    another at 0.974 and a buzz against itself at 0.988. A fixture the model
    cannot see a difference in cannot test the model, so these tests pin the
    describer they were written for and `test_speaker_model.py` covers the
    embedding path on real speech.

    The cepstral path is not dead code: it is what an installation without the
    84 MB model file falls back to, and it is what these rules protect there.
    """
    monkeypatch.setattr(voiceprint.speakermodel, "available", lambda: False)
    monkeypatch.setattr(voiceprint.speakermodel, "embed",
                        lambda values, rate: None)
    voiceprint.ensure_schema()
    voiceprint.forget(PHONE)


def _consent():
    voiceprint.consent_given(PHONE, said="haan, meri aawaz yaad rakhiye",
                             language="hi")


def _register(who=RAMESH, clips=3):
    _consent()
    last = {}
    for i in range(clips):
        last = voiceprint.enrol(PHONE, clip_of(who, seed=i + 1), language="hi")
    return last


# --------------------------------------------------- 1. a clip is inspected
def test_a_clip_of_a_quiet_room_is_refused_with_a_reason():
    report = voiceprint.inspect(silence())
    assert report["ok"] is False
    assert report["reason"] in ("too_quiet", "mostly_silence", "not_voiced")
    assert report["detail"], "a refusal with no reason sends them round the loop again"


def test_a_clip_too_short_says_how_short():
    report = voiceprint.inspect(clip_of(RAMESH, seconds=0.4))
    assert report["ok"] is False
    assert report["reason"] == "too_short"
    assert "0.4 seconds" in report["detail"]


def test_a_distorted_clip_is_refused():
    report = voiceprint.inspect(clip_of(RAMESH, loudness=6.0))
    assert report["ok"] is False
    assert report["reason"] == "too_loud"
    assert "further away" in report["detail"]


def test_a_good_clip_passes_and_reports_its_pitch():
    report = voiceprint.inspect(clip_of(RAMESH))
    assert report["ok"] is True, report
    assert 95 <= report["pitch_hz"] <= 130, report["pitch_hz"]
    assert report["speech_share"] > voiceprint.MIN_SPEECH_SHARE


def test_an_unreadable_clip_is_not_guessed_at():
    report = voiceprint.inspect(b"this is not a wav file at all")
    assert report["ok"] is False
    assert report["reason"] == "unreadable"


# ------------------------------------- 2. registration finishes when it finishes
def test_one_clip_is_not_a_registration():
    """The whole bug, in one test. One clip used to mean registered."""
    _consent()
    first = voiceprint.enrol(PHONE, clip_of(RAMESH, seed=1), language="hi")

    assert first["ok"] is True
    assert first["done"] is False
    assert first["need"] == 2
    assert voiceprint.enrolled(PHONE) is False
    assert voiceprint.status(PHONE)["registered"] is False
    assert voiceprint.status(PHONE)["started"] is True


def test_three_good_clips_finish_it():
    last = _register()
    assert last["ok"] is True and last["done"] is True
    assert last["need"] == 0
    assert voiceprint.enrolled(PHONE) is True

    state = voiceprint.status(PHONE)
    assert state["registered"] is True
    assert state["clips"] == 3
    assert state["quality"] in ("good", "fair")


def test_a_refused_clip_does_not_count_towards_the_three():
    _consent()
    voiceprint.enrol(PHONE, clip_of(RAMESH, seed=1), language="hi")
    bad = voiceprint.enrol(PHONE, silence(), language="hi")

    assert bad["ok"] is False
    assert bad["clips"] == 1, "a clip it could not use still moved the count"
    assert voiceprint.status(PHONE)["clips"] == 1


def test_nothing_is_stored_without_consent():
    result = voiceprint.enrol(PHONE, clip_of(RAMESH), language="hi")
    assert result["ok"] is False
    assert result["reason"] == "no_consent"
    assert voiceprint.get(PHONE) is None


# ------------------------------------------ 3. the clips must be one person
def test_three_clips_of_two_people_are_thrown_away():
    """Two voices in one registration build a print that matches everybody a
    little and nobody well - and the farmer would never find out why."""
    _consent()
    voiceprint.enrol(PHONE, clip_of(RAMESH, seed=1), language="hi")
    voiceprint.enrol(PHONE, clip_of(RAMESH, seed=2), language="hi")
    result = voiceprint.enrol(PHONE, clip_of(SUNITA, seed=3), language="hi")

    assert result["ok"] is False
    assert result["reason"] == "inconsistent"
    assert result["clips"] == 0, "the mixed set was kept"
    assert result["need"] == 3
    assert voiceprint.enrolled(PHONE) is False
    assert "same voice" in result["detail"]


def test_a_thrown_away_set_can_be_registered_again():
    _consent()
    for seed in (1, 2):
        voiceprint.enrol(PHONE, clip_of(RAMESH, seed=seed), language="hi")
    voiceprint.enrol(PHONE, clip_of(SUNITA, seed=3), language="hi")    # thrown away

    last = None
    for seed in (4, 5, 6):
        last = voiceprint.enrol(PHONE, clip_of(RAMESH, seed=seed), language="hi")
    assert last["done"] is True
    assert voiceprint.enrolled(PHONE) is True


# ----------------------------------------------- 4. the threshold is calibrated
def test_the_threshold_comes_from_the_farmers_own_clips():
    last = _register()
    agreement = last["agreement"]
    assert agreement >= voiceprint.CONSISTENT_FLOOR
    assert last["accept_above"] == pytest.approx(
        voiceprint.accept_threshold(agreement), abs=0.001)


@pytest.mark.parametrize("tightness,expected", [
    (0.0, (voiceprint.STRONG_MATCH + voiceprint.WEAK_MATCH) / 2),
    (0.95, 0.84),          # clamped at the top: never a gate that means nothing
    (0.80, 0.70),
    (0.65, 0.60),          # clamped at the bottom: never so tight it locks them out
])
def test_the_threshold_stays_inside_sane_bounds(tightness, expected):
    assert voiceprint.accept_threshold(tightness) == pytest.approx(expected, abs=0.001)


def test_the_farmer_is_recognised_and_the_other_person_is_not():
    _register(RAMESH)

    mine = voiceprint.verify(PHONE, clip_of(RAMESH, seed=9))
    theirs = voiceprint.verify(PHONE, clip_of(SUNITA, seed=9))

    assert mine["match"] in ("strong", "weak"), mine
    assert theirs["match"] == "no", theirs
    assert mine["score"] > theirs["score"]


def test_a_half_finished_registration_is_never_compared_against():
    _consent()
    voiceprint.enrol(PHONE, clip_of(RAMESH, seed=1), language="hi")

    verdict = voiceprint.verify(PHONE, clip_of(SUNITA, seed=2))
    assert verdict["match"] == "unknown"
    assert verdict["enrolled"] is False
    # And the gate therefore allows: nothing to compare means carry on.
    assert voiceprint.gate(PHONE, clip_of(SUNITA, seed=2))["allow"] is True


def test_the_gate_refuses_a_stranger_once_registration_is_finished():
    _register(RAMESH)
    assert voiceprint.gate(PHONE, clip_of(RAMESH, seed=8))["allow"] is True
    assert voiceprint.gate(PHONE, clip_of(SUNITA, seed=8))["allow"] is False


# ----------------------------------------------------------------- prompts
def test_there_are_three_different_things_to_say():
    """One word repeated three times describes the word, not the speaker."""
    for language in ("en", "hi"):
        said = voiceprint.prompts(language)
        assert len(said) == voiceprint.MAX_CLIPS
        assert len(set(said)) == voiceprint.MAX_CLIPS


def test_an_unknown_language_still_gets_prompts():
    assert voiceprint.prompts("xx") == voiceprint.prompts("en")


def test_forgetting_removes_everything():
    _register()
    assert voiceprint.forget(PHONE) is True
    assert voiceprint.get(PHONE) is None
    assert voiceprint.status(PHONE)["registered"] is False


# ============================== the failure that was reported out loud
# "the voice recognition still does not work, it is catching another person's
# voice". It was, and it was measurable: a different man whose pitch was five
# hertz away scored 0.896 against an enrolled farmer with the accept threshold
# at 0.84. Three things were wrong and all three are covered below.
def _nearby_speaker():
    """A different person with a pitch close enough to defeat a pitch-led score."""
    return dict(pitch=119.0, formants=[(730, .5), (1600, .32), (3000, .14)])


def test_a_different_man_with_a_similar_pitch_is_refused():
    """Fault one: pitch was sixty per cent of the score, so anyone with a
    similar fundamental started at 0.6 before the spectrum was consulted."""
    _register(RAMESH)
    stranger = _nearby_speaker()

    for seed in (4, 9, 14):
        verdict = voiceprint.verify(PHONE, clip_of(stranger, seed=seed))
        assert verdict["match"] == "no", verdict
        assert voiceprint.gate(PHONE, clip_of(stranger, seed=seed))["allow"] is False


def test_the_farmer_is_still_recognised_after_the_tightening():
    """The other half of it. A filter that keeps strangers out by keeping the
    owner out as well has solved nothing."""
    _register(RAMESH)
    for seed in (5, 9, 13, 21):
        verdict = voiceprint.verify(PHONE, clip_of(RAMESH, seed=seed))
        assert verdict["match"] in ("strong", "weak"), verdict
        assert voiceprint.gate(PHONE, clip_of(RAMESH, seed=seed))["allow"] is True


def test_the_decision_is_made_against_a_background_not_in_isolation():
    """Fault two: "how like the farmer is this" cannot separate 0.994 from
    0.954. "How much MORE like the farmer than like people in general" can."""
    _register(RAMESH)
    verdict = voiceprint.verify(PHONE, clip_of(RAMESH, seed=9))

    assert verdict["basis"] == "cohort"
    assert verdict["background"] >= 4, "too little background to normalise against"
    assert verdict["z"] is not None


def test_a_print_from_the_old_description_is_not_matched_against():
    """Fault three: the stored numbers changed shape. Comparing a new clip with
    an old print would be comparing numbers that mean different things, and it
    would look like it was working."""
    _register(RAMESH)
    from app.core.storage import cursor

    with cursor() as cur:
        cur.execute("UPDATE voices SET vector_version = 1 WHERE phone = ?",
                    (PHONE,))

    verdict = voiceprint.verify(PHONE, clip_of(RAMESH, seed=9))
    assert verdict["match"] == "unknown"
    assert verdict["enrolled"] is False
    assert verdict.get("stale") is True
    # And it says so, rather than quietly never matching again.
    assert voiceprint.status(PHONE)["stale"] is True
    assert voiceprint.status(PHONE)["registered"] is False
    # Unknown still means carry on - it must not lock the farmer out.
    assert voiceprint.gate(PHONE, clip_of(RAMESH, seed=9))["allow"] is True


def test_pitch_rules_out_rather_than_rules_in():
    """A voice an octave away is a different person and no amount of spectral
    agreement should rescue it. A voice a few hertz away is not ruled out - it
    is left to the cepstra, which is the only thing that can tell."""
    from app.core import voicefeatures

    # One person's own wobble: no penalty.
    assert voicefeatures.pitch_penalty(118, 3, 121, 3) == 1.0
    # Far enough that it cannot be the same person: ruled out outright.
    assert voicefeatures.pitch_penalty(118, 3, 215, 5) == 0.0
    # In between: weakened, not decided.
    middle = voicefeatures.pitch_penalty(118, 3, 142, 4)
    assert 0.0 < middle < 1.0, middle


# ------------------------------------------- 5. registering again, which is not
#    the same as carrying on
def test_registering_again_does_not_destroy_the_print_it_is_replacing():
    """Live, this is what "Register again" did: the first new clip was compared
    against two OLD ones, the three disagreed - a different room is enough -
    and the whole working registration was thrown away. So the one button a
    farmer would press to improve their voiceprint deleted it instead."""
    _register(RAMESH)
    assert voiceprint.status(PHONE)["registered"] is True

    # One clip, of anybody. It begins a NEW set rather than joining the old.
    first = voiceprint.enrol(PHONE, clip_of(SUNITA, seed=9), language="hi")

    assert first["ok"] is True, first
    assert first["clips"] == 1, "the new clip was blended into the finished set"
    assert first["done"] is False
    assert voiceprint.status(PHONE)["started"] is True


def test_restart_clears_the_clips_and_keeps_the_consent():
    """Consent survives because it was already given. Asking a farmer to agree
    again to something they agreed to is not more careful, it is noise."""
    _register(RAMESH)
    body = voiceprint.restart(PHONE)

    assert body["clips"] == 0
    assert body["need"] == voiceprint.MAX_CLIPS
    assert body["registered"] is False
    assert body["consent"] is True
    # And enrolling still works straight afterwards, without re-consenting.
    assert voiceprint.enrol(PHONE, clip_of(RAMESH, seed=7), language="hi")["ok"] is True


def test_restarting_a_voice_nobody_registered_is_not_an_error():
    voiceprint.forget(PHONE)
    body = voiceprint.restart(PHONE)

    assert body["registered"] is False
    assert body["clips"] == 0


def test_the_endpoint_restarts_without_asking_for_consent_again(client, account):
    voiceprint.consent_given(account.phone, said="haan", language="hi")
    for seed in (1, 2, 3):
        voiceprint.enrol(account.phone, clip_of(RAMESH, seed=seed), language="hi")
    assert client.get("/api/voiceid/status",
                      headers=account.headers).json()["registered"] is True

    body = client.post("/api/voiceid/restart", headers=account.headers).json()

    assert body["restarted"] is True
    assert body["clips"] == 0
    assert body["consent"] is True, "a farmer was asked to agree all over again"
    assert len(body["prompts"]) == voiceprint.MAX_CLIPS
    assert body["clips_wanted"] == voiceprint.MAX_CLIPS
