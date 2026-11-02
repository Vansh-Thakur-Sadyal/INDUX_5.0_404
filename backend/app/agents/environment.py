"""Agent 5 - Weather and Climate Risk.
Agent 6 - Nutrient Management and Fertiliser.
Agent 7 - Irrigation and Water Management.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core.schemas import AgentResult, Provenance, Severity, Status
from . import crop_data
from .base import AgentContext, BaseAgent

# Soil texture -> (field capacity %, permanent wilting point %) by volume
SOIL_WATER = {
    "sandy": (12.0, 5.0),
    "sandy loam": (18.0, 8.0),
    "loam": (28.0, 12.0),
    "alluvial": (28.0, 12.0),
    "clay loam": (36.0, 18.0),
    "clay": (42.0, 22.0),
    "black": (40.0, 21.0),
    "red": (22.0, 10.0),
    "laterite": (20.0, 9.0),
}

IRRIGATION_EFFICIENCY = {
    "flood": 0.55, "border": 0.55, "basin": 0.6, "furrow": 0.65,
    "sprinkler": 0.75, "drip": 0.9, "rainfed": 1.0,
}

# General N-P2O5-K2O recommendations (kg/ha) for irrigated conditions.
# Starting points only; a soil test and the state package of practices decide
# the final dose.
GENERAL_NPK_RECOMMENDATION = {
    "wheat": (120, 60, 40),
    "rice": (120, 60, 40),
    "maize": (120, 60, 40),
    "mustard": (80, 40, 40),
    "chickpea": (20, 50, 20),
    "barley": (60, 30, 20),
    "cotton": (100, 50, 50),
    "potato": (180, 80, 100),
    "tomato": (150, 80, 80),
    "onion": (110, 60, 80),
    "lentil": (20, 40, 20),
    "pea": (25, 50, 25),
    "garlic": (100, 50, 50),
    # Light feeders, and the two legumes carry their own nitrogen.
    "coriander": (40, 30, 20),
    "fenugreek": (20, 40, 20),
    "watermelon": (100, 50, 50),
    "muskmelon": (90, 50, 50),
    "cucumber": (80, 40, 40),
}

FERTILISER_CONTENT = {
    "urea": {"n": 0.46, "p": 0.0, "k": 0.0, "label": "Urea (46-0-0)"},
    "dap": {"n": 0.18, "p": 0.46, "k": 0.0, "label": "DAP (18-46-0)"},
    "mop": {"n": 0.0, "p": 0.0, "k": 0.60, "label": "MOP (0-0-60)"},
    "ssp": {"n": 0.0, "p": 0.16, "k": 0.0, "label": "SSP (0-16-0)"},
}


class WeatherRiskAgent(BaseAgent):
    agent_id = 5
    name = "Weather and Climate Risk"
    purpose = "Assess weather risk for the crop and for field operations."
    inputs = ["farm location", "timestamped forecast", "crop and stage"]
    outputs = ["weather risk summary", "timing guidance"]

    async def analyse(self, ctx: AgentContext) -> AgentResult:
        weather = ctx.weather or {}
        if not weather.get("available"):
            return self.unavailable(
                "Weather data is unavailable right now (%s), so I will not guess the "
                "forecast." % weather.get("reason", "service unreachable"),
                missing=["weather forecast"],
            )

        current = weather.get("current", {})
        forecast: List[Dict[str, Any]] = weather.get("forecast", [])
        findings = [
            "Now: %s, %s C, humidity %s%%, wind %s km/h (source %s, %s s old)."
            % (current.get("condition"), current.get("temperature_c"),
               current.get("humidity_pct"), current.get("wind_kmh"),
               weather.get("source"), weather.get("age_seconds")),
        ]
        risks: List[str] = []
        actions: List[str] = []
        severity = Severity.NONE

        next_days = forecast[:5]
        rain_total = sum(float(d.get("rain_mm") or 0) for d in next_days)
        max_temp = max([float(d.get("t_max")) for d in next_days
                        if d.get("t_max") is not None] or [0])
        min_temp = min([float(d.get("t_min")) for d in next_days
                        if d.get("t_min") is not None] or [99])
        max_wind = max([float(d.get("wind_max")) for d in next_days
                        if d.get("wind_max") is not None] or [0])
        et0_total = sum(float(d.get("et0_mm") or 0) for d in next_days)

        findings.append("Next %d days: about %.1f mm rain, highs to %.1f C, lows to %.1f C, "
                        "wind to %.0f km/h, reference ET0 about %.1f mm."
                        % (len(next_days), rain_total, max_temp, min_temp, max_wind, et0_total))

        if rain_total >= 25:
            risks.append("significant rain expected")
            actions.append("Postpone irrigation and any spraying until after the rain.")
            severity = Severity.MEDIUM
        elif rain_total >= 5:
            risks.append("light rain expected")
            actions.append("Check the forecast again before irrigating.")
            severity = max(severity, Severity.LOW, key=_rank)
        if max_temp >= 38:
            risks.append("heat stress")
            actions.append("A light irrigation during the hot spell can moderate canopy "
                           "temperature.")
            severity = Severity.HIGH if max_temp >= 42 else Severity.MEDIUM
        if min_temp <= 4:
            risks.append("frost risk")
            actions.append("Light evening irrigation before a frost night reduces damage.")
            severity = Severity.HIGH
        if max_wind >= 20:
            risks.append("windy conditions")
            actions.append("Do not spray while wind is above about 15 km/h; drift wastes "
                           "product and affects neighbouring fields.")
            severity = max(severity, Severity.MEDIUM, key=_rank)
        humidity = current.get("humidity_pct")
        if humidity is not None and float(humidity) >= 80 and 18 <= float(current.get("temperature_c") or 0) <= 30:
            risks.append("humid conditions that favour fungal disease")
            actions.append("Scout more often over the next week rather than spraying by default.")
            severity = max(severity, Severity.LOW, key=_rank)

        warnings: List[str] = []
        if weather.get("stale"):
            warnings.append("The forecast is older than the freshness limit; treat timing "
                            "advice with care.")

        return self.blank(
            headline=("Weather risks: " + ", ".join(risks)) if risks
                     else "No major weather risk in the next five days.",
            findings=findings,
            actions=actions or ["No weather-driven change to planned operations."],
            data={"rain_5d_mm": round(rain_total, 1), "max_temp_c": max_temp,
                  "min_temp_c": min_temp, "max_wind_kmh": max_wind,
                  "et0_5d_mm": round(et0_total, 2), "risks": risks,
                  "retrieved_at": weather.get("retrieved_at"), "forecast": next_days},
            assumptions=["Forecast is for the farm coordinates on file, not a field-level "
                         "observation."],
            warnings=warnings,
            evidence=ctx.evidence_from("weather", "risk"),
            provenance=[Provenance.LIVE_API],
            severity=severity,
            confidence=0.75,
        )


class NutrientAgent(BaseAgent):
    agent_id = 6
    name = "Nutrient Management and Fertiliser"
    purpose = "Evidence-based nutrient guidance with transparent arithmetic."
    inputs = ["soil test", "sensor NPK screening", "crop and stage", "fertiliser history"]
    outputs = ["nutrient plan with calculations, assumptions and sources"]
    depends_on = [1, 2, 4]

    async def analyse(self, ctx: AgentContext) -> AgentResult:
        crop = (ctx.entities.get("crop") or ctx.farm.crop or "").lower()
        spec = crop_data.profile(crop)
        recommendation = GENERAL_NPK_RECOMMENDATION.get(crop)

        if not crop:
            return self.unavailable(
                "I need to know the crop before I can discuss nutrients.",
                missing=["crop"],
                questions=["Which crop is this fertiliser for?"],
            )
        if not spec or not recommendation:
            # The crop IS known - it just is not one this agent holds figures for.
            # Asking the farmer for it again would be blaming them for our gap, and
            # no answer they could type would ever clear it.
            # UNAVAILABLE with no questions: the distinction base.unavailable draws
            # is that NEEDS_INPUT is answerable by the farmer and UNAVAILABLE is not.
            # This one is not - so it carries somewhere to go instead of a question.
            return self.blank(
                status=Status.UNAVAILABLE,
                headline="I do not hold a general nutrient recommendation for %s yet, "
                         "so any dose I gave would be invented." % crop,
                missing_inputs=["nutrient table for %s" % crop],
                actions=["Ask your block agriculture officer or nearest Krishi Vigyan "
                         "Kendra for the local recommendation for %s." % crop,
                         "A soil test for this field would give a better answer than "
                         "any general table anyway."],
                provenance=[Provenance.UNAVAILABLE],
                confidence=0.0,
            )

        n_req, p_req, k_req = recommendation
        area_ha = max(float(ctx.farm.area_acres or 0) * 0.4047, 0.0)

        # Arithmetic: phosphorus first (DAP), then the nitrogen DAP already supplied.
        dap_kg = p_req / FERTILISER_CONTENT["dap"]["p"]
        n_from_dap = dap_kg * FERTILISER_CONTENT["dap"]["n"]
        urea_kg = max(n_req - n_from_dap, 0) / FERTILISER_CONTENT["urea"]["n"]
        mop_kg = k_req / FERTILISER_CONTENT["mop"]["k"]

        findings = [
            "General recommendation for %s under irrigated conditions: %d kg N, %d kg "
            "P2O5 and %d kg K2O per hectare." % (spec["label"], n_req, p_req, k_req),
            "Product arithmetic per hectare: DAP %.0f kg (supplies %d kg P2O5 and %.0f kg N), "
            "urea %.0f kg for the remaining %.0f kg N, MOP %.0f kg for %d kg K2O."
            % (dap_kg, p_req, n_from_dap, urea_kg, max(n_req - n_from_dap, 0), mop_kg, k_req),
        ]
        if area_ha > 0:
            findings.append("For your %.2f acre (%.2f ha) plot: DAP about %.0f kg, urea about "
                            "%.0f kg, MOP about %.0f kg for the whole season."
                            % (ctx.farm.area_acres, area_ha, dap_kg * area_ha,
                               urea_kg * area_ha, mop_kg * area_ha))

        actions = [
            "Apply the full DAP and MOP as a basal dose, placed near the seed row.",
            "Split the urea: about one third at sowing and the rest over two top dressings "
            "timed to crop stage and irrigation.",
        ]
        stage_result = ctx.result(4)
        if stage_result and stage_result.data.get("stage"):
            stage_name = str(stage_result.data["stage"])
            if stage_name in ("tillering", "vegetative", "crown_root_initiation"):
                actions.append("The crop is at %s, which is the right window for a nitrogen "
                               "top dress when the soil is moist." % stage_name.replace("_", " "))
            elif stage_name in ("flowering", "grain_filling", "maturity", "siliqua_filling",
                                "pod_development", "tuber_bulking"):
                actions.append("The crop is at %s; a nitrogen top dress now would mostly be "
                               "wasted." % stage_name.replace("_", " "))

        warnings = ["This is a general recommendation, not a soil-test-based prescription. "
                    "A soil test for this field can change it substantially."]
        warnings.extend(ctx.simulation_warning())

        screening: Dict[str, Any] = {}
        for field, label in (("nitrogen_mg_kg", "N"), ("phosphorus_mg_kg", "P"),
                             ("potassium_mg_kg", "K")):
            value = ctx.value(field)
            if value is not None:
                screening[label] = value
        if screening:
            findings.append("Sensor screening values (not a laboratory test): "
                            + ", ".join("%s %s mg/kg" % (k, v) for k, v in screening.items()) + ".")
            warnings.append("The dose above was NOT calculated from the sensor NPK values. "
                            "Those readings are indirect and are used only to watch for change.")

        soil_result = ctx.result(2)
        if soil_result and "alkaline" in (soil_result.headline or "").lower():
            actions.append("On alkaline soil, consider a zinc sulphate application if the "
                           "crop shows deficiency symptoms, after confirming with an officer.")

        return self.blank(
            headline="Nutrient plan for %s, with the arithmetic shown." % spec["label"],
            findings=findings,
            actions=actions,
            data={
                "crop": crop,
                "recommended_npk_kg_ha": {"N": n_req, "P2O5": p_req, "K2O": k_req},
                "products_kg_ha": {"dap": round(dap_kg, 1), "urea": round(urea_kg, 1),
                                   "mop": round(mop_kg, 1)},
                "products_for_plot_kg": {
                    "dap": round(dap_kg * area_ha, 1),
                    "urea": round(urea_kg * area_ha, 1),
                    "mop": round(mop_kg * area_ha, 1),
                } if area_ha else {},
                "area_ha": round(area_ha, 3),
                "sensor_screening": screening,
            },
            assumptions=[
                "Irrigated conditions and an average yield target for the zone.",
                "Fertiliser grades: urea 46 percent N, DAP 18 percent N and 46 percent "
                "P2O5, MOP 60 percent K2O.",
                "No nutrient credit from manure or a previous legume has been subtracted.",
            ],
            warnings=warnings,
            questions=["Do you have a soil test report for this field, and did you apply "
                       "any manure or fertiliser already this season?"],
            evidence=ctx.evidence_from("nutrients", "npk", "fertiliser"),
            provenance=[Provenance.KNOWLEDGE_BASE, Provenance.ESTIMATED],
            severity=Severity.LOW,
            confidence=0.6,
        )


class IrrigationAgent(BaseAgent):
    agent_id = 7
    name = "Irrigation and Water Management"
    purpose = "Decide whether to irrigate and estimate how much water to apply."
    inputs = ["soil moisture", "soil type", "crop stage", "weather", "irrigation method"]
    outputs = ["irrigation decision, depth and volume with assumptions"]
    depends_on = [1, 2, 4, 5]

    async def analyse(self, ctx: AgentContext) -> AgentResult:
        moisture = ctx.value("moisture_pct")
        soil_type = (ctx.farm.soil_type or "loam").lower()
        field_capacity, wilting_point = SOIL_WATER.get(soil_type, SOIL_WATER["loam"])
        method = (ctx.farm.irrigation_method or "flood").lower()
        efficiency = IRRIGATION_EFFICIENCY.get(method, 0.6)

        stage_result = ctx.result(4)
        kc = float(stage_result.data.get("kc")) if stage_result and stage_result.data.get("kc") else None
        stage_name = stage_result.data.get("stage") if stage_result else None

        weather = ctx.weather or {}
        forecast = weather.get("forecast", []) if weather.get("available") else []
        rain_3d = sum(float(d.get("rain_mm") or 0) for d in forecast[:3])
        et0_3d = sum(float(d.get("et0_mm") or 0) for d in forecast[:3])

        findings: List[str] = []
        warnings: List[str] = list(ctx.simulation_warning())
        actions: List[str] = []
        data: Dict[str, Any] = {
            "soil_type": soil_type, "field_capacity_pct": field_capacity,
            "wilting_point_pct": wilting_point, "method": method,
            "application_efficiency": efficiency, "kc": kc, "stage": stage_name,
            "rain_next_3d_mm": round(rain_3d, 1), "et0_next_3d_mm": round(et0_3d, 2),
        }

        if moisture is None:
            missing = ["soil moisture reading"]
            questions = ["Can you check the soil by hand at root depth, or switch the "
                         "sensor on, so I can judge the moisture?"]
            if not forecast:
                return self.unavailable(
                    "Without a soil moisture value or a forecast I cannot judge irrigation.",
                    missing=missing + ["weather forecast"], questions=questions)
            findings.append("No soil moisture reading, so this is based on weather only.")
            depletion_pct = None
        else:
            available_water = max(field_capacity - wilting_point, 0.1)
            depletion = max(field_capacity - moisture, 0.0)
            depletion_pct = round(min(depletion / available_water, 1.5) * 100, 1)
            if depletion_pct <= 0:
                findings.append(
                    "Soil moisture %.1f%% is at or above the field capacity of %.0f%% for "
                    "%s soil, so the root zone is full and no available water has been "
                    "used yet." % (moisture, field_capacity, soil_type))
            else:
                findings.append(
                    "Soil moisture %.1f%% against a field capacity of %.0f%% and a wilting "
                    "point of %.0f%% for %s soil means about %.0f%% of available water is "
                    "used." % (moisture, field_capacity, wilting_point, soil_type,
                               depletion_pct))
            data["depletion_pct"] = depletion_pct

        et_demand = None
        if kc is not None and et0_3d:
            et_demand = round(et0_3d * kc, 1)
            findings.append("Crop water demand for the next three days is about %.1f mm "
                            "(ET0 %.1f mm multiplied by Kc %.2f for the %s stage)."
                            % (et_demand, et0_3d, kc, str(stage_name).replace("_", " ")))
            data["etc_next_3d_mm"] = et_demand
        if forecast:
            findings.append("Rain expected in the next three days: about %.1f mm." % rain_3d)

        decision = "insufficient data"
        severity = Severity.NONE
        late_stage = stage_name in ("maturity", "harvest")
        if late_stage:
            decision = "stop irrigation, the crop is at %s" % str(stage_name).replace("_", " ")
            findings.append("The crop is at %s, when irrigation is normally stopped to let "
                            "the grain dry down and to allow harvesting."
                            % str(stage_name).replace("_", " "))
            actions.append("Do not irrigate now; prepare for harvest instead.")
        elif depletion_pct is not None:
            if rain_3d >= 20:
                decision = "wait"
                actions.append("Hold irrigation: about %.0f mm of rain is forecast within "
                               "three days." % rain_3d)
            elif depletion_pct >= 50:
                decision = "irrigate now"
                severity = Severity.MEDIUM
            elif depletion_pct >= 35:
                decision = "irrigate within a day or two"
                severity = Severity.LOW
            else:
                decision = "no irrigation needed yet"

            if decision.startswith("irrigate"):
                root_depth_mm = 450.0           # assumed effective root depth
                deficit_mm = (field_capacity - (moisture or 0)) / 100.0 * root_depth_mm
                gross_mm = deficit_mm / efficiency
                litres = gross_mm * 10000 * max(float(ctx.farm.area_acres or 0) * 0.4047, 0)
                findings.append(
                    "To refill the root zone: net %.0f mm, gross %.0f mm at %.0f percent "
                    "%s efficiency." % (deficit_mm, gross_mm, efficiency * 100, method))
                if litres:
                    findings.append("That is roughly %.0f litres for %.2f acres."
                                    % (litres, ctx.farm.area_acres))
                if gross_mm > 80:
                    # State the per-turn figure here. Leaving the split to the language
                    # model means the model does arithmetic, which it must never do.
                    per_turn = gross_mm / 2.0
                    data["split_turns"] = 2
                    data["per_turn_depth_mm"] = round(per_turn, 1)
                    actions.append("Apply about %.0f mm of water (gross) in two turns of "
                                   "about %.0f mm each, a few days apart; a single %.0f mm "
                                   "application would mostly run off or drain past the "
                                   "roots." % (gross_mm, per_turn, gross_mm))
                    warnings.append("The soil is very dry, so the full refill is large. "
                                    "Splitting the irrigation wastes less water.")
                else:
                    actions.append("Apply about %.0f mm of water (gross) and re-check "
                                   "moisture the next day." % gross_mm)
                data.update({"net_depth_mm": round(deficit_mm, 1),
                             "gross_depth_mm": round(gross_mm, 1),
                             "volume_litres": round(litres) if litres else None,
                             "root_depth_mm_assumed": root_depth_mm})

        if stage_name in ("flowering", "panicle_initiation", "crown_root_initiation",
                          "tasseling_silking", "tuber_initiation"):
            warnings.append("The crop is at a water-sensitive stage (%s); do not let it "
                            "run short." % str(stage_name).replace("_", " "))
            severity = max(severity, Severity.MEDIUM, key=_rank)

        if moisture is not None and moisture > field_capacity + 10:
            warnings.append("Soil moisture is above field capacity; check drainage rather "
                            "than adding water.")

        data["decision"] = decision
        return self.blank(
            status=Status.OK if decision != "insufficient data" else Status.NEEDS_INPUT,
            headline="Irrigation: %s." % decision,
            findings=findings,
            actions=actions or ["Re-check soil moisture at root depth before irrigating."],
            data=data,
            assumptions=[
                "Effective root depth assumed 450 mm; adjust for the actual crop and stage.",
                "Field capacity and wilting point taken from typical values for %s soil."
                % soil_type,
                "Application efficiency assumed %.0f percent for %s irrigation."
                % (efficiency * 100, method),
                "A single sensor point may not represent the whole field.",
            ],
            warnings=warnings,
            evidence=ctx.evidence_from("irrigation", "water", "soil-moisture"),
            provenance=([ctx.reading.provenance] if ctx.reading else []) +
                       ([Provenance.LIVE_API] if forecast else []),
            severity=severity,
            confidence=0.7 if (moisture is not None and forecast) else 0.45,
        )


def _rank(severity: Severity) -> int:
    return {Severity.NONE: 0, Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3}[severity]
