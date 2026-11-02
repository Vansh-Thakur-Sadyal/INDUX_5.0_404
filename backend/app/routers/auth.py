"""Sign up, sign in, verify, reset - and the administrator's view of everyone.

Signing up does not create a usable account on its own: the code sent to the email
address or the phone number is what turns ``pending`` into ``active``. That is the
point of the one-time code, so the "verify later" shortcut is deliberately absent.

Error messages are vague on purpose where being specific would help someone guess:
"email or password is wrong" never says which, and asking for a password reset says
the same thing whether or not the address exists.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..core import accounts, captcha, farmers, profile as profile_store
from ..core.accounts import EMAIL_RE, OTP_MINUTES
from ..core.config import get_settings
from ..core.security import COOKIE_NAME, current_user, require_admin, require_user
from ..telephony import n8n_client, otp_client

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/captcha")
async def new_captcha() -> Dict[str, Any]:
    """A question to answer before signing up, signing in or resetting.

    The answer is not in this reply - it is held server-side and checked there.
    """
    return captcha.issue()


def _check_captcha(payload: Dict[str, Any]) -> None:
    problem = captcha.check(str(payload.get("challenge_id") or ""),
                            str(payload.get("captcha") or ""))
    if problem:
        # 428: the request was understood but a precondition was not met. The browser
        # fetches a fresh question and lets the person try again.
        raise HTTPException(status_code=428, detail=problem)


def _set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    # Secure only when we are actually on https, or the cookie would be dropped in
    # local development and nothing would stay signed in.
    public = str(settings.get("SAARTHI_PUBLIC_URL", "") or "")
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=accounts.SESSION_DAYS * 24 * 3600,
        httponly=True,                      # javascript cannot read it, so XSS cannot steal it
        samesite="lax",
        secure=public.startswith("https"),
        path="/",
    )


async def _deliver(user: Dict[str, Any], purpose: str,
                   channel: str = "") -> Dict[str, Any]:
    """Issue ONE code and send it to every channel the account has.

    The sign-in page has always said "we send one 6-digit code to the email address and
    the phone number on your account", and it was sending to whichever one it found
    first - email, in practice, for anyone who signed up on the web. Now both get it,
    and the farmer uses whichever reaches them.

    Passing ``channel`` narrows it to one, which is how the phone-enrolment flow asks
    for SMS alone: a caller registering by voice may have given no email at all.
    """
    email = str(user.get("email") or "").strip()
    phone = str(user.get("phone") or "").strip()

    wanted = [channel] if channel else [c for c, v in (("email", email), ("sms", phone)) if v]
    targets = {"email": email, "sms": n8n_client.to_e164(phone) if phone else ""}
    wanted = [c for c in wanted if targets.get(c)]
    if not wanted:
        return {"sent": False,
                "reason": "There is no email address or phone number on this account."}

    # One code, several deliveries. Issuing per channel would mean the second code
    # silently cancelled the first, and whichever message arrived second would be the
    # only one that worked.
    issued = accounts.issue_otp(user["user_id"], purpose, "+".join(wanted),
                                targets[wanted[0]])
    if not issued["code"]:
        return {"sent": False, "wait": issued["wait"],
                "reason": "A code was just sent. Wait %d second(s) before asking again."
                          % issued["wait"]}

    results: Dict[str, Any] = {}
    for one in wanted:
        outcome = await otp_client.send_code(
            channel=one, to=targets[one], code=issued["code"],
            purpose=purpose, expires_in_minutes=OTP_MINUTES)
        results[one] = outcome

    delivered = [c for c, r in results.items() if r["ok"]]
    failed = {c: r["reason"] for c, r in results.items() if not r["ok"]}
    shown = [_mask(targets[c], c) for c in delivered] or [_mask(targets[c], c) for c in wanted]

    return {
        "sent": bool(delivered),
        "channels": delivered,
        "channel": delivered[0] if delivered else wanted[0],
        "to": " and ".join(shown),
        "failed": failed,
        # Half-delivered is still delivered: say so rather than reporting success.
        "reason": "" if not failed else
                  "; ".join("%s: %s" % (c, r) for c, r in failed.items()),
    }


def _mask(target: str, channel: str) -> str:
    """Show enough to recognise the address without printing it in full."""
    if channel == "email" and "@" in target:
        name, domain = target.split("@", 1)
        head = name[:2] if len(name) > 2 else name[:1]
        return "%s%s@%s" % (head, "*" * max(2, len(name) - len(head)), domain)
    digits = "".join(ch for ch in target if ch.isdigit())
    return ("*" * max(0, len(digits) - 4)) + digits[-4:] if digits else target


# ------------------------------------------------------------------ sign up
@router.post("/register")
async def register(payload: Dict[str, Any]) -> Dict[str, Any]:
    _check_captcha(payload)
    accounts.ensure_schema()
    email = accounts.normalise_email(payload.get("email", ""))
    phone = farmers.normalise_phone(payload.get("phone", ""))
    password = str(payload.get("password") or "")
    full_name = str(payload.get("full_name") or payload.get("name") or "")

    if not email or not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    if len(phone) != 10:
        raise HTTPException(status_code=400,
                            detail="Enter a 10-digit mobile number.")
    problem = accounts.password_problem(password)
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    if accounts.find_user(email) or accounts.find_user(phone):
        # Not a leak: the sign-in page would reveal the same by refusing the address,
        # and a vague message here would leave someone stuck with no way forward.
        raise HTTPException(status_code=409,
                            detail="An account already exists for that email or number. "
                                   "Sign in, or use 'forgot password'.")

    user = accounts.create_user(email=email, phone=phone, password=password,
                                full_name=full_name, role="user", status="pending")
    delivery = await _deliver(user, "signup",
                              channel=str(payload.get("channel") or "") or "")
    return {
        "user_id": user["user_id"],
        "status": user["status"],
        "verification": delivery,
        "message": ("We sent a 6-digit code to %s. Enter it to finish."
                    % delivery.get("to", "you")) if delivery.get("sent")
                   else "Account created, but the code could not be sent: "
                        + str(delivery.get("reason") or ""),
    }


@router.post("/verify")
async def verify(payload: Dict[str, Any], response: Response,
                 request: Request) -> Dict[str, Any]:
    """Confirm a sign-up code. On success the account is active and signed in."""
    user = accounts.find_user(str(payload.get("identifier") or payload.get("email") or
                                  payload.get("phone") or ""))
    if not user:
        raise HTTPException(status_code=404, detail="No account for that email or number.")

    problem = accounts.check_otp(user["user_id"], "signup", str(payload.get("code") or ""))
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    pending = accounts.pending_otp(user["user_id"], "signup") or {}
    field = "email_verified" if pending.get("channel") == "email" else "phone_verified"
    user = accounts.update_user(user["user_id"], status="active",
                                **{field: accounts._iso(accounts._now())}) or user

    # The account owns the farm records for its phone number from here on.
    profile_store.save_profile(user["phone"], {"name": user.get("full_name") or ""})

    token = accounts.start_session(user["user_id"], request.headers.get("user-agent", ""))
    _set_cookie(response, token)
    return {"ok": True, "user": accounts.public_user(user), "token": token,
            "message": "Your account is ready."}


@router.post("/resend")
async def resend(payload: Dict[str, Any]) -> Dict[str, Any]:
    user = accounts.find_user(str(payload.get("identifier") or ""))
    if not user:
        raise HTTPException(status_code=404, detail="No account for that email or number.")
    purpose = str(payload.get("purpose") or "signup")
    if purpose not in ("signup", "reset"):
        raise HTTPException(status_code=400, detail="Unknown purpose.")
    delivery = await _deliver(user, purpose, channel=str(payload.get("channel") or ""))
    if not delivery["sent"]:
        raise HTTPException(status_code=429 if delivery.get("wait") else 502,
                            detail=delivery["reason"])
    return {"ok": True, "verification": delivery}


# ------------------------------------------------------------------ sign in
@router.post("/login")
async def login(payload: Dict[str, Any], response: Response,
                request: Request) -> Dict[str, Any]:
    _check_captcha(payload)
    accounts.ensure_schema()
    identifier = str(payload.get("identifier") or payload.get("email") or "")
    password = str(payload.get("password") or "")
    user = accounts.find_user(identifier)

    if not user or not accounts.verify_password(password, user["password_hash"]):
        if user:
            accounts.note_failed_login(user)
        # One message for both cases: which half was wrong is not the caller's business.
        raise HTTPException(status_code=401, detail="Email, number or password is wrong.")

    locked = accounts.lock_state(user)
    if locked:
        raise HTTPException(status_code=423, detail=locked)

    if user.get("status") == "pending":
        delivery = await _deliver(user, "signup")
        raise HTTPException(
            status_code=403,
            detail="This account is not verified yet. We sent a new code to %s."
                   % delivery.get("to", "you"))

    # The password was right. That is step one; the code is step two, and no session
    # exists until it comes back. Nothing is returned here that could stand in for
    # one - no token, no cookie.
    accounts.note_successful_login(user)
    delivery = await _deliver(user, "signin")
    return {
        "ok": True,
        "otp_required": True,
        "identifier": identifier,
        "verification": delivery,
        "message": "Password accepted. Enter the 6-digit code we sent to %s."
                   % delivery.get("to", "you"),
    }


@router.post("/login-verify")
async def login_verify(payload: Dict[str, Any], response: Response,
                       request: Request) -> Dict[str, Any]:
    """Second step of sign-in: the code, and only then a session."""
    accounts.ensure_schema()
    identifier = str(payload.get("identifier") or "")
    code = str(payload.get("code") or "").strip()
    user = accounts.find_user(identifier)
    if not user:
        # Same refusal as a wrong code: whether the account exists is not public.
        raise HTTPException(status_code=400, detail="That code is wrong or has expired.")

    locked = accounts.lock_state(user)
    if locked:
        raise HTTPException(status_code=423, detail=locked)

    problem = accounts.check_otp(user["user_id"], "signin", code)
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    token = accounts.start_session(user["user_id"], request.headers.get("user-agent", ""))
    _set_cookie(response, token)
    return {"ok": True, "user": accounts.public_user(accounts.get_user(user["user_id"])),
            "token": token}


@router.post("/logout")
async def logout(request: Request, response: Response) -> Dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME) or ""
    accounts.end_session(token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: Optional[Dict[str, Any]] = Depends(current_user)) -> Dict[str, Any]:
    """Who is signed in. The dashboard calls this before it shows anything."""
    if not user:
        return {"signed_in": False, "user": None}
    return {"signed_in": True, "user": accounts.public_user(user),
            "is_admin": user.get("role") == "admin"}


# ----------------------------------------------------------- forgot password
@router.post("/forgot")
async def forgot(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Start a reset. Answers the same whether or not the account exists."""
    _check_captcha(payload)
    same = {"ok": True,
            "message": "If that account exists, a 6-digit code is on its way."}
    user = accounts.find_user(str(payload.get("identifier") or ""))
    if not user:
        return same
    delivery = await _deliver(user, "reset", channel=str(payload.get("channel") or ""))
    if delivery.get("sent"):
        same["sent_to"] = delivery.get("to")
    return same


