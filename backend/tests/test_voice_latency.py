"""The spoken-turn budget, enforced as a test.

A phone turn has to be audible inside two seconds. Nothing here measures wall
clock - a test that asserts on timing fails on a slow laptop and passes on a fast
one without saying anything true. Instead these lock the three things that put the
live turn at 3.9 s when it was measured:

  * the reply is capped at VOICE_MAX_WORDS in code, not just asked for in the prompt
  * identical words are never synthesised twice, so the fixed greeting is free
  * the agent chain and fact extraction stay off the critical path
"""
from __future__ import annotations

import json

import pytest

from app.conversation.voice_agent import VOICE_MAX_TOKENS, VOICE_MAX_WORDS, voice_agent
from app.core import farmers
from app.llm import mistral_client, sarvam_client
from app.speech import voice_io

PHONE = "9812345670"

# Enough of a header for the format check: MP3 frame sync, and "RIFF....WAVE".
FAKE_MP3 = b"\xff\xe3\x88\xc4" + b"\x00" * 60
FAKE_WAV_B64 = "UklGRiQAAABXQVZF"


@pytest.fixture
def scripted_model(monkeypatch):
    """A Mistral that says exactly what the test tells it to."""
    def install(reply):
        def transport(url, payload):
            system = payload["messages"][0].get("content") or ""
            if "extract ONLY durable facts" in system:
                content = json.dumps({"facts": []})
            elif "Summarise this farm helpline call" in system:
                content = "Call summary."
            else:
                content = reply
            return {"model": payload["model"],
                    "choices": [{"message": {"role": "assistant", "content": content}}]}

        monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")
        mistral_client.set_transport(transport)

    yield install
    mistral_client.set_transport(None)


# ---------------------------------------------------------------- the word cap
def test_a_long_reply_is_cut_to_the_spoken_budget():
    """The model ignored a 25-word instruction and returned 28. Cap it in code.

    This is the exact reply the live box produced at 3.9 s.
    """
    measured = ("Haan ji, paani dena toh achha hai, par aapki mitti mein abhi tees point "
                "chhah pratishat paani hai jo optimal range mein hai, isliye aaj sinchai "
                "ki zaroorat nahin hai, kal dekhte hain.")
    reply = voice_agent._tidy(measured, VOICE_MAX_WORDS)
    assert len(reply.split()) <= VOICE_MAX_WORDS
    assert reply.endswith((".", "?", "!", "।"))


def test_the_cap_prefers_a_finished_sentence():
    """Ending on a full stop beats ending mid-clause, even if it costs some words."""
    text = ("Haan ji, aapki mitti theek hai. Aaj sinchai ki koi zaroorat nahin hai, "
            "kal subah phir dekh lenge saath mein.")
    assert len(text.split()) > VOICE_MAX_WORDS           # the cap really does engage
    assert voice_agent._tidy(text, VOICE_MAX_WORDS) == "Haan ji, aapki mitti theek hai."


@pytest.mark.parametrize("text,ends_with", [
    # both of these came off real calls, where the cap cut them inside a number
    ("Haan ji, aaj aapke khet mein 27.7 C ka temperature hai aur 30.6 pratishat "
     "nami bhi hai", "hai"),
    ("Haan ji, wheat ka mandi ka bhav aaj per quintal 1950 rupee se 2000 tak "
     "chal raha hai", "rupee"),
])
def test_truncation_never_ends_on_a_half_spoken_number(text, ends_with):
    """A figure cut mid-range is worse than no figure: the farmer hears a wrong price."""
    reply = voice_agent._tidy(text, 14)
    assert reply.rstrip(".").split()[-1] == ends_with, reply
    assert not reply.rstrip(".").split()[-1].isdigit()


def test_a_short_reply_is_left_exactly_as_it_is():
    assert voice_agent._tidy("Haan ji, theek hai.", VOICE_MAX_WORDS) == "Haan ji, theek hai."


def test_an_answer_gets_more_room_than_small_talk():
    """Fourteen words cannot carry a list of crops.

    Asked which crops to sow, the capped reply stopped at "...agar aap agli fasal." -
    before the answer. The data cap costs about 0.6 s of extra speech and is worth it.
    """
    from app.conversation.voice_agent import VOICE_MAX_WORDS_DATA
    assert VOICE_MAX_WORDS_DATA > VOICE_MAX_WORDS
    assert VOICE_MAX_WORDS_DATA <= 24          # still a spoken turn, not a paragraph

    long_answer = ("Haan ji, is mausam mein aap sarson, chana ya gehu laga sakte hain, "
                   "mitti aur paani dono theek hain abhi")
    kept = voice_agent._tidy(long_answer, VOICE_MAX_WORDS_DATA)
    assert len(kept.split()) <= VOICE_MAX_WORDS_DATA
    # the crops survive the cut, which is the whole point
    assert "sarson" in kept and "chana" in kept


def test_the_budget_constants_agree_with_each_other():
    """The token ceiling must let a 14-word sentence FINISH, with room to spare.

    They are not the same size on purpose. The word cap is what the caller hears; the
    token ceiling only has to be loose enough that the model reaches a full stop on its
    own, because a reply cut off by the ceiling ends mid-word.
    """
    assert VOICE_MAX_WORDS == 14
    assert VOICE_MAX_TOKENS >= VOICE_MAX_WORDS * 3      # Hindi runs >1 token per word
    assert VOICE_MAX_TOKENS <= VOICE_MAX_WORDS * 6      # but not room for a paragraph


