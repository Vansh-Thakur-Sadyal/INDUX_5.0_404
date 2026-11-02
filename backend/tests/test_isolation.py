"""One account cannot reach another's data, and an administrator can reach all of it.

These are written from the attacker's side. Before accounts existed,
``/api/profile?phone=<anyone>`` handed over that farmer's whole record to any caller,
so each test here names a number that is not the caller's and checks what comes back.
"""
from __future__ import annotations

import pytest

from app.core import accounts, farmers, storage
from app.telephony import n8n_client, otp_client

EMAIL = "ramesh@example.com"
PHONE = "9876500001"
PASSWORD = "wheat-field-7"
OTHER_EMAIL = "sunita@example.com"
OTHER_PHONE = "9876500002"


@pytest.fixture
def sent_codes(monkeypatch):
    sent = []

    def poster(url, payload, headers):
        sent.append({**payload, "secret": headers.get("x-otp-secret", "")})
        return {"ok": True, "channel": payload.get("channel")}

    from app.core.config import get_settings
    get_settings().values["N8N_BASE_URL"] = "https://n8n.example"
    get_settings().values["N8N_OTP_PATH"] = "/webhook/saarthi-otp"
    get_settings().values["N8N_OTP_SECRET"] = "test-otp-secret"
    otp_client.set_poster(poster)
    yield sent
    otp_client.set_poster(None)


def _captcha_fields(client):
    """The public auth routes require a challenge; answer it the way a person would."""
    from app.core import captcha as _captcha
    issued = client.get("/api/auth/captcha").json()
    words = _captcha.WORDS
    numbers = [words.index(w) for w in issued["question"].lower()
               .replace("?", "").split() if w in words]
    return {"challenge_id": issued["challenge_id"], "captcha": str(sum(numbers))}


def _as(token):
    return {"Authorization": "Bearer " + token}


def _register(client, sent, email=EMAIL, phone=PHONE, password=PASSWORD):
    payload = {"email": email, "phone": phone, "password": password,
               "full_name": "Farmer"}
    payload.update(_captcha_fields(client))
    client.post("/api/auth/register", json=payload)
    client.post("/api/auth/verify", json={"identifier": email, "code": sent[-1]["code"]})


def _sign_in(client, sent, identifier=EMAIL, password=PASSWORD):
    """Password, then the code. A correct password alone no longer opens a session."""
    body = {"identifier": identifier, "password": password}
    body.update(_captcha_fields(client))
    response = client.post("/api/auth/login", json=body)
    assert response.status_code == 200, response.text
    done = client.post("/api/auth/login-verify",
                       json={"identifier": identifier, "code": sent[-1]["code"]})
    assert done.status_code == 200, done.text
    return done.json()["token"]


@pytest.fixture
def two_farmers(client, sent_codes, profile_code):
    """Ramesh and Sunita, each with a profile and a field of their own."""
    _register(client, sent_codes)
    ramesh = _sign_in(client, sent_codes, EMAIL)
    client.post("/api/profile",
                json={"code": profile_code(_as(ramesh)), "village": "Rampur",
                      "land_acres": "4.5"}, headers=_as(ramesh))
    client.post("/api/profile/fields", json={"name": "Ramesh North", "crop": "wheat"},
                headers=_as(ramesh))

    _register(client, sent_codes, email=OTHER_EMAIL, phone=OTHER_PHONE)
    sunita = _sign_in(client, sent_codes, OTHER_EMAIL)
    client.post("/api/profile",
                json={"code": profile_code(_as(sunita)), "village": "Sunitapur",
                      "land_acres": "1.2"}, headers=_as(sunita))
    client.post("/api/profile/fields", json={"name": "Sunita South", "crop": "mustard"},
                headers=_as(sunita))
    return ramesh, sunita


# ---------------------------------------------------------------- isolation
def test_naming_another_number_returns_your_own_record(client, two_farmers):
    """The attack this exists to stop: /api/profile?phone=<theirs>."""
    ramesh, _ = two_farmers
    body = client.get("/api/profile", params={"phone": OTHER_PHONE},
                      headers=_as(ramesh)).json()
    assert body["profile"]["village"] == "Rampur"
    assert body["phone"] == PHONE


def test_one_farmer_cannot_list_the_fields_of_another(client, two_farmers):
    ramesh, _ = two_farmers
    body = client.get("/api/profile/fields", params={"phone": OTHER_PHONE},
                      headers=_as(ramesh)).json()
    assert [f["name"] for f in body["fields"]] == ["Ramesh North"]


def test_writing_with_another_number_writes_to_your_own(client, two_farmers,
                                                       profile_code):
    ramesh, sunita = two_farmers
    client.post("/api/profile",
                json={"code": profile_code(_as(ramesh)), "phone": OTHER_PHONE,
                      "village": "Hijacked"}, headers=_as(ramesh))
    hers = client.get("/api/profile", headers=_as(sunita)).json()
    assert hers["profile"]["village"] == "Sunitapur"


def test_one_farmer_cannot_delete_the_field_of_another(client, two_farmers):
    ramesh, sunita = two_farmers
    hers = client.get("/api/profile/fields", headers=_as(sunita)).json()["fields"][0]
    refused = client.delete("/api/profile/fields/" + hers["field_id"], headers=_as(ramesh))
    assert refused.status_code == 403
    assert client.get("/api/profile/fields", headers=_as(sunita)).json()["count"] == 1


