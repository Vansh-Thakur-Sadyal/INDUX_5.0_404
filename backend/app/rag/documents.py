"""The record of what is in the knowledge base, who put it there, and what happened.

The vector index answers "what is similar to this question". It cannot answer the
questions a person running a knowledge base actually asks:

    who uploaded this, and when?
    is it still being processed, or did it fail?
    which of these documents is the chatbot allowed to use, and which the phone line?
    what did the retrieval chain actually do with my question yesterday?

So the index keeps vectors and this keeps the facts about them. Four tables:

``kb_documents``  one row per source document, with its owner and its state
``kb_jobs``       one row per ingestion run, with the stage it is on right now
``kb_events``     an append-only trail: uploaded, parsed, indexed, disabled, deleted
``kb_queries``    one row per retrieval run, with the per-stage timings

Nothing here is ever inferred. A document whose parse failed says so and reports the
error; it does not quietly show zero chunks and let someone assume it worked.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .store import store
from ..core.storage import cursor

# Which bot may retrieve a document. A grid of two bots is overkill; three values
# cover every case anyone has asked for and keep the picker to one control.
AUDIENCES = ("both", "chat", "call")

# The life of a document, in the order it happens. "disabled" is reachable from
# "indexed" and is the only way to take something out of retrieval without
# destroying it - which matters, because most "delete this" requests are really
# "stop answering from this".
STATES = ("queued", "parsing", "chunking", "embedding", "indexing",
          "indexed", "failed", "disabled")

# The stages of the chain, in the order the diagram draws them. Named once, here,
# so the console, the API and the trace cannot disagree about what ran.
INGEST_STAGES = ("load", "parse", "clean", "chunk", "embed", "index")
QUERY_STAGES = ("rewrite", "guardrails", "embed", "retrieve", "rerank",
                "generate", "verify")

SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_documents (
    doc_id        TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    source_kind   TEXT NOT NULL,
    source_name   TEXT NOT NULL,
    media_type    TEXT,
    bytes         INTEGER NOT NULL DEFAULT 0,
    sha256        TEXT,
    audience      TEXT NOT NULL DEFAULT 'both',
    topics        TEXT NOT NULL DEFAULT '',
    crops         TEXT NOT NULL DEFAULT '',
    language      TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    state         TEXT NOT NULL DEFAULT 'queued',
    chunks        INTEGER NOT NULL DEFAULT 0,
    characters    INTEGER NOT NULL DEFAULT 0,
    provider      TEXT NOT NULL DEFAULT '',
    version       INTEGER NOT NULL DEFAULT 1,
    owner_id      TEXT NOT NULL DEFAULT '',
    owner_name    TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    body          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_docs_state ON kb_documents(state, updated_at DESC);

CREATE TABLE IF NOT EXISTS kb_jobs (
    job_id        TEXT PRIMARY KEY,
    doc_id        TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    stage         TEXT NOT NULL DEFAULT 'load',
    percent       INTEGER NOT NULL DEFAULT 0,
    state         TEXT NOT NULL DEFAULT 'running',
    actor_id      TEXT NOT NULL DEFAULT '',
    actor_name    TEXT NOT NULL DEFAULT '',
    stages_json   TEXT NOT NULL DEFAULT '[]',
    error         TEXT NOT NULL DEFAULT '',
    started_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_kb_jobs_live ON kb_jobs(state, started_at DESC);

CREATE TABLE IF NOT EXISTS kb_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    at            TEXT NOT NULL,
    kind          TEXT NOT NULL,
    doc_id        TEXT NOT NULL DEFAULT '',
    job_id        TEXT NOT NULL DEFAULT '',
    actor_id      TEXT NOT NULL DEFAULT '',
    actor_name    TEXT NOT NULL DEFAULT '',
    detail        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_kb_events_at ON kb_events(id DESC);

CREATE TABLE IF NOT EXISTS kb_queries (
    query_id      TEXT PRIMARY KEY,
    at            TEXT NOT NULL,
    question      TEXT NOT NULL,
    rewritten     TEXT NOT NULL DEFAULT '',
    channel       TEXT NOT NULL DEFAULT 'console',
    audience      TEXT NOT NULL DEFAULT 'both',
    actor_id      TEXT NOT NULL DEFAULT '',
    actor_name    TEXT NOT NULL DEFAULT '',
    stages_json   TEXT NOT NULL DEFAULT '[]',
    hits          INTEGER NOT NULL DEFAULT 0,
    top_score     REAL NOT NULL DEFAULT 0,
    grounded      INTEGER NOT NULL DEFAULT 0,
    blocked       TEXT NOT NULL DEFAULT '',
    answer        TEXT NOT NULL DEFAULT '',
    duration_ms   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_kb_queries_at ON kb_queries(at DESC);
"""


