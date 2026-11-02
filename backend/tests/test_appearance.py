"""The glass theme's backdrop: one picture for the whole server.

The farmer's own choice of theme is not here - that lives in their browser,
because it is about the screen in front of them. What is here is the picture
behind the glass theme and how long each one stays up, which an administrator
sets once for every account.

Most of what is tested is the refusals. A value from this endpoint ends up
inside a CSS ``background-image`` on every account's dashboard, so "an
administrator typed it" is not on its own a reason to put it there.
"""
from __future__ import annotations

import io

import pytest

from app.routers import appearance

# The first bytes of a 1x1 image of each type. Enough for the magic-number
# check, which is the only thing the server reads them for.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64
NOT_AN_IMAGE = b"GIF89\x00 this is not really a gif"


@pytest.fixture
def admin(client):
    from app.core import accounts

    accounts.ensure_schema()
    user = accounts.create_user(email="look@example.com", phone="9811100077",
                                password="admin-pass-77", role="admin",
                                status="active")
    return {"Authorization": "Bearer " + accounts.start_session(user["user_id"])}


@pytest.fixture
def farmer(client):
    from app.core import accounts

    accounts.ensure_schema()
    user = accounts.create_user(email="looker@example.com", phone="9811100078",
                                password="farm-pass-77", status="active")
    return {"Authorization": "Bearer " + accounts.start_session(user["user_id"])}


# --------------------------------------------------------------- the default
def test_an_unconfigured_server_still_has_a_backdrop(client, farmer):
    """The glass theme has to look like something on a box nobody has set up."""
    body = client.get("/api/appearance", headers=farmer).json()
    assert body["images"] == appearance.DEFAULT_IMAGES
    assert body["interval_seconds"] == appearance.DEFAULT_INTERVAL


def test_a_farmer_can_read_it_but_not_change_it(client, farmer):
    assert client.get("/api/appearance", headers=farmer).status_code == 200
    refused = client.put("/api/appearance", headers=farmer,
                         json={"images": ["backgrounds/field-sunrise.jpg"]})
    assert refused.status_code == 403


def test_signing_in_is_required(client):
    assert client.get("/api/appearance").status_code == 401


# ------------------------------------------------------------- what is saved
def test_an_administrator_sets_it_for_everyone(client, admin, farmer):
    saved = client.put("/api/appearance", headers=admin, json={
        "images": ["backgrounds/field-sunrise.jpg", "https://example.com/dawn.jpg"],
        "interval_seconds": 90,
    })
    assert saved.status_code == 200, saved.text

    seen = client.get("/api/appearance", headers=farmer).json()
    assert seen["images"] == ["backgrounds/field-sunrise.jpg",
                              "https://example.com/dawn.jpg"]
    assert seen["interval_seconds"] == 90


def test_zero_turns_the_slideshow_off_rather_than_running_flat_out(client, admin):
    body = client.put("/api/appearance", headers=admin, json={
        "images": ["backgrounds/field-sunrise.jpg"], "interval_seconds": 0}).json()
    assert body["interval_seconds"] == 0


def test_a_silly_interval_is_brought_into_range(client, admin):
    """A backdrop that changes every second is a strobe, not a theme."""
    fast = client.put("/api/appearance", headers=admin, json={
        "images": ["backgrounds/field-sunrise.jpg"], "interval_seconds": 1}).json()
    assert fast["interval_seconds"] == appearance.MIN_INTERVAL

    slow = client.put("/api/appearance", headers=admin, json={
        "images": ["backgrounds/field-sunrise.jpg"], "interval_seconds": 99999}).json()
    assert slow["interval_seconds"] == appearance.MAX_INTERVAL


def test_an_empty_list_falls_back_to_the_shipped_picture(client, admin):
    body = client.put("/api/appearance", headers=admin,
                      json={"images": [], "interval_seconds": 30}).json()
    assert body["images"] == appearance.DEFAULT_IMAGES


# ------------------------------------------------- what is not allowed to be a picture
@pytest.mark.parametrize("value", [
    "javascript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "../../../etc/passwd",
    "/api/appearance/image/../../saarthi.db",
    "nonsense",
])
def test_a_value_that_is_not_a_picture_is_refused(client, admin, value):
    """This string ends up inside url(...) on every account's dashboard."""
    response = client.put("/api/appearance", headers=admin,
                          json={"images": [value]})
    assert response.status_code == 400, value