def test_one_farmer_cannot_erase_the_memory_of_another(client, two_farmers):
    ramesh, _ = two_farmers
    farmers.remember(OTHER_PHONE, "crop", "mustard", call_id="c1")
    client.request("DELETE", "/api/profile/memory", params={"phone": OTHER_PHONE},
                   headers=_as(ramesh))
    assert len(farmers.recall(OTHER_PHONE)) == 1


def test_the_assistant_answers_as_you_whatever_number_you_name(client, two_farmers,
                                                               fake_llm):
    ramesh, _ = two_farmers
    body = client.post("/api/chat", json={
        "message": "Where is my farm?", "phone": OTHER_PHONE}, headers=_as(ramesh)).json()
    farm = client.get("/api/chat/session/" + body["session_id"],
                      headers=_as(ramesh)).json()["summary"]["farm"]
    assert farm["village"] == "Rampur"


def test_signed_out_callers_get_nothing(client, two_farmers):
    # Signing in left a cookie on this client; drop it, or the "signed out" caller
    # is still carrying Ramesh's session and the test proves nothing.
    client.cookies.clear()
    for path in ("/api/profile", "/api/profile/fields", "/api/calls", "/api/farmers"):
        assert client.get(path).status_code == 401, path


def test_a_farmer_cannot_list_every_farmer(client, two_farmers):
    ramesh, _ = two_farmers
    assert client.get("/api/farmers", headers=_as(ramesh)).status_code == 403


def test_a_farmer_cannot_reach_the_admin_pages(client, two_farmers):
    ramesh, _ = two_farmers
    for path in ("/api/admin/users", "/api/admin/otp-status"):
        assert client.get(path, headers=_as(ramesh)).status_code == 403, path


def test_the_call_log_shows_only_your_own_calls(client, two_farmers):
    ramesh, _ = two_farmers
    for phone, call_id in ((PHONE, "call-mine"), (OTHER_PHONE, "call-hers")):
        storage.create_call({"call_id": call_id, "direction": "outbound",
                             "phone": "+91" + phone, "language": "hi",
                             "status": "completed", "mode": "live", "topic": "",
                             "session_id": call_id,
                             "created_at": "2026-10-15T09:00:00+00:00", "detail": {}})
    body = client.get("/api/calls", headers=_as(ramesh)).json()
    assert [c["call_id"] for c in body["calls"]] == ["call-mine"]
    assert body["scope"] == "mine"
    assert client.get("/api/calls/call-hers", headers=_as(ramesh)).status_code == 403


def test_a_call_back_rings_your_own_number_not_one_you_typed(client, two_farmers):
    """Otherwise the button is a way to make Saarthi dial any number at all."""
    ramesh, _ = two_farmers
    dialled = []

    def poster(url, payload, headers):
        dialled.append(payload["to"])
        return {"call_sid": "CA" + "0" * 30, "status": "queued"}

    n8n_client.set_poster(poster)
    try:
        client.post("/api/calls/outbound", json={"phone": "9999988888"},
                    headers=_as(ramesh))
    finally:
        n8n_client.set_poster(None)
    assert dialled == ["+91" + PHONE]


# -------------------------------------------------------------------- admin
@pytest.fixture
def admin_token(client, sent_codes):
    accounts.ensure_schema()
    accounts.create_user(email="admin@example.com", phone="9000000099",
                         password="admin-pass-77", role="admin", status="active")
    return _sign_in(client, sent_codes, "admin@example.com", "admin-pass-77")


def test_an_admin_sees_every_account_and_what_they_hold(client, two_farmers, admin_token):
    body = client.get("/api/admin/users", headers=_as(admin_token)).json()
    emails = {u["email"] for u in body["users"]}
    assert {EMAIL, OTHER_EMAIL} <= emails
    row = next(u for u in body["users"] if u["email"] == EMAIL)
    assert row["fields"] == 1
    assert "password_hash" not in row


def test_an_admin_can_open_one_account_in_full(client, two_farmers, admin_token):
    users = client.get("/api/admin/users", headers=_as(admin_token)).json()["users"]
    hers = next(u for u in users if u["email"] == OTHER_EMAIL)
    detail = client.get("/api/admin/users/" + hers["user_id"],
                        headers=_as(admin_token)).json()
    assert detail["profile"]["profile"]["village"] == "Sunitapur"
    assert detail["profile"]["fields"][0]["name"] == "Sunita South"


def test_an_admin_may_read_a_named_farmers_record(client, two_farmers, admin_token):
    """The one caller for whom ?phone= means what it says."""
    body = client.get("/api/profile", params={"phone": OTHER_PHONE},
                      headers=_as(admin_token)).json()
    assert body["profile"]["village"] == "Sunitapur"


def test_an_admin_cannot_strip_their_own_admin_rights(client, admin_token):
    me = client.get("/api/auth/me", headers=_as(admin_token)).json()["user"]
    refused = client.post("/api/admin/users/%s/role" % me["user_id"],
                          json={"role": "user"}, headers=_as(admin_token))
    assert refused.status_code == 400


def test_locking_an_account_signs_it_out_immediately(client, two_farmers, admin_token):
    ramesh, _ = two_farmers
    users = client.get("/api/admin/users", headers=_as(admin_token)).json()["users"]
    his = next(u for u in users if u["email"] == EMAIL)
    client.post("/api/admin/users/%s/status" % his["user_id"],
                json={"status": "locked"}, headers=_as(admin_token))
    assert client.get("/api/auth/me", headers=_as(ramesh)).json()["signed_in"] is False
