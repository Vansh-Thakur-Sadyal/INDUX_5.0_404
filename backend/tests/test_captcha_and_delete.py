"""The challenge in front of the auth routes, and deleting an account for good."""
from __future__ import annotations

import pytest

from app.core import accounts, captcha, farmers, profile as profile_store, storage
from app.telephony import otp_client

EMAIL = "ramesh@example.com"
PHONE = "9876500001"
PASSWORD = "wheat-field-7"


def _login_token(client, sign_in, sent):
    """Password then code: sign-in is two steps now."""
    first = client.post("/api/auth/login", json=sign_in)
    assert first.status_code == 200, first.text
    code = sent[-1]["code"]
    done = client.post("/api/auth/login-verify",
                       json={"identifier": sign_in["identifier"], "code": code})
    assert done.status_code == 200, done.text
    return done.json()["token"]


@pytest.fixture
def sent_codes(monkeypatch):
    sent = []

    def poster(url, payload, headers):
        sent.append(payload)
        return {"ok": True, "channel": payload.get("channel")}

    from app.core.config import get_settings
    get_settings().values["N8N_BASE_URL"] = "https://n8n.example"
    get_settings().values["N8N_OTP_PATH"] = "/webhook/saarthi-otp"
    get_settings().values["N8N_OTP_SECRET"] = "test-otp-secret"
    otp_client.set_poster(poster)
    yield sent
    otp_client.set_poster(None)


def _solve(client):
    """Fetch a challenge and work out the answer the way a person would."""
    challenge = client.get("/api/auth/captcha").json()
    words = captcha.WORDS
    numbers = [words.index(w) for w in challenge["question"].lower()
               .replace("?", "").split() if w in words]
    assert len(numbers) == 2, challenge["question"]
    return {"challenge_id": challenge["challenge_id"],
            "captcha": str(sum(numbers))}


def _as(token):
    return {"Authorization": "Bearer " + token}


# ----------------------------------------------------------------- captcha
def test_the_answer_is_never_sent_to_the_browser():
    """A captcha whose answer travels with the question is decoration."""
    issued = captcha.issue()
    assert "answer" not in issued
    assert set(issued) == {"challenge_id", "question", "expires_in_minutes"}


def test_a_right_answer_passes_and_a_wrong_one_does_not(client):
    good = _solve(client)
    assert captcha.check(good["challenge_id"], good["captcha"]) == ""

    bad = _solve(client)
    assert "not right" in captcha.check(bad["challenge_id"], "99")


def test_the_word_form_of_the_answer_is_accepted(client):
    issued = captcha.issue()
    numbers = [captcha.WORDS.index(w) for w in issued["question"].lower()
               .replace("?", "").split() if w in captcha.WORDS]
    assert captcha.check(issued["challenge_id"], captcha.WORDS[sum(numbers)]) == ""


def test_a_challenge_cannot_be_used_twice(client):
    """Otherwise one challenge can be brute-forced twenty times over."""
    solved = _solve(client)
    assert captcha.check(solved["challenge_id"], solved["captcha"]) == ""
    again = captcha.check(solved["challenge_id"], solved["captcha"])
    assert "already answered" in again


def test_a_wrong_answer_also_burns_the_challenge(client):
    solved = _solve(client)
    captcha.check(solved["challenge_id"], "99")
    assert "already answered" in captcha.check(solved["challenge_id"], solved["captcha"])


def test_an_unknown_challenge_is_refused():
    assert captcha.check("cap-nonsense", "7")
    assert captcha.check("", "7")


@pytest.mark.parametrize("path,body", [
    ("/api/auth/register", {"email": EMAIL, "phone": PHONE, "password": PASSWORD}),
    ("/api/auth/login", {"identifier": EMAIL, "password": PASSWORD}),
    ("/api/auth/forgot", {"identifier": EMAIL}),
])
def test_the_public_auth_routes_demand_a_challenge(client, sent_codes, path, body):
    response = client.post(path, json=body)
    assert response.status_code == 428, path
    assert "question" in response.json()["detail"].lower()


def test_signing_up_works_once_the_challenge_is_answered(client, sent_codes):
    body = {"email": EMAIL, "phone": PHONE, "password": PASSWORD, "full_name": "Ramesh"}
    body.update(_solve(client))
    created = client.post("/api/auth/register", json=body)
    assert created.status_code == 200
    assert created.json()["status"] == "pending"


