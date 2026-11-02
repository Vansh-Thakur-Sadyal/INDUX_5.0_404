"""Agent 12 - Agricultural Market Intelligence.
Agent 13 - Farm Economics and Profitability.
Agent 14 - Sustainable Farming and Crop Residue.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..core.schemas import AgentResult, Provenance, Severity, Status
from .base import AgentContext, BaseAgent

RESIDUE_OPTIONS = [
    {
        "option": "In-situ incorporation",
        "how": "Chop the residue and mix it into the soil with enough moisture, adding a "
               "small nitrogen dose to support decomposition.",
        "suits": "Fields with two to three weeks before the next sowing.",
        "watch": "High carbon residue temporarily locks up soil nitrogen.",
    },
    {
        "option": "Surface mulching with direct sowing",
        "how": "Leave the residue on the surface and sow the next crop through it with a "
               "suitable machine.",
        "suits": "Short windows between harvest and sowing, and water-short fields.",
        "watch": "Needs access to the right machine; hire availability decides this.",
    },
    {
        "option": "Baling and removal",
        "how": "Bale the straw for fodder, bedding, mushroom cultivation, board or biomass "
               "industry.",
        "suits": "Places with a local buyer or livestock demand.",
        "watch": "Removes nutrients from the field; they have to be replaced.",
    },
    {
        "option": "Composting or vermicomposting",
        "how": "Stack with moisture and a nitrogen source, turn periodically.",
        "suits": "Farms that can spare space and six to ten weeks.",
        "watch": "Slower, and needs labour for turning.",
    },
]


class MarketAgent(BaseAgent):
    agent_id = 12
    name = "Agricultural Market Intelligence"
    purpose = "Provide traceable mandi price information with its date and source."
    inputs = ["crop", "market location", "Agmarknet records"]
    outputs = ["market summary with source, date, units and caveats"]

    async def analyse(self, ctx: AgentContext) -> AgentResult:
        market = ctx.market or {}
        crop = ctx.entities.get("crop") or ctx.farm.crop or ""
        if not market:
            return self.unavailable(
                "No market lookup was performed for this question.",
                missing=["commodity"],
                questions=["Which crop's price would you like, and which market?"],
            )
        if not market.get("available"):
            return self.unavailable(
                "Live mandi prices are unavailable: %s" % market.get("reason", "unknown reason"),
                missing=["live market data"],
            )

        highest = market.get("highest", {})
        lowest = market.get("lowest", {})
        records: List[Dict[str, Any]] = market.get("records", [])
        findings = [
            "%s prices from %d market records (source: %s)."
            % (market.get("commodity"), market.get("record_count", 0), market.get("source")),
            "Highest modal price: %s per quintal at %s, %s on %s."
            % (highest.get("modal_price"), highest.get("market"), highest.get("state"),
               highest.get("arrival_date")),
            "Lowest modal price: %s per quintal at %s, %s on %s."
            % (lowest.get("modal_price"), lowest.get("market"), lowest.get("state"),
               lowest.get("arrival_date")),
            "Median modal price across the returned records: %s per quintal."
            % market.get("median_modal"),
        ]
        nearby = [r for r in records
                  if ctx.farm.state and r.get("state", "").lower() == ctx.farm.state.lower()]
        if nearby:
            findings.append("In %s: " % ctx.farm.state + "; ".join(
                "%s %s on %s" % (r["market"], r["modal_price"], r["arrival_date"])
                for r in nearby[:4]) + ".")

        spread = None
        if highest.get("modal_price") and lowest.get("modal_price"):
            spread = round(float(highest["modal_price"]) - float(lowest["modal_price"]), 2)
            findings.append("Spread between the highest and lowest market is %s per "
                            "quintal, before transport and market charges." % spread)

        return self.blank(
            headline="%s: median modal price %s per quintal in the latest records."
                     % (market.get("commodity"), market.get("median_modal")),
            findings=findings,
            actions=["Compare the price net of transport, labour, market fee and commission "
                     "before deciding where to sell."],
            data={"commodity": market.get("commodity"), "records": records[:10],
                  "median_modal": market.get("median_modal"), "spread": spread,
                  "retrieved_at": market.get("retrieved_at"), "unit": "INR per quintal"},
            assumptions=["Records are as published by the markets for the stated arrival "
                         "dates; not every market reports every day."],
            warnings=["Past and present prices do not predict future prices. No forecast is "
                      "offered here."],
            evidence=ctx.evidence_from("market", "prices"),
            provenance=[Provenance.LIVE_API],
            confidence=0.8,
        )


class EconomicsAgent(BaseAgent):
    agent_id = 13
    name = "Farm Economics and Profitability"
    purpose = "Compare costs and returns transparently, using the farmer's own numbers."
    inputs = ["input costs", "market price", "yield estimate", "area"]
    outputs = ["cost and revenue comparison with break-even figures"]
    depends_on = [12]

    COST_CATEGORIES = ["seed", "fertiliser and manure", "crop protection", "irrigation",
                       "machinery and tillage", "labour", "harvest and threshing",
                       "transport and market charges", "land rent if leased"]

    def _numbers(self, text: str) -> Dict[str, float]:
        found: Dict[str, float] = {}
        cost = re.search(r"(?:cost|kharcha|lagat|spent|invest\w*)\D{0,12}(\d{3,7})",
                         text or "", re.I)
        if cost:
            found["cost"] = float(cost.group(1))
        yld = re.search(r"(\d{1,3}(?:\.\d+)?)\s*(?:quintal|qtl|ton|tonne)", text or "", re.I)
        if yld:
            found["yield_quintal"] = float(yld.group(1))
        price = re.search(r"(?:price|rate|bhav|per quintal)\D{0,12}(\d{3,6})", text or "", re.I)
        if price:
            found["price"] = float(price.group(1))
        return found

    async def analyse(self, ctx: AgentContext) -> AgentResult:
        # Typed into the boxes on the Pipeline page, or pulled out of the sentence the
        # farmer spoke. Reading only the sentence meant there was no way to answer the
        # question this agent asks, so it asked it again on every single run.
        given = self._numbers(ctx.question)
        for key in ("cost", "yield_quintal", "price"):
            value = ctx.entities.get(key)
            if value in (None, ""):
                continue
            try:
                given[key] = float(value)
            except (TypeError, ValueError):
                continue
        market_result = ctx.result(12)
        market_price: Optional[float] = None
        price_source = ""
        if market_result and market_result.data.get("median_modal"):
            market_price = float(market_result.data["median_modal"])
            price_source = "median modal price from %s records" % market_result.data.get("commodity", "")
        if given.get("price"):
            market_price = given["price"]
            price_source = "price you provided"

        findings: List[str] = []
        actions: List[str] = []
        warnings: List[str] = []
        data: Dict[str, Any] = {"area_acres": ctx.farm.area_acres}
        questions: List[str] = []

        findings.append("Cost categories to total for this crop: " +
                        ", ".join(self.COST_CATEGORIES) + ".")

        cost = given.get("cost")
        yield_q = given.get("yield_quintal")

        if market_price:
            findings.append("Price used: %s per quintal (%s)." % (market_price, price_source))
        else:
            questions.append("What price per quintal are you expecting, or shall I look up "
                             "the mandi price for your crop?")

        if cost and market_price:
            break_even_yield = round(cost / market_price, 2)
            findings.append("At a total cost of %s and a price of %s per quintal, you need "
                            "%s quintals just to break even."
                            % (cost, market_price, break_even_yield))
            data["break_even_yield_quintal"] = break_even_yield
        if cost and yield_q:
            break_even_price = round(cost / yield_q, 2)
            findings.append("At a total cost of %s and a yield of %s quintals, the break-even "
                            "price is %s per quintal." % (cost, yield_q, break_even_price))
            data["break_even_price"] = break_even_price
        if yield_q and market_price:
            revenue = round(yield_q * market_price, 2)
            findings.append("Gross revenue at %s quintals and %s per quintal is %s, before "
                            "by-product value such as straw." % (yield_q, market_price, revenue))
            data["gross_revenue"] = revenue
            if cost:
                margin = round(revenue - cost, 2)
                findings.append("Gross margin against the %s cost you gave is %s." % (cost, margin))
                data["gross_margin"] = margin

        if not cost:
            questions.append("What did this crop cost you in total, or what do you spend on "
                             "seed, fertiliser, irrigation and labour?")
        if not yield_q:
            questions.append("What yield do you expect, in quintals from this plot?")

        warnings.append("I do not hold input prices for your area, so I will not invent a "
                        "cost of cultivation. Every figure above uses numbers you gave me or "
                        "a price from a named market record.")
        actions.append("Write down your actual spend by category this season; next year the "
                       "comparison becomes exact instead of indicative.")

        return self.blank(
            status=Status.OK if (cost or yield_q or market_price) else Status.NEEDS_INPUT,
            headline="Cost and return comparison on your own numbers.",
            findings=findings,
            actions=actions,
            data=data,
            assumptions=["All figures are per the plot area on file (%.2f acres) unless you "
                         "said otherwise." % ctx.farm.area_acres,
                         "By-product value such as straw or fodder is not included."],
            warnings=warnings,
            questions=questions[:3],
            evidence=ctx.evidence_from("economics", "cost", "profit"),
            provenance=[Provenance.FARMER_REPORTED, Provenance.ESTIMATED],
            confidence=0.55 if (cost and yield_q) else 0.35,
        )


class ResidueAgent(BaseAgent):
    agent_id = 14
    name = "Sustainable Farming and Crop Residue"
    purpose = "Support residue management and soil conservation choices."
    inputs = ["crop and residue type", "time before next sowing", "equipment access"]
    outputs = ["residue management plan with trade-offs"]

    async def analyse(self, ctx: AgentContext) -> AgentResult:
        crop = ctx.entities.get("crop") or ctx.farm.crop or "your crop"
        findings = [
            "Crop residue is organic matter and nutrients. Burning destroys both, pollutes "
            "the air, and is restricted in several states.",
            "Four usable alternatives, chosen by the time, machinery and market you have: "
            + "; ".join(option["option"] for option in RESIDUE_OPTIONS) + ".",
        ]
        actions = ["%s - %s (%s)" % (o["option"], o["how"], o["suits"]) for o in RESIDUE_OPTIONS]
        warnings = ["Quantified carbon or emission benefits need measurement. I can describe "
                    "the direction of the effect, not a number."]
        questions = ["How many days do you have between this harvest and the next sowing?",
                     "Can you hire a baler or a residue-managing seed drill locally?"]

        return self.blank(
            headline="Residue options for %s, without burning." % crop,
            findings=findings,
            actions=actions,
            data={"options": RESIDUE_OPTIONS, "crop": crop},
            assumptions=["Machinery availability and the sowing window decide which option "
                         "is practical; both are unknown here."],
            warnings=warnings,
            questions=questions,
            evidence=ctx.evidence_from("residue", "sustainability"),
            provenance=[Provenance.KNOWLEDGE_BASE],
            confidence=0.6,
        )
