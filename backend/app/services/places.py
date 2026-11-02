"""Finding a place by name, and naming a place from its coordinates.

Two things the dashboard and the app both need and neither had: type a village
and have the map go there, and drop a pin and have the village, district and
state fill themselves in. Until now a farmer had to know where their own field
was on an unlabelled map, and then type their address into four boxes.

Two providers, in order
-----------------------
**Google Maps Platform** when ``GOOGLE_MAPS_API_KEY`` is set AND the project it
belongs to has billing enabled. Google knows Indian villages that nothing else
does, which is the whole reason to pay for it.

**OpenStreetMap Nominatim** otherwise. Free, no key, and already the source of
the map tiles this dashboard draws, so the names match what the farmer is
looking at. Thinner on hamlets than Google, and that is the trade.

The key never reaches the browser. A Maps JavaScript key is designed to be
public and locked to a referrer, but a Geocoding key is not: anybody who lifts
it out of a page can spend the account's quota from anywhere. So every lookup
goes through this server, which also means the app and the dashboard share one
implementation and one cache.

Nothing here invents a place. A search that finds nothing says so; it never
returns the nearest big town as though it were the village that was asked for.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..core.config import get_settings
from . import http_cache

log = logging.getLogger("saarthi.places")

GOOGLE_GEOCODE = "https://maps.googleapis.com/maps/api/geocode/json"
NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"

# Nominatim asks for an identifying User-Agent and no more than one request a
# second. The cache below does most of the rate limiting; the header is ours.
NOMINATIM_HEADERS = {"User-Agent": "Saarthi-2.0 (farm advisory; contact via visioncoders.in)"}

# A place name does not change. An hour is short enough that a correction shows
# up the same day and long enough that a farmer typing their village twice costs
# one lookup.
TTL_SECONDS = 3600
MAX_RESULTS = 6

# India only. Every farmer on this platform is in India, and letting the search
# wander means "Mathura" can come back as a street in another country.
COUNTRY = "in"

# What a Google component type means in our own words.
_GOOGLE_FIELDS = (
    ("village", ("locality", "sublocality", "sublocality_level_1", "neighborhood")),
    ("tehsil", ("administrative_area_level_3", "administrative_area_level_4")),
    ("district", ("administrative_area_level_2",)),
    ("state", ("administrative_area_level_1",)),
    ("pincode", ("postal_code",)),
)

# The same, for Nominatim's address object, in the order to try.
_OSM_FIELDS = (
    ("village", ("village", "hamlet", "town", "suburb", "city", "municipality")),
    ("tehsil", ("county", "subdistrict", "city_district")),
    ("district", ("state_district", "district")),
    ("state", ("state",)),
    ("pincode", ("postcode",)),
)


def provider() -> str:
    """Which of the two is in use. Reported, never guessed at by the caller."""
    return "google" if get_settings().has("GOOGLE_MAPS_API_KEY") else "osm"


def status() -> Dict[str, Any]:
    settings = get_settings()
    has_key = settings.has("GOOGLE_MAPS_API_KEY")
    return {
        "provider": provider(),
        "google_key": has_key,
        "note": ("" if has_key else
                 "Add GOOGLE_MAPS_API_KEY to config/api_keys.env for Google's village "
                 "coverage. Search works without it, through OpenStreetMap."),
    }


# ------------------------------------------------------------------- search
async def search(query: str, *, limit: int = MAX_RESULTS) -> Dict[str, Any]:
    """Places matching a name, best first. Empty is a real answer."""
    text = " ".join(str(query or "").split())
    if len(text) < 2:
        return {"query": text, "places": [], "provider": provider(),
                "note": "Type at least two letters."}

    if provider() == "google":
        found = await _google_search(text, limit)
        if found is not None:
            return found
        # Google refused or is unreachable. Say so once, then answer anyway.
        log.info("Google geocoding unavailable; using OpenStreetMap")
    return await _osm_search(text, limit)


async def _google_search(text: str, limit: int) -> Optional[Dict[str, Any]]:
    settings = get_settings()
    result = await http_cache.get_json(
        GOOGLE_GEOCODE, {"address": text, "key": settings.get("GOOGLE_MAPS_API_KEY"),
                         "region": COUNTRY, "components": "country:IN"},
        source="google-geocode", ttl=TTL_SECONDS)
    if not result.ok or not isinstance(result.data, dict):
        return None
    body = result.data
    state = str(body.get("status") or "")
    if state == "ZERO_RESULTS":
        return {"query": text, "places": [], "provider": "google",
                "note": "No place by that name."}
    if state != "OK":
        log.warning("Google geocoding said %s: %s", state,
                    str(body.get("error_message") or "")[:200])
        return None
    places = [_from_google(r) for r in (body.get("results") or [])[:limit]]
    return {"query": text, "places": [p for p in places if p], "provider": "google"}


async def _osm_search(text: str, limit: int) -> Dict[str, Any]:
    result = await http_cache.get_json(
        NOMINATIM_SEARCH,
        {"q": text, "format": "jsonv2", "addressdetails": 1, "limit": limit,
         "countrycodes": COUNTRY},
        source="nominatim", ttl=TTL_SECONDS, headers=NOMINATIM_HEADERS)
    if not result.ok:
        return {"query": text, "places": [], "provider": "osm",
                "note": "The place search could not be reached just now.",
                "error": result.error}
    rows = result.data if isinstance(result.data, list) else []
    return {"query": text, "places": [_from_osm(r) for r in rows[:limit]],
            "provider": "osm"}


# ------------------------------------------------------------------ reverse
async def reverse(lat: float, lon: float) -> Dict[str, Any]:
    """The address at a point: what a dropped pin is standing on."""
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return {"place": None, "provider": provider(),
                "note": "Those are not coordinates."}
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return {"place": None, "provider": provider(),
                "note": "Those coordinates are not on the earth."}

    if provider() == "google":
        settings = get_settings()
        result = await http_cache.get_json(
            GOOGLE_GEOCODE,
            {"latlng": "%.6f,%.6f" % (lat, lon),
             "key": settings.get("GOOGLE_MAPS_API_KEY")},
            source="google-reverse", ttl=TTL_SECONDS)
        body = result.data if (result.ok and isinstance(result.data, dict)) else {}
        if str(body.get("status") or "") == "OK" and body.get("results"):
            return {"place": _from_google(body["results"][0]), "provider": "google"}
        log.info("Google reverse geocoding unavailable; using OpenStreetMap")

    result = await http_cache.get_json(
        NOMINATIM_REVERSE,
        {"lat": "%.6f" % lat, "lon": "%.6f" % lon, "format": "jsonv2",
         "addressdetails": 1, "zoom": 14},
        source="nominatim-reverse", ttl=TTL_SECONDS, headers=NOMINATIM_HEADERS)
    if not result.ok or not isinstance(result.data, dict):
        return {"place": None, "provider": "osm",
                "note": "Nothing is mapped at that point."}
    return {"place": _from_osm(result.data), "provider": "osm"}


# -------------------------------------------------------------- the shapes
def _from_google(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    location = ((row.get("geometry") or {}).get("location") or {})
    if "lat" not in location or "lng" not in location:
        return None
    parts: Dict[str, str] = {}
    for component in row.get("address_components") or []:
        types = set(component.get("types") or [])
        for field, wanted in _GOOGLE_FIELDS:
            if field in parts:
                continue
            if types & set(wanted):
                parts[field] = str(component.get("long_name") or "")
    return {
        "label": str(row.get("formatted_address") or ""),
        "latitude": float(location["lat"]),
        "longitude": float(location["lng"]),
        "village": parts.get("village", ""),
        "tehsil": parts.get("tehsil", ""),
        "district": parts.get("district", ""),
        "state": parts.get("state", ""),
        "pincode": parts.get("pincode", ""),
        "source": "Google Maps",
    }


def _from_osm(row: Dict[str, Any]) -> Dict[str, Any]:
    address = row.get("address") or {}
    parts: Dict[str, str] = {}
    for field, wanted in _OSM_FIELDS:
        for key in wanted:
            if address.get(key):
                parts[field] = str(address[key])
                break
    # Nominatim's district often arrives as "Mathura District"; the farmer's own
    # records say "Mathura", and a mismatch means the mandi lookup misses.
    district = parts.get("district", "")
    for tail in (" District", " district"):
        if district.endswith(tail):
            district = district[: -len(tail)]
    return {
        "label": str(row.get("display_name") or ""),
        "latitude": float(row.get("lat") or 0.0),
        "longitude": float(row.get("lon") or 0.0),
        "village": parts.get("village", ""),
        "tehsil": parts.get("tehsil", ""),
        "district": district,
        "state": parts.get("state", ""),
        "pincode": parts.get("pincode", ""),
        "source": "OpenStreetMap",
    }
