"""HTTP surface: dashboard data, chat, sensors, speech and the n8n call contract."""
from __future__ import annotations

import pytest

from app.core.schemas import CallRequest
from app.telephony import n8n_client


# ------------------------------------------------------------------ system
def test_health_and_capability_report(client):
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and health["agents"] == 15

    keys = client.get("/api/keys/status").json()
    ids = {c["id"] for c in keys["capabilities"]}
    assert {"llm", "speech", "telephony", "market", "weather"} <= ids
    for capability in keys["capabilities"]:
        assert capability["without_it"]          # every gap explains itself

PHONE = "9811100001"


def test_language_list_has_fourteen_languages(client):
    """Thirteen until Nepali was added on 2026-11-02. The count is asserted
    rather than left loose because every other surface - the keypad menu, the
    picker, the site copy that says how many - is written against it."""
    data = client.get("/api/languages").json()
    assert len(data["languages"]) == 14
    assert all(l["locale"] and l["voice"] and l["dtmf"] for l in data["languages"])


def test_agent_directory_and_routing(client):
    agents = client.get("/api/agents").json()
    assert agents["count"] == 15
    assert len(agents["shared_capabilities"]) >= 4
    routing = client.get("/api/agents/routing").json()
    # Agent 15 validates whatever the others produced, so it runs with any
    # intent that runs agents. Small talk runs none - there is nothing to
    # validate about "hello" - which is the one intent exempt from this.
    assert all(15 in i["agents"] for i in routing["intents"] if i["agents"])
    assert any(not i["agents"] for i in routing["intents"]),         "small talk should reach no agents at all"


# -------------------------------------------------------------------- chat
def test_chat_returns_an_advisory_with_a_trace(client):
    response = client.post("/api/chat", json={"message": "Should I irrigate my wheat today?"})
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] and body["reply"]
    assert body["advisory"]["agents_invoked"]
    assert body["advisory"]["intent"] == "irrigation"


def test_chat_keeps_the_session_across_turns(client):
    first = client.post("/api/chat", json={"message": "Is mustard suitable here?"}).json()
    second = client.post("/api/chat", json={"message": "and the seed rate?",
                                            "session_id": first["session_id"]}).json()
    assert second["session_id"] == first["session_id"]
    detail = client.get("/api/chat/session/" + first["session_id"]).json()
    assert len(detail["turns"]) >= 4


def test_chat_rejects_an_empty_message(client):
    assert client.post("/api/chat", json={"message": "   "}).status_code == 400


def test_language_can_be_switched_mid_session(client):
    first = client.post("/api/chat", json={"message": "How is my soil?"}).json()
    switched = client.post("/api/chat/session/%s/language" % first["session_id"],
                           json={"language": "ta"}).json()
    assert switched["language"] == "ta" and switched["locale"] == "ta-IN"


# ----------------------------------------------------------------- sensors
def test_typed_readings_are_stored_and_show_in_the_history(client):
    """The simulate endpoint is gone. Typing figures in is how a reading is created
    without hardware now, and it is real data about this field."""
    created = client.post("/api/sensors/manual",
                          json={"values": {"ph": 6.4, "moisture_pct": 22.0}}).json()
    assert created["reading"]["mode"] == "real"
    assert created["reading"]["provenance"] == "farmer_reported"
    latest = client.get("/api/sensors/latest").json()
    assert latest["available"] is True and latest["reading"]["ph"] == 6.4
    history = client.get("/api/sensors/history?limit=5").json()
    assert history["count"] >= 1


def test_sensor_ingest_requires_the_device_token(client):
    packet = {"device_id": "d1", "mode": "real", "ph": 6.8, "moisture_pct": 40}
    assert client.post("/api/sensors/ingest", json=packet).status_code == 401
    ok = client.post("/api/sensors/ingest", json=packet,
                     headers={"X-Device-Token": "test-secret"})
    assert ok.status_code == 200
    assert ok.json()["reading"]["provenance"] == "real_sensor"


def test_ingest_refuses_a_simulated_packet_on_the_real_endpoint(client):
    response = client.post("/api/sensors/ingest", json={"device_id": "d", "mode": "simulation"},
                           headers={"X-Device-Token": "test-secret"})
    assert response.status_code == 400


def test_register_map_is_published_as_provisional(client):
    data = client.get("/api/sensors/register-map").json()
    assert len(data["register_map"]) == 7
    assert "provisional" in data["status"]


