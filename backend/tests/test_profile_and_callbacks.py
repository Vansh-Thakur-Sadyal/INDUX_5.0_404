"""The farmer's own record, their plots, the memory switch, and call-back rules.

The call tests are the ones that matter most: the dashboard used to report a call as
"requested" whether or not anything had been dialled, which made a rejected number
look exactly like a call on its way.
"""
from __future__ import annotations

import pytest

from app.core import farmers, profile as profile_store, storage
from app.core.schemas import CallRequest
from app.telephony import n8n_client, scheduler

PHONE = "9876500011"

# The turn webhook is n8n calling in, so it carries the shared secret.
TURN_HEADERS = {"X-Saarthi-Token": "test-secret"}


# ------------------------------------------------------------------- profile
def test_profile_round_trips_everything_the_farmer_types(client, profile_code):
    body = client.post("/api/profile", json={
        "code": profile_code(), "phone": PHONE, "name": "Ramesh", "full_name": "Ramesh Kumar Yadav",
        "age": "47", "village": "Rampur", "tehsil": "Chhata", "district": "Mathura",
        "state": "Uttar Pradesh", "pincode": "281401", "land_acres": "4.5",
        "soil_type": "loam", "irrigation": "drip", "farming_since": "2003",
    }).json()
    saved = body["profile"]
    assert saved["full_name"] == "Ramesh Kumar Yadav"
    assert saved["age"] == 47 and isinstance(saved["age"], int)
    assert saved["land_acres"] == 4.5 and isinstance(saved["land_acres"], float)
    assert saved["district"] == "Mathura"

    again = client.get("/api/profile", params={"phone": PHONE}).json()
    assert again["profile"]["pincode"] == "281401"
    assert again["complete"]["missing"] == ["at least one field on the map"]


def test_a_number_typed_any_way_is_the_same_farmer(client, profile_code):
    client.post("/api/profile", json={"code": profile_code(),
                                      "phone": "+91 98765 00011", "village": "Rampur"})
    body = client.get("/api/profile", params={"phone": "098765-00011"}).json()
    assert body["profile"]["village"] == "Rampur"


def test_an_unparseable_number_does_not_wipe_the_stored_one(client, profile_code):
    client.post("/api/profile", json={"code": profile_code(), "phone": PHONE,
                                      "land_acres": "4.5"})
    client.post("/api/profile", json={"code": profile_code(), "phone": PHONE,
                                      "land_acres": "four and a half"})
    body = client.get("/api/profile", params={"phone": PHONE}).json()
    assert body["profile"]["land_acres"] == 4.5


# -------------------------------------------------------------------- fields
def test_fields_are_created_listed_and_deleted(client):
    made = client.post("/api/profile/fields", json={
        "phone": PHONE, "name": "North plot", "crop": "wheat", "area_acres": "2.5",
        "soil_type": "loam", "centre_lat": 27.49, "centre_lon": 77.67,
        "bounds": {"north": 27.4905, "south": 27.4895, "east": 77.6705, "west": 77.6695},
    }).json()["field"]
    assert made["crop"] == "wheat" and made["area_acres"] == 2.5
    assert made["bounds"]["north"] == 27.4905

    listing = client.get("/api/profile/fields", params={"phone": PHONE}).json()
    assert listing["count"] == 1 and listing["total_acres"] == 2.5

    assert client.delete("/api/profile/fields/" + made["field_id"]).status_code == 200
    assert client.get("/api/profile/fields", params={"phone": PHONE}).json()["count"] == 0


def test_a_field_without_a_name_still_gets_one(client):
    made = client.post("/api/profile/fields", json={"phone": PHONE, "crop": "mustard"}).json()
    assert made["field"]["name"] == "Field 1"


def test_deleting_a_field_that_is_not_there_is_a_404(client):
    assert client.delete("/api/profile/fields/field-nope").status_code == 404


