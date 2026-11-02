"""Weather and climate-risk data from Open-Meteo (keyless public API).

Returns current conditions, a 7-day daily forecast and reference
evapotranspiration, each with the timestamp of retrieval so Agent 5 can refuse
to use stale data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .http_cache import ServiceResult, get_json

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FRESHNESS_LIMIT_SECONDS = 3 * 3600

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
    55: "Dense drizzle", 61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Heavy freezing rain", 71: "Slight snow", 73: "Moderate snow",
    75: "Heavy snow", 80: "Rain showers", 81: "Moderate rain showers",
    82: "Violent rain showers", 95: "Thunderstorm", 96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}


def describe(code: Optional[int]) -> str:
    try:
        return WMO_CODES.get(int(code), "Unknown conditions")
    except (TypeError, ValueError):
        return "Unknown conditions"


async def fetch(latitude: float, longitude: float, days: int = 7) -> ServiceResult:
    params = {
        "latitude": round(float(latitude), 4),
        "longitude": round(float(longitude), 4),
        "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "daily": ("weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
                  "precipitation_probability_max,wind_speed_10m_max,et0_fao_evapotranspiration"),
        "timezone": "auto",
        "forecast_days": max(1, min(int(days), 16)),
    }
    return await get_json(FORECAST_URL, params, source="Open-Meteo", ttl=1800)


def summarise(result: ServiceResult) -> Dict[str, Any]:
    """Flatten the Open-Meteo payload into the shape the dashboard and agents use."""
    if not result.ok or not isinstance(result.data, dict):
        return {
            "available": False,
            "reason": result.error or "Weather service unavailable",
            "source": "Open-Meteo",
        }
    data = result.data
    current = data.get("current", {}) or {}
    daily = data.get("daily", {}) or {}
    times: List[str] = list(daily.get("time", []) or [])

    def column(name: str) -> List[Any]:
        values = daily.get(name) or []
        return list(values) + [None] * (len(times) - len(values))

    forecast = []
    for i, day in enumerate(times):
        forecast.append({
            "date": day,
            "code": column("weather_code")[i],
            "condition": describe(column("weather_code")[i]),
            "t_max": column("temperature_2m_max")[i],
            "t_min": column("temperature_2m_min")[i],
            "rain_mm": column("precipitation_sum")[i],
            "rain_probability": column("precipitation_probability_max")[i],
            "wind_max": column("wind_speed_10m_max")[i],
            "et0_mm": column("et0_fao_evapotranspiration")[i],
        })
    return {
        "available": True,
        "source": "Open-Meteo",
        "retrieved_at": datetime.fromtimestamp(result.fetched_at, tz=timezone.utc).isoformat(),
        "age_seconds": result.age_seconds,
        "stale": result.age_seconds > FRESHNESS_LIMIT_SECONDS,
        "timezone": data.get("timezone"),
        "current": {
            "temperature_c": current.get("temperature_2m"),
            "humidity_pct": current.get("relative_humidity_2m"),
            "precipitation_mm": current.get("precipitation"),
            "wind_kmh": current.get("wind_speed_10m"),
            "code": current.get("weather_code"),
            "condition": describe(current.get("weather_code")),
            "observed_at": current.get("time"),
        },
        "forecast": forecast,
    }


async def get_summary(latitude: float, longitude: float, days: int = 7) -> Dict[str, Any]:
    return summarise(await fetch(latitude, longitude, days))
