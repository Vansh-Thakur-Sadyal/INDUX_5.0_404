"""The farmer's own record: who they are, where they farm, and their plots.

``farmers`` already held the handful of facts a phone call could establish. This
module widens that into a profile the farmer fills in themselves on the dashboard,
and adds the plots they draw on the map.

Two ideas are kept apart on purpose:

    profile fields   typed in by the farmer, authoritative, never overwritten by a call
    remembered facts heard on a call, corrigible, stored in farmer_facts

A call may still fill a blank, but it does not get to contradict what the farmer
typed. That is why "Bijai" (Hindi for sowing) once replaced the name "Ramesh Kumar".

Plots live in their own table rather than as facts: a plot has a shape on the map,
and several of them belong to one farmer, which key/value memory cannot express.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import farmers
from .storage import cursor

SCHEMA = """
CREATE TABLE IF NOT EXISTS fields (
    field_id      TEXT PRIMARY KEY,
    phone         TEXT NOT NULL,
    name          TEXT NOT NULL,
    crop          TEXT,
    area_acres    REAL,
    soil_type     TEXT,
    irrigation    TEXT,
    sowing_date   TEXT,
    centre_lat    REAL,
    centre_lon    REAL,
    bounds_json   TEXT NOT NULL DEFAULT '{}',
    notes         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fields_phone ON fields(phone, created_at);
"""

# Columns added to ``farmers`` after it first shipped. SQLite has no "ADD COLUMN IF
# NOT EXISTS", so each is attempted and the duplicate-column error is the signal that
# it is already there.
EXTRA_FARMER_COLUMNS = {
    "full_name": "TEXT",
    "age": "INTEGER",
    "gender": "TEXT",
    "alt_phone": "TEXT",
    "email": "TEXT",
    "tehsil": "TEXT",
    "pincode": "TEXT",
    "landmark": "TEXT",
    "farming_since": "INTEGER",
    "farmer_type": "TEXT",          # smallholder / marginal / commercial, free text
    "call_delay_minutes": "INTEGER",
    "updated_by": "TEXT",           # 'farmer' when typed in, 'call' when heard
}

# Everything the dashboard may write. Deliberately a superset of farmers.PROFILE_FIELDS:
# the dashboard is the farmer typing, which is allowed to set more than a call is.
EDITABLE = (
    "name", "full_name", "age", "gender", "alt_phone", "email",
    "village", "tehsil", "district", "state", "pincode", "landmark",
    "language", "land_acres", "soil_type", "irrigation", "latitude", "longitude",
    "farming_since", "farmer_type", "call_delay_minutes",
)

NUMERIC = {"age": int, "land_acres": float, "latitude": float, "longitude": float,
           "farming_since": int, "call_delay_minutes": int}

# Columns declared NOT NULL. A blank from the form means "I did not fill this in",
# which for these has to mean "leave it alone" - writing NULL raises IntegrityError
# and loses the whole save, including the fields that were filled in.
NEVER_NULL = {"language"}

FIELD_EDITABLE = ("name", "crop", "area_acres", "soil_type", "irrigation",
                  "sowing_date", "centre_lat", "centre_lon", "notes")
FIELD_NUMERIC = {"area_acres": float, "centre_lat": float, "centre_lon": float}


def _key(phone: str) -> str:
    """The row key for a farmer.

    ``farmers.normalise_phone`` keeps the last ten DIGITS, so a non-numeric identity -
    the dashboard's placeholder before a number is entered - normalises to the empty
    string. Everyone without a number would then share one row, and one farmer's plots
    would show up under another's. Keep the text as the key when there are no digits.
    """
    digits = farmers.normalise_phone(phone)
    return digits or (str(phone or "").strip() or "unknown")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema() -> None:
    farmers.ensure_schema()
    with cursor() as cur:
        cur.executescript(SCHEMA)
        cur.execute("PRAGMA table_info(farmers)")
        have = {row[1] for row in cur.fetchall()}
        for column, sql_type in EXTRA_FARMER_COLUMNS.items():
            if column not in have:
                cur.execute("ALTER TABLE farmers ADD COLUMN %s %s" % (column, sql_type))


def _coerce(values: Dict[str, Any], allowed, numeric) -> Dict[str, Any]:
    """Keep the allowed keys, drop blanks, and make numbers actual numbers.

    A number arriving as "2.5" from a form field must not be stored as text, or the
    agents comparing acreage would compare strings.
    """
    clean: Dict[str, Any] = {}
    for key, value in (values or {}).items():
        if key not in allowed:
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            if key not in NEVER_NULL:
                clean[key] = None        # an explicit blank clears the field
            continue
        if key in numeric:
            try:
                clean[key] = numeric[key](str(value).strip())
            except (TypeError, ValueError):
                continue                 # unparseable number: leave the old value alone
        else:
            clean[key] = farmers.clean_text(value)
    return clean


# ------------------------------------------------------------------ profile
def get_profile(phone: str) -> Dict[str, Any]:
    ensure_schema()
    key = _key(phone)
    record = farmers.get_farmer(key) or {}
    plots = list_fields(key)
    return {
        "phone": key,
        "profile": record,
        "fields": plots,
        "field_count": len(plots),
        "total_acres": round(sum(float(f.get("area_acres") or 0) for f in plots), 2),
        "remembered_facts": farmers.recall(key),
        "calls": farmers.call_history(key, limit=5),
        "complete": _completeness(record, plots),
    }


def _completeness(record: Dict[str, Any], plots: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What is still missing, so the dashboard can say so instead of looking finished."""
    wanted = ("name", "village", "district", "state", "land_acres", "soil_type")
    missing = [key for key in wanted if not record.get(key)]
    if not plots:
        missing.append("at least one field on the map")
    return {"missing": missing, "percent": round(100 * (1 - len(missing) / (len(wanted) + 1)))}


def save_profile(phone: str, values: Dict[str, Any]) -> Dict[str, Any]:
    """Write what the farmer typed. This outranks anything heard on a call."""
    ensure_schema()
    key = _key(phone)
    clean = _coerce(values, EDITABLE, NUMERIC)
    if not clean:
        return get_profile(key)
    clean["updated_by"] = "farmer"
    now = _now()
    with cursor() as cur:
        cur.execute("SELECT phone FROM farmers WHERE phone=?", (key,))
        if cur.fetchone() is None:
            columns = ["phone", "created_at", "updated_at"] + list(clean)
            cur.execute("INSERT INTO farmers (%s) VALUES (%s)"
                        % (", ".join(columns), ", ".join("?" * len(columns))),
                        [key, now, now] + list(clean.values()))
        else:
            sets = ", ".join("%s=?" % c for c in clean) + ", updated_at=?"
            cur.execute("UPDATE farmers SET %s WHERE phone=?" % sets,
                        list(clean.values()) + [now, key])
    return get_profile(key)


# ------------------------------------------------------------------- fields
def list_fields(phone: str) -> List[Dict[str, Any]]:
    ensure_schema()
    key = _key(phone)
    with cursor() as cur:
        cur.execute("SELECT * FROM fields WHERE phone=? ORDER BY created_at", (key,))
        rows = [dict(r) for r in cur.fetchall()]
    for row in rows:
        try:
            row["bounds"] = json.loads(row.pop("bounds_json") or "{}")
        except ValueError:
            row["bounds"] = {}
    return rows


def get_field(field_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT * FROM fields WHERE field_id=?", (field_id,))
        row = cur.fetchone()
    if not row:
        return None
    record = dict(row)
    try:
        record["bounds"] = json.loads(record.pop("bounds_json") or "{}")
    except ValueError:
        record["bounds"] = {}
    return record


def save_field(phone: str, values: Dict[str, Any],
               field_id: str = "") -> Dict[str, Any]:
    """Create or update one plot. The map supplies the bounds, the form the rest."""
    ensure_schema()
    key = _key(phone)
    clean = _coerce(values, FIELD_EDITABLE, FIELD_NUMERIC)
    bounds = values.get("bounds") or {}
    if not clean.get("name"):
        clean["name"] = "Field %d" % (len(list_fields(key)) + 1)
    now = _now()

    if field_id and get_field(field_id):
        sets = ", ".join("%s=?" % c for c in clean) + ", bounds_json=?, updated_at=?"
        with cursor() as cur:
            cur.execute("UPDATE fields SET %s WHERE field_id=?" % sets,
                        list(clean.values()) + [json.dumps(bounds), now, field_id])
        return get_field(field_id) or {}

    new_id = field_id or ("field-" + uuid.uuid4().hex[:10])
    columns = ["field_id", "phone", "bounds_json", "created_at", "updated_at"] + list(clean)
    values_sql = [new_id, key, json.dumps(bounds), now, now] + list(clean.values())
    with cursor() as cur:
        cur.execute("INSERT INTO fields (%s) VALUES (%s)"
                    % (", ".join(columns), ", ".join("?" * len(columns))), values_sql)
    return get_field(new_id) or {}


def delete_field(field_id: str) -> bool:
    ensure_schema()
    with cursor() as cur:
        cur.execute("DELETE FROM fields WHERE field_id=?", (field_id,))
        return cur.rowcount > 0


# ------------------------------------------------------------------- memory
def erase_memory(phone: str, keep_name: bool = True) -> Dict[str, Any]:
    """Forget everything the conversational model knows about this farmer.

    What goes: every remembered fact, every call transcript and summary, and the
    stored sessions and turns behind them. What stays, when ``keep_name`` is set, is
    the name - the farmer asked to be forgotten, not to become a stranger.

    Typed profile details are the farmer's own data and are NOT touched here; the
    dashboard offers clearing those separately, so "erase what you remember about me"
    cannot silently wipe the address they entered.
    """
    ensure_schema()
    key = _key(phone)
    record = farmers.get_farmer(key) or {}
    name = record.get("name") if keep_name else None

    removed = {"facts": 0, "calls": 0, "turns": 0, "sessions": 0}
    with cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM farmer_facts WHERE phone=?", (key,))
        removed["facts"] = int(cur.fetchone()[0])
        cur.execute("DELETE FROM farmer_facts WHERE phone=?", (key,))

        cur.execute("SELECT call_id, session_id FROM call_log WHERE phone=?", (key,))
        rows = cur.fetchall()
        removed["calls"] = len(rows)
        session_ids = [r["session_id"] for r in rows if r["session_id"]]
        cur.execute("DELETE FROM call_log WHERE phone=?", (key,))

        # The sessions behind those calls hold the same conversation a second time.
        cur.execute("SELECT session_id FROM sessions WHERE phone LIKE ?", ("%" + key,))
        session_ids += [r["session_id"] for r in cur.fetchall()]
        for session_id in set(session_ids):
            cur.execute("SELECT COUNT(*) FROM turns WHERE session_id=?", (session_id,))
            removed["turns"] += int(cur.fetchone()[0])
            cur.execute("DELETE FROM turns WHERE session_id=?", (session_id,))
            cur.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
            removed["sessions"] += 1

        cur.execute("UPDATE farmers SET calls=0, last_called=NULL, updated_at=? "
                    "WHERE phone=?", (_now(), key))
        if keep_name:
            cur.execute("UPDATE farmers SET name=? WHERE phone=?", (name, key))
        else:
            cur.execute("UPDATE farmers SET name=NULL WHERE phone=?", (key,))

    return {"phone": key, "kept_name": name if keep_name else None, "removed": removed}
