"""Build a bounded agronomy context from explicit user and sensor inputs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Callable

import requests

from .agronomy import season_for_month


LocationResolver = Callable[[float, float], str]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _message_location(message: str) -> str:
    match = re.search(
        r"\b(?:in|near|at|around)\s+([a-z][a-z .'-]{1,60})",
        message,
        re.IGNORECASE,
    )
    if not match:
        return ""
    location = re.sub(
        r"\b(today|tomorrow|now|current|weather|forecast|rain|temperature|crop|vegetable).*$",
        "",
        match.group(1),
        flags=re.IGNORECASE,
    ).strip(" ,.")
    return "" if location.casefold() in {"me", "here"} else location


def _resolve_named_location(location: str) -> tuple[float, float, str] | None:
    response = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": 1, "language": "en", "format": "json"},
        timeout=6,
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    if not results:
        return None
    place = results[0]
    latitude = float(place["latitude"])
    longitude = float(place["longitude"])
    label = ", ".join(
        part
        for part in (place.get("name"), place.get("admin1"), place.get("country"))
        if part
    )
    return latitude, longitude, label


def _fetch_weather(latitude: float, longitude: float) -> dict[str, Any] | None:
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,relative_humidity_2m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "timezone": "auto",
            "forecast_days": 3,
        },
        timeout=8,
    )
    response.raise_for_status()
    data = response.json()
    current = data["current"]
    daily = data["daily"]
    days = [
        {
            "date": daily["time"][index],
            "minimum_c": daily["temperature_2m_min"][index],
            "maximum_c": daily["temperature_2m_max"][index],
            "rain_mm": daily["precipitation_sum"][index],
        }
        for index in range(min(3, len(daily["time"])))
    ]
    return {
        "local_time": current.get("time"),
        "timezone": data.get("timezone"),
        "temperature_c": current.get("temperature_2m"),
        "humidity_percent": current.get("relative_humidity_2m"),
        "forecast": days,
        "source": "Open-Meteo",
    }


def build_agri_context(
    message: str,
    body: dict[str, Any],
    farm_context: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    location_resolver: LocationResolver | None = None,
    sensor_telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve season, approximate location, live weather, and current sensor inputs."""
    farm_context = farm_context if isinstance(farm_context, dict) else {}
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    latitude = _number(body.get("lat"))
    longitude = _number(body.get("lon"))
    if (
        latitude is None
        or longitude is None
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        latitude = longitude = None

    location = str(
        body.get("location") or body.get("state") or _message_location(message)
    ).strip()[:120]
    location_source = "user-provided" if location else ""
    if latitude is not None and longitude is not None:
        location_source = "browser GPS"
        if location_resolver:
            try:
                location = str(location_resolver(round(latitude, 3), round(longitude, 3)) or "")
            except (requests.RequestException, ValueError, TypeError) as error:
                print(f"[WARN] Agronomy GPS locality lookup failed: {type(error).__name__}")
        if not location:
            location = f"{latitude:.3f}, {longitude:.3f}"
    elif location:
        try:
            resolved = _resolve_named_location(location)
            if resolved:
                latitude, longitude, location = resolved
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            print(f"[WARN] Agronomy location lookup failed: {type(error).__name__}")

    weather = None
    if latitude is not None and longitude is not None:
        try:
            weather = _fetch_weather(latitude, longitude)
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as error:
            print(f"[WARN] Agronomy weather lookup failed: {type(error).__name__}")

    current_date = now.date().isoformat()
    if weather and weather.get("local_time"):
        local_date = str(weather["local_time"]).split("T", 1)[0]
        try:
            current_date = datetime.fromisoformat(local_date).date().isoformat()
        except ValueError:
            pass
    month = int(current_date[5:7])
    requested_season = str(body.get("season") or farm_context.get("season") or "").strip().title()
    season = requested_season if requested_season in {"Kharif", "Rabi", "Zaid"} else season_for_month(month)
    region = location or "India (regional location unavailable)"

    reading_keys = ("nitrogen", "phosphorus", "potassium", "soil_ph", "soil_ec", "soil_moisture", "temperature", "humidity")
    readings = {
        key: value
        for key in reading_keys
        if (value := _number(farm_context.get(key, body.get(key)))) is not None
    }
    reading_source = str(
        farm_context.get("input_source") or farm_context.get("source") or "browser context"
    )[:120]
    telemetry_time = _parse_timestamp((sensor_telemetry or {}).get("timestamp"))
    saved_time = _parse_timestamp(farm_context.get("timestamp"))
    active_telemetry = bool(
        telemetry_time
        and telemetry_time <= now
        and now - telemetry_time <= timedelta(hours=6)
    )
    explicitly_entered = "currently entered" in reading_source.casefold()
    if active_telemetry and not explicitly_entered and (
        saved_time is None or telemetry_time > saved_time
    ):
        telemetry_keys = {
            "nitrogen": "nitrogen",
            "phosphorus": "phosphorus",
            "potassium": "potassium",
            "soil_ph": "ph",
            "soil_ec": "ec",
            "soil_moisture": "soil_moisture",
            "temperature": "temperature",
            "humidity": "humidity",
        }
        telemetry_readings = {
            key: value
            for key, source_key in telemetry_keys.items()
            if (value := _number((sensor_telemetry or {}).get(source_key))) is not None
        }
        readings.update(telemetry_readings)
        reading_source = str((sensor_telemetry or {}).get("source") or "active HM-10 sensor")
        if not farm_context.get("npk_unit") and not farm_context.get("unit"):
            farm_context = {**farm_context, "npk_unit": "mg/kg"}
    return {
        "current_date": current_date,
        "month": month,
        "season": season,
        "season_basis": (
            "explicitly supplied"
            if requested_season in {"Kharif", "Rabi", "Zaid"}
            else "broad India calendar inferred from local date; district sowing windows vary"
        ),
        "region": region,
        "location_source": location_source or "not available",
        "weather": weather,
        "readings": readings,
        "npk_unit": str(
            farm_context.get("npk_unit")
            or farm_context.get("unit")
            or body.get("npk_unit")
            or body.get("unit")
            or "mg/kg"
        ).strip().lower(),
        "crop": str(body.get("crop") or farm_context.get("crop") or "").strip()[:100],
        "soil_type": str(farm_context.get("soil_type") or "").strip()[:60],
        "irrigation": str(farm_context.get("irrigation") or "").strip()[:60],
        "crop_stage": str(farm_context.get("crop_stage") or "").strip()[:60],
        "reading_source": reading_source[:120],
        "sensor_timestamp": (
            telemetry_time.isoformat() if active_telemetry else None
        ),
    }