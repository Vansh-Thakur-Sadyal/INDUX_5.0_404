"""What is on a page, in words, read from the same data the page draws itself from.

Asked "what does the weather page say", Saarthi used to answer that it could not
see the screen. That is true and useless: it cannot see the screen, but it can
read the data the screen was built from, which is better - the screen is only a
rendering of it.

The rule that makes this worth having is that it reads the SAME source. An
assistant that answers from somewhere else produces a second set of numbers, and
a farmer looking at two different temperatures on one screen has learnt not to
trust either. So every reader here calls the service the page's own endpoint
calls, with the same farm, and reports exactly what comes back - including when
it comes back empty.

What a reader must never do is fill a gap. "No sensor reading yet" is the right
answer to a question about the soil page when there is no reading; inventing a
moisture figure to seem helpful is the one failure this project does not
tolerate.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from ..core import ledger as ledger_store
from ..core.schemas import FarmContext
from ..services import market as market_service
from ..services import soilgrids as soil_service
from ..services import weather as weather_service

log = logging.getLogger("saarthi.screens")

# Each page sits behind one or two service calls, so this is generous without
# being long enough to be heard as a pause.
BUDGET_SECONDS = 7.0


def _line(*parts: Any) -> str:
    return " ".join(str(p) for p in parts if p not in (None, ""))


# --------------------------------------------------------------------- weather
async def weather(farm: FarmContext) -> List[str]:
    summary = await weather_service.get_summary(farm.latitude, farm.longitude, days=7)
    if not summary.get("available"):
        return ["WEATHER PAGE: no forecast available right now (%s)."
                % (summary.get("reason") or "the service did not answer")]

    now = summary.get("current") or {}
    out = ["WEATHER PAGE is showing %s, %s district." % (farm.village, farm.district)]
    if now.get("temperature_c") is not None:
        out.append(_line("Right now:", now.get("condition"), "-",
                         now.get("temperature_c"), "degrees C, humidity",
                         now.get("humidity_pct"), "percent, wind",
                         now.get("wind_kmh"), "kmph."))
    # Named by weekday, the same way the live lookup names them, because the
    # two reach the same model in the same turn and a farmer asking about
    # Thursday must not be answered off two differently-labelled lists.
    from .lookups import day_label

    for day in (summary.get("forecast") or [])[:7]:
        out.append(_line(day_label(day.get("date")) + ":", "chance of rain",
                         day.get("rain_probability"), "percent, rainfall",
                         day.get("rain_mm"), "mm,", day.get("condition"), "-",
                         day.get("t_min"), "to", day.get("t_max"), "degrees C."))
    return out


# ------------------------------------------------------------------ soil health
async def soil(farm: FarmContext, parcel_id: str = "") -> List[str]:
    from ..sensors import source as sensor_source
    from ..sensors.validation import describe_reading, validate

    reading, meta = sensor_source.current(parcel_id or str(farm.parcel_id or ""))
    out: List[str] = []
    if not reading:
        out.append("SOIL HEALTH PAGE: no sensor reading has been taken for this "
                   "field yet, so the page shows background data only. There is no "
                   "measured moisture, pH or NPK to report.")
    else:
        checked = validate(dict(reading))
        out.append("SOIL HEALTH PAGE, from the latest reading (%s):"
                   % (meta.get("source") or "sensor"))
        for item in describe_reading(checked["reading"]):
            out.append(_line("-", item.get("label"), ":", item.get("value"),
                             item.get("unit"), "-", item.get("verdict")))

    background = await soil_service.get_summary(farm.latitude, farm.longitude)
    if background.get("available"):
        # Labelled as estimated, because that is what it is: a global model at
        # coarse resolution, not a reading off this field.
        bits = ["%s %s %s" % (v.get("label"), v.get("value"), v.get("unit"))
                for v in (background.get("values") or {}).values()]
        if bits:
            out.append("Background soil for this location, ESTIMATED by %s (not "
                       "measured on the field): %s."
                       % (background.get("source") or "SoilGrids", ", ".join(bits)))
    return out


# ----------------------------------------------------------------- mandi prices
async def market(farm: FarmContext, commodity: str = "") -> List[str]:
    crop = commodity or str(farm.crop or "wheat")
    summary = await market_service.get_summary(crop, farm.state or None,
                                               farm.district or None)
    if not summary.get("available"):
        return ["MARKET PRICES PAGE: no mandi records for %s right now (%s)."
                % (crop, summary.get("reason") or "nothing was returned")]

    out = ["MARKET PRICES PAGE is showing %s %s: typical (median modal) %s %s, from "
           "%d market records."
           % (summary.get("commodity"), market_service.scope_phrase(summary),
              summary.get("median_modal"),
              (summary.get("highest") or {}).get("unit") or "INR per quintal",
              int(summary.get("record_count") or 0))]
    if summary.get("note"):
        out.append("Scope note: " + str(summary["note"]))
    for row in (summary.get("records") or [])[:3]:
        out.append(_line("-", row.get("market"), "in", row.get("district"),
                         "district,", row.get("state") + ":",
                         row.get("modal_price"), row.get("unit")))
    return out


# ------------------------------------------------------------------- farm money
def finance(phone: str) -> List[str]:
    try:
        summary = ledger_store.spoken_summary(phone)
    except Exception:                                   # pragma: no cover
        return ["FARM FINANCE PAGE could not be read right now."]
    return ["FARM FINANCE PAGE: " + (summary or "nothing has been recorded yet.")]


# --------------------------------------------------------------------- schemes
def schemes(farm: FarmContext) -> List[str]:
    from ..services import support as support_service

    try:
        found = support_service.relevant_to(farm.model_dump(mode="json"))[:4]
    except Exception:                                   # pragma: no cover
        return ["GOVT SCHEMES PAGE could not be read right now."]
    if not found:
        return ["GOVT SCHEMES PAGE: no schemes are loaded."]
    # The field names are the catalogue's own. They were guessed at before -
    # "name" and "benefit", which no record has - so every scheme came out as a
    # title followed by a colon and nothing, and the assistant reading that
    # concluded it had no scheme data and said so.
    out = ["GOVT SCHEMES PAGE is listing, most relevant to this farm first:"]
    for item in found:
        out.append(_line("-", item.get("title"), "(" + str(item.get("category") or
                                                           "scheme") + "):",
                         str(item.get("financial_benefit")
                             or item.get("objective") or "")[:200]))
        if item.get("eligibility"):
            out.append(_line("  who is eligible:",
                             str(item["eligibility"])[:160]))
        if item.get("portal_name"):
            out.append(_line("  apply at:", item.get("portal_name"),
                             item.get("portal_url")))
    return out


# ---------------------------------------------------------------- the overview
async def overview(farm: FarmContext, phone: str, parcel_id: str = "") -> List[str]:
    """The home page, which is a summary of the others."""
    parts = await asyncio.gather(weather(farm), soil(farm, parcel_id),
                                 return_exceptions=True)
    out = ["OVERVIEW PAGE for %s, %s - %s acres, growing %s."
           % (farm.village, farm.district, farm.area_acres, farm.crop or "nothing recorded")]
    for part in parts:
        if isinstance(part, BaseException):
            continue
        out.extend(part[:3])
    return out


# ------------------------------------------------------------- what each is for
# Pages with no live data behind them still deserve a real answer: what the page
# is for, so "what is on the reports page" is answered rather than refused.
DESCRIBED = {
    "pipeline": "PIPELINE RESULTS PAGE shows the last run of the fifteen agents - "
                "each agent's finding, how confident it was, and what it still needs.",
    "reports": "REPORTS PAGE holds the saved advisories, downloadable as PDF or Excel.",
    "fields": "MY FIELDS PAGE is the map of the plots this farmer has drawn, each with "
              "its own crop and area.",
    "devices": "SENSOR PAGE is where a reading is fetched from the soil probe, or "
               "typed in by hand.",
    "calls": "VOICE CALLS PAGE lists the calls with Saarthi and lets one be booked.",
    "help": "FARM SUPPORT PAGE has the helpline numbers and the support desk.",
    "pests": "PEST AND DISEASE PAGE identifies a problem from a photo of the leaf.",
    "profile": "PROFILE PAGE holds the farmer's own details, their land and their "
               "language.",
}


async def read(page: str, *, farm: FarmContext, phone: str = "",
               parcel_id: str = "", budget: float = BUDGET_SECONDS) -> List[str]:
    """Everything worth saying about one page, or an empty list.

    A reader that overruns is dropped rather than waited for: the farmer asked a
    question, and an answer that arrives after they have given up is not one.
    """
    page = (page or "").strip().lower()

    async def run() -> List[str]:
        if page == "weather":
            return await weather(farm)
        if page == "soil":
            return await soil(farm, parcel_id)
        if page in ("market", "economics"):
            return await market(farm)
        if page == "overview":
            return await overview(farm, phone, parcel_id)
        if page == "finance":
            return finance(phone)
        if page == "schemes":
            return schemes(farm)
        described = DESCRIBED.get(page)
        return [described] if described else []

    try:
        return await asyncio.wait_for(run(), timeout=budget)
    except asyncio.TimeoutError:
        log.warning("reading the %s page did not finish inside %.1fs", page, budget)
        return []
    except Exception as exc:                            # noqa: BLE001 - never fatal
        log.warning("reading the %s page failed: %s", page, exc)
        return []


# ------------------------------------------- which page a question is asking about
# Reuses the voice parser's own page vocabulary, so a page that can be OPENED by
# voice can also be READ by voice. Keeping a second list here would drift.
def page_in(said: str, normalised: str = "") -> Optional[str]:
    from . import actions as action_registry

    text = ((said or "") + " " + (normalised or "")).lower()
    best: Optional[str] = None
    best_len = 0
    for page, spec in action_registry.PAGES.items():
        for word in spec["words"]:
            if word in text and len(word) > best_len:
                best, best_len = page, len(word)
    return best


# Asking what is on screen, rather than asking to be taken there.
_READING = (
    "what does", "what is on", "what's on", "read", "tell me what", "show me what",
    "kya likha", "kya dikh", "kya bata", "batao kya", "screen par", "page par",
    "is page", "this page", "is screen", "this screen", "yahan kya",
    "क्या लिखा", "क्या दिख", "इस पेज", "इस स्क्रीन", "यहाँ क्या", "पढ़",
)


def asks_to_read(said: str) -> bool:
    text = (said or "").lower()
    return any(cue in text for cue in _READING)
