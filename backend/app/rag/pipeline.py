"""RAG pipeline: collect -> clean -> chunk -> embed -> index -> retrieve.

Documents live in ``data/knowledge/*.md`` with a small YAML-ish header:

    ---
    id: irrigation-scheduling
    title: Irrigation scheduling basics
    topics: irrigation, soil-moisture
    crops: wheat, rice
    source: Saarthi knowledge base - general agronomy notes
    ---

Retrieved text is treated as untrusted data. ``sanitise`` strips instruction-
shaped lines before anything reaches the LLM prompt, which is the prompt-
injection control called for in the write-up.

Documents uploaded through the knowledge console take the same path, one stage at
a time and with its progress visible - see ``rag.ingest``. This module stays the
loader for the documents that ship WITH Saarthi, in ``data/knowledge``, and the
retrieval entry point that every channel calls.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..core.config import KNOWLEDGE_DIR
from ..core.schemas import RetrievedChunk
from .embeddings import provider
from .store import Chunk, store

log = logging.getLogger("saarthi.rag")

FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Lines that try to steer the model rather than inform it.
_INJECTION_PATTERNS = [
    re.compile(r"(?i)\bignore (all |any )?(previous|prior|above) (instructions|rules)"),
    re.compile(r"(?i)\byou are now\b"),
    re.compile(r"(?i)\bsystem prompt\b"),
    re.compile(r"(?i)\bdisregard (the )?(rules|instructions)"),
    re.compile(r"(?i)\breveal (your )?(prompt|instructions|key)"),
    re.compile(r"(?i)\bact as\b.*\badministrator\b"),
]

MAX_CHUNK_CHARS = 900
CHUNK_OVERLAP_CHARS = 150


# ------------------------------------------------------------------ safety
def sanitise(text: str) -> str:
    """Drop instruction-shaped lines from retrieved text."""
    kept: List[str] = []
    for line in (text or "").splitlines():
        if any(p.search(line) for p in _INJECTION_PATTERNS):
            kept.append("[line removed: looked like an instruction, not agronomy]")
            continue
        kept.append(line)
    return "\n".join(kept)


# --------------------------------------------------------------- documents
def parse_document(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    meta: Dict[str, Any] = {}
    body = raw
    match = FRONTMATTER.match(raw)
    if match:
        body = raw[match.end():]
        for line in match.group(1).splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip()
    doc_id = meta.get("id") or path.stem
    topics = [t.strip() for t in str(meta.get("topics", "")).split(",") if t.strip()]
    crops = [c.strip() for c in str(meta.get("crops", "")).split(",") if c.strip()]
    return {
        "doc_id": doc_id,
        "title": meta.get("title", path.stem.replace("-", " ").title()),
        "topics": topics,
        "crops": crops,
        "source": meta.get("source", "Saarthi knowledge base"),
        "body": body.strip(),
        "path": str(path),
    }


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS,
               overlap: int = CHUNK_OVERLAP_CHARS) -> List[str]:
    """Split on blank lines, then pack paragraphs up to max_chars with overlap."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: List[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > max_chars:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sentence in sentences:
                if len(current) + len(sentence) + 1 > max_chars and current:
                    chunks.append(current.strip())
                    current = current[-overlap:] if overlap else ""
                current += (" " if current else "") + sentence
            continue
        if len(current) + len(para) + 2 > max_chars and current:
            chunks.append(current.strip())
            current = current[-overlap:] if overlap else ""
        current += ("\n\n" if current else "") + para
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if len(c) > 40]


