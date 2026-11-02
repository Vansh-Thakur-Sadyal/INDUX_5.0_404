"""Embedding providers for the RAG pipeline.

Two providers, one interface:

* ``mistral``  - Mistral ``mistral-embed`` (1024 dims). Used when a key is
  configured. This is the production path described in the write-up.
* ``offline``  - a deterministic hashed bag-of-words vector (384 dims) with
  sub-word shingles so it still matches morphological variants. No network, no
  key, reproducible across runs. Every retrieval result carries the provider
  name, so a demo can never be mistaken for semantic search.

Switching providers invalidates the index, so the store records which provider
built it and re-embeds when they differ.
"""
from __future__ import annotations

import hashlib
import math
import re
import time
from typing import List, Optional, Sequence, Tuple

from ..llm.mistral_client import client as mistral

OFFLINE_DIMS = 384
_TOKEN_RE = re.compile(r"[a-z0-9ऀ-ൿ]+")

# Light English stemming: strip a few common suffixes so "irrigation" and
# "irrigate" share tokens. Deliberately crude - it only feeds the hash.
_SUFFIXES = ("ingly", "ation", "ions", "ing", "ers", "er", "es", "s", "ed", "ly")


def _tokenize(text: str) -> List[str]:
    tokens: List[str] = []
    for raw in _TOKEN_RE.findall((text or "").lower()):
        tokens.append(raw)
        for suffix in _SUFFIXES:
            if len(raw) > len(suffix) + 3 and raw.endswith(suffix):
                tokens.append(raw[: -len(suffix)])
                break
        if len(raw) > 5:                      # a prefix shingle helps Indic text
            tokens.append(raw[:5])
    return tokens


def _bucket(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") % OFFLINE_DIMS


def offline_embed(text: str) -> List[float]:
    """Deterministic hashed term-frequency vector, L2 normalised."""
    vector = [0.0] * OFFLINE_DIMS
    tokens = _tokenize(text)
    if not tokens:
        return vector
    for token in tokens:
        idx = _bucket(token)
        # sub-linear term frequency keeps long documents from dominating
        vector[idx] += 1.0
    for i, value in enumerate(vector):
        if value:
            vector[i] = 1.0 + math.log(value)
    norm = math.sqrt(sum(v * v for v in vector))
    if norm:
        vector = [v / norm for v in vector]
    return vector


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class EmbeddingProvider:
    """Resolves to Mistral when configured, otherwise to the offline embedder."""

    def __init__(self, force: Optional[str] = None) -> None:
        self.force = force
        self.last_fallback_at: Optional[float] = None

    @property
    def name(self) -> str:
        if self.force:
            return self.force
        return "mistral" if mistral.configured() else "offline"

    @property
    def dims(self) -> int:
        return 1024 if self.name == "mistral" else OFFLINE_DIMS

    async def embed_with_provider(self, texts: Sequence[str], *,
                                  urgent: bool = False
                                  ) -> Tuple[List[List[float]], str]:
        """Embed, and say which provider actually produced the vectors.

        A failed Mistral call falls back to the offline embedder for THIS call
        only. It used to latch the provider to offline for the rest of the
        process, so one rate-limited request quietly downgraded retrieval until
        a restart - and the index kept claiming it was built with Mistral.
        """
        texts = list(texts)
        if not texts:
            return [], self.name
        if self.name == "mistral":
            # ``urgent`` on a retrieval someone is waiting for: one attempt, then
            # keyword-only search. Three retries with backoff would spend four
            # seconds before the reply could even start being written.
            vectors = await mistral.embed(texts, urgent=urgent)
            if vectors and len(vectors) == len(texts):
                return vectors, "mistral"
            self.last_fallback_at = time.time()
        return [offline_embed(t) for t in texts], "offline"

    async def embed(self, texts: Sequence[str], *,
                    urgent: bool = False) -> List[List[float]]:
        vectors, _ = await self.embed_with_provider(texts, urgent=urgent)
        return vectors


provider = EmbeddingProvider()
