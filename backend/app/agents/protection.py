"""Agent 8  - Pest and Disease Risk.
Agent 9  - Weed Management.
Agent 11 - Crop Protection and Safe Application.

These three never produce a definitive diagnosis or a product dose. They narrow
possibilities, ask for the observations that would separate them, and push any
chemical decision to the approved label or a local agriculture officer.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from ..core.schemas import AgentResult, Provenance, Severity, Status
from .base import AgentContext, BaseAgent

# symptom keyword -> candidate causes. Deliberately includes abiotic causes,
# because yellowing is far more often water or nutrition than disease.
SYMPTOM_MAP: Dict[str, Dict[str, Any]] = {
    "yellow": {
        "label": "yellowing of leaves",
        "causes": ["nitrogen deficiency", "waterlogging or root damage", "sulphur or zinc "
                   "deficiency", "sucking pest damage", "a root or vascular disease"],
        "questions": ["Are the older leaves yellow first, or the new ones?",
                      "Is the yellowing spread evenly across the field or in patches?"],
    },
    "spot": {
        "label": "spots on leaves",
        "causes": ["a fungal leaf spot", "a bacterial leaf spot", "spray or fertiliser burn",
                   "sun scorch"],
        "questions": ["What colour is the centre and the edge of the spot?",
                      "Are the spots growing and joining together?"],
    },
    "wilt": {
        "label": "wilting plants",
        "causes": ["water shortage", "root rot or a vascular wilt", "termite or root grub "
                   "damage", "salinity in the root zone"],
        "questions": ["Do the plants recover in the evening?",
                      "When you pull one out, do the roots look dark or cut?"],
    },
    "hole": {
        "label": "holes in leaves",
        "causes": ["caterpillar or borer feeding", "beetle feeding", "mechanical or hail damage"],
        "questions": ["Can you see caterpillars, droppings or an entry hole in the stem?"],
    },
    "curl": {
        "label": "curled or distorted leaves",
        "causes": ["sucking pests such as aphids, jassids or whitefly", "a virus spread by "
                   "those pests", "herbicide drift"],
        "questions": ["Are there small insects on the underside of the leaf?",
                      "Was any herbicide sprayed nearby recently?"],
    },
    "rust": {
        "label": "rust-coloured pustules",
        "causes": ["a rust disease, which is favoured by cool humid weather"],
        "questions": ["Does the orange or brown powder rub off on your finger?"],
    },
    "white": {
        "label": "white powder or growth",
        "causes": ["powdery mildew", "downy mildew on the underside", "mealybug or woolly "
                   "aphid colonies"],
        "questions": ["Is the white growth on the upper surface or the underside?"],
    },
    "stunt": {
        "label": "stunted growth",
        "causes": ["nutrient deficiency", "nematodes", "compacted soil", "a virus"],
        "questions": ["Is the whole field stunted or only patches?"],
    },
}

WEED_OPTIONS = [
    ("cultural", "Timely sowing, correct seed rate and row spacing for quick canopy "
                 "closure, clean seed, and clean bunds and channels."),
    ("mechanical", "A stale seedbed before sowing, then hand weeding or a wheel hoe "
                   "between the rows during the first third of the crop cycle."),
    ("mulching", "Crop residue or plastic mulch to suppress germination and conserve "
                 "moisture."),
    ("rotation", "Rotating crops and operations so the same weed species is not favoured "
                 "every season."),
]


class PestDiseaseAgent(BaseAgent):
    agent_id = 8
    name = "Pest and Disease Risk"
    purpose = "Narrow the possible causes of a crop symptom and say what to check next."
    inputs = ["crop and stage", "farmer observations", "photo if supplied", "weather"]
    outputs = ["candidate causes, uncertainty and diagnostic next steps"]
    depends_on = [4, 5]

    async def analyse(self, ctx: AgentContext) -> AgentResult:
        text = (ctx.question or "").lower()
        matched = [spec for key, spec in SYMPTOM_MAP.items() if key in text]
        # a few common spellings that are not substrings of the key
        if "peela" in text or "yellowing" in text:
            matched.append(SYMPTOM_MAP["yellow"])
        if "patch" in text and SYMPTOM_MAP["spot"] not in matched:
            matched.append(SYMPTOM_MAP["spot"])

        crop = ctx.entities.get("crop") or ctx.farm.crop
        has_image = bool(ctx.entities.get("image_id"))
        model_label = str(ctx.entities.get("image_model_label") or "")
        model_confidence = float(ctx.entities.get("image_model_confidence") or 0.0)
        verdict = str(ctx.entities.get("image_model_verdict") or "")

        if not matched and not model_label and verdict not in (
                "crop_mismatch", "unknown_crop"):
            return self.unavailable(
                "I need a description of what you can see before I can narrow the cause.",
                missing=["symptom description"],
                questions=[
                    "What exactly do you see: yellowing, spots, holes, wilting, curling, "
                    "or powder on the leaves?",
                    "Which part of the plant is affected, and how many plants out of ten?",
                ],
            )

        causes: List[str] = []
        questions: List[str] = []
        labels: List[str] = []
        for spec in matched:
            labels.append(spec["label"])
            causes.extend(spec["causes"])
            questions.extend(spec["questions"])

        findings: List[str] = []
        warnings: List[str] = []
        confidence = 0.4

        # The image model runs before the agents and hands its result in through
        # entities. It is evidence, ranked first, never the final word.
        if verdict == "crop_mismatch":
            seen = str(ctx.entities.get("image_model_seen_crop") or "another crop")
            selected = str(ctx.entities.get("selected_crop") or "the selected crop")
            findings.append(
                "The photo does not match the crop that was selected: %s was chosen, but "
                "the image model sees %s. No disease has been read from this photo."
                % (selected.replace("_", " "), seen.replace("_", " ")))
            warnings.append(
                "Either the wrong crop was picked, or the photo is not a leaf this model "
                "was trained on. Re-take the photo of a single affected leaf of the crop "
                "you actually grow, or describe the symptom in words instead.")
            questions.append("Which crop is in the photo, and which crop is in the field "
                             "you are asking about?")
            confidence = 0.2
        elif verdict == "unknown_crop":
            findings.append(
                "The image model was never trained on the selected crop, so it cannot "
                "screen this photo. Nothing is claimed from the image.")
            warnings.append("Image screening only covers the crops in the model card. The "
                            "advice below comes from your description, not the photo.")
            confidence = 0.3

        if model_label:
            condition = model_label.split("__", 1)[-1].replace("_", " ")
            model_crop = model_label.split("__")[0].replace("_", " ")
            findings.append(
                "The trained image model screened the photo as %s on %s, with %.0f%% "
                "calibrated confidence." % (condition, model_crop, model_confidence * 100))
            causes.insert(0, "%s (image model, %.0f%% confidence)"
                          % (condition, model_confidence * 100))
            warnings.append(
                "The image model only recognises the crops and conditions it was trained "
                "on, and it judges one photo. It is a screening aid, not a diagnosis.")
            confidence = min(0.4 + model_confidence * 0.4, 0.75)
            questions.append("Does the rest of the plant match this, or only the leaf in "
                             "the photo?")
        elif has_image:
            findings.append("A crop photo was attached, but the image model was not "
                            "confident enough to name a condition, so none is claimed.")

        causes = list(dict.fromkeys(causes))
        questions = list(dict.fromkeys(questions))[:3]

        if labels:
            findings.append("Reported symptom: %s%s."
                            % (", ".join(dict.fromkeys(labels)), " on " + crop if crop else ""))
        if causes:
            findings.append("Possible causes, not a diagnosis: " + "; ".join(causes) + ".")
        warnings.append("These are possibilities, not a confirmed diagnosis. Confirming one "
                        "needs a field inspection or a laboratory test.")

        weather = ctx.weather or {}
        if weather.get("available"):
            current = weather.get("current", {})
            humidity = float(current.get("humidity_pct") or 0)
            temp = float(current.get("temperature_c") or 0)
            if humidity >= 75 and 15 <= temp <= 30:
                findings.append("Current weather (humidity %.0f%%, %.0f C) favours fungal "
                                "disease development." % (humidity, temp))
            elif humidity < 50:
                findings.append("Current weather is dry (humidity %.0f%%), which favours "
                                "sucking pests and mites more than fungal disease." % humidity)

        actions = [
            "Walk the field in a W pattern and check at least five spots, including the "
            "underside of leaves and the stem base.",
            "Note how many plants in ten are affected and whether the patch is spreading.",
            "Take a clear close photo of an affected leaf and a whole plant.",
        ]

        # OK, not NEEDS_INPUT. The agent has done its job: it has named the likely
        # causes and the checks that tell them apart. NEEDS_INPUT means "I cannot say
        # anything yet", which was true before the symptom arrived and is not true
        # now - and reporting it as still-incomplete after the farmer answered reads
        # as the answer having been ignored. The requirement to confirm in the field
        # before spraying is a WARNING on a finished result, not a missing input; the
        # Weed agent next door already works exactly this way.
        return self.blank(
            status=Status.OK,
            headline="Possible causes identified; confirm in the field before any "
                     "treatment.",
            findings=findings,
            actions=actions,
            data={"symptoms": labels, "candidate_causes": causes, "crop": crop,
                  "image_attached": has_image, "image_model_label": model_label,
                  "image_model_confidence": round(model_confidence, 4),
                  "image_model_verdict": verdict,
                  "selected_crop": ctx.entities.get("selected_crop", "")},
            assumptions=["Based on the photo and your description; no field inspection has "
                         "been done."],
            warnings=warnings,
            questions=questions,
            evidence=ctx.evidence_from("pest", "disease", "ipm"),
            provenance=[Provenance.FARMER_REPORTED, Provenance.KNOWLEDGE_BASE],
            severity=Severity.MEDIUM,
            confidence=confidence,
        )


class WeedAgent(BaseAgent):
    agent_id = 9
    name = "Weed Management"
    purpose = "Support weed decisions across cultural, mechanical and chemical options."
    inputs = ["crop and stage", "weed observation", "field conditions"]
    outputs = ["weed management plan and precautions"]
    depends_on = [4]

    async def analyse(self, ctx: AgentContext) -> AgentResult:
        crop = ctx.entities.get("crop") or ctx.farm.crop
        stage_result = ctx.result(4)
        stage = stage_result.data.get("stage") if stage_result else None
        das = stage_result.data.get("days_after_sowing") if stage_result else None

        findings = [
            "The critical crop-weed competition period is usually the first third of the "
            "crop cycle; weeds removed in that window protect the most yield.",
        ]
        if das is not None:
            findings.append("The crop is about %d days after sowing, at the %s stage."
                            % (das, str(stage).replace("_", " ")))
        actions = ["%s: %s" % (name.title(), text) for name, text in WEED_OPTIONS]

        warnings = [
            "Herbicide choice depends on the weed species present, the crop, the crop stage "
            "and the next crop in the rotation. I will not name a product or a dose.",
            "Repeating the same herbicide molecule every season selects for resistance; "
            "Phalaris minor in wheat is the well-known Indian example.",
        ]
        questions = [
            "Are the weeds mostly grasses, broadleaf weeds, or sedges?",
            "How tall are they compared with the crop?",
        ]

        return self.blank(
            status=Status.OK,
            headline="Weed plan: start with cultural and mechanical control, keep chemicals "
                     "as the last step.",
            findings=findings,
            actions=actions,
            data={"crop": crop, "stage": stage, "days_after_sowing": das,
                  "options": [name for name, _ in WEED_OPTIONS]},
            assumptions=["No weed species identification has been made."],
            warnings=warnings,
            questions=questions,
            evidence=ctx.evidence_from("weeds", "crop-protection", "ipm"),
            provenance=[Provenance.KNOWLEDGE_BASE],
            severity=Severity.LOW,
            confidence=0.6,
        )


class CropProtectionAgent(BaseAgent):
    agent_id = 11
    name = "Crop Protection and Safe Application"
    purpose = "Check that any protection action is justified, safe and label-backed."
    inputs = ["pest or disease findings", "crop and stage", "weather", "label information"]
    outputs = ["safety-checked guidance or an escalation"]
    depends_on = [8, 9, 5]

    DOSE_PATTERN = re.compile(
        r"(?i)\b(dose|dosage|how much|kitna|ml per|gram per|g per|litre per|per acre|per "
        r"hectare|spray schedule|mix)\b")

    async def analyse(self, ctx: AgentContext) -> AgentResult:
        text = ctx.question or ""
        asked_for_dose = bool(self.DOSE_PATTERN.search(text))
        pest_result = ctx.result(8)
        identified = bool(pest_result and pest_result.data.get("candidate_causes")
                          and pest_result.confidence >= 0.7)

        findings: List[str] = []
        warnings: List[str] = []
        actions: List[str] = []
        severity = Severity.LOW
        status = Status.OK

        if asked_for_dose:
            status = Status.NEEDS_INPUT
            severity = Severity.HIGH
            findings.append("You asked for a product quantity or spray schedule.")
            warnings.append(
                "I will not give a pesticide name, dose, dilution or spray interval. Those "
                "must come from the product label approved for this crop and pest in India, "
                "or from your local agriculture officer or Krishi Vigyan Kendra.")
            actions.append("Take a photo of the affected plant and the product label you "
                           "already have to your nearest agriculture officer or KVK.")
        if not identified:
            findings.append("The cause is not confirmed, so no treatment is justified yet.")
            actions.append("Confirm the cause by field inspection before spending on any "
                           "input.")

        actions.extend([
            "Try non-chemical measures first: remove and destroy affected plant parts, "
            "keep nitrogen balanced, and protect natural enemies.",
            "If you do spray, follow the label for the crop, wear the protective equipment "
            "stated on it, and respect the pre-harvest interval.",
        ])

        weather = ctx.weather or {}
        if weather.get("available"):
            forecast = weather.get("forecast", [])[:2]
            rain = sum(float(day.get("rain_mm") or 0) for day in forecast)
            wind = max([float(day.get("wind_max") or 0) for day in forecast] or [0])
            if rain >= 5:
                warnings.append("About %.0f mm of rain is expected in the next two days; "
                                "most sprays are washed off." % rain)
            if wind >= 15:
                warnings.append("Wind up to %.0f km/h is forecast; spraying would drift."
                                % wind)
            if rain < 5 and wind < 15:
                findings.append("Weather in the next two days is suitable for a spray "
                                "operation if one turns out to be justified.")

        stage_result = ctx.result(4)
        if stage_result and stage_result.data.get("stage") in ("maturity", "grain_filling"):
            warnings.append("The crop is close to harvest; the pre-harvest interval on the "
                            "label may rule out spraying entirely.")
            severity = Severity.HIGH

        return self.blank(
            status=status,
            headline="Crop protection check: escalate for any product decision.",
            findings=findings,
            actions=actions,
            data={"dose_requested": asked_for_dose, "cause_confirmed": identified},
            assumptions=["No product label information is available to this system."],
            warnings=warnings,
            questions=["Would you like the contact pattern for your block agriculture "
                       "office or nearest KVK?"],
            evidence=ctx.evidence_from("crop-protection", "safety", "ipm"),
            provenance=[Provenance.KNOWLEDGE_BASE],
            severity=severity,
            confidence=0.65,
        )
