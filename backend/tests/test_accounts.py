"""Accounts: how secrets are stored, who gets in, and who sees what.

The isolation tests are the important ones. Before accounts existed,
``/api/profile?phone=<anyone>`` handed over that farmer's whole record to any caller,
so "a user cannot see another user's data" is tested here from the attacker's side,
not just the happy path.
"""
from __future__ import annotations

import pytest

from app.core import accounts, farmers
from app.telephony import otp_client

EMAIL = "ramesh@example.com"
PHONE = "9876500001"
PASSWORD = "wheat-field-7"

# The codes the fixture captured, so _sign_in can finish the second step.
_SENT: list = []


@pytest.fixture
def sent_codes(monkeypatch):
    """Capture what would have gone to n8n, and hand the code back to the test."""
    sent = []

    def poster(url, payload, headers):
        sent.append({**payload, "secret": headers.get("x-otp-secret", "")})
        return {"ok": True, "channel": payload.get("channel")}

    from app.core.config import get_settings
    get_settings().values["N8N_BASE_URL"] = "https://n8n.example"
    get_settings().values["N8N_OTP_PATH"] = "/webhook/saarthi-otp"
    get_settings().values["N8N_OTP_SECRET"] = "test-otp-secret"
    otp_client.set_poster(poster)
    global _SENT
    _SENT = sent
    yield sent
    otp_client.set_poster(None)
    _SENT = []


def _register(client, sent, email=EMAIL, phone=PHONE, password=PASSWORD):
    payload = {"email": email, "phone": phone, "password": password,
               "full_name": "Ramesh"}
    payload.update(_captcha_fields(client))
    body = _post_auth(client, "/api/auth/register", payload).json()
    code = sent[-1]["code"]
    client.post("/api/auth/verify", json={"identifier": email, "code": code})
    return body


def _sign_in(client, identifier=EMAIL, password=PASSWORD, sent=None):
    """Both steps: the password, then the code.

    A correct password no longer opens a session on its own, so every helper that
    used to stop at /login has to carry on to /login-verify.
    """
    body = {"identifier": identifier, "password": password}
    body.update(_captcha_fields(client))
    response = _post_auth(client, "/api/auth/login", body)
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["otp_required"] is True
    assert "token" not in first, "the password alone must not open a session"

    code = (sent or _SENT)[-1]["code"]
    done = client.post("/api/auth/login-verify",
                       json={"identifier": identifier, "code": code})
    assert done.status_code == 200, done.text
    return done.json()["token"]


def _captcha_fields(client):
    """The public auth routes require a challenge; answer it the way a person would."""
    from app.core import captcha as _captcha
    issued = client.get("/api/auth/captcha").json()
    words = _captcha.WORDS
    numbers = [words.index(w) for w in issued["question"].lower()
               .replace("?", "").split() if w in words]
    return {"challenge_id": issued["challenge_id"], "captcha": str(sum(numbers))}


def _post_auth(client, path, body):
    """POST to a captcha-protected route with the challenge answered."""
    payload = dict(body)
    payload.update(_captcha_fields(client))
    return client.post(path, json=payload)


def _as(token):
    return {"Authorization": "Bearer " + token}


# ------------------------------------------------------------ stored secrets
def test_a_password_is_never_recoverable_from_what_is_stored():
    stored = accounts.hash_password(PASSWORD)
    assert PASSWORD not in stored
    assert stored.startswith("scrypt$")
    assert accounts.verify_password(PASSWORD, stored)
    assert not accounts.verify_password("wrong", stored)


def test_two_people_with_the_same_password_do_not_share_a_hash():
    """Per-user salt: one rainbow table must not cover two accounts."""
    assert accounts.hash_password(PASSWORD) != accounts.hash_password(PASSWORD)


def test_a_damaged_hash_fails_closed_rather_than_raising():
    for junk in ("", "nonsense", "scrypt$x$y$z$q$r", "md5$abc"):
        assert accounts.verify_password(PASSWORD, junk) is False


def test_the_session_token_is_stored_only_as_a_digest(client):
    accounts.ensure_schema()
    user = accounts.create_user(email="a@b.com", phone="9000000001",
                                password=PASSWORD, status="active")
    token = accounts.start_session(user["user_id"])
    from app.core.storage import cursor
    with cursor() as cur:
        cur.execute("SELECT token_hash FROM sessions_auth")
        stored = [r[0] for r in cur.fetchall()]
    assert token not in stored                 # a leaked table cannot be replayed
    assert accounts.session_user(token)["user_id"] == user["user_id"]