def load_documents(directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    folder = Path(directory) if directory else KNOWLEDGE_DIR
    if not folder.exists():
        return []
    return [parse_document(p) for p in sorted(folder.glob("*.md"))]


# ----------------------------------------------------------------- ingest
async def ingest(directory: Optional[Path] = None, rebuild: bool = False) -> Dict[str, Any]:
    """Build (or rebuild) the vector index from the knowledge folder."""
    documents = load_documents(directory)
    if not documents:
        return {"documents": 0, "chunks": 0, "provider": provider.name,
                "note": "No knowledge documents found in " + str(directory or KNOWLEDGE_DIR)}
    if rebuild or store.is_stale():
        store.clear()
    pending: List[Chunk] = []
    texts: List[str] = []
    for doc in documents:
        for i, piece in enumerate(chunk_text(sanitise(doc["body"]))):
            pending.append(Chunk(
                chunk_id=doc["doc_id"] + "#" + str(i),
                doc_id=doc["doc_id"],
                title=doc["title"],
                text=piece,
                topics=doc["topics"],
                source=doc["source"],
                crops=doc["crops"],
            ))
            texts.append(doc["title"] + "\n" + piece)
    # Label the index with the provider that actually produced the vectors, not the
    # one we hoped for: a rate-limited Mistral call yields offline vectors, and an
    # index that lied about that would never be rebuilt.
    vectors, used_provider = await provider.embed_with_provider(texts)
    for chunk, vector in zip(pending, vectors):
        chunk.embedding = list(vector)
    added = store.upsert(pending, used_provider)
    store.save()
    _register_shipped(documents, pending, used_provider)
    stats = store.stats()
    stats.update({"ingested_documents": len(documents), "new_chunks": added})
    return stats


def _register_shipped(documents: List[Dict[str, Any]], chunks: List[Chunk],
                      used_provider: str) -> None:
    """Give the folder documents a row in the console too.

    Otherwise the console shows the three PDFs someone uploaded and not the fifteen
    agronomy notes the agents have been answering from all along, which makes it a
    list of recent uploads rather than a picture of the knowledge base. They are
    marked ``shipped`` so nobody mistakes them for something a colleague added.
    """
    try:
        from . import documents as doc_store
        doc_store.ensure_schema()
    except Exception:                                              # noqa: BLE001
        return                        # no database open (a bare ingest script): skip
    per_doc: Dict[str, int] = {}
    for chunk in chunks:
        per_doc[chunk.doc_id] = per_doc.get(chunk.doc_id, 0) + 1
    for document in documents:
        doc_id = document["doc_id"]
        existing = doc_store.get_document(doc_id)
        # The file name, not the absolute path. The console is read by a person,
        # and a column of full paths tells them nothing they did not already know
        # while hiding the part that matters.
        source = Path(document.get("path", "") or (doc_id + ".md")).name
        fields = dict(state="indexed", chunks=per_doc.get(doc_id, 0),
                      characters=len(document["body"]), provider=used_provider,
                      source_name=source, title=document["title"], error="")
        if existing:
            doc_store.update_document(doc_id, **fields)
            continue
        doc_store.create_document(
            doc_id=doc_id, title=document["title"], source_kind="shipped",
            source_name=source,
            media_type="text/markdown", owner_name="Saarthi (shipped with the app)",
            topics=", ".join(document.get("topics", [])),
            crops=", ".join(document.get("crops", [])),
            notes=document.get("source", ""))
        doc_store.update_document(doc_id, **fields)


async def ensure_index() -> Dict[str, Any]:
    """Index on first use, and re-index when the embedding provider changed."""
    if not store.chunks or store.is_stale():
        return await ingest(rebuild=store.is_stale())
    return store.stats()


# --------------------------------------------------------------- retrieval
def _log_retrieval(query: str, hits: List[Dict[str, Any]], audience: str,
                   started: float) -> None:
    """Record one retrieval. Must never break the answer it is observing."""
    try:
        from . import documents as doc_store
        from .guardrails import mask_pii
        # The chatbot and the phone line reach retrieval directly, so the masking
        # the console chain does has not happened yet. A farmer who types their
        # number into the chat must not have it written into the query log.
        doc_store.record_query(
            question=mask_pii(query).text,
            channel=audience or "chat", audience=audience or "both",
            actor_name="", hits=len(hits),
            top_score=float(hits[0]["score"]) if hits else 0.0,
            duration_ms=int((time.perf_counter() - started) * 1000))
    except Exception as exc:                                       # noqa: BLE001
        log.debug("retrieval not logged: %s", exc)


async def retrieve(query: str, top_k: int = 5,
                   topics: Optional[Sequence[str]] = None,
                   audience: str = "") -> List[RetrievedChunk]:
    """Retrieve for one channel.

    ``audience`` is the access rule, not a preference: "chat" never sees a document
    marked for the phone line only, and the other way round. It is passed down to
    the store rather than filtered here, so the rule is applied before scoring and
    cannot be forgotten by a caller.
    """
    started = time.perf_counter()
    await ensure_index()
    if not query.strip():
        return []
    # ``urgent``: this sits on the critical path of a reply someone is waiting for,
    # so a failed embedding falls through to keyword search immediately instead of
    # retrying for four seconds.
    vectors = await provider.embed([query], urgent=True)
    query_vector = vectors[0] if vectors else []
    hits = store.search(query, query_vector, top_k=top_k, topics=topics,
                        audience=audience)
    if not hits and topics:
        # A vague question ("what should I do") scores below the threshold. Relax the
        # threshold inside the requested topics first; only drop the topic filter if
        # the topics hold nothing at all.
        hits = store.search(query, query_vector, top_k=top_k, topics=topics,
                            min_score=0.0, audience=audience)
    if not hits and topics:
        hits = store.search(query, query_vector, top_k=top_k, audience=audience)

    # Every retrieval is logged, not only the ones run from the console. A question
    # the chatbot could not find anything for is the most useful row in the whole
    # system: it names a gap somebody can fill with one upload. Logging only the
    # console would hide exactly the traffic that matters.
    _log_retrieval(query, hits, audience, started)

    return [
        RetrievedChunk(
            chunk_id=hit["chunk_id"],
            doc_id=hit["doc_id"],
            title=hit["title"],
            text=sanitise(hit["text"]),
            score=hit["score"],
            topics=hit["topics"],
            source=hit["source"],
        )
        for hit in hits
    ]


def to_prompt_block(chunks: Sequence[RetrievedChunk], max_chars: int = 3200) -> str:
    """Render retrieved chunks for the LLM prompt, with source labels."""
    parts: List[str] = []
    used = 0
    for i, chunk in enumerate(chunks, 1):
        block = "[%d] %s (%s)\n%s" % (i, chunk.title, chunk.source, chunk.text)
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)
