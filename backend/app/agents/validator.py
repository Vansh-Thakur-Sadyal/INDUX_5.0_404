"""Agent 15 - Integrated Advisory and Recommendation Validation.

The last agent in every run. It reads what the other agents produced and checks
for the failure modes the write-up lists: conflicts, missing inputs, stale data,
simulated values presented as real, and unsafe recommendations. Its output is
what the orchestrator trusts when it decides whether an answer may be given at
all.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..core.schemas import AgentResult, Provenance, Severity, Status
from .base import AgentContext, BaseAgent

HIGH_RISK_INTENTS = {"crop_protection", "pest_disease"}


class ValidationAgent(BaseAgent):
    agent_id = 15
    name = "Integrated Advisory and Recommendation Validation"
    purpose = "Combine agent outputs and validate the advisory before it is delivered."
    inputs = ["all invoked agent outputs", "evidence", "sensor provenance", "farm context"]
    outputs = ["unified advisory with warnings, assumptions and follow-up questions"]
    depends_on = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]

    async def analyse(self, ctx: AgentContext) -> AgentResult:
        others = [r for r in ctx.results.values() if r.agent_id != self.agent_id]
        if not others:
            return self.unavailable("No agent results to validate.")

        conflicts: List[str] = []
        warnings: List[str] = []
        missing: List[str] = []
        questions: List[str] = []
        provenance_counts: Dict[str, int] = {}
        escalate = False

        for result in others:
            missing.extend(result.missing_inputs)
            questions.extend(result.questions)
            for prov in result.provenance:
                provenance_counts[prov.value] = provenance_counts.get(prov.value, 0) + 1

        # 1. values that were not measured must be visible as such
        if ctx.reading_is_simulated:
            warnings.append(
                "The soil values behind this advisory are not measurements. The packet "
                "reached us from the device, but the board is generating the numbers "
                "because no soil probe is wired in yet. Do not act on them as if they "
                "came out of your field.")
        # 2. stale sensor data
        if (ctx.reading_validation or {}).get("stale"):
            warnings.append("The sensor reading is older than the freshness limit, so the "
                            "soil picture may have changed.")
        # 3. stale or missing weather
        if ctx.weather and not ctx.weather.get("available"):
            warnings.append("Weather data was unavailable, so timing advice is incomplete.")
        elif ctx.weather.get("stale"):
            warnings.append("The weather forecast used here is stale.")
        # 4. market data
        if ctx.market and not ctx.market.get("available"):
            warnings.append("Live market prices were unavailable, so no price figure is given.")

        # 5. cross-agent conflict checks
        irrigation = ctx.result(7)
        weather = ctx.result(5)
        if irrigation and weather:
            decision = str(irrigation.data.get("decision", ""))
            rain = float(weather.data.get("rain_5d_mm") or 0)
            if decision.startswith("irrigate") and rain >= 25:
                conflicts.append("Agent 7 advised irrigation while Agent 5 expects about "
                                 "%.0f mm of rain in five days; the rain takes priority "
                                 "unless the crop is at a critical stage." % rain)
        stage = ctx.result(4)
        if irrigation and stage:
            if (str(stage.data.get("stage")) in ("maturity", "harvest")
                    and str(irrigation.data.get("decision", "")).startswith("irrigate")):
                conflicts.append("Agent 7 advised irrigation while Agent 4 places the crop "
                                 "at maturity, when irrigation is normally stopped. "
                                 "Treat the irrigation advice as unsafe here.")
        nutrient = ctx.result(6)
        soil = ctx.result(2)
        if nutrient and soil and "alkaline" in (soil.headline or "").lower():
            warnings.append("Soil is alkaline, which changes nutrient availability; the "
                            "general fertiliser figures may need a local adjustment.")
        protection = ctx.result(11)
        if protection and protection.data.get("dose_requested"):
            escalate = True
            warnings.append("A product dose was requested. That decision is escalated to the "
                            "product label or a local agriculture officer.")
        if ctx.intent in HIGH_RISK_INTENTS:
            pest = ctx.result(8)
            if pest and pest.confidence < 0.6:
                escalate = True

        # 6. numbers that came from nothing
        unsupported = [r.agent_name for r in others
                       if r.status == Status.OK and not r.evidence and not r.data]
        if unsupported:
            warnings.append("Some findings carry no evidence reference: " +
                            ", ".join(unsupported) + ".")

        severities = [r.severity for r in others]
        severity = Severity.NONE
        for level in (Severity.HIGH, Severity.MEDIUM, Severity.LOW):
            if level in severities:
                severity = level
                break

        confident = [r for r in others if r.status == Status.OK]
        overall_confidence = (sum(r.confidence for r in confident) / len(confident)) if confident else 0.0
        if warnings:
            overall_confidence *= 0.9
        if conflicts:
            overall_confidence *= 0.75

        checks = {
            "agents_checked": len(others),
            "conflicts_found": len(conflicts),
            "simulated_data_used": ctx.reading_is_simulated,
            "stale_inputs": bool([w for w in warnings if "stale" in w.lower()]),
            "escalation_required": escalate,
            "provenance_counts": provenance_counts,
            "missing_inputs": sorted(set(missing)),
        }

        findings = ["Checked %d agent outputs for conflicts, units, provenance and "
                    "missing inputs." % len(others)]
        findings.extend(conflicts)
        if not conflicts:
            findings.append("No contradiction found between the agents that ran.")
        if provenance_counts:
            findings.append("Provenance mix: " + ", ".join(
                "%s x%d" % (k, v) for k, v in sorted(provenance_counts.items())) + ".")

        actions = []
        if escalate:
            actions.append("Take this to your block agriculture officer or nearest Krishi "
                           "Vigyan Kendra before spending money or spraying.")
        if missing:
            actions.append("Provide the missing inputs for a sharper answer: " +
                           ", ".join(sorted(set(missing))[:4]) + ".")

        return self.blank(
            status=Status.OK,
            headline=("Validated with %d warning(s)." % len(warnings)) if warnings
                     else "Validated: no blocking issue found.",
            findings=findings,
            actions=actions,
            data=checks,
            assumptions=["Validation checks consistency and provenance; it cannot verify "
                         "agronomic correctness in the field."],
            warnings=warnings,
            questions=list(dict.fromkeys(questions))[:3],
            provenance=[Provenance.KNOWLEDGE_BASE],
            severity=severity,
            confidence=round(min(max(overall_confidence, 0.0), 0.95), 2),
        )