# -------------------------------------------------------------------- memory
def test_erasing_memory_keeps_the_name_and_drops_the_rest(client):
    farmers.ensure_schema()
    farmers.upsert_farmer(PHONE, name="Ramesh", village="Rampur")
    farmers.remember(PHONE, "crop", "wheat", call_id="c1")
    farmers.remember(PHONE, "soil_type", "loam", call_id="c1")
    farmers.append_turn("c1", PHONE, "paani kab dein", "kal subah", "hi")

    body = client.request("DELETE", "/api/profile/memory",
                          params={"phone": PHONE}).json()

    assert body["kept_name"] == "Ramesh"
    assert body["removed"]["facts"] == 2
    assert body["removed"]["calls"] == 1
    assert farmers.recall(PHONE) == []
    assert farmers.call_history(PHONE) == []
    # the typed profile is the farmer's own data and is NOT part of "what you remember"
    assert (farmers.get_farmer(PHONE) or {}).get("village") == "Rampur"


def test_memory_can_be_erased_name_and_all(client):
    farmers.ensure_schema()
    farmers.upsert_farmer(PHONE, name="Ramesh")
    body = client.request("DELETE", "/api/profile/memory",
                          params={"phone": PHONE, "keep_name": "false"}).json()
    assert body["kept_name"] is None
    assert (farmers.get_farmer(PHONE) or {}).get("name") is None


# ---------------------------------------------------------------- call backs
@pytest.mark.parametrize("typed,expected", [
    ("7719605581", "+917719605581"),
    ("07719605581", "+917719605581"),
    ("+91 77196 05581", "+917719605581"),
    ("91-7719605581", "+917719605581"),
    ("+1 415 449 7491", "+14154497491"),
])
def test_numbers_are_normalised_to_what_twilio_accepts(typed, expected):
    """Twilio rejects anything that is not E.164, and that rejection was invisible."""
    assert n8n_client.to_e164(typed) == expected


def test_a_call_is_not_reported_as_requested_unless_twilio_took_it(client):
    """n8n answering 200 is not the same as Twilio dialling.

    This is the live fault: the farmer pressed the button, the dashboard said the call
    was on its way, and the number had in fact been rejected.
    """
    def poster(url, payload, headers):
        return {"ok": True}                      # 200, but no call SID: nothing dialled

    n8n_client.set_poster(poster)
    try:
        response = client.post("/api/calls/outbound",
                               json={"phone": "7719605581", "language": "hi"})
    finally:
        n8n_client.set_poster(None)

    # 502, not 200 and not 400: the request was fine, telephony refused it.
    assert response.status_code == 502
    detail = response.json()["detail"].lower()
    assert "sid" in detail or "did not accept" in detail

    # and the attempt is on the record as failed, not as "requested"
    attempts = [c for c in storage.list_calls(10) if c["direction"] == "outbound"]
    assert attempts and attempts[0]["status"] == "failed"


def test_a_call_twilio_accepted_is_reported_as_dialling(client):
    def poster(url, payload, headers):
        return {"call_sid": "CA" + "1" * 30, "status": "queued"}

    n8n_client.set_poster(poster)
    try:
        body = client.post("/api/calls/outbound",
                           json={"phone": "7719605581", "language": "hi"}).json()
    finally:
        n8n_client.set_poster(None)

    assert body["accepted"] and body["call"]["status"] == "queued"
    assert body["call"]["call_sid"].startswith("CA")
    # The account's own number, normalised - not the one in the request body.
    assert body["call"]["phone"] == "+91" + PHONE


def test_a_number_in_the_body_cannot_redirect_the_call(client):
    """Superseded by accounts: the body number is ignored, so there is nothing to
    validate. The call always goes to the number the account signed up with."""
    dialled = []

    def poster(url, payload, headers):
        dialled.append(payload["to"])
        return {"call_sid": "CA" + "1" * 30, "status": "queued"}

    n8n_client.set_poster(poster)
    try:
        response = client.post("/api/calls/outbound", json={"phone": "123"})
    finally:
        n8n_client.set_poster(None)
    assert response.status_code == 200
    assert dialled == ["+91" + PHONE]


