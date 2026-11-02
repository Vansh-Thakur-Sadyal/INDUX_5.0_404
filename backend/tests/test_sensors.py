"""Seven-parameter sensor: decoding, validation and provenance."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.schemas import Provenance
import reading_factory as simulator
from app.sensors.validation import (PARAMETERS, decode_registers, describe_reading,
                                    interpret, validate)


def _packet(**overrides):
    payload = {
        "device_id": "7IN1-UNO-01", "parcel_id": "field-1", "mode": "real",
        "nitrogen_mg_kg": 32, "phosphorus_mg_kg": 18, "potassium_mg_kg": 210,
        "temperature_c": 24.1, "moisture_pct": 42.0, "ec_ds_m": 0.8, "ph": 6.8,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(overrides)
    return payload


def test_all_seven_fields_decode_and_validate():
    result = validate(_packet())
    assert result["validation"]["status"] == "valid"
    assert len(result["validation"]["valid_fields"]) == 7
    assert result["reading"].provenance is Provenance.REAL_SENSOR


def test_register_block_decodes_with_documented_scaling():
    decoded = decode_registers([420, 241, 800, 68, 32, 18, 210])
    assert decoded["moisture_pct"] == 42.0        # 420 * 0.1
    assert decoded["temperature_c"] == 24.1       # 241 * 0.1
    assert decoded["ec_ds_m"] == 0.8              # 800 * 0.001 uS/cm -> dS/m
    assert decoded["ph"] == 6.8                   # 68 * 0.1
    assert decoded["nitrogen_mg_kg"] == 32


def test_ingest_accepts_raw_registers():
    result = validate({"device_id": "x", "mode": "real",
                       "registers": [420, 241, 800, 68, 32, 18, 210]})
    assert result["validation"]["status"] == "valid"
    assert result["reading"].ph == 6.8


def test_out_of_range_values_are_dropped_not_clamped():
    result = validate(_packet(ph=14.8, moisture_pct=143.0))
    assert result["reading"].ph is None
    assert result["reading"].moisture_pct is None
    assert set(result["validation"]["invalid_fields"]) == {"ph", "moisture_pct"}
    assert result["validation"]["status"] == "degraded"


def test_missing_fields_are_reported_not_invented():
    payload = _packet()
    payload.pop("phosphorus_mg_kg")
    payload.pop("ec_ds_m")
    result = validate(payload)
    assert set(result["validation"]["missing_fields"]) == {"phosphorus_mg_kg", "ec_ds_m"}
    assert result["reading"].phosphorus_mg_kg is None
    assert result["validation"]["status"] == "partial"


def test_stale_reading_is_flagged():
    old = (datetime.now(timezone.utc) - timedelta(hours=19)).isoformat()
    result = validate(_packet(recorded_at=old))
    assert result["validation"]["stale"] is True
    assert "stale" in result["validation"]["flags"]


def test_every_stored_reading_is_real_mode_now():
    """The simulator is gone, so there is no second mode to be in. What a number is
    worth is carried by its provenance, not by a global switch."""
    result = simulator.reading("normal", seed=11)
    assert result["reading"].mode == "real"
    assert result["reading"].provenance.value in ("real_sensor", "device_simulated",
                                                  "farmer_reported")
    assert "simulated" not in result["validation"]["flags"]


def test_simulation_is_reproducible_from_a_seed():
    first = simulator.packet("normal", seed=99)
    second = simulator.packet("normal", seed=99)
    for field in PARAMETERS:
        assert first[field] == second[field]


def test_a_missing_packet_is_reported_as_invalid_not_invented():
    """The factory models a device that said nothing. Nothing is what must come back."""
    assert simulator.packet("disconnected", seed=1) is None
    result = simulator.reading("disconnected")
    assert result["reading"] is None
    assert result["validation"]["status"] == "invalid"
    assert result["validation"]["completeness"] == 0.0


@pytest.mark.parametrize("scenario", list(simulator.SCENARIOS))
def test_every_scenario_passes_through_the_same_validator(scenario):
    result = simulator.reading(scenario, seed=7)
    assert "status" in result["validation"]
    if scenario == "out_of_range":
        assert result["validation"]["invalid_fields"]
    if scenario == "stale":
        assert result["validation"]["stale"] is True


def test_band_interpretation():
    assert interpret("ph", 6.8) == "optimal"
    assert interpret("moisture_pct", 10) == "low"
    assert interpret("ec_ds_m", 3.5) == "high"
    assert interpret("ph", None) == "unknown"


def test_describe_reading_covers_all_parameters():
    reading = validate(_packet())["reading"]
    rows = describe_reading(reading)
    assert len(rows) == len(PARAMETERS)
    assert all(row["band"] != "" for row in rows)
