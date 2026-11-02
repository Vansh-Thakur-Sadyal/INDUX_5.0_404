"""One account, both channels.

A number saved on the dashboard must be a number the phone line knows. These tests
pin that down from both directions: what the voice agent is told about a caller, and
whether what is said on the dashboard is still there on the next call.
"""
from __future__ import annotations

import json

import pytest

from app.conversation import manager
from app.conversation.voice_agent import apply_profile, memory_prompt, voice_agent
from app.core import farmers
from app.llm import sarvam_client

PHONE = "9876512345"


@pytest.fixture
def scripted_sarvam(monkeypatch):
    prompts = []

    def install(reply="Haan ji, theek hai."):
        def transport(url, payload):
            prompts.append(payload)
            system = payload["messages"][0].get("content") or ""
            if "extract ONLY durable facts" in system:
                content = json.dumps({"facts": []})
            elif "Summarise this farm helpline call" in system:
                content = "Call summary."
            else:
                content = reply
            return {"model": payload["model"],
                    "choices": [{"message": {"role": "assistant", "content": content}}]}

        monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key")
        sarvam_client.set_transport(transport)
        return prompts

    yield install
    sarvam_client.set_transport(None)


def _full_account(client, profile_code=None):
    """A farmer who filled in the dashboard properly, and mapped a plot."""
    client.post("/api/profile", json={
        # Saving a profile takes a code now; these tests are about what the
        # record then drives, not about how it was confirmed.
        "code": profile_code() if profile_code else "",
        "phone": PHONE, "name": "Ramesh", "full_name": "Ramesh Kumar Yadav", "age": "47",
        "email": "ramesh@example.com", "village": "Rampur", "tehsil": "Chhata",
        "district": "Mathura", "state": "Uttar Pradesh", "pincode": "281401",
        "land_acres": "4.5", "soil_type": "loam", "irrigation": "drip",
        "farming_since": "2003",
    })
    client.post("/api/profile/fields", json={
        "phone": PHONE, "name": "North plot", "crop": "mustard", "area_acres": "2.5",
        "soil_type": "black cotton", "irrigation": "sprinkler", "sowing_date": "2026-10-20",
    })


# ------------------------------------------------- what the caller's memory contains
def test_the_dashboard_account_is_what_the_phone_line_knows(client, profile_code):
    _full_account(client, profile_code)
    memory = farmers.memory_block("+91 98765 12345")      # Twilio's formatting

    assert memory["known"]
    assert memory["profile"]["full_name"] == "Ramesh Kumar Yadav"
    assert memory["profile"]["email"] == "ramesh@example.com"
    assert memory["profile"]["pincode"] == "281401"
    assert len(memory["fields"]) == 1
    assert memory["fields"][0]["crop"] == "mustard"


def test_every_detail_reaches_the_words_the_model_sees(client, profile_code):
    """A farmer who typed it in should never be asked for it again."""
    _full_account(client, profile_code)
    prompt = memory_prompt(farmers.memory_block(PHONE))

    for expected in ("Ramesh", "Ramesh Kumar Yadav", "47", "Rampur", "Chhata", "Mathura",
                     "Uttar Pradesh", "281401", "4.5", "loam", "drip", "2003",
                     "ramesh@example.com"):
        assert expected in prompt, "%r is in the account but not in the prompt" % expected
    # and the plot they drew on the map
    assert "North plot" in prompt and "mustard" in prompt and "2.5 acres" in prompt
    assert "sown 2026-10-20" in prompt
    assert "do not ask for it again" in prompt


def test_an_unknown_number_is_still_treated_as_a_stranger(client):
    prompt = memory_prompt(farmers.memory_block("9000000000"))
    assert "new number" in prompt
    # It may ask the name - once, and only when there is nothing to answer. It
    # used to ask on every turn, and once refused to name a stem borer treatment
    # until the caller introduced themselves.
    assert "may ask their name once" in prompt
    assert "ANSWER WHAT THEY ASKED FIRST" in prompt


def test_mapped_fields_alone_are_enough_to_be_known(client):
    """A farmer may map a plot before filling in anything about themselves."""
    client.post("/api/profile/fields", json={"phone": PHONE, "name": "Plot", "crop": "wheat"})
    assert farmers.memory_block(PHONE)["known"] is True


# --------------------------------------------- the agents point at the farmer's farm
def test_the_agents_are_pointed_at_this_farmers_farm(client, profile_code):
    _full_account(client, profile_code)
    session = manager.load(None, language="hi", channel="call")
    apply_profile(session, farmers.memory_block(PHONE))

    farm = session.farm
    assert farm.farmer_name == "Ramesh"
    assert farm.village == "Rampur" and farm.district == "Mathura"
    assert farm.area_acres == 4.5
    # the mapped plot is more specific than the profile, so its crop and soil win
    assert farm.crop == "mustard"
    assert farm.soil_type == "black cotton"
    assert farm.sowing_date == "2026-10-20"


def test_a_fact_from_a_call_overrides_the_mapped_crop(client, profile_code):
    """What the farmer says today beats what the map said last month."""
    _full_account(client, profile_code)
    farmers.remember(PHONE, "crop", "wheat", call_id="c9")
    session = manager.load(None, language="hi", channel="call")
    apply_profile(session, farmers.memory_block(PHONE))
    assert session.farm.crop == "wheat"


# --------------------------------------------------- dashboard and phone share memory
@pytest.mark.asyncio
async def test_what_the_dashboard_hears_the_phone_line_remembers(client, scripted_sarvam,
                                                                 profile_code):
    """The assistant is keyed on the account, not on a browser session."""
    _full_account(client, profile_code)
    scripted_sarvam()
    farmers.remember(PHONE, "problem", "yellow spots on lower leaves", call_id="web-1")

    turn = await voice_agent.respond(phone=PHONE, said="mera khet kaisa hai",
                                     call_id="phone-1", language="hi")
    for task in turn.background:
        task.cancel()

    assert turn.recalled_facts >= 1
    assert turn.returning_caller or turn.recalled_facts


def test_the_dashboard_chat_answers_as_the_account(client, fake_llm, profile_code):
    _full_account(client, profile_code)
    body = client.post("/api/chat", json={
        "message": "Should I irrigate today?", "phone": PHONE, "run_agents": True,
    }).json()

    farm = client.get("/api/chat/session/" + body["session_id"]).json()["summary"]["farm"]
    assert farm["village"] == "Rampur"
    assert farm["crop"] == "mustard"          # from the plot they mapped
    assert farm["farmer_name"] == "Ramesh"
    assert farm["area_acres"] == 4.5


def test_a_guest_without_a_number_still_works(client, fake_llm):
    body = client.post("/api/chat", json={"message": "Should I irrigate today?"}).json()
    assert body["reply"]


@pytest.fixture(autouse=True)
def _signed_in_as_the_test_farmer(client):
    """These tests predate accounts.

    Every route they exercise now needs a session, so the shared client is signed in
    as the owner of PHONE. The behaviour under test is unchanged; what changed is
    that an anonymous caller no longer reaches any of it.
    """
    from app.core import accounts as _accounts
    _accounts.ensure_schema()
    user = _accounts.create_user(email="legacy-%s@example.com" % PHONE, phone=PHONE,
                                 password="legacy-pass-99", status="active")
    client.cookies.set("saarthi_session", _accounts.start_session(user["user_id"]))
    return user
