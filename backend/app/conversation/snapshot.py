"""A small, always-ready picture of one farm, so a phone turn never waits on the chain.

The 15-agent chain is the right tool for the dashboard, where a farmer can watch a
spinner. On a call it is fatal: measured at 11 seconds, which is past Twilio's patience,
and the caller just hears silence.

So the call path reads a snapshot instead - latest validated sensor reading, cached
weather, crop and stage - and the heavy chain runs in the background afterwards, with its
findings folded into the snapshot for the next turn. The farmer gets an answer now, built
from real data, and a better-informed one thirty seconds later.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..core import storage
from ..core.schemas import FarmContext
from ..sensors.validation import PARAMETERS, interpret, validate
from ..services import weather as weather_service

log = logging.getLogger("saarthi.snapshot")

TTL_SECONDS = 600          # weather and soil do not move in ten minutes
_cache: Dict[str, "Snapshot"] = {}
_lock = asyncio.Lock()


@dataclass
class Snapshot:
    parcel_id: str
    built_at: float = field(default_factory=time.time)
    lines: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    agent_lines: List[str] = field(default_factory=list)

    @property
    def age(self) -> int:
        return int(time.time() - self.built_at)

    @property
    def fresh(self) -> bool:
        return self.age < TTL_SECONDS

    def as_prompt(self) -> str:
        """Compact enough to sit in every turn's prompt without slowing it down."""
        body = list(self.lines) + list(self.agent_lines)
        if not body:
            return "FARM DATA: none available right now."
        # The unit sentence is here because of a real call: the market agent reported
        # "median modal price 3500 per quintal" and the model spoke it as "3500 per kg",
        # which is a hundredfold error in the only number the farmer cares about.
        return ("FARM DATA - quote these numbers WITH THE UNIT EXACTLY AS WRITTEN. Never "
                "convert a unit: a price given per quintal is said per quintal, never "
                "per kilo. Never say a number that is not listed here.\n- "
                + "\n- ".join(body[:8]))


async def build(farm: FarmContext) -> Snapshot:
    snapshot = Snapshot(parcel_id=farm.parcel_id)

    reading = storage.latest_reading(farm.parcel_id)
    if reading:
        try:
            checked = validate(dict(reading))
            record = checked["reading"]
            simulated = record.mode == "simulation"
            parts = []
            for field_name in ("moisture_pct", "ph", "ec_ds_m", "temperature_c"):
                value = getattr(record, field_name, None)
                if value is None:
                    continue
                label, unit = PARAMETERS[field_name][0], PARAMETERS[field_name][1]
                parts.append("%s %s %s (%s)" % (label, value, unit,
                                                interpret(field_name, value)))
            if parts:
                snapshot.lines.append(
                    ("Soil sensor%s: " % (" [SIMULATED test data]" if simulated else ""))
                    + ", ".join(parts))
                snapshot.data["simulated_sensor"] = simulated
        except Exception as exc:                                # noqa: BLE001
            log.info("snapshot: sensor unreadable (%s)", exc)

    try:
        weather = await weather_service.get_summary(farm.latitude, farm.longitude, days=3)
        if weather.get("available"):
            current = weather.get("current", {})
            days = weather.get("forecast", [])[:3]
            rain = sum(float(d.get("rain_mm") or 0) for d in days)
            snapshot.lines.append(
                "Weather now %s, %s C, wind %s km/h; rain expected in the next three days "
                "about %.1f mm" % (current.get("condition"), current.get("temperature_c"),
                                   current.get("wind_kmh"), rain))
            snapshot.data["rain_3d_mm"] = round(rain, 1)
        else:
            snapshot.lines.append("Weather data unavailable right now")
    except Exception as exc:                                    # noqa: BLE001
        log.info("snapshot: weather unavailable (%s)", exc)

    if farm.crop:
        detail = "Crop %s" % farm.crop
        if farm.sowing_date:
            detail += ", sown %s" % farm.sowing_date
        if farm.stage:
            detail += ", stage %s" % farm.stage
        snapshot.lines.append(detail + ", %s acres, %s soil, %s irrigation"
                              % (farm.area_acres, farm.soil_type, farm.irrigation_method))
    return snapshot


async def get(farm: FarmContext, *, refresh: bool = False) -> Snapshot:
    key = "%s|%.3f|%.3f" % (farm.parcel_id, farm.latitude, farm.longitude)
    cached = _cache.get(key)
    if cached and cached.fresh and not refresh:
        return cached
    async with _lock:
        cached = _cache.get(key)
        if cached and cached.fresh and not refresh:
            return cached
        snapshot = await build(farm)
        if cached:
            snapshot.agent_lines = cached.agent_lines     # keep what the chain learned
        _cache[key] = snapshot
        return snapshot


def peek(farm: FarmContext) -> Optional[Snapshot]:
    """Whatever is cached, fresh or not - a stale number beats a silent phone line."""
    return _cache.get("%s|%.3f|%.3f" % (farm.parcel_id, farm.latitude, farm.longitude))


def store_agent_findings(farm: FarmContext, lines: List[str]) -> None:
    snapshot = peek(farm)
    if snapshot is not None:
        snapshot.agent_lines = lines[:4]


def clear() -> None:
    _cache.clear()
