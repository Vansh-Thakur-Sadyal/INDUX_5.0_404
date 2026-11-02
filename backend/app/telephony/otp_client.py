"""Sending a one-time code, through the n8n chain that owns the mail and SMS accounts.

Saarthi never holds the SMTP password or the Twilio token - the same arrangement the
call path already uses. It posts the code to a webhook behind a shared secret and n8n
decides how to deliver it.

The contract (cloned from the existing AFI_OTP_Delivery chain):

    POST  <N8N_BASE_URL><N8N_OTP_PATH>
    header  x-otp-secret: <N8N_OTP_SECRET>
    body    {channel: "email"|"sms", to, app, code, expires_in_minutes,
             subject, intro}
    ->  200 {ok: true, channel}   |  401 bad secret  |  502 the send failed
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

import httpx

from ..core.config import get_settings

log = logging.getLogger("saarthi.otp")

APP_NAME = "Saarthi 2.0"

_poster: Optional[Callable[[str, Dict[str, Any], Dict[str, str]], Any]] = None


def set_poster(poster) -> None:
    """Install a fake transport. Tests only."""
    global _poster
    _poster = poster


def configured() -> bool:
    settings = get_settings()
    return bool(_poster) or bool(
        settings.n8n_url("N8N_OTP_PATH", "") and settings.has("N8N_OTP_SECRET"))


def status() -> Dict[str, Any]:
    settings = get_settings()
    endpoint = settings.n8n_url("N8N_OTP_PATH", "")
    return {
        "configured": configured(),
        "endpoint": endpoint,
        "secret_set": settings.has("N8N_OTP_SECRET"),
        "note": "" if configured() else (
            "Set N8N_BASE_URL, N8N_OTP_PATH and N8N_OTP_SECRET in config/api_keys.env. "
            "Until then a code is created but never delivered."),
    }


async def send_code(*, channel: str, to: str, code: str, purpose: str,
                    expires_in_minutes: int) -> Dict[str, Any]:
    """Deliver ``code`` to ``to``. Returns {ok, reason}."""
    settings = get_settings()
    endpoint = settings.n8n_url("N8N_OTP_PATH", "")
    if not configured():
        return {"ok": False, "reason": status()["note"]}

    what = {
        "signup": "Confirm this is you and your Saarthi account is ready.",
        "reset": "Use this code to set a new password.",
        "login": "Use this code to finish signing in.",
    }.get(purpose, "Use this code to continue.")

    payload = {
        "channel": channel,
        "to": to,
        "app": APP_NAME,
        "code": code,
        "expires_in_minutes": expires_in_minutes,
        "subject": "%s: your verification code" % APP_NAME,
        "intro": what,
    }
    headers = {"Content-Type": "application/json",
               "x-otp-secret": settings.get("N8N_OTP_SECRET", "")}

    try:
        if _poster is not None:
            body = _poster(endpoint, payload, headers)
        else:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(endpoint, json=payload, headers=headers)
            if response.status_code >= 400:
                # The code itself must never appear in a log or an error shown to a
                # caller; only why the send failed.
                reason = response.text[:200]
                log.warning("OTP send failed (%s): %s", response.status_code, reason)
                return {"ok": False,
                        "reason": "The code could not be sent (%s)." % response.status_code}
            body = response.json() if response.content else {}
    except Exception as exc:                                      # noqa: BLE001
        log.warning("OTP send error: %s", exc)
        return {"ok": False, "reason": "The code could not be sent just now."}

    if isinstance(body, dict) and body.get("ok") is False:
        return {"ok": False, "reason": str(body.get("reason", ""))[:200]}
    return {"ok": True, "reason": ""}


async def send_notice(*, channel: str, to: str, subject: str, intro: str,
                      lines: Optional[List[str]] = None) -> Dict[str, Any]:
    """Deliver a plain message - not a code - through the same n8n chain.

    Separate from [send_code] because the two must not be confused at the far
    end: a code is a secret with an expiry and a single use, and a notice is
    neither. The payload carries no ``code`` field at all, so a workflow that
    reads one cannot accidentally treat a notice as one.

    Used to tell a farmer their profile changed, and what it changed from. That
    message is the only thing standing between "somebody edited my account" and
    nobody ever finding out, so it goes to every channel the account has.
    """
    settings = get_settings()
    endpoint = settings.n8n_url("N8N_OTP_PATH", "")
    if not configured():
        return {"ok": False, "reason": status()["note"]}

    payload = {
        "channel": channel,
        "to": to,
        "app": APP_NAME,
        "subject": subject,
        "intro": intro,
        "lines": list(lines or []),
        # A notice, not a code. Named explicitly so the workflow can tell.
        "kind": "notice",
    }
    headers = {"Content-Type": "application/json",
               "x-otp-secret": settings.get("N8N_OTP_SECRET", "")}

    try:
        if _poster is not None:
            body = _poster(endpoint, payload, headers)
        else:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(endpoint, json=payload, headers=headers)
            if response.status_code >= 400:
                log.warning("notice send failed (%s)", response.status_code)
                return {"ok": False,
                        "reason": "The message could not be sent (%s)." % response.status_code}
            body = response.json() if response.content else {}
    except Exception as exc:                                      # noqa: BLE001
        log.warning("notice send error: %s", exc)
        return {"ok": False, "reason": "The message could not be sent just now."}

    if isinstance(body, dict) and body.get("ok") is False:
        return {"ok": False, "reason": str(body.get("reason", ""))[:200]}
    return {"ok": True, "reason": ""}
