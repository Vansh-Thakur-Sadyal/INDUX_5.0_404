"""Accounts: who may sign in, as what, and how their secrets are stored.

Three rules this module exists to enforce:

1. **Nothing reversible is stored.** A password is put through scrypt, a session token
   and a one-time code through SHA-256. "Encrypt the saved credentials" is the right
   instinct and hashing is the stronger form of it: encryption implies a key that can
   turn the stored value back into the secret, and for a password nobody - including
   this server - ever needs to do that. A stolen database yields no passwords.

2. **A farmer is a phone number.** The rest of Saarthi already keys everything on the
   last ten digits (``farmers.normalise_phone``), so an account owns exactly the data
   belonging to its phone number. That is what makes "users cannot see another user's
   data" enforceable rather than aspirational.

3. **Roles are only ``user`` and ``admin``.** An admin sees everything; a user sees
   their own row and nothing else. There is no third state and no per-object sharing,
   because every extra case is another way to get it wrong.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import farmers
from .storage import cursor

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id          TEXT PRIMARY KEY,
    email            TEXT UNIQUE,
    phone            TEXT UNIQUE,
    password_hash    TEXT NOT NULL,
    full_name        TEXT,
    role             TEXT NOT NULL DEFAULT 'user',
    status           TEXT NOT NULL DEFAULT 'pending',
    email_verified   TEXT,
    phone_verified   TEXT,
    failed_logins    INTEGER NOT NULL DEFAULT 0,
    locked_until     TEXT,
    last_login       TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions_auth (
    token_hash   TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    user_agent   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions_auth(user_id);
CREATE TABLE IF NOT EXISTS otp_codes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    purpose      TEXT NOT NULL,
    channel      TEXT NOT NULL,
    target       TEXT NOT NULL,
    code_hash    TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    consumed_at  TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_otp_user ON otp_codes(user_id, purpose, id DESC);
"""

ROLES = ("user", "admin")
SESSION_DAYS = 7
OTP_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_SECONDS = 60
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15

# scrypt at these parameters costs ~100 ms and 16 MB per hash on this server: slow
# enough to make guessing expensive, fast enough that a farmer is not left waiting.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1