def test_only_one_call_can_be_pending_at_a_time(client):
    storage.create_call({
        "call_id": "call-pending1", "direction": "outbound", "phone": "+91" + PHONE,
        "language": "hi", "status": "requested", "mode": "live", "topic": "",
        "session_id": "s1", "created_at": scheduler._now().isoformat(), "detail": {},
    })
    response = client.post("/api/calls/outbound", json={})
    assert response.status_code == 409
    assert "already" in response.json()["detail"].lower()


def test_a_stale_request_does_not_lock_the_farmer_out_for_ever(client):
    from datetime import timedelta
    old = (scheduler._now() - timedelta(minutes=scheduler.STALE_AFTER_MINUTES + 5))
    storage.create_call({
        "call_id": "call-old1", "direction": "outbound", "phone": "+917719605581",
        "language": "hi", "status": "requested", "mode": "live", "topic": "",
        "session_id": "s1", "created_at": old.isoformat(), "detail": {},
    })
    assert scheduler.pending_for("7719605581") is None


@pytest.mark.asyncio
async def test_a_delay_books_the_call_instead_of_dialling_it(client):
    farmers.ensure_schema()
    profile_store.save_profile("7719605581", {"call_delay_minutes": "15"})

    outcome = await scheduler.request_call(CallRequest(phone="7719605581", language="hi"))

    assert outcome["accepted"] and outcome["delay_minutes"] == 15
    assert outcome["call"]["status"] == "scheduled"
    assert outcome["call"]["detail"]["dial_at"]
    # nothing is due yet, so the sweeper must not dial it
    assert await scheduler.dial_due_calls() == 0


@pytest.mark.asyncio
async def test_the_sweeper_dials_a_booked_call_once_it_is_due(client):
    from datetime import timedelta
    due = (scheduler._now() - timedelta(minutes=1)).isoformat()
    storage.create_call({
        "call_id": "call-due1", "direction": "outbound", "phone": "+917719605581",
        "language": "hi", "status": "scheduled", "mode": "live", "topic": "",
        "session_id": "s-due", "created_at": scheduler._now().isoformat(),
        "detail": {"dial_at": due, "farmer_name": "Ramesh"},
    })
    dialled = []

    def poster(url, payload, headers):
        dialled.append(payload)
        return {"call_sid": "CA" + "0" * 30, "status": "queued"}

    n8n_client.set_poster(poster)
    try:
        count = await scheduler.dial_due_calls()
    finally:
        n8n_client.set_poster(None)

    assert count == 1
    assert dialled and dialled[0]["to"] == "+917719605581"
    assert storage.get_call("call-due1")["status"] == "queued"




# ------------------------------------------------------- manual sensor values
def test_manual_values_are_stored_and_reach_the_agents(client):
    """The farmer's own soil-test figures, not the same scenario every time."""
    body = client.post("/api/sensors/manual", json={
        "values": {"ph": 7.8, "moisture_pct": 18.5, "nitrogen_mg_kg": 120},
        "parcel_id": "field-1",
    }).json()

    reading = body["reading"]
    assert reading["ph"] == 7.8 and reading["moisture_pct"] == 18.5
    assert sorted(body["entered"]) == ["moisture_pct", "nitrogen_mg_kg", "ph"]

    # Labelled honestly: real data about this field, typed in rather than measured
    # by a device. There is no "simulation" mode left to file it under.
    assert reading["mode"] == "real"
    assert reading["provenance"] == "farmer_reported"

    # and it is what the chain now reads
    latest = client.get("/api/sensors/latest", params={"parcel_id": "field-1"}).json()
    assert latest["reading"]["ph"] == 7.8


def test_blank_parameters_stay_blank(client):
    """They used to be filled from a generated scenario, so a farmer who had one
    figure got six they never measured, sitting beside it and looking the same.
    With the simulator gone, a blank is a blank."""
    body = client.post("/api/sensors/manual", json={"values": {"ph": 7.1}}).json()
    reading = body["reading"]
    assert reading["ph"] == 7.1
    for field in ("nitrogen_mg_kg", "moisture_pct", "ec_ds_m"):
        assert reading[field] is None
    assert body["entered"] == ["ph"]

