"""Telephony through n8n (which holds the Twilio credentials).

Saarthi never stores a Twilio Account SID or auth token. The n8n instance on the
VPS owns those credentials; Saarthi posts a signed request to an n8n webhook and
n8n places or controls the Twilio call. For each spoken turn, n8n posts the
caller's transcript back to Saarthi, Saarthi runs the full agent pipeline and
returns the reply text plus a URL to Azure-synthesised telephony audio.

    farmer's phone -> Twilio -> n8n -> POST /api/calls/turn -> orchestrator
                                   <- reply text + audio url <-

Nothing here fakes a call. If the webhook is not configured, the call is recorded
as a clearly labelled simulation and the advisory is still produced, so the flow
can be demonstrated and tested without telephony credits.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from ..core import languages, storage
from ..core.config import get_settings
from ..core.schemas import CallRequest

log = logging.getLogger("saarthi.telephony")

# Test hook: fn(url, payload, headers) -> dict
_poster = None


def set_poster(poster) -> None:
    global _poster
    _poster = poster


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def configured() -> bool:
    return bool(get_settings().n8n_url("N8N_OUTBOUND_CALL_PATH",
                                       "/webhook/saarthi-outbound-call")) or _poster is not None


def status() -> Dict[str, Any]:
    settings = get_settings()
    outbound = settings.n8n_url("N8N_OUTBOUND_CALL_PATH", "/webhook/saarthi-outbound-call")
    return {
        "configured": bool(outbound) or _poster is not None,
        "n8n_base_url": settings.n8n_base_url,
        "outbound_webhook": outbound,
        "status_webhook": settings.n8n_url("N8N_CALL_STATUS_PATH", "/webhook/saarthi-call-status"),
        "inbound_turn_endpoint": settings.public_url + "/api/calls/turn",
        "inbound_status_endpoint": settings.public_url + "/api/calls/status-callback",
        "helpline_number": settings.helpline_number,
        "auth": {
            "outbound_header": "X-Saarthi-Token (sent to n8n, value N8N_WEBHOOK_TOKEN)",
            "inbound_header": "X-Saarthi-Token (expected from n8n, value SAARTHI_INBOUND_TOKEN)",
            "inbound_token_set": settings.has("SAARTHI_INBOUND_TOKEN"),
        },
        "languages": [{"code": l["code"], "native_name": l["native_name"], "dtmf": l["dtmf"]}
                      for l in languages.as_dicts()],
        "note": ("" if outbound else
                 "Set N8N_BASE_URL and N8N_OUTBOUND_CALL_PATH in config/api_keys.env. Until "
                 "then, call requests are recorded as simulations and never dialled."),
    }


def verify_inbound_token(token: Optional[str]) -> bool:
    """n8n must present the shared secret. An unset secret means 'reject'."""
    settings = get_settings()
    expected = settings.inbound_token
    if not settings.has("SAARTHI_INBOUND_TOKEN"):
        return False
    return bool(token) and token == expected


# ----------------------------------------------------------------- outbound
def to_e164(phone: str, default_country: str = "91") -> str:
    """Twilio rejects anything else, and the rejection is easy to miss.

    "7719605581", "07719605581", "+91 77196 05581" and "91-7719605581" are one
    number. Anything already carrying a + is trusted as typed.
    """
    raw = str(phone or "").strip()
    if raw.startswith("+"):
        return "+" + "".join(ch for ch in raw[1:] if ch.isdigit())
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return ""
    digits = digits.lstrip("0")
    if len(digits) == 10:
        return "+" + default_country + digits
    return "+" + digits


async def place_call(request: CallRequest) -> Dict[str, Any]:
    """Ask n8n to dial the farmer. Returns the stored call record."""
    settings = get_settings()
    # Normalise here as well as at the request door: every path that dials goes
    # through this function, and Twilio rejects anything that is not E.164.
    dialled_number = to_e164(request.phone) or request.phone
    request = request.model_copy(update={"phone": dialled_number})
    lang = languages.resolve(request.language)
    call_id = "call-" + uuid.uuid4().hex[:12]
    session_id = "call-" + uuid.uuid4().hex[:10]
    webhook = settings.n8n_url("N8N_OUTBOUND_CALL_PATH", "/webhook/saarthi-outbound-call")
    simulate = request.simulate or not (webhook or _poster)

    record: Dict[str, Any] = {
        "call_id": call_id,
        "direction": "outbound",
        "phone": request.phone,
        "language": lang.code,
        "status": "simulated" if simulate else "requested",
        "mode": "simulated" if simulate else "live",
        "topic": request.topic,
        "session_id": session_id,
        "created_at": _now(),
        "detail": {
            "farmer_name": request.farmer_name,
            "language_name": lang.english_name,
            "voice": lang.voice,
            # What to say when the line opens, when this call was placed with
            # something to deliver.
            "opening": request.opening,
            "turns": [],
        },
    }

    if simulate:
        record["detail"]["note"] = (
            "SIMULATED CALL. No telephony request was sent. "
            + ("Simulation was requested explicitly." if request.simulate else
               "n8n telephony is not configured, so nothing was dialled."))
        storage.create_call(record)
        return record

    payload = {
        "call_id": call_id,
        "session_id": session_id,
        "to": request.phone,
        "language": lang.code,
        "locale": lang.locale,
        "voice": lang.voice,
        "farmer_name": request.farmer_name,
        "topic": request.topic,
        "turn_webhook": settings.public_url + "/api/calls/turn",
        "status_webhook": settings.public_url + "/api/calls/status-callback",
        "inbound_token": settings.inbound_token,
        "requested_at": _now(),
    }
    headers = {"Content-Type": "application/json"}
    if settings.has("N8N_WEBHOOK_TOKEN"):
        headers["X-Saarthi-Token"] = settings.n8n_webhook_token

    try:
        if _poster is not None:
            response = _poster(webhook, payload, headers)
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(webhook, json=payload, headers=headers)
                if resp.status_code >= 400:
                    raise RuntimeError("n8n returned HTTP %s: %s"
                                       % (resp.status_code, resp.text[:160]))
                try:
                    response = resp.json()
                except ValueError:
                    response = {"raw": resp.text[:400]}
    except Exception as exc:                                     # noqa: BLE001
        log.warning("Outbound call request failed: %s", exc)
        record["status"] = "failed"
        record["detail"]["error"] = str(exc)[:240]
        storage.create_call(record)
        return record

    # n8n answering 200 is not the same as Twilio accepting the call: the workflow
    # responds from its own node, and a rejected number comes back without a SID.
    # Recording "requested" regardless is what made the dashboard promise calls that
    # were never dialled.
    call_sid = (response or {}).get("call_sid") or (response or {}).get("sid")
    if not call_sid:
        record["status"] = "failed"
        record["detail"]["error"] = _no_sid_reason(response)
        record["detail"]["n8n_response"] = response
        storage.create_call(record)
        return record

    record["status"] = (response or {}).get("status") or "requested"
    record["call_sid"] = call_sid
    record["detail"]["n8n_response"] = response
    storage.create_call(record)
    return record


def _no_sid_reason(response: Any) -> str:
    """Say what came back instead of a call SID, in words a person can act on."""
    body = response if isinstance(response, dict) else {"raw": response}
    for key in ("message", "error", "detail", "raw"):
        value = body.get(key)
        if isinstance(value, dict):
            value = value.get("message") or value.get("detail")
        if value:
            return ("Telephony did not accept the call: %s" % str(value))[:300]
    return ("Telephony returned no call SID, so nothing was dialled. Check the Twilio "
            "credential in n8n and that the number is in international format.")


async def hangup(record: Dict[str, Any]) -> bool:
    """Ask n8n to hang up a live call. True only if it actually did.

    Optional. Twilio can end a call instantly through its REST API, but that needs
    the auth token, and the token lives only in n8n's encrypted credential store -
    copying it into this config to make a button work would be a bad trade. So if the
    operator has added a hangup webhook we use it, and if not we return False and the
    caller falls back to ending the call at its next turn.

    Returns False on any doubt. A hangup we only THINK happened is worse than one we
    know did not: the farmer would be told the call was cut while it kept talking,
    which is the bug this whole path exists to fix.
    """
    settings = get_settings()
    webhook = settings.n8n_url("N8N_CALL_HANGUP_PATH", "")
    call_sid = record.get("call_sid") or ""
    if not webhook or not call_sid or not str(call_sid).startswith("CA"):
        return False

    payload = {"call_id": record.get("call_id"), "call_sid": call_sid,
               "action": "hangup", "requested_at": _now()}
    headers = {"Content-Type": "application/json"}
    if settings.has("N8N_WEBHOOK_TOKEN"):
        headers["X-Saarthi-Token"] = settings.n8n_webhook_token

    try:
        if _poster is not None:
            response = _poster(webhook, payload, headers)
        else:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(webhook, json=payload, headers=headers)
                if resp.status_code >= 400:
                    log.warning("hangup webhook returned HTTP %s", resp.status_code)
                    return False
                response = resp.json() if resp.content else {}
    except Exception as exc:                                      # noqa: BLE001
        log.warning("hangup webhook failed: %s", exc)
        return False

    body = response if isinstance(response, dict) else {}
    done = bool(body.get("hung_up") or body.get("ok")
                or str(body.get("status", "")).lower() in ("completed", "canceled",
                                                           "cancelled"))
    if not done:
        log.info("hangup webhook did not confirm; ending at the next turn instead")
    return done


def record_scheduled_call(request: CallRequest, dial_at: str) -> Dict[str, Any]:
    """Book a call for later. The sweeper dials it when ``dial_at`` passes."""
    lang = languages.resolve(request.language)
    record = {
        "call_id": "call-" + uuid.uuid4().hex[:12],
        "direction": "outbound",
        "phone": request.phone,
        "language": lang.code,
        "status": "scheduled",
        "mode": "live",
        "topic": request.topic,
        "session_id": "call-" + uuid.uuid4().hex[:10],
        "created_at": _now(),
        "detail": {
            "farmer_name": request.farmer_name,
            "language_name": lang.english_name,
            "voice": lang.voice,
            "dial_at": dial_at,
            "opening": request.opening,
            "turns": [],
        },
    }
    storage.create_call(record)
    return record


async def dial_scheduled(record: Dict[str, Any]) -> Dict[str, Any]:
    """Dial a call that was booked earlier, keeping its original call_id."""
    detail = dict(record.get("detail") or {})
    placed = await place_call(CallRequest(
        phone=record["phone"], language=record.get("language", "hi"),
        topic=record.get("topic") or "",
        opening=detail.get("opening") or "",
        farmer_name=detail.get("farmer_name", "Farmer")))

    detail.update({"dialled_as": placed.get("call_id"),
                   "n8n_response": (placed.get("detail") or {}).get("n8n_response")})
    if placed.get("status") == "failed":
        detail["error"] = (placed.get("detail") or {}).get("error", "")
    storage.update_call(record["call_id"], status=placed.get("status", "failed"),
                        call_sid=placed.get("call_sid") or "", detail=detail)
    return placed


# ------------------------------------------------------------------ inbound
def _greeting(lang_code: str, farmer_name: str = "") -> str:
    lang = languages.resolve(lang_code)
    if lang.code == "hi":
        return "नमस्ते, मैं सारथी हूँ। अपनी खेती से जुड़ा सवाल पूछिए।"
    return ("Hello, this is Saarthi. Please ask your farming question after the beep."
            if lang.code == "en" else
            "Hello, this is Saarthi. Please ask your farming question.")


def language_menu() -> List[Dict[str, str]]:
    """The DTMF menu read out at the start of a call."""
    return [{"digit": l["dtmf"], "language": l["native_name"], "code": l["code"]}
            for l in languages.as_dicts()]


def start_call(call_sid: str, phone: str, language: str = "en",
               direction: str = "inbound") -> Dict[str, Any]:
    """Register an inbound call that n8n has just answered."""
    existing = storage.get_call(call_sid)
    if existing:
        return existing
    lang = languages.resolve(language)
    record = {
        "call_id": "call-" + uuid.uuid4().hex[:12],
        "call_sid": call_sid,
        "direction": direction,
        "phone": phone,
        "language": lang.code,
        "status": "in_progress",
        "mode": "live",
        "topic": "",
        "session_id": "call-" + uuid.uuid4().hex[:10],
        "created_at": _now(),
        "detail": {"turns": [], "greeting": _greeting(lang.code)},
    }
    storage.create_call(record)
    return record


def record_turn(call_id: str, question: str, reply: str, language: str,
                audio_url: str = "", meta: Optional[Dict[str, Any]] = None) -> None:
    record = storage.get_call(call_id)
    if not record:
        return
    detail = record.get("detail") or {}
    turns = detail.get("turns") or []
    turns.append({
        "at": _now(),
        "question": question,
        "reply": reply,
        "language": language,
        "audio_url": audio_url,
        "meta": meta or {},
    })
    detail["turns"] = turns
    storage.update_call(record["call_id"], detail=detail, language=language)


def set_status(call_id: str, status_value: str, extra: Optional[Dict[str, Any]] = None) -> None:
    record = storage.get_call(call_id)
    if not record:
        return
    detail = record.get("detail") or {}
    if extra:
        detail.setdefault("status_events", []).append({"at": _now(), **extra})
    storage.update_call(record["call_id"], status=status_value, detail=detail)


def history(limit: int = 50) -> List[Dict[str, Any]]:
    records = storage.list_calls(limit)
    for record in records:
        detail = record.get("detail") or {}
        record["turn_count"] = len(detail.get("turns") or [])
        record["last_question"] = (detail.get("turns") or [{}])[-1].get("question", "") \
            if detail.get("turns") else ""
    return records
