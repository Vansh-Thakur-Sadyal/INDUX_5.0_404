"""Instructions waiting for a screen to carry them out.

A farmer on a phone call says "mandi bhav dikhao". The server cannot open a
page; it can only write down that one should be opened, and let whichever
surface is watching - the dashboard in a browser, the app on the phone - pick it
up. That is all this is: a short queue per account, delivered once.

Three rules it follows, each from a way this goes wrong:

**Delivered once.** A command is marked the moment it is handed over, so two
open tabs do not both navigate and the farmer does not watch the page jump twice.

**It expires.** A command nobody collected within a couple of minutes is dropped
rather than fired at whoever opens the dashboard next. "Open the sensor page"
arriving an hour after the call is not help, it is a haunted browser.

**It is scoped to the account.** The queue is keyed on the farmer's own number,
which is the same key the rest of the system uses, so a command can only ever
reach the person who spoke it.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .storage import cursor

# Long enough to survive a page being opened after the call ends, short enough
# that nothing surprising happens later.
TTL_SECONDS = 150

SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_commands (
    command_id   TEXT PRIMARY KEY,
    phone        TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'call',
    spoken       TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    delivered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_voice_commands_phone
    ON voice_commands(phone, delivered_at, created_at);
"""


def ensure_schema() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def queue(phone: str, payload: Dict[str, Any], *, source: str = "call",
          spoken: str = "") -> Dict[str, Any]:
    """Write down something for a screen to do."""
    ensure_schema()
    now = _now()
    record = {
        "command_id": "cmd-" + uuid.uuid4().hex[:12],
        "phone": str(phone or ""),
        "payload_json": json.dumps(payload, ensure_ascii=False),
        "source": source,
        "spoken": (spoken or "")[:300],
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=TTL_SECONDS)).isoformat(),
        "delivered_at": None,
    }
    with cursor() as cur:
        cur.execute(
            "INSERT INTO voice_commands (command_id, phone, payload_json, source,"
            " spoken, created_at, expires_at, delivered_at)"
            " VALUES (:command_id, :phone, :payload_json, :source, :spoken,"
            " :created_at, :expires_at, :delivered_at)", record)
    return {"command_id": record["command_id"], "payload": payload}


def take(phone: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Everything waiting for this account, marked delivered as it is handed over."""
    ensure_schema()
    now = _now()
    with cursor() as cur:
        cur.execute(
            "SELECT command_id, payload_json, source, spoken, created_at"
            " FROM voice_commands WHERE phone = ? AND delivered_at IS NULL"
            " AND expires_at > ? ORDER BY created_at ASC LIMIT ?",
            (str(phone or ""), now.isoformat(), int(limit)))
        rows = [dict(r) for r in cur.fetchall()]
        if rows:
            cur.executemany(
                "UPDATE voice_commands SET delivered_at = ? WHERE command_id = ?",
                [(now.isoformat(), r["command_id"]) for r in rows])
        # Housekeeping, here rather than in a sweeper: the table is tiny and this
        # is the only place that reads it.
        cur.execute("DELETE FROM voice_commands WHERE expires_at < ?",
                    ((now - timedelta(hours=1)).isoformat(),))

    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        out.append({"command_id": row["command_id"], "source": row["source"],
                    "spoken": row["spoken"], "at": row["created_at"], **payload})
    return out


def pending_count(phone: str) -> int:
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT COUNT(*) c FROM voice_commands WHERE phone = ?"
                    " AND delivered_at IS NULL AND expires_at > ?",
                    (str(phone or ""), _now().isoformat()))
        row = cur.fetchone()
    return int(row["c"]) if row else 0


def clear(phone: str) -> int:
    """Drop whatever is waiting. Used when the farmer says stop."""
    ensure_schema()
    with cursor() as cur:
        cur.execute("UPDATE voice_commands SET delivered_at = ? WHERE phone = ?"
                    " AND delivered_at IS NULL", (_now().isoformat(), str(phone or "")))
        return cur.rowcount
