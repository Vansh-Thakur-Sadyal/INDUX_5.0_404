"""Opening an account over the phone.

A farmer with a feature phone has no browser to fill a form in, so the call itself has
to be able to create the account. n8n drives this: it posts what the caller said at
each step and gets back the next thing to say.

Two things differ from the web sign-up on purpose:

* **The code goes by SMS, never email.** Someone registering by voice has given a phone
  number and may have given no email at all, and reading a six-digit code out of an
  inbox is not something this caller can do. ``channel="sms"`` is fixed here.
* **There is no password and no captcha.** The caller proved they hold the number by
  answering the call on it, and a password typed on a numeric keypad helps nobody. A
  long random one is set so the row is never password-less; the farmer uses "forgot
  password" from the website if they ever want to sign in there.

The 15-agent chain is NOT run from here. Collecting the details and running the chain
over them are separate jobs, and this one has to stay inside a phone turn.
"""
from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException

from ..core import accounts, farmers, profile as profile_store
from ..core.accounts import OTP_MINUTES
from ..telephony import n8n_client, otp_client

router = APIRouter(prefix="/api/enrol", tags=["enrolment"])

# What the call collects, in the order it is asked for. Each step is one question and
# one short answer, because that is all a spoken turn can carry reliably.
STEPS: List[Dict[str, str]] = [
    {"field": "full_name", "ask_en": "What is your name?",
     "ask_hi": "आपका नाम क्या है?"},
    {"field": "village", "ask_en": "Which village are you in?",
     "ask_hi": "आप किस गाँव में हैं?"},
    {"field": "district", "ask_en": "And which district?",
     "ask_hi": "और कौन सा ज़िला?"},
    {"field": "land_acres", "ask_en": "How many acres of land do you farm?",
     "ask_hi": "आप कितने एकड़ ज़मीन पर खेती करते हैं?"},
    {"field": "crop", "ask_en": "What are you growing right now?",
     "ask_hi": "अभी आप क्या उगा रहे हैं?"},
]

# Land registration is offered after the basics, and skipped unless they say yes.
LAND_STEPS: List[Dict[str, str]] = [
    {"field": "field_name", "ask_en": "What do you call that field?",
     "ask_hi": "उस खेत को आप क्या कहते हैं?"},
    {"field": "field_acres", "ask_en": "How many acres is it?",
     "ask_hi": "वह कितने एकड़ का है?"},
    {"field": "field_soil", "ask_en": "What is the soil like there?",
     "ask_hi": "वहाँ की मिट्टी कैसी है?"},
]

YES_WORDS = ("yes", "yeah", "haan", "ha", "ji", "haanji", "ok", "okay", "theek",
             "sahi", "karo", "chahiye", "hai")
NO_WORDS = ("no", "nahi", "nahin", "nai", "baad", "later", "skip", "mat")


def _require_token(token: Optional[str]) -> None:
    if not n8n_client.verify_inbound_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Saarthi-Token.")


def _said_yes(text: str) -> bool:
    words = str(text or "").lower().split()
    if any(w.strip(".,!?") in NO_WORDS for w in words):
        return False
    return any(w.strip(".,!?") in YES_WORDS for w in words)


def _ask(step: Dict[str, str], language: str) -> str:
    return step["ask_hi"] if language.startswith("hi") else step["ask_en"]


def _number(text: str) -> Optional[float]:
    """Pull an acreage out of what was said. "two and a half" is not handled: the
    caller is asked again rather than guessed at."""
    cleaned = str(text or "").replace(",", " ")
    for token in cleaned.split():
        token = token.strip(".acresekadएकड़")
        try:
            return float(token)
        except ValueError:
            continue
    return None


@router.get("/steps")
async def steps() -> Dict[str, Any]:
    """What the call will ask, so n8n (or a reviewer) can see the script."""
    return {"steps": STEPS, "land_steps": LAND_STEPS,
            "note": "One question per turn. Answers are stored only when the caller "
                    "confirms the code sent to their number."}


