"""Agent 1 - Soil Sensor Acquisition and Data Quality.
Agent 2 - Soil Health and Interpretation.
"""
from __future__ import annotations

from typing import List

from ..core.schemas import AgentResult, Provenance, Severity, Status
from ..sensors.validation import PARAMETERS, REGISTER_MAP_STATUS, interpret
from .base import AgentContext, BaseAgent


class SensorAcquisitionAgent(BaseAgent):
    agent_id = 1
    name = "Soil Sensor Acquisition and Data Quality"
    purpose = "Acquire, decode and validate the seven sensor fields with provenance."
    inputs = ["sensor packet", "mode", "timestamp", "device id", "register map"]
    outputs = ["validated measurement record", "provenance", "validation status"]

    async def analyse(self, ctx: AgentContext) -> AgentResult:
        validation = ctx.reading_validation or {}
        if ctx.reading is None:
            return self.unavailable(
                "No soil sensor reading is available for this field.",
                missing=["sensor reading"],
                questions=["Would you like me to use a clearly labelled simulated "
                           "reading so you can see how the advice works?"],
            )
        status = validation.get("status", "unknown")
        valid_fields: List[str] = validation.get("valid_fields", [])
        findings = [
            "Reading mode: %s (provenance: %s)." % (ctx.reading.mode, ctx.reading.provenance.value),
            "Validation status: %s, %d of %d fields usable."
            % (status, len(valid_fields), len(PARAMETERS)),
            "Recorded at %s (age %s s)." % (ctx.reading.recorded_at.isoformat(),
                                            validation.get("age_seconds", "?")),
        ]
        if validation.get("missing_fields"):
            findings.append("Missing fields: " + ", ".join(validation["missing_fields"]))
        if validation.get("invalid_fields"):
            findings.append("Rejected as out of range (dropped, not clamped): "
                            + ", ".join(validation["invalid_fields"]))

        warnings = list(validation.get("warnings", [])) + list(validation.get("errors", []))
        warnings.extend(ctx.simulation_warning())

        severity = Severity.NONE
        if status in ("invalid", "unavailable"):
            severity = Severity.HIGH
        elif status == "degraded" or validation.get("stale"):
            severity = Severity.MEDIUM
        elif status == "partial":
            severity = Severity.LOW

        return self.blank(
            status=Status.OK if valid_fields else Status.UNAVAILABLE,
            # Provenance, not mode: with the simulator gone every reading is "real"
            # mode, so the mode said nothing. What a farmer needs on this line is
            # whether the numbers were measured.
            headline="Sensor data %s (%s)." % (
                status, str(getattr(ctx.reading.provenance, "value",
                                    ctx.reading.provenance)).replace("_", " ")),
            findings=findings,
            actions=["Confirm the register map against the sensor manual before "
                     "treating these values as calibrated."],
            data={
                "device_id": ctx.reading.device_id,
                "parcel_id": ctx.reading.parcel_id,
                "mode": ctx.reading.mode,
                "provenance": str(getattr(ctx.reading.provenance, "value",
                                          ctx.reading.provenance)),
                "status": status,
                "completeness": validation.get("completeness"),
                "flags": validation.get("flags", []),
                "register_map_status": REGISTER_MAP_STATUS,
                "values": {f: getattr(ctx.reading, f) for f in PARAMETERS},
            },
            assumptions=["The provisional register map matches this sensor model.",
                         "Plausibility checks screen for obvious faults; they do not "
                         "prove the probe is calibrated."],
            warnings=warnings,
            provenance=[ctx.reading.provenance],
            severity=severity,
            confidence=0.85 if status == "valid" else 0.5,
        )