def test_more_pictures_than_the_limit_are_refused(client, admin):
    response = client.put("/api/appearance", headers=admin, json={
        "images": ["https://example.com/%d.jpg" % i
                   for i in range(appearance.MAX_IMAGES + 1)]})
    assert response.status_code == 400


def test_an_uploaded_picture_that_is_not_on_this_server_is_refused(client, admin):
    response = client.put("/api/appearance", headers=admin,
                          json={"images": ["/api/appearance/image/deadbeef.jpg"]})
    assert response.status_code == 400


# ------------------------------------------------------------------ uploads
def test_an_upload_is_stored_and_can_then_be_chosen(client, admin):
    up = client.post("/api/appearance/upload", headers=admin,
                     files={"file": ("dawn.jpg", io.BytesIO(JPEG), "image/jpeg")})
    assert up.status_code == 200, up.text
    url = up.json()["image"]
    assert url.startswith("/api/appearance/image/")

    # Storing it does not put it on anybody's screen.
    assert url not in client.get("/api/appearance", headers=admin).json()["images"]

    chosen = client.put("/api/appearance", headers=admin,
                        json={"images": [url], "interval_seconds": 60})
    assert chosen.status_code == 200
    assert client.get("/api/appearance", headers=admin).json()["images"] == [url]


def test_the_stored_picture_comes_back_with_its_real_type(client, admin):
    url = client.post("/api/appearance/upload", headers=admin,
                      files={"file": ("x.png", io.BytesIO(PNG), "image/png")}).json()["image"]
    got = client.get(url)
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/png"


def test_a_file_that_is_not_an_image_is_refused_whatever_it_is_called(client, admin):
    """The extension is a claim; the bytes are the evidence."""
    response = client.post("/api/appearance/upload", headers=admin,
                           files={"file": ("payload.jpg", io.BytesIO(NOT_AN_IMAGE),
                                           "image/jpeg")})
    assert response.status_code == 400
    assert "not a JPEG" in response.json()["detail"]


def test_an_empty_upload_is_refused(client, admin):
    response = client.post("/api/appearance/upload", headers=admin,
                           files={"file": ("nothing.jpg", io.BytesIO(b""), "image/jpeg")})
    assert response.status_code == 400


def test_a_farmer_cannot_upload(client, farmer):
    response = client.post("/api/appearance/upload", headers=farmer,
                           files={"file": ("x.jpg", io.BytesIO(JPEG), "image/jpeg")})
    assert response.status_code == 403


def test_a_picture_name_that_is_not_one_is_a_404_not_a_traversal(client):
    for name in ("../saarthi.db", "..%2Fsaarthi.db", "not-here.jpg"):
        assert client.get("/api/appearance/image/" + name).status_code == 404


def test_webp_is_recognised_and_a_plain_riff_file_is_not(client, admin):
    ok = client.post("/api/appearance/upload", headers=admin,
                     files={"file": ("x.webp", io.BytesIO(WEBP), "image/webp")})
    assert ok.status_code == 200

    riff = b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 64
    bad = client.post("/api/appearance/upload", headers=admin,
                      files={"file": ("x.webp", io.BytesIO(riff), "image/webp")})
    assert bad.status_code == 400, "a WAV is a RIFF file and is not a picture"


def test_the_stored_listing_says_which_are_in_use(client, admin):
    url = client.post("/api/appearance/upload", headers=admin,
                      files={"file": ("a.jpg", io.BytesIO(JPEG), "image/jpeg")}).json()["image"]
    client.put("/api/appearance", headers=admin, json={"images": [url]})

    listing = client.get("/api/appearance/stored", headers=admin).json()
    mine = [x for x in listing["stored"] if x["image"] == url]
    assert mine and mine[0]["in_use"] is True
    assert listing["shipped"] == appearance.DEFAULT_IMAGES


def test_settings_that_are_not_json_fall_back_rather_than_raising(client, farmer):
    """A hand-edited row should not take the dashboard's backdrop down with it."""
    from app.core import storage

    storage.set_state(appearance.STATE_KEY, "{not json")
    body = client.get("/api/appearance", headers=farmer).json()
    assert body["images"] == appearance.DEFAULT_IMAGES
