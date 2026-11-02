"""The farm money record, the district-level mandi view, and clearing history.

The ledger is the one place in this project that accumulates numbers over months, so
the thing to guard is that it never produces one the farmer did not put in.
"""
from __future__ import annotations

import pytest

from app.core import accounts, ledger as ledger_store

EMAIL = "led@example.com"
PHONE = "9876500099"
PASSWORD = "wheat-field-7"


@pytest.fixture
def signed_in(client):
    accounts.ensure_schema()
    ledger_store.ensure_schema()
    user = accounts.create_user(email=EMAIL, phone=PHONE, password=PASSWORD,
                                status="active")
    return {"Authorization": "Bearer " + accounts.start_session(user["user_id"])}


def _add(client, headers, **kw):
    body = {"category": "fertiliser", "amount_inr": 1000}
    body.update(kw)
    return client.post("/api/ledger", json=body, headers=headers)


# --------------------------------------------------------------- the record
def test_an_entry_is_stored_and_totalled(client, signed_in):
    _add(client, signed_in, amount_inr=4200, category="fertiliser", note="2 bags urea")
    _add(client, signed_in, amount_inr=1800, category="labour")
    body = client.get("/api/ledger", headers=signed_in).json()
    assert body["summary"]["spent_inr"] == 6000
    assert body["summary"]["by_category"] == {"fertiliser": 4200.0, "labour": 1800.0}
    assert body["summary"]["biggest_cost"] == "fertiliser"


def test_income_and_expense_net_out(client, signed_in):
    _add(client, signed_in, amount_inr=6000, category="seed")
    _add(client, signed_in, amount_inr=15000, kind="income", category="other")
    summary = client.get("/api/ledger/summary", headers=signed_in).json()
    assert summary["earned_inr"] == 15000
    assert summary["net_inr"] == 9000


def test_an_empty_record_reports_zero_rather_than_an_average(client, signed_in):
    """There is no district cost-of-cultivation table in here. One would be read as
    this farm's own spend."""
    summary = client.get("/api/ledger/summary", headers=signed_in).json()
    assert summary["empty"] is True
    assert summary["spent_inr"] == 0
    assert summary["by_category"] == {}
    assert "Nothing recorded" in summary["note"]


def test_a_zero_or_negative_amount_is_refused(client, signed_in):
    for bad in (0, -50):
        assert _add(client, signed_in, amount_inr=bad).status_code == 400


def test_an_unknown_category_lands_in_other_rather_than_being_invented(client, signed_in):
    body = _add(client, signed_in, category="helicopter", amount_inr=100).json()
    assert body["entry"]["category"] == "other"


def test_entries_are_scoped_to_their_own_account(client, signed_in):
    """A ledger is about money. One farmer must never see another's."""
    _add(client, signed_in, amount_inr=5000)
    other = accounts.create_user(email="other@example.com", phone="9876500011",
                                 password=PASSWORD, status="active")
    other_headers = {"Authorization": "Bearer " + accounts.start_session(other["user_id"])}
    body = client.get("/api/ledger", headers=other_headers).json()
    assert body["summary"]["spent_inr"] == 0
    assert body["entries"] == []


def test_an_entry_can_be_removed(client, signed_in):
    entry = _add(client, signed_in, amount_inr=900).json()["entry"]
    assert client.delete("/api/ledger/" + entry["entry_id"],
                         headers=signed_in).status_code == 200
    assert client.get("/api/ledger/summary", headers=signed_in).json()["spent_inr"] == 0


def test_one_farmer_cannot_delete_anothers_entry(client, signed_in):
    entry = _add(client, signed_in, amount_inr=900).json()["entry"]
    other = accounts.create_user(email="thief@example.com", phone="9876500022",
                                 password=PASSWORD, status="active")
    headers = {"Authorization": "Bearer " + accounts.start_session(other["user_id"])}
    assert client.delete("/api/ledger/" + entry["entry_id"],
                         headers=headers).status_code == 404
    assert client.get("/api/ledger/summary", headers=signed_in).json()["spent_inr"] == 900


def test_a_field_filter_narrows_the_total(client, signed_in):
    _add(client, signed_in, amount_inr=1000, field_id="field-a")
    _add(client, signed_in, amount_inr=3000, field_id="field-b")
    one = client.get("/api/ledger/summary?field_id=field-a", headers=signed_in).json()
    assert one["spent_inr"] == 1000


def test_the_season_is_worked_out_and_stored(client, signed_in):
    """Stored rather than derived on read, so changing the season rule later cannot
    silently re-file entries that are already in the book."""
    entry = _add(client, signed_in, amount_inr=500, spent_on="2026-11-14T00:00:00+00:00"
                 ).json()["entry"]
    assert entry["season"] == "rabi"
    entry2 = _add(client, signed_in, amount_inr=500, spent_on="2026-07-14T00:00:00+00:00"
                  ).json()["entry"]
    assert entry2["season"] == "kharif"


