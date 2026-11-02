"""Calling endpoints: outbound call-backs, the per-turn voice brain for n8n,
status callbacks, the IVR language menu and the call log.

Twilio credentials live in n8n. These endpoints are the contract between the two
systems; every inbound one requires the shared secret in X-Saarthi-Token.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Depends, Header, HTTPException

from ..conversation import manager
from ..core import farmers, languages, storage
from ..core.config import get_settings
from ..core.schemas import CallRequest, CallTurnIn, FarmContext
from ..orchestrator import orchestrator
from ..speech import azure_speech
from ..core.security import assert_may_read, current_user, owned_phone, require_user
from ..telephony import n8n_client, scheduler

router = APIRouter(prefix="/api/calls", tags=["calls"])


def _require_token(token: Optional[str]) -> None:
    if not n8n_client.verify_inbound_token(token):
        raise HTTPException(
            status_code=401,
            detail=("Invalid or missing X-Saarthi-Token. Set SAARTHI_INBOUND_TOKEN in "
                    "config/api_keys.env and send the same value from n8n."),
        )


@router.get("/status")
async def status() -> Dict[str, Any]:
    return n8n_client.status()


@router.get("/ivr/languages")
async def ivr_languages() -> Dict[str, Any]:
    """The keypad menu n8n reads out, and the prompt text per language."""
    return {
        "menu": n8n_client.language_menu(),
        "prompt_en": "For English press 1. हिन्दी के लिए 2 दबाइए.",
        "note": "A farmer may also simply speak; speech recognition picks the language "
                "from the selected locale.",
    }


@router.post("/outbound")
async def outbound(request: CallRequest,
                   user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """Ask for a call back: one at a time, after the farmer's chosen delay.

    The reply says what actually happened. It used to say "requested" whether or not
    anything was dialled, which is how a rejected number looked identical to a call
    on its way.
    """
    # Dial the account's own number. Otherwise the call-back button is a way to
    # make Saarthi ring any number a caller types.
    request = request.model_copy(update={"phone": owned_phone(user, request.phone)})
    outcome = await scheduler.request_call(request)
    record = outcome.get("call") or {}

    if not outcome["accepted"]:
        # Three different failures, three different codes: the farmer mistyped (400),
        # a call is already on its way (409), or telephony refused it (502). The
        # dashboard shows all three differently, and only the first is the farmer's
        # fault - which is the distinction the old "requested" answer threw away.
        status = {"already_pending": 409, "telephony_failed": 502}.get(
            outcome.get("code"), 400)
        raise HTTPException(status_code=status, detail=outcome["reason"])

    if record.get("mode") == "simulated":
        message = "No call was dialled. " + (record.get("detail") or {}).get("note", "")
    elif record.get("status") == "scheduled":
        message = ("Call booked. Saarthi will dial %s in about %d minute(s)."
                   % (record.get("phone"), outcome.get("delay_minutes", 0)))
    else:
        message = ("Twilio accepted the call to %s and is dialling now."
                   % record.get("phone"))

    return {"call": record, "accepted": True,
            "simulated": record.get("mode") == "simulated",
            "dial_at": outcome.get("dial_at"),
            "delay_minutes": outcome.get("delay_minutes", 0),
            "message": message}


@router.post("/cancel")
async def cancel(payload: Optional[Dict[str, Any]] = None,
                 user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """Force-end anything in flight for this farmer: queued, ringing or connected."""
    payload = payload or {}
    phone = owned_phone(user, str(payload.get("phone") or ""))
    result = await scheduler.cancel_pending(phone)
    # The scheduler already worked out which of the three things actually happened -
    # stopped before dialling, ending at the next turn, or ringing out. Saying
    # "Stopped N calls" over the top of that is how the button came to claim a call
    # was cut while it was still talking.
    result["note"] = result["reason"]
    return result


@router.post("/simulate")
async def simulate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run one full call turn locally: question -> agents -> reply -> speech.

    This is the demo path for a feature-phone conversation when telephony is not
    connected. It is recorded as a simulated call and labelled as one.
    """
    question = str(payload.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    language = str(payload.get("language", "hi"))
    phone = str(payload.get("phone", "+91XXXXXXXXXX"))

    record = await n8n_client.place_call(CallRequest(
        phone=phone, language=language, topic=question[:80],
        farmer_name=str(payload.get("farmer_name", "Farmer")), simulate=True))

    session = manager.load(record["session_id"], language=language, channel="call",
                           phone=phone)
    manager.record_user_turn(session, question)
    advisory = await orchestrator.run(question, session)
    manager.record_assistant_turn(session, advisory.answer, {}, advisory.intent)

    speech = await azure_speech.synthesize(advisory.answer, language, telephony=True)
    n8n_client.record_turn(record["call_id"], question, advisory.answer, language,
                           speech.audio_url if speech.ok else "",
                           {"intent": advisory.intent, "agents": advisory.agents_invoked})
    n8n_client.set_status(record["call_id"], "completed")

    return {
        "call_id": record["call_id"],
        "simulated": True,
        "question": question,
        "reply": advisory.answer,
        "language": language,
        "audio_url": speech.audio_url if speech.ok else None,
        "speech_note": "" if speech.ok else speech.reason,
        "advisory": advisory.model_dump(mode="json"),
    }


@router.post("/turn")
async def turn(payload: CallTurnIn,
               x_saarthi_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Called by n8n for every spoken turn of a live call."""
    _require_token(x_saarthi_token)

    record = storage.get_call(payload.call_sid)
    if record is None:
        record = n8n_client.start_call(payload.call_sid, payload.phone or "unknown",
                                       payload.language or "en", direction="inbound")

    language = payload.language or record.get("language") or "en"

    # Force end call. Saarthi cannot hang up Twilio from here, but it decides what
    # every turn replies with - so the first turn after the request says goodbye and
    # sets end_call, and Twilio drops the line. Checked before anything else: once
    # the farmer has asked for the call to stop, nothing else should be said.
    if scheduler.cancel_requested(record["call_id"]):
        bye = ("ठीक है, कॉल बंद कर रहा हूँ। नमस्ते।" if language == "hi"
               else "All right, ending the call now. Goodbye.")
        speech = await azure_speech.synthesize(bye, language, telephony=True)
        storage.update_call(record["call_id"], status="cancelled")
        return {"call_id": record["call_id"], "language": language, "reply": bye,
                "audio_url": speech.audio_url if speech.ok else None,
                "end_call": True, "next_action": "hangup"}

    if payload.digits:
        chosen = languages.by_dtmf(payload.digits)
        if chosen:
            language = chosen.code
            storage.update_call(record["call_id"], language=language)
            prompt = ("आप सारथी से बात कर रहे हैं। अपना सवाल पूछिए।" if language == "hi"
                      else "You are connected to Saarthi. Please ask your question.")
            speech = await azure_speech.synthesize(prompt, language, telephony=True)
            return {
                "call_id": record["call_id"], "language": language,
                "reply": prompt, "audio_url": speech.audio_url if speech.ok else None,
                "end_call": False, "next_action": "gather_speech",
            }

    question = (payload.transcript or "").strip()
    if not question:
        prompt = ("मुझे आपकी बात समझ नहीं आई। कृपया फिर से बोलिए।" if language == "hi"
                  else "I did not catch that. Please say it again.")
        speech = await azure_speech.synthesize(prompt, language, telephony=True)
        return {"call_id": record["call_id"], "language": language, "reply": prompt,
                "audio_url": speech.audio_url if speech.ok else None,
                "end_call": False, "next_action": "gather_speech"}

    session = manager.load(record.get("session_id"), language=language, channel="call",
                           phone=record.get("phone"))
    manager.record_user_turn(session, question)
    advisory = await orchestrator.run(question, session)
    manager.record_assistant_turn(session, advisory.answer, {}, advisory.intent)

    speech = await azure_speech.synthesize(advisory.answer, language, telephony=True)
    n8n_client.record_turn(record["call_id"], question, advisory.answer, language,
                           speech.audio_url if speech.ok else "",
                           {"intent": advisory.intent, "agents": advisory.agents_invoked})

    settings = get_settings()
    return {
        "call_id": record["call_id"],
        "session_id": session.session_id,
        "language": language,
        "reply": advisory.answer,
        "audio_url": (settings.public_url + speech.audio_url) if speech.ok else None,
        "speech_note": "" if speech.ok else speech.reason,
        "intent": advisory.intent,
        "agents": advisory.agents_invoked,
        "follow_up": advisory.follow_up_questions[:1],
        "end_call": False,
        "next_action": "gather_speech",
    }


@router.post("/status-callback")
async def status_callback(payload: Dict[str, Any],
                          x_saarthi_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Twilio status forwarded by n8n (queued, ringing, completed, failed...)."""
    _require_token(x_saarthi_token)
    call_id = str(payload.get("call_id") or payload.get("CallSid") or "")
    new_status = str(payload.get("status") or payload.get("CallStatus") or "unknown")
    if not call_id:
        raise HTTPException(status_code=400, detail="call_id or CallSid is required")
    record = storage.get_call(call_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown call")
    if payload.get("CallSid") and not record.get("call_sid"):
        storage.update_call(record["call_id"], call_sid=str(payload["CallSid"]))
    n8n_client.set_status(record["call_id"], new_status, {"payload": payload})
    return {"ok": True, "call_id": record["call_id"], "status": new_status}


@router.get("")
@router.get("/")
async def list_calls(limit: int = 50,
                     user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """This account's calls. An administrator sees everyone's."""
    calls = n8n_client.history(limit if user.get("role") == "admin" else 200)
    if user.get("role") == "admin":
        return {"calls": calls, "scope": "all"}
    mine = farmers.normalise_phone(user.get("phone") or "")
    calls = [c for c in calls if farmers.normalise_phone(c.get("phone", "")) == mine]
    return {"calls": calls[:limit], "scope": "mine"}


@router.get("/{call_id}")
async def call_detail(call_id: str,
                      user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    record = storage.get_call(call_id)
    if record is None:
        raise HTTPException(status_code=404, detail="call not found")
    # A call id names a transcript, so reading one has to be an ownership check.
    assert_may_read(user, record.get("phone", ""))
    return record
