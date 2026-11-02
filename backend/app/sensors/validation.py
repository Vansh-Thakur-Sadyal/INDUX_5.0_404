"""Seven-parameter soil sensor: schema, plausibility checks and provenance.

This is the data-quality half of Agent 1, kept here so that the ingest API and the agent
all share exactly one definition of "valid".

Rules that matter:
* An out-of-range value is marked invalid and dropped from the usable set. It is
  never clamped into range, because a clamped value looks like a measurement.
* A reading older than FRESHNESS_LIMIT is marked stale.
* mode and provenance travel with every record, end to end.
* The register map is PROVISIONAL until the sensor manual is confirmed; that flag
  is carried in the output so nothing downstream can forget it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..core.schemas import Provenance, SensorReading

FRESHNESS_LIMIT = timedelta(hours=6)

# field -> (label, unit, plausible min, plausible max, typical agronomic low, high)
PARAMETERS: Dict[str, Tuple[str, str, float, float, float, float]] = {
    "nitrogen_mg_kg":   ("Nitrogen (N)",     "mg/kg", 0.0, 2000.0, 20.0, 60.0),
    "phosphorus_mg_kg": ("Phosphorus (P)",   "mg/kg", 0.0, 2000.0, 10.0, 25.0),
    "potassium_mg_kg":  ("Potassium (K)",    "mg/kg", 0.0, 2000.0, 120.0, 280.0),
    "temperature_c":    ("Soil temperature", "C",   -10.0, 60.0, 15.0, 35.0),
    "moisture_pct":     ("Soil moisture",    "%",     0.0, 100.0, 25.0, 60.0),
    "ec_ds_m":          ("EC",               "dS/m",  0.0, 10.0, 0.2, 1.6),
    "ph":               ("pH",               "pH",    3.0, 10.0, 6.0, 7.5),
}

# Provisional Modbus register map for a common 7-in-1 RS485 probe.
# Address, scale and order MUST be confirmed against the sensor manual before
# any reading from a real device is treated as calibrated.
REGISTER_MAP: List[Dict[str, Any]] = [
    {"register": "0x0000", "field": "moisture_pct", "scale": 0.1, "unit": "%"},
    {"register": "0x0001", "field": "temperature_c", "scale": 0.1, "unit": "C"},
    {"register": "0x0002", "field": "ec_ds_m", "scale": 0.001, "unit": "dS/m (from uS/cm)"},
    {"register": "0x0003", "field": "ph", "scale": 0.1, "unit": "pH"},
    {"register": "0x0004", "field": "nitrogen_mg_kg", "scale": 1.0, "unit": "mg/kg"},
    {"register": "0x0005", "field": "phosphorus_mg_kg", "scale": 1.0, "unit": "mg/kg"},
    {"register": "0x0006", "field": "potassium_mg_kg", "scale": 1.0, "unit": "mg/kg"},
]
REGISTER_MAP_STATUS = "provisional - confirm against the sensor manual before field use"

# Only one mode remains: a reading came from a device or from the farmer.
VALID_MODES = ("real",)

# How the coordinates on a reading were obtained. Kept separate from provenance,
# which is about the soil numbers, not about the position.
LOCATION_SOURCES = ("gps", "browser", "manual", "field_centre")


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    number = _as_float(value)
    return None if number is None else int(number)


def _as_bool(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "fix", "valid")


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def decode_registers(registers: List[int]) -> Dict[str, Optional[float]]:
    """Decode a raw Modbus holding-register block using the provisional map."""
    decoded: Dict[str, Optional[float]] = {}
    for entry in REGISTER_MAP:
        index = int(entry["register"], 16)
        if index < len(registers):
            raw = registers[index]
            decoded[entry["field"]] = round(float(raw) * float(entry["scale"]), 3)
        else:
            decoded[entry["field"]] = None
    return decoded


def validate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate one packet. Returns the record plus a validation report."""
    now = datetime.now(timezone.utc)
    mode = str(payload.get("mode", "real")).lower()
    if mode not in VALID_MODES:
        mode = "real"

    if "registers" in payload and isinstance(payload["registers"], list):
        decoded = decode_registers([int(r) for r in payload["registers"]])
        for field, value in decoded.items():
            payload.setdefault(field, value)

    errors: List[str] = []
    warnings: List[str] = []
    valid_fields: List[str] = []
    invalid_fields: List[str] = []
    missing_fields: List[str] = []
    out_of_range: Dict[str, Any] = {}
    cleaned: Dict[str, Optional[float]] = {}
    flags: List[str] = []

    for field, (label, unit, low, high, _, _) in PARAMETERS.items():
        value = _as_float(payload.get(field))
        if value is None:
            missing_fields.append(field)
            cleaned[field] = None
            continue
        if value < low or value > high:
            invalid_fields.append(field)
            out_of_range[field] = {"value": value, "allowed": [low, high], "unit": unit}
            cleaned[field] = None            # dropped, never clamped
            continue
        cleaned[field] = round(value, 3)
        valid_fields.append(field)

    if missing_fields:
        warnings.append("Missing fields: " + ", ".join(missing_fields))
        flags.append("incomplete_packet")
    if invalid_fields:
        errors.append("Out-of-range fields dropped: " + ", ".join(invalid_fields))
        flags.append("out_of_range")

    latitude = _as_float(payload.get("latitude"))
    longitude = _as_float(payload.get("longitude"))
    location_source = str(payload.get("location_source") or "") or None
    if latitude is not None and not -90.0 <= latitude <= 90.0:
        warnings.append("Latitude %s is outside -90..90; location dropped." % latitude)
        latitude = longitude = None
        flags.append("bad_location")
    if longitude is not None and not -180.0 <= longitude <= 180.0:
        warnings.append("Longitude %s is outside -180..180; location dropped." % longitude)
        latitude = longitude = None
        flags.append("bad_location")
    if (latitude is None) != (longitude is None):
        # Half a coordinate is not a place. Drop the other half rather than plot it.
        warnings.append("Only one of latitude/longitude was sent; location dropped.")
        latitude = longitude = None
        flags.append("bad_location")
    if latitude is None:
        location_source = None
    elif location_source not in LOCATION_SOURCES:
        location_source = "manual"

    recorded_at = _parse_time(payload.get("recorded_at")) or now
    age = now - recorded_at
    stale = age > FRESHNESS_LIMIT
    if stale:
        warnings.append("Reading is %.1f hours old; limit is %.0f hours."
                        % (age.total_seconds() / 3600, FRESHNESS_LIMIT.total_seconds() / 3600))
        flags.append("stale")
    if recorded_at > now + timedelta(minutes=10):
        warnings.append("Reading timestamp is in the future; device clock may be wrong.")
        flags.append("future_timestamp")

    # Where the numbers came from. REAL_SENSOR is the default because every stored
    # reading now arrives from a device or from the farmer - the simulator is gone.
    # The two overrides are the honest exceptions:
    #   DEVICE_SIMULATED - the BLE packet is real, but the Arduino is generating the
    #     seven values because no soil probe is wired in yet. Not a measurement.
    #   FARMER_REPORTED  - typed in off a soil-test slip or the farmer's own meter.
    #     Real data about this field, just not read by this device.
    provenance = Provenance.REAL_SENSOR
    declared = str(payload.get("provenance", ""))
    if declared == Provenance.DEVICE_SIMULATED.value:
        provenance = Provenance.DEVICE_SIMULATED
        flags.append("device_simulated")
    elif declared == Provenance.FARMER_REPORTED.value:
        provenance = Provenance.FARMER_REPORTED
        flags.append("farmer_reported")

    completeness = round(len(valid_fields) / len(PARAMETERS), 3)
    if not valid_fields:
        status = "invalid"
    elif errors or stale:
        status = "degraded"
    elif missing_fields:
        status = "partial"
    else:
        status = "valid"

    reading = SensorReading(
        device_id=str(payload.get("device_id", "unknown")),
        parcel_id=str(payload.get("parcel_id", "field-1")),
        mode=mode,
        provenance=provenance,
        recorded_at=recorded_at,
        received_at=now,
        scenario=payload.get("scenario"),
        seed=payload.get("seed"),
        raw_packet=payload.get("raw_packet"),
        latitude=latitude,
        longitude=longitude,
        location_source=location_source,
        gps_satellites=_as_int(payload.get("gps_satellites")),
        gps_fix=_as_bool(payload.get("gps_fix")),
        link=str(payload.get("link") or "") or None,
        **cleaned,
    )
    reading.validation = {
        "status": status,
        "completeness": completeness,
        "valid_fields": valid_fields,
        "invalid_fields": invalid_fields,
        "missing_fields": missing_fields,
        "out_of_range": out_of_range,
        "errors": errors,
        "warnings": warnings,
        "flags": flags,
        "stale": stale,
        "age_seconds": int(age.total_seconds()),
        "has_location": latitude is not None,
        "location_source": location_source,
        "register_map_status": REGISTER_MAP_STATUS,
        "checked_at": now.isoformat(),
    }
    return {"reading": reading, "validation": reading.validation}


def interpret(field: str, value: Optional[float]) -> str:
    """One-word agronomic band for the dashboard tiles."""
    spec = PARAMETERS.get(field)
    if spec is None or value is None:
        return "unknown"
    _, _, _, _, low, high = spec
    if value < low:
        return "low"
    if value > high:
        return "high"
    return "optimal"


def describe_reading(reading: SensorReading) -> List[Dict[str, Any]]:
    data = reading.model_dump(mode="json")
    rows = []
    for field, (label, unit, _, _, low, high) in PARAMETERS.items():
        value = data.get(field)
        rows.append({
            "field": field,
            "label": label,
            "unit": unit,
            "value": value,
            "band": interpret(field, value),
            "typical_range": [low, high],
        })
    return rows
