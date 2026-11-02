"""Sensor endpoints: ingest from the Android BLE gateway, simulation, history.

The Android companion app POSTs to /api/sensors/ingest with a device token. Real
and simulated readings share one validation path and one schema, which is what
makes the simulation safe to demo.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from ..core import storage
from ..core.config import get_settings
from ..core.schemas import Provenance
from ..core.security import owned_phone, require_user
from ..sensors import source
from ..sensors.validation import (LOCATION_SOURCES, PARAMETERS, REGISTER_MAP,
                                  REGISTER_MAP_STATUS, describe_reading, validate)

router = APIRouter(prefix="/api/sensors", tags=["sensors"])

@router.get("/register-map")
async def register_map() -> Dict[str, Any]:
    return {
        "register_map": REGISTER_MAP,
        "status": REGISTER_MAP_STATUS,
        "parameters": [
            {"field": field, "label": spec[0], "unit": spec[1],
             "plausible_range": [spec[2], spec[3]], "typical_range": [spec[4], spec[5]]}
            for field, spec in PARAMETERS.items()
        ],
        # Two routes off the HM-10, not one. The browser path is what the dashboard
        # itself uses: Web Bluetooth reads the same lines the Serial Bluetooth
        # Terminal app shows, so no companion app has to exist for a reading to
        # arrive. The Android gateway stays for unattended, scheduled uploads.
        "transport": ["RS485 soil probe", "Arduino Uno", "HM-10 BLE",
                      "This dashboard over Web Bluetooth", "POST /api/sensors/ble"],
        "transport_alternate": ["RS485 soil probe", "Arduino Uno", "HM-10 BLE",
                                "Android gateway app", "POST /api/sensors/ingest"],
        # What the sketch should print for the parser to recognise it.
        "expected_lines": [
            "N: 45 mg/kg", "P: 20 mg/kg", "K: 180 mg/kg",
            "Temp: 24.5 C", "Moisture: 42.0 %", "EC: 800 uS/cm", "pH: 6.80",
            "LAT: 27.176700 LON: 78.008100 SATS: 7 FIX: 1",
        ],
    }


@router.post("/ingest")
async def ingest(payload: Dict[str, Any],
                 x_device_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Receive a real packet from the Android BLE gateway."""
    settings = get_settings()
    if settings.has("SAARTHI_INBOUND_TOKEN"):
        if x_device_token != settings.inbound_token:
            raise HTTPException(status_code=401, detail="invalid X-Device-Token")
    payload = dict(payload)
    payload.setdefault("mode", "real")
    if str(payload.get("mode")).lower() == "simulation":
        raise HTTPException(status_code=400,
                            detail="use /api/sensors/simulate for simulated readings")
    checked = validate(payload)
    reading = checked["reading"]
    stored = reading.model_dump(mode="json")
    stored["validation"] = checked["validation"]
    row_id = storage.add_reading(stored)
    return {"stored": True, "id": row_id, "validation": checked["validation"],
            "reading": stored}


