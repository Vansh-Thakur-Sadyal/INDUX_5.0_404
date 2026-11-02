"""Reading packets for tests. Test infrastructure, not a product feature.

The simulator used to live in the application and doubled as the tests' fixture
factory. The simulator is gone - every stored reading now comes from a device or from
the farmer - but the tests still need a soil reading with known properties: a saline
one, an acidic one, one that must be rejected, one that is stale.

So the scenario profiles moved here, where they can only ever reach a test. The draws
are seeded, so a failure is reproducible from the seed in the test name.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

SCENARIOS: Dict[str, str] = {
    "normal": "Healthy loam within typical agronomic bands",
    "dry": "Low moisture",
    "saline": "High EC and alkaline pH",
    "acidic": "Low pH with low nutrient availability",
    "nutrient_poor": "All three NPK below typical bands",
    "unusual": "Physically possible but agronomically odd",
    "missing_fields": "Packet with two fields absent",
    "out_of_range": "Implausible values that must be rejected",
    "stale": "Valid values with an old timestamp",
    "disconnected": "No packet at all",
}

_PROFILES: Dict[str, Dict[str, Any]] = {
    "normal": {
        "nitrogen_mg_kg": (28, 55), "phosphorus_mg_kg": (12, 24),
        "potassium_mg_kg": (140, 260), "temperature_c": (18, 30),
        "moisture_pct": (30, 55), "ec_ds_m": (0.3, 1.2), "ph": (6.2, 7.3),
    },
    "dry": {
        "nitrogen_mg_kg": (22, 40), "phosphorus_mg_kg": (10, 20),
        "potassium_mg_kg": (120, 210), "temperature_c": (26, 36),
        "moisture_pct": (8, 18), "ec_ds_m": (0.4, 1.4), "ph": (6.5, 7.6),
    },
    "saline": {
        "nitrogen_mg_kg": (20, 45), "phosphorus_mg_kg": (8, 18),
        "potassium_mg_kg": (160, 300), "temperature_c": (20, 32),
        "moisture_pct": (25, 45), "ec_ds_m": (2.2, 4.5), "ph": (8.0, 8.9),
    },
    "acidic": {
        "nitrogen_mg_kg": (18, 38), "phosphorus_mg_kg": (5, 12),
        "potassium_mg_kg": (90, 170), "temperature_c": (18, 28),
        "moisture_pct": (28, 50), "ec_ds_m": (0.2, 0.7), "ph": (4.6, 5.4),
    },
    "nutrient_poor": {
        "nitrogen_mg_kg": (6, 18), "phosphorus_mg_kg": (2, 9),
        "potassium_mg_kg": (40, 110), "temperature_c": (18, 30),
        "moisture_pct": (26, 48), "ec_ds_m": (0.15, 0.6), "ph": (6.0, 7.2),
    },
    "unusual": {
        "nitrogen_mg_kg": (120, 400), "phosphorus_mg_kg": (60, 160),
        "potassium_mg_kg": (420, 900), "temperature_c": (2, 9),
        "moisture_pct": (82, 96), "ec_ds_m": (3.5, 6.0), "ph": (8.6, 9.4),
    },
}


def _draw(rng: random.Random, profile: Dict[str, Any], field: str) -> float:
    low, high = profile[field]
    value = rng.uniform(low, high)
    return round(value, 2 if field in ("ec_ds_m", "ph") else 1)


def packet(scenario: str = "normal", seed: Optional[int] = None,
           device_id: str = "TEST-7IN1", parcel_id: str = "field-1",
           provenance: str = "") -> Optional[Dict[str, Any]]:
    """One reading packet. None for the disconnected scenario."""
    scenario = scenario if scenario in SCENARIOS else "normal"
    if scenario == "disconnected":
        return None
    rng = random.Random(seed if seed is not None else random.randrange(1, 10 ** 6))
    profile = _PROFILES[scenario if scenario in _PROFILES else "normal"]

    values = {field: _draw(rng, profile, field) for field in profile}
    recorded = datetime.now(timezone.utc)

    if scenario == "missing_fields":
        values.pop("phosphorus_mg_kg", None)
        values.pop("ec_ds_m", None)
    elif scenario == "out_of_range":
        values["ph"] = 13.4          # outside 3..10, must be dropped not clamped
        values["moisture_pct"] = 182.0
    elif scenario == "stale":
        recorded = recorded - timedelta(hours=9)

    built: Dict[str, Any] = {
        "device_id": device_id,
        "parcel_id": parcel_id,
        "mode": "real",
        "recorded_at": recorded.isoformat(),
        **values,
    }
    # "device_simulated" builds the case the farm is actually in: a real BLE packet
    # whose seven values the board generated, because no soil probe is wired in.
    if provenance:
        built["provenance"] = provenance
    return built


def reading(scenario: str = "normal", seed: Optional[int] = None,
            device_id: str = "TEST-7IN1", parcel_id: str = "field-1",
            provenance: str = "") -> Dict[str, Any]:
    """A packet put through the real validation path, as the product would."""
    from app.sensors.validation import validate

    built = packet(scenario, seed, device_id, parcel_id, provenance)
    if built is None:
        return {"reading": None,
                "validation": {"status": "invalid", "errors": ["no packet received"],
                               "warnings": [], "flags": ["disconnected"],
                               "valid_fields": [], "missing_fields": [],
                               "invalid_fields": [], "completeness": 0.0}}
    return validate(built)


def series(hours: int = 24, step_hours: int = 1, scenario: str = "normal",
           seed: int = 42, parcel_id: str = "field-1") -> List[Dict[str, Any]]:
    """A run of readings over time, for trend assertions."""
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    out: List[Dict[str, Any]] = []
    steps = max(1, hours // max(step_hours, 1))
    for i in range(steps):
        built = packet(scenario, seed=rng.randrange(1, 10 ** 6), parcel_id=parcel_id)
        if built is None:
            continue
        built["recorded_at"] = (now - timedelta(hours=(steps - i) * step_hours)).isoformat()
        out.append(built)
    return out