def test_the_password_hash_never_leaves_the_api(client, sent_codes):
    _register(client, sent_codes)
    token = _sign_in(client, sent=sent_codes)
    body = client.get("/api/auth/me", headers=_as(token)).json()
    assert "password_hash" not in body["user"]
    assert "password_hash" not in client.post(
        "/api/auth/login", json={"identifier": EMAIL, "password": PASSWORD}).text


# -------------------------------------------------------------- registration
def test_signing_up_needs_the_code_before_the_account_works(client, sent_codes):
    body = _post_auth(client, "/api/auth/register", {
        "email": EMAIL, "phone": PHONE, "password": PASSWORD}).json()
    assert body["status"] == "pending"
    # One code now goes to BOTH channels, so look for email among them.
    assert {c["channel"] for c in sent_codes} == {"email", "sms"}

    # a pending account cannot sign in
    blocked = _post_auth(client, "/api/auth/login", {"identifier": EMAIL, "password": PASSWORD})
    assert blocked.status_code == 403
    assert "not verified" in blocked.json()["detail"]

    ok = client.post("/api/auth/verify",
                     json={"identifier": EMAIL, "code": sent_codes[-1]["code"]})
    assert ok.status_code == 200 and ok.json()["user"]["status"] == "active"


def test_the_code_is_six_digits_and_never_echoed_back(client, sent_codes):
    body = _post_auth(client, "/api/auth/register", {
        "email": EMAIL, "phone": PHONE, "password": PASSWORD})
    code = sent_codes[-1]["code"]
    assert len(code) == 6 and code.isdigit()
    assert code not in body.text              # the response must not carry it


def test_the_address_is_masked_in_what_comes_back(client, sent_codes):
    body = _post_auth(client, "/api/auth/register", {
        "email": "ramesh.kumar@example.com", "phone": PHONE, "password": PASSWORD}).json()
    shown = body["verification"]["to"]
    assert "ramesh.kumar@example.com" not in shown      # never shown in full
    assert "@example.com" in shown and "*" in shown     # recognisable, not readable


def test_a_wrong_code_is_refused_and_eventually_locked_out(client, sent_codes):
    _post_auth(client, "/api/auth/register", {
        "email": EMAIL, "phone": PHONE, "password": PASSWORD})
    for _ in range(accounts.OTP_MAX_ATTEMPTS):
        bad = client.post("/api/auth/verify", json={"identifier": EMAIL, "code": "000000"})
        assert bad.status_code == 400
    exhausted = client.post("/api/auth/verify",
                            json={"identifier": EMAIL, "code": sent_codes[-1]["code"]})
    assert exhausted.status_code == 400
    assert "Ask for a new code" in exhausted.json()["detail"]


@pytest.mark.parametrize("field,value,fragment", [
    ("email", "not-an-email", "valid email"),
    ("phone", "123", "10-digit"),
    ("password", "short", "8 characters"),
    ("password", "12345678", "only digits"),
])
def test_bad_sign_ups_are_refused_with_a_usable_reason(client, sent_codes, field, value, fragment):
    payload = {"email": EMAIL, "phone": PHONE, "password": PASSWORD, field: value}
    response = _post_auth(client, "/api/auth/register", payload)
    assert response.status_code == 400
    assert fragment in response.json()["detail"]


def test_a_second_account_on_the_same_email_is_refused(client, sent_codes):
    _register(client, sent_codes)
    again = _post_auth(client, "/api/auth/register", {
        "email": EMAIL, "phone": "9999999999", "password": PASSWORD})
    assert again.status_code == 409