# ------------------------------------------------------------------ delete
@pytest.fixture
def farmer_with_data(client, sent_codes, profile_code):
    """A verified farmer with a profile, a field, a fact and a call."""
    body = {"email": EMAIL, "phone": PHONE, "password": PASSWORD, "full_name": "Ramesh"}
    body.update(_solve(client))
    client.post("/api/auth/register", json=body)
    client.post("/api/auth/verify",
                json={"identifier": EMAIL, "code": sent_codes[-1]["code"]})

    sign_in = {"identifier": EMAIL, "password": PASSWORD}
    sign_in.update(_solve(client))
    token = _login_token(client, sign_in, sent_codes)

    client.post("/api/profile",
                json={"code": profile_code(_as(token)), "village": "Rampur"},
                headers=_as(token))
    client.post("/api/profile/fields", json={"name": "North", "crop": "wheat"},
                headers=_as(token))
    farmers.remember(PHONE, "crop", "wheat", call_id="c1")
    farmers.append_turn("c1", PHONE, "paani kab", "kal subah", "hi")
    storage.create_call({"call_id": "call-1", "direction": "outbound",
                         "phone": "+91" + PHONE, "language": "hi", "status": "completed",
                         "mode": "live", "topic": "", "session_id": "sess-1",
                         "created_at": "2026-10-15T09:00:00+00:00", "detail": {}})
    return token


@pytest.fixture
def admin_token(client, sent_codes):
    accounts.ensure_schema()
    accounts.create_user(email="root@example.com", phone="9000000099",
                         password="admin-pass-77", role="admin", status="active")
    sign_in = {"identifier": "root@example.com", "password": "admin-pass-77"}
    sign_in.update(_solve(client))
    return _login_token(client, sign_in, sent_codes)


def test_deleting_an_account_takes_its_farm_data_with_it(client, farmer_with_data,
                                                         admin_token):
    """The farm tables are keyed on the phone number, not the user id.

    Deleting only the users row would leave the profile, fields, facts and transcripts
    behind - and hand them to the next person who registers that number.
    """
    users = client.get("/api/admin/users", headers=_as(admin_token)).json()["users"]
    victim = next(u for u in users if u["email"] == EMAIL)

    done = client.delete("/api/admin/users/" + victim["user_id"], headers=_as(admin_token))
    assert done.status_code == 200
    assert done.json()["removed"]

    assert accounts.find_user(EMAIL) is None
    assert farmers.get_farmer(PHONE) is None
    assert farmers.recall(PHONE) == []
    assert farmers.call_history(PHONE) == []
    assert profile_store.list_fields(PHONE) == []
    assert [c for c in storage.list_calls(50)
            if farmers.normalise_phone(c["phone"]) == PHONE] == []


def test_the_deleted_account_can_no_longer_sign_in(client, farmer_with_data, admin_token):
    users = client.get("/api/admin/users", headers=_as(admin_token)).json()["users"]
    victim = next(u for u in users if u["email"] == EMAIL)
    client.delete("/api/admin/users/" + victim["user_id"], headers=_as(admin_token))

    sign_in = {"identifier": EMAIL, "password": PASSWORD}
    sign_in.update(_solve(client))
    assert client.post("/api/auth/login", json=sign_in).status_code == 401
    # and their session is dead
    assert client.get("/api/auth/me", headers=_as(farmer_with_data)).json()["signed_in"] is False


def test_the_profile_may_be_kept_when_asked(client, farmer_with_data, admin_token):
    """purge=false removes the login but leaves the farm records in place."""
    users = client.get("/api/admin/users", headers=_as(admin_token)).json()["users"]
    victim = next(u for u in users if u["email"] == EMAIL)
    client.delete("/api/admin/users/%s?purge=false" % victim["user_id"],
                  headers=_as(admin_token))
    assert accounts.find_user(EMAIL) is None
    assert farmers.get_farmer(PHONE) is not None


def test_an_admin_cannot_delete_themselves(client, admin_token):
    me = client.get("/api/auth/me", headers=_as(admin_token)).json()["user"]
    refused = client.delete("/api/admin/users/" + me["user_id"], headers=_as(admin_token))
    assert refused.status_code == 400


