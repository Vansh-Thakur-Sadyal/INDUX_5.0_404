"""Schemes, farm finance and the support desk.

Three things a farmer asks about that are not agronomy, so none of the fifteen agents
covers them. What matters here is that nothing is invented: a scheme carries the
portal to check it on, and the season arithmetic uses only numbers the farmer gave.
"""
from __future__ import annotations

import pytest

from app.services import support


# ------------------------------------------------------------------ schemes
def test_every_scheme_can_be_checked_by_the_farmer(client):
    """An amount with no way to verify it is worth very little - the shares and
    limits change, and the state top-ups differ."""
    body = client.get("/api/schemes").json()
    assert body["count"] >= 5
    for scheme in body["schemes"]:
        assert scheme["title"]
        assert scheme["portal_url"].startswith("https://"), scheme["title"]
        assert scheme["eligibility"], scheme["title"]
        assert scheme["documents"], scheme["title"]
    assert "do change" in body["note"]


def test_the_portals_are_government_domains(client):
    """A scheme page that sends a farmer to someone's affiliate link is worse than
    no scheme page."""
    allowed = (".gov.in", ".nic.in", "myscheme.gov.in")
    for scheme in client.get("/api/schemes").json()["schemes"]:
        host = scheme["portal_url"].split("/")[2]
        assert host.endswith(allowed), scheme["portal_url"]


def test_schemes_can_be_narrowed_by_category(client):
    body = client.get("/api/schemes").json()
    category = body["categories"][0]
    narrowed = client.get("/api/schemes?category=" + category).json()
    assert 0 < narrowed["count"] <= body["count"]
    assert narrowed["ranked_for_farm"] is False


def test_an_unknown_category_returns_nothing_rather_than_everything(client):
    body = client.get("/api/schemes?category=spaceflight").json()
    assert body["count"] == 0


def test_the_ranking_puts_the_broadly_useful_ones_first(client):
    """Weak on purpose: nearly every farmer is eligible for most central schemes, and
    a confident filter would hide one somebody could have claimed."""
    titles = [s["id"] for s in client.get("/api/schemes").json()["schemes"]]
    assert set(titles[:3]) & {"sub_pmkisan", "sub_kcc", "sub_pmfby"}
    assert len(titles) == len(support.SCHEMES), "ranking must not drop any scheme"


# ------------------------------------------------------------------ finance
def test_the_season_arithmetic_uses_only_what_was_given(client):
    body = client.get("/api/finance?cost=42000&yield_quintal=18&price=2500").json()
    e = body["economics"]
    assert e["break_even_price_per_quintal"] == pytest.approx(42000 / 18, rel=0.01)
    assert e["break_even_quintal"] == pytest.approx(42000 / 2500, rel=0.01)
    assert e["gross_revenue_inr"] == 45000
    assert e["gross_margin_inr"] == 3000
    assert e["missing"] == []


def test_a_missing_cost_is_reported_not_guessed(client):
    """There is no national cost-of-cultivation table in here. One would be quoted
    back at a farmer as if it were their own spend."""
    e = client.get("/api/finance?yield_quintal=18&price=2500").json()["economics"]
    assert "cost" in e["missing"]
    assert e.get("gross_margin_inr") is None
    assert e["gross_revenue_inr"] == 45000, "what CAN be worked out still is"


def test_the_price_says_where_it_came_from(client):
    """A break-even built on a price nobody can point at is not worth having."""
    body = client.get("/api/finance?cost=42000&yield_quintal=18&price=2500").json()
    assert body["economics"]["price_source"] == "you"
    without = client.get("/api/finance?cost=42000&yield_quintal=18").json()
    source = without["economics"]["price_source"]
    assert "mandi records" in source or source == "no price available"


def test_credit_terms_are_stated_with_the_condition_attached(client):
    credit = client.get("/api/finance").json()["credit"]
    assert credit["effective_if_repaid_on_time_percent"] == 4.0
    assert credit["base_interest_percent"] == 7.0
    assert "repaid on time" in credit["note"]
    assert credit["collateral_free_limit_inr"] > 0


def test_nothing_is_computed_from_an_empty_request(client):
    e = client.get("/api/finance").json()["economics"]
    assert e.get("gross_margin_inr") is None
    assert e.get("break_even_quintal") is None
    assert "cost" in e["missing"] and "yield" in e["missing"]


# ------------------------------------------------------------------ support
def test_the_helplines_are_real_numbers_with_a_reason(client):
    body = client.get("/api/support").json()
    assert len(body["helplines"]) >= 4
    for line in body["helplines"]:
        assert line["number"].replace("-", "").replace("+", "").isdigit(), line
        assert line["about"] and line["hours"]
    numbers = {h["number"] for h in body["helplines"]}
    assert "1800-180-1551" in numbers, "the Kisan Call Centre is the main one"


def test_the_support_page_points_past_itself(client):
    """A national helpline knows less about a district than the block officer does,
    and the page should say so rather than pretend to be the last word."""
    note = client.get("/api/support").json()["note"]
    assert "Krishi Vigyan Kendra" in note or "block agriculture" in note


# -------------------------------------------- what the agents can see of this
def test_the_block_handed_to_the_model_is_short_and_actionable(client):
    """It goes into a prompt a spoken reply has to share, so it carries the thing a
    farmer can act on - the portal and the number - and little else."""
    block = support.summary_for_agents({"area_acres": 2.0, "irrigation_method": "flood"})
    assert 1 <= len(block["schemes"]) <= 4
    for scheme in block["schemes"]:
        assert scheme["apply_at"].startswith("https://")
    assert len(block["helplines"]) <= 3
    assert block["credit"]["kisan_credit_card_interest_percent"] == 4.0


def test_the_prompt_text_quotes_the_portal_and_the_number(client):
    from app.llm.mistral_client import support_block
    text = support_block(support.summary_for_agents({}))
    assert "https://" in text
    assert "1800-180-1551" in text
    assert "never invent" in text.lower()


def test_an_empty_support_block_adds_nothing_to_the_prompt(client):
    from app.llm.mistral_client import support_block
    assert support_block(None) == ""
    assert support_block({}) == ""


@pytest.mark.parametrize("question,intent", [
    ("is there any subsidy for a drip irrigation set", "scheme"),
    ("what interest does a kisan credit card charge", "finance"),
    ("which helpline do I call about my pm kisan instalment", "scheme"),
])
def test_these_questions_route_to_the_new_intents(question, intent):
    """They used to fall through to general and get agronomy small talk."""
    from app.conversation import intent as intent_module
    detected = intent_module.detect(question)
    assert detected.intent in ("scheme", "finance", "support"), \
        "%r routed to %s" % (question, detected.intent)