class SoilHealthAgent(BaseAgent):
    agent_id = 2
    name = "Soil Health and Interpretation"
    purpose = "Interpret soil values and identify likely constraints."
    inputs = ["validated sensor values", "lab results", "location", "soil type", "crop"]
    outputs = ["soil interpretation", "constraints", "follow-up checks"]
    depends_on = [1]

    async def analyse(self, ctx: AgentContext) -> AgentResult:
        findings: List[str] = []
        actions: List[str] = []
        warnings: List[str] = list(ctx.simulation_warning())
        constraints: List[str] = []
        severity = Severity.NONE
        data = {}

        if ctx.has_usable_reading and ctx.reading is not None:
            ph = ctx.value("ph")
            ec = ctx.value("ec_ds_m")
            moisture = ctx.value("moisture_pct")
            temperature = ctx.value("temperature_c")

            if ph is not None:
                if ph < 5.5:
                    constraints.append("strongly acidic soil")
                    findings.append("pH %.1f is strongly acidic; phosphorus, calcium and "
                                    "magnesium availability falls and aluminium can harm roots." % ph)
                    actions.append("Get a laboratory lime requirement test before buying lime.")
                    severity = Severity.MEDIUM
                elif ph > 8.5:
                    constraints.append("strongly alkaline soil")
                    findings.append("pH %.1f is strongly alkaline; zinc, iron and phosphorus "
                                    "become less available." % ph)
                    actions.append("Ask for a laboratory test for sodicity (ESP or SAR) "
                                   "before applying gypsum.")
                    severity = Severity.MEDIUM
                elif ph > 7.5:
                    constraints.append("alkaline soil")
                    findings.append("pH %.1f is alkaline; watch for zinc and iron deficiency "
                                    "symptoms in the crop." % ph)
                    severity = max(severity, Severity.LOW, key=_severity_rank)
                else:
                    findings.append("pH %.1f is in the band where most nutrients are "
                                    "available." % ph)

            if ec is not None:
                if ec > 3.0:
                    constraints.append("high salinity")
                    findings.append("EC %.2f dS/m is high; germination and early growth "
                                    "are affected in most crops." % ec)
                    actions.append("Check irrigation water quality and field drainage.")
                    severity = Severity.HIGH
                elif ec > 1.6:
                    constraints.append("moderate salinity")
                    findings.append("EC %.2f dS/m is moderate; salt-sensitive crops such as "
                                    "beans, onion and many vegetables may lose yield." % ec)
                    severity = max(severity, Severity.MEDIUM, key=_severity_rank)
                else:
                    findings.append("EC %.2f dS/m is in the normal range for a fertilised "
                                    "field." % ec)

            if moisture is not None:
                band = interpret("moisture_pct", moisture)
                findings.append("Soil moisture %.1f%% reads %s for a typical loam; the "
                                "irrigation decision belongs to Agent 7." % (moisture, band))
            if temperature is not None:
                findings.append("Soil temperature %.1f C." % temperature)

            npk_notes = []
            for field, label in (("nitrogen_mg_kg", "Nitrogen"),
                                 ("phosphorus_mg_kg", "Phosphorus"),
                                 ("potassium_mg_kg", "Potassium")):
                value = ctx.value(field)
                if value is None:
                    continue
                npk_notes.append("%s %s mg/kg (%s)" % (label, value, interpret(field, value)))
            if npk_notes:
                findings.append("NPK screening: " + "; ".join(npk_notes) + ".")
                warnings.append("Sensor NPK values are a screening signal, not a laboratory "
                                "soil test. They must not be used alone to set a fertiliser dose.")
            data["parameters"] = {f: getattr(ctx.reading, f) for f in PARAMETERS}
        else:
            findings.append("No usable sensor values, so the soil assessment relies on "
                            "background data and what you tell me.")

        soil = ctx.soilgrids or {}
        if soil.get("available"):
            values = soil.get("values", {})
            pieces = ["%s %s %s" % (v["label"], v["value"], v["unit"]) for v in values.values()]
            findings.append("Background soil model (ISRIC SoilGrids, ESTIMATED, not a test "
                            "of your field): " + ", ".join(pieces) + ".")
            data["soilgrids"] = values
        elif soil:
            warnings.append("Background soil model unavailable: %s" % soil.get("reason", ""))

        questions = []
        if not ctx.has_usable_reading:
            questions.append("Do you have a recent soil test report for this field?")

        return self.blank(
            status=Status.OK if findings else Status.NEEDS_INPUT,
            headline=("Soil constraints: " + ", ".join(constraints)) if constraints
                     else "No major soil constraint detected in the available data.",
            findings=findings,
            actions=actions or ["Repeat the reading at the same depth and spot before "
                                "acting on a single value."],
            data=data,
            assumptions=["Interpretation bands are general screening ranges, not "
                         "crop-specific critical limits."],
            warnings=warnings,
            questions=questions,
            evidence=ctx.evidence_from("soil", "soil-health", "ph", "ec"),
            provenance=([ctx.reading.provenance] if ctx.reading else []) +
                       ([Provenance.ESTIMATED] if soil.get("available") else []),
            severity=severity,
            confidence=0.7 if ctx.has_usable_reading else 0.4,
        )


def _severity_rank(severity: Severity) -> int:
    return {Severity.NONE: 0, Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3}[severity]
