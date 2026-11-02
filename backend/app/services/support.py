"""Government schemes, farm finance and farmer support.

Three things a farmer asks about that are not agronomy, and that the fifteen agents
deliberately do not cover: which scheme pays for what, what a season actually costs
and whether it pays, and who to ring when something is wrong.

What is in here is REFERENCE DATA, not a prediction and not a live feed:

  * the schemes are real central schemes, each with its own official portal. Amounts
    and percentages are what the scheme documents state; they change, and the state
    top-ups vary, so every entry carries the portal to check it on.
  * the helplines are the published national numbers.
  * the finance side computes nothing it was not given. Cost of cultivation is the
    farmer's own figures; the price comes from a named mandi record or from them.
    There is no "typical cost per acre" table, because that number differs by district
    and would be quoted back as if it were theirs.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List, Optional

_DATA = pathlib.Path(__file__).resolve().parent.parent / "data"

# Reviewed 2026-10: the published national numbers. A farmer acts on these, so each
# one says what it is for rather than sitting in an undifferentiated list.
HELPLINES: List[Dict[str, str]] = [
    {"name": "Kisan Call Centre", "number": "1800-180-1551",
     "hours": "6 am to 10 pm, all days",
     "about": "Free agricultural advice in your own language, from agriculture graduates."},
    {"name": "PM-KISAN helpdesk", "number": "155261",
     "hours": "Working hours",
     "about": "Instalments not received, registration and e-KYC problems."},
    {"name": "Crop insurance (PMFBY)", "number": "14447",
     "hours": "Working hours",
     "about": "Enrolment, premium and claim questions for Fasal Bima."},
    {"name": "Soil Health Card", "number": "1800-180-1551",
     "hours": "6 am to 10 pm",
     "about": "Where to get your soil tested and how to read the card."},
    {"name": "Agri-market (eNAM)", "number": "1800-270-0224",
     "hours": "Working hours",
     "about": "Selling through the national market platform."},
]

PORTALS: List[Dict[str, str]] = [
    {"name": "Kisan Suvidha", "url": "https://kisansuvidha.gov.in",
     "about": "Weather, prices, inputs and advisories in one government app."},
    {"name": "eNAM", "url": "https://enam.gov.in",
     "about": "Sell produce to buyers outside your own mandi."},
    {"name": "Agmarknet", "url": "https://agmarknet.gov.in",
     "about": "The daily mandi prices this dashboard reads for Agent 12."},
    {"name": "mKisan", "url": "https://mkisan.gov.in",
     "about": "SMS advisories from your own block and district officers."},
    {"name": "Find your KVK", "url": "https://kvk.icar.gov.in",
     "about": "The nearest Krishi Vigyan Kendra: free soil testing, trials and training."},
]

# What a Kisan Credit Card actually costs, as the scheme states it. Deliberately not
# turned into a monthly instalment: that depends on the bank, the limit and the tenure.
KCC_TERMS: Dict[str, Any] = {
    "base_interest_percent": 7.0,
    "prompt_repayment_rebate_percent": 3.0,
    "effective_if_repaid_on_time_percent": 4.0,
    "collateral_free_limit_inr": 160000,
    "note": ("The 3% rebate applies only if the loan is repaid on time. Limits above "
             "the collateral-free threshold need security. The bank sets the final "
             "limit from your land and crop."),
}


def _load_schemes() -> List[Dict[str, Any]]:
    path = _DATA / "schemes.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


SCHEMES: List[Dict[str, Any]] = _load_schemes()


def catalogue_version() -> str:
    """A digest of which schemes exist, not of their contents.

    Ids and titles only: correcting a subsidy percentage should not look to the
    farmer like a new scheme appeared, but adding or renaming one should.
    """
    import hashlib

    spine = "|".join(sorted("%s:%s" % (s.get("id", ""), s.get("title", ""))
                            for s in SCHEMES))
    return hashlib.sha256(spine.encode("utf-8")).hexdigest()[:16]


def list_schemes(category: str = "", query: str = "") -> List[Dict[str, Any]]:
    """Schemes, optionally narrowed. Matching is plain substring, on purpose."""
    items = SCHEMES
    if category:
        want = category.strip().lower()
        items = [s for s in items
                 if want in str(s.get("category", "")).lower()
                 or want in str(s.get("category_tag", "")).lower()]
    if query:
        needle = query.strip().lower()
        items = [s for s in items if needle in json.dumps(s, ensure_ascii=False).lower()]
    return items


def categories() -> List[str]:
    seen: List[str] = []
    for s in SCHEMES:
        tag = str(s.get("category_tag") or s.get("category") or "").strip()
        if tag and tag not in seen:
            seen.append(tag)
    return seen


def relevant_to(farm: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The schemes worth reading first for this farm.

    A weak ranking on purpose: every farmer is eligible for most central schemes, and
    pretending otherwise would hide one they could have claimed. This only lifts the
    obviously relevant ones to the top.
    """
    acres = float(farm.get("area_acres") or 0)
    irrigation = str(farm.get("irrigation_method") or "").lower()
    ranked: List[Dict[str, Any]] = []
    for scheme in SCHEMES:
        score = 0
        sid = scheme.get("id", "")
        if sid == "sub_pmkisan":
            score += 3                       # income support, open to almost everyone
        if sid == "sub_kcc":
            score += 3                       # credit, always relevant
        if sid == "sub_soil":
            score += 2
        if sid == "sub_pmksy" and irrigation in ("flood", "furrow", ""):
            score += 2                       # most to gain from micro-irrigation
        if sid == "sub_smam" and acres >= 2:
            score += 1
        if sid == "sub_pmfby":
            score += 2
        ranked.append({**scheme, "_score": score})
    ranked.sort(key=lambda s: -s["_score"])
    for item in ranked:
        item.pop("_score", None)
    return ranked


