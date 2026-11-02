"""What the farm spent and earned, per field and per season.

The Farm Finance page works out a break-even from figures the farmer types in once and
then forgets. That is useful on the day and useless a month later, because nobody
remembers what the urea cost in October. So entries are kept: an amount, a category, a
field, a crop and a season, with a note.

Two rules it inherits from the rest of the project:

  * nothing is estimated. A season with no entries reports zero spend and says the
    record is empty, rather than filling in a district average and calling it theirs.
  * every entry carries where it came from - typed on the dashboard, or spoken on the
    phone - because a figure a farmer dictated while standing in a field is worth
    knowing about when the totals look wrong.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .storage import cursor

# What a rupee was spent on. Deliberately short: a long list is one nobody picks from
# on a phone, and "other" with a note beats a wrong category.
CATEGORIES = ("seed", "fertiliser", "pesticide", "irrigation", "labour",
              "machinery", "transport", "storage", "other")

KINDS = ("expense", "income")
SOURCES = ("dashboard", "call", "import")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    entry_id    TEXT PRIMARY KEY,
    phone       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    category    TEXT NOT NULL,
    amount_inr  REAL NOT NULL,
    field_id    TEXT,
    crop        TEXT,
    season      TEXT,
    note        TEXT,
    source      TEXT NOT NULL,
    spent_on    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_phone ON ledger(phone, spent_on DESC);
"""


def ensure_schema() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _season_for(when: str) -> str:
    """The Indian crop season a date falls in.

    Rabi is sown around October and harvested in spring; kharif follows the monsoon.
    The boundaries are conventional rather than exact, and the season is stored so a
    later change to this function cannot silently re-file old entries.
    """
    try:
        month = datetime.fromisoformat(str(when).replace("Z", "+00:00")).month
    except (TypeError, ValueError):
        month = datetime.now(timezone.utc).month
    if month in (6, 7, 8, 9):
        return "kharif"
    if month in (10, 11, 12, 1, 2, 3):
        return "rabi"
    return "zaid"


def add_entry(phone: str, *, kind: str = "expense", category: str = "other",
              amount_inr: float = 0.0, field_id: str = "", crop: str = "",
              note: str = "", source: str = "dashboard",
              spent_on: str = "", season: str = "") -> Dict[str, Any]:
    """Record one spend or one sale. Returns the stored row."""
    ensure_schema()
    kind = kind if kind in KINDS else "expense"
    category = category.lower().strip() if category.lower().strip() in CATEGORIES else "other"
    source = source if source in SOURCES else "dashboard"
    when = str(spent_on or "").strip() or _now()
    entry_id = "led-" + uuid.uuid4().hex[:10]

    row = {
        "entry_id": entry_id,
        "phone": str(phone or ""),
        "kind": kind,
        "category": category,
        "amount_inr": round(float(amount_inr or 0), 2),
        "field_id": str(field_id or "") or None,
        "crop": str(crop or "") or None,
        "season": str(season or "") or _season_for(when),
        "note": str(note or "")[:300] or None,
        "source": source,
        "spent_on": when,
        "created_at": _now(),
    }
    with cursor() as cur:
        cur.execute(
            "INSERT INTO ledger (entry_id, phone, kind, category, amount_inr, field_id,"
            " crop, season, note, source, spent_on, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            tuple(row[k] for k in ("entry_id", "phone", "kind", "category", "amount_inr",
                                   "field_id", "crop", "season", "note", "source",
                                   "spent_on", "created_at")))
    return row


def list_entries(phone: str, *, field_id: str = "", season: str = "",
                 limit: int = 200) -> List[Dict[str, Any]]:
    ensure_schema()
    query = "SELECT * FROM ledger WHERE phone=?"
    params: List[Any] = [str(phone or "")]
    if field_id:
        query += " AND field_id=?"
        params.append(field_id)
    if season:
        query += " AND season=?"
        params.append(season)
    query += " ORDER BY spent_on DESC LIMIT ?"
    params.append(int(limit))
    with cursor() as cur:
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def delete_entry(phone: str, entry_id: str) -> bool:
    """Remove one entry. Scoped by phone so an id alone is not enough."""
    ensure_schema()
    with cursor() as cur:
        cur.execute("DELETE FROM ledger WHERE entry_id=? AND phone=?",
                    (entry_id, str(phone or "")))
        return cur.rowcount > 0


def summary(phone: str, *, field_id: str = "", season: str = "") -> Dict[str, Any]:
    """Totals, with the categories broken out.

    An empty record reports zeros and says so. It does not reach for a district
    average, because that number is not this farm's and would be read as if it were.
    """
    entries = list_entries(phone, field_id=field_id, season=season, limit=2000)
    spent = sum(e["amount_inr"] for e in entries if e["kind"] == "expense")
    earned = sum(e["amount_inr"] for e in entries if e["kind"] == "income")

    by_category: Dict[str, float] = {}
    for e in entries:
        if e["kind"] != "expense":
            continue
        by_category[e["category"]] = round(
            by_category.get(e["category"], 0) + e["amount_inr"], 2)

    by_month: Dict[str, float] = {}
    for e in entries:
        if e["kind"] != "expense":
            continue
        month = str(e["spent_on"])[:7]
        by_month[month] = round(by_month.get(month, 0) + e["amount_inr"], 2)

    by_field: Dict[str, Dict[str, float]] = {}
    for e in entries:
        key = e.get("field_id") or "unassigned"
        bucket = by_field.setdefault(key, {"spent": 0.0, "earned": 0.0})
        bucket["spent" if e["kind"] == "expense" else "earned"] += e["amount_inr"]
    for bucket in by_field.values():
        bucket["spent"] = round(bucket["spent"], 2)
        bucket["earned"] = round(bucket["earned"], 2)
        bucket["net"] = round(bucket["earned"] - bucket["spent"], 2)

    biggest = max(by_category.items(), key=lambda kv: kv[1])[0] if by_category else None
    return {
        "entries": len(entries),
        "spent_inr": round(spent, 2),
        "earned_inr": round(earned, 2),
        "net_inr": round(earned - spent, 2),
        "by_category": by_category,
        "by_month": by_month,
        "by_field": by_field,
        "biggest_cost": biggest,
        "empty": not entries,
        "note": ("Nothing recorded yet for this selection."
                 if not entries else
                 "Every figure is one you entered. Nothing is estimated, and family "
                 "labour is not costed unless you added it."),
    }


def spoken_summary(phone: str, *, field_id: str = "", season: str = "") -> str:
    """One or two sentences for the phone line.

    A spoken turn has seconds, so this gives the total, the biggest category and the
    net - and nothing else.
    """
    data = summary(phone, field_id=field_id, season=season)
    if data["empty"]:
        return "Nothing is recorded yet. Tell me an amount and what it was for."
    parts = ["You have spent %d rupees so far" % round(data["spent_inr"])]
    if data["biggest_cost"]:
        parts.append("most of it on %s" % data["biggest_cost"])
    if data["earned_inr"]:
        parts.append("and earned %d" % round(data["earned_inr"]))
        parts.append("so you are %s by %d"
                     % ("ahead" if data["net_inr"] >= 0 else "behind",
                        abs(round(data["net_inr"]))))
    return ", ".join(parts) + "."