@router.post("/turn")
async def enrol_turn(payload: Dict[str, Any],
                     x_saarthi_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """One step of signing up by phone.

    n8n keeps no state: it sends the whole ``collected`` dictionary back each time and
    receives the next question. That makes a dropped call harmless - nothing is written
    until the number is verified.
    """
    _require_token(x_saarthi_token)

    phone = farmers.normalise_phone(payload.get("phone") or payload.get("From") or "")
    if len(phone) != 10:
        raise HTTPException(status_code=400, detail="a 10-digit phone number is required")

    language = str(payload.get("language") or "hi")
    said = str(payload.get("transcript") or payload.get("SpeechResult") or "").strip()
    collected: Dict[str, Any] = dict(payload.get("collected") or {})
    wants_land = bool(payload.get("wants_land"))

    accounts.ensure_schema()
    existing = accounts.find_user(phone)
    if existing and existing.get("status") == "active":
        return {"done": True, "already_registered": True,
                "say": ("आपका खाता पहले से बना हुआ है।" if language.startswith("hi")
                        else "You already have an account with us."),
                "collected": collected}

    # Which question are we on? The first field with no answer yet.
    script = STEPS + (LAND_STEPS if wants_land else [])
    pending = [s for s in script if not collected.get(s["field"])]

    # Record the answer to whatever was last asked.
    #
    # The land offer is handled FIRST and separately: it is a yes/no, not a field, so
    # the general branch below would find no step for it, record nothing, and ask the
    # same question again on every turn.
    asked = str(payload.get("asked") or "")
    if asked == "land_offer" and said:
        wants_land = _said_yes(said)
        collected["wants_land"] = wants_land
        script = STEPS + (LAND_STEPS if wants_land else [])
        pending = [s for s in script if not collected.get(s["field"])]
    elif asked and said:
        step = next((s for s in script if s["field"] == asked), None)
        if step:
            if asked in ("land_acres", "field_acres"):
                acres = _number(said)
                if acres is None:
                    return {"done": False, "asked": asked,
                            "say": _ask(step, language), "collected": collected,
                            "wants_land": wants_land,
                            "note": "could not hear a number"}
                collected[asked] = acres
            else:
                collected[asked] = farmers.clean_text(said)[:80]
            pending = [s for s in script if not collected.get(s["field"])]

    if pending:
        step = pending[0]
        return {"done": False, "asked": step["field"], "say": _ask(step, language),
                "collected": collected, "wants_land": wants_land}

    # The basics are in. Offer land registration once, before finishing.
    if not collected.get("wants_land") and "wants_land" not in collected:
        return {"done": False, "asked": "land_offer", "wants_land": False,
                "say": ("क्या आप अपना खेत भी दर्ज करना चाहेंगे?"
                        if language.startswith("hi")
                        else "Would you like to register a field as well?"),
                "collected": collected}

    # Everything collected: create the pending account and send the code by SMS.
    user = existing
    if not user:
        user = accounts.create_user(
            email="", phone=phone,
            # No password is chosen here; a long random one keeps the row valid and the
            # farmer sets their own through "forgot password" if they ever sign in.
            password=secrets.token_urlsafe(24),
            full_name=str(collected.get("full_name") or ""),
            role="user", status="pending")

    issued = accounts.issue_otp(user["user_id"], "signup", "sms", phone)
    if issued["code"]:
        await otp_client.send_code(channel="sms", to="+91" + phone, code=issued["code"],
                                   purpose="signup", expires_in_minutes=OTP_MINUTES)

    return {
        "done": False, "asked": "code", "user_id": user["user_id"],
        "collected": collected, "wants_land": wants_land,
        "say": ("मैंने आपके नंबर पर छह अंकों का कोड भेजा है। वह कोड बताइए।"
                if language.startswith("hi")
                else "I have sent a six-digit code to your number. Please say that code."),
    }


@router.post("/confirm")
async def enrol_confirm(payload: Dict[str, Any],
                        x_saarthi_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """The caller reads the code back; only now is anything written to their profile."""
    _require_token(x_saarthi_token)

    phone = farmers.normalise_phone(payload.get("phone") or "")
    language = str(payload.get("language") or "hi")
    spoken = str(payload.get("transcript") or payload.get("code") or "")
    # "nine one two three four five" and "912345" are the same code to a person.
    digits = "".join(ch for ch in spoken if ch.isdigit())

    user = accounts.find_user(phone)
    if not user:
        raise HTTPException(status_code=404, detail="no sign-up is in progress for that number")

    problem = accounts.check_otp(user["user_id"], "signup", digits)
    if problem:
        return {"ok": False, "say": ("वह कोड सही नहीं है। दोबारा बताइए।"
                                     if language.startswith("hi")
                                     else "That code is not right. Please say it again."),
                "reason": problem}

    collected: Dict[str, Any] = dict(payload.get("collected") or {})
    accounts.update_user(user["user_id"], status="active",
                         phone_verified=accounts._iso(accounts._now()),
                         full_name=str(collected.get("full_name") or user.get("full_name") or ""))

    # Now write the profile, and the field if one was described.
    profile_store.save_profile(phone, {
        "name": collected.get("full_name"),
        "village": collected.get("village"),
        "district": collected.get("district"),
        "land_acres": collected.get("land_acres"),
        "language": language,
    })
    if collected.get("field_name"):
        profile_store.save_field(phone, {
            "name": collected.get("field_name"),
            "crop": collected.get("crop"),
            "area_acres": collected.get("field_acres"),
            "soil_type": collected.get("field_soil"),
        })
    if collected.get("crop"):
        farmers.remember(phone, "crop", str(collected["crop"]),
                         call_id=str(payload.get("call_id") or "enrol"), source="said_on_call")

    return {
        "ok": True, "user_id": user["user_id"],
        "say": ("आपका खाता बन गया है। अब आप कभी भी कॉल करके सलाह ले सकते हैं।"
                if language.startswith("hi")
                else "Your account is ready. You can call any time for advice."),
        "profile": profile_store.get_profile(phone),
    }