log = logging.getLogger("saarthi.accounts")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _parse(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def ensure_schema() -> None:
    farmers.ensure_schema()
    with cursor() as cur:
        cur.executescript(SCHEMA)


# ----------------------------------------------------------------- secrets
def hash_password(password: str) -> str:
    """scrypt with a per-user salt. The salt is stored beside the hash, as intended:
    it is not a secret, it is what stops one rainbow table covering every account."""
    salt = os.urandom(16)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                         n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (
        SCRYPT_N, SCRYPT_R, SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(key).decode())


def verify_password(password: str, stored: str) -> bool:
    """Constant-time comparison; a wrong format is a failed check, never an exception."""
    try:
        scheme, n, r, p, salt_b64, key_b64 = str(stored).split("$")
        if scheme != "scrypt":
            return False
        key = hashlib.scrypt(password.encode("utf-8"),
                             salt=base64.b64decode(salt_b64),
                             n=int(n), r=int(r), p=int(p), dklen=32)
    except Exception:                                             # noqa: BLE001
        return False
    return hmac.compare_digest(key, base64.b64decode(key_b64))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def password_problem(password: str) -> str:
    """Why this password is not acceptable, or "" when it is."""
    if len(password or "") < 8:
        return "Use at least 8 characters."
    if password.isdigit():
        return "A password of only digits is too easy to guess."
    if password.lower() in ("password", "12345678", "saarthi1", "qwertyui"):
        return "That password is too common."
    return ""


# ------------------------------------------------------------------- users
def normalise_email(email: str) -> str:
    return str(email or "").strip().lower()


def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    with cursor() as cur:
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def find_user(identifier: str) -> Optional[Dict[str, Any]]:
    """Look someone up by whichever of email or phone they typed."""
    ensure_schema()
    text = str(identifier or "").strip()
    if not text:
        return None
    email = normalise_email(text)
    phone = farmers.normalise_phone(text)
    with cursor() as cur:
        cur.execute("SELECT * FROM users WHERE email=?", (email,))
        row = cur.fetchone()
        if row is None and phone:
            cur.execute("SELECT * FROM users WHERE phone=?", (phone,))
            row = cur.fetchone()
    return dict(row) if row else None


def create_user(*, email: str, phone: str, password: str, full_name: str = "",
                role: str = "user", status: str = "pending") -> Dict[str, Any]:
    ensure_schema()
    if role not in ROLES:
        raise ValueError("role must be one of %s" % (ROLES,))
    record = {
        "user_id": "usr-" + uuid.uuid4().hex[:12],
        "email": normalise_email(email) or None,
        "phone": farmers.normalise_phone(phone) or None,
        "password_hash": hash_password(password),
        "full_name": farmers.clean_text(full_name) or None,
        "role": role,
        "status": status,
        "created_at": _iso(_now()),
        "updated_at": _iso(_now()),
    }
    with cursor() as cur:
        cur.execute(
            "INSERT INTO users (%s) VALUES (%s)"
            % (", ".join(record), ", ".join("?" * len(record))), list(record.values()))
    return get_user(record["user_id"]) or {}


def update_user(user_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    allowed = {"email", "phone", "password_hash", "full_name", "role", "status",
               "email_verified", "phone_verified", "failed_logins", "locked_until",
               "last_login"}
    clean = {k: v for k, v in fields.items() if k in allowed}
    if not clean:
        return get_user(user_id)
    sets = ", ".join("%s=?" % k for k in clean) + ", updated_at=?"
    with cursor() as cur:
        cur.execute("UPDATE users SET %s WHERE user_id=?" % sets,
                    list(clean.values()) + [_iso(_now()), user_id])
    return get_user(user_id)


def list_users(limit: int = 200) -> List[Dict[str, Any]]:
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT user_id, email, phone, full_name, role, status, "
                    "email_verified, phone_verified, last_login, created_at "
                    "FROM users ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]


def public_user(record: Dict[str, Any]) -> Dict[str, Any]:
    """What may be sent to a browser. The hash never leaves this module."""
    if not record:
        return {}
    return {k: v for k, v in record.items() if k != "password_hash"}


# ---------------------------------------------------------------- lockout
def lock_state(record: Dict[str, Any]) -> str:
    """"" when the account may try to sign in, otherwise why it may not."""
    if record.get("status") == "locked":
        return "This account has been locked. Contact the administrator."
    until = _parse(record.get("locked_until"))
    if until and until > _now():
        minutes = max(1, int((until - _now()).total_seconds() // 60) + 1)
        return ("Too many failed attempts. Try again in about %d minute(s)." % minutes)
    return ""


def note_failed_login(record: Dict[str, Any]) -> None:
    failed = int(record.get("failed_logins") or 0) + 1
    fields: Dict[str, Any] = {"failed_logins": failed}
    if failed >= MAX_FAILED_LOGINS:
        fields["locked_until"] = _iso(_now() + timedelta(minutes=LOCKOUT_MINUTES))
        fields["failed_logins"] = 0
    update_user(record["user_id"], **fields)


def note_successful_login(record: Dict[str, Any]) -> None:
    update_user(record["user_id"], failed_logins=0, locked_until=None,
                last_login=_iso(_now()))


# --------------------------------------------------------------- sessions
def start_session(user_id: str, user_agent: str = "") -> str:
    """Return the raw token. Only its digest is stored, so a database leak cannot be
    replayed as a login."""
    ensure_schema()
    token = secrets.token_urlsafe(32)
    with cursor() as cur:
        cur.execute("INSERT INTO sessions_auth (token_hash, user_id, created_at, "
                    "expires_at, user_agent) VALUES (?,?,?,?,?)",
                    (_digest(token), user_id, _iso(_now()),
                     _iso(_now() + timedelta(days=SESSION_DAYS)), user_agent[:200]))
    return token


def session_user(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT * FROM sessions_auth WHERE token_hash=?", (_digest(token),))
        row = cur.fetchone()
    if not row:
        return None
    expires = _parse(row["expires_at"])
    if expires is None or expires <= _now():
        end_session(token)
        return None
    record = get_user(row["user_id"])
    if not record or record.get("status") == "locked":
        return None
    return record


def end_session(token: str) -> None:
    if not token:
        return
    with cursor() as cur:
        cur.execute("DELETE FROM sessions_auth WHERE token_hash=?", (_digest(token),))


def end_all_sessions(user_id: str) -> int:
    """Used when a password changes: every other device is signed out."""
    with cursor() as cur:
        cur.execute("DELETE FROM sessions_auth WHERE user_id=?", (user_id,))
        return cur.rowcount


# -------------------------------------------------------------------- OTP
def issue_otp(user_id: str, purpose: str, channel: str, target: str) -> Dict[str, Any]:
    """Make a six-digit code, store only its digest, and hand the code back once.

    Returns ``{"code": ..., "wait": seconds}``; ``wait`` is non-zero when a code was
    sent moments ago, so a resend button cannot be used to flood someone's phone.
    """
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT created_at FROM otp_codes WHERE user_id=? AND purpose=? "
                    "AND consumed_at IS NULL ORDER BY id DESC LIMIT 1",
                    (user_id, purpose))
        row = cur.fetchone()
    if row:
        made = _parse(row["created_at"])
        if made:
            age = (_now() - made).total_seconds()
            if age < OTP_RESEND_SECONDS:
                return {"code": "", "wait": int(OTP_RESEND_SECONDS - age)}

    code = "%06d" % secrets.randbelow(1_000_000)
    with cursor() as cur:
        # One live code per purpose: an old one must not keep working.
        cur.execute("UPDATE otp_codes SET consumed_at=? WHERE user_id=? AND purpose=? "
                    "AND consumed_at IS NULL", (_iso(_now()), user_id, purpose))
        cur.execute("INSERT INTO otp_codes (user_id, purpose, channel, target, "
                    "code_hash, expires_at, created_at) VALUES (?,?,?,?,?,?,?)",
                    (user_id, purpose, channel, target, _digest(code),
                     _iso(_now() + timedelta(minutes=OTP_MINUTES)), _iso(_now())))
    return {"code": code, "wait": 0}


def check_otp(user_id: str, purpose: str, code: str) -> str:
    """"" when the code is good, otherwise the reason it is not."""
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT * FROM otp_codes WHERE user_id=? AND purpose=? AND "
                    "consumed_at IS NULL ORDER BY id DESC LIMIT 1", (user_id, purpose))
        row = cur.fetchone()
    if not row:
        return "No code is waiting. Ask for a new one."

    expires = _parse(row["expires_at"])
    if expires is None or expires <= _now():
        return "That code has expired. Ask for a new one."
    if int(row["attempts"]) >= OTP_MAX_ATTEMPTS:
        return "Too many wrong attempts. Ask for a new code."

    if not hmac.compare_digest(row["code_hash"], _digest(str(code or "").strip())):
        with cursor() as cur:
            cur.execute("UPDATE otp_codes SET attempts=attempts+1 WHERE id=?", (row["id"],))
        left = OTP_MAX_ATTEMPTS - int(row["attempts"]) - 1
        return ("That code is not right. %d attempt(s) left." % left if left > 0
                else "That code is not right. Ask for a new code.")

    with cursor() as cur:
        cur.execute("UPDATE otp_codes SET consumed_at=? WHERE id=?",
                    (_iso(_now()), row["id"]))
    return ""


def pending_otp(user_id: str, purpose: str) -> Optional[Dict[str, Any]]:
    with cursor() as cur:
        cur.execute("SELECT channel, target, expires_at FROM otp_codes WHERE user_id=? "
                    "AND purpose=? AND consumed_at IS NULL ORDER BY id DESC LIMIT 1",
                    (user_id, purpose))
        row = cur.fetchone()
    return dict(row) if row else None


def delete_user(user_id: str, purge_farm_data: bool = True) -> Dict[str, int]:
    """Erase an account. With ``purge_farm_data`` set, everything it owns goes too.

    The farm tables are keyed on the PHONE NUMBER, not on the user id, so deleting the
    row in ``users`` alone would leave the profile, the mapped fields, the remembered
    facts and every call transcript sitting in the database - and the next person to
    register that number would inherit all of it. That is the bug this function exists
    to not have.

    Returns a count per table, so the caller can report what actually went.
    """
    ensure_schema()
    record = get_user(user_id)
    if not record:
        return {}
    phone = farmers.normalise_phone(record.get("phone") or "")
    removed: Dict[str, int] = {}

    def wipe(cur, table: str, where: str, params) -> None:
        try:
            cur.execute("DELETE FROM %s WHERE %s" % (table, where), params)
        except Exception:                                         # noqa: BLE001
            return                                                # table not present
        if cur.rowcount and cur.rowcount > 0:
            removed[table] = removed.get(table, 0) + cur.rowcount

    with cursor() as cur:
        # the account itself
        wipe(cur, "otp_codes", "user_id=?", (user_id,))
        wipe(cur, "sessions_auth", "user_id=?", (user_id,))
        wipe(cur, "users", "user_id=?", (user_id,))

        if not (purge_farm_data and phone):
            return removed

        # everything keyed on the farmer's number
        session_ids = []
        try:
            rows = cur.execute("SELECT session_id FROM call_log WHERE phone=?",
                               (phone,)).fetchall()
            session_ids = [r["session_id"] for r in rows if r["session_id"]]
        except Exception:                                         # noqa: BLE001
            session_ids = []
        try:
            rows = cur.execute("SELECT session_id FROM sessions WHERE phone LIKE ?",
                               ("%" + phone,)).fetchall()
            session_ids += [r["session_id"] for r in rows]
        except Exception:                                         # noqa: BLE001
            pass

        wipe(cur, "farmer_facts", "phone=?", (phone,))
        wipe(cur, "call_log", "phone=?", (phone,))
        wipe(cur, "fields", "phone=?", (phone,))
        wipe(cur, "farmers", "phone=?", (phone,))
        wipe(cur, "calls", "phone LIKE ?", ("%" + phone,))

        for session_id in sorted(set(session_ids)):
            wipe(cur, "turns", "session_id=?", (session_id,))
            wipe(cur, "advisories", "session_id=?", (session_id,))
            wipe(cur, "sessions", "session_id=?", (session_id,))

    return removed


# ------------------------------------------------------------------ admin
def count_admins() -> int:
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
        return int(cur.fetchone()[0])


def ensure_admin(email: str, phone: str, password: str,
                 full_name: str = "Administrator") -> Optional[Dict[str, Any]]:
    """Create the first administrator if there is none.

    Called at start-up from the configured credentials. It will not overwrite an
    existing admin, and it does nothing at all when no credentials are configured -
    an account with a default password is worse than no account.
    """
    ensure_schema()
    if not (email and password):
        return None
    existing = find_user(email)
    if existing:
        changes: Dict[str, Any] = {}
        if existing.get("role") != "admin":
            changes.update(role="admin", status="active")

        # Keep the configured number on the account. Without this, editing
        # SAARTHI_ADMIN_PHONE changed the file and nothing else, because the account
        # was found by email and left exactly as it was.
        wanted = farmers.normalise_phone(phone)
        if wanted and wanted != (existing.get("phone") or ""):
            owner = find_user(wanted)
            if owner and owner["user_id"] != existing["user_id"]:
                log.warning("Not moving the admin to %s: another account owns it.", wanted)
            else:
                changes["phone"] = wanted

        return update_user(existing["user_id"], **changes) if changes else existing
    if count_admins():
        return None
    return create_user(email=email, phone=phone, password=password,
                       full_name=full_name, role="admin", status="active")
