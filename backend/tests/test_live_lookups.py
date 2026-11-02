"""A spoken question about any crop, answered with a real number.

This file is here because of one call. Asked "tomato ka mandi bhav kya hai",
Saarthi said it would check and come back, and then the line re-greeted the
caller by name and the conversation started over. Three separate faults, all
covered below:

1. nothing ever looked tomato up - the call path only knew the caller's own field
2. the model was INSTRUCTED to promise something nothing could deliver
3. silence mid-call was treated as a brand new call

The tests use a fake market service rather than data.gov.in, because a test that
depends on today's tomato harvest is a test that fails on a bad day for tomatoes.
What is asserted is the wiring: that the question's own crop reaches the lookup,
that the figures reach the prompt, and that no promise is made.
"""
from __future__ import annotations

import pytest

from app.conversation import intent, lookups
from app.core import farmers
from app.core.schemas import FarmContext


WHEAT_FARM = FarmContext(farmer_name="Ramesh", village="Rampur", district="Agra",
                         state="Uttar Pradesh", crop="wheat")


def _priced(commodity: str, median: float = 1500.0, scope: str = "national"):
    return {
        "available": True, "commodity": commodity.title(), "scope": scope,
        "source": "data.gov.in Agmarknet", "record_count": 17,
        "median_modal": median,
        "highest": {"market": "Palamaner APMC", "district": "Chittor",
                    "state": "Andhra Pradesh", "modal_price": 2700.0,
                    "arrival_date": "25/10/2026", "unit": "INR per quintal"},
        "lowest": {"market": "Katni APMC", "district": "Katni",
                   "state": "Madhya Pradesh", "modal_price": 366.0,
                   "arrival_date": "25/10/2026", "unit": "INR per quintal"},
    }


# --------------------------------------------------- what was asked about
@pytest.mark.parametrize("said,expected", [
    ("tomato ka mandi bhav kya hai", "tomato"),
    ("what is the price of onion today", "onion"),
    ("aloo ka daam bataiye", "potato"),
    ("टमाटर का भाव क्या है", "tomato"),
    ("प्याज का मंडी भाव बताओ", "onion"),
    ("gehu ka bhav", "wheat"),
    ("soybean rate kya chal raha hai", "soybean"),
])
def test_the_crop_in_the_question_is_the_one_looked_up(said, expected):
    detected = intent.detect(said)
    found = lookups.commodity_in(said, detected.normalised_query)
    assert found == expected, "%r -> %r" % (said, found)


def test_a_question_with_no_crop_falls_back_to_the_farmers_own():
    detected = intent.detect("mandi bhav kya hai")
    assert lookups.commodity_in("mandi bhav kya hai", detected.normalised_query) is None


def test_devanagari_crop_names_now_reach_the_entities():
    """The bug underneath the bug: entities were read off the raw text while the
    Hindi was only in the normalised one, so spoken Hindi named no crop at all."""
    assert intent.detect("टमाटर का भाव क्या है").entities.get("crop") == "tomato"
    assert intent.detect("आलू का दाम बताओ").entities.get("crop") == "potato"


@pytest.mark.parametrize("said,state", [
    ("tomato price in Maharashtra", "Maharashtra"),
    ("punjab mein gehu ka bhav", "Punjab"),
    ("what is the onion rate", None),
])
def test_a_state_named_in_the_question_narrows_the_search(said, state):
    assert lookups.state_in(said) == state


# ------------------------------------------------------- the lookup itself
@pytest.mark.asyncio
async def test_a_price_question_produces_real_figures_not_a_promise(monkeypatch):
    asked = {}

    async def fake_summary(commodity, state=None, district=None):
        asked.update(commodity=commodity, state=state, district=district)
        return _priced(commodity)

    monkeypatch.setattr(lookups.market_service, "get_summary", fake_summary)
    detected = intent.detect("tomato ka mandi bhav kya hai")
    found = await lookups.mandi("tomato ka mandi bhav kya hai",
                                normalised=detected.normalised_query, farm=WHEAT_FARM)

    assert asked["commodity"] == "tomato", "it looked up the wrong crop"
    assert found.found and not found.gaps
    block = found.as_prompt()
    assert "1500.0" in block and "INR per quintal" in block
    assert "per quintal, never per kilo" in block, "the unit rule was dropped"


