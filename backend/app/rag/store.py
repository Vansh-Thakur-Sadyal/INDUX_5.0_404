"""Vector store for the Saarthi knowledge base.

Default backend is a local JSON index (data/storage/vector_index.json) with
in-process cosine search - no external service, which keeps the demo runnable
offline. The interface mirrors a MongoDB Atlas Vector Search collection
(``kb_vectors``) so the production backend named in the write-up can be swapped
in behind the same two methods: ``upsert`` and ``search``.

Retrieval is hybrid: cosine similarity over embeddings, blended with a lexical
keyword overlap score. Hybrid scoring matters here because the offline embedder
is purely lexical and because agricultural questions are keyword-heavy
("urea", "blight", "mandi").
"""
from __future__ import annotations

import json
import math
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..core.config import STORAGE_DIR, ensure_dirs
from .embeddings import cosine, provider

INDEX_FILE = STORAGE_DIR / "vector_index.json"
_WORD = re.compile(r"[a-z0-9ऀ-ൿ]{3,}")

# Question words carry no retrieval signal and otherwise dilute the lexical score.
STOPWORDS = {
    "the", "and", "for", "are", "was", "with", "what", "when", "how", "should", "would",
    "much", "many", "can", "will", "that", "this", "from", "have", "has", "its", "into",
    "but", "not", "you", "your", "our", "out", "about", "there", "then", "than", "они",
    "does", "did", "doing", "any", "all", "some", "more", "most", "other", "which",
    "give", "tell", "need", "want", "please", "mera", "meri", "mujhe", "kya", "hai",
    "kaise", "kab", "kitna", "kitni", "karna", "chahiye", "मेरा", "क्या", "कैसे", "कब",
}


@dataclass
class Chunk:
    """One retrievable passage, with the metadata that decides whether it is allowed.

    ``audience`` is an access rule, not a hint. It is applied inside ``search``,
    before anything is scored, because a filter the caller is trusted to apply is a
    filter that will eventually be forgotten - and the thing it is protecting is
    "the phone line must not quote the internal operations notes".

    ``section`` and ``position`` exist so a citation can say where in the document a
    sentence came from. Without them every citation points at page one.
    """

    chunk_id: str
    doc_id: str
    title: str
    text: str
    topics: List[str] = field(default_factory=list)
    source: str = ""
    crops: List[str] = field(default_factory=list)
    embedding: List[float] = field(default_factory=list)
    audience: str = "both"
    section: str = ""
    position: int = 0
    doc_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "title": self.title,
            "text": self.text,
            "topics": self.topics,
            "source": self.source,
            "crops": self.crops,
            "embedding": self.embedding,
            "audience": self.audience,
            "section": self.section,
            "position": self.position,
            "doc_version": self.doc_version,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Chunk":
        # Defaults on every new field: an index written before they existed must
        # still load, or one deploy silently empties the knowledge base.
        return Chunk(
            chunk_id=data["chunk_id"],
            doc_id=data["doc_id"],
            title=data.get("title", ""),
            text=data.get("text", ""),
            topics=list(data.get("topics", [])),
            source=data.get("source", ""),
            crops=list(data.get("crops", [])),
            embedding=list(data.get("embedding", [])),
            audience=data.get("audience", "both") or "both",
            section=data.get("section", ""),
            position=int(data.get("position", 0) or 0),
            doc_version=int(data.get("doc_version", 1) or 1),
        )


def _keywords(text: str, drop_stopwords: bool = False) -> set:
    words = set(_WORD.findall((text or "").lower()))
    if drop_stopwords:
        words -= STOPWORDS
    return words


class VectorStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else INDEX_FILE
        self.chunks: List[Chunk] = []
        self.provider_name: str = ""
        self._idf_cache: Optional[Dict[str, float]] = None
        self._lock = threading.RLock()
        self.load()

    # ----------------------------------------------------------- lifecycle
    def load(self) -> None:
        with self._lock:
            self.chunks = []
            self.provider_name = ""
            self._idf_cache = None
            if not self.path.exists():
                return
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return
            self.provider_name = data.get("provider", "")
            self.chunks = [Chunk.from_dict(c) for c in data.get("chunks", [])]

    def save(self) -> None:
        with self._lock:
            ensure_dirs()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "provider": self.provider_name,
                "dims": len(self.chunks[0].embedding) if self.chunks else 0,
                "count": len(self.chunks),
                "chunks": [c.to_dict() for c in self.chunks],
            }
            self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def clear(self) -> None:
        with self._lock:
            self.chunks = []
            self.provider_name = ""
            self._idf_cache = None

    # --------------------------------------------------------------- write
    def upsert(self, chunks: Iterable[Chunk], provider_name: str) -> int:
        with self._lock:
            index = {c.chunk_id: i for i, c in enumerate(self.chunks)}
            added = 0
            for chunk in chunks:
                if chunk.chunk_id in index:
                    self.chunks[index[chunk.chunk_id]] = chunk
                else:
                    self.chunks.append(chunk)
                    added += 1
            self.provider_name = provider_name
            self._idf_cache = None
            return added

    def remove_document(self, doc_id: str) -> int:
        """Drop every chunk of one document. Returns how many went."""
        with self._lock:
            before = len(self.chunks)
            self.chunks = [c for c in self.chunks if c.doc_id != doc_id]
            removed = before - len(self.chunks)
            if removed:
                self._idf_cache = None
            return removed

    def document_ids(self) -> List[str]:
        with self._lock:
            return sorted({c.doc_id for c in self.chunks})

    # ---------------------------------------------------------------- read
    def is_stale(self) -> bool:
        """True when the index was built by a different embedding provider."""
        return bool(self.chunks) and self.provider_name != provider.name

    def _idf(self) -> Dict[str, float]:
        """Inverse document frequency over chunks; rare words should decide ties."""
        if self._idf_cache is None:
            total = max(len(self.chunks), 1)
            counts: Dict[str, int] = {}
            for chunk in self.chunks:
                for word in _keywords(chunk.title + " " + chunk.text):
                    counts[word] = counts.get(word, 0) + 1
            self._idf_cache = {
                word: math.log((total + 1) / (count + 0.5))
                for word, count in counts.items()
            }
        return self._idf_cache

    def search(self, query: str, query_vector: Sequence[float], top_k: int = 5,
               topics: Optional[Sequence[str]] = None,
               min_score: float = 0.05, audience: str = "",
               doc_ids: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.chunks:
                return []
            q_words = _keywords(query, drop_stopwords=True)
            idf = self._idf()
            default_idf = math.log(len(self.chunks) + 1)
            q_weight = sum(idf.get(w, default_idf) for w in q_words) or 1.0
            wanted = {t.lower() for t in (topics or [])}
            allowed = set(doc_ids) if doc_ids is not None else None
            scored: List[Dict[str, Any]] = []
            for chunk in self.chunks:
                # Access first, relevance second. A chunk the caller may not read is
                # not a low-scoring result, it is not a result.
                if audience and chunk.audience not in ("both", audience):
                    continue
                if allowed is not None and chunk.doc_id not in allowed:
                    continue
                if wanted and not wanted.intersection({t.lower() for t in chunk.topics}):
                    continue
                vector_score = cosine(query_vector, chunk.embedding) if query_vector else 0.0
                chunk_words = _keywords(chunk.title + " " + chunk.text)
                matched = q_words & chunk_words
                lexical = sum(idf.get(w, default_idf) for w in matched) / q_weight
                title_words = _keywords(chunk.title, drop_stopwords=True)
                title_bonus = 0.15 * (len(q_words & title_words) / max(len(q_words), 1))
                topic_bonus = 0.08 if wanted else 0.0
                score = 0.45 * vector_score + 0.55 * lexical + title_bonus + topic_bonus * min(lexical * 3, 1.0)
                if score < min_score:
                    continue
                scored.append({
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "title": chunk.title,
                    "text": chunk.text,
                    "topics": chunk.topics,
                    "source": chunk.source,
                    "audience": chunk.audience,
                    "section": chunk.section,
                    "position": chunk.position,
                    "score": round(score, 4),
                    "vector_score": round(vector_score, 4),
                    "lexical_score": round(lexical, 4),
                })
            scored.sort(key=lambda d: d["score"], reverse=True)
            return scored[:top_k]

    # --------------------------------------------------------------- stats
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            docs = {c.doc_id for c in self.chunks}
            topics: Dict[str, int] = {}
            for chunk in self.chunks:
                for topic in chunk.topics:
                    topics[topic] = topics.get(topic, 0) + 1
            return {
                "documents": len(docs),
                "chunks": len(self.chunks),
                "provider": self.provider_name or provider.name,
                "active_provider": provider.name,
                "dims": len(self.chunks[0].embedding) if self.chunks else 0,
                "stale": self.is_stale(),
                "topics": dict(sorted(topics.items(), key=lambda kv: -kv[1])),
                "index_file": str(self.path),
            }


store = VectorStore()