def test_manual_entry_needs_at_least_one_value(client):
    response = client.post("/api/sensors/manual", json={"values": {}})
    assert response.status_code == 400
    assert "at least one value" in response.json()["detail"].lower()


def test_a_non_numeric_identity_is_not_an_empty_key():
    """normalise_phone keeps the last ten DIGITS, so a text identity reduces to "".

    Used as a key that would put every such farmer in one row, sharing each other's
    fields. The API always has an account behind it now, but the voice path still
    reaches this store directly, so the guard is tested where it lives.
    """
    made = profile_store.save_field("dashboard-owner", {"name": "Home plot",
                                                        "crop": "wheat"})
    assert made["phone"] == "dashboard-owner", "stored under an empty key"
    assert profile_store.list_fields("9000000001") == []
    assert len(profile_store.list_fields("dashboard-owner")) == 1


def test_a_blank_language_does_not_destroy_the_whole_save(client, profile_code):
    """language is NOT NULL. A blank from an unfilled form used to raise
    IntegrityError and lose every other field in the same request."""
    client.post("/api/profile", json={"code": profile_code(), "phone": PHONE,
                                      "language": "hi"})
    body = client.post("/api/profile", json={
        "code": profile_code(), "phone": PHONE, "language": "",
        "village": "Rampur", "land_acres": "3",
    }).json()
    assert body["profile"]["village"] == "Rampur"      # the save went through
    assert body["profile"]["language"] == "hi"         # and the old value survived


def test_a_blank_clears_a_field_that_is_allowed_to_be_empty(client, profile_code):
    client.post("/api/profile", json={"code": profile_code(), "phone": PHONE,
                                      "landmark": "Near the canal"})
    body = client.post("/api/profile", json={"code": profile_code(), "phone": PHONE,
                                            "landmark": ""}).json()
    assert body["profile"]["landmark"] is None


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


# ------------------------------------------------------- force ending a call

def _live_call(status="in_progress", sid="CA" + "9" * 30):
    """A call Twilio is already holding, on this account's number."""
    record = {
        "call_id": "call-live-1", "direction": "outbound", "phone": "+91" + PHONE,
        "language": "hi", "status": status, "mode": "live", "topic": "",
        "session_id": "sess-live-1", "call_sid": sid,
        "created_at": "2026-10-19T04:00:00+00:00",
        "detail": {"farmer_name": "Ramesh", "turns": []},
    }
    storage.create_call(record)
    return record


@pytest.mark.asyncio
async def test_a_call_not_yet_dialled_is_genuinely_stopped(client):
    """Nothing has rung, so cancelling it really does mean nothing will."""
    scheduler_record = n8n_client.record_scheduled_call(
        CallRequest(phone="+91" + PHONE, language="hi", topic="test"),
        "2099-01-01T00:00:00+00:00")

    result = await scheduler.cancel_pending("+91" + PHONE)

    assert scheduler_record["call_id"] in result["stopped"]
    assert storage.get_call(scheduler_record["call_id"])["status"] == "cancelled"
    assert "before dialling" in result["reason"]


@pytest.mark.asyncio
async def test_a_connected_call_is_not_claimed_to_be_over(client):
    """The exact bug.

    Twilio is still on the line, so marking our row "cancelled" and reporting success
    tells the farmer the call was cut while the voice carries on talking to them. It
    stays `cancel_requested` until a turn has actually carried the instruction.
    """
    record = _live_call()

    result = await scheduler.cancel_pending("+91" + PHONE)

    assert storage.get_call(record["call_id"])["status"] == "cancel_requested"
    assert record["call_id"] in result["ending"]
    assert record["call_id"] not in result["stopped"]
    assert "next turn" in result["reason"]


