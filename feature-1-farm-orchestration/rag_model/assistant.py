"""Short, grounded RAG answers and crop shortlists."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

import requests

from .agronomy import (
    exact_crop_reference,
    rank_crops,
    seasonal_fallback,
)
from .assistant_knowledge import retrieve_feature_knowledge
from .context_builder import build_agri_context
from .model_client import mistral_chat
from .response_guard import concise_response, guard_response
from .system_prompt import AGRI_RAG_JSON_RETRY_INSTRUCTION, AGRI_RAG_SYSTEM_PROMPT


LocationResolver = Callable[[float, float], str]
WebSearch = Callable[[str], list[dict[str, str]]]
_LOGGER = logging.getLogger(__name__)


def is_crop_selection_question(message: str) -> bool:
    lowered = message.casefold()
    asks_crop = any(term in lowered for term in ("crop", "vegetable", "veggie"))
    asks_decision = any(
        term in lowered
        for term in (
            "recommend",
            "suggest",
            "which",
            "what can i grow",
            "what should i grow",
            "tell me crops",
            "give me crops",
            "best crop",
            "suitable crop",
            "grow instead",
            "alternative",
            "another",
            "instead of",
        )
    )
    return asks_crop and asks_decision


def alternative_crop_reply(
    message: str,
    body: dict[str, Any] | None = None,
    *,
    location_resolver: LocationResolver | None = None,
    sensor_telemetry: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Return a short seasonal shortlist or NPK-ranked alternatives."""
    body = body if isinstance(body, dict) else {}
    supplied_context = body.get("farm_context")
    farm_context = supplied_context if isinstance(supplied_context, dict) else {}
    explicit_crop = body.get("crop") or farm_context.get("crop")
    crop_reference = exact_crop_reference(message)
    current_crop = str(explicit_crop or (crop_reference or {}).get("crop") or "").strip()[:100]

    context = build_agri_context(
        message,
        body,
        farm_context,
        location_resolver=location_resolver,
        sensor_telemetry=sensor_telemetry,
    )
    readings = context["readings"]
    vegetables_only = any(
        word in message.casefold() for word in ("vegetable", "veggie")
    )
    complete_npk = all(
        key in readings for key in ("nitrogen", "phosphorus", "potassium")
    )
    ranked = (
        rank_crops(
            readings,
            current_crop,
            context["season"],
            context["npk_unit"],
            limit=3,
            vegetables_only=vegetables_only,
        )
        if complete_npk
        else []
    )

    if ranked:
        crop_names = [item["crop"] for item in ranked]
        intro = (
            f"Alternatives to {current_crop} for {context['season']}"
            if current_crop
            else f"Closest app-range matches for {context['season']}"
        )
        reply = f"{intro}: {', '.join(crop_names[:-1])} and {crop_names[-1]}."
    else:
        options = seasonal_fallback(
            context["season"],
            vegetables_only=vegetables_only,
        )
        if current_crop:
            options = [
                crop for crop in options
                if crop.casefold() != current_crop.casefold()
            ]
        reply = f"Using broad India {context['season']} options: {', '.join(options[:3])}."
        if complete_npk and context["npk_unit"] not in {"mg/kg", "mgkg"}:
            reply += " Share readings in mg/kg for a soil-based ranking."
        elif not complete_npk:
            reply += " Add N, P, and K readings for a soil-based ranking."

    return {
        "reply": reply,
        "action_link": "npk_advisor.html",
        "action_text": "Open NPK Advisor",
    }


