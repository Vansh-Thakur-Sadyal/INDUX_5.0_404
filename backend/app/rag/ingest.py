"""The indexing half of the pipeline, run as a visible job.

    load -> parse -> clean -> chunk -> embed -> index

Every stage writes its state to ``kb_jobs`` as it finishes, which is what lets the
console draw the chain live instead of showing a spinner and a hope. A 300-page PDF
spends most of its time in ``embed``, and somebody watching deserves to know that
rather than wonder whether the upload worked.

Two decisions worth stating.

**The work happens in a background task, not in the request.** An upload that takes
ninety seconds to embed must not hold an HTTP connection open for ninety seconds -
browsers and proxies both give up long before that. The POST returns a job id as
soon as the bytes are safely on disk.

**A failure is a state, not an exception.** Every stage reports what went wrong into
the job row and the document row. "Failed: no text could be read from any of the 40
pages - this is usually a scan" is actionable. A 500 is not.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import documents as docs
from . import guardrails, loaders
from .embeddings import provider
from .pipeline import chunk_text
from .store import Chunk, store

log = logging.getLogger("saarthi.rag.ingest")

# Mistral's embedding endpoint takes a list; too long a list is a slow request that
# fails as a whole. 48 keeps each call under a second on a normal connection and
# gives the progress bar something to move for.
EMBED_BATCH = 48


@dataclass
class StageRecord:
    name: str
    state: str = "pending"            # pending | running | done | failed | skipped
    detail: str = ""
    ms: int = 0
    started: float = field(default=0.0, repr=False)

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "state": self.state, "detail": self.detail,
                "ms": self.ms}


class Run:
    """One ingestion, with its stages. Writes progress as it goes."""

    def __init__(self, job_id: str, doc_id: str) -> None:
        self.job_id = job_id
        self.doc_id = doc_id
        self.stages = [StageRecord(name) for name in docs.INGEST_STAGES]
        self._by_name = {s.name: s for s in self.stages}

    def _flush(self, stage: str = "", percent: Optional[int] = None,
               state: str = "", error: str = "") -> None:
        docs.update_job(self.job_id, stage=stage, percent=percent, state=state,
                        error=error, stages=[s.as_dict() for s in self.stages])

    def begin(self, name: str, detail: str = "") -> None:
        record = self._by_name[name]
        record.state = "running"
        record.detail = detail
        record.started = time.perf_counter()
        index = docs.INGEST_STAGES.index(name)
        self._flush(stage=name, percent=int(100 * index / len(docs.INGEST_STAGES)))

    def finish(self, name: str, detail: str = "") -> None:
        record = self._by_name[name]
        record.state = "done"
        record.ms = int((time.perf_counter() - record.started) * 1000)
        if detail:
            record.detail = detail
        index = docs.INGEST_STAGES.index(name) + 1
        self._flush(percent=int(100 * index / len(docs.INGEST_STAGES)))

    def progress(self, name: str, detail: str, percent: int) -> None:
        """Movement inside a long stage - embedding, in practice."""
        self._by_name[name].detail = detail
        self._flush(percent=percent)

    def fail(self, name: str, reason: str) -> None:
        record = self._by_name[name]
        record.state = "failed"
        record.detail = reason
        record.ms = int((time.perf_counter() - record.started) * 1000) if record.started else 0
        for other in self.stages:
            if other.state == "pending":
                other.state = "skipped"
        self._flush(state="failed", error=reason)

    def done(self) -> None:
        self._flush(state="done", percent=100)


async def ingest_document(doc_id: str, raw: bytes = b"", text: str = "",
                          filename: str = "", actor_id: str = "",
                          actor_name: str = "") -> Dict[str, Any]:
    """Run the six stages for one document. Returns the finished job row.

    Either ``raw`` (an uploaded file) or ``text`` (pasted or already fetched).
    """
    record = docs.get_document(doc_id)
    if not record:
        return {"ok": False, "error": "That document is not registered."}

    job = docs.start_job(doc_id, record["title"], actor_id=actor_id,
                         actor_name=actor_name)
    run = Run(job["job_id"], doc_id)
    docs.record_event("ingest_started", doc_id=doc_id, job_id=job["job_id"],
                      actor_id=actor_id, actor_name=actor_name,
                      detail=record["source_name"])

    try:
        # ------------------------------------------------------------- 1. load
        run.begin("load", "Reading " + record["source_name"])
        docs.update_document(doc_id, state="parsing", error="")
        if raw:
            digest = hashlib.sha256(raw).hexdigest()
            docs.update_document(doc_id, sha256=digest, bytes=len(raw))
            run.finish("load", "%.1f KB read" % (len(raw) / 1024.0))
        else:
            run.finish("load", "%d characters of pasted text" % len(text))

        # ------------------------------------------------------------ 2. parse
        run.begin("parse", "Extracting text")
        loaded = (loaders.load_bytes(raw, filename or record["source_name"])
                  if raw else loaders.load_text(text, record["title"]))
        if not loaded.ok:
            run.fail("parse", loaded.error)
            docs.update_document(doc_id, state="failed", error=loaded.error)
            docs.record_event("ingest_failed", doc_id=doc_id, job_id=job["job_id"],
                              actor_id=actor_id, actor_name=actor_name,
                              detail=loaded.error)
            return {"ok": False, "job_id": job["job_id"], "error": loaded.error}
        run.finish("parse", "%d characters%s" % (
            loaded.characters, (", " + loaded.note) if loaded.note else ""))
        if loaded.media_type:
            docs.update_document(doc_id, media_type=loaded.media_type)

        # ------------------------------------------------------------ 3. clean
        # Retrieved text reaches a prompt, so a document that contains an
        # instruction is a prompt-injection vector. It is neutralised here, once,
        # rather than on every retrieval - and the count is reported, because a
        # document that trips this is one a person should look at.
        run.begin("clean", "Checking for instruction-shaped text")
        body, removed = guardrails.sanitise(loaded.text)
        masked = guardrails.mask_pii(body)
        detail = "Clean."
        if removed or masked.changed:
            parts = []
            if removed:
                parts.append("%d instruction-like line(s) neutralised" % removed)
            if masked.changed:
                parts.append("%d personal identifier(s) masked"
                             % sum(masked.found.values()))
            detail = "; ".join(parts) + "."
        body = masked.text
        docs.update_document(doc_id, state="chunking", characters=len(body), body=body)
        run.finish("clean", detail)

        # ------------------------------------------------------------ 4. chunk
        run.begin("chunk", "Splitting into passages")
        pieces = chunk_text(body)
        if not pieces:
            reason = ("The document produced no passage long enough to index "
                      "(every section was under 40 characters).")
            run.fail("chunk", reason)
            docs.update_document(doc_id, state="failed", error=reason)
            return {"ok": False, "job_id": job["job_id"], "error": reason}
        sections = loaded.sections or []
        run.finish("chunk", "%d passage(s)" % len(pieces))

        # ------------------------------------------------------------ 5. embed
        run.begin("embed", "Embedding %d passage(s)" % len(pieces))
        docs.update_document(doc_id, state="embedding")
        texts = [record["title"] + "\n" + piece for piece in pieces]
        vectors: List[List[float]] = []
        used_provider = provider.name
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start:start + EMBED_BATCH]
            produced, used_provider = await provider.embed_with_provider(batch)
            vectors.extend(produced)
            done = min(start + len(batch), len(texts))
            run.progress("embed", "%d of %d passage(s)" % (done, len(texts)),
                         percent=int(100 * (4 + done / len(texts)) / 6))
        if len(vectors) != len(pieces):
            reason = "Embedding returned %d vectors for %d passages." % (
                len(vectors), len(pieces))
            run.fail("embed", reason)
            docs.update_document(doc_id, state="failed", error=reason)
            return {"ok": False, "job_id": job["job_id"], "error": reason}
        run.finish("embed", "%d vector(s) from %s" % (len(vectors), used_provider))

        # ------------------------------------------------------------ 6. index
        run.begin("index", "Writing to the vector index")
        docs.update_document(doc_id, state="indexing")
        version = int(record.get("version") or 1)
        # Re-indexing replaces: the old chunks go first, so a shorter second
        # version cannot leave orphans of the first one answering questions.
        store.remove_document(doc_id)
        topics = [t.strip() for t in (record["topics"] or "").split(",") if t.strip()]
        crops = [c.strip() for c in (record["crops"] or "").split(",") if c.strip()]
        built = [
            Chunk(chunk_id="%s#%d" % (doc_id, index),
                  doc_id=doc_id,
                  title=record["title"],
                  text=piece,
                  topics=topics,
                  source=record["source_name"] or record["title"],
                  crops=crops,
                  embedding=list(vector),
                  audience=record["audience"],
                  section=_section_for(index, len(pieces), sections),
                  position=index,
                  doc_version=version)
            for index, (piece, vector) in enumerate(zip(pieces, vectors))
        ]
        store.upsert(built, used_provider)
        store.save()
        run.finish("index", "%d passage(s) searchable" % len(built))

        docs.update_document(doc_id, state="indexed", chunks=len(built),
                             provider=used_provider, error="")
        run.done()
        docs.record_event("indexed", doc_id=doc_id, job_id=job["job_id"],
                          actor_id=actor_id, actor_name=actor_name,
                          detail="%d passage(s), embedded by %s"
                                 % (len(built), used_provider))
        return {"ok": True, "job_id": job["job_id"], "chunks": len(built),
                "provider": used_provider, "characters": len(body)}

    except asyncio.CancelledError:
        run.fail(run.stages[0].name, "The server stopped while this was running.")
        docs.update_document(doc_id, state="failed",
                             error="Interrupted by a server restart.")
        raise
    except Exception as exc:                                       # noqa: BLE001
        log.exception("ingestion failed for %s", doc_id)
        reason = "Unexpected failure: %s" % str(exc)[:200]
        current = next((s.name for s in run.stages if s.state == "running"), "load")
        run.fail(current, reason)
        docs.update_document(doc_id, state="failed", error=reason)
        docs.record_event("ingest_failed", doc_id=doc_id, job_id=job["job_id"],
                          actor_id=actor_id, actor_name=actor_name, detail=reason)
        return {"ok": False, "job_id": job["job_id"], "error": reason}


def _section_for(index: int, total: int, sections: List[Dict[str, Any]]) -> str:
    """Which heading a passage most likely sits under.

    Approximate on purpose. The exact character offset of a chunk is lost by the
    time it is packed, and a citation that says "Page 12" when the sentence is on
    page 13 is still far more useful than one that says nothing. Anything that
    claimed more precision than this would be claiming precision it does not have.
    """
    if not sections or total <= 0:
        return ""
    position = int(index * len(sections) / total)
    return str(sections[min(position, len(sections) - 1)].get("title") or "")


# ----------------------------------------------------------------- background
_tasks: Dict[str, asyncio.Task] = {}


def schedule(doc_id: str, **kwargs: Any) -> str:
    """Start the ingestion without making the uploader wait for it."""
    task = asyncio.ensure_future(ingest_document(doc_id, **kwargs))
    _tasks[doc_id] = task

    def _clear(finished: asyncio.Task) -> None:
        _tasks.pop(doc_id, None)
        if not finished.cancelled() and finished.exception() is not None:
            log.warning("ingestion task for %s died: %s", doc_id, finished.exception())

    task.add_done_callback(_clear)
    return doc_id


def running() -> List[str]:
    return [doc_id for doc_id, task in _tasks.items() if not task.done()]


async def wait_for(doc_id: str, timeout: float = 120.0) -> None:
    """Used by the tests, and by the one-document reindex button."""
    task = _tasks.get(doc_id)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
