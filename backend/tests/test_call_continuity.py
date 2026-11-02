"""A call that keeps going, instead of starting over.

One live call, reported as "it is just crap, does not work": the caller asked for
a tomato price, Saarthi said it would check, and a few seconds later the line
said *"Namaste Vansh ji, Saarthi bol raha hoon. Aaj kya madad chahiye?"* - the
opening greeting, mid-conversation.

The mechanism, which nothing in the suite could see: an empty transcript was
treated as "the line just opened" wherever it arrived. On a real call Twilio
gathers speech on a timer, and a caller waiting for an answer says nothing - so
the turn after any slow answer arrived with an empty transcript and was greeted
as a brand new call. Every test until now sent an empty transcript only as the
first turn, which is exactly the case that was right.
"""
from __future__ import annotations

import pytest

from app.core import farmers

TOKEN = {"X-Saarthi-Token": "test-secret"}
PHONE = "+91 90000 22222"


@pytest.fixture
def speaking(monkeypatch):
    """Synthesis that always succeeds, so these tests are about words only."""
    class _Spoken:
        ok, url, provider, voice, reason, path = (
            True, "/api/speech/audio/x.wav", "sarvam", "priya", "", None)

    async def fake_tts(*args, **kwargs):
        return _Spoken()

    from app.speech import voice_io
    monkeypatch.setattr(voice_io, "synthesize", fake_tts)
    return fake_tts


def _turn(client, transcript, call_id="CALL-CONT-1", **extra):
    body = {"phone": PHONE, "transcript": transcript, "call_id": call_id,
            "language": "hi"}
    body.update(extra)
    response = client.post("/api/voice/turn", json=body, headers=TOKEN)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_first_empty_transcript_is_the_greeting(client, speaking, fake_llm):
    opening = _turn(client, "")
    assert opening["greeted"] is True
    assert "सारथी" in opening["reply"] or "Saarthi" in opening["reply"]


def test_silence_later_in_the_call_is_not_greeted_again(client, speaking, fake_llm):
    """The bug exactly: ask something, go quiet, and the call restarts."""
    farmers.ensure_schema()
    opening = _turn(client, "", call_id="CALL-CONT-2")
    assert opening["greeted"] is True

    _turn(client, "tomato ka mandi bhav kya hai", call_id="CALL-CONT-2", resumed=True)

    # The caller is now waiting for the price and says nothing.
    silence = _turn(client, "", call_id="CALL-CONT-2", resumed=True)

    assert silence["greeted"] is False, "it greeted the caller again mid-call"
    reply = silence["reply"]
    for opener in ("नमस्ते", "Namaste", "क्या मदद", "What do you need",
                   "आपका नाम", "Your name"):
        assert opener not in reply, "the greeting came back as %r" % reply
    assert reply, "silence produced nothing to say at all"


def test_the_nudge_is_short(client, speaking, fake_llm):
    """It is spoken to somebody who is already waiting. A long sentence there
    spends their patience on being told to speak."""
    from app.routers.voice import NUDGE

    for language, words in NUDGE.items():
        assert len(words.split()) <= 7, "%s nudge is %r" % (language, words)


@pytest.fixture
def chain_found(monkeypatch):
    """Pretend the 15-agent chain has finished and left these findings.

    Patched rather than planted in the snapshot cache: the real chain runs in the
    background during the question turn and overwrites whatever is in that cache,
    so a planted entry made the test depend on which task won the race. What is
    being tested here is the delivery - that a finding reaches the caller on a
    quiet turn, exactly once - not that the chain produces one, which the
    agent tests already cover.
    """
    lines = ["Soil nitrogen is 142 kg per hectare, which is low for wheat.",
             "Irrigation: skip the next flood irrigation; 31 mm of rain is forecast."]

    from app.conversation import snapshot as snapshot_module

    def peek(_farm):
        return snapshot_module.Snapshot(parcel_id="field-1", agent_lines=list(lines))

    # `_mid_call_silence` imports the module inside the function, so patching the
    # attribute on the module itself is what reaches it.
    monkeypatch.setattr(snapshot_module, "peek", peek)
    return lines