@router.post("/reset")
async def reset(payload: Dict[str, Any]) -> Dict[str, Any]:
    user = accounts.find_user(str(payload.get("identifier") or ""))
    if not user:
        raise HTTPException(status_code=400, detail="That code is not valid.")

    problem = accounts.check_otp(user["user_id"], "reset", str(payload.get("code") or ""))
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    password = str(payload.get("password") or "")
    bad = accounts.password_problem(password)
    if bad:
        raise HTTPException(status_code=400, detail=bad)

    accounts.update_user(user["user_id"],
                         password_hash=accounts.hash_password(password),
                         status="active", failed_logins=0, locked_until=None)
    # Any session opened with the old password is no longer trustworthy.
    closed = accounts.end_all_sessions(user["user_id"])
    return {"ok": True, "sessions_closed": closed,
            "message": "Password changed. Sign in with the new one."}


@router.post("/change-password")
async def change_password(payload: Dict[str, Any],
                          user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    if not accounts.verify_password(str(payload.get("current") or ""),
                                    user["password_hash"]):
        raise HTTPException(status_code=401, detail="Your current password is wrong.")
    problem = accounts.password_problem(str(payload.get("password") or ""))
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    accounts.update_user(user["user_id"],
                         password_hash=accounts.hash_password(str(payload["password"])))
    accounts.end_all_sessions(user["user_id"])
    return {"ok": True, "message": "Password changed. Sign in again."}


# ------------------------------------------------------------------- admin
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


@admin_router.get("/users")
async def admin_users(_: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    people = accounts.list_users()
    for person in people:
        phone = person.get("phone") or ""
        plots = profile_store.list_fields(phone) if phone else []
        person["fields"] = len(plots)
        person["acres"] = round(sum(float(f.get("area_acres") or 0) for f in plots), 2)
        person["remembered_facts"] = len(farmers.recall(phone)) if phone else 0
        person["calls"] = len(farmers.call_history(phone, limit=50)) if phone else 0
    return {"users": people, "count": len(people),
            "admins": sum(1 for p in people if p["role"] == "admin")}


@admin_router.get("/users/{user_id}")
async def admin_user_detail(user_id: str,
                            _: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    """Everything held about one account - the whole point of the admin view."""
    record = accounts.get_user(user_id)
    if not record:
        raise HTTPException(status_code=404, detail="No such user.")
    phone = record.get("phone") or ""
    return {
        "user": accounts.public_user(record),
        "profile": profile_store.get_profile(phone) if phone else {},
        "calls": farmers.call_history(phone, limit=20) if phone else [],
    }


@admin_router.post("/users/{user_id}/role")
async def admin_set_role(user_id: str, payload: Dict[str, Any],
                         admin: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    role = str(payload.get("role") or "")
    if role not in accounts.ROLES:
        raise HTTPException(status_code=400, detail="Role must be user or admin.")
    if user_id == admin["user_id"] and role != "admin":
        # Removing your own last admin rights locks everybody out of this page.
        raise HTTPException(status_code=400,
                            detail="You cannot remove your own administrator access.")
    record = accounts.update_user(user_id, role=role)
    if not record:
        raise HTTPException(status_code=404, detail="No such user.")
    return {"ok": True, "user": accounts.public_user(record)}


@admin_router.post("/users/{user_id}/status")
async def admin_set_status(user_id: str, payload: Dict[str, Any],
                           admin: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    status = str(payload.get("status") or "")
    if status not in ("active", "locked", "pending"):
        raise HTTPException(status_code=400, detail="Unknown status.")
    if user_id == admin["user_id"] and status == "locked":
        raise HTTPException(status_code=400, detail="You cannot lock your own account.")
    record = accounts.update_user(user_id, status=status)
    if not record:
        raise HTTPException(status_code=404, detail="No such user.")
    if status == "locked":
        accounts.end_all_sessions(user_id)
    return {"ok": True, "user": accounts.public_user(record)}


@admin_router.delete("/users/{user_id}")
async def admin_delete_user(user_id: str, purge: bool = True,
                            admin: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    """Remove an account and, by default, everything belonging to it.

    "Completely from the database" means the farm data too: the profile, the mapped
    fields, every remembered fact, the call log and its transcripts, the advisory
    history and the stored sensor readings for that farmer. Those live in tables keyed
    on the phone number rather than on the user id, so deleting the row in `users`
    alone would leave all of it behind, reachable by the next person to register that
    number.

    This cannot be undone.
    """
    record = accounts.get_user(user_id)
    if not record:
        raise HTTPException(status_code=404, detail="No such user.")
    if user_id == admin["user_id"]:
        raise HTTPException(status_code=400,
                            detail="You cannot delete the account you are signed in as.")

    removed = accounts.delete_user(user_id, purge_farm_data=purge)
    return {"ok": True, "deleted": record.get("email") or record.get("phone"),
            "removed": removed,
            "message": "Account and %d related record(s) deleted permanently."
                       % sum(removed.values())}


@admin_router.get("/otp-status")
async def admin_otp_status(_: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    return otp_client.status()