# ---------------------------------------------------------------- insights
def test_dashboard_overview_carries_the_provenance(client):
    client.post("/api/sensors/manual", json={"values": {
        "ph": 6.8, "moisture_pct": 41.0, "temperature_c": 24.0, "ec_ds_m": 0.6,
        "nitrogen_mg_kg": 40, "phosphorus_mg_kg": 18, "potassium_mg_kg": 180}})
    data = client.get("/api/dashboard/overview").json()
    assert data["soil"]["provenance"] == "farmer_reported"
    assert data["weather"]["available"] is True
    assert len(data["parameters"]) == 7


def test_market_endpoint_returns_records_with_dates(client):
    data = client.get("/api/market?commodity=wheat&state=Uttar Pradesh").json()
    assert data["available"] is True
    assert data["records"][0]["arrival_date"]
    assert "do not predict" in data["caveat"].lower()


def test_reports_list_and_download(client):
    client.post("/api/sensors/manual", json={"values": {"ph": 6.8, "moisture_pct": 41.0}})
    listing = client.get("/api/reports").json()
    assert any(r["id"] == "soil" and r["ready"] for r in listing["reports"])
    report = client.get("/api/reports/soil").json()
    assert report["reading"]["provenance"] == "farmer_reported"
    assert client.get("/api/reports/does-not-exist").status_code == 404


def test_a_field_that_is_not_yours_is_refused(client):
    """An unchecked field id would be a way to read another farm's readings by
    guessing one, so it is refused rather than quietly read as "whole farm"."""
    response = client.get("/api/reports?field=field-somebody-else")
    assert response.status_code == 404
    assert "your fields" in response.json()["detail"]


def test_a_report_is_headed_with_the_account_it_was_downloaded_by(client):
    """It used to read the profile stored under "dashboard-owner", so every
    farmer's report came out headed with the demo farmer's name."""
    from app.routers import insights

    client.post("/api/sensors/manual", json={"values": {"ph": 6.8, "moisture_pct": 41.0}})
    me = client.get("/api/auth/me").json()
    farm = insights._report_farm(me.get("user") or me, [], "")
    assert (farm.get("name") or farm.get("farmer_name")) != "Ramesh"


# ------------------------------------------------------------------ speech
def test_speech_reports_that_it_is_not_configured(client):
    status = client.get("/api/speech/status").json()
    assert status["configured"] is False and status["note"]
    assert client.post("/api/speech/token").status_code == 503
    assert client.post("/api/speech/tts", json={"text": "hello"}).status_code == 503


def test_ssml_uses_the_right_locale_and_voice():
    from app.speech.azure_speech import build_ssml
    ssml = build_ssml("नमस्ते", "hi")
    assert 'xml:lang="hi-IN"' in ssml and "hi-IN-SwaraNeural" in ssml


def test_audio_file_lookup_refuses_path_traversal():
    from app.speech.azure_speech import audio_file
    assert audio_file("../../config/api_keys.env") is None


# --------------------------------------------------------------- telephony
def test_call_status_describes_the_n8n_contract(client):
    status = client.get("/api/calls/status").json()
    assert status["inbound_turn_endpoint"].endswith("/api/calls/turn")
    assert status["auth"]["inbound_token_set"] is True
    assert len(status["languages"]) == 14


def test_outbound_call_is_simulated_when_n8n_is_not_configured(client):
    response = client.post("/api/calls/outbound",
                           json={"phone": "+919876543210", "language": "hi"}).json()
    assert response["simulated"] is True
    assert response["call"]["status"] == "simulated"
    assert "never dialled" in response["message"] or "nothing was dialled" in response["message"]


def test_outbound_call_ignores_the_number_in_the_body(client):
    """A call-back goes to the account's own number, whatever the body asks for.

    This used to be a 400 on a malformed number. Now the number is never taken from
    the request at all, so there is nothing to validate - and no way to make Saarthi
    dial a number that is not yours.
    """
    from app.telephony import n8n_client
    dialled = []

    def poster(url, payload, headers):
        dialled.append(payload["to"])
        return {"call_sid": "CA" + "0" * 30, "status": "queued"}

    n8n_client.set_poster(poster)
    try:
        response = client.post("/api/calls/outbound", json={"phone": "123"})
    finally:
        n8n_client.set_poster(None)
    assert response.status_code == 200
    assert dialled == ["+91" + PHONE]