def test_a_finding_that_arrived_since_is_read_out_on_the_silence(client, speaking,
                                                                 fake_llm, chain_found):
    """What makes "ask again in a moment" honest.

    The chain keeps running after a turn that could not be answered. When the
    caller goes quiet, its findings are read out - so the answer arrives without
    the caller having to work out that they were meant to ask twice.
    """
    farmers.ensure_schema()
    _turn(client, "", call_id="CALL-CONT-3")
    _turn(client, "meri mitti kaisi hai", call_id="CALL-CONT-3", resumed=True)

    silence = _turn(client, "", call_id="CALL-CONT-3", resumed=True)
    assert silence["greeted"] is False
    assert chain_found[0] in silence["reply"], silence["reply"]


def test_a_finding_is_not_read_out_twice(client, speaking, fake_llm, chain_found):
    """Two quiet moments in a row must not replay the same answer."""
    farmers.ensure_schema()
    _turn(client, "", call_id="CALL-CONT-4")
    _turn(client, "meri mitti kaisi hai", call_id="CALL-CONT-4", resumed=True)

    first = _turn(client, "", call_id="CALL-CONT-4", resumed=True)
    assert chain_found[0] in first["reply"]

    second = _turn(client, "", call_id="CALL-CONT-4", resumed=True)
    assert chain_found[0] not in second["reply"],         "the same finding was read out again: %r" % second["reply"]
    # Both findings were delivered across the two turns, so by the third quiet
    # moment there is nothing left and the caller gets the short nudge.
    third = _turn(client, "", call_id="CALL-CONT-4", resumed=True)
    from app.routers.voice import NUDGE
    assert third["reply"] == NUDGE["hi"], third["reply"]


def test_a_different_call_from_the_same_number_still_greets(client, speaking, fake_llm):
    """Continuity is per CALL, not per number. Hanging up and ringing back is a
    new conversation and should be greeted as one."""
    farmers.ensure_schema()
    _turn(client, "", call_id="CALL-CONT-5")
    _turn(client, "namaste", call_id="CALL-CONT-5", resumed=True)

    later = _turn(client, "", call_id="CALL-CONT-6")
    assert later["greeted"] is True


# ------------------------------------------------- a call that rang for a reason
def test_a_booked_call_says_what_it_rang_about(client, speaking, fake_llm):
    """Reported live: a call booked with "call me after 2 minutes and tell me the
    market price of wheat" rang exactly on time, said "Namaste Vansh ji, Saarthi
    bol raha hoon" and waited - never mentioning wheat.

    The answer had already been worked out at fire time and was sitting on the
    call record. The line just never read it out.
    """
    from app.core import storage
    from app.telephony import n8n_client
    from app.core.schemas import CallRequest

    farmers.ensure_schema()
    answer = "Gehu ka bhav aaj 2650 rupaye quintal hai."
    record = n8n_client.record_scheduled_call(
        CallRequest(phone=PHONE, language="hi", topic=answer, opening=answer,
                    farmer_name="Vansh"),
        dial_at="2026-01-01T00:00:00+00:00")
    storage.update_call(record["call_id"], status="requested")

    opening = _turn(client, "", call_id=record["call_id"], direction="outbound")

    assert answer in opening["reply"], opening["reply"]
    assert "क्या मदद चाहिए" not in opening["reply"], "it asked instead of telling"


def test_an_inbound_call_is_still_greeted(client, speaking, fake_llm):
    """The farmer rang Saarthi, so Saarthi has nothing to deliver and asking what
    they need is exactly right."""
    opening = _turn(client, "", call_id="CALL-INBOUND-1")
    assert opening["greeted"] is True
    assert "सारथी" in opening["reply"] or "Saarthi" in opening["reply"]
