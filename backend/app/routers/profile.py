"""The farmer's own record: profile, plots on the map, and the memory switch.

Everything here is about one phone number. The dashboard works as a single farmer
(the one whose number is set in the profile), so the number is resolved once and
every route hangs off it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..core import accounts, farmers, profile as profile_store
from ..core.security import assert_may_read, owned_phone, require_user
from ..core.config import get_settings
from ..telephony import scheduler

router = APIRouter(prefix="/api/profile", tags=["profile"])

# The dashboard's own identity when no number has been entered yet. It is a real row
# in the same table, so everything the farmer types survives, and the moment they put
# their number in, the profile moves across to it.
DEFAULT_PHONE = "dashboard-owner"


def _phone(value: Optional[str]) -> str:
    """Resolve the farmer this request is about, as a stable key.

    ``normalise_phone`` keeps the last ten digits, so anything without digits - the
    placeholder identity included - normalises to the empty string. Falling back to
    the sentinel rather than to "" matters: an empty key is one every such farmer
    would share, and their fields would pile up in the same row.
    """
    candidate = (value or "").strip()
    if not candidate:
        settings = get_settings()
        candidate = settings.get("SAARTHI_OWNER_PHONE", "") or DEFAULT_PHONE
    return farmers.normalise_phone(candidate) or DEFAULT_PHONE


@router.get("")
@router.get("/")
async def read_profile(phone: str = "",
                       user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """This account's record. ``phone`` is honoured for administrators only."""
    key = _phone(owned_phone(user, phone))
    body = profile_store.get_profile(key)
    body["call_settings"] = {
        "delay_minutes": scheduler.delay_minutes_for(key),
        "pending": scheduler.pending_for(key),
        "max_delay_minutes": scheduler.MAX_DELAY_MINUTES,
    }
    return body


# Changing the profile moves real things: the phone number the call bot rings, the
# land the weather is read for, the district the mandi prices come from. So a save
# is confirmed with a one-time code, the same way signing in is.
EDIT_PURPOSE = "profile"