@pytest.mark.asyncio
async def test_a_question_about_my_own_crop_narrows_to_my_district(monkeypatch):
    asked = {}

    async def fake_summary(commodity, state=None, district=None):
        asked.update(commodity=commodity, state=state, district=district)
        return _priced(commodity, scope="district")

    monkeypatch.setattr(lookups.market_service, "get_summary", fake_summary)
    await lookups.mandi("mere gehu ka bhav kya hai", normalised="mere gehu ka bhav wheat price",
                        farm=WHEAT_FARM)
    assert asked["district"] == "Agra", asked
    assert asked["state"] == "Uttar Pradesh"


@pytest.mark.asyncio
async def test_a_price_somewhere_else_is_not_narrowed_to_my_district(monkeypatch):
    asked = {}

    async def fake_summary(commodity, state=None, district=None):
        asked.update(commodity=commodity, state=state, district=district)
        return _priced(commodity)

    monkeypatch.setattr(lookups.market_service, "get_summary", fake_summary)
    await lookups.mandi("tomato price in Maharashtra",
                        normalised="tomato price in Maharashtra", farm=WHEAT_FARM)
    assert asked["state"] == "Maharashtra"
    assert asked["district"] is None, "it searched a district the farmer never named"


@pytest.mark.asyncio
async def test_a_source_with_nothing_says_so_instead_of_promising(monkeypatch):
    async def nothing(commodity, state=None, district=None):
        return {"available": False, "commodity": "Tomato",
                "reason": "No price records returned for this commodity and filter.",
                "source": "data.gov.in Agmarknet"}

    monkeypatch.setattr(lookups.market_service, "get_summary", nothing)
    found = await lookups.mandi("tomato ka bhav", normalised="tomato ka bhav price",
                                farm=WHEAT_FARM)

    assert not found.found and found.gaps
    block = found.as_prompt()
    assert "not available" in block
    assert "do NOT say you will check or come back with it" in block


@pytest.mark.asyncio
async def test_a_slow_source_costs_the_figure_and_not_the_answer(monkeypatch):
    import asyncio

    async def never(commodity, state=None, district=None):
        await asyncio.sleep(5)
        return _priced(commodity)

    monkeypatch.setattr(lookups.market_service, "get_summary", never)
    detected = intent.detect("tomato ka mandi bhav")
    found = await lookups.for_question("tomato ka mandi bhav", detection=detected,
                                       farm=WHEAT_FARM, budget=0.2)
    # Nothing found, and no gap either: a gap means the source had nothing to
    # say, which is an answer. Being slow is not the farmer's problem to hear.
    assert not found.found and not found.gaps


@pytest.mark.asyncio
async def test_a_price_asked_inside_another_question_is_still_looked_up(monkeypatch):
    async def fake_summary(commodity, state=None, district=None):
        return _priced(commodity)

    monkeypatch.setattr(lookups.market_service, "get_summary", fake_summary)
    said = "gehu peela ho raha hai aur mandi bhav bhi bata do"
    detected = intent.detect(said)
    found = await lookups.for_question(said, detection=detected, farm=WHEAT_FARM)
    assert found.found, "a price asked in passing was dropped (intent was %r)" % detected.intent


# ------------------------------------------- the turn, end to end, no promise
@pytest.mark.asyncio
async def test_the_spoken_turn_says_the_real_price(monkeypatch, fake_llm):
    from app.conversation.voice_agent import voice_agent

    async def fake_summary(commodity, state=None, district=None):
        return _priced(commodity)

    monkeypatch.setattr(lookups.market_service, "get_summary", fake_summary)
    farmers.ensure_schema()
    turn = await voice_agent.respond(phone="9876500071",
                                     said="tomato ka mandi bhav kya hai",
                                     call_id="lookup-call-1", language="hi")
    for task in turn.background:
        task.cancel()

    assert turn.reply
    # The chain is not started when the figure is already in hand: it is eleven
    # seconds of work whose findings nobody would ever hear.
    assert len(turn.background) == 2, [t.get_name() for t in turn.background]


