"""The knowledge console API: feed the bots, and watch what they do with it.

Everything under ``/api/kb`` is administrator-only. The knowledge base decides what
the chatbot and the phone line say to every farmer on the system, so adding a
document is a far more powerful act than editing one farm - a wrong fertiliser dose
uploaded here reaches everyone at once.

Three groups of endpoints:

``/documents``  what has been fed in, by whom, and in what state. Upload, paste,
                fetch a URL, re-index, enable, disable, delete.
``/jobs``       ingestion in flight, stage by stage, so the console can draw it.
``/query``      the retrieval chain, with every stage reported. ``/query/stream``
                is the same thing as server-sent events, for the live diagram.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     UploadFile)
from fastapi.responses import StreamingResponse

from ..core.security import require_admin
from ..llm import router
from ..rag import chain as query_chain
from ..rag import documents as docs
from ..rag import ingest, loaders
from ..rag import pipeline as rag_pipeline
from ..rag.embeddings import provider
from ..rag.store import store

log = logging.getLogger("saarthi.kb")

router = APIRouter(prefix="/api/kb", tags=["knowledge base"])


def _actor(user: Dict[str, Any]) -> Dict[str, str]:
    return {"actor_id": str(user.get("user_id") or ""),
            "actor_name": str(user.get("full_name") or user.get("email") or "")}


def _owner(user: Dict[str, Any]) -> Dict[str, str]:
    return {"owner_id": str(user.get("user_id") or ""),
            "owner_name": str(user.get("full_name") or user.get("email") or "")}


# ============================================================ what this can take
@router.get("/capabilities")
async def capabilities(user: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    """What the console can accept and what each stage of the chain is for.

    The console renders its block diagram from this rather than from a hard-coded
    copy in the page, so the diagram cannot drift away from the code that runs.
    """
    return {
        "formats": loaders.describe_supported(),
        "max_bytes": loaders.MAX_BYTES,
        "audiences": [
            {"id": "both", "label": "Both bots",
             "note": "The dashboard chatbot and the phone line can both quote it."},
            {"id": "chat", "label": "Chatbot only",
             "note": "Reaches the dashboard assistant and the app. Not the phone line."},
            {"id": "call", "label": "Calling bot only",
             "note": "Reaches the phone line. Useful for anything too long to read on "
                     "a screen but fine to hear."},
        ],
        "ingest_stages": [
            {"id": "load", "label": "Load",
             "note": "Read the bytes, record the size and a checksum."},
            {"id": "parse", "label": "Parse",
             "note": "PDF, Word, spreadsheet or web page to plain text with its "
                     "headings kept."},
            {"id": "clean", "label": "Clean",
             "note": "Collapse converter whitespace, mask personal data, neutralise "
                     "any line that reads as an instruction."},
            {"id": "chunk", "label": "Chunk",
             "note": "Split on paragraphs into overlapping passages of about 900 "
                     "characters."},
            {"id": "embed", "label": "Embed",
             "note": "Mistral embeddings, in batches, with the provider recorded."},
            {"id": "index", "label": "Index",
             "note": "Replace this document's passages in the vector index."},
        ],
        "query_stages": [
            {"id": "guardrails", "label": "Input guardrails",
             "note": "Mask personal data in the question, refuse instruction-shaped "
                     "questions."},
            {"id": "rewrite", "label": "Query rewrite",
             "note": "Optional. Multi-query, HyDE or step-back, written by Mistral."},
            {"id": "embed", "label": "Embed query",
             "note": "The same embedding model that indexed the documents."},
            {"id": "retrieve", "label": "Hybrid retrieval",
             "note": "Cosine similarity blended with IDF-weighted keyword overlap, "
                     "filtered by audience."},
            {"id": "rerank", "label": "Rerank",
             "note": "Rank fusion across queries, then diversity, then optionally a "
                     "model that scores each passage."},
            {"id": "generate", "label": "Generate",
             "note": "Mistral writes the answer from the passages and cites them."},
            {"id": "verify", "label": "Verify",
             "note": "Every number in the answer must appear in a passage, or it is "
                     "flagged."},
        ],
        "rewrite_strategies": [
            {"id": "none", "label": "As typed", "note": "No model call. The default."},
            {"id": "expand", "label": "Multi-query",
             "note": "Two or three rephrasings searched together."},
            {"id": "hyde", "label": "HyDE",
             "note": "Search with a hypothetical answer instead of the question."},
            {"id": "step_back", "label": "Step back",
             "note": "Search with the general question behind the specific one."},
        ],
    }


# ==================================================================== documents
@router.get("/documents")
async def list_documents(state: str = "", audience: str = "", search: str = "",
                         limit: int = Query(300, ge=1, le=1000),
                         user: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    rows = docs.list_documents(limit=limit, state=state, audience=audience,
                               search=search)
    return {"documents": rows, "counts": docs.counts(),
            "index": store.stats()}


@router.get("/documents/{doc_id}")
async def document_detail(doc_id: str,
                          user: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    record = docs.get_document(doc_id)
    if not record:
        raise HTTPException(status_code=404, detail="No such document.")
    # The passages, so somebody can see exactly what retrieval will match on. This
    # is the single most useful debugging view in a RAG system: a bad answer is
    # nearly always a bad chunk, and until you look at the chunks it is a mystery.
    passages = [
        {"chunk_id": c.chunk_id, "position": c.position, "section": c.section,
         "characters": len(c.text), "text": c.text}
        for c in sorted((c for c in store.chunks if c.doc_id == doc_id),
                        key=lambda c: c.position)
    ]
    record.pop("body", None)
    return {"document": record, "passages": passages}


@router.post("/documents/upload")
async def upload(file: UploadFile = File(...),
                 title: str = Form(""), audience: str = Form("both"),
                 topics: str = Form(""), crops: str = Form(""),
                 language: str = Form(""), notes: str = Form(""),
                 user: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    """Take a file and start indexing it. Returns as soon as the bytes are safe.

    The ingestion itself runs in the background: embedding a long PDF takes longer
    than any browser will hold a POST open. The response carries the document id, and
    ``/api/kb/jobs`` reports the stages from there.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="The file was empty.")
    if len(raw) > loaders.MAX_BYTES:
        raise HTTPException(status_code=413, detail=(
            "That file is %.1f MB. The limit is %d MB."
            % (len(raw) / 1048576.0, loaders.MAX_BYTES // 1048576)))
    filename = file.filename or "upload"
    if not loaders.is_supported(filename):
        raise HTTPException(status_code=415, detail=(
            "%s files are not read. Supported: %s."
            % (loaders.extension_of(filename) or "Extensionless",
               ", ".join(sorted(loaders.SUPPORTED)))))

    digest = hashlib.sha256(raw).hexdigest()
    duplicate = docs.find_by_hash(digest)
    if duplicate and duplicate["state"] != "failed":
        # Byte-identical to something already here. Re-indexing it would double
        # every passage and let the bot cite the same sentence twice as if it were
        # two sources.
        return {"ok": False, "duplicate_of": duplicate["doc_id"],
                "detail": "This exact file is already in the knowledge base as '%s', "
                          "uploaded by %s." % (duplicate["title"],
                                               duplicate["owner_name"] or "someone")}

    record = docs.create_document(
        title=title or filename, source_kind="upload", source_name=filename,
        media_type=file.content_type or "", size_bytes=len(raw), sha256=digest,
        audience=audience, topics=topics, crops=crops, language=language, notes=notes,
        **_owner(user))
    docs.record_event("uploaded", doc_id=record["doc_id"], detail=filename,
                      **_actor(user))
    ingest.schedule(record["doc_id"], raw=raw, filename=filename, **_actor(user))
    return {"ok": True, "document": record}


@router.post("/documents/text")
async def add_text(payload: Dict[str, Any],
                   user: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    """Paste a note straight in. The fastest way to correct one wrong answer."""
    text = str(payload.get("text") or "").strip()
    if len(text) < 40:
        raise HTTPException(status_code=400, detail=(
            "Paste at least a short paragraph - under 40 characters there is nothing "
            "to retrieve."))
    title = str(payload.get("title") or "").strip() or (text[:60] + "...")
    record = docs.create_document(
        title=title, source_kind="text", source_name="Pasted note",
        media_type="text/plain", size_bytes=len(text.encode("utf-8")),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        audience=str(payload.get("audience") or "both"),
        topics=str(payload.get("topics") or ""), crops=str(payload.get("crops") or ""),
        language=str(payload.get("language") or ""),
        notes=str(payload.get("notes") or ""), **_owner(user))
    docs.record_event("pasted", doc_id=record["doc_id"], detail=title, **_actor(user))
    ingest.schedule(record["doc_id"], text=text, **_actor(user))
    return {"ok": True, "document": record}


@router.post("/documents/url")
async def add_url(payload: Dict[str, Any],
                  user: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    """Fetch a page and index it.

    The fetch happens here rather than in the background, because a URL that 404s
    should be a message on the form, not a failed job the person has to go and find.
    """
    url = str(payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="A URL is required.")
    loaded, page_title = await loaders.load_url(url)
    if not loaded.ok:
        raise HTTPException(status_code=400, detail=loaded.error)

    digest = hashlib.sha256(loaded.text.encode("utf-8")).hexdigest()
    duplicate = docs.find_by_hash(digest)
    if duplicate and duplicate["state"] != "failed":
        return {"ok": False, "duplicate_of": duplicate["doc_id"],
                "detail": "This page has not changed since it was last fetched as '%s'."
                          % duplicate["title"]}

    title = str(payload.get("title") or "").strip() or page_title or url
    record = docs.create_document(
        title=title, source_kind="url", source_name=url,
        media_type=loaded.media_type, size_bytes=len(loaded.text.encode("utf-8")),
        sha256=digest, audience=str(payload.get("audience") or "both"),
        topics=str(payload.get("topics") or ""), crops=str(payload.get("crops") or ""),
        language=str(payload.get("language") or ""),
        notes=str(payload.get("notes") or ""), **_owner(user))
    docs.record_event("fetched", doc_id=record["doc_id"], detail=url, **_actor(user))
    ingest.schedule(record["doc_id"], text=loaded.text, **_actor(user))
    return {"ok": True, "document": record, "note": loaded.note}


@router.patch("/documents/{doc_id}")
async def edit_document(doc_id: str, payload: Dict[str, Any],
                        user: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    """Change the metadata. A changed audience takes effect immediately."""
    record = docs.get_document(doc_id)
    if not record:
        raise HTTPException(status_code=404, detail="No such document.")
    fields = {k: str(payload[k]) for k in ("title", "topics", "crops", "language",
                                           "notes", "audience")
              if k in payload}
    if "audience" in fields and fields["audience"] not in docs.AUDIENCES:
        raise HTTPException(status_code=400, detail="Unknown audience.")
    updated = docs.update_document(doc_id, **fields)

    # The audience lives on each chunk as well as on the document, because the
    # store filters on it before scoring. Changing it on the row alone would leave
    # the old rule in force in the only place it is actually applied.
    if "audience" in fields:
        changed = 0
        for chunk in store.chunks:
            if chunk.doc_id == doc_id:
                chunk.audience = fields["audience"]
                changed += 1
        if changed:
            store.save()
    if "topics" in fields or "crops" in fields:
        topics = [t.strip() for t in fields.get("topics", record["topics"]).split(",")
                  if t.strip()]
        crops = [c.strip() for c in fields.get("crops", record["crops"]).split(",")
                 if c.strip()]
        for chunk in store.chunks:
            if chunk.doc_id == doc_id:
                chunk.topics, chunk.crops = topics, crops
        store.save()
    docs.record_event("edited", doc_id=doc_id,
                      detail=", ".join("%s=%s" % (k, v) for k, v in fields.items()),
                      **_actor(user))
    return {"ok": True, "document": updated}


@router.post("/documents/{doc_id}/state")
async def set_state(doc_id: str, payload: Dict[str, Any],
                    user: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    """Take a document out of retrieval, or put it back.

    Disabling drops its chunks from the index but keeps the text, so putting it
    back is a re-index rather than another upload. Most requests to delete a
    document are really this.
    """
    wanted = str(payload.get("state") or "").strip()
    if wanted not in ("indexed", "disabled"):
        raise HTTPException(status_code=400,
                            detail="Use state 'disabled' or 'indexed'.")
    record = docs.get_document(doc_id)
    if not record:
        raise HTTPException(status_code=404, detail="No such document.")

    if wanted == "disabled":
        removed = store.remove_document(doc_id)
        store.save()
        docs.update_document(doc_id, state="disabled", chunks=0)
        docs.record_event("disabled", doc_id=doc_id,
                          detail="%d passage(s) removed from retrieval" % removed,
                          **_actor(user))
        return {"ok": True, "state": "disabled", "removed": removed}

    body = record.get("body") or ""
    if not body:
        raise HTTPException(status_code=409, detail=(
            "The text of this document was not kept, so it cannot be re-indexed. "
            "Upload it again."))
    ingest.schedule(doc_id, text=body, **_actor(user))
    docs.record_event("re_enabled", doc_id=doc_id, **_actor(user))
    return {"ok": True, "state": "queued"}


@router.post("/documents/{doc_id}/reindex")
async def reindex(doc_id: str,
                  user: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    record = docs.get_document(doc_id)
    if not record:
        raise HTTPException(status_code=404, detail="No such document.")
    body = record.get("body") or ""
    if not body:
        raise HTTPException(status_code=409, detail=(
            "The text of this document was not kept, so it cannot be re-indexed. "
            "Upload it again."))
    docs.update_document(doc_id, version=int(record.get("version") or 1) + 1)
    ingest.schedule(doc_id, text=body, **_actor(user))
    docs.record_event("reindex_requested", doc_id=doc_id, **_actor(user))
    return {"ok": True, "doc_id": doc_id}


@router.delete("/documents/{doc_id}")
async def delete(doc_id: str,
                 user: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    record = docs.get_document(doc_id)
    if not record:
        raise HTTPException(status_code=404, detail="No such document.")
    removed = docs.delete_document(doc_id)
    store.save()
    docs.record_event("deleted", doc_id=doc_id,
                      detail="%s (%d passage(s))" % (record["title"], removed),
                      **_actor(user))
    return {"ok": True, "removed_passages": removed}


# ========================================================================= jobs
@router.get("/jobs")
async def jobs(limit: int = Query(25, ge=1, le=100), live: bool = False,
               user: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    """Ingestion in flight and recently finished, with who started each one."""
    return {"jobs": docs.list_jobs(limit=limit, live_only=live),
            "running": ingest.running()}


@router.get("/activity")
async def activity(limit: int = Query(60, ge=1, le=200),
                   user: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    return {"events": docs.list_events(limit=limit)}


@router.get("/stats")
async def stats(user: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    """One call for the whole header of the console."""
    await rag_pipeline.ensure_index()
    index = store.stats()
    return {
        "documents": docs.counts(),
        "index": index,
        "embeddings": {
            "provider": provider.name,
            "dimensions": provider.dims,
            "index_provider": index.get("provider"),
            "stale": index.get("stale"),
            "note": ("The index was built by a different embedding provider and will "
                     "be rebuilt on the next ingest."
                     if index.get("stale") else
                     "The index and the active embedder agree."),
        },
        "retrieval": docs.query_health(),
        "jobs": docs.list_jobs(limit=5),
        # Which model does which job, and the measurement that decided it. Here
        # rather than in a comment because the split will change as the providers
        # do, and whoever re-measures should be able to see what they are
        # arguing with.
        "models": router.describe(),
    }


# ======================================================================== query
@router.post("/query")
async def query(payload: Dict[str, Any],
                user: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    """Run the chain and return the whole trace."""
    question = str(payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="A question is required.")
    result = await query_chain.run(
        question,
        audience=str(payload.get("audience") or "both"),
        top_k=int(payload.get("top_k") or 5),
        strategy=str(payload.get("strategy") or "none"),
        use_judge=bool(payload.get("rerank_with_model")),
        generate=bool(payload.get("generate", True)),
        language=str(payload.get("language") or "en"),
        channel="console", **_actor(user))
    return result.as_dict()


@router.post("/query/stream")
async def query_stream(payload: Dict[str, Any],
                       user: Dict[str, Any] = Depends(require_admin)):
    """The chain as server-sent events, which is what the live diagram reads."""
    question = str(payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="A question is required.")

    async def events() -> AsyncIterator[str]:
        try:
            async for event in query_chain.stream(
                    question,
                    audience=str(payload.get("audience") or "both"),
                    top_k=int(payload.get("top_k") or 5),
                    strategy=str(payload.get("strategy") or "none"),
                    use_judge=bool(payload.get("rerank_with_model")),
                    generate=bool(payload.get("generate", True)),
                    language=str(payload.get("language") or "en"),
                    channel="console", **_actor(user)):
                if "result" in event:
                    event = {"result": event["result"].as_dict()}
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        except Exception as exc:                                   # noqa: BLE001
            log.exception("knowledge query stream failed")
            yield ("data: " + json.dumps({"error": str(exc)[:200]}) + "\n\n")

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/queries")
async def recent_queries(limit: int = Query(40, ge=1, le=200),
                         user: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    """Every question the knowledge base has been asked, and what the chain did.

    Including the ones from the chatbot and the phone line, not just the console -
    which is the point. "Found nothing" rows are the backlog: each one is a gap
    somebody could fill with an upload.
    """
    return {"queries": docs.list_queries(limit=limit),
            "health": docs.query_health()}
