"""The phone conversation and the farmer memory behind it.

The language model is stubbed, so these assert the behaviour the farmer actually
experiences: the second call knows them, a bad extraction cannot erase a good fact,
and nothing is claimed that was not said.

The stub is Mistral because Mistral is now the only model that writes words here.
Sarvam remains the ears and the voice - speech to text and text to speech - and is
stubbed separately in the speech tests.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.conversation import voice_agent as voice_module
from app.conversation.voice_agent import voice_agent
from app.core import farmers
from app.llm import mistral_client

PHONE = "+91 98765 43210"


@pytest.fixture(autouse=True)
def schema():
    farmers.ensure_schema()


@pytest.fixture
def fake_model(monkeypatch):
    """Install a scripted Mistral. Returns the list of prompts it received."""
    calls = []

    def install(reply="Haan ji, theek hai.", extraction=None):
        def transport(url, payload):
            # Embeddings are not prompts and are not recorded as such. The
            # spoken path retrieves from the knowledge base now, so an
            # embeddings payload reaches this transport on most turns - and it
            # has `input`, not `messages`, which used to raise straight through
            # the client as "embeddings failed" and shifted every calls[0] in
            # this file onto the wrong call.
            if "input" in payload:
                rows = [{"index": i, "embedding": [0.01 * (i + 1)] * 1024}
                        for i, _ in enumerate(payload["input"])]
                return {"model": payload.get("model"), "data": rows}

            calls.append(payload)
            system = (payload["messages"][0].get("content") or "")
            is_extraction = "extract ONLY durable facts" in system
            is_summary = "Summarise this farm helpline call" in system
            if is_extraction:
                content = json.dumps(extraction if extraction is not None else {"facts": []})
            elif is_summary:
                content = "Farmer asked about wheat irrigation."
            else:
                content = reply
            return {"model": payload["model"],
                    "choices": [{"message": {"role": "assistant", "content": content}}]}

        monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")
        mistral_client.set_transport(transport)
        return calls

    yield install
    mistral_client.set_transport(None)


# ------------------------------------------------------------------ memory
def test_phone_numbers_normalise_to_one_identity():
    assert farmers.normalise_phone("+91 98765 43210") == "9876543210"
    assert farmers.normalise_phone("09876543210") == "9876543210"
    assert farmers.normalise_phone("9876543210") == "9876543210"


def test_profile_and_facts_round_trip():
    farmers.upsert_farmer(PHONE, name="Ramesh", village="Rampur", land_acres=2.0)
    assert farmers.remember(PHONE, "crop", "wheat", call_id="C1") is True
    assert farmers.remember(PHONE, "sowing_date", "10 November", call_id="C1") is True
    memory = farmers.memory_block(PHONE)
    assert memory["known"] is True
    assert memory["profile"]["name"] == "Ramesh"
    assert {f["key"] for f in memory["facts"]} == {"crop", "sowing_date"}


def test_only_allowed_fact_keys_are_kept():
    assert farmers.remember(PHONE, "favourite_colour", "blue") is False
    assert farmers.remember(PHONE, "crop", "") is False
    assert farmers.remember(PHONE, "crop", "x" * 300) is False


def test_a_later_call_corrects_an_earlier_fact():
    farmers.remember(PHONE, "crop", "wheat", call_id="C1")
    farmers.remember(PHONE, "crop", "mustard", call_id="C2")
    facts = {f["key"]: f["value"] for f in farmers.recall(PHONE)}
    assert facts["crop"] == "mustard"


def test_a_farmer_can_have_a_fact_forgotten():
    farmers.remember(PHONE, "crop", "wheat")
    assert farmers.forget(PHONE, "crop") is True
    assert farmers.recall(PHONE) == []


def test_call_transcript_is_stored_turn_by_turn():
    farmers.append_turn("C9", PHONE, "paani kab de", "do din baad")
    farmers.append_turn("C9", PHONE, "theek hai", "ji")
    transcript = farmers.last_transcript("C9")
    assert len(transcript) == 2
    assert transcript[0]["farmer"] == "paani kab de"
    assert transcript[1]["saarthi"] == "ji"


# ---------------------------------------------------------------- the turn
@pytest.mark.asyncio
async def test_first_turn_stores_what_the_farmer_introduced(fake_model):
    """Extraction happens AFTER the farmer is answered, so await the background work."""
    fake_model(reply="Namaste Ramesh ji.",
                extraction={"name": "Ramesh Kumar", "village": "Rampur",
                            "facts": [{"key": "crop", "value": "wheat"}]})
    turn = await voice_agent.respond(phone=PHONE, said="Mera naam Ramesh Kumar hai",
                                     call_id="CALL-A", language="hi")
    assert turn.reply == "Namaste Ramesh ji."
    assert turn.source == "mistral"
    await asyncio.gather(*turn.background)
    assert farmers.get_farmer(PHONE)["name"] == "Ramesh Kumar"
    assert {f["key"]: f["value"] for f in farmers.recall(PHONE)}["crop"] == "wheat"


@pytest.mark.asyncio
async def test_a_bad_extraction_cannot_overwrite_a_known_name(fake_model):
    """Regression: "Bijai maine das November ko ki thi" (bijai = sowing) was once
    stored as the farmer's name, destroying "Ramesh Kumar"."""
    farmers.upsert_farmer(PHONE, name="Ramesh Kumar")
    fake_model(reply="Theek hai.",
                extraction={"name": "Bijai",
                            "facts": [{"key": "sowing_date", "value": "10 November"}]})
    turn = await voice_agent.respond(phone=PHONE, said="Bijai maine das November ko ki thi",
                                     call_id="CALL-B", language="hi")
    await asyncio.gather(*turn.background)
    assert farmers.get_farmer(PHONE)["name"] == "Ramesh Kumar"
    assert {f["key"] for f in farmers.recall(PHONE)} == {"sowing_date"}


