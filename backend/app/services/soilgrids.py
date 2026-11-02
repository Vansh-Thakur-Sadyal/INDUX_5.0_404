"""Background soil properties from ISRIC SoilGrids (keyless public API).

SoilGrids is a global model, not a measurement of this farmer's field. Every
value returned here is labelled ESTIMATED so Agent 2 presents it as background
context and never as a soil test result.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .http_cache import ServiceResult, get_json

URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"

# property -> (label, conversion factor from SoilGrids mapped units, unit)
PROPERTIES = {
    "phh2o": ("pH (water)", 0.1, "pH"),
    "soc": ("Soil organic carbon", 0.1, "g/kg"),
    "clay": ("Clay", 0.1, "%"),
    "sand": ("Sand", 0.1, "%"),
    "silt": ("Silt", 0.1, "%"),
    "cec": ("Cation exchange capacity", 0.1, "cmol(c)/kg"),
    "nitrogen": ("Total nitrogen", 0.01, "g/kg"),
}


async def fetch(latitude: float, longitude: float) -> ServiceResult:
    params = {
        "lat": round(float(latitude), 5),
        "lon": round(float(longitude), 5),
        "depth": "0-5cm",
        "value": "mean",
    }
    for prop in PROPERTIES:
        params.setdefault("property", prop)
    # SoilGrids accepts repeated property params; httpx needs a list for that.
    query = [("lat", params["lat"]), ("lon", params["lon"]),
             ("depth", "0-5cm"), ("value", "mean")]
    query += [("property", prop) for prop in PROPERTIES]
    url = URL + "?" + "&".join("%s=%s" % (k, v) for k, v in query)
    # SoilGrids is by far the slowest service here (~20 s cold, and sometimes it
    # simply does not answer). It is background context, not a measurement, so:
    # warm the cache at startup (main.py), hold a success for 24 h, and remember a
    # failure for 15 min so a bad spell cannot become the latency of every request.
    return await get_json(url, {}, source="ISRIC SoilGrids", ttl=86400,
                          timeout=10.0, failure_ttl=900)


def summarise(result: ServiceResult) -> Dict[str, Any]:
    if not result.ok or not isinstance(result.data, dict):
        return {"available": False, "source": "ISRIC SoilGrids",
                "reason": result.error or "SoilGrids unavailable"}
    layers = (result.data.get("properties", {}) or {}).get("layers", []) or []
    values: Dict[str, Any] = {}
    for layer in layers:
        name = layer.get("name")
        spec = PROPERTIES.get(name)
        if not spec:
            continue
        label, factor, unit = spec
        for depth in layer.get("depths", []) or []:
            mean = (depth.get("values", {}) or {}).get("mean")
            if mean is None:
                continue
            values[name] = {
                "label": label,
                "value": round(float(mean) * factor, 2),
                "unit": unit,
                "depth": depth.get("label", "0-5cm"),
            }
            break
    if not values:
        return {"available": False, "source": "ISRIC SoilGrids",
                "reason": "No modelled values for this location"}
    return {
        "available": True,
        "source": "ISRIC SoilGrids",
        "provenance": "estimated",
        "values": values,
        "caveat": ("SoilGrids is a global model at coarse resolution. Treat these as "
                   "background context, not as a measurement of this field."),
    }


async def get_summary(latitude: float, longitude: float) -> Dict[str, Any]:
    return summarise(await fetch(latitude, longitude))
