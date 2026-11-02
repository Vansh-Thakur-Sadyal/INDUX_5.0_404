"""Working out what to say when a booked call comes due, and placing it.

Everything here runs at FIRE time. That is the point of the feature: a price asked
for "in half an hour" is looked up in half an hour, so the farmer hears what the
market is doing when the phone rings rather than what it was doing when they asked.

The honesty rule from the rest of the project applies hardest here, because nobody is
watching. A farmer who answers the phone is going to act on whatever it says, and they
have no screen to check it against. So when a lookup fails the call says the lookup
failed, with the reason, and never fills the gap with the last number it saw.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..core import farmers, tasks
from ..core.schemas import CallRequest
from ..telephony import scheduler
from . import market as market_service
from . import weather as weather_service

log = logging.getLogger("saarthi.tasks")

# A forecast window a farmer can actually act on. Rain in nine days is not a reason
# to ring somebody.
RAIN_LOOKAHEAD_DAYS = 3


def _farm_for_phone(phone: str):
    """The farm behind a phone number, without importing the router layer."""
    from ..routers.insights import farm_for                      # local: avoids a cycle
    return farm_for(phone=phone)


# ------------------------------------------------------------------- resolving

async def _speak_price(crop: str, farm) -> str:
    crop = crop or (farm.crop or "wheat")
    summary = await market_service.get_summary(
        crop, state=farm.state, district=farm.district)

    if not summary.get("available"):
        # Say which crop, so the farmer knows the call was not about something else.
        return ("You asked about the %s price. I could not get it from the market "
                "service just now, so I do not have a number for you. Please try on "
                "the dashboard in a little while." % crop)

    median = summary.get("median_modal")
    highest = summary.get("highest") or {}
    scope = summary.get("scope", "")
    where = {"district": "in your district", "state": "in your state",
             "national": "across the country"}.get(scope, "")
    line = ("The %s price %s is about %d rupees a quintal from %d market reports."
            % (crop, where, round(median or 0), summary.get("record_count", 0)))
    if highest.get("market") and highest.get("modal_price"):
        line += (" The highest is %d at %s."
                 % (round(highest["modal_price"]), highest["market"]))
    # The caveat is not decoration: these are reported arrivals, not an offer to buy.
    return line + " That is what the markets reported, not a guaranteed rate."


async def _speak_weather(farm) -> str:
    summary = await weather_service.get_summary(farm.latitude, farm.longitude, days=4)
    if not summary.get("available"):
        return ("You asked about the weather. I could not reach the weather service "
                "just now, so I do not have a forecast for you.")

    current = summary.get("current") or {}
    forecast = (summary.get("forecast") or [])[:RAIN_LOOKAHEAD_DAYS]
    rain = sum(float(d.get("rain_mm") or 0) for d in forecast)
    line = "Right now it is %s" % (current.get("condition") or "unclear")
    if current.get("temperature_c") is not None:
        line += ", %d degrees" % round(current["temperature_c"])
    if rain >= 1:
        line += (". About %d millimetres of rain is expected over the next %d days."
                 % (round(rain), len(forecast)))
    else:
        line += ". No useful rain is expected in the next few days."
    return line


async def resolve(task: Dict[str, Any]) -> str:
    """What this task should say, worked out now."""
    said = task.get("said") or ""
    if task["kind"] == "reminder":
        # Their own words, handed back. Nothing to get wrong and nothing to go stale.
        return "You asked me to remind you: %s" % said.strip().rstrip(".")

    farm = _farm_for_phone(task["phone"])
    subject = task.get("subject") or ""

    if subject == "weather" or (not subject and "weather" in said.lower()):
        return await _speak_weather(farm)
    if subject in ("", "price"):
        crop = tasks.find_crop(said) or (farm.crop or "")
        return await _speak_price(crop, farm)
    return await _speak_price(subject, farm)


# -------------------------------------------------------------------- watching

async def check_watch(task: Dict[str, Any]) -> Tuple[bool, str]:
    """Has this condition happened yet? Returns (fire now, what to say)."""
    farm = _farm_for_phone(task["phone"])
    kind = task.get("watch_type") or ""

    if kind == "rain":
        summary = await weather_service.get_summary(farm.latitude, farm.longitude, days=4)
        if not summary.get("available"):
            # A failed check is not a triggered watch. Stay quiet and look again on
            # the next sweep; the farmer asked to hear about rain, not about outages.
            return False, ""
        window = (summary.get("forecast") or [])[:RAIN_LOOKAHEAD_DAYS]
        total = sum(float(d.get("rain_mm") or 0) for d in window)
        if total < float(task.get("threshold") or 2.0):
            return False, ""
        wettest = max(window, key=lambda d: float(d.get("rain_mm") or 0), default={})
        return True, ("You asked me to tell you if rain was coming. About %d "
                      "millimetres is expected over the next %d days, the most of it "
                      "on %s. Plan your irrigation and spraying around it."
                      % (round(total), len(window), wettest.get("date", "those days")))

    if kind in ("price_above", "price_below"):
        crop = task.get("subject") or ""
        summary = await market_service.get_summary(
            crop, state=farm.state, district=farm.district)
        if not summary.get("available"):
            return False, ""
        median = summary.get("median_modal")
        if median is None:
            return False, ""
        threshold = float(task.get("threshold") or 0)
        hit = median > threshold if kind == "price_above" else median < threshold
        if not hit:
            return False, ""
        direction = "above" if kind == "price_above" else "below"
        return True, ("You asked me to call when %s went %s %d rupees. It is now "
                      "about %d rupees a quintal in %s. That is what the markets "
                      "reported, not a guaranteed rate."
                      % (crop, direction, round(threshold), round(median),
                         summary.get("scope", "your area")))

    return False, ""


# ------------------------------------------------------------------- the sweep

async def _dial(task: Dict[str, Any], spoken: str) -> bool:
    """Place the call that carries this task's answer."""
    record = farmers.get_farmer(task["phone"]) or {}
    request = CallRequest(
        phone=task["phone"],
        language=task.get("language") or "hi",
        topic=spoken,
        # The whole point of the call. Without this the line opened with the
        # ordinary greeting and waited for the farmer to speak - so a call
        # booked to deliver the wheat price rang on time and never mentioned it.
        opening=spoken,
        farmer_name=record.get("name") or "Farmer",
    )
    outcome = await scheduler.request_call(request)
    if not outcome.get("accepted"):
        # Already on a call, or telephony refused. Leave the task pending so the next
        # sweep tries again - a reminder dropped because the line was busy is exactly
        # the promise this feature exists to keep - but count the attempt, because a
        # setup that can never dial would otherwise retry every twenty seconds for
        # ever and the farmer would never learn the call was not coming.
        reason = outcome.get("reason") or outcome.get("code") or "not accepted"
        attempts = tasks.note_attempt(task["task_id"])
        if attempts >= tasks.MAX_DIAL_ATTEMPTS:
            log.warning("task %s given up after %d attempts: %s",
                        task["task_id"], attempts, reason)
            tasks.mark(task["task_id"], "failed", spoken="Could not place the call: "
                       + str(reason)[:200])
        else:
            log.info("task %s not dialled yet (attempt %d): %s",
                     task["task_id"], attempts, reason)
        return False
    call = outcome.get("call") or {}
    tasks.mark(task["task_id"], "done", spoken=spoken,
               call_id=call.get("call_id", ""))
    return True


