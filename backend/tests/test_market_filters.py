"""Which records a mandi price is actually a median OF.

Found while checking a spoken tomato price. The reply said "Uttar Pradesh mein
Tomato ka bhav 1500" and the records behind it were from Andhra Pradesh and
Madhya Pradesh. Two separate faults, measured against the live data.gov.in
resource (9ef84268-d588-465a-a308-a864a43d0070):

**The filters matched on tokens, not values.** The plain field names are analysed
text at the API's end, so a filter returns anything sharing a token with it:

    filters[state]=Uttar Pradesh                   89 records, Madhya Pradesh
                                                   among them - shared "Pradesh"
    filters[state.keyword]=Uttar Pradesh            1 record, Uttar Pradesh
    filters[commodity]=Bengal Gram(Gram)(Whole)    41 records over SIX commodities
                                                   - black gram, moong, lentil,
                                                   horse gram: shared "Gram"
    filters[commodity.keyword]=Bengal Gram(...)    14 records, chickpea only

So a chickpea price was a median across unrelated pulses, and every state price
was a median across several states - both quoted to farmers as fact.

**The scope label was assumed from the request.** Even with the filters fixed, a
label that says what was ASKED FOR rather than what came BACK is one API change
away from lying again. It is now read off the records.
"""
from __future__ import annotations

import pytest

from app.services import market


def _records(*rows):
    """A fake API payload. Each row is (state, district, modal_price)."""
    return {"records": [{"state": s, "district": d, "market": d + " APMC",
                         "commodity": "Tomato", "variety": "Tomato",
                         "grade": "FAQ", "arrival_date": "25/10/2026",
                         "min_price": price - 100, "max_price": price + 100,
                         "modal_price": price}
                        for s, d, price in rows]}


class _Result:
    def __init__(self, data):
        self.ok, self.data, self.error = True, data, ""
        self.fetched_at, self.age_seconds = 1_700_000_000.0, 0


# ------------------------------------------------------------- the filters
@pytest.mark.asyncio
async def test_the_filters_ask_for_exact_values(monkeypatch):
    sent = {}

    async def capture(url, params, **kwargs):
        sent.update(params)
        return _Result(_records(("Uttar Pradesh", "Agra", 1500.0)))

    monkeypatch.setattr(market, "get_json", capture)
    monkeypatch.setattr(market.get_settings(), "values",
                        {**market.get_settings().values,
                         "DATA_GOV_API_KEY": "test-key",
                         "DATA_GOV_RESOURCE_ID": "test-resource"})

    await market.fetch_prices("tomato", "Uttar Pradesh", "Agra")

    assert "filters[commodity.keyword]" in sent, sent
    assert "filters[state.keyword]" in sent
    assert "filters[district.keyword]" in sent
    # The analysed forms must be gone, not merely accompanied: sending both
    # would widen the match straight back out again.
    for loose in ("filters[commodity]", "filters[state]", "filters[district]"):
        assert loose not in sent, "%s is a token match, not a value match" % loose


# --------------------------------------------------------------- the scope
def test_records_from_one_district_are_called_a_district(monkeypatch):
    summary = market.summarise(
        _Result(_records(("Uttar Pradesh", "Agra", 1500.0),
                         ("Uttar Pradesh", "Agra", 1700.0))), "tomato")
    summary["scope"] = market._scope_of(summary, "Uttar Pradesh", "Agra")
    assert summary["scope"] == "district"
    assert market.scope_phrase(summary) == "in Agra district, Uttar Pradesh"


def test_records_from_one_state_are_called_that_state():
    summary = market.summarise(
        _Result(_records(("Maharashtra", "Pune", 950.0),
                         ("Maharashtra", "Nashik", 1100.0))), "tomato")
    summary["scope"] = market._scope_of(summary, "Maharashtra", None)
    assert summary["scope"] == "state"
    assert market.scope_phrase(summary) == "across Maharashtra"


def test_records_from_several_states_are_never_called_one_state():
    """The bug itself. Asked for Uttar Pradesh, given three states, and the
    farmer was told it was the Uttar Pradesh price."""
    summary = market.summarise(
        _Result(_records(("Uttar Pradesh", "Agra", 1500.0),
                         ("Andhra Pradesh", "Chittor", 2700.0),
                         ("Madhya Pradesh", "Katni", 366.0))), "tomato")
    summary["scope"] = market._scope_of(summary, "Uttar Pradesh", None)

    assert summary["scope"] == "national", "three states were labelled as one"
    phrase = market.scope_phrase(summary)
    assert "Uttar Pradesh" not in phrase, phrase
    assert phrase == "across 3 states of India"


def test_the_states_and_districts_present_are_reported():
    summary = market.summarise(
        _Result(_records(("Maharashtra", "Pune", 950.0),
                         ("Gujarat", "Surat", 1200.0))), "tomato")
    assert summary["states"] == ["Gujarat", "Maharashtra"]
    assert summary["districts"] == ["Pune", "Surat"]


def test_an_unavailable_summary_still_has_a_scope():
    """Nothing came back, so there is nothing to read a scope off. It falls back
    to what was asked for, which is the only thing known."""
    class _Empty:
        ok, data, error = True, {"records": []}, ""
        fetched_at, age_seconds = 1_700_000_000.0, 0

    summary = market.summarise(_Empty(), "tomato")
    assert summary["available"] is False
    assert market._scope_of(summary, "Punjab", None) == "state"
    assert market._scope_of(summary, None, None) == "national"


# ------------------------------------------------- and what gets said out loud
@pytest.mark.asyncio
async def test_the_spoken_line_names_the_real_scope(monkeypatch):
    from app.conversation import lookups
    from app.core.schemas import FarmContext

    async def mixed(commodity, state=None, district=None):
        summary = market.summarise(
            _Result(_records(("Uttar Pradesh", "Agra", 1500.0),
                             ("Andhra Pradesh", "Chittor", 2700.0))), commodity)
        summary["scope"] = market._scope_of(summary, state, district)
        return summary

    monkeypatch.setattr(lookups.market_service, "get_summary", mixed)
    found = await lookups.mandi(
        "tomato ka bhav", normalised="tomato ka bhav price",
        farm=FarmContext(state="Uttar Pradesh", district="Agra", crop="wheat"))

    block = found.as_prompt()
    assert "across 2 states of India" in block, block
    assert "across Uttar Pradesh" not in block
