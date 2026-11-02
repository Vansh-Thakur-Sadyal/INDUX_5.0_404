"""Which reading the dashboard is working from, and where it shows up.

The bug this file was written for: a fetched sensor reading appeared on the Sensor
page and nowhere else, because the BLE card filed it against the chosen field while
every reader asked for the literal "field-1".

The real/simulation switch that used to live here is gone with the simulator. There is
no mode to choose any more - a reading exists because a device sent it or the farmer
typed it - so what a number is worth is carried by its provenance instead.
"""
from __future__ import annotations

import pytest

from app.core import accounts

PHONE = "9876500088"
EMAIL = "src@example.com"
PASSWORD = "wheat-field-7"


@pytest.fixture
def signed_in(client):
    accounts.ensure_schema()
    user = accounts.create_user(email=EMAIL, phone=PHONE, password=PASSWORD,
                                status="active")
    return {"Authorization": "Bearer " + accounts.start_session(user["user_id"])}


def _ble(client, headers, parcel="field-1", **values):
    return client.post("/api/sensors/ble",
                       json={"values": values or {"ph": 6.8}, "parcel_id": parcel,
                             "probe_attached": True},
                       headers=headers).json()


# ------------------------------------------------- the reading reaches every page
def test_a_reading_filed_against_any_plot_is_the_one_the_dashboard_shows(client, signed_in):
    _ble(client, signed_in, parcel="field-ab12cd34", ph=7.1, moisture_pct=38.0)

    latest = client.get("/api/sensors/latest").json()
    assert latest["available"] is True
    assert latest["reading"]["ph"] == 7.1
    assert latest["reading"]["parcel_id"] == "field-ab12cd34"

    overview = client.get("/api/dashboard/overview").json()
    assert overview["soil"]["available"] is True
    phs = [p["value"] for p in overview["parameters"] if p["field"] == "ph"]
    assert phs == [7.1], "the overview is reading a different row from /latest"


def test_an_explicit_plot_still_narrows(client, signed_in):
    _ble(client, signed_in, parcel="field-a", ph=6.0)
    _ble(client, signed_in, parcel="field-b", ph=7.5)
    assert client.get("/api/sensors/latest?parcel_id=field-a").json()["reading"]["ph"] == 6.0
    assert client.get("/api/sensors/latest?parcel_id=field-b").json()["reading"]["ph"] == 7.5


def test_the_agent_chain_reasons_over_the_same_reading(client, signed_in):
    """A chain working from different numbers than the page is worse than no chain."""
    _ble(client, signed_in, parcel="field-1", ph=5.2, moisture_pct=18.0)
    from app.agents.base import AgentContext
    from app.core.schemas import FarmContext
    from app.orchestrator import orchestrator

    ctx = AgentContext(question="how is my soil", farm=FarmContext())
    orchestrator._load_reading(ctx)
    assert ctx.reading is not None, "the agents saw no reading at all"
    assert ctx.reading.ph == 5.2
    assert ctx.reading.moisture_pct == 18.0


def test_with_nothing_stored_the_page_is_told_why(client):
    body = client.get("/api/sensors/latest").json()
    assert body["available"] is False
    assert "Fetch" in body["note"] or "soil-test" in body["note"]


# ---------------------------------------------------------------- provenance
def test_a_device_packet_without_a_probe_is_not_called_a_measurement(client, signed_in):
    """The Arduino generates the seven values at this stage of the hardware."""
    body = client.post("/api/sensors/ble", json={"values": {"ph": 6.8}},
                       headers=signed_in).json()
    assert body["provenance"] == "device_simulated"
    assert body["reading"]["mode"] == "real", "the LINK really is real"


def test_an_attached_probe_makes_it_a_measurement(client, signed_in):
    body = _ble(client, signed_in, ph=6.8)
    assert body["provenance"] == "real_sensor"


def test_typed_figures_are_the_farmers_own_data(client, signed_in):
    """A soil-test slip is real data about this field, not a simulation."""
    body = client.post("/api/sensors/manual", json={"values": {"ph": 7.9}},
                       headers=signed_in).json()
    assert body["provenance"] == "farmer_reported"
    assert body["reading"]["mode"] == "real"
    assert client.get("/api/sensors/latest").json()["reading"]["ph"] == 7.9


def test_typed_figures_leave_the_rest_blank(client, signed_in):
    """They used to be filled from a generated scenario. That was the simulator
    putting numbers nobody measured next to ones somebody did."""
    body = client.post("/api/sensors/manual", json={"values": {"ph": 7.9}},
                       headers=signed_in).json()
    reading = body["reading"]
    for field in ("nitrogen_mg_kg", "moisture_pct", "ec_ds_m", "temperature_c"):
        assert reading[field] is None, "%s was filled in from somewhere" % field


def test_nothing_in_the_project_can_store_a_simulated_reading(client, signed_in):
    """The simulator endpoints are gone, not merely hidden."""
    for path in ("/api/sensors/simulate", "/api/sensors/seed-demo",
                 "/api/sensors/mode", "/api/sensors/scenarios"):
        response = client.post(path, json={})
        assert response.status_code in (404, 405), "%s still answers" % path


def test_the_history_carries_provenance_rather_than_a_mode(client, signed_in):
    _ble(client, signed_in, ph=6.1)
    series = client.get("/api/sensors/history").json()["series"]
    assert series
    assert "provenance" in series[0]
    assert all(row["provenance"] for row in series)
