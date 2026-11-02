"""Readings fetched off the HM-10 over Web Bluetooth.

The browser holds the radio and posts a parsed packet here, so what this file guards
is what the server does with a packet that is INCOMPLETE or MISLABELLED - which is the
normal case, not the exception, with one software serial link shared between a GPS and
a BLE module.

Two rules carry the weight:

  * only the parameters that actually arrived are stored. /api/sensors/manual fills the
    blanks from a scenario on purpose, so a farmer with one soil-test figure does not
    have to invent six more. Doing that here would be the opposite of useful: the page
    is being read to find out what the probe said.
  * a packet whose numbers the Arduino generated itself is labelled DEVICE_SIMULATED.
    The packet is real, the link is real, the numbers are not measurements, and only
    an explicit probe_attached turns them into one.
"""
from __future__ import annotations

import pytest

from app.core import accounts
from app.sensors.validation import PARAMETERS

PHONE = "9876500077"
EMAIL = "ble@example.com"
PASSWORD = "wheat-field-7"


@pytest.fixture
def signed_in(client):
    """An active account and a token, without going through OTP and captcha."""
    accounts.ensure_schema()
    user = accounts.create_user(email=EMAIL, phone=PHONE, password=PASSWORD,
                                status="active")
    token = accounts.start_session(user["user_id"])
    return {"Authorization": "Bearer " + token}


def _post(client, headers, **payload):
    return client.post("/api/sensors/ble", json=payload, headers=headers)


# ------------------------------------------------------------------- access
def test_a_stranger_cannot_push_a_reading_onto_the_dashboard(client):
    """The readings table drives the soil page and every agent after it."""
    assert client.post("/api/sensors/ble",
                       json={"values": {"ph": 6.8}}).status_code == 401


# ------------------------------------------------- only what actually arrived
def test_a_packet_with_one_value_stores_one_value(client, signed_in):
    response = _post(client, signed_in, values={"nitrogen_mg_kg": 45})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["received"] == ["nitrogen_mg_kg"]
    reading = body["reading"]
    assert reading["nitrogen_mg_kg"] == 45
    for field in PARAMETERS:
        if field != "nitrogen_mg_kg":
            assert reading[field] is None, "%s was filled in from somewhere" % field
    assert body["validation"]["missing_fields"] == [
        f for f in PARAMETERS if f != "nitrogen_mg_kg"]


def test_the_seven_values_round_trip_when_the_whole_packet_arrives(client, signed_in):
    sent = {"nitrogen_mg_kg": 45, "phosphorus_mg_kg": 20, "potassium_mg_kg": 180,
            "temperature_c": 24.5, "moisture_pct": 42.0, "ec_ds_m": 0.85, "ph": 6.8}
    body = _post(client, signed_in, values=sent).json()
    assert body["validation"]["status"] == "valid"
    assert body["validation"]["completeness"] == 1.0
    for field, value in sent.items():
        assert body["reading"][field] == value


def test_a_key_the_server_does_not_know_is_ignored_not_stored(client, signed_in):
    body = _post(client, signed_in,
                 values={"ph": 6.8, "humidity": 99, "nitrogen": 45}).json()
    assert body["received"] == ["ph"]
    assert "humidity" not in body["reading"]
    # "nitrogen" is the parser's job to canonicalise; the server takes field names only
    assert body["reading"]["nitrogen_mg_kg"] is None


def test_a_value_that_is_not_a_number_is_rejected_by_name(client, signed_in):
    body = _post(client, signed_in, values={"ph": "n/a", "nitrogen_mg_kg": 45}).json()
    assert body["rejected"] == {"ph": "not a number"}
    assert body["reading"]["ph"] is None
    assert body["reading"]["nitrogen_mg_kg"] == 45


def test_an_empty_packet_is_refused_with_the_accepted_names(client, signed_in):
    response = _post(client, signed_in, values={})
    assert response.status_code == 400
    assert "nitrogen_mg_kg" in response.json()["detail"]


def test_an_impossible_value_is_dropped_rather_than_pulled_into_range(client, signed_in):
    """A clamped number looks like a measurement. 14.5 pH is a broken probe."""
    body = _post(client, signed_in, values={"ph": 14.5, "moisture_pct": 42}).json()
    assert body["reading"]["ph"] is None
    assert "ph" in body["validation"]["invalid_fields"]
    assert body["reading"]["moisture_pct"] == 42       # the good value survives