def ensure_schema() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex[:12]


def _row(record: Any) -> Dict[str, Any]:
    return {k: record[k] for k in record.keys()}


# ------------------------------------------------------------------ documents
def create_document(*, title: str, source_kind: str, source_name: str,
                    owner_id: str = "", owner_name: str = "",
                    media_type: str = "", size_bytes: int = 0, sha256: str = "",
                    audience: str = "both", topics: str = "", crops: str = "",
                    language: str = "", notes: str = "",
                    doc_id: Optional[str] = None) -> Dict[str, Any]:
    """Register a document before any work is done on it.

    The row exists from the moment the file arrives, in state ``queued``. An upload
    that then fails to parse is visible as a failure rather than as nothing at all -
    which is what a person who just pressed Upload needs to see.
    """
    ensure_schema()
    now = _now()
    record = {
        "doc_id": doc_id or new_id("doc"),
        "title": (title or source_name or "Untitled").strip()[:200],
        "source_kind": source_kind,
        "source_name": source_name[:400],
        "media_type": media_type[:120],
        "bytes": int(size_bytes),
        "sha256": sha256,
        "audience": audience if audience in AUDIENCES else "both",
        "topics": topics.strip(),
        "crops": crops.strip(),
        "language": language.strip(),
        "notes": notes.strip()[:2000],
        "state": "queued",
        "chunks": 0,
        "characters": 0,
        "provider": "",
        "version": 1,
        "owner_id": owner_id,
        "owner_name": owner_name[:120],
        "error": "",
        "body": "",
        "created_at": now,
        "updated_at": now,
    }
    with cursor() as cur:
        cur.execute(
            "INSERT INTO kb_documents (doc_id,title,source_kind,source_name,media_type,"
            "bytes,sha256,audience,topics,crops,language,notes,state,chunks,characters,"
            "provider,version,owner_id,owner_name,error,body,created_at,updated_at) "
            "VALUES (:doc_id,:title,:source_kind,:source_name,:media_type,:bytes,:sha256,"
            ":audience,:topics,:crops,:language,:notes,:state,:chunks,:characters,"
            ":provider,:version,:owner_id,:owner_name,:error,:body,:created_at,:updated_at)",
            record)
    return record