@router.post("/edit-code")
async def profile_edit_code(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """Send the code that confirms a profile change."""
    from .auth import _deliver

    return await _deliver(user, EDIT_PURPOSE)


@router.post("")
@router.post("/")
async def write_profile(payload: Dict[str, Any],
                        user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """Save the profile, once a code sent to this account has been entered.

    The code is checked here rather than on the page, because a check on the page
    is a check anybody can skip. It is required for every save that changes
    something: the phone number decides which account a caller reaches, and the
    village and district decide whose weather and whose mandi prices the farmer
    is shown.
    """
    key = _phone(owned_phone(user, payload.get("phone", "")))
    values = {k: v for k, v in payload.items() if k not in ("phone", "code")}

    # Nothing actually changing needs no code - that is a repaint, not an edit.
    #
    # `get_profile` returns the whole page's worth - fields, calls, facts - with
    # the record itself under "profile". Comparing against the wrapper made
    # every field look changed and every "before" look empty, so the notice read
    # "village: (empty) -> Kalsan" for a farmer who had a village all along.
    current = (profile_store.get_profile(key) or {}).get("profile") or {}
    changed = {k: v for k, v in values.items()
               if str(current.get(k) or "") != str(v or "")}
    wanted_phone = _phone(str(payload.get("phone") or key))
    if wanted_phone != key:
        changed["phone"] = wanted_phone

    if changed:
        problem = accounts.check_otp(user["user_id"], EDIT_PURPOSE,
                                     str(payload.get("code") or ""))
        if problem:
            raise HTTPException(status_code=403, detail=problem)

    body = profile_store.save_profile(key, values)
    body["saved"] = True
    body["changed"] = sorted(changed)

    if changed:
        # Tell them it happened, and what it was before.
        #
        # This is the only thing standing between "somebody changed my account"
        # and nobody ever finding out. It names the old value as well as the new
        # one, because "your village was changed" is a notification and "your
        # village was changed from Rampur to Kalsan" is something the farmer can
        # actually check against what they meant to do.
        body["notified"] = await _tell_them_it_changed(user, changed, current)
    return body


# Fields whose old value must not be read back to anybody, even its owner, over
# a channel as open as SMS. Nothing here is secret today; the list exists so
# that adding one later does not quietly start broadcasting it.
NEVER_ECHOED = {"password", "code"}


def _describe(field: str, before: Any, after: Any) -> str:
    name = field.replace("_", " ")
    if field in NEVER_ECHOED:
        return "%s was changed" % name
    old = str(before or "").strip() or "(empty)"
    new = str(after or "").strip() or "(empty)"
    return "%s: %s -> %s" % (name, old, new)


async def _tell_them_it_changed(user: Dict[str, Any], changed: Dict[str, Any],
                                before: Dict[str, Any]) -> Dict[str, Any]:
    """Email and SMS the farmer that their profile moved, and how."""
    from ..telephony import n8n_client, otp_client

    lines = [_describe(field, before.get(field), value)
             for field, value in sorted(changed.items())]
    email = str(user.get("email") or "").strip()
    phone = str(user.get("phone") or "").strip()
    targets = {"email": email, "sms": n8n_client.to_e164(phone) if phone else ""}

    sent: List[str] = []
    failed: Dict[str, str] = {}
    for channel, target in targets.items():
        if not target:
            continue
        outcome = await otp_client.send_notice(
            channel=channel, to=target,
            subject="Your Saarthi profile was updated",
            intro=("Your Saarthi profile was just updated. If this was not you, "
                   "change your password and tell us."),
            lines=lines)
        if outcome["ok"]:
            sent.append(channel)
        else:
            failed[channel] = outcome["reason"]
    return {"sent": sent, "failed": failed, "lines": lines}


# --------------------------------------------------------------------- fields
@router.get("/fields")
async def list_fields(phone: str = "",
                      user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    key = _phone(owned_phone(user, phone))
    plots = profile_store.list_fields(key)
    return {"phone": key, "fields": plots, "count": len(plots),
            "total_acres": round(sum(float(f.get("area_acres") or 0) for f in plots), 2)}


@router.post("/fields")
async def create_field(payload: Dict[str, Any],
                       user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    key = _phone(owned_phone(user, payload.get("phone", "")))
    field_id = str(payload.get("field_id") or "")
    record = profile_store.save_field(key, payload, field_id=field_id)
    if not record:
        raise HTTPException(status_code=400, detail="the field could not be saved")
    return {"field": record, "saved": True}


@router.delete("/fields/{field_id}")
async def remove_field(field_id: str,
                       user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    existing = profile_store.get_field(field_id)
    if not existing:
        raise HTTPException(status_code=404, detail="no such field")
    # A field id is guessable enough that ownership has to be checked, not assumed.
    assert_may_read(user, existing.get("phone", ""))
    if not profile_store.delete_field(field_id):
        raise HTTPException(status_code=404, detail="no such field")
    return {"deleted": True, "field_id": field_id}


# --------------------------------------------------------------------- memory
@router.delete("/memory")
async def erase_memory(phone: str = "", keep_name: bool = True,
                       user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """Forget every remembered fact and transcript for this farmer.

    ``keep_name`` is on by default: the farmer asked the assistant to forget what it
    learned, not to forget who they are. Typed profile details are untouched.
    """
    key = _phone(owned_phone(user, phone))
    result = profile_store.erase_memory(key, keep_name=keep_name)
    result["message"] = (
        "Memory cleared. %d remembered fact(s), %d call transcript(s) and %d stored "
        "turn(s) were deleted.%s" % (
            result["removed"]["facts"], result["removed"]["calls"],
            result["removed"]["turns"],
            (" The name %s was kept." % result["kept_name"]) if result["kept_name"]
            else " The name was cleared too."))
    return result
