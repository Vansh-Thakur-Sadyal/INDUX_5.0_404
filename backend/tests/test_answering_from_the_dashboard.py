"""Four questions the assistant answered with "I do not have that information".

All four were asked on the phone and on the dashboard, and all four had their
answer sitting in this repository at the time:

    "tell me the government schemes"   7 schemes with benefits and portals
    "how is my soil"                   the sensor reading and SoilGrids
    "tell me about growing mustard"    19 crop profiles in `crop_data`
    "rain chance on Thursday"          7 days of Open-Meteo forecast

The cause was the same in each: ``BY_INTENT`` sent these intents to the
knowledge base and nowhere else, and the knowledge base has nothing on most of
them. A lookup that reads the page's own service is not an extra feature here -
it is the difference between an answer and a refusal.

What is asserted is the wiring: that the question routes to a source, and that
the source's real figures reach the block the model is given. The services are
faked where they are remote, because a test that needs today's forecast is a
test that fails on a quiet weather day.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.agents import crop_data
from app.conversation import intent, lookups
from app.core.schemas import FarmContext

FARM = FarmContext(farmer_name="Ramesh", village="Rampur", district="Agra",
                   state="Uttar Pradesh", crop="wheat")


def _forecast(days: int = 7):
    """A forecast where every day differs, so a wrong day cannot pass."""
    start = date.today()
    return {
        "available": True, "source": "Open-Meteo", "age_seconds": 10, "stale": False,
        "current": {"temperature_c": 29.0, "humidity_pct": 54, "wind_kmh": 8,
                    "condition": "Partly cloudy"},
        "forecast": [{
            "date": (start + timedelta(days=i)).isoformat(),
            "condition": "Slight rain", "t_min": 20 + i, "t_max": 30 + i,
            "rain_mm": float(i), "rain_probability": 10 * i,
        } for i in range(days)],
    }


# ------------------------------------------------- which source each question gets
@pytest.mark.parametrize("said,source", [
    ("tell me about the government schemes", "schemes"),
    ("kaun si sarkari yojana mil sakti hai", "schemes"),
    ("how is my soil health", "soil"),
    ("mitti ki jaanch batao", "soil"),
    ("tell me about growing mustard", "crop"),
    ("sarson kaise ugaye", "crop"),
    ("what are the rain chances this week", "forecast"),
    # "loan" is a keyword of both economics and finance, and economics wins -
    # so the credit terms have to be reachable from there too.
    ("I need a loan, what can I get", "schemes"),
    ("kcc loan ke liye kya chahiye", "schemes"),
])
def test_the_question_reaches_a_source_that_has_the_answer(said, source):
    detected = intent.detect(said)
    wanted = lookups.BY_INTENT.get(detected.intent, ())
    named = lookups.crop_in(said, detected.normalised_query)
    assert source in wanted or (source == "crop" and named), \
        "%r -> intent %r -> %r" % (said, detected.intent, wanted)


# ------------------------------------------------------------------- the schemes
async def test_the_schemes_arrive_with_their_money_and_their_portal():
    """Every scheme line used to be a title, a colon and nothing: the reader
    asked for "name" and "benefit", which no record in the catalogue has."""
    found = await lookups.schemes(FARM)
    text = "\n".join(found.lines)

    assert found.found
    assert "PM-KISAN" in text
    assert "6,000" in text, text
    assert "pmkisan.gov.in" in text
    assert "who is eligible" in text
    # This IS the answer, so the agent chain is not asked to produce another.
    assert found.complete is True


# ---------------------------------------------------------------------- the soil
async def test_the_soil_answer_says_what_is_measured_and_what_is_modelled(monkeypatch):
    async def background(lat, lon):
        return {"available": True, "source": "SoilGrids",
                "values": {"ph": {"label": "pH", "value": 7.8, "unit": ""},
                           "soc": {"label": "organic carbon", "value": 0.42,
                                   "unit": "percent"}}}

    monkeypatch.setattr("app.services.soilgrids.get_summary", background)
    found = await lookups.soil_reading(FARM, "")
    text = "\n".join(found.lines)

    assert found.found, "a soil question got nothing back"
    assert "7.8" in text
    # The honesty that makes the number usable: a global model is not a reading
    # off this field, and the farmer is told which one they are hearing.
    assert "ESTIMATED" in text
    assert "no sensor reading" in text


# ---------------------------------------------------------------------- the crop
async def test_a_named_crop_comes_back_with_its_real_agronomy():
    found = await lookups.agronomy("tell me about growing mustard",
                                   normalised="mustard growing", farm=FARM)
    text = "\n".join(found.lines)
    spec = crop_data.CROP_PROFILES["mustard"]

    assert found.found
    assert "Mustard" in text
    assert spec["sowing_window"] in text
    assert str(spec["water_need_mm"][0]) in text
    assert "seed rate" in text
    # The stages, which is what "tell me about growing it" actually means.
    assert "flowering" in text


async def test_no_crop_named_offers_the_ones_that_suit_this_season():
    found = await lookups.agronomy("which crop should I sow", normalised="crop sow",
                                   farm=FARM)
    text = "\n".join(found.lines)

    assert found.found
    assert "SEASON" in text
    assert str(FARM.district) in text


@pytest.mark.parametrize("said,crop", [
    ("tell me about growing mustard", "mustard"),
    ("sarson ki buvai kab", "mustard"),
    ("dhaniya kaise ugaye", "coriander"),
    ("methi ki buvai", "fenugreek"),
    ("gehu me khad kitni", "wheat"),
])
def test_the_crop_in_the_question_is_the_one_profiled(said, crop):
    assert lookups.crop_in(said, "") == crop, said


def test_dhan_is_not_found_inside_dhaniya():
    """The substring that caused it: "dhan" matched inside "dhaniya", so a
    farmer asking about coriander was answered about rice."""
    from app.core import tasks

    assert tasks.find_crop("dhaniya kaise ugaye") == ""
    assert tasks.find_crop("धनिया kaise ugaye") == ""
    assert tasks.find_crop("dhan ka rate") == "rice"
    assert tasks.find_crop("धान ka rate") == "rice"


# ------------------------------------------------------------------- the weather
async def test_every_forecast_day_is_named_by_its_weekday(monkeypatch):
    """Asked about Thursday, the model had "Today", "Tomorrow" and four bare
    ISO dates - three of them missing entirely - and answered nought
    millimetres for every day while the page showed the real chance."""
    async def summary(lat, lon, days=7):
        return _forecast()

    monkeypatch.setattr("app.services.weather.get_summary", summary)
    found = await lookups.forecast(FARM)
    text = "\n".join(found.lines)

    # Seven days, not three.
    assert len(found.lines) >= 8, found.lines
    for i in range(7):
        day = date.today() + timedelta(days=i)
        assert day.strftime("%A") in text, day
    # The chance is said, and the day it belongs to is identifiable.
    assert "chance of rain 60 percent" in text


def test_a_day_label_says_the_weekday_the_date_and_how_far_off():
    today = date.today()
    label = lookups.day_label((today + timedelta(days=2)).isoformat())

    assert (today + timedelta(days=2)).strftime("%A") in label
    assert "in 2 days" in label
    assert lookups.day_label(today.isoformat()).endswith("(today)")
    # A date that will not parse is returned rather than invented.
    assert lookups.day_label("not-a-date") == "not-a-date"


def test_october_is_the_rabi_sowing_season_not_kharif():
    """October is in both lists, and dict order was answering it - so in late
    October, with every rabi crop going in, the advisor offered kharif crops
    and warned that it was the wrong season for them."""
    assert crop_data.current_season(10) == "rabi"
    assert crop_data.current_season(9) == "kharif"
    assert crop_data.current_season(6) == "kharif"
    assert crop_data.current_season(3) == "zaid"