@pytest.mark.asyncio
async def test_the_next_turn_hangs_the_call_up(client):
    """What actually ends it: the turn webhook is told to stop."""
    record = _live_call()
    await scheduler.cancel_pending("+91" + PHONE)

    response = client.post("/api/calls/turn",
                           json={"call_sid": record["call_sid"],
                                 "transcript": "aur batao",
                                 "phone": "+91" + PHONE, "language": "hi"},
                           headers=TURN_HEADERS)
    assert response.status_code == 200
    body = response.json()

    # The two things Twilio acts on.
    assert body["end_call"] is True
    assert body["next_action"] == "hangup"
    # And only now is the call actually over.
    assert storage.get_call(record["call_id"])["status"] == "cancelled"


@pytest.mark.asyncio
async def test_the_goodbye_comes_before_any_answer(client):
    """Once the farmer has asked for the call to stop, nothing else gets said.

    The check sits above the rest of the turn handler, so a question already in
    flight does not get answered on the way out.
    """
    record = _live_call()
    await scheduler.cancel_pending("+91" + PHONE)

    body = client.post("/api/calls/turn",
                       json={"call_sid": record["call_sid"],
                             "transcript": "gehu mein kitna paani dena chahiye",
                             "phone": "+91" + PHONE, "language": "hi"},
                       headers=TURN_HEADERS).json()

    assert body["end_call"] is True
    assert "paani" not in body["reply"].lower()
    assert "advisory" not in body


@pytest.mark.asyncio
async def test_a_ringing_call_is_reported_as_ringing_out(client):
    """No turns means no way to carry the instruction, so it rings out.

    Reporting this as "stopped" would be the same lie in a quieter place: the farmer
    watches the phone keep ringing after being told it was cut.
    """
    record = _live_call(status="ringing")

    result = await scheduler.cancel_pending("+91" + PHONE)

    assert record["call_id"] in result["ringing_out"]
    assert record["call_id"] not in result["stopped"]
    assert "ring out" in result["reason"]


@pytest.mark.asyncio
async def test_an_n8n_hangup_ends_it_at_once(client, monkeypatch):
    """When the operator has wired up a hangup webhook, no waiting for a turn."""
    from app.core.config import get_settings
    get_settings().values["N8N_BASE_URL"] = "https://n8n.example"
    get_settings().values["N8N_CALL_HANGUP_PATH"] = "/webhook/saarthi-hangup"
    record = _live_call()

    def poster(url, payload, headers):
        assert payload["call_sid"] == record["call_sid"]
        assert payload["action"] == "hangup"
        return {"hung_up": True}

    n8n_client.set_poster(poster)
    try:
        result = await scheduler.cancel_pending("+91" + PHONE)
    finally:
        n8n_client.set_poster(None)

    assert record["call_id"] in result["stopped"]
    assert storage.get_call(record["call_id"])["status"] == "cancelled"


@pytest.mark.asyncio
async def test_an_unconfirmed_hangup_falls_back_instead_of_assuming(client,
                                                                     monkeypatch):
    from app.core.config import get_settings
    get_settings().values["N8N_BASE_URL"] = "https://n8n.example"
    get_settings().values["N8N_CALL_HANGUP_PATH"] = "/webhook/saarthi-hangup"
    """A hangup we only think happened is worse than one we know did not.

    If the webhook answers without confirming, the call is NOT marked over - it goes
    down the turn path, which we can actually verify.
    """
    record = _live_call()

    def poster(url, payload, headers):
        return {"ok": False, "message": "no such call"}

    n8n_client.set_poster(poster)
    try:
        result = await scheduler.cancel_pending("+91" + PHONE)
    finally:
        n8n_client.set_poster(None)

    assert record["call_id"] not in result["stopped"]
    assert storage.get_call(record["call_id"])["status"] == "cancel_requested"


@pytest.mark.asyncio
async def test_stopping_a_call_frees_the_line_for_the_next_one(client):
    """The one-at-a-time guard must not keep holding a call that was called off."""
    _live_call()
    await scheduler.cancel_pending("+91" + PHONE)
    assert scheduler.pending_for("+91" + PHONE) is None


@pytest.mark.asyncio
async def test_cancelling_with_nothing_in_flight_says_so(client):
    result = await scheduler.cancel_pending("+91" + PHONE)
    assert result["cancelled"] == 0
    assert "nothing was in flight" in result["reason"].lower()