# ------------------------------------------------------- what the call hears
def test_the_spoken_summary_is_one_sentence(client, signed_in):
    _add(client, signed_in, amount_inr=4200, category="fertiliser")
    spoken = client.get("/api/ledger/summary", headers=signed_in).json()["spoken"]
    assert "4200" in spoken and "fertiliser" in spoken
    assert len(spoken.split()) < 40, "a spoken turn has seconds, not paragraphs"


@pytest.mark.parametrize("said,amount,category,kind", [
    ("I spent 4200 on urea today", 4200, "fertiliser", "expense"),
    ("majdoor ko 1800 diye", 1800, "labour", "expense"),
    ("paid 950 for diesel", 950, "machinery", "expense"),
    ("sold the wheat for 12500", 12500, "other", "income"),
])
def test_a_spoken_amount_is_parsed(said, amount, category, kind):
    from app.conversation.voice_agent import parse_spoken_money
    parsed = parse_spoken_money(said)
    assert parsed == {"amount_inr": float(amount), "category": category, "kind": kind}


@pytest.mark.parametrize("said,amount,category", [
    # Spelled-out amounts used to be refused on the grounds that reading "four
    # thousand" as 4 would be worse than asking again. That was right about the
    # risk and wrong about the answer: a call-coverage audit found that "do
    # hazaar ka urea liya" - the ordinary Hindi way to say it - was recorded
    # nowhere at all. They are parsed now, with the multiplier, and an amount
    # that cannot be read confidently still returns nothing.
    ("four thousand rupees on urea", 4000, "fertiliser"),
    ("do hazaar ka urea liya", 2000, "fertiliser"),
    ("ek lakh ka tractor liya", 100000, "machinery"),
    ("paanch sau ka diesel", 500, "machinery"),
])
def test_an_amount_said_in_words_is_parsed_at_the_right_size(said, amount, category):
    from app.conversation.voice_agent import parse_spoken_money
    parsed = parse_spoken_money(said)
    assert parsed is not None, said
    assert parsed["amount_inr"] == float(amount)
    assert parsed["category"] == category


def test_a_multiplier_is_never_dropped():
    """"12 hazaar mein gehu becha" was recorded as twelve rupees - a
    thousandfold error in the farmer's own books, with nothing on screen to
    show it had happened."""
    from app.conversation.voice_agent import parse_spoken_money
    assert parse_spoken_money("12 hazaar mein gehu becha")["amount_inr"] == 12000


@pytest.mark.parametrize("said", [
    "how much have I spent this season",
    "what is my total",
])
def test_nothing_is_recorded_when_no_figure_was_spoken(said):
    """Filing a wrong amount silently is worse than asking the farmer to repeat it."""
    from app.conversation.voice_agent import parse_spoken_money
    assert parse_spoken_money(said) is None


# ------------------------------------------------------------- mandi scope
def test_the_market_reports_how_far_it_had_to_widen(client):
    body = client.get("/api/market?commodity=wheat&state=Uttar Pradesh").json()
    assert body["scope"] in ("district", "state", "national")


def test_the_districts_present_are_listed_with_their_market_counts(client):
    body = client.get("/api/market?commodity=wheat").json()
    if not body.get("available"):
        pytest.skip("data.gov.in is not reachable from here right now")
    for row in body["districts"]:
        assert row["district"] and row["markets"] >= 1


def test_the_nearest_list_puts_the_farmers_district_first(client):
    body = client.get("/api/market?commodity=wheat").json()
    if not body.get("available") or not body.get("nearest"):
        pytest.skip("data.gov.in is not reachable from here right now")
    assert len(body["nearest"]) <= 5


# ----------------------------------------------------------- clearing history
def test_clearing_history_leaves_the_sensor_record_alone(client, signed_in):
    """A farmer clearing a cluttered question log would not expect their soil
    history to go with it."""
    client.post("/api/sensors/manual", json={"values": {"ph": 6.8}}, headers=signed_in)
    before = client.get("/api/sensors/latest").json()
    assert before["available"] is True

    client.delete("/api/history?what=all", headers=signed_in)
    after = client.get("/api/sensors/latest").json()
    assert after["available"] is True, "readings must survive a history clear"


def test_readings_are_only_cleared_when_asked_for_by_name(client, signed_in):
    client.post("/api/sensors/manual", json={"values": {"ph": 6.8}}, headers=signed_in)
    client.delete("/api/history?what=readings", headers=signed_in)
    assert client.get("/api/sensors/latest").json()["available"] is False


def test_clearing_needs_a_known_target(client, signed_in):
    assert client.delete("/api/history?what=nonsense",
                         headers=signed_in).status_code == 400


def test_a_signed_out_caller_cannot_clear_anything(client):
    assert client.delete("/api/history?what=all").status_code == 401
