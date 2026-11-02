"""SQLite persistence for sessions, conversation turns, sensor readings,
advisories and call records.

One file (data/storage/saarthi.db), WAL mode, guarded by a lock.
Deliberately dependency-free: no ORM and no database to install for a demo,
while the schema stays close to the PostgreSQL+JSONB design in the write-up.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import STORAGE_DIR, ensure_dirs

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    channel      TEXT NOT NULL DEFAULT 'dashboard',
    language     TEXT NOT NULL DEFAULT 'en',
    phone        TEXT,
    farm_json    TEXT NOT NULL DEFAULT '{}',
    memory_json  TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL,
    language     TEXT,
    intent       TEXT,
    meta_json    TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, id);
CREATE TABLE IF NOT EXISTS readings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id    TEXT NOT NULL,
    parcel_id    TEXT NOT NULL,
    mode         TEXT NOT NULL,
    provenance   TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    received_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_parcel ON readings(parcel_id, id DESC);
CREATE TABLE IF NOT EXISTS app_state (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS advisories (
    request_id   TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    question     TEXT NOT NULL,
    intent       TEXT NOT NULL,
    language     TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calls (
    call_id      TEXT PRIMARY KEY,
    call_sid     TEXT,
    direction    TEXT NOT NULL,
    phone        TEXT NOT NULL,
    language     TEXT NOT NULL,
    status       TEXT NOT NULL,
    mode         TEXT NOT NULL DEFAULT 'live',
    topic        TEXT,
    session_id   TEXT,
    detail_json  TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calls_created ON calls(created_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_missing_columns(table: str, columns: Dict[str, str]) -> List[str]:
    """Add columns a newer version expects to a table an older one created.

    ``CREATE TABLE IF NOT EXISTS`` does nothing to a table that already exists,
    so a column added later is simply absent on every database made before it -
    and the first write that mentions it fails with "table X has no column
    named Y", at runtime, in front of a farmer. A call-back booking did exactly
    that on a development database.

    SQLite can only add a column with a constant default, which is all these
    need. Called from each module's ``ensure_schema`` so the fix travels with
    the table it belongs to.
    """
    added: List[str] = []
    with cursor() as cur:
        cur.execute("PRAGMA table_info(%s)" % table)
        existing = {row["name"] for row in cur.fetchall()}
        if not existing:
            return added                      # no such table yet; SCHEMA makes it
        for name, declaration in columns.items():
            if name in existing:
                continue
            cur.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, declaration))
            added.append(name)
    return added


def folder() -> Path:
    """Where this process is keeping its files.

    The database decides, not the constant: a test opens a temporary database,
    and anything else that writes beside it - uploaded backdrops, cached audio -
    has to land in the same place or the test writes into the real storage
    directory and leaves it there.
    """
    return _folder


_folder: Path = STORAGE_DIR


def init(db_path: Optional[Path] = None, reset: bool = False) -> None:
    """Open (or reopen) the database. Tests pass a temporary path."""
    global _conn, _folder
    with _lock:
        ensure_dirs()
        path = Path(db_path) if db_path else STORAGE_DIR / "saarthi.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        _folder = path.parent
        if _conn is not None:
            _conn.close()
        if reset and path.exists():
            path.unlink()
        _conn = sqlite3.connect(str(path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        _conn.commit()


def is_open() -> bool:
    """True when a database is already open.

    Application startup uses this so it does not reopen the default file over a
    database someone else already opened - which is exactly what let API tests
    write into the real data/storage/saarthi.db and made their assertions depend
    on leftovers from the previous run.
    """
    return _conn is not None


def connection() -> sqlite3.Connection:
    if _conn is None:
        init()
    assert _conn is not None
    return _conn


@contextmanager
def cursor():
    with _lock:
        conn = connection()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        finally:
            cur.close()


# --------------------------------------------------------------- sessions
def upsert_session(session_id: str, **fields: Any) -> None:
    now = _now()
    with cursor() as cur:
        cur.execute("SELECT session_id FROM sessions WHERE session_id=?", (session_id,))
        if cur.fetchone() is None:
            cur.execute(
                "INSERT INTO sessions (session_id, channel, language, phone, farm_json,"
                " memory_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    fields.get("channel", "dashboard"),
                    fields.get("language", "en"),
                    fields.get("phone"),
                    json.dumps(fields.get("farm", {}), ensure_ascii=False),
                    json.dumps(fields.get("memory", {}), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            return
        sets: List[str] = []
        params: List[Any] = []
        for key in ("channel", "language", "phone"):
            if key in fields:
                sets.append(key + "=?")
                params.append(fields[key])
        for key, column in (("farm", "farm_json"), ("memory", "memory_json")):
            if key in fields:
                sets.append(column + "=?")
                params.append(json.dumps(fields[key], ensure_ascii=False))
        sets.append("updated_at=?")
        params.append(now)
        params.append(session_id)
        cur.execute("UPDATE sessions SET " + ", ".join(sets) + " WHERE session_id=?", params)


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    with cursor() as cur:
        cur.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,))
        row = cur.fetchone()
    if not row:
        return None
    data = dict(row)
    data["farm"] = json.loads(data.pop("farm_json") or "{}")
    data["memory"] = json.loads(data.pop("memory_json") or "{}")
    return data


def add_turn(session_id: str, role: str, content: str, language: str = "en",
             intent: str = "", meta: Optional[Dict[str, Any]] = None) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO turns (session_id, role, content, language, intent, meta_json,"
            " created_at) VALUES (?,?,?,?,?,?,?)",
            (session_id, role, content, language, intent,
             json.dumps(meta or {}, ensure_ascii=False), _now()),
        )


def get_turns(session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM (SELECT * FROM turns WHERE session_id=? ORDER BY id DESC LIMIT ?)"
            " ORDER BY id ASC",
            (session_id, limit),
        )
        rows = [dict(r) for r in cur.fetchall()]
    for row in rows:
        row["meta"] = json.loads(row.pop("meta_json") or "{}")
    return rows


# -------------------------------------------------------------- app state
def get_state(key: str, default: str = "") -> str:
    """A tiny key/value store for settings that must survive a restart.

    The sensor mode lived in a module-level dict, so every container restart silently
    put the farm back into simulation while the switch on screen still said real.
    """
    with cursor() as cur:
        cur.execute("SELECT value FROM app_state WHERE key=?", (key,))
        row = cur.fetchone()
    return row[0] if row else default


def set_state(key: str, value: str) -> str:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, _now()))
    return value


# --------------------------------------------------------------- readings
def add_reading(payload: Dict[str, Any]) -> int:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO readings (device_id, parcel_id, mode, provenance, payload_json,"
            " recorded_at, received_at) VALUES (?,?,?,?,?,?,?)",
            (
                payload.get("device_id", "unknown"),
                payload.get("parcel_id", "field-1"),
                payload.get("mode", "simulation"),
                payload.get("provenance", "simulated"),
                json.dumps(payload, ensure_ascii=False, default=str),
                str(payload.get("recorded_at") or _now()),
                _now(),
            ),
        )
        return int(cur.lastrowid)


def latest_reading(parcel_id: Optional[str] = None,
                   mode: Optional[str] = None) -> Optional[Dict[str, Any]]:
    query = "SELECT payload_json FROM readings"
    clauses: List[str] = []
    params: List[Any] = []
    if parcel_id:
        clauses.append("parcel_id=?")
        params.append(parcel_id)
    if mode:
        clauses.append("mode=?")
        params.append(mode)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC LIMIT 1"
    with cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    return json.loads(row[0]) if row else None


def recent_readings(parcel_id: Optional[str] = None, limit: int = 30) -> List[Dict[str, Any]]:
    query = "SELECT payload_json FROM readings"
    params: List[Any] = []
    if parcel_id:
        query += " WHERE parcel_id=?"
        params.append(parcel_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [json.loads(r[0]) for r in rows]


# ------------------------------------------------------------- advisories
def save_advisory(advisory: Dict[str, Any]) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO advisories (request_id, session_id, question, intent,"
            " language, payload_json, created_at) VALUES (?,?,?,?,?,?,?)",
            (
                advisory.get("request_id"),
                advisory.get("session_id"),
                advisory.get("question", ""),
                advisory.get("intent", ""),
                advisory.get("language", "en"),
                json.dumps(advisory, ensure_ascii=False, default=str),
                _now(),
            ),
        )


def recent_advisories(limit: int = 20, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    query = "SELECT payload_json FROM advisories"
    params: List[Any] = []
    if session_id:
        query += " WHERE session_id=?"
        params.append(session_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [json.loads(r[0]) for r in rows]


# ------------------------------------------------------------------ calls
def create_call(record: Dict[str, Any]) -> None:
    now = _now()
    with cursor() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO calls (call_id, call_sid, direction, phone, language,"
            " status, mode, topic, session_id, detail_json, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record["call_id"], record.get("call_sid"), record.get("direction", "outbound"),
                record.get("phone", ""), record.get("language", "en"),
                record.get("status", "queued"), record.get("mode", "live"),
                record.get("topic", ""), record.get("session_id"),
                json.dumps(record.get("detail", {}), ensure_ascii=False, default=str),
                record.get("created_at", now), now,
            ),
        )


def update_call(call_id: str, **fields: Any) -> None:
    sets: List[str] = []
    params: List[Any] = []
    for key in ("call_sid", "status", "language", "session_id", "phone", "mode"):
        if key in fields:
            sets.append(key + "=?")
            params.append(fields[key])
    if "detail" in fields:
        sets.append("detail_json=?")
        params.append(json.dumps(fields["detail"], ensure_ascii=False, default=str))
    if not sets:
        return
    sets.append("updated_at=?")
    params.append(_now())
    params.append(call_id)
    with cursor() as cur:
        cur.execute("UPDATE calls SET " + ", ".join(sets) + " WHERE call_id=?", params)


def get_call(call_id: str) -> Optional[Dict[str, Any]]:
    with cursor() as cur:
        cur.execute("SELECT * FROM calls WHERE call_id=? OR call_sid=?", (call_id, call_id))
        row = cur.fetchone()
    if not row:
        return None
    data = dict(row)
    data["detail"] = json.loads(data.pop("detail_json") or "{}")
    return data


def list_calls(limit: int = 50) -> List[Dict[str, Any]]:
    with cursor() as cur:
        cur.execute("SELECT * FROM calls ORDER BY created_at DESC LIMIT ?", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
    for row in rows:
        row["detail"] = json.loads(row.pop("detail_json") or "{}")
    return rows


def counts() -> Dict[str, int]:
    out: Dict[str, int] = {}
    with cursor() as cur:
        for table in ("sessions", "turns", "readings", "advisories", "calls"):
            cur.execute("SELECT COUNT(*) FROM " + table)
            out[table] = int(cur.fetchone()[0])
    return out