def update_document(doc_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    allowed = {"title", "audience", "topics", "crops", "language", "notes", "state",
               "chunks", "characters", "provider", "version", "error", "body",
               "sha256", "bytes", "media_type", "source_name"}
    changes = {k: v for k, v in fields.items() if k in allowed}
    if not changes:
        return get_document(doc_id)
    changes["doc_id"] = doc_id
    changes["updated_at"] = _now()
    assignments = ", ".join("%s = :%s" % (k, k) for k in changes if k != "doc_id")
    with cursor() as cur:
        cur.execute("UPDATE kb_documents SET " + assignments + " WHERE doc_id = :doc_id",
                    changes)
    return get_document(doc_id)


def get_document(doc_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT * FROM kb_documents WHERE doc_id = ?", (doc_id,))
        row = cur.fetchone()
    return _row(row) if row else None


def find_by_hash(sha256: str) -> Optional[Dict[str, Any]]:
    """The same file uploaded twice is the same document, not two.

    Without this, re-uploading a corrected PDF leaves both versions in retrieval and
    the chatbot cites whichever scored higher - which is a coin toss, not an answer.
    """
    if not sha256:
        return None
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT * FROM kb_documents WHERE sha256 = ? "
                    "ORDER BY created_at DESC LIMIT 1", (sha256,))
        row = cur.fetchone()
    return _row(row) if row else None


def list_documents(limit: int = 500, state: str = "", audience: str = "",
                   owner_id: str = "", search: str = "") -> List[Dict[str, Any]]:
    ensure_schema()
    where: List[str] = []
    params: List[Any] = []
    if state:
        where.append("state = ?")
        params.append(state)
    if audience and audience != "both":
        # "both" documents are visible to every audience, so asking for the chat
        # audience must include them - otherwise a general note uploaded once would
        # be invisible to both bots.
        where.append("(audience = ? OR audience = 'both')")
        params.append(audience)
    if owner_id:
        where.append("owner_id = ?")
        params.append(owner_id)
    if search:
        where.append("(LOWER(title) LIKE ? OR LOWER(source_name) LIKE ? "
                     "OR LOWER(topics) LIKE ?)")
        needle = "%" + search.lower() + "%"
        params.extend([needle, needle, needle])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    params.append(int(limit))
    with cursor() as cur:
        cur.execute("SELECT doc_id,title,source_kind,source_name,media_type,bytes,"
                    "audience,topics,crops,language,notes,state,chunks,characters,"
                    "provider,version,owner_id,owner_name,error,created_at,updated_at "
                    "FROM kb_documents" + clause +
                    " ORDER BY updated_at DESC LIMIT ?", params)
        return [_row(r) for r in cur.fetchall()]


def delete_document(doc_id: str) -> int:
    """Remove the row and its vectors together.

    Leaving the chunks behind would keep a deleted document answering questions,
    which is the single worst failure a knowledge base can have.
    """
    removed = store.remove_document(doc_id)
    with cursor() as cur:
        cur.execute("DELETE FROM kb_documents WHERE doc_id = ?", (doc_id,))
    return removed


def counts() -> Dict[str, Any]:
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT state, COUNT(*) c, SUM(chunks) ch, SUM(bytes) b "
                    "FROM kb_documents GROUP BY state")
        by_state = {r["state"]: {"documents": r["c"], "chunks": r["ch"] or 0,
                                 "bytes": r["b"] or 0} for r in cur.fetchall()}
        cur.execute("SELECT audience, COUNT(*) c FROM kb_documents "
                    "WHERE state = 'indexed' GROUP BY audience")
        by_audience = {r["audience"]: r["c"] for r in cur.fetchall()}
        cur.execute("SELECT owner_name, COUNT(*) c FROM kb_documents "
                    "WHERE owner_name <> '' GROUP BY owner_name ORDER BY c DESC LIMIT 10")
        by_owner = [{"name": r["owner_name"], "documents": r["c"]} for r in cur.fetchall()]
    return {
        "by_state": by_state,
        "by_audience": by_audience,
        "by_contributor": by_owner,
        "documents": sum(v["documents"] for v in by_state.values()),
        "indexed_chunks": by_state.get("indexed", {}).get("chunks", 0),
        "bytes": sum(v["bytes"] for v in by_state.values()),
    }


# ----------------------------------------------------------------------- jobs
def start_job(doc_id: str, title: str, actor_id: str = "",
              actor_name: str = "") -> Dict[str, Any]:
    ensure_schema()
    now = _now()
    record = {"job_id": new_id("job"), "doc_id": doc_id, "title": title[:200],
              "stage": INGEST_STAGES[0], "percent": 0, "state": "running",
              "actor_id": actor_id, "actor_name": actor_name[:120],
              "stages_json": "[]", "error": "", "started_at": now, "updated_at": now,
              "finished_at": None}
    with cursor() as cur:
        cur.execute("INSERT INTO kb_jobs (job_id,doc_id,title,stage,percent,state,"
                    "actor_id,actor_name,stages_json,error,started_at,updated_at,"
                    "finished_at) VALUES (:job_id,:doc_id,:title,:stage,:percent,:state,"
                    ":actor_id,:actor_name,:stages_json,:error,:started_at,:updated_at,"
                    ":finished_at)", record)
    return record


def update_job(job_id: str, *, stage: str = "", percent: Optional[int] = None,
               state: str = "", error: str = "",
               stages: Optional[List[Dict[str, Any]]] = None) -> None:
    changes: Dict[str, Any] = {"job_id": job_id, "updated_at": _now()}
    if stage:
        changes["stage"] = stage
    if percent is not None:
        changes["percent"] = max(0, min(100, int(percent)))
    if state:
        changes["state"] = state
        if state in ("done", "failed"):
            changes["finished_at"] = _now()
    if error:
        changes["error"] = error[:600]
    if stages is not None:
        changes["stages_json"] = json.dumps(stages, ensure_ascii=False)
    assignments = ", ".join("%s = :%s" % (k, k) for k in changes if k != "job_id")
    with cursor() as cur:
        cur.execute("UPDATE kb_jobs SET " + assignments + " WHERE job_id = :job_id",
                    changes)


