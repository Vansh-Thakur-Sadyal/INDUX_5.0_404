"""Saarthi answering about the screen, from the data the screen is drawn from.

Two complaints, one cause. Asked "what is today's weather" while the Weather
page showed 20-31 degrees, the assistant said 21.5 to 33.0 - and called it
"wahan", over there. Asked what a page said, it answered that it could not see
the screen.

It still cannot see the screen. What it can do is read the same data the screen
was built from, with the same farm - and that farm was the whole problem: the
conversational session used the default farm, which is in Agra, while every page
used the farmer's registered land. Two different places, two different
forecasts, one screen.
"""
from __future__ import annotations

import pytest

from app.conversation import screens
from app.core.schemas import FarmContext

FARM = FarmContext(farmer_name="Vansh", village="Kalsan", district="Rajgarh",
                   state="Madhya Pradesh", latitude=23.45, longitude=76.72,
                   crop="wheat", area_acres=2.0)


# ------------------------------------------------- which page is being asked about
@pytest.mark.parametrize("said,page", [
    ("what does the weather page say", "weather"),
    ("mandi wale page par kya hai", "market"),
    ("read me the soil health page", "soil"),
    ("pipeline results mein kya aaya", "pipeline"),
])
def test_the_page_being_asked_about_is_recognised(said, page):
    assert screens.page_in(said) == page


@pytest.mark.parametrize("said", [
    "what does this page say",
    "is page par kya likha hai",
    "read this screen",
    "yahan kya dikh raha hai",
    "इस पेज पर क्या लिखा है",
])
def test_asking_to_be_read_to_is_recognised(said):
    assert screens.asks_to_read(said)


@pytest.mark.parametrize("said", [
    "mandi bhav dikhao",            # an instruction, not a question about the page
    "gehu ka bhav kya hai",         # a question about data, not about a page
    "namaste",
])
def test_an_ordinary_turn_is_not_a_request_to_read_the_screen(said):
    assert not screens.asks_to_read(said)


# ------------------------------------------------------ it reads the real thing
@pytest.mark.asyncio
async def test_the_weather_page_is_read_from_the_farms_own_coordinates(monkeypatch):
    asked = {}

    async def fake(lat, lon, days=7):
        asked.update(lat=lat, lon=lon)
        return {"available": True, "source": "Open-Meteo",
                "current": {"temperature_c": 23.0, "condition": "Clear sky",
                            "humidity_pct": 55, "wind_kmh": 7.0},
                "forecast": [{"date": "2026-10-26", "condition": "Clear sky",
                              "t_min": 20, "t_max": 31, "rain_mm": 0.0,
                              "rain_probability": 0}]}

    monkeypatch.setattr(screens.weather_service, "get_summary", fake)
    lines = await screens.weather(FARM)

    assert asked == {"lat": 23.45, "lon": 76.72}, "it read somewhere else"
    joined = " ".join(lines)
    assert "Kalsan" in joined and "23.0 degrees C" in joined
    assert "20 to 31 degrees C" in joined


@pytest.mark.asyncio
async def test_a_page_with_no_data_says_so_rather_than_inventing(monkeypatch):
    async def nothing(lat, lon, days=7):
        return {"available": False, "reason": "the service did not answer"}

    monkeypatch.setattr(screens.weather_service, "get_summary", nothing)
    lines = await screens.weather(FARM)
    assert len(lines) == 1
    assert "no forecast available" in lines[0]
    assert not any(ch.isdigit() for ch in lines[0].replace("Open-Meteo", ""))


@pytest.mark.asyncio
async def test_a_page_with_no_live_data_still_gets_a_real_answer():
    """"What is on the pipeline results page" used to be refused. There is no
    service behind it to call, but there is a true sentence to say."""
    lines = await screens.read("pipeline", farm=FARM)
    assert lines and "fifteen agents" in lines[0]


@pytest.mark.asyncio
async def test_a_page_nobody_has_heard_of_reads_as_nothing():
    assert await screens.read("nonsense", farm=FARM) == []


@pytest.mark.asyncio
async def test_a_slow_reader_is_dropped_rather_than_waited_for(monkeypatch):
    import asyncio

    async def never(lat, lon, days=7):
        await asyncio.sleep(5)
        return {}

    monkeypatch.setattr(screens.weather_service, "get_summary", never)
    assert await screens.read("weather", farm=FARM, budget=0.2) == []


# ------------------------------------------- and the turn uses the farmer's farm
@pytest.mark.asyncio
async def test_a_spoken_turn_reads_the_farmers_own_land_not_the_default(monkeypatch):
    """The false answer in full: the session farm was the default one, in Agra,
    while the page showed the farmer's registered land."""
    from app.conversation.voice_agent import voice_agent
    from app.core import farmers

    seen = {}

    async def fake(lat, lon, days=7):
        seen["lat"], seen["lon"] = lat, lon
        return {"available": True, "source": "Open-Meteo", "current": {},
                "forecast": []}

    monkeypatch.setattr(screens.weather_service, "get_summary", fake)
    farmers.ensure_schema()

    turn = await voice_agent.respond(
        phone="9876500101", said="what does this page say", call_id="screen-1",
        language="en", channel="dashboard", page="weather", farm=FARM)
    for task in turn.background:
        task.cancel()

    assert seen["lat"] == FARM.latitude, "it read the default farm, not theirs"


def test_every_sidebar_page_can_be_opened_by_voice():
    """"Open pipeline results" was answered "sorry, I cannot open that" - about a
    page sitting in the sidebar the farmer was looking at."""
    from app.conversation import actions

    # Admin and the knowledge console are deliberately absent: a farmer's phone
    # has no admin screen to open.
    # "agents" and "crops" are deliberately absent: both pages were removed.
    # The agents' findings live on Pipeline Results attached to the run that
    # produced them.
    sidebar = {"overview", "pipeline", "soil", "weather", "pests",
               "schemes", "finance", "help", "market", "assistant", "calls",
               "fields", "devices", "reports"}
    missing = sidebar - set(actions.PAGES)
    assert not missing, "not reachable by voice: %s" % sorted(missing)
    assert "agents" not in actions.PAGES, "a page that no longer exists"
    assert "crops" not in actions.PAGES, "a page that no longer exists"
    assert "admin" not in actions.PAGES
    assert "knowledge" not in actions.PAGES


@pytest.mark.parametrize("said,page", [
    ("pipeline results kholo", "pipeline"),
    ("mera profile kholo", "profile"),
])
def test_the_new_pages_are_recognised_as_instructions(said, page):
    from app.conversation import actions

    found = actions.detect(said)
    assert found.found and found.action.id == "open_page", said
    outcome = actions.run(found, channel=actions.SCREEN, language="en")
    assert outcome.ui and outcome.ui.get("page") == page, outcome.ui
