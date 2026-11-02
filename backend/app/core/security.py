"""Who is asking, and what they are allowed to see.

Every protected route depends on one of these. They are deliberately small and
deliberately the only place the rules live, so "a user cannot read another user's
data" is one function rather than a habit spread across twenty handlers.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request

from . import accounts, farmers

COOKIE_NAME = "saarthi_session"


def _token(request: Request) -> str:
    """The session token: an Authorization header first, then the cookie.

    The cookie is how the dashboard authenticates; the header is for anything calling
    the API directly. The header wins because sending one is a deliberate act, while
    the cookie rides along on every request - so a caller naming a token explicitly
    gets that token, not whichever session the browser happens to be holding.
    """
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(COOKIE_NAME) or ""


async def current_user(request: Request) -> Optional[Dict[str, Any]]:
    """The signed-in user, or None. Never raises - for routes that work either way."""
    return accounts.session_user(_token(request))


async def require_user(request: Request) -> Dict[str, Any]:
    record = accounts.session_user(_token(request))
    if not record:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    if record.get("status") == "pending":
        raise HTTPException(status_code=403,
                            detail="Verify your email or phone to finish signing up.")
    return record


async def require_admin(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    if user.get("role") != "admin":
        # 404 rather than 403 would hide that the page exists, but an honest 403 is
        # better here: the dashboard shows the admin link only to admins anyway, and a
        # misleading 404 makes a real permissions bug much harder to diagnose.
        raise HTTPException(status_code=403, detail="Administrator access only.")
    return user


def owned_phone(user: Dict[str, Any], requested: str = "") -> str:
    """The phone number this request may act on.

    A user always gets their own, whatever they asked for - passing someone else's
    number is simply ignored rather than refused, because there is no legitimate
    reason for the dashboard to send one and no information to leak either way.

    An admin may act on any number by passing it, and on their own when they do not.
    """
    mine = str(user.get("phone") or "")
    if user.get("role") != "admin":
        return mine
    asked = farmers.normalise_phone(requested) or str(requested or "").strip()
    return asked or mine


def assert_may_read(user: Dict[str, Any], phone: str) -> None:
    """Raise unless this user may read the records of ``phone``."""
    if user.get("role") == "admin":
        return
    if farmers.normalise_phone(phone) != farmers.normalise_phone(user.get("phone") or ""):
        raise HTTPException(status_code=403, detail="That is not your data.")