@pytest.mark.asyncio
async def test_outbound_call_posts_the_full_contract_to_n8n(monkeypatch):
    sent = {}

    def poster(url, payload, headers):
        sent["url"] = url
        sent["payload"] = payload
        sent["headers"] = headers
        return {"call_sid": "CA-test-123"}

    monkeypatch.setenv("N8N_BASE_URL", "https://n8n.example.test")
    monkeypatch.setenv("N8N_WEBHOOK_TOKEN", "outbound-secret")
    n8n_client.set_poster(poster)
    record = await n8n_client.place_call(CallRequest(phone="+919876543210", language="hi",
                                                     topic="irrigation"))
    n8n_client.set_poster(None)

    assert record["mode"] == "live" and record["call_sid"] == "CA-test-123"
    assert sent["url"].startswith("https://n8n.example.test/")
    assert sent["headers"]["X-Saarthi-Token"] == "outbound-secret"
    for key in ("call_id", "to", "language", "voice", "turn_webhook", "status_webhook"):
        assert key in sent["payload"]
    assert sent["payload"]["voice"] == "hi-IN-SwaraNeural"


def test_call_turn_requires_the_shared_secret(client):
    body = {"call_sid": "CA1", "transcript": "paani kab dena hai", "phone": "+91999"}
    assert client.post("/api/calls/turn", json=body).status_code == 401
    assert client.post("/api/calls/turn", json=body,
                       headers={"X-Saarthi-Token": "wrong"}).status_code == 401


def test_call_turn_runs_the_pipeline_and_records_the_transcript(client):
    client.post("/api/sensors/simulate", json={"scenario": "dry", "seed": 9})
    body = {"call_sid": "CA-live-1", "transcript": "Should I irrigate my wheat today?",
            "phone": "+919876543210", "language": "en"}
    response = client.post("/api/calls/turn", json=body,
                           headers={"X-Saarthi-Token": "test-secret"})
    assert response.status_code == 200
    data = response.json()
    assert data["reply"] and data["intent"] == "irrigation"
    assert data["next_action"] == "gather_speech"

    # The caller is +919876543210, not the signed-in farmer, so only an
    # administrator may read that transcript.
    detail = client.get("/api/calls/" + data["call_id"],
                        headers=_admin_headers(client)).json()
    assert detail["detail"]["turns"][0]["question"].startswith("Should I irrigate")


def test_keypad_language_selection(client):
    body = {"call_sid": "CA-ivr-1", "transcript": "", "digits": "2", "phone": "+9199"}
    data = client.post("/api/calls/turn", json=body,
                       headers={"X-Saarthi-Token": "test-secret"}).json()
    assert data["language"] == "hi"
    assert data["next_action"] == "gather_speech"


def test_empty_transcript_asks_the_caller_to_repeat(client):
    body = {"call_sid": "CA-empty", "transcript": "   ", "phone": "+9199", "language": "en"}
    data = client.post("/api/calls/turn", json=body,
                       headers={"X-Saarthi-Token": "test-secret"}).json()
    assert "again" in data["reply"].lower()


def test_status_callback_updates_the_record(client):
    created = client.post("/api/calls/outbound",
                          json={"phone": "+919876543210"}).json()["call"]
    response = client.post("/api/calls/status-callback",
                           json={"call_id": created["call_id"], "status": "completed"},
                           headers={"X-Saarthi-Token": "test-secret"})
    assert response.status_code == 200
    assert client.get("/api/calls/" + created["call_id"]).json()["status"] == "completed"


def test_simulated_call_turn_produces_a_full_advisory(client):
    client.post("/api/sensors/simulate", json={"scenario": "normal", "seed": 2})
    data = client.post("/api/calls/simulate",
                       json={"question": "Mere gehu me khad kitni daalu?",
                             "language": "hi"}).json()
    assert data["simulated"] is True
    assert data["advisory"]["intent"] == "nutrient"
    assert data["audio_url"] is None            # Azure not configured in tests
    assert data["speech_note"]
    # A simulated call is filed under a placeholder number, so it shows in the
    # administrator's view of every call rather than this farmer's own.
    assert client.get("/api/calls", headers=_admin_headers(client)).json()["calls"]


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


def _admin_headers(client):
    """A session for an administrator, for the views that span every farmer."""
    from app.core import accounts as _accounts
    _accounts.ensure_schema()
    existing = _accounts.find_user("root-view@example.com")
    if not existing:
        existing = _accounts.create_user(email="root-view@example.com",
                                         phone="9811100098", password="admin-pass-77",
                                         role="admin", status="active")
    return {"Authorization": "Bearer " + _accounts.start_session(existing["user_id"])}