def answer_question(
    message: str,
    *,
    body: dict[str, Any] | None = None,
    location_resolver: LocationResolver | None = None,
    sensor_telemetry: dict[str, Any] | None = None,
    web_search: WebSearch | None = None,
) -> dict[str, str]:
    """Answer from local retrieval, agronomy context, or supplied web results."""
    body = body if isinstance(body, dict) else {}
    farm_context = body.get("farm_context")
    if not isinstance(farm_context, dict):
        farm_context = {}

    if is_crop_selection_question(message):
        return alternative_crop_reply(
            message,
            body,
            location_resolver=location_resolver,
            sensor_telemetry=sensor_telemetry,
        )

    documents = retrieve_feature_knowledge(message, limit=3)
    web_results: list[dict[str, str]] = []
    if not documents and web_search:
        try:
            web_results = web_search(message)
        except requests.RequestException as error:
            _LOGGER.warning("RAG web search failed: %s", type(error).__name__)
    if not documents and not web_results:
        return {
            "reply": (
                "I couldn't find a matching app record or web result. "
                "Try adding the crop, feature, or location."
                if web_search
                else "Which app feature or farming question do you mean?"
            )
        }

    prompt_context = [f"Question: {message}"]
    if documents:
        retrieved_text = "\n".join(
            f"- {item['name']} ({item['route']}): {item['content']}"
            for item in documents
        )
        prompt_context.append(f"Retrieved app information:\n{retrieved_text}")
    if web_results:
        prompt_context.append(
            "External web excerpts (use only supported facts and cite supplied URLs):\n"
            + "\n".join(
                f"- {item['title']}: {item['snippet']} ({item['url']})"
                for item in web_results[:3]
            )
        )

    agronomy_related = any(
        term in message.casefold()
        for term in (
            "crop", "soil", "npk", "plant", "sow", "seed", "harvest",
            "fertiliz", "fertilis", "irrigat", "pest", "leaf", "yield",
        )
    )
    context: dict[str, Any] | None = None
    if agronomy_related:
        context = build_agri_context(
            message,
            body,
            farm_context,
            location_resolver=location_resolver,
            sensor_telemetry=sensor_telemetry,
        )
        prompt_context.append(f"Agronomy context: {context}")
        exact_reference = exact_crop_reference(message, context["crop"])
        if exact_reference:
            prompt_context.append(f"Exact app crop reference: {exact_reference}")
        readings = context["readings"]
        if all(key in readings for key in ("nitrogen", "phosphorus", "potassium")):
            prompt_context.append(
                "Rule-based crop ranking: "
                + str(rank_crops(
                    readings,
                    context["crop"],
                    context["season"],
                    context["npk_unit"],
                ))
            )
        else:
            prompt_context.append(
                f"Broad seasonal options: {seasonal_fallback(context['season'])}"
            )

    if body.get("location"):
        prompt_context.append(f"User location: {str(body['location'])[:120]}")
    if farm_context.get("live_weather"):
        prompt_context.append(f"Live weather: {farm_context['live_weather']}")

    fallback = (
        concise_response(documents[0]["content"])
        if documents
        else concise_response(
            f"{web_results[0]['title']}. {web_results[0]['snippet']}"
        )
    )

    def retry_structured() -> str:
        return mistral_chat(
            [
                {
                    "role": "system",
                    "content": (
                        f"{AGRI_RAG_SYSTEM_PROMPT}\n"
                        f"{AGRI_RAG_JSON_RETRY_INSTRUCTION}"
                    ),
                },
                {"role": "user", "content": "\n\n".join(prompt_context)},
            ],
            json_mode=True,
            retries=1,
            max_tokens=256,
        )

    try:
        reply = mistral_chat(
            [
                {"role": "system", "content": AGRI_RAG_SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n".join(prompt_context)},
            ],
            max_tokens=256,
        )
    except (RuntimeError, requests.RequestException) as error:
        _LOGGER.warning("RAG model unavailable; using grounded retrieval: %s", type(error).__name__)
        reply = fallback
    else:
        reply = guard_response(reply, retry_structured, fallback)

    response = {
        "reply": concise_response(reply),
    }
    if documents:
        response["action_link"] = documents[0]["route"]
        response["action_text"] = f"Open {documents[0]['name']}"
    elif web_results:
        response["action_link"] = web_results[0]["url"]
        response["action_text"] = "View web source"
    return {
        **response,
    }
