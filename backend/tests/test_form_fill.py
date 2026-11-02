"""Filling the pre-run form by speaking it.

The form has eleven boxes on it. A farmer standing in a field with one hand on a
phone fills in none of them, so the two agents that need those answers said
"missing input" and the most useful part of the system sat behind the least
usable part of it.

What every test here is really protecting is one rule: **nothing is guessed**. A
box the farmer did not speak to stays empty, and empty is honest - the agent then
says what it is missing, which is the design. A box filled with a plausible
number the farmer never said is far worse, because nobody re-reads a box that is
already filled.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.conversation import formfill

TODAY = date(2026, 10, 25)

FIELDS = [
    {"key": "crop", "label": "Crop growing in this field", "type": "select",
     "options": ["wheat", "rice", "maize", "mustard", "tomato", "onion"]},
    {"key": "sowing_date", "label": "Date it was sown", "type": "date"},
    {"key": "stage", "label": "Or the stage you can see", "type": "select",
     "options": ["sowing", "tillering", "flowering", "grain filling", "harvest"]},
    {"key": "symptoms", "label": "What you can see on the plants",
     "type": "multiselect",
     "options": ["yellowing leaves", "brown or black spots", "holes in the leaves",
                 "wilting", "curling leaves", "white powder on the leaves"]},
    {"key": "cost", "label": "Total spend on this crop so far", "type": "number"},
    {"key": "yield_quintal", "label": "Yield you expect", "type": "number"},
    {"key": "price", "label": "Price you expect", "type": "number"},
]


@pytest.fixture
def model(monkeypatch):
    """A scripted Mistral. Returns a setter for what it should answer."""
    import json as _json
    from app.llm import mistral_client

    state = {"values": {}}

    def install(values):
        state["values"] = values

    def transport(url, payload):
        if "input" in payload:
            return {"model": payload.get("model"),
                    "data": [{"index": i, "embedding": [0.1] * 8}
                             for i, _ in enumerate(payload["input"])]}
        return {"model": payload.get("model"),
                "choices": [{"message": {"role": "assistant",
                                         "content": _json.dumps({"values": state["values"]})}}]}

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    mistral_client.set_transport(transport)
    yield install
    mistral_client.set_transport(None)


# --------------------------------------------------- the choices, from the words
@pytest.mark.asyncio
async def test_a_crop_and_a_symptom_are_picked_from_what_was_said(model):
    model({})
    out = await formfill.fill("mere gehu mein peele patte dikh rahe hain",
                              FIELDS, today=TODAY)
    assert out["values"]["crop"] == "wheat"
    assert out["values"]["symptoms"] == "yellowing leaves"


@pytest.mark.asyncio
async def test_several_symptoms_at_once(model):
    model({})
    out = await formfill.fill(
        "yellowing leaves and wilting, also some holes in the leaves",
        FIELDS, today=TODAY)
    picked = out["values"]["symptoms"].split(", ")
    assert set(picked) == {"yellowing leaves", "wilting", "holes in the leaves"}


@pytest.mark.asyncio
async def test_a_symptom_that_was_not_mentioned_is_not_ticked(model):
    model({})
    out = await formfill.fill("curling leaves", FIELDS, today=TODAY)
    assert out["values"]["symptoms"] == "curling leaves"
    assert "yellowing leaves" not in out["values"]["symptoms"]


@pytest.mark.asyncio
async def test_nothing_said_fills_nothing(model):
    model({})
    out = await formfill.fill("", FIELDS, today=TODAY)
    assert out["values"] == {}
    assert set(out["unfilled"]) == {f["key"] for f in FIELDS}


# ----------------------------------------------------------- the numbers
@pytest.mark.asyncio
async def test_a_number_the_farmer_said_goes_in(model):
    model({"cost": "15000", "yield_quintal": "20"})
    out = await formfill.fill("kharcha pandrah hazaar aaya aur bees quintal upaj hogi",
                              FIELDS, today=TODAY)
    assert out["values"]["cost"] == "15000"
    assert out["values"]["yield_quintal"] == "20"


@pytest.mark.asyncio
async def test_a_number_the_farmer_never_said_is_refused(model):
    """The one that matters. Asked for a spend, a model will happily produce a
    typical one - and a farmer who does not re-read the box has just sent the
    agents somebody else's costs."""
    model({"cost": "18000"})
    out = await formfill.fill("gehu bo diya hai", FIELDS, today=TODAY)

    assert "cost" not in out["values"]
    assert "cost" in out["unfilled"]
    assert any("18000" in note for note in out["notes"]), out["notes"]


