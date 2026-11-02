"""Call-back requests: one at a time, dialled when they are due, honestly reported.

Three faults this fixes, all of them seen on the live box:

1. A request was recorded as "requested" whether or not Twilio accepted it. The
   dashboard said the call was on its way and nothing ever rang.
2. The number was passed through as typed. Twilio needs E.164, so "7719605581"
   was rejected - and that rejection was the thing nobody saw.
3. Nothing stopped a farmer pressing the button five times and being called five
   times.

A request is now stored with the moment it is due, swept by a background loop, and
its status only becomes ``requested`` once Twilio has actually accepted the call.
Keeping ``dial_at`` in the database rather than in an asyncio timer is deliberate:
a restart during the wait would otherwise drop the call silently.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..core import farmers, storage
from ..core.schemas import CallRequest
from . import n8n_client
from .n8n_client import to_e164

log = logging.getLogger("saarthi.callback")

# Statuses that mean "this farmer already has a call coming".
PENDING = ("scheduled", "requested", "queued", "ringing", "in_progress", "initiated")

# A request that never reached a terminal status is not held against the farmer for
# ever; Twilio can go quiet, and the farmer should not be locked out by our bug.
STALE_AFTER_MINUTES = 30

DEFAULT_DELAY_MINUTES = 0
MAX_DELAY_MINUTES = 24 * 60

_sweeper: Optional[asyncio.Task] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def pending_for(phone: str) -> Optional[Dict[str, Any]]:
    """The call this farmer is already waiting for, if there is one."""
    farmers.ensure_schema()
    key = farmers.normalise_phone(phone)
    cutoff = _now() - timedelta(minutes=STALE_AFTER_MINUTES)
    for record in storage.list_calls(60):
        if farmers.normalise_phone(record.get("phone", "")) != key:
            continue
        if record.get("direction") != "outbound" or record.get("status") not in PENDING:
            continue
        created = _parse(record.get("created_at"))
        if created and created < cutoff:
            continue
        return record
    return None


# A call Twilio is already holding. We cannot hang these up from here, so they are
# ended at the next turn instead.
LIVE = ("requested", "queued", "ringing", "in_progress", "initiated")


async def cancel_pending(phone: str) -> Dict[str, Any]:
    """Stop this farmer's calls, and report honestly what could actually be stopped.

    This used to flip our own row to "cancelled" and return success while Twilio
    carried on talking - the button said Force end call and nothing ended. There are
    three genuinely different cases and they are now reported as three:

      stopped    not dialled yet, so cancelling it means nothing will ring.
      ending     Twilio has it. The next turn of the call is told to hang up, which
                 is the farmer's next sentence - a few seconds.
      ringing    dialled but unanswered. There are no turns to carry the instruction,
                 so it will ring out. Saying so beats pretending.
    """
    farmers.ensure_schema()
    key = farmers.normalise_phone(phone)
    stopped, ending, ringing = [], [], []

    for record in storage.list_calls(60):
        if farmers.normalise_phone(record.get("phone", "")) != key:
            continue
        status = str(record.get("status") or "").replace("-", "_")
        if status not in PENDING:
            continue
        call_id = record["call_id"]

        if status == "scheduled":
            # Never dialled. This one really is cancelled.
            storage.update_call(call_id, status="cancelled")
            stopped.append(call_id)
            continue

        # Twilio is holding it. Try for an immediate hangup if the operator has wired
        # one up; otherwise ask the call to end itself at its next turn.
        hung_up = await n8n_client.hangup(record)
        if hung_up:
            storage.update_call(call_id, status="cancelled")
            stopped.append(call_id)
            continue

        detail = dict(record.get("detail") or {})
        detail["cancel_requested_at"] = _now().isoformat()
        storage.update_call(call_id, status="cancel_requested", detail=detail)
        (ending if status == "in_progress" else ringing).append(call_id)

    note = []
    if stopped:
        note.append("%d call(s) stopped before dialling." % len(stopped))
    if ending:
        note.append("%d call(s) in progress will hang up at the next turn, in a few "
                    "seconds." % len(ending))
    if ringing:
        note.append("%d call(s) already ringing cannot be cut from here and will ring "
                    "out unanswered." % len(ringing))
    if not note:
        note.append("Nothing was in flight for this number.")

    return {"cancelled": len(stopped) + len(ending) + len(ringing),
            "stopped": stopped, "ending": ending, "ringing_out": ringing,
            "call_ids": stopped + ending + ringing,
            "reason": " ".join(note)}


def cancel_requested(call_id: str) -> bool:
    """Has someone pressed Force end call on this one?"""
    record = storage.get_call(call_id)
    return bool(record) and str(record.get("status")) == "cancel_requested"


def delay_minutes_for(phone: str) -> int:
    farmers.ensure_schema()
    record = farmers.get_farmer(phone) or {}
    try:
        minutes = int(record.get("call_delay_minutes") or DEFAULT_DELAY_MINUTES)
    except (TypeError, ValueError):
        minutes = DEFAULT_DELAY_MINUTES
    return max(0, min(MAX_DELAY_MINUTES, minutes))


async def request_call(request: CallRequest) -> Dict[str, Any]:
    """Accept a call-back request, or explain why it was not accepted."""
    e164 = to_e164(request.phone)
    if len(e164) < 11:
        return {"accepted": False, "code": "invalid_number",
                "reason": "That does not look like a phone number. Enter 10 digits, or "
                          "the full number with its country code.",
                "call": None}

    existing = pending_for(e164)
    if existing:
        return {"accepted": False, "code": "already_pending",
                "reason": "A call to this number is already %s. Wait for it to finish "
                          "before asking for another." % existing.get("status"),
                "call": existing}

    minutes = delay_minutes_for(e164)
    due = _now() + timedelta(minutes=minutes)
    scheduled = CallRequest(phone=e164, language=request.language, topic=request.topic,
                            opening=request.opening, farmer_name=request.farmer_name,
                            simulate=request.simulate)

    if minutes <= 0:
        record = await n8n_client.place_call(scheduled)
        failed = record.get("status") == "failed"
        return {"accepted": not failed,
                "code": "telephony_failed" if failed else "",
                "reason": (record.get("detail", {}) or {}).get("error", ""),
                "call": record, "dial_at": None, "delay_minutes": 0}

    record = n8n_client.record_scheduled_call(scheduled, due.isoformat())
    return {"accepted": True, "reason": "", "call": record,
            "dial_at": due.isoformat(), "delay_minutes": minutes}


async def dial_due_calls() -> int:
    """Dial every scheduled call whose time has come. Returns how many were dialled."""
    now = _now()
    dialled = 0
    for record in storage.list_calls(100):
        if record.get("status") != "scheduled":
            continue
        detail = record.get("detail") or {}
        due = _parse(detail.get("dial_at"))
        if due is None or due > now:
            continue
        try:
            await n8n_client.dial_scheduled(record)
            dialled += 1
        except Exception as exc:                                  # noqa: BLE001
            log.warning("scheduled call %s failed to dial: %s", record.get("call_id"), exc)
            storage.update_call(record["call_id"], status="failed",
                                detail=dict(detail, error=str(exc)[:240]))
    return dialled


async def _sweep_forever(interval: float = 20.0) -> None:
    while True:
        try:
            await asyncio.sleep(interval)
            await dial_due_calls()
            await _run_due_tasks()
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                  # noqa: BLE001
            log.warning("call sweeper: %s", exc)


async def _run_due_tasks() -> None:
    """Fire the call-backs the farmer booked. Imported here, not at the top, because
    the task runner places its calls through this module."""
    try:
        from ..services import task_runner
        await task_runner.run_due()
    except Exception as exc:                                      # noqa: BLE001
        # A failing task sweep must not stop calls being dialled.
        log.warning("task sweeper: %s", exc)


def start_sweeper() -> Optional[asyncio.Task]:
    global _sweeper
    if _sweeper is None or _sweeper.done():
        _sweeper = asyncio.create_task(_sweep_forever())
    return _sweeper


def stop_sweeper() -> None:
    global _sweeper
    if _sweeper is not None:
        _sweeper.cancel()
        _sweeper = None