# ---------------------------------------------------------------- provenance
def test_a_packet_from_the_current_hardware_is_not_called_a_measurement(client, signed_in):
    """The Arduino generates the seven values at this stage of the build."""
    body = _post(client, signed_in, values={"ph": 6.8}).json()
    assert body["provenance"] == "device_simulated"
    assert "device_simulated" in body["validation"]["flags"]
    assert body["reading"]["mode"] == "real"            # the LINK really is real
    assert "not measurements" in body["note"] or "generated by the Arduino" in body["note"]


def test_only_an_explicit_probe_makes_it_a_real_sensor_reading(client, signed_in):
    body = _post(client, signed_in, values={"ph": 6.8}, probe_attached=True).json()
    assert body["provenance"] == "real_sensor"
    assert "device_simulated" not in body["validation"]["flags"]


def test_the_link_is_recorded_so_a_reading_can_be_traced(client, signed_in):
    body = _post(client, signed_in, values={"ph": 6.8}).json()
    assert body["reading"]["link"] == "ble:hm-10"


# ------------------------------------------------------------------ location
def test_a_gps_fix_is_stored_with_the_reading(client, signed_in):
    body = _post(client, signed_in, values={"ph": 6.8}, latitude=27.1767,
                 longitude=78.0081, location_source="gps", gps_satellites=7,
                 gps_fix=True).json()
    reading = body["reading"]
    assert reading["latitude"] == 27.1767 and reading["longitude"] == 78.0081
    assert reading["location_source"] == "gps"
    assert reading["gps_satellites"] == 7 and reading["gps_fix"] is True
    assert body["validation"]["has_location"] is True


def test_half_a_coordinate_is_not_a_place(client, signed_in):
    """A latitude with no longitude would otherwise plot on the prime meridian."""
    body = _post(client, signed_in, values={"ph": 6.8}, latitude=27.1767).json()
    assert body["reading"]["latitude"] is None
    assert body["reading"]["longitude"] is None
    assert "bad_location" in body["validation"]["flags"]


def test_an_impossible_coordinate_is_dropped(client, signed_in):
    body = _post(client, signed_in, values={"ph": 6.8}, latitude=127.0,
                 longitude=78.0).json()
    assert body["reading"]["latitude"] is None
    assert "bad_location" in body["validation"]["flags"]


def test_a_hand_typed_point_is_never_recorded_as_a_gps_fix(client, signed_in):
    body = _post(client, signed_in, values={"ph": 6.8}, latitude=27.1767,
                 longitude=78.0081, location_source="made-up").json()
    assert body["reading"]["location_source"] == "manual"


def test_a_reading_with_no_location_simply_has_none(client, signed_in):
    body = _post(client, signed_in, values={"ph": 6.8}).json()
    assert body["reading"]["latitude"] is None
    assert body["validation"]["has_location"] is False
    assert "bad_location" not in body["validation"]["flags"]


# -------------------------------------------------- what the rest of the app sees
def test_the_fetched_reading_becomes_the_one_the_dashboard_reads(client, signed_in):
    _post(client, signed_in, values={"ph": 6.8, "moisture_pct": 41.0},
          parcel_id="field-7", probe_attached=True)
    latest = client.get("/api/sensors/latest?parcel_id=field-7").json()
    assert latest["available"] is True
    assert latest["reading"]["ph"] == 6.8
    assert latest["reading"]["provenance"] == "real_sensor"
    # and the agents see it through the same describe_reading the soil page uses
    bands = {p["field"]: p["band"] for p in latest["parameters"]}
    assert bands["ph"] == "optimal"
    assert bands["nitrogen_mg_kg"] == "unknown", "a value nobody sent must not get a band"


def test_the_parcel_keeps_readings_apart(client, signed_in):
    _post(client, signed_in, values={"ph": 6.0}, parcel_id="field-a")
    _post(client, signed_in, values={"ph": 7.5}, parcel_id="field-b")
    assert client.get("/api/sensors/latest?parcel_id=field-a").json()["reading"]["ph"] == 6.0
    assert client.get("/api/sensors/latest?parcel_id=field-b").json()["reading"]["ph"] == 7.5


def test_the_raw_packet_is_kept_but_bounded(client, signed_in):
    """Useful for diagnosing a sketch; not a place to store a megabyte of stream."""
    body = _post(client, signed_in, values={"ph": 6.8},
                 raw_packet="pH: 6.8\n" + "x" * 5000).json()
    assert body["reading"]["raw_packet"].startswith("pH: 6.8")
    assert len(body["reading"]["raw_packet"]) <= 2000


def test_storing_can_be_turned_off_for_a_dry_run(client, signed_in):
    body = _post(client, signed_in, values={"ph": 6.8}, parcel_id="field-dry",
                 store=False).json()
    assert body["stored"] is False
    assert client.get("/api/sensors/latest?parcel_id=field-dry").json()["available"] is False