@router.post("/manual")
async def manual(payload: Dict[str, Any],
                 user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """Store figures the farmer has in hand - a soil-test slip, or their own meter.

    Real data, not a simulation: it is about this field and the farmer stands behind
    it, which is exactly what FARMER_REPORTED means. Blanks stay blank. The old
    behaviour filled them from a generated scenario, and that is how a number nobody
    measured ended up on the page next to ones that were.
    """
    values_in = payload.get("values") or payload
    values: Dict[str, float] = {}
    rejected: Dict[str, str] = {}
    for field, raw in values_in.items():
        if field not in PARAMETERS or raw in (None, ""):
            continue
        try:
            values[field] = float(raw)
        except (TypeError, ValueError):
            rejected[field] = "not a number"
    if not values:
        raise HTTPException(
            status_code=400,
            detail="Enter at least one value. Accepted: " + ", ".join(PARAMETERS))

    packet: Dict[str, Any] = dict(values)
    packet["device_id"] = str(payload.get("device_id") or "TYPED-IN")
    packet["parcel_id"] = str(payload.get("parcel_id") or "field-1")
    packet["mode"] = "real"
    packet["provenance"] = Provenance.FARMER_REPORTED.value
    packet["link"] = "typed"
    for key in ("latitude", "longitude"):
        if payload.get(key) not in (None, ""):
            packet[key] = payload[key]
    if str(payload.get("location_source") or "") in LOCATION_SOURCES:
        packet["location_source"] = payload["location_source"]

    checked = validate(packet)
    stored = checked["reading"].model_dump(mode="json")
    stored["validation"] = checked["validation"]
    stored["owner_phone"] = owned_phone(user, str(payload.get("phone") or ""))
    row_id = storage.add_reading(stored) if payload.get("store", True) else None

    return {
        "stored": row_id is not None,
        "id": row_id,
        "reading": stored,
        "validation": checked["validation"],
        "parameters": describe_reading(checked["reading"]),
        "entered": sorted(values),
        "rejected": rejected,
        "provenance": stored.get("provenance"),
        "note": ("Stored %d value(s) you entered, labelled FARMER_REPORTED. Anything "
                 "you left blank stays blank - nothing was filled in for it."
                 % len(values)),
    }


@router.post("/ble")
async def ble_reading(payload: Dict[str, Any],
                      user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """Store one packet the browser read off the HM-10 over Web Bluetooth.

    This is the fetch button on the Sensor Devices page. The browser holds the BLE
    link - the server never sees the radio - and posts the parsed packet here.

    The rule that matters is in the loop below: ONLY the keys that actually arrived
    are stored. /manual deliberately fills the blanks from a scenario so a farmer with
    one soil-test figure does not have to invent the other six; doing that here would
    be the opposite of useful, because the farmer is looking at the page to find out
    what the probe said. A packet carrying nitrogen alone stores nitrogen alone, and
    every other parameter reads as "no value" rather than as a number nobody measured.
    """
    values_in = payload.get("values")
    if not isinstance(values_in, dict):
        values_in = payload
    rejected: Dict[str, str] = {}
    values: Dict[str, float] = {}
    for field, raw in values_in.items():
        if field not in PARAMETERS or raw in (None, ""):
            continue
        try:
            values[field] = float(raw)
        except (TypeError, ValueError):
            rejected[field] = "not a number"
    if not values:
        raise HTTPException(
            status_code=400,
            detail=("No readable values in that packet. Expected one or more of: "
                    + ", ".join(PARAMETERS)))

    probe_attached = bool(payload.get("probe_attached"))
    packet: Dict[str, Any] = dict(values)
    packet["device_id"] = str(payload.get("device_id") or "HM-10")
    packet["parcel_id"] = str(payload.get("parcel_id") or "field-1")
    packet["mode"] = "real"
    # Default to the honest label for the current hardware stage. The caller has to
    # say a probe is wired in before a value is recorded as a measurement.
    packet["provenance"] = (Provenance.REAL_SENSOR.value if probe_attached
                            else Provenance.DEVICE_SIMULATED.value)
    packet["link"] = "ble:hm-10"
    packet["raw_packet"] = str(payload.get("raw_packet") or "")[:2000] or None
    for key in ("latitude", "longitude", "gps_satellites", "gps_fix"):
        if payload.get(key) not in (None, ""):
            packet[key] = payload[key]
    # Not named `source`: that is the module deciding which reading is live, and
    # shadowing it here made every BLE post raise AttributeError.
    loc_source = str(payload.get("location_source") or "")
    if loc_source in LOCATION_SOURCES:
        packet["location_source"] = loc_source

    checked = validate(packet)
    stored = checked["reading"].model_dump(mode="json")
    stored["validation"] = checked["validation"]
    stored["owner_phone"] = owned_phone(user, str(payload.get("phone") or ""))
    row_id = storage.add_reading(stored) if payload.get("store", True) else None

    received = sorted(values)
    return {
        "stored": row_id is not None,
        "id": row_id,
        "reading": stored,
        "validation": checked["validation"],
        "parameters": describe_reading(checked["reading"]),
        "received": received,
        "rejected": rejected,
        "provenance": stored.get("provenance"),
        "note": ("%d of %d parameters arrived from the device. The rest are shown as no "
                 "value - nothing was filled in for them." % (len(received), len(PARAMETERS))
                 + ("" if probe_attached else
                    " Labelled DEVICE_SIMULATED: the packet is real, the numbers are "
                    "generated by the Arduino because no soil probe is wired in yet.")
),
    }


@router.get("/latest")
async def latest(parcel_id: str = "") -> Dict[str, Any]:
    """The reading the whole dashboard is working from.

    ``parcel_id`` is empty by default on purpose: the newest reading is the one the
    farmer just took, whichever plot they filed it against. Defaulting to "field-1"
    was why a reading fetched into any other field appeared on the Sensor Devices
    page and nowhere else.
    """
    payload, meta = source.current(parcel_id)
    if not payload:
        return {"available": False, "note": meta["note"]}
    checked = validate(dict(payload))
    reading = checked["reading"]
    return {
        "available": True,
        "reading": reading.model_dump(mode="json"),
        "validation": checked["validation"],
        "parameters": describe_reading(reading),
        "mode": reading.mode,
        "provenance": reading.provenance.value if hasattr(reading.provenance, "value")
        else reading.provenance,
    }


@router.get("/history")
async def history(parcel_id: str = "", limit: int = 30) -> Dict[str, Any]:
    rows = storage.recent_readings(parcel_id or None, limit)
    series: List[Dict[str, Any]] = []
    for row in reversed(rows):
        entry = {"recorded_at": row.get("recorded_at"),
                 "provenance": row.get("provenance")}
        for field in PARAMETERS:
            entry[field] = row.get(field)
        series.append(entry)
    return {"series": series, "count": len(series)}