@pytest.mark.asyncio
async def test_the_second_call_is_given_the_memory_in_its_prompt(fake_model):
    farmers.upsert_farmer(PHONE, name="Ramesh", village="Rampur")
    farmers.remember(PHONE, "crop", "wheat")
    farmers.remember(PHONE, "sowing_date", "10 November")
    farmers.record_call_started(PHONE, "CALL-OLD")
    prompts = fake_model(reply="Haan Ramesh ji.")

    await voice_agent.respond(phone=PHONE, said="phir se baat kar raha hoon",
                              call_id="CALL-NEW", language="hi")
    system = prompts[0]["messages"][0]["content"]
    assert "Ramesh" in system
    assert "crop = wheat" in system
    assert "sowing date = 10 November" in system
    assert "WHAT YOU KNOW ABOUT THIS CALLER" in system


@pytest.mark.asyncio
async def test_an_unknown_number_is_told_it_is_unknown(fake_model):
    prompts = fake_model(reply="Namaste.")
    await voice_agent.respond(phone="+91 90000 00001", said="namaste",
                              call_id="CALL-NEW2", language="hi")
    assert "nothing yet - this is a new number" in prompts[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_small_talk_does_not_run_the_agent_chain(fake_model):
    fake_model(reply="Haan ji, bataiye.")
    turn = await voice_agent.respond(phone=PHONE, said="namaste ji kaise hain",
                                     call_id="CALL-C", language="hi")
    assert turn.used_agents is False
    assert turn.agents_invoked == []


@pytest.mark.asyncio
async def test_an_agronomic_question_schedules_the_agents_off_the_critical_path(fake_model):
    """Running the chain inline measured 11 s, past Twilio's patience. It now runs
    after the farmer has been answered, and feeds the NEXT turn."""
    fake_model(reply="Abhi paani mat dijiye.")
    turn = await voice_agent.respond(phone=PHONE, said="Mujhe paani kab dena chahiye?",
                                     call_id="CALL-D", language="hi")
    assert turn.reply                        # answered immediately
    assert turn.used_agents is False         # not on the critical path any more
    # extraction, snapshot refresh, agent chain. The chain still belongs here:
    # a live forecast lookup now rides along with an irrigation question, but a
    # forecast is one input to "when should I water", not the answer - that
    # needs the soil, the crop stage and ET as well.
    assert len(turn.background) >= 3
    for task in turn.background:
        task.cancel()


@pytest.mark.asyncio
async def test_the_farm_snapshot_is_handed_to_the_speaking_model(fake_model):
    """The numbers reach the model through the cached snapshot, not a live chain."""
    from app.conversation import snapshot as snapshot_module
    from app.core.schemas import FarmContext

    snapshot_module.clear()
    farm = FarmContext()
    snap = await snapshot_module.get(farm)
    snap.lines = ["Soil sensor: Soil moisture 30.6 % (optimal)"]
    snap.agent_lines = ["Irrigation: no irrigation needed yet"]

    prompts = fake_model(reply="Abhi paani mat dijiye.")
    turn = await voice_agent.respond(phone=PHONE, said="paani kab dena chahiye",
                                     call_id="CALL-E", language="hi")
    for task in turn.background:
        task.cancel()
    system = prompts[0]["messages"][0]["content"]
    assert "FARM DATA" in system
    assert "30.6" in system
    assert "not listed here" in system          # no numbers beyond the snapshot
    assert "per quintal" in system              # and no silent unit conversion


@pytest.mark.asyncio
async def test_markdown_is_stripped_because_the_reply_is_spoken(fake_model):
    fake_model(reply="**Paani** ab mat dijiye.\n\n- do din baad dekhiye")
    turn = await voice_agent.respond(phone=PHONE, said="namaste", call_id="CALL-F")
    assert "*" not in turn.reply and "- " not in turn.reply


@pytest.mark.asyncio
async def test_a_dead_model_does_not_kill_the_call(monkeypatch):
    """Both providers down is the only way the call runs out of models.

    One of them failing is survivable - that is what the other is for, and it is
    tested in test_model_fusion.py. This is the floor below that: when nothing
    can be reached, the caller must still hear a sentence, and it must admit the
    answer is missing rather than invent agronomy.
    """
    from app.llm import sarvam_client

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setenv("SARVAM_API_KEY", "test-key")

    def broken(url, payload):
        raise RuntimeError("HTTP 503: upstream down")

    mistral_client.set_transport(broken)
    sarvam_client.set_transport(broken)
    try:
        turn = await voice_agent.respond(phone=PHONE, said="namaste", call_id="CALL-G")
    finally:
        mistral_client.set_transport(None)
        sarvam_client.set_transport(None)
    assert turn.reply                                   # the caller still hears something
    assert turn.source == "unavailable"
    assert any("No language model was reachable" in n for n in turn.notices)
    assert "koshish" in turn.reply, "the caller should be told to try again"


@pytest.mark.asyncio
async def test_call_summary_is_written_for_the_next_call(fake_model):
    fake_model(reply="Theek hai.")
    await voice_agent.respond(phone=PHONE, said="paani ki baat", call_id="CALL-H")
    summary = await voice_agent.summarise_call("CALL-H", PHONE)
    assert summary == "Farmer asked about wheat irrigation."
    assert farmers.call_history(PHONE)[0]["summary"] == summary


# ------------------------------------------------------------- the endpoint
def test_voice_turn_requires_the_shared_secret(client):
    body = {"phone": PHONE, "transcript": "namaste", "call_id": "CALL-X"}
    assert client.post("/api/voice/turn", json=body).status_code == 401
    assert client.post("/api/voice/turn", json=body,
                       headers={"X-Saarthi-Token": "wrong"}).status_code == 401


def test_voice_turn_greets_an_empty_transcript(client, fake_model):
    fake_model()
    body = client.post("/api/voice/turn",
                       json={"phone": "+91 90000 00009", "transcript": "",
                             "call_id": "CALL-Y", "language": "hi", "speak": False},
                       headers={"X-Saarthi-Token": "test-secret"}).json()
    assert body["reply"]
    assert body["next_action"] == "gather_speech"
    assert body["returning_caller"] is False


def test_voice_turn_returns_a_spoken_reply(client, fake_model):
    fake_model(reply="Haan ji, bataiye.")
    body = client.post("/api/voice/turn",
                       json={"phone": PHONE, "transcript": "namaste ji",
                             "call_id": "CALL-Z", "language": "hi", "speak": False},
                       headers={"X-Saarthi-Token": "test-secret"}).json()
    assert body["reply"] == "Haan ji, bataiye."
    assert body["call_id"] == "CALL-Z"
    assert body["end_call"] is False


def test_farmer_api_exposes_and_corrects_memory(client, fake_model):
    farmers.upsert_farmer(PHONE, name="Ramesh", village="Rampur")
    farmers.remember(PHONE, "crop", "wheat")
    detail = client.get("/api/farmers/9876543210").json()
    assert detail["profile"]["name"] == "Ramesh"
    assert detail["facts"][0]["key"] == "crop"

    client.post("/api/farmers/9876543210", json={"village": "Sikandra"})
    assert client.get("/api/farmers/9876543210").json()["profile"]["village"] == "Sikandra"

    removed = client.delete("/api/farmers/9876543210/facts/crop").json()
    assert removed["removed"] is True and removed["facts"] == []


def test_unknown_farmer_is_a_404(client):
    assert client.get("/api/farmers/9999999999").status_code == 404


def test_audio_urls_handed_to_twilio_are_absolute(client, fake_model, monkeypatch):
    """Twilio fetches <Play> from its own servers: a relative path plays nothing."""
    monkeypatch.setenv("SAARTHI_PUBLIC_URL", "https://example.test")

    class _Spoken:
        ok, url, provider, voice, reason, path = True, "/api/speech/audio/x.wav", "sarvam", "priya", "", None

    async def fake_tts(*args, **kwargs):
        return _Spoken()

    from app.speech import voice_io
    monkeypatch.setattr(voice_io, "synthesize", fake_tts)
    fake_model(reply="Haan ji.")

    greeting = client.post("/api/voice/turn",
                           json={"phone": "+91 90000 11111", "transcript": "",
                                 "call_id": "CALL-ABS1"},
                           headers={"X-Saarthi-Token": "test-secret"}).json()
    assert greeting["audio_url"].startswith("https://example.test/")

    spoken = client.post("/api/voice/turn",
                         json={"phone": "+91 90000 11111", "transcript": "namaste",
                               "call_id": "CALL-ABS1", "resumed": True},
                         headers={"X-Saarthi-Token": "test-secret"}).json()
    assert spoken["audio_url"].startswith("https://example.test/")


# ---------------------------------------------- guards learned from a real call
def test_a_whole_sentence_is_not_stored_as_a_categorical_fact():
    """A live call stored a full sentence as BOTH soil_type and problem."""
    long_sentence = "there is far too little water in the soil of this field right now"
    assert farmers.remember(PHONE, "soil_type", long_sentence) is False
    assert farmers.remember(PHONE, "soil_type", "alluvial") is True
    # 'problem' is allowed to be a phrase, but still not an essay
    assert farmers.remember(PHONE, "problem", long_sentence) is True
    assert farmers.remember(PHONE, "problem", "x" * 300) is False


def test_broken_characters_from_speech_are_not_stored():
    """Truncated UTF-8 from a transcript must not reach the database."""
    cleaned = farmers.clean_text("soil \udc8d moisture")
    assert "\udc8d" not in cleaned
    assert "soil" in cleaned and "moisture" in cleaned
    cleaned.encode("utf-8")                 # must not raise, which is the whole point
    farmers.remember(PHONE, "crop", "gehu\udc8d")
    stored = {f["key"]: f["value"] for f in farmers.recall(PHONE)}
    assert "\udc8d" not in stored.get("crop", "")


def test_dashboard_conversational_mode_uses_the_phone_engine(client, fake_model):
    """Same engine, same memory - the toggle on the AI Assistant page."""
    farmers.upsert_farmer("dashboard-guest", name="Ramesh")
    farmers.remember("dashboard-guest", "crop", "wheat")
    prompts = fake_model(reply="Haan Ramesh ji, gehu ki baat karte hain.")

    body = client.post("/api/chat", json={"message": "mera khet kaisa hai",
                                          "conversational": True, "speak": False}).json()
    assert body["reply"] == "Haan Ramesh ji, gehu ki baat karte hain."
    assert body["advisory"] is None          # conversation mode skips the heavy trace
    assert any("remembered" in n for n in body["notices"])
    assert "crop = wheat" in prompts[0]["messages"][0]["content"]


def test_dashboard_default_mode_still_runs_the_full_chain(client, fake_model):
    fake_model()
    body = client.post("/api/chat", json={"message": "Should I irrigate my wheat today?"}).json()
    assert body["advisory"] is not None
    assert body["advisory"]["agents_invoked"]
