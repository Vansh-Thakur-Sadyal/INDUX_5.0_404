"""Finding a village, and naming the place under a pin.

Two providers and one contract. Google is preferred where the account allows
it, OpenStreetMap answers otherwise, and the caller is told which answered -
because "Mathura" from Google and "Mathura" from OSM are not always the same
row, and a farmer comparing two screens deserves to know where each came from.

The rule the whole module exists to keep: a search that finds nothing says so.
It never returns the nearest large town as though it were the village that was
asked for, because that village is where the weather, the soil and the mandi
price are all read from.
"""
from __future__ import annotations

import pytest

from app.services import http_cache, places


@pytest.fixture
def account(client):
    from app.core import accounts

    accounts.ensure_schema()
    user = accounts.create_user(email="maps@example.com", phone="9811100066",
                                password="maps-pass-66", status="active")
    return {"Authorization": "Bearer " + accounts.start_session(user["user_id"])}


GOOGLE_HIT = {
    "status": "OK",
    "results": [{
        "formatted_address": "Kalsan, Mathura, Uttar Pradesh 281401, India",
        "geometry": {"location": {"lat": 27.4924, "lng": 77.6737}},
        "address_components": [
            {"long_name": "Kalsan", "types": ["locality", "political"]},
            {"long_name": "Chhata", "types": ["administrative_area_level_3"]},
            {"long_name": "Mathura", "types": ["administrative_area_level_2"]},
            {"long_name": "Uttar Pradesh", "types": ["administrative_area_level_1"]},
            {"long_name": "281401", "types": ["postal_code"]},
        ],
    }],
}

OSM_HIT = [{
    "display_name": "Kalsan, Raikot Tahsil, Ludhiana, Punjab, India",
    "lat": "30.6512", "lon": "75.6031",
    "address": {"village": "Kalsan", "county": "Raikot Tahsil",
                "state_district": "Ludhiana District", "state": "Punjab",
                "postcode": "141109"},
}]


@pytest.fixture
def answered(monkeypatch):
    """Point the shared HTTP cache at a scripted reply per host."""
    http_cache.clear_cache()
    seen = {}

    def fetch(url, params):
        seen["url"] = url
        seen["params"] = dict(params or {})
        if "googleapis" in url:
            return seen.get("google", {"status": "REQUEST_DENIED",
                                       "error_message": "billing"})
        return seen.get("osm", [])

    http_cache.set_fetcher(fetch)
    yield seen
    http_cache.set_fetcher(None)
    http_cache.clear_cache()


# ----------------------------------------------------------- which provider
def test_google_is_used_when_a_key_is_configured(answered, monkeypatch):
    from app.core import config

    settings = config.get_settings()
    monkeypatch.setitem(settings.values, "GOOGLE_MAPS_API_KEY", "test-key")
    assert places.provider() == "google"


def test_openstreetmap_is_used_without_a_key(answered, monkeypatch):
    from app.core import config

    settings = config.get_settings()
    monkeypatch.setitem(settings.values, "GOOGLE_MAPS_API_KEY", "")
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    assert places.provider() == "osm"


# ------------------------------------------------------------------ search
@pytest.mark.asyncio
async def test_a_google_result_becomes_our_shape(answered, monkeypatch):
    from app.core import config

    monkeypatch.setitem(config.get_settings().values, "GOOGLE_MAPS_API_KEY", "test-key")
    answered["google"] = GOOGLE_HIT

    found = await places.search("Kalsan")
    assert found["provider"] == "google"
    place = found["places"][0]
    assert place["village"] == "Kalsan"
    assert place["tehsil"] == "Chhata"
    assert place["district"] == "Mathura"
    assert place["state"] == "Uttar Pradesh"
    assert place["pincode"] == "281401"
    assert place["latitude"] == pytest.approx(27.4924)
    assert place["source"] == "Google Maps"


@pytest.mark.asyncio
async def test_a_refused_google_key_falls_back_rather_than_failing(answered, monkeypatch):
    """Billing not enabled on the project is the live case: the key is real and
    every Maps call comes back REQUEST_DENIED. A farmer should still be able to
    find their village."""
    from app.core import config

    monkeypatch.setitem(config.get_settings().values, "GOOGLE_MAPS_API_KEY", "test-key")
    answered["google"] = {"status": "REQUEST_DENIED", "error_message": "billing"}
    answered["osm"] = OSM_HIT

    found = await places.search("Kalsan")
    assert found["provider"] == "osm", "a refused key took the search down with it"
    assert found["places"][0]["village"] == "Kalsan"


@pytest.mark.asyncio
async def test_the_district_suffix_osm_adds_is_taken_off(answered, monkeypatch):
    """OSM says "Ludhiana District"; the farmer's record and the mandi data both
    say "Ludhiana", and a mismatch means the price lookup finds nothing."""
    from app.core import config

    monkeypatch.setitem(config.get_settings().values, "GOOGLE_MAPS_API_KEY", "")
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    answered["osm"] = OSM_HIT

    found = await places.search("Kalsan")
    assert found["places"][0]["district"] == "Ludhiana"


@pytest.mark.asyncio
async def test_nothing_found_is_said_rather_than_approximated(answered, monkeypatch):
    from app.core import config

    monkeypatch.setitem(config.get_settings().values, "GOOGLE_MAPS_API_KEY", "")
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    answered["osm"] = []

    found = await places.search("a village that is not there")
    assert found["places"] == []


@pytest.mark.asyncio
async def test_a_one_letter_search_is_not_sent_anywhere(answered):
    found = await places.search("K")
    assert found["places"] == []
    assert "url" not in answered, "a single letter was sent to the geocoder"


@pytest.mark.asyncio
async def test_the_search_is_confined_to_india(answered, monkeypatch):
    from app.core import config

    monkeypatch.setitem(config.get_settings().values, "GOOGLE_MAPS_API_KEY", "")
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    answered["osm"] = OSM_HIT

    await places.search("Mathura")
    assert answered["params"].get("countrycodes") == "in"


# ----------------------------------------------------------------- reverse
@pytest.mark.asyncio
async def test_a_point_becomes_an_address(answered, monkeypatch):
    from app.core import config

    monkeypatch.setitem(config.get_settings().values, "GOOGLE_MAPS_API_KEY", "")
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    answered["osm"] = OSM_HIT[0]

    body = await places.reverse(30.6512, 75.6031)
    assert body["place"]["village"] == "Kalsan"
    assert body["place"]["state"] == "Punjab"


@pytest.mark.asyncio
@pytest.mark.parametrize("lat,lon", [(200.0, 0.0), (0.0, 400.0)])
async def test_coordinates_that_are_not_on_the_earth_are_refused(answered, lat, lon):
    body = await places.reverse(lat, lon)
    assert body["place"] is None


# ------------------------------------------------------------- the endpoint
def test_the_endpoint_needs_an_account(client):
    assert client.get("/api/geo/search?q=Mathura").status_code == 401
    assert client.get("/api/geo/reverse?lat=27&lon=77").status_code == 401


def test_the_key_is_never_sent_to_the_caller(client, account, answered, monkeypatch):
    """A geocoding key in a page is a key anybody can lift and spend."""
    from app.core import config

    monkeypatch.setitem(config.get_settings().values, "GOOGLE_MAPS_API_KEY", "secret-key")
    answered["google"] = GOOGLE_HIT

    body = client.get("/api/geo/search?q=Kalsan", headers=account).text
    assert "secret-key" not in body

    status = client.get("/api/geo/status", headers=account).json()
    assert status["google_key"] is True
    assert "secret-key" not in str(status)
