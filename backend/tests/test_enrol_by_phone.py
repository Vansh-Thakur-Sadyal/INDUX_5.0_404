"""Opening an account over a phone call.

A farmer with a feature phone has no browser, so the call has to be able to do it. The
code goes by SMS and never by email, and nothing is written to the profile until the
caller reads that code back.
"""
from __future__ import annotations

import pytest

from app.core import accounts, farmers, profile as profile_store
from app.telephony import otp_client

PHONE = "9811155501"
TOKEN = {"X-Saarthi-Token": "test-secret"}


@pytest.fixture
def sent(monkeypatch):
    out = []

    def poster(url, payload, headers):
        out.append(payload)
        return {"ok": True, "channel": payload.get("channel")}

    from app.core.config import get_settings
    get_settings().values["N8N_BASE_URL"] = "https://n8n.example"
    get_settings().values["N8N_OTP_PATH"] = "/webhook/saarthi-otp"
    get_settings().values["N8N_OTP_SECRET"] = "test-otp-secret"
    otp_client.set_poster(poster)
    yield out
    otp_client.set_poster(None)


def _turn(client, **body):
    body.setdefault("phone", PHONE)
    body.setdefault("language", "en")
    return client.post("/api/enrol/turn", json=body, headers=TOKEN).json()


def _run_through(client, land=False):
    """Walk the whole script the way n8n would, carrying `collected` each time."""
    answers = {"full_name": "Ramesh Kumar", "village": "Rampur", "district": "Mathura",
               "land_acres": "four", "crop": "wheat",
               "field_name": "North plot", "field_acres": "two", "field_soil": "loam"}
    state = _turn(client)
    guard = 0
    while not state.get("done") and state.get("asked") != "code":
        guard += 1
        assert guard < 20, "the script did not finish: %s" % state
        asked = state["asked"]
        if asked == "land_offer":
            said = "yes please" if land else "no, later"
        elif asked == "land_acres":
            said = "4 acres"
        elif asked == "field_acres":
            said = "2 acres"
        else:
            said = answers.get(asked, "something")
        state = _turn(client, asked=asked, transcript=said,
                      collected=state["collected"],
                      wants_land=state.get("wants_land", False))
    return state


# ------------------------------------------------------------------- script
def test_the_call_asks_for_each_detail_in_turn(client, sent):
    first = _turn(client)
    assert first["asked"] == "full_name"
    assert "name" in first["say"].lower()

    second = _turn(client, asked="full_name", transcript="Ramesh Kumar",
                   collected=first["collected"])
    assert second["collected"]["full_name"] == "Ramesh Kumar"
    assert second["asked"] == "village"


def test_the_whole_script_ends_at_the_code(client, sent):
    state = _run_through(client)
    assert state["asked"] == "code"
    assert state["collected"]["village"] == "Rampur"
    assert state["collected"]["land_acres"] == 4.0        # heard as a number


def test_the_code_goes_by_sms_and_never_by_email(client, sent):
    """This caller has given a phone number and may have no email at all."""
    _run_through(client)
    assert sent, "no code was sent"
    assert sent[-1]["channel"] == "sms"
    assert sent[-1]["to"] == "+91" + PHONE


def test_an_unclear_acreage_is_asked_again_rather_than_guessed(client, sent):
    first = _turn(client)
    collected = _turn(client, asked="full_name", transcript="Ramesh",
                      collected=first["collected"])["collected"]
    collected = _turn(client, asked="village", transcript="Rampur",
                      collected=collected)["collected"]
    collected = _turn(client, asked="district", transcript="Mathura",
                      collected=collected)["collected"]
    again = _turn(client, asked="land_acres", transcript="quite a lot really",
                  collected=collected)
    assert again["asked"] == "land_acres"
    assert "could not hear" in again.get("note", "")


# ------------------------------------------------------------------ writing
def test_nothing_is_written_until_the_code_comes_back(client, sent):
    _run_through(client)
    # the account exists but is pending, and no farm record has been made
    user = accounts.find_user(PHONE)
    assert user and user["status"] == "pending"
    assert (farmers.get_farmer(PHONE) or {}).get("village") is None
    assert profile_store.list_fields(PHONE) == []


def test_reading_the_code_back_creates_the_profile(client, sent):
    state = _run_through(client)
    code = sent[-1]["code"]
    done = client.post("/api/enrol/confirm",
                       json={"phone": PHONE, "transcript": code,
                             "collected": state["collected"], "language": "en"},
                       headers=TOKEN).json()
    assert done["ok"]
    assert accounts.find_user(PHONE)["status"] == "active"
    saved = done["profile"]["profile"]
    assert saved["village"] == "Rampur" and saved["district"] == "Mathura"
    assert saved["land_acres"] == 4.0
    assert any(f["key"] == "crop" for f in farmers.recall(PHONE))


def test_a_spoken_code_with_spaces_is_understood(client, sent):
    state = _run_through(client)
    code = sent[-1]["code"]
    spoken = " ".join(code)                       # "9 1 2 3 4 5", as a person says it
    done = client.post("/api/enrol/confirm",
                       json={"phone": PHONE, "transcript": spoken,
                             "collected": state["collected"]}, headers=TOKEN).json()
    assert done["ok"]


def test_a_wrong_code_does_not_activate_the_account(client, sent):
    state = _run_through(client)
    refused = client.post("/api/enrol/confirm",
                          json={"phone": PHONE, "transcript": "000000",
                                "collected": state["collected"]}, headers=TOKEN).json()
    assert refused["ok"] is False
    assert accounts.find_user(PHONE)["status"] == "pending"


def test_registering_a_field_on_the_call(client, sent):
    state = _run_through(client, land=True)
    code = sent[-1]["code"]
    client.post("/api/enrol/confirm",
                json={"phone": PHONE, "transcript": code,
                      "collected": state["collected"]}, headers=TOKEN)
    plots = profile_store.list_fields(PHONE)
    assert len(plots) == 1
    assert plots[0]["name"] == "North plot"
    assert plots[0]["area_acres"] == 2.0
    assert plots[0]["soil_type"] == "loam"


def test_declining_the_land_question_skips_it(client, sent):
    state = _run_through(client, land=False)
    code = sent[-1]["code"]
    client.post("/api/enrol/confirm",
                json={"phone": PHONE, "transcript": code,
                      "collected": state["collected"]}, headers=TOKEN)
    assert profile_store.list_fields(PHONE) == []


# -------------------------------------------------------------------- guards
def test_the_endpoint_needs_the_shared_secret(client):
    assert client.post("/api/enrol/turn", json={"phone": PHONE}).status_code == 401
    assert client.post("/api/enrol/confirm", json={"phone": PHONE}).status_code == 401


def test_an_existing_account_is_not_enrolled_twice(client, sent):
    accounts.ensure_schema()
    accounts.create_user(email="x@example.com", phone=PHONE, password="already-here-1",
                         status="active")
    state = _turn(client)
    assert state["done"] and state["already_registered"]


def test_the_account_created_by_phone_has_no_usable_password(client, sent):
    """It is a random one nobody holds; the farmer sets their own if they ever sign in."""
    state = _run_through(client)
    code = sent[-1]["code"]
    client.post("/api/enrol/confirm",
                json={"phone": PHONE, "transcript": code,
                      "collected": state["collected"]}, headers=TOKEN)
    user = accounts.find_user(PHONE)
    assert user["password_hash"].startswith("scrypt$")
    for guess in ("", "password", PHONE, "123456"):
        assert not accounts.verify_password(guess, user["password_hash"])
