"""Mandi price data from data.gov.in (Agmarknet daily prices resource).

If the key is missing or the API fails, the result says so. No price is ever
generated, estimated or carried over silently from an older date - Agent 12
depends on that guarantee.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..core.config import get_settings
from .http_cache import ServiceResult, get_json

BASE_URL = "https://api.data.gov.in/resource/"

COMMODITY_ALIASES = {
    "paddy": "Paddy(Dhan)(Common)",
    "rice": "Rice",
    "wheat": "Wheat",
    "mustard": "Mustard",
    "chickpea": "Bengal Gram(Gram)(Whole)",
    "gram": "Bengal Gram(Gram)(Whole)",
    "maize": "Maize",
    "cotton": "Cotton",
    "potato": "Potato",
    "onion": "Onion",
    "tomato": "Tomato",
    "soybean": "Soyabean",
    "groundnut": "Groundnut",
    "sugarcane": "Sugarcane",
    "barley": "Barley (Jau)",
    "lentil": "Lentil (Masur)(Whole)",
}


def canonical_commodity(name: str) -> str:
    key = (name or "").strip().lower()
    return COMMODITY_ALIASES.get(key, (name or "").strip().title())


async def fetch_prices(commodity: str, state: Optional[str] = None,
                       district: Optional[str] = None, limit: int = 30) -> ServiceResult:
    settings = get_settings()
    if not settings.has("DATA_GOV_API_KEY"):
        return ServiceResult(
            ok=False, source="data.gov.in Agmarknet",
            error=("DATA_GOV_API_KEY is not configured, so live mandi prices are "
                   "unavailable. Add it to config/api_keys.env."),
        )
    # ``.keyword`` on every one of these, and it is not a detail.
    #
    # The plain field names are ANALYSED text, so the API matches on tokens and
    # returns anything sharing one. Measured against the live resource:
    #
    #   filters[state]=Uttar Pradesh            89 records, Madhya Pradesh among
    #                                           them - they share "Pradesh"
    #   filters[state.keyword]=Uttar Pradesh     1 record, Uttar Pradesh
    #   filters[commodity]=Bengal Gram(Gram)(Whole)
    #                                           41 records across SIX commodities:
    #                                           black gram, moong, lentil, horse
    #                                           gram - everything sharing "Gram"
    #   filters[commodity.keyword]=...          14 records, chickpea only
    #
    # So a chickpea price was a median over unrelated pulses, and a state price
    # was a median over several states. Both were quoted to farmers as fact.
    params: Dict[str, Any] = {
        "api-key": settings.data_gov_api_key,
        "format": "json",
        "limit": max(1, min(int(limit), 100)),
        "filters[commodity.keyword]": canonical_commodity(commodity),
    }
    if state:
        params["filters[state.keyword]"] = state
    if district:
        params["filters[district.keyword]"] = district
    url = BASE_URL + settings.data_gov_resource_id
    # Short on purpose. get_summary may call this twice - once for the state, once for
    # all India - and both sit inside the chain's gathering phase. A mandi price is
    # worth waiting seconds for, not most of a minute.
    return await get_json(url, params, source="data.gov.in Agmarknet", ttl=1800,
                          timeout=8.0)


def _number(value: Any) -> Optional[float]:
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def summarise(result: ServiceResult, commodity: str) -> Dict[str, Any]:
    if not result.ok:
        return {
            "available": False,
            "commodity": canonical_commodity(commodity),
            "reason": result.error or "Market service unavailable",
            "source": "data.gov.in Agmarknet",
        }
    records: List[Dict[str, Any]] = []
    payload = result.data if isinstance(result.data, dict) else {}
    for row in payload.get("records", []) or []:
        modal = _number(row.get("modal_price"))
        if modal is None:
            continue
        records.append({
            "market": row.get("market") or row.get("market_name") or "",
            "district": row.get("district", ""),
            "state": row.get("state", ""),
            "variety": row.get("variety", ""),
            "grade": row.get("grade", ""),
            "arrival_date": row.get("arrival_date", ""),
            "min_price": _number(row.get("min_price")),
            "max_price": _number(row.get("max_price")),
            "modal_price": modal,
            "unit": "INR per quintal",
        })
    if not records:
        return {
            "available": False,
            "commodity": canonical_commodity(commodity),
            "reason": "No price records returned for this commodity and filter.",
            "source": "data.gov.in Agmarknet",
        }
    records.sort(key=lambda r: r["modal_price"], reverse=True)
    prices = [r["modal_price"] for r in records]
    states = sorted({r["state"] for r in records if r["state"]})
    districts = sorted({r["district"] for r in records if r["district"]})
    return {
        "available": True,
        "commodity": canonical_commodity(commodity),
        "source": "data.gov.in Agmarknet",
        # Where these records are actually FROM, read off the records.
        #
        # The scope used to be assumed from what was asked for, which is how
        # "today's tomato price across Uttar Pradesh" came to be said over
        # records from Andhra Pradesh and Madhya Pradesh. A label about data must
        # come from the data: if the filter is ignored, widened, or changes
        # behaviour at the far end, this still describes what is really there.
        "states": states,
        "districts": districts,
        "retrieved_at": datetime.fromtimestamp(result.fetched_at, tz=timezone.utc).isoformat(),
        "record_count": len(records),
        "highest": records[0],
        "lowest": records[-1],
        "median_modal": sorted(prices)[len(prices) // 2],
        "records": records[:20],
        "caveat": ("Prices are as reported by the listed markets on the stated arrival "
                   "date, in INR per quintal. Past prices do not predict future prices."),
    }


async def get_summary(commodity: str, state: Optional[str] = None,
                      district: Optional[str] = None) -> Dict[str, Any]:
    """Prices, narrowed as far as the records allow.

    Widens rather than fails: district, then state, then national. Each step says so,
    because "the price in your district" and "the price somewhere in India" are very
    different numbers to make a selling decision on.
    """
    result = await fetch_prices(commodity, state, district)
    summary = summarise(result, commodity)
    summary["scope"] = _scope_of(summary, state, district)

    if not summary.get("available") and district:
        summary = summarise(await fetch_prices(commodity, state), commodity)
        if summary.get("available"):
            summary["scope"] = _scope_of(summary, state, None)
            summary["note"] = ("No records for %s district today; showing the whole "
                               "state." % district)
    if not summary.get("available") and state:
        summary = summarise(await fetch_prices(commodity), commodity)
        if summary.get("available"):
            summary["scope"] = _scope_of(summary, None, None)
            summary["note"] = ("No records for the requested state; showing national "
                               "records.")
    return summary


def _scope_of(summary: Dict[str, Any], state: Optional[str],
              district: Optional[str]) -> str:
    """How wide the records in ``summary`` really are.

    Asked-for and got are different things, and only the second one may be said
    out loud. One district means one district; one state means that state; more
    than one of either is "national" however narrow the request was.
    """
    if not summary.get("available"):
        return "district" if district else ("state" if state else "national")
    states = summary.get("states") or []
    districts = summary.get("districts") or []
    if len(states) > 1:
        return "national"
    if district and len(districts) == 1 and districts[0].lower() == district.lower():
        return "district"
    if len(districts) == 1:
        return "district"
    return "state" if len(states) == 1 else "national"


def scope_phrase(summary: Dict[str, Any]) -> str:
    """Where these prices are from, in words, for a reply that will be spoken."""
    states = summary.get("states") or []
    districts = summary.get("districts") or []
    scope = str(summary.get("scope") or "")
    if scope == "district" and districts:
        return "in %s district%s" % (districts[0],
                                     (", " + states[0]) if states else "")
    if scope == "state" and states:
        return "across %s" % states[0]
    if len(states) > 1:
        return "across %d states of India" % len(states)
    return "across India"


def districts_in(result_records) -> list:
    """Every district present in a set of records, with how many markets reported."""
    counts: Dict[str, int] = {}
    for row in result_records or []:
        name = str(row.get("district") or "").strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return [{"district": k, "markets": v}
            for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]


def nearest_markets(records, district: str, limit: int = 5) -> list:
    """Markets in the farmer's own district first, then the rest.

    Ordered by district match and then by price, NOT by distance: these records carry
    a district and a market name and no coordinates at all, so any distance here
    would be invented.
    """
    want = (district or "").strip().lower()
    scored = []
    for row in records or []:
        same = str(row.get("district") or "").strip().lower() == want
        scored.append((0 if same else 1, -(row.get("modal_price") or 0), row))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [row for _, _, row in scored[:limit]]
