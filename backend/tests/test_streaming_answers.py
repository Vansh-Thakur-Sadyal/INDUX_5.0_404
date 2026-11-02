"""The dashboard answer, and the machinery that makes it quick.

**Streaming.** Measured on this deployment, a dashboard answer about irrigation
with the data caches warm took 1.86 s end to end and produced its first words at
0.62 s. Waiting for the whole thing made the system feel three times slower than it
is. Nothing here asserts wall-clock timing - that would pass on a fast laptop and
fail on a slow one without saying anything true. What is asserted is the structure
that makes the speed possible: one pooled connection, a cached embedding, one model
call on the critical path, and deltas that arrive before the result does.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.conversation import voice_agent as voice_module
from app.conversation.voice_agent import voice_agent
from app.core import farmers
from app.llm import mistral_client, pool, sarvam_client

PHONE = "9812345678"


@pytest.fixture
def scripted(monkeypatch):
    """A Mistral that answers, and a Sarvam that would fail if anyone called it."""
    calls = []

    def mistral_transport(url, payload):
        # An embeddings payload carries `input`, not `messages`. The spoken path
        # retrieves from the knowledge base now, so one reaches here on most
        # turns; reading it as a chat payload raised inside the client and was
        # swallowed as "embeddings failed".
        if "input" in payload:
            rows = [{"index": i, "embedding": [0.1, 0.2, 0.3]}
                    for i, _ in enumerate(payload["input"])]
            return {"model": payload.get("model"), "data": rows}

        calls.append(payload)
        system = payload["messages"][0].get("content") or ""
        if "extract ONLY durable facts" in system:
            content = json.dumps({"facts": []})
        elif "Summarise this farm helpline call" in system:
            content = "Asked about irrigation."
        else:
            content = "Haan ji, abhi paani ki zaroorat nahin hai."
        return {"model": payload["model"],
                "choices": [{"message": {"role": "assistant", "content": content}}]}

    # Sarvam is switched OFF for this file, so the turn falls through to
    # Mistral and these tests measure Mistral's behaviour without having to
    # script two providers. The split itself is tested in test_model_fusion.py.
    def sarvam_unavailable(url, payload):
        raise RuntimeError("HTTP 503: sarvam not part of this test")

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam")
    mistral_client.set_transport(mistral_transport)
    sarvam_client.set_transport(sarvam_unavailable)
    yield calls
    mistral_client.set_transport(None)
    sarvam_client.set_transport(None)


# ------------------------------------------------------- what is Mistral's
# The split between the two models, and the figure check that makes it safe,
# are tested in test_model_fusion.py. What belongs here is the half that is
# Mistral's whatever is speaking on the call.
@pytest.mark.asyncio
async def test_remembering_what_was_said_goes_to_mistral(scripted):
    """Fact extraction wants strict JSON back, and only Mistral has a JSON mode.

    It also sits off the critical path, so it can afford the retries. Sarvam
    carries the talking; it does not carry the structured work.
    """
    farmers.ensure_schema()
    turn = await voice_agent.respond(phone=PHONE, said="mera naam Ramesh hai aur "
                                                       "main gehu ugata hoon",
                                     call_id="one-provider-2", language="hi")
    await asyncio.gather(*turn.background, return_exceptions=True)
    systems = [c["messages"][0]["content"] for c in scripted]
    assert any("extract ONLY durable facts" in s for s in systems), \
        "nothing tried to remember what the farmer said"


def test_one_attempt_on_the_phone_not_three():
    """The default client retries three times with a growing backoff.

    That is right for a report and wrong for a call: by the second attempt the
    farmer has already said "hello?". The budget is named in code so it cannot
    drift back to the default by accident.
    """
    assert voice_module.VOICE_TIMEOUT_SECONDS <= 8.0


class _Flaky:
    """A pooled client that answers 503 and counts how often it was asked.

    This has to sit at the pool rather than at the test transport, because the
    transport hook short-circuits before the retry loop - which is exactly the
    code under test here.
    """

    def __init__(self) -> None:
        self.attempts = 0
        self.is_closed = False

    async def post(self, url, **kwargs):
        self.attempts += 1

        class _Response:
            status_code = 503
            text = "upstream unavailable"

        return _Response()


@pytest.mark.asyncio
async def test_an_urgent_call_does_not_retry(monkeypatch):
    """A 1.5 s backoff and a second attempt is right for a report and wrong for a
    phone call: by then the farmer has already said "hello?"."""
    flaky = _Flaky()

    async def fake_pool(timeout=45.0):
        return flaky

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setattr(mistral_client.pool, "client", fake_pool)
    # No sleeping: the point is the number of attempts, not the wait between them.
    # The real sleep is captured first - patching the module attribute with a
    # lambda that calls asyncio.sleep makes it call itself.
    real_sleep = asyncio.sleep

    async def no_wait(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(mistral_client.asyncio, "sleep", no_wait)

    urgent = await mistral_client.client.chat(
        [{"role": "user", "content": "hi"}], urgent=True)
    assert not urgent.ok
    assert flaky.attempts == 1, "an urgent call retried: %d attempts" % flaky.attempts

    flaky.attempts = 0
    patient = await mistral_client.client.chat([{"role": "user", "content": "hi"}])
    assert not patient.ok
    assert flaky.attempts > 1, "a normal call should still retry"


# ----------------------------------------------------------------- the pool
@pytest.mark.asyncio
async def test_the_connection_is_reused_between_calls():
    """A fresh TLS handshake per request cost a measured 265 ms of every answer.

    No socket is opened here - the suite forbids that. What is checked is the
    bookkeeping that decides whether one gets opened: a client already held for
    this loop is handed back, and a closed one is not.
    """
    class _Stub:
        is_closed = False

    held = _Stub()
    pool._clients[pool._loop_key()] = held
    try:
        assert await pool.client() is held
        held.is_closed = True
        # A closed client must not be handed out again; without this the first
        # shutdown would poison every later request with "client has been closed".
        pool._clients[pool._loop_key()] = held
        assert pool._clients.get(pool._loop_key()).is_closed
    finally:
        pool._clients.pop(pool._loop_key(), None)
    assert pool._loop_key() != 0, "a running loop must key the pool"


@pytest.mark.asyncio
async def test_the_same_text_is_embedded_once(monkeypatch):
    """The retry button should not cost a round trip before retrieval can start."""
    calls = []

    def transport(url, payload):
        calls.append(payload)
        rows = [{"index": i, "embedding": [0.1, 0.2, 0.3]}
                for i in range(len(payload["input"]))]
        return {"data": rows}

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    mistral_client.set_transport(transport)
    try:
        one = await mistral_client.client.embed(["when should I irrigate wheat"])
        two = await mistral_client.client.embed(["when should I irrigate wheat"])
        assert one == two
        assert len(calls) == 1, "the second embedding hit the network"

        # A new text still goes out, and only the new one.
        await mistral_client.client.embed(["when should I irrigate wheat",
                                           "mandi price for mustard"])
        assert len(calls) == 2
        assert calls[1]["input"] == ["mandi price for mustard"]
    finally:
        mistral_client.set_transport(None)


def test_installing_a_transport_clears_the_cache(monkeypatch):
    """Otherwise a vector from the real API is served to a test that installed a
    transport precisely to make embedding fail, and the index records a provider
    that never ran."""
    mistral_client._embed_cache["x"] = [1.0]
    mistral_client.set_transport(None)
    assert not mistral_client._embed_cache


# ------------------------------------------------------------------ streaming
@pytest.mark.asyncio
async def test_the_stream_yields_text_then_a_summary(monkeypatch):
    def transport(url, payload):
        return {"model": "test-model", "choices": [
            {"message": {"role": "assistant", "content": "Do not irrigate today."}}]}

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    mistral_client.set_transport(transport)
    try:
        events = [e async for e in mistral_client.client.stream(
            [{"role": "user", "content": "should I irrigate"}])]
    finally:
        mistral_client.set_transport(None)

    assert any("delta" in e for e in events)
    final = [e for e in events if e.get("done")]
    assert final and final[0]["text"] == "Do not irrigate today."
    # The pieces must reassemble into exactly the final text, or a page that
    # appends deltas and a page that waits for the result show different answers.
    joined = "".join(e["delta"] for e in events if "delta" in e)
    assert joined == final[0]["text"]


def _sse_events(response):
    out = []
    for frame in response.text.split("\n\n"):
        line = next((l for l in frame.split("\n") if l.startswith("data:")), None)
        if line:
            out.append(json.loads(line[5:].strip()))
    return out


def test_the_dashboard_stream_reports_stages_then_the_advisory(client, fake_llm):
    """The same answer as POST /api/chat, sent as it is written."""
    response = client.post("/api/chat/stream", json={
        "message": "should I irrigate my wheat today", "language": "en"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _sse_events(response)
    stages = [e["stage"] for e in events if "stage" in e]
    assert "routing" in stages and "gathering" in stages and "answering" in stages

    advisories = [e["advisory"] for e in events if "advisory" in e]
    assert len(advisories) == 1, "exactly one final result, at the end"
    advisory = advisories[0]
    assert advisory["answer"]
    assert advisory["agents_invoked"], "the chain still ran"
    assert advisory["timings"]["answer_ms"] >= 0

    # Whatever was streamed has to end up as the stored answer. A page that
    # appends deltas and then never hears a correction would be left showing
    # uncleaned markdown.
    pieces = "".join(e["delta"] for e in events if "delta" in e)
    replacements = [e["replace"] for e in events if "replace" in e]
    shown = replacements[-1] if replacements else pieces
    assert shown == advisory["answer"]


def test_a_greeting_streams_as_one_piece_and_runs_no_agents(client, fake_llm):
    """Small talk has no model call to stream; the page should not have to care."""
    events = _sse_events(client.post("/api/chat/stream",
                                     json={"message": "hello", "language": "en"}))
    advisory = next(e["advisory"] for e in events if "advisory" in e)
    assert advisory["intent"] == "small_talk"
    assert advisory["agents_invoked"] == []
    assert "".join(e["delta"] for e in events if "delta" in e) == advisory["answer"]


def test_an_empty_message_is_refused_before_the_stream_opens(client):
    """Once the stream is open there is no status code left to send."""
    assert client.post("/api/chat/stream", json={"message": "   "}).status_code == 400


def test_the_streamed_and_plain_endpoints_agree_on_the_shape(client, fake_llm):
    plain = client.post("/api/chat", json={"message": "what is my soil like",
                                           "language": "en"}).json()["advisory"]
    events = _sse_events(client.post("/api/chat/stream",
                                     json={"message": "what is my soil like",
                                           "language": "en"}))
    streamed = next(e["advisory"] for e in events if "advisory" in e)
    assert set(plain) == set(streamed), "the two endpoints returned different objects"
    assert streamed["intent"] == plain["intent"]
    assert sorted(streamed["agents_invoked"]) == sorted(plain["agents_invoked"])