# ------------------------------------------------------------------ sign in
def test_a_wrong_password_does_not_say_which_half_was_wrong(client, sent_codes):
    _register(client, sent_codes)
    wrong_pw = _post_auth(client, "/api/auth/login", {"identifier": EMAIL, "password": "nope"})
    no_user = _post_auth(client, "/api/auth/login", {"identifier": "ghost@example.com", "password": "nope"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json()["detail"] == no_user.json()["detail"]


def test_signing_in_works_by_email_or_by_phone(client, sent_codes):
    _register(client, sent_codes)
    assert _sign_in(client, EMAIL, sent=sent_codes)
    assert _sign_in(client, PHONE, sent=sent_codes)
    assert _sign_in(client, "+91 98765 00001", sent=sent_codes)   # however it is typed


def test_a_correct_password_alone_does_not_open_a_session(client, sent_codes):
    """The whole point of the second step: a leaked password is not enough."""
    _register(client, sent_codes)
    # Verifying the signup signs the new account in, so start from a clean jar or
    # this asserts against that cookie rather than against the login.
    client.cookies.clear()
    body = _post_auth(client, "/api/auth/login",
                      {"identifier": EMAIL, "password": PASSWORD}).json()
    assert body["otp_required"] is True
    assert "token" not in body
    assert client.get("/api/auth/me").json()["signed_in"] is False


def test_a_wrong_code_does_not_open_a_session(client, sent_codes):
    _register(client, sent_codes)
    client.cookies.clear()
    _post_auth(client, "/api/auth/login", {"identifier": EMAIL, "password": PASSWORD})
    bad = client.post("/api/auth/login-verify",
                      json={"identifier": EMAIL, "code": "000000"})
    assert bad.status_code == 400
    assert client.get("/api/auth/me").json()["signed_in"] is False


def test_a_signup_code_cannot_be_used_to_sign_in(client, sent_codes):
    """Codes are scoped to their purpose, or one flow unlocks another."""
    _post_auth(client, "/api/auth/register",
               {"email": "scoped@example.com", "phone": "9876500123",
                "password": PASSWORD})
    signup_code = sent_codes[-1]["code"]
    misuse = client.post("/api/auth/login-verify",
                         json={"identifier": "scoped@example.com", "code": signup_code})
    assert misuse.status_code == 400


def test_repeated_failures_lock_the_account_for_a_while(client, sent_codes):
    _register(client, sent_codes)
    for _ in range(accounts.MAX_FAILED_LOGINS):
        _post_auth(client, "/api/auth/login", {"identifier": EMAIL, "password": "nope"})
    locked = _post_auth(client, "/api/auth/login", {"identifier": EMAIL, "password": PASSWORD})
    assert locked.status_code == 423
    assert "Try again in" in locked.json()["detail"]


def test_signing_out_makes_the_token_useless(client, sent_codes):
    _register(client, sent_codes)
    token = _sign_in(client, sent=sent_codes)
    assert client.get("/api/auth/me", headers=_as(token)).json()["signed_in"]
    client.post("/api/auth/logout", headers=_as(token))
    assert client.get("/api/auth/me", headers=_as(token)).json()["signed_in"] is False


# ----------------------------------------------------------- forgot password
def test_forgot_password_says_the_same_thing_either_way(client, sent_codes):
    _register(client, sent_codes)
    known = _post_auth(client, "/api/auth/forgot", {"identifier": EMAIL}).json()
    unknown = _post_auth(client, "/api/auth/forgot", {"identifier": "ghost@example.com"}).json()
    assert known["message"] == unknown["message"]       # no account enumeration


def test_a_reset_changes_the_password_and_signs_other_devices_out(client, sent_codes):
    _register(client, sent_codes)
    old_token = _sign_in(client, sent=sent_codes)

    _post_auth(client, "/api/auth/forgot", {"identifier": EMAIL})
    code = sent_codes[-1]["code"]
    done = client.post("/api/auth/reset", json={
        "identifier": EMAIL, "code": code, "password": "new-harvest-9"})
    assert done.status_code == 200

    assert _post_auth(client, "/api/auth/login", {"identifier": EMAIL, "password": PASSWORD}).status_code == 401
    assert _sign_in(client, EMAIL, "new-harvest-9", sent=sent_codes)
    # the session opened with the old password is gone
    assert client.get("/api/auth/me", headers=_as(old_token)).json()["signed_in"] is False


def test_a_reset_code_cannot_be_used_twice(client, sent_codes):
    _register(client, sent_codes)
    _post_auth(client, "/api/auth/forgot", {"identifier": EMAIL})
    code = sent_codes[-1]["code"]
    assert client.post("/api/auth/reset", json={
        "identifier": EMAIL, "code": code, "password": "new-harvest-9"}).status_code == 200
    again = client.post("/api/auth/reset", json={
        "identifier": EMAIL, "code": code, "password": "other-password-1"})
    assert again.status_code == 400


def test_a_signup_code_is_not_accepted_as_a_reset_code(client, sent_codes):
    """Codes are scoped to their purpose, or one email unlocks the other flow."""
    _post_auth(client, "/api/auth/register", {
        "email": EMAIL, "phone": PHONE, "password": PASSWORD})
    signup_code = sent_codes[-1]["code"]
    misuse = client.post("/api/auth/reset", json={
        "identifier": EMAIL, "code": signup_code, "password": "new-harvest-9"})
    assert misuse.status_code == 400