def list_jobs(limit: int = 30, live_only: bool = False) -> List[Dict[str, Any]]:
    ensure_schema()
    clause = " WHERE state = 'running'" if live_only else ""
    with cursor() as cur:
        cur.execute("SELECT * FROM kb_jobs" + clause +
                    " ORDER BY started_at DESC LIMIT ?", (int(limit),))
        rows = [_row(r) for r in cur.fetchall()]
    for row in rows:
        try:
            row["stages"] = json.loads(row.pop("stages_json") or "[]")
        except json.JSONDecodeError:
            row["stages"] = []
    return rows


def abandon_running_jobs() -> int:
    """Mark jobs that were in flight when the process died.

    A job row stuck on "embedding" forever is worse than one that says it was
    interrupted: the first makes someone wait, the second makes them press Retry.
    """
    ensure_schema()
    with cursor() as cur:
        cur.execute("UPDATE kb_jobs SET state='failed', error=?, finished_at=? "
                    "WHERE state='running'",
                    ("The server restarted while this was being processed.", _now()))
        return cur.rowcount


# --------------------------------------------------------------------- events
def record_event(kind: str, *, doc_id: str = "", job_id: str = "", actor_id: str = "",
                 actor_name: str = "", detail: str = "") -> None:
    ensure_schema()
    with cursor() as cur:
        cur.execute("INSERT INTO kb_events (at,kind,doc_id,job_id,actor_id,actor_name,"
                    "detail) VALUES (?,?,?,?,?,?,?)",
                    (_now(), kind, doc_id, job_id, actor_id, actor_name[:120],
                     detail[:600]))


def list_events(limit: int = 60) -> List[Dict[str, Any]]:
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT * FROM kb_events ORDER BY id DESC LIMIT ?", (int(limit),))
        return [_row(r) for r in cur.fetchall()]


# -------------------------------------------------------------------- queries
def record_query(*, question: str, rewritten: str = "", channel: str = "console",
                 audience: str = "both", actor_id: str = "", actor_name: str = "",
                 stages: Optional[List[Dict[str, Any]]] = None, hits: int = 0,
                 top_score: float = 0.0, grounded: bool = False, blocked: str = "",
                 answer: str = "", duration_ms: int = 0) -> str:
    """Keep the trace of one retrieval run.

    This is the observability row in the architecture: it is what makes "the bot
    answered badly yesterday" a question with an answer, because the stages, the
    rewritten query and the chunks that won are all still here.
    """
    ensure_schema()
    query_id = new_id("q")
    with cursor() as cur:
        cur.execute("INSERT INTO kb_queries (query_id,at,question,rewritten,channel,"
                    "audience,actor_id,actor_name,stages_json,hits,top_score,grounded,"
                    "blocked,answer,duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (query_id, _now(), question[:600], rewritten[:600], channel,
                     audience, actor_id, actor_name[:120],
                     json.dumps(stages or [], ensure_ascii=False), int(hits),
                     float(top_score), 1 if grounded else 0, blocked[:200],
                     answer[:4000], int(duration_ms)))
    return query_id


def list_queries(limit: int = 40) -> List[Dict[str, Any]]:
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT * FROM kb_queries ORDER BY at DESC LIMIT ?", (int(limit),))
        rows = [_row(r) for r in cur.fetchall()]
    for row in rows:
        try:
            row["stages"] = json.loads(row.pop("stages_json") or "[]")
        except json.JSONDecodeError:
            row["stages"] = []
        row["grounded"] = bool(row.get("grounded"))
    return rows


def query_health(limit: int = 200) -> Dict[str, Any]:
    """What retrieval has actually been doing lately.

    Deliberately small. A retrieval dashboard that shows twelve charts and no
    answer to "is it finding anything" is decoration.
    """
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT hits, top_score, grounded, blocked, duration_ms FROM "
                    "kb_queries ORDER BY at DESC LIMIT ?", (int(limit),))
        rows = [_row(r) for r in cur.fetchall()]
    if not rows:
        return {"queries": 0, "note": "No question has been asked of the knowledge base yet."}
    durations = sorted(r["duration_ms"] for r in rows)
    empty = sum(1 for r in rows if not r["hits"])
    return {
        "queries": len(rows),
        "found_nothing": empty,
        "found_nothing_percent": round(100.0 * empty / len(rows), 1),
        "grounded": sum(1 for r in rows if r["grounded"]),
        "blocked": sum(1 for r in rows if r["blocked"]),
        "median_ms": durations[len(durations) // 2],
        "p95_ms": durations[max(0, int(len(durations) * 0.95) - 1)],
        "median_top_score": round(
            sorted(r["top_score"] for r in rows)[len(rows) // 2], 4),
    }
