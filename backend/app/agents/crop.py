"""Agent 3  - Crop Suitability and Selection.
Agent 4  - Crop Growth Stage and Agronomy.
Agent 10 - Seed, Sowing and Crop Establishment.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from ..core.schemas import AgentResult, Provenance, Severity, Status
from . import crop_data
from .base import AgentContext, BaseAgent

WATER_AVAILABILITY_MM = {"low": 300, "moderate": 600, "high": 1100, "assured": 1400}


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


class CropSuitabilityAgent(BaseAgent):
    agent_id = 3
    name = "Crop Suitability and Selection"
    purpose = "Rank crops that fit this farm, season, soil and water situation."
    inputs = ["location", "season", "soil pH and EC", "water availability", "preferences"]
    outputs = ["ranked crop shortlist with the reason for each score"]
    depends_on = [2]

    async def analyse(self, ctx: AgentContext) -> AgentResult:
        season = (ctx.entities.get("season") or ctx.farm.season
                  or crop_data.current_season(datetime.now().month)).lower()
        candidates = crop_data.crops_for_season(season)
        if not candidates:
            return self.unavailable(
                "No crop profiles are loaded for the %s season." % season,
                missing=["crop profiles for this season"],
            )

        ph = ctx.value("ph")
        ec = ctx.value("ec_ds_m")
        water_mm = WATER_AVAILABILITY_MM.get((ctx.farm.water_availability or "moderate").lower(), 600)

        ranked: List[Dict[str, Any]] = []
        for crop in candidates:
            spec = crop_data.CROP_PROFILES[crop]
            score = 60.0
            reasons: List[str] = []

            lo, best_lo, best_hi, hi = spec["ph"]
            if ph is None:
                reasons.append("soil pH unknown, scored on season and water only")
            elif best_lo <= ph <= best_hi:
                score += 18
                reasons.append("pH %.1f is in the preferred band %.1f-%.1f" % (ph, best_lo, best_hi))
            elif lo <= ph <= hi:
                score += 6
                reasons.append("pH %.1f is tolerable but not ideal" % ph)
            else:
                score -= 25
                reasons.append("pH %.1f is outside the tolerated range %.1f-%.1f" % (ph, lo, hi))

            if ec is None:
                reasons.append("soil EC unknown")
            elif ec <= spec["ec_tolerance"] * 0.5:
                score += 12
                reasons.append("EC %.2f dS/m is well within tolerance" % ec)
            elif ec <= spec["ec_tolerance"]:
                score += 4
                reasons.append("EC %.2f dS/m is near the tolerance limit %.1f"
                               % (ec, spec["ec_tolerance"]))
            else:
                score -= 22
                reasons.append("EC %.2f dS/m exceeds the tolerance of %.1f dS/m"
                               % (ec, spec["ec_tolerance"]))

            need_lo, need_hi = spec["water_need_mm"]
            if water_mm >= need_hi:
                score += 12
                reasons.append("water availability covers the %d-%d mm requirement"
                               % (need_lo, need_hi))
            elif water_mm >= need_lo:
                score += 4
                reasons.append("water availability is tight against the %d-%d mm requirement"
                               % (need_lo, need_hi))
            else:
                score -= 20
                reasons.append("water availability is below the %d mm minimum requirement"
                               % need_lo)

            if ctx.farm.crop and ctx.farm.crop.lower() == crop:
                score -= 6
                reasons.append("same crop as the previous season; rotation would be better")

            ranked.append({
                "crop": crop,
                "label": spec["label"],
                "score": max(0, min(100, round(score))),
                "duration_days": spec["duration_days"],
                "water_need_mm": spec["water_need_mm"],
                "sowing_window": spec["sowing_window"],
                "reasons": reasons,
            })

        ranked.sort(key=lambda r: r["score"], reverse=True)
        top = ranked[:4]
        findings = ["%s: %d/100 - %s" % (r["label"], r["score"], "; ".join(r["reasons"]))
                    for r in top]
        assumptions = [
            "Season taken as %s." % season,
            "Water availability '%s' read as about %d mm for the season."
            % (ctx.farm.water_availability, water_mm),
            "Scores compare crops against each other on this data only; they are not "
            "yield predictions.",
        ]
        if ph is None or ec is None:
            assumptions.append("Soil pH or EC was unavailable, so the soil part of the "
                               "score is incomplete.")

        return self.blank(
            headline="Best fit for the %s season: %s." % (season, top[0]["label"]),
            findings=findings,
            actions=["Confirm seed availability and the local package of practices "
                     "before committing to a crop."],
            data={"season": season, "ranked": ranked, "water_assumption_mm": water_mm},
            assumptions=assumptions,
            warnings=ctx.simulation_warning(),
            evidence=ctx.evidence_from("crop", "crop-selection", "agronomy"),
            provenance=[Provenance.KNOWLEDGE_BASE] +
                       ([ctx.reading.provenance] if ctx.reading else []),
            confidence=0.7 if (ph is not None and ec is not None) else 0.45,
        )


class CropStageAgent(BaseAgent):
    agent_id = 4
    name = "Crop Growth Stage and Agronomy"
    purpose = "Establish the crop stage and the management it calls for."
    inputs = ["crop", "sowing date", "reported stage", "observations"]
    outputs = ["stage summary", "stage-specific actions", "crop coefficient"]

    async def analyse(self, ctx: AgentContext) -> AgentResult:
        crop = (ctx.entities.get("crop") or ctx.farm.crop or "").lower()
        spec = crop_data.profile(crop)
        if not spec:
            return self.unavailable(
                "I do not have an agronomy profile for this crop yet.",
                missing=["crop"],
                questions=["Which crop is growing in this field right now?"],
            )

        sowing = _parse_date(ctx.farm.sowing_date)
        reported = ctx.entities.get("stage") or ctx.farm.stage
        stage = None
        basis = ""
        das: Optional[int] = None

        if sowing:
            das = (date.today() - sowing).days
            stage = crop_data.stage_for_days(crop, das)
            basis = "days after sowing (%d days since %s)" % (das, sowing.isoformat())
        if stage is None and reported:
            stage = crop_data.stage_by_name(crop, str(reported))
            basis = "stage reported by the farmer (%s)" % reported
        if stage is None:
            return self.unavailable(
                "The crop stage is unknown, so stage-specific advice would be a guess.",
                missing=["sowing date or observed stage"],
                questions=["Has the crop started flowering yet, and roughly how many "
                           "days ago was it sown?"],
            )

        findings = [
            "Crop: %s." % spec["label"],
            "Stage: %s, established from %s." % (stage["name"].replace("_", " "), basis),
            "Stage note: %s" % stage["note"],
            "Crop coefficient (Kc) for this stage: %.2f." % stage["kc"],
        ]
        if das is not None:
            findings.append("Typical season length for this crop: %d to %d days."
                            % (spec["duration_days"][0], spec["duration_days"][1]))

        return self.blank(
            headline="%s is at the %s stage." % (spec["label"], stage["name"].replace("_", " ")),
            findings=findings,
            actions=[stage["note"]],
            data={"crop": crop, "label": spec["label"], "stage": stage["name"],
                  "kc": stage["kc"], "days_after_sowing": das,
                  "stages": [s["name"] for s in spec["stages"]], "basis": basis},
            assumptions=["Stage windows are typical for the zone; a cool or warm season "
                         "shifts them by several days."],
            evidence=ctx.evidence_from("stages", "agronomy"),
            provenance=[Provenance.FARMER_REPORTED if reported and not sowing
                        else Provenance.ESTIMATED],
            confidence=0.75 if sowing else 0.55,
        )


class SeedSowingAgent(BaseAgent):
    agent_id = 10
    name = "Seed, Sowing and Crop Establishment"
    purpose = "Support seed rate, sowing window, spacing and establishment decisions."
    inputs = ["crop", "season", "soil moisture", "weather", "seed guidance"]
    outputs = ["sowing plan with rate, spacing, depth and timing checks"]
    depends_on = [3, 5]

    async def analyse(self, ctx: AgentContext) -> AgentResult:
        crop = (ctx.entities.get("crop") or ctx.farm.crop or "").lower()
        spec = crop_data.profile(crop)
        if not spec:
            suggestion = ctx.result(3)
            if suggestion and suggestion.data.get("ranked"):
                crop = suggestion.data["ranked"][0]["crop"]
                spec = crop_data.profile(crop)
        if not spec:
            return self.unavailable(
                "I need to know which crop you plan to sow.",
                missing=["crop"],
                questions=["Which crop do you plan to sow in this field?"],
            )

        rate_lo, rate_hi = spec["seed_rate_kg_ha"]
        area_ha = max(float(ctx.farm.area_acres or 0) * 0.4047, 0.0)
        findings = [
            "Sowing window: %s." % spec["sowing_window"],
            "Seed rate: %s to %s kg per hectare for a normal, timely sowing."
            % (rate_lo, rate_hi),
            "Row spacing %s to %s cm, sowing depth %s to %s cm."
            % (spec["row_spacing_cm"][0], spec["row_spacing_cm"][1],
               spec["sowing_depth_cm"][0], spec["sowing_depth_cm"][1]),
        ]
        if area_ha > 0:
            findings.append("For your %.2f acre (%.2f ha) plot that is about %.1f to %.1f kg "
                            "of seed." % (ctx.farm.area_acres, area_ha,
                                          rate_lo * area_ha, rate_hi * area_ha))

        warnings: List[str] = list(ctx.simulation_warning())
        actions = [
            "Use certified seed with a recent germination test and adjust the rate if "
            "germination is below 85 percent.",
            "Line sow with a drill rather than broadcasting for even depth and spacing.",
        ]

        moisture = ctx.value("moisture_pct")
        if moisture is not None:
            if moisture < 15:
                warnings.append("Soil moisture %.1f%% is low for germination; a pre-sowing "
                                "irrigation is usually needed." % moisture)
            elif moisture > 75:
                warnings.append("Soil moisture %.1f%% is very high; sowing into saturated "
                                "soil risks crusting and poor emergence." % moisture)
            else:
                findings.append("Soil moisture %.1f%% is workable for sowing." % moisture)

        weather = ctx.weather or {}
        if weather.get("available"):
            next_three = weather.get("forecast", [])[:3]
            rain = sum(float(day.get("rain_mm") or 0) for day in next_three)
            if rain > 25:
                warnings.append("About %.0f mm of rain is forecast over the next three "
                                "days; delay sowing until the field is workable." % rain)
            else:
                findings.append("Rain forecast for the next three days is about %.0f mm." % rain)
        else:
            warnings.append("Weather forecast unavailable, so sowing-time weather could "
                            "not be checked.")

        return self.blank(
            headline="Sowing plan for %s." % spec["label"],
            findings=findings,
            actions=actions,
            data={"crop": crop, "seed_rate_kg_ha": spec["seed_rate_kg_ha"],
                  "row_spacing_cm": spec["row_spacing_cm"],
                  "sowing_depth_cm": spec["sowing_depth_cm"],
                  "area_ha": round(area_ha, 3),
                  "sowing_window": spec["sowing_window"]},
            assumptions=["Seed rates are for normal sowing time and bold seed; late sowing "
                         "usually needs a higher rate.",
                         "Plot area taken as %.2f acres." % ctx.farm.area_acres],
            warnings=warnings,
            evidence=ctx.evidence_from("seed", "sowing", "establishment"),
            provenance=[Provenance.KNOWLEDGE_BASE],
            severity=Severity.LOW if warnings else Severity.NONE,
            confidence=0.7,
        )