@pytest.mark.asyncio
async def test_digits_with_a_multiplier_count_as_said(model):
    model({"cost": "12000"})
    out = await formfill.fill("12 hazaar ka kharcha hua", FIELDS, today=TODAY)
    assert out["values"]["cost"] == "12000"


@pytest.mark.asyncio
async def test_a_number_that_is_not_a_number_is_dropped(model):
    model({"price": "about two thousand"})
    out = await formfill.fill("do hazaar ke aas paas", FIELDS, today=TODAY)
    assert "price" not in out["values"]


def test_every_number_in_a_sentence_is_found():
    found = formfill.numbers_said("kharcha pandrah hazaar, upaj 20 quintal, bhav 2300")
    assert 15000.0 in found and 20.0 in found and 2300.0 in found


# -------------------------------------------------------------- the dates
@pytest.mark.asyncio
async def test_a_sowing_date_is_normalised(model):
    model({"sowing_date": "7 November"})
    out = await formfill.fill("saat November ko boya tha", FIELDS, today=TODAY)
    # This November, not last. On 25 October a rabi farmer who says "saat
    # November" means the sowing a fortnight away; reading it as eleven months
    # ago would put the crop at the wrong stage in every agent that uses it.
    assert out["values"]["sowing_date"] == "2026-11-07"


@pytest.mark.asyncio
async def test_a_sowing_date_in_the_future_is_refused(model):
    model({"sowing_date": "2027-03-01"})
    out = await formfill.fill("agle saal boyenge", FIELDS, today=TODAY)
    assert "sowing_date" not in out["values"]
    assert any("sowing date" in note for note in out["notes"])


@pytest.mark.asyncio
async def test_an_unparseable_date_is_refused(model):
    model({"sowing_date": "some time ago"})
    out = await formfill.fill("kaafi pehle boya tha", FIELDS, today=TODAY)
    assert "sowing_date" not in out["values"]


# -------------------------------------------------------- the option guard
@pytest.mark.asyncio
async def test_a_choice_that_is_not_on_the_list_is_refused(model):
    model({"stage": "nearly ready"})
    out = await formfill.fill("fasal lagbhag taiyar hai", FIELDS, today=TODAY)
    assert "stage" not in out["values"]
    assert any("not one of the choices" in note for note in out["notes"])


@pytest.mark.asyncio
async def test_a_key_that_is_not_on_the_form_is_ignored(model):
    model({"secret_field": "x", "crop": "wheat"})
    out = await formfill.fill("gehu", FIELDS, today=TODAY)
    assert "secret_field" not in out["values"]


# ----------------------------------------------------------- the endpoint
def test_the_endpoint_fills_the_chains_own_form(client, monkeypatch):
    import json as _json
    from app.llm import mistral_client

    def transport(url, payload):
        if "input" in payload:
            return {"model": payload.get("model"), "data": []}
        return {"model": payload.get("model"),
                "choices": [{"message": {"role": "assistant", "content":
                                         _json.dumps({"values": {"cost": "15000"}})}}]}

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    mistral_client.set_transport(transport)
    try:
        body = client.post("/api/pipeline/fill-inputs", json={
            "said": "gehu mein peele patte hain aur kharcha pandrah hazaar hua",
            "language": "hi"}).json()
    finally:
        mistral_client.set_transport(None)

    assert body["values"]["crop"] == "wheat"
    assert body["values"]["symptoms"] == "yellowing leaves"
    assert body["values"]["cost"] == "15000"
    assert "price" in body["unfilled"]
    assert body["heard"].startswith("gehu")