def season_economics(cost: Optional[float], yield_quintal: Optional[float],
                     price_per_quintal: Optional[float],
                     area_acres: Optional[float] = None) -> Dict[str, Any]:
    """Break-even and margin from numbers the farmer supplied.

    Returns what is missing rather than filling it in. A cost of cultivation invented
    from a national average, quoted back at a farmer as their own, is the exact thing
    this project does not do.
    """
    missing = [name for name, value in (("cost", cost), ("yield", yield_quintal),
                                        ("price", price_per_quintal)) if not value]
    out: Dict[str, Any] = {"missing": missing, "inputs": {
        "cost_inr": cost, "yield_quintal": yield_quintal,
        "price_per_quintal_inr": price_per_quintal, "area_acres": area_acres}}

    if cost and price_per_quintal:
        out["break_even_quintal"] = round(cost / price_per_quintal, 2)
    if cost and yield_quintal:
        out["break_even_price_per_quintal"] = round(cost / yield_quintal, 2)
    if yield_quintal and price_per_quintal:
        revenue = round(yield_quintal * price_per_quintal, 2)
        out["gross_revenue_inr"] = revenue
        if cost:
            out["gross_margin_inr"] = round(revenue - cost, 2)
            out["margin_percent"] = round((revenue - cost) / revenue * 100, 1) if revenue else None
            if area_acres:
                out["margin_per_acre_inr"] = round((revenue - cost) / area_acres, 2)
    out["caveat"] = ("Every figure here comes from the numbers you gave, plus a mandi "
                     "price from a named record. By-product value such as straw is not "
                     "included, and family labour is not costed.")
    return out


def summary_for_agents(farm: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A compact block the conversational and voice agents can read.

    Short on purpose: it goes into a prompt, and a phone reply has seconds to work
    with. The portal and the number are the parts a farmer can act on.
    """
    top = relevant_to(farm or {})[:4]
    return {
        "schemes": [{
            "title": s["title"],
            "benefit": s.get("financial_benefit") or s.get("subsidy_percent"),
            "apply_at": s.get("portal_url"),
            "eligibility": s.get("eligibility"),
        } for s in top],
        "helplines": [{"name": h["name"], "number": h["number"]} for h in HELPLINES[:3]],
        "credit": {
            "kisan_credit_card_interest_percent": KCC_TERMS["effective_if_repaid_on_time_percent"],
            "collateral_free_limit_inr": KCC_TERMS["collateral_free_limit_inr"],
        },
    }