@pytest.mark.asyncio
async def test_every_spoken_reply_goes_through_the_cap(scripted_model):
    """Not just the helper - the real turn must come back inside the budget."""
    scripted_model("Haan ji " + " ".join("shabd%d" % i for i in range(40)) + ".")
    farmers.ensure_schema()
    turn = await voice_agent.respond(phone=PHONE, said="mera khet kaisa hai",
                                     call_id="cap-1", language="hi")
    for task in turn.background:
        task.cancel()
    assert len(turn.reply.split()) <= VOICE_MAX_WORDS


# ------------------------------------------------------------- the audio cache
@pytest.mark.asyncio
async def test_identical_words_are_synthesised_once(monkeypatch):
    """The greeting is the same text on every call; it must cost the network once.

    The digest used to be built from the API's response, so the lookup could never
    happen before the call it was meant to avoid.
    """
    spoken = []

    def transport(url, payload):
        spoken.append(url)
        return FAKE_MP3 if url.endswith("/stream") else {"audios": [FAKE_WAV_B64]}

    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key")
    sarvam_client.set_transport(transport)
    try:
        first = await voice_io.synthesize("नमस्ते, मैं सारथी हूँ।", "hi")
        second = await voice_io.synthesize("नमस्ते, मैं सारथी हूँ।", "hi")
    finally:
        sarvam_client.set_transport(None)

    assert first.ok and second.ok
    assert first.url == second.url
    assert first.cached is False and second.cached is True
    assert first.fmt == "mp3" and first.url.endswith(".mp3")
    assert len(spoken) == 1, "the second call hit the network: %r" % (spoken,)


# ------------------------------------------------ the fast endpoint and its fallback
@pytest.mark.asyncio
async def test_the_streaming_endpoint_is_preferred(monkeypatch):
    """Measured on the VPS: 1037 ms streaming against 2289 ms batch, same voice."""
    urls = []

    def transport(url, payload):
        urls.append(url)
        return FAKE_MP3 if url.endswith("/stream") else {"audios": [FAKE_WAV_B64]}

    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key")
    sarvam_client.set_transport(transport)
    try:
        out = await sarvam_client.client.speak("Haan ji, theek hai.", "hi")
    finally:
        sarvam_client.set_transport(None)

    assert out.ok and out.fmt == "mp3" and out.endpoint == "stream"
    assert urls == [sarvam_client.TTS_STREAM_URL], "the batch endpoint was called too"


@pytest.mark.asyncio
async def test_a_dead_streaming_endpoint_falls_back_to_batch(monkeypatch):
    """Latency is the right thing to lose here, not the call."""
    def transport(url, payload):
        if url.endswith("/stream"):
            raise RuntimeError("HTTP 503: upstream unavailable")
        return {"audios": [FAKE_WAV_B64]}

    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key")
    sarvam_client.set_transport(transport)
    try:
        out = await sarvam_client.client.speak("Haan ji, theek hai.", "hi")
    finally:
        sarvam_client.set_transport(None)

    assert out.ok and out.fmt == "wav" and out.endpoint == "batch"


@pytest.mark.asyncio
async def test_non_mp3_from_the_stream_is_not_handed_to_twilio(monkeypatch):
    """Twilio plays silence for a file it cannot decode, which reads as a dead line."""
    def transport(url, payload):
        if url.endswith("/stream"):
            return b'{"error":"something changed"}'
        return {"audios": [FAKE_WAV_B64]}

    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key")
    sarvam_client.set_transport(transport)
    try:
        out = await sarvam_client.client.speak("Haan ji, theek hai.", "hi")
    finally:
        sarvam_client.set_transport(None)

    assert out.ok and out.fmt == "wav" and out.endpoint == "batch"


@pytest.mark.asyncio
async def test_different_words_are_not_served_the_same_clip(monkeypatch):
    def transport(url, payload):
        return {"audios": ["UklGRiQAAABXQVZF"]}

    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key")
    sarvam_client.set_transport(transport)
    try:
        one = await voice_io.synthesize("Namaste ji.", "hi")
        two = await voice_io.synthesize("Dhanyavaad ji.", "hi")
        telephony_off = await voice_io.synthesize("Namaste ji.", "hi", telephony=False)
    finally:
        sarvam_client.set_transport(None)

    assert one.url != two.url
    assert one.url != telephony_off.url, "sample rate must be part of the cache key"


# ------------------------------------------------- what stays off the hot path
@pytest.mark.asyncio
async def test_the_agent_chain_does_not_block_the_answer(scripted_model):
    """Running the 15 agents inline measured 11.2 s live, and the caller heard silence."""
    scripted_model("Haan ji, abhi dekh kar batata hoon.")
    farmers.ensure_schema()
    turn = await voice_agent.respond(phone=PHONE, said="gehu mein paani dena chahiye kya",
                                     call_id="hot-1", language="hi")
    assert turn.reply
    assert turn.background, "extraction and the chain should be scheduled, not awaited"
    for task in turn.background:
        assert not task.done() or task.cancelled() or task.exception() is None
        task.cancel()
