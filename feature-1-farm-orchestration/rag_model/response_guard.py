"""Post-process generated advice and retry generic deflections in structured form."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

import requests


_LOGGER = logging.getLogger(__name__)

FORBIDDEN_RESPONSE_PATTERNS = (
    r"\bi['’]?m not sure\b",
    r"\bi am not sure\b",
    r"\bi (?:do not|don't) know\b",
    r"\bi can['’]?t confirm\b",
    r"\bi cannot confirm\b",
    r"\bthese are examples, not recommendations\b",
    r"\bi can['’]?t recommend\b",
    r"\bi cannot recommend\b",
    r"\bi won['’]?t recommend\b",
    r"\bconsult (?:a )?local (?:agricultural )?extension\b",
    r"\bask (?:a )?local extension\b",
)


def has_generic_deflection(reply: str) -> bool:
    lowered = reply.casefold()
    return any(re.search(pattern, lowered) for pattern in FORBIDDEN_RESPONSE_PATTERNS)


def concise_response(reply: str) -> str:
    """Keep generated replies to the direct answer and at most three items."""
    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    if not lines:
        return ""

    bullet_pattern = re.compile(r"^(?:[-*•]|\d+[.)])\s+")
    bullet_indices = [
        index for index, line in enumerate(lines) if bullet_pattern.match(line)
    ]
    if bullet_indices:
        first_bullet = bullet_indices[0]
        heading = " ".join(lines[:first_bullet]).strip()
        if heading.startswith("#"):
            heading = ""
        bullets = [
            line for line in lines[first_bullet:]
            if bullet_pattern.match(line)
        ][:3]
        return "\n".join(([heading] if heading else []) + bullets)

    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", " ".join(lines))
    return " ".join(sentences[:3]).strip()


def _reply_from_json(payload: str) -> str | None:
    candidate = payload.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE)
    try:
        decoded: Any = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    reply = decoded.get("reply")
    if isinstance(reply, str) and reply.strip():
        return reply.strip()

    parts = []
    for key in ("recommendations", "actions", "assumptions", "follow_up_questions"):
        values = decoded.get(key)
        if isinstance(values, list):
            parts.extend(str(value).strip() for value in values if str(value).strip())
    return "\n".join(parts) or None


def guard_response(
    reply: str,
    retry_structured: Callable[[], str],
    deterministic_fallback: str,
) -> str:
    """Retry flagged generic answers as JSON, then use the rule-based answer."""
    if not has_generic_deflection(reply):
        return reply

    try:
        retry_payload = retry_structured()
    except (RuntimeError, requests.RequestException) as error:
        _LOGGER.warning("Structured agronomy response retry failed: %s", type(error).__name__)
        return deterministic_fallback
    structured_reply = _reply_from_json(retry_payload)
    if structured_reply and not has_generic_deflection(structured_reply):
        return structured_reply
    _LOGGER.warning("Structured agronomy response remained invalid or generic; using rule output.")
    return deterministic_fallback