"""Per-farmer memory: who they are, what they told us, and what was said when.

A phone number is the identity. Everything a farmer says on a call that is durable
(their name, village, crop, acreage, sowing date, a problem they reported) is stored
against that number, so the next call does not start from nothing.

Three tables, on top of the existing sessions/turns/calls:

    farmers       one row per phone - the profile
    farmer_facts  key/value memory with provenance and when it was last heard
    call_log      one row per call with its transcript and summary

Nothing here is inferred. Facts are written only from what the farmer actually said,
each carries the call it came from, and a fact can be corrected by a later call.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .storage import cursor

SCHEMA = """
CREATE TABLE IF NOT EXISTS farmers (
    phone         TEXT PRIMARY KEY,
    name          TEXT,
    village       TEXT,
    district      TEXT,
    state         TEXT,
    language      TEXT NOT NULL DEFAULT 'hi',
    land_acres    REAL,
    soil_type     TEXT,
    irrigation    TEXT,
    latitude      REAL,
    longitude     REAL,
    calls         INTEGER NOT NULL DEFAULT 0,
    last_called   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS farmer_facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    phone         TEXT NOT NULL,
    key           TEXT NOT NULL,
    value         TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'said_on_call',
    call_id       TEXT,
    first_heard   TEXT NOT NULL,
    last_heard    TEXT NOT NULL,
    UNIQUE(phone, key)
);
CREATE INDEX IF NOT EXISTS idx_facts_phone ON farmer_facts(phone);
CREATE TABLE IF NOT EXISTS call_log (
    call_id       TEXT PRIMARY KEY,
    phone         TEXT NOT NULL,
    session_id    TEXT,
    language      TEXT,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    turns         INTEGER NOT NULL DEFAULT 0,
    summary       TEXT,
    transcript    TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_calllog_phone ON call_log(phone, started_at DESC);
"""

PROFILE_FIELDS = ("name", "village", "district", "state", "language", "land_acres",
                  "soil_type", "irrigation", "latitude", "longitude")

# Facts worth carrying between calls. Anything else the model extracts is ignored,
# so the memory cannot fill up with chatter.
ALLOWED_FACT_KEYS = {
    "crop", "previous_crop", "next_crop", "sowing_date", "field_name", "field_size",
    "irrigation_method", "water_source", "soil_type", "problem", "pest_or_disease",
    "fertiliser_used", "last_irrigation", "harvest_plan", "market", "livestock",
    "equipment", "preferred_time_to_call", "language",
}

# Most of these answers are a word or two. On a real call the extractor stored a whole
# sentence ("there is far too little water in the soil") as BOTH soil_type and problem,
# which is useless to recall and misleading to read back to the farmer. A per-key length
# cap is the cheap guard: a long value means the model summarised instead of extracting.
FACT_MAX_LENGTH = {
    "crop": 40, "previous_crop": 40, "next_crop": 40, "soil_type": 40,
    "irrigation_method": 40, "water_source": 40, "field_name": 40, "field_size": 40,
    "sowing_date": 40, "market": 60, "language": 20, "preferred_time_to_call": 40,
    "livestock": 60, "equipment": 60, "fertiliser_used": 80, "last_irrigation": 40,
    "pest_or_disease": 80, "harvest_plan": 120, "problem": 160,
}
DEFAULT_FACT_MAX = 120


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)


def normalise_phone(phone: str) -> str:
    """Digits only, last 10 kept: +91 98765 43210, 09876543210 and 9876543210 are
    the same farmer, and Twilio is not consistent about the prefix."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


# ---------------------------------------------------------------- profile
def get_farmer(phone: str) -> Optional[Dict[str, Any]]:
    key = normalise_phone(phone)
    with cursor() as cur:
        cur.execute("SELECT * FROM farmers WHERE phone=?", (key,))
        row = cur.fetchone()
    return dict(row) if row else None


def upsert_farmer(phone: str, **fields: Any) -> Dict[str, Any]:
    key = normalise_phone(phone)
    now = _now()
    clean = {k: v for k, v in fields.items() if k in PROFILE_FIELDS and v not in (None, "")}
    with cursor() as cur:
        cur.execute("SELECT phone FROM farmers WHERE phone=?", (key,))
        if cur.fetchone() is None:
            columns = ["phone", "created_at", "updated_at"] + list(clean)
            values = [key, now, now] + [clean[c] for c in clean]
            cur.execute("INSERT INTO farmers (%s) VALUES (%s)"
                        % (", ".join(columns), ", ".join("?" * len(columns))), values)
        elif clean:
            sets = ", ".join("%s=?" % c for c in clean) + ", updated_at=?"
            cur.execute("UPDATE farmers SET %s WHERE phone=?" % sets,
                        list(clean.values()) + [now, key])
    return get_farmer(key) or {}


def record_call_started(phone: str, call_id: str, session_id: str = "",
                        language: str = "hi") -> None:
    key = normalise_phone(phone)
    upsert_farmer(key, language=language)
    now = _now()
    with cursor() as cur:
        cur.execute("UPDATE farmers SET calls = calls + 1, last_called=?, updated_at=? "
                    "WHERE phone=?", (now, now, key))
        cur.execute("INSERT OR IGNORE INTO call_log (call_id, phone, session_id, language, "
                    "started_at) VALUES (?,?,?,?,?)", (call_id, key, session_id, language, now))


# ------------------------------------------------------------------ facts
def clean_text(value: str) -> str:
    """Drop lone surrogates and control characters before anything is stored.

    A real call produced a truncated UTF-8 sequence from the speech transcript. Lone
    surrogates break JSON round-trips and render as tofu, so they are removed rather
    than stored - and they also crash any later write of the file they land in.
    """
    text = str(value or "")
    text = text.encode("utf-8", "replace").decode("utf-8", "replace").replace("�", "")
    return "".join(ch for ch in text if ch.isprintable() or ch.isspace()).strip()


def remember(phone: str, key: str, value: str, *, call_id: str = "",
             source: str = "said_on_call") -> bool:
    """Store or refresh one durable fact. Returns False for keys we do not keep."""
    fact_key = str(key or "").strip().lower().replace(" ", "_")
    text = clean_text(value)
    if fact_key not in ALLOWED_FACT_KEYS or not text:
        return False
    if len(text) > FACT_MAX_LENGTH.get(fact_key, DEFAULT_FACT_MAX):
        return False
    phone_key = normalise_phone(phone)
    now = _now()
    with cursor() as cur:
        cur.execute("SELECT id, value FROM farmer_facts WHERE phone=? AND key=?",
                    (phone_key, fact_key))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO farmer_facts (phone, key, value, source, call_id, "
                        "first_heard, last_heard) VALUES (?,?,?,?,?,?,?)",
                        (phone_key, fact_key, text, source, call_id, now, now))
        else:
            # A later call corrects an earlier one - the farmer is the authority.
            cur.execute("UPDATE farmer_facts SET value=?, source=?, call_id=?, last_heard=? "
                        "WHERE id=?", (text, source, call_id, now, row["id"]))
    return True


def recall(phone: str, limit: int = 25) -> List[Dict[str, Any]]:
    with cursor() as cur:
        cur.execute("SELECT key, value, source, first_heard, last_heard FROM farmer_facts "
                    "WHERE phone=? ORDER BY last_heard DESC LIMIT ?",
                    (normalise_phone(phone), limit))
        return [dict(r) for r in cur.fetchall()]


def forget(phone: str, key: str) -> bool:
    with cursor() as cur:
        cur.execute("DELETE FROM farmer_facts WHERE phone=? AND key=?",
                    (normalise_phone(phone), str(key).strip().lower()))
        return cur.rowcount > 0


# -------------------------------------------------------------- call log
def append_turn(call_id: str, phone: str, farmer_said: str, saarthi_said: str,
                meta: Optional[Dict[str, Any]] = None) -> None:
    key = normalise_phone(phone)
    with cursor() as cur:
        cur.execute("SELECT transcript, turns FROM call_log WHERE call_id=?", (call_id,))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO call_log (call_id, phone, started_at, transcript) "
                        "VALUES (?,?,?,?)", (call_id, key, _now(), "[]"))
            transcript, turns = [], 0
        else:
            transcript = json.loads(row["transcript"] or "[]")
            turns = int(row["turns"])
        transcript.append({"at": _now(), "farmer": farmer_said, "saarthi": saarthi_said,
                           "meta": meta or {}})
        cur.execute("UPDATE call_log SET transcript=?, turns=? WHERE call_id=?",
                    (json.dumps(transcript, ensure_ascii=False), turns + 1, call_id))


def close_call(call_id: str, summary: str = "") -> None:
    with cursor() as cur:
        cur.execute("UPDATE call_log SET ended_at=?, summary=? WHERE call_id=?",
                    (_now(), summary, call_id))


def call_history(phone: str, limit: int = 5) -> List[Dict[str, Any]]:
    with cursor() as cur:
        cur.execute("SELECT call_id, started_at, ended_at, turns, summary, transcript "
                    "FROM call_log WHERE phone=? ORDER BY started_at DESC LIMIT ?",
                    (normalise_phone(phone), limit))
        rows = [dict(r) for r in cur.fetchall()]
    for row in rows:
        row["transcript"] = json.loads(row.get("transcript") or "[]")
    return rows


def last_transcript(call_id: str) -> List[Dict[str, Any]]:
    with cursor() as cur:
        cur.execute("SELECT transcript FROM call_log WHERE call_id=?", (call_id,))
        row = cur.fetchone()
    return json.loads(row["transcript"]) if row else []


# --------------------------------------------------------------- summary
def mapped_fields(phone: str) -> List[Dict[str, Any]]:
    """The plots this farmer drew on the dashboard map.

    Read with plain SQL rather than through ``core.profile``: that module imports this
    one, and the caller here is the voice agent, which must not depend on the import
    order of the dashboard's modules. The table may not exist yet on a database that
    has never served the dashboard, which is not an error.
    """
    key = normalise_phone(phone) or str(phone or "").strip()
    try:
        with cursor() as cur:
            cur.execute(
                "SELECT name, crop, area_acres, soil_type, irrigation, sowing_date "
                "FROM fields WHERE phone=? ORDER BY created_at", (key,))
            return [dict(r) for r in cur.fetchall()]
    except Exception:                                             # noqa: BLE001
        return []


def memory_block(phone: str) -> Dict[str, Any]:
    """Everything the agent should know before it opens its mouth.

    This is the whole account, not a summary of it: the profile the farmer typed on
    the dashboard, the plots they mapped, what they have said on past calls, and how
    those calls ended. A caller whose number is on the dashboard must not be greeted
    as a stranger.
    """
    ensure_schema()          # a caller may reach this before anything else has run
    key = normalise_phone(phone) or str(phone or "").strip()
    profile = get_farmer(key) or {}
    facts = recall(key)
    history = call_history(key, limit=3)
    fields = mapped_fields(key)
    return {
        "known": bool(profile) or bool(fields),
        "phone": key,
        "profile": profile,
        "fields": fields,
        "facts": facts,
        "previous_calls": [
            {"at": h["started_at"], "turns": h["turns"], "summary": h["summary"] or "",
             "last_exchange": (h["transcript"][-1] if h["transcript"] else None)}
            for h in history
        ],
        "returning": int(profile.get("calls") or 0) > 1,
    }