async def run_due(now: Optional[datetime] = None) -> Dict[str, int]:
    """One pass: fire what is due, retire what has run out. Never raises."""
    now = now or datetime.now(timezone.utc)
    fired = held = expired = 0

    for task in tasks.due_now(now):
        try:
            if task["kind"] == "watch":
                due = tasks._parse_dt(task.get("expires_at"))
                if due and due <= now:
                    # Same again: an expiry notice at 2am helps nobody.
                    if tasks.in_quiet_hours(now):
                        held += 1
                        continue
                    tasks.mark(task["task_id"], "expired")
                    # Tell them it expired. A watch that quietly stops is a promise
                    # the farmer still believes is running.
                    await _dial(task, _expiry_line(task))
                    expired += 1
                    continue
                triggered, spoken = await check_watch(task)
                if not triggered:
                    continue
                # Nobody asked for a 3am call about the price of wheat. A watch
                # fires whenever the market or the sky decides, so this is the one
                # case where holding until morning is right - the condition is
                # still true then, and the farmer can act on it either way.
                if tasks.in_quiet_hours(now):
                    held += 1
                    continue
            else:
                # A time the farmer chose is honoured, whatever the hour.
                spoken = await resolve(task)

            if await _dial(task, spoken):
                fired += 1
        except Exception as exc:                                  # noqa: BLE001
            log.warning("task %s failed: %s", task.get("task_id"), exc)
            tasks.mark(task["task_id"], "failed", spoken=str(exc)[:300])

    return {"fired": fired, "held_for_morning": held, "expired": expired}


def _expiry_line(task: Dict[str, Any]) -> str:
    if task.get("watch_type") == "rain":
        return ("You asked me to watch for rain. No rain has shown up in the forecast "
                "for %d days, so I am stopping that watch. Ask again any time."
                % tasks.WATCH_DAYS)
    return ("You asked me to watch the %s price. It has not reached %d rupees in %d "
            "days, so I am stopping that watch. Ask again any time."
            % (task.get("subject") or "crop", round(task.get("threshold") or 0),
               tasks.WATCH_DAYS))