@pytest.mark.asyncio
async def test_with_no_figures_it_never_promises_to_come_back(monkeypatch, fake_llm):
    """The sentence that broke the call. Nothing on this line can speak
    unprompted, so "I will check and come back" leaves the caller waiting in a
    silence that Twilio reads as a finished call."""
    from app.conversation import voice_agent as module

    seen = {}

    async def capture(self, choice, messages, **kwargs):
        seen["system"] = messages[0]["content"]
        return ("Theek hai.", "test", [])

    monkeypatch.setattr(module.VoiceAgent, "_ask", capture)

    # Nothing found anywhere: no cached snapshot, no live lookup. That is the
    # only state in which this branch of the prompt is reached, and it is worth
    # arranging explicitly - the snapshot cache is module-level and outlives a
    # test, and the knowledge base now answers most soil questions outright.
    async def found_nothing(said, **kwargs):
        return lookups.Lookup()

    monkeypatch.setattr(lookups, "for_question", found_nothing)

    from app.conversation import snapshot as snapshot_module
    monkeypatch.setattr(snapshot_module, "_cache", {})
    farmers.ensure_schema()
    turn = await module.voice_agent.respond(
        phone="9876500072", said="meri mitti mein nitrogen kitna hai",
        call_id="lookup-call-2", language="hi")
    for task in turn.background:
        task.cancel()

    system = seen["system"]
    assert "Do NOT say you will check" in system
    assert "ask again in a moment" in system


def test_the_withheld_reply_does_not_promise_either():
    from app.conversation.voice_agent import NO_FIGURE_REPLY

    for promise in ("dekh kar", "bata deta hoon", "come back", "call back"):
        assert promise not in NO_FIGURE_REPLY.lower(), NO_FIGURE_REPLY
    assert "dobara poochh" in NO_FIGURE_REPLY


# ------------------------------------------------- the name is never the price of an answer
@pytest.mark.parametrize("said,expected", [
    ("Maharashtra mein pyaaz ka bhav kya hai", "onion"),
    ("tamatar ka rate", "tomato"),
    ("bhindi ka mandi bhav", "okra"),
    ("moong ka daam bataiye", "moong"),
    ("masoor ki keemat", "lentil"),
    ("haldi ka bhav kya chal raha hai", "turmeric"),
])
def test_romanised_hindi_crop_names_are_looked_up(said, expected):
    """Twilio's recogniser hands back Hinglish in Latin script for a caller
    speaking Hindi, and the romanised half of the lexicon had vegetables
    missing - so "pyaaz ka bhav" was answered "I do not have the onion rate"
    with the rate one HTTP call away."""
    detected = intent.detect(said)
    assert lookups.commodity_in(said, detected.normalised_query) == expected


def test_a_new_caller_is_told_to_answer_before_asking_their_name():
    from app.conversation.voice_agent import memory_prompt

    fresh = memory_prompt({"known": False})
    assert "ANSWER WHAT THEY ASKED FIRST" in fresh
    assert "never be a condition for answering" in fresh
    assert "may ask their name once" in fresh


def test_once_the_name_has_been_asked_it_is_not_asked_again():
    from app.conversation.voice_agent import memory_prompt

    again = memory_prompt({"known": False, "name_already_asked": True})
    assert "Do NOT ask again" in again
    assert "may ask their name once" not in again


@pytest.mark.parametrize("spoken,asked", [
    ("नमस्ते, मैं सारथी हूँ, खेती की हेल्पलाइन। आपका नाम क्या है?", True),
    ("Kya aap apna naam bataenge?", True),
    ("Namaste, this is Saarthi. Your name please?", True),
    ("Haan ji, Tomato ka rate 3200 INR per quintal chal raha hai.", False),
])
def test_an_earlier_name_request_is_recognised(spoken, asked):
    from app.conversation.voice_agent import _ASKED_A_NAME

    assert bool(_ASKED_A_NAME.search(spoken)) is asked


def test_the_persona_does_not_promise_to_come_back():
    from app.conversation.voice_agent import PERSONA

    assert "say you will check" not in PERSONA
    assert "that promise is never kept" in PERSONA


def test_asking_a_price_does_not_change_what_the_farmer_grows():
    """A live call did this: "Maharashtra mein pyaaz ka bhav kya hai" set the
    farm crop to onion, and the next turn asked a wheat farmer how their onion
    crop was doing. The crop in a price question is the subject of the question,
    not a statement about their field - which comes from fact extraction."""
    from app.conversation import manager

    session = manager.load("crop-test-1", language="hi", channel="call")
    session.farm.crop = "wheat"

    manager.update_memory(session, intent.detect("Maharashtra mein pyaaz ka bhav kya hai"))
    assert session.farm.crop == "wheat", "a price question rewrote their field"

    # But telling Saarthi about the crop still does change it.
    manager.update_memory(session, intent.detect("meri pyaaz ki fasal mein keeda lag gaya hai"))
    assert session.farm.crop == "onion"