def test_a_farmer_cannot_delete_anyone(client, farmer_with_data, admin_token):
    users = client.get("/api/admin/users", headers=_as(admin_token)).json()["users"]
    other = next(u for u in users if u["email"] == "root@example.com")
    refused = client.delete("/api/admin/users/" + other["user_id"],
                            headers=_as(farmer_with_data))
    assert refused.status_code == 403


# ------------------------------------------------------- admin bootstrap
def test_changing_the_configured_admin_number_moves_the_account(client):
    """Editing SAARTHI_ADMIN_PHONE used to change the file and nothing else."""
    accounts.ensure_schema()
    first = accounts.ensure_admin("boss@example.com", "9000000001", "admin-pass-77")
    assert first["phone"] == "9000000001"

    moved = accounts.ensure_admin("boss@example.com", "7340330988", "admin-pass-77")
    assert moved["phone"] == "7340330988"
    assert moved["user_id"] == first["user_id"]        # the same account, not a new one


def test_the_admin_is_not_moved_onto_someone_elses_number(client):
    accounts.ensure_schema()
    accounts.create_user(email="farmer@example.com", phone="9811100777",
                         password="farmer-pass-1", status="active")
    accounts.ensure_admin("boss@example.com", "9000000002", "admin-pass-77")
    kept = accounts.ensure_admin("boss@example.com", "9811100777", "admin-pass-77")
    assert kept["phone"] == "9000000002"               # left where it was


def test_no_admin_is_created_without_a_password(client):
    """An account with a guessable default password is worse than no account."""
    accounts.ensure_schema()
    assert accounts.ensure_admin("nobody@example.com", "9000000003", "") is None
    assert accounts.count_admins() == 0


# --------------------------------------------------- one code, both channels
def test_the_code_goes_to_the_email_and_the_phone(client, sent_codes):
    """The sign-in page promises both; it used to send to whichever it found first."""
    body = {"email": EMAIL, "phone": PHONE, "password": PASSWORD}
    body.update(_solve(client))
    created = client.post("/api/auth/register", json=body).json()

    channels = {c["channel"] for c in sent_codes}
    assert channels == {"email", "sms"}, channels
    assert set(created["verification"]["channels"]) == {"email", "sms"}


def test_both_messages_carry_the_SAME_code(client, sent_codes):
    """Issuing one per channel would cancel the first, so only the later would work."""
    body = {"email": EMAIL, "phone": PHONE, "password": PASSWORD}
    body.update(_solve(client))
    client.post("/api/auth/register", json=body)

    codes = {c["code"] for c in sent_codes}
    assert len(codes) == 1, codes
    # and that one code actually verifies the account
    done = client.post("/api/auth/verify",
                       json={"identifier": EMAIL, "code": codes.pop()})
    assert done.status_code == 200


def test_the_sms_goes_out_in_international_form(client, sent_codes):
    """Twilio rejects a bare 10-digit number, which is why no SMS ever arrived."""
    body = {"email": EMAIL, "phone": PHONE, "password": PASSWORD}
    body.update(_solve(client))
    client.post("/api/auth/register", json=body)

    sms = next(c for c in sent_codes if c["channel"] == "sms")
    assert sms["to"] == "+91" + PHONE


def test_one_channel_failing_is_reported_not_hidden(client, monkeypatch):
    """Half-delivered is not delivered; the farmer should know which one worked."""
    from app.telephony import otp_client as oc

    def poster(url, payload, headers):
        if payload.get("channel") == "sms":
            return {"ok": False, "reason": "carrier rejected"}
        return {"ok": True, "channel": "email"}

    from app.core.config import get_settings
    get_settings().values["N8N_BASE_URL"] = "https://n8n.example"
    get_settings().values["N8N_OTP_PATH"] = "/webhook/saarthi-otp"
    get_settings().values["N8N_OTP_SECRET"] = "test-otp-secret"
    oc.set_poster(poster)
    try:
        body = {"email": EMAIL, "phone": PHONE, "password": PASSWORD}
        body.update(_solve(client))
        created = client.post("/api/auth/register", json=body).json()
    finally:
        oc.set_poster(None)

    assert created["verification"]["channels"] == ["email"]
    assert "sms" in created["verification"]["failed"]