def test_the_endpoint_accepts_a_form_it_has_never_seen(client, monkeypatch):
    """So the same voice button works on any form, not only this one."""
    import json as _json
    from app.llm import mistral_client

    def transport(url, payload):
        if "input" in payload:
            return {"model": payload.get("model"), "data": []}
        return {"model": payload.get("model"),
                "choices": [{"message": {"role": "assistant", "content":
                                         _json.dumps({"values": {"note": "paani kam hai"}})}}]}

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    mistral_client.set_transport(transport)
    try:
        body = client.post("/api/pipeline/fill-inputs", json={
            "said": "paani kam hai",
            "fields": [{"key": "note", "label": "Note", "type": "text"}]}).json()
    finally:
        mistral_client.set_transport(None)

    assert body["values"]["note"] == "paani kam hai"


# ------------------------- a choice has to have been said, not merely be legal
@pytest.mark.asyncio
async def test_a_legal_choice_that_was_never_said_is_refused(model):
    """Found live. "gehu hai, saat November ko boya tha, peele patte dikh rahe
    hain, kharcha pandrah hazaar aaya" came back with stage = "germination".
    Nothing in that sentence is about a growth stage - the model picked a
    plausible option off the list, and two agents would have reasoned from it."""
    model({"stage": "tillering"})
    out = await formfill.fill(
        "gehu hai, saat November ko boya tha, peele patte dikh rahe hain",
        FIELDS, today=TODAY)

    assert "stage" not in out["values"], out["values"]
    assert "stage" in out["unfilled"]
    assert any("nothing in what you said matched" in n for n in out["notes"]), out["notes"]


@pytest.mark.asyncio
async def test_a_choice_that_was_said_still_goes_in(model):
    model({"stage": "flowering"})
    out = await formfill.fill("fasal flowering par hai", FIELDS, today=TODAY)
    assert out["values"]["stage"] == "flowering"


# ------------------------------------- the date comes out of the farmer's words
def test_a_date_is_read_from_the_sentence():
    """The year is the part a model gets wrong. The farmer never says one, so
    reading the day and month out of the sentence is the surer route."""
    assert formfill.date_in("saat November ko boya tha", "", TODAY) == "2026-11-07"
    assert formfill.date_in("boya tha 12 November ko", "", TODAY) == "2026-11-12"
    assert formfill.date_in("15 October ko boya", "", TODAY) == "2026-10-15"


def test_a_sentence_with_no_date_in_it_yields_none():
    assert formfill.date_in("gehu mein peele patte hain", "", TODAY) is None
    assert formfill.date_in("kharcha pandrah hazaar", "", TODAY) is None


@pytest.mark.asyncio
async def test_the_model_inventing_a_year_does_not_lose_the_date(model):
    """Live, the model answered "2023-11-07" for "saat November" - three years
    out. The date is still recoverable, because the farmer said it."""
    model({"sowing_date": "2023-11-07"})
    out = await formfill.fill("saat November ko boya tha", FIELDS, today=TODAY)
    assert out["values"]["sowing_date"] == "2026-11-07"


def test_joining_words_in_an_option_label_are_not_required():
    """"white powder on the leaves" was refused for a farmer who said "patte pe
    safed powder hai". "the" is four letters long, so it counted as a word that
    had to be matched - and nothing in Hinglish was ever going to contain it."""
    from app.conversation import intent as intent_module

    said = "patte pe safed powder hai"
    normalised = intent_module.normalise_query(said)
    assert formfill._matches_option("white powder on the leaves", said, normalised)
    # And it still does not match something that was not said.
    assert not formfill._matches_option("holes in the leaves", said, normalised)
