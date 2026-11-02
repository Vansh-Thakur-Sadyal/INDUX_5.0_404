"""Dashboard data endpoints: overview, weather, market, soil background, reports.

Every figure returned here carries where it came from. The front end renders that
label next to the number, so a simulated reading can never look like a measurement.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response

from ..core import farmers, profile as profile_store, storage
from ..core.security import current_user, owned_phone, require_user
from ..sensors import source as sensor_source
from ..core.config import get_settings
from ..core.schemas import FarmContext
from ..sensors.validation import PARAMETERS, describe_reading, interpret, validate
from ..services import market as market_service
from ..services import soilgrids as soil_service
from ..services import weather as weather_service

router = APIRouter(prefix="/api", tags=["insights"])

DEFAULT_FARM = FarmContext()


def farm_for(user: Optional[Dict[str, Any]] = None, phone: str = "",
             parcel_id: str = "") -> FarmContext:
    """The signed-in farmer as a FarmContext, falling back field by field.

    Field by field rather than all or nothing: a farmer who has entered a village but
    no soil type should see their village and a sensible default for the rest, not the
    demo village because one box is blank.
    """
    key = profile_store._key(owned_phone(user, phone) if user else phone)
    record = (farmers.get_farmer(key) or {}) if key else {}
    if not record:
        return DEFAULT_FARM

    plots = profile_store.list_fields(key)
    # The plot the dashboard is showing, not just the first one on file. Each field
    # has its own crop, its own position and therefore its own weather, so taking
    # plots[0] regardless made every field look like Field 1.
    first = next((p for p in plots if p.get("field_id") == parcel_id), None) \
        or (plots[0] if plots else {})
    data = DEFAULT_FARM.model_dump()

    for src, dest in (("name", "farmer_name"), ("village", "village"),
                      ("district", "district"), ("state", "state"),
                      ("soil_type", "soil_type"), ("language", "language"),
                      ("irrigation", "irrigation_method")):
        value = record.get(src)
        if value not in (None, ""):
            data[dest] = value

    acres = record.get("land_acres")
    if acres not in (None, "", 0):
        try:
            data["area_acres"] = float(acres)
        except (TypeError, ValueError):
            pass
    if key:
        data["phone"] = key

    # The mapped plot is the best location we have; it beats a district centroid.
    if first.get("centre_lat") not in (None, "") and first.get("centre_lon") not in (None, ""):
        try:
            data["latitude"] = float(first["centre_lat"])
            data["longitude"] = float(first["centre_lon"])
        except (TypeError, ValueError):
            pass
    if first.get("crop"):
        data["crop"] = first["crop"]
    if first.get("sowing_date"):
        data["sowing_date"] = first["sowing_date"]
    if first.get("field_id"):
        data["parcel_id"] = first["field_id"]

    try:
        return FarmContext(**data)
    except Exception:                                     # noqa: BLE001
        return DEFAULT_FARM

# Weight of each parameter in the soil health index. Moisture and pH dominate
# because they are the most dependable outputs of this sensor class.
INDEX_WEIGHTS = {
    "ph": 0.25, "moisture_pct": 0.22, "ec_ds_m": 0.18,
    "nitrogen_mg_kg": 0.12, "phosphorus_mg_kg": 0.08,
    "potassium_mg_kg": 0.08, "temperature_c": 0.07,
}


def _band_score(field: str, value: Optional[float]) -> Optional[float]:
    band = interpret(field, value)
    return {"optimal": 100.0, "low": 55.0, "high": 55.0}.get(band)


def soil_index(reading: Dict[str, Any]) -> Dict[str, Any]:
    """A transparent 0-100 index: weighted band scores over the available fields."""
    total_weight = 0.0
    total = 0.0
    contributions = {}
    for field, weight in INDEX_WEIGHTS.items():
        score = _band_score(field, reading.get(field))
        if score is None:
            continue
        total += score * weight
        total_weight += weight
        contributions[field] = {"value": reading.get(field), "score": score, "weight": weight}
    if total_weight == 0:
        return {"available": False, "reason": "No usable sensor values."}
    index = round(total / total_weight)
    label = "Good" if index >= 75 else "Fair" if index >= 55 else "Needs attention"
    return {
        "available": True,
        "index": index,
        "label": label,
        "method": "Weighted score of each parameter's agronomic band; weights shown.",
        "contributions": contributions,
        "provenance": reading.get("provenance"),
        "simulated": reading.get("mode") == "simulation",
    }


@router.get("/farm")
async def farm(user: Optional[Dict[str, Any]] = Depends(current_user)) -> Dict[str, Any]:
    return {"farm": farm_for(user).model_dump(mode="json"),
            "note": ("The signed-in farmer's record. Anything not filled in on the "
                     "profile page falls back to a sensible default.")}


@router.get("/dashboard/overview")
async def overview(parcel_id: str = "",
                   user: Optional[Dict[str, Any]] = Depends(current_user)) -> Dict[str, Any]:
    settings = get_settings()
    farm = farm_for(user, parcel_id=parcel_id)
    reading, reading_meta = sensor_source.current(parcel_id)
    soil_block: Dict[str, Any] = {"available": False}
    parameters: List[Dict[str, Any]] = []
    validation: Dict[str, Any] = {}
    if reading:
        checked = validate(dict(reading))
        parameters = describe_reading(checked["reading"])
        validation = checked["validation"]
        soil_block = soil_index(checked["reading"].model_dump(mode="json"))

    weather_block = await weather_service.get_summary(farm.latitude,
                                                      farm.longitude, days=7)
    risks: List[str] = []
    if weather_block.get("available"):
        days = weather_block.get("forecast", [])[:5]
        rain = sum(float(d.get("rain_mm") or 0) for d in days)
        highs = [float(d["t_max"]) for d in days if d.get("t_max") is not None]
        lows = [float(d["t_min"]) for d in days if d.get("t_min") is not None]
        if rain >= 25:
            risks.append("heavy rain expected")
        if highs and max(highs) >= 38:
            risks.append("heat stress")
        if lows and min(lows) <= 4:
            risks.append("frost risk")
    weather_risk = "High" if len(risks) > 1 else "Medium" if risks else "Low"

    recommendations = []
    moisture = (reading or {}).get("moisture_pct")
    if moisture is not None and moisture < 20:
        recommendations.append({"icon": "water", "title": "Check soil moisture",
                                "detail": "Moisture reads %.0f%%; open the Soil Health page."
                                          % moisture, "page": "soil"})
    if risks:
        recommendations.append({"icon": "weather", "title": "Review the forecast",
                                "detail": ", ".join(risks).capitalize() + " in the next 5 days.",
                                "page": "weather"})
    recommendations.append({"icon": "leaf", "title": "Scout your crop",
                            "detail": "Walk a W pattern and check five spots for early "
                                      "pest or disease signs.", "page": "pests"})

    advisories = storage.recent_advisories(5)
    activity = [{
        "title": a.get("intent", "advisory").replace("_", " ").title(),
        "detail": (a.get("question") or "")[:80],
        "at": a.get("generated_at"),
    } for a in advisories]

    return {
        "farm": farm.model_dump(mode="json"),
        "soil": soil_block,
        "parameters": parameters,
        "validation": validation,
        "sensor_mode": (reading or {}).get("mode", "none"),
        "weather": weather_block,
        "weather_at": {"latitude": farm.latitude, "longitude": farm.longitude,
                       "field": farm.parcel_id, "village": farm.village},
        "weather_risk": {"level": weather_risk, "risks": risks},
        "crop": {
            "name": farm.crop,
            "field": farm.parcel_id,
            "season": farm.season,
            "condition": "Not assessed" if not reading else
                         ("Review" if soil_block.get("index", 100) < 55 else "Healthy"),
            "condition_basis": "Derived from the soil index; no crop imagery is analysed.",
        },
        # The farmer's own plots. This used to be three fixed rows that existed
        # nowhere else in the system.
        "fields": [
            {"id": p.get("field_id"), "name": p.get("name"), "crop": p.get("crop"),
             "area_acres": p.get("area_acres"),
             "selected": p.get("field_id") == (parcel_id or farm.parcel_id)}
            for p in profile_store.list_fields(
                profile_store._key(owned_phone(user, "") if user else ""))
        ],
        "selected_field": parcel_id or farm.parcel_id,
        "recommendations": recommendations,
        "activity": activity,
        "capabilities": settings.capability_report(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/weather")
async def weather(lat: Optional[float] = None, lon: Optional[float] = None,
                  days: int = 7, parcel_id: str = "",
                  user: Optional[Dict[str, Any]] = Depends(current_user)) -> Dict[str, Any]:
    """The forecast. Defaults to the farmer's own land, not a demo coordinate.

    It used to fall back to DEFAULT_FARM, which is in Agra. So "at my registered land"
    and "where I am now" returned the same wrong place for anyone whose farm is not
    in Agra, and the two options looked identical.
    """
    farm = farm_for(user, parcel_id=parcel_id)
    result = await weather_service.get_summary(lat if lat is not None else farm.latitude,
                                               lon if lon is not None else farm.longitude,
                                               days=days)
    result["at"] = {
        "latitude": lat if lat is not None else farm.latitude,
        "longitude": lon if lon is not None else farm.longitude,
        "source": "your location" if lat is not None else "your registered land",
        "field": None if lat is not None else farm.parcel_id,
        "village": None if lat is not None else farm.village,
    }
    return result


@router.get("/market")
async def market(commodity: str = "wheat", state: Optional[str] = None,
                 district: Optional[str] = None, parcel_id: str = "",
                 user: Optional[Dict[str, Any]] = Depends(current_user)) -> Dict[str, Any]:
    """Mandi prices for a crop, narrowed to the farmer's own district by default.

    A state median is a poor guide to what this farmer will be paid - they sell at a
    mandi they can reach, and the spread inside one state is routinely wider than the
    move everyone watches.
    """
    farm = farm_for(user, parcel_id=parcel_id)
    use_state = state if state is not None else (farm.state or "")

    # The farmer's own district is only a sensible default INSIDE their own
    # state. Carried into a state they deliberately chose, it is a filter for
    # somewhere that does not exist there - Punjab plus a district in
    # Maharashtra - which matched nothing and silently widened the search to the
    # whole country. The page then showed national prices under a Punjab label.
    own_state = (use_state or "").strip().lower() == (farm.state or "").strip().lower()
    if district is not None:
        use_district = district
    elif own_state:
        use_district = farm.district or ""
    else:
        use_district = ""
    summary = await market_service.get_summary(commodity, use_state or None,
                                               use_district or None)
    records = summary.get("records") or []
    summary["districts"] = market_service.districts_in(records)
    summary["nearest"] = market_service.nearest_markets(records, use_district)
    summary["asked_for"] = {"state": use_state, "district": use_district,
                            "field": farm.parcel_id}
    return summary


@router.get("/soil/background")
async def soil_background(lat: Optional[float] = None,
                          lon: Optional[float] = None) -> Dict[str, Any]:
    return await soil_service.get_summary(lat if lat is not None else DEFAULT_FARM.latitude,
                                          lon if lon is not None else DEFAULT_FARM.longitude)


@router.delete("/history")
async def clear_history(what: str = "advisories",
                        user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """Permanently delete stored records. There is no undo.

    Sensor readings are deliberately NOT included in "all": they are the measured
    record of this farm, and a farmer clearing a cluttered question log would not
    expect their soil history to go with it. They have to be asked for by name.
    """
    wanted = {w.strip() for w in str(what or "").split(",") if w.strip()}
    if "all" in wanted:
        wanted = {"advisories", "calls", "sessions"}

    allowed = {"advisories": "advisories", "calls": "calls",
               "sessions": "sessions", "readings": "readings"}
    removed: Dict[str, int] = {}
    with storage.cursor() as cur:
        for name in wanted:
            table = allowed.get(name)
            if not table:
                continue
            cur.execute("SELECT COUNT(*) FROM %s" % table)
            count = int(cur.fetchone()[0])
            cur.execute("DELETE FROM %s" % table)
            removed[name] = count
    if not removed:
        raise HTTPException(status_code=400,
                            detail="Nothing recognised to clear. Use advisories, calls, "
                                   "sessions, readings or all.")
    return {"deleted": removed,
            "note": "Deleted permanently. Sensor readings are only cleared when asked "
                    "for by name."}


# Which reports are about one plot of land, and which are about the whole farm.
# A call log has no field, and saying so is better than offering a filter that
# quietly changes nothing.
PER_FIELD = {"soil", "sensor-history"}


def _plots(user: Dict[str, Any]) -> List[Dict[str, Any]]:
    return profile_store.list_fields(owned_phone(user))


def _owned_parcel(plots: List[Dict[str, Any]], field: str) -> str:
    """The field a report was asked for, once it is known to be this farmer's.

    An unchecked id would be a way to read another farm's readings by guessing
    one, so an id that is not in this farmer's own list is refused rather than
    quietly treated as "whole farm" - which would answer with the wrong data and
    say nothing about it.
    """
    wanted = str(field or "").strip()
    if not wanted:
        return ""
    if any(str(plot.get("field_id")) == wanted for plot in plots):
        return wanted
    raise HTTPException(status_code=404, detail="That is not one of your fields.")


@router.get("/reports")
async def reports(field: str = "",
                  user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """What can be exported, and whether the data behind each report exists yet."""
    plots = _plots(user)
    parcel = _owned_parcel(plots, field)
    reading, _ = sensor_source.current(parcel)
    history = storage.recent_readings(parcel or None, limit=100)
    advisories = storage.recent_advisories(50)
    calls = storage.list_calls(50)
    nothing_here = ("No sensor reading stored for this field yet."
                    if parcel else "No sensor reading stored yet.")
    return {
        "reports": [
            {"id": "soil", "title": "Soil health report",
             "detail": "Latest validated sensor record with bands and provenance.",
             "ready": bool(reading), "per_field": True,
             "note": "" if reading else nothing_here},
            {"id": "advisory", "title": "Advisory history",
             "detail": "Every advisory with its agents, evidence and warnings.",
             "ready": bool(advisories), "per_field": False,
             "note": "" if advisories else "No advisory has been generated yet."},
            {"id": "calls", "title": "Call log",
             "detail": "Voice calls with transcripts and replies.",
             "ready": bool(calls), "per_field": False,
             "note": "" if calls else "No calls recorded yet."},
            {"id": "sensor-history", "title": "Sensor history",
             "detail": "Reading series with mode and timestamps.",
             "ready": bool(history), "per_field": True,
             "note": "" if history else nothing_here},
        ],
        "counts": storage.counts(),
        "field": parcel,
        "fields": [{"field_id": plot.get("field_id"),
                    "name": plot.get("name") or plot.get("field_id"),
                    "crop": plot.get("crop") or ""} for plot in plots],
    }


REPORT_TITLES = {
    "soil": "Soil health report",
    "advisory": "Advisory history",
    "calls": "Call log",
    "sensor-history": "Sensor history",
}


def _report_farm(user: Dict[str, Any], plots: List[Dict[str, Any]],
                 parcel: str) -> Dict[str, Any]:
    """Whose farm this report is about, for the cover and the About sheet.

    It used to read the profile stored under "dashboard-owner" - the shared
    placeholder identity - so every farmer's report came out headed with the
    demo farmer's name whoever had signed in and downloaded it. It now reads the
    account's own record, and falls back to the name the account was created
    with rather than to somebody else's.
    """
    farm: Dict[str, Any] = {}
    try:
        owner = profile_store.get_profile(owned_phone(user))
        farm = dict(owner.get("profile") or {})
    except Exception:                                             # noqa: BLE001
        farm = {}
    if not (farm.get("farmer_name") or farm.get("name")):
        farm["name"] = str(user.get("full_name") or "").strip()
    if parcel:
        plot = next((p for p in plots if str(p.get("field_id")) == parcel), None)
        if plot:
            farm["field_name"] = str(plot.get("name") or parcel)
            if plot.get("crop"):
                farm["field_crop"] = str(plot["crop"])
    return farm


@router.get("/reports/{report_id}")
async def report_detail(report_id: str, format: str = "json", field: str = "",
                        user: Dict[str, Any] = Depends(require_user)) -> Any:
    """One report, in the format asked for, for the whole farm or for one field.

    ``json`` keeps the original shape, so anything already reading this endpoint is
    unaffected. ``pdf`` and ``xlsx`` render the same data - the PDF as a document with
    charts, the workbook with typed columns a farmer can sort and filter.
    """
    wanted = (format or "json").lower()
    if wanted not in ("json", "pdf", "xlsx", "excel"):
        raise HTTPException(status_code=400,
                            detail="format must be json, pdf or xlsx")

    plots = _plots(user)
    parcel = _owned_parcel(plots, field)
    data = _report_payload(report_id, parcel)
    if wanted == "json":
        return data

    farm = _report_farm(user, plots, parcel)
    title = REPORT_TITLES.get(report_id, report_id)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")

    if wanted == "pdf":
        from ..reports import pdf as pdf_report
        body = pdf_report.build(report_id, data, farm=farm, title=title)
        media, suffix = "application/pdf", "pdf"
    else:
        from ..reports import excel as excel_report
        body = excel_report.build(report_id, data, farm=farm, title=title)
        media = ("application/vnd.openxmlformats-officedocument."
                 "spreadsheetml.sheet")
        suffix = "xlsx"

    return Response(
        content=body, media_type=media,
        headers={"Content-Disposition":
                 'attachment; filename="saarthi-%s-%s.%s"' % (report_id, stamp, suffix)})


def _report_payload(report_id: str, parcel: str = "") -> Dict[str, Any]:
    """The data behind one report, narrowed to one field when asked.

    Only the two reports that are ABOUT a plot of land take the filter. An
    advisory and a call belong to the farm, so narrowing them would mean
    pretending to a precision the records do not have.
    """
    if report_id == "soil":
        reading, _ = sensor_source.current(parcel)
        if not reading:
            raise HTTPException(status_code=404, detail="no sensor reading stored")
        checked = validate(dict(reading))
        return {"report": "soil", "generated_at": datetime.now(timezone.utc).isoformat(),
                "field": parcel,
                "reading": checked["reading"].model_dump(mode="json"),
                "validation": checked["validation"],
                "parameters": describe_reading(checked["reading"]),
                "index": soil_index(checked["reading"].model_dump(mode="json"))}
    if report_id == "advisory":
        return {"report": "advisory", "advisories": storage.recent_advisories(50)}
    if report_id == "calls":
        return {"report": "calls", "calls": storage.list_calls(50)}
    if report_id == "sensor-history":
        return {"report": "sensor-history", "field": parcel,
                "readings": storage.recent_readings(parcel or None, limit=100)}
    raise HTTPException(status_code=404, detail="unknown report")
