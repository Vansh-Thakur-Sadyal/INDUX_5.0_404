"""Changing a profile takes a code, the same way signing in does.

A profile edit is not a cosmetic thing: the phone number decides which account a
caller reaches, and the village and district decide whose weather and whose mandi
prices the farmer is shown. Anyone who got as far as an open session could change
all of it silently.

The check is on the server, because a check on the page is one anybody can skip.
"""
from __future__ import annotations

from app.core import accounts


def _code_for(user_id, purpose="profile"):
    from app.core.storage import cursor

    with cursor() as cur:
        cur.execute("SELECT code_hash FROM otp_codes WHERE user_id=? AND purpose=?"
                    " AND consumed_at IS NULL ORDER BY id DESC LIMIT 1",
                    (user_id, purpose))
        return cur.fetchone() is not None


def test_a_change_without_a_code_is_refused(client, account):
    response = client.post("/api/profile", json={"village": "Kalsan"},
                           headers=account.headers)
    assert response.status_code == 403
    assert "code" in response.json()["detail"].lower()


def test_saving_the_same_values_needs_no_code(client, account):
    """A repaint is not an edit. Asking for a code to save what is already there
    would mean a code every time the page loads and re-submits itself."""
    first = client.post("/api/profile", json={}, headers=account.headers)
    assert first.status_code == 200
    assert first.json()["changed"] == []


def test_a_change_goes_through_with_the_right_code(client, account, monkeypatch):
    from app.core import accounts as accounts_module

    user = accounts_module.find_user(identifier=account.phone)
    issued = accounts_module.issue_otp(user["user_id"], "profile", "sms", account.phone)
    assert issued["code"]

    response = client.post("/api/profile",
                           json={"village": "Kalsan", "code": issued["code"]},
                           headers=account.headers)
    assert response.status_code == 200, response.text
    assert response.json()["saved"] is True
    assert "village" in response.json()["changed"]


def test_a_wrong_code_is_refused(client, account):
    from app.core import accounts as accounts_module

    user = accounts_module.find_user(identifier=account.phone)
    accounts_module.issue_otp(user["user_id"], "profile", "sms", account.phone)

    response = client.post("/api/profile",
                           json={"village": "Somewhere", "code": "000000"},
                           headers=account.headers)
    assert response.status_code == 403


def test_a_code_can_be_asked_for(client, account):
    response = client.post("/api/profile/edit-code", headers=account.headers)
    assert response.status_code == 200
    body = response.json()
    assert "sent" in body


# ------------------------------------- and the farmer is told what changed
def test_a_save_tells_the_farmer_what_changed(client, account, monkeypatch):
    """The only thing standing between "somebody changed my account" and nobody
    ever finding out. It names the OLD value too: "your village was changed" is
    a notification, "changed from Rampur to Kalsan" is something the farmer can
    check against what they meant to do."""
    from app.core import accounts as accounts_module
    from app.telephony import otp_client

    posted = []

    def poster(endpoint, payload, headers):
        posted.append(payload)
        return {"ok": True}

    otp_client.set_poster(poster)
    try:
        user = accounts_module.find_user(identifier=account.phone)
        first = accounts_module.issue_otp(user["user_id"], "profile", "sms", "")["code"]
        client.post("/api/profile", json={"village": "Rampur", "code": first},
                    headers=account.headers)

        posted.clear()
        second = accounts_module.issue_otp(user["user_id"], "profile", "sms", "")["code"]
        body = client.post("/api/profile",
                           json={"village": "Kalsan", "code": second},
                           headers=account.headers).json()
    finally:
        otp_client.set_poster(None)

    assert body["notified"]["lines"] == ["village: Rampur -> Kalsan"]
    # Both channels on the account, because a farmer who lost their phone still
    # has the email, and the other way round.
    assert set(body["notified"]["sent"]) == {"email", "sms"}

    # The message itself carries no code field, so nothing downstream can read a
    # notice as a one-time code.
    for payload in posted:
        assert "code" not in payload
        assert payload["kind"] == "notice"
        assert "village: Rampur -> Kalsan" in payload["lines"]


def test_an_empty_value_is_described_rather_than_left_blank(client, account):
    from app.routers.profile import _describe

    assert _describe("landmark", "", "Near the canal") == "landmark: (empty) -> Near the canal"
    assert _describe("landmark", "Near the canal", "") == "landmark: Near the canal -> (empty)"


def test_a_secret_field_is_never_read_back():
    from app.routers.profile import _describe

    assert _describe("password", "old-one", "new-one") == "password was changed"
