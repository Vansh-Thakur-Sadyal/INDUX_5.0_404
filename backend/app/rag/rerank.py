"""Stage 5: merge what the searches found, and put the right passage first.

Retrieval produces candidates. Reranking decides which of them the model actually
sees, and it matters more than it looks: the prompt has room for four or five
passages, so a correct passage ranked seventh is a passage that does not exist.

Three steps, applied in this order.

**Fusion.** Multi-query search returns one ranked list per query. They are combined
with reciprocal rank fusion - each list votes with ``1/(k+rank)`` - rather than by
comparing scores. Scores from two different queries are not on the same scale and
averaging them lets one oddly-worded rewrite dominate; ranks are comparable by
construction.

**Diversity.** Five chunks from the same PDF look like five sources and are one.
A maximal-marginal-relevance pass trades a little relevance for coverage, so the
model sees the irrigation note AND the fertiliser note instead of five paragraphs
of the same chapter.

**Judgement, optional.** A model call that scores each candidate against the
question. It is the most accurate step and the slowest, so it is off by default
and on in the console, where somebody is deliberately testing retrieval quality.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Sequence

from ..llm.mistral_client import client as mistral

log = logging.getLogger("saarthi.rag.rerank")

RERANK_MODEL = "ministral-8b-latest"

# The constant in reciprocal rank fusion. 60 is the value from the original
# paper and it behaves well here: large enough that rank 1 and rank 2 are not
# wildly apart, small enough that rank 20 stops mattering.
RRF_K = 60


def fuse(result_sets: Sequence[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Reciprocal rank fusion over one ranked list per query."""
    if not result_sets:
        return []
    if len(result_sets) == 1:
        return [dict(hit) for hit in result_sets[0]]

    merged: Dict[str, Dict[str, Any]] = {}
    for results in result_sets:
        for rank, hit in enumerate(results, 1):
            key = hit["chunk_id"]
            entry = merged.get(key)
            if entry is None:
                entry = dict(hit)
                entry["fusion_score"] = 0.0
                entry["found_by"] = 0
                merged[key] = entry
            entry["fusion_score"] += 1.0 / (RRF_K + rank)
            entry["found_by"] += 1
            # Keep the best single-query score too; the console shows both, and
            # "found by three rewrites but never scored well" is a useful thing
            # to be able to see.
            entry["score"] = max(entry.get("score", 0.0), hit.get("score", 0.0))

    out = sorted(merged.values(), key=lambda h: h["fusion_score"], reverse=True)
    for hit in out:
        hit["fusion_score"] = round(hit["fusion_score"], 5)
    return out


_WORD = re.compile(r"[a-z0-9ऀ-ൿ]{3,}")


def _words(text: str) -> set:
    return set(_WORD.findall((text or "").lower()))


def _similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """How much two candidates repeat each other. Jaccard over content words."""
    wa, wb = _words(a.get("text", "")), _words(b.get("text", ""))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def diversify(hits: Sequence[Dict[str, Any]], keep: int = 5,
              lambda_: float = 0.72, per_document: int = 2) -> List[Dict[str, Any]]:
    """Maximal marginal relevance, with a hard cap per document.

    ``lambda_`` is the weight on relevance against novelty; 0.72 was chosen by
    reading the output, not by tuning a metric - below about 0.6 the top result
    starts getting displaced by something merely different.

    ``per_document`` is the blunt instrument that actually does the work. A long
    manual will otherwise win every slot on sheer length, because every one of its
    chunks is about the right topic.
    """
    pool = [dict(h) for h in hits]
    if not pool:
        return []
    key = "fusion_score" if "fusion_score" in pool[0] else "score"
    pool.sort(key=lambda h: h.get(key, 0.0), reverse=True)

    chosen: List[Dict[str, Any]] = []
    per_doc: Dict[str, int] = {}
    while pool and len(chosen) < keep:
        best_index, best_value = 0, -1e9
        for index, candidate in enumerate(pool):
            if per_doc.get(candidate["doc_id"], 0) >= per_document:
                continue
            relevance = candidate.get(key, 0.0)
            novelty = max((_similarity(candidate, picked) for picked in chosen),
                          default=0.0)
            value = lambda_ * relevance - (1 - lambda_) * novelty
            if value > best_value:
                best_index, best_value = index, value
        if best_value <= -1e9:                     # every document is at its cap
            break
        picked = pool.pop(best_index)
        per_doc[picked["doc_id"]] = per_doc.get(picked["doc_id"], 0) + 1
        picked["mmr_score"] = round(best_value, 5)
        chosen.append(picked)

    # Everything was capped out but we still have room: fill from what is left,
    # best first. Returning three passages when five were asked for and five exist
    # would be the diversity rule hurting the answer it is meant to help.
    if len(chosen) < keep:
        taken = {h["chunk_id"] for h in chosen}
        for candidate in pool:
            if candidate["chunk_id"] in taken:
                continue
            chosen.append(candidate)
            if len(chosen) >= keep:
                break
    return chosen


_JUDGE_PROMPT = (
    "You score how well each numbered passage answers a farmer's question.\n"
    "Return strict JSON: {\"scores\": [{\"id\": 1, \"score\": 0-10}, ...]}\n"
    "10 means the passage directly answers it. 5 means it is about the right topic "
    "but does not answer. 0 means it is unrelated.\n"
    "Score every passage you are given, by its number. Add nothing else - no "
    "explanation, no advice, no answer to the question itself."
)


async def judge(question: str, hits: Sequence[Dict[str, Any]], keep: int = 5,
                model: str = RERANK_MODEL) -> List[Dict[str, Any]]:
    """Ask the model to score each candidate, and reorder by that.

    One call for the whole batch rather than one per passage: a cross-encoder would
    be better and is not available here, and twelve calls would cost twelve round
    trips for a step that is already the slow one.

    If the model fails or answers in the wrong shape, the candidates come back in
    the order they arrived. A reranker that cannot rank must not also lose the
    results.
    """
    candidates = list(hits)[:12]
    if not candidates or not mistral.configured():
        return list(hits)[:keep]

    listing = "\n\n".join(
        "[%d] %s\n%s" % (i, hit.get("title", ""), (hit.get("text") or "")[:600])
        for i, hit in enumerate(candidates, 1))
    try:
        payload = await mistral.chat_json(
            [{"role": "system", "content": _JUDGE_PROMPT},
             {"role": "user", "content": "QUESTION: " + question +
                                         "\n\nPASSAGES:\n" + listing}],
            temperature=0.0, max_tokens=320, model=model)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("rerank judging failed: %s", exc)
        return list(hits)[:keep]

    scores: Dict[int, float] = {}
    for entry in (payload or {}).get("scores", []) or []:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("id"))
            value = float(entry.get("score"))
        except (TypeError, ValueError):
            continue
        if 1 <= index <= len(candidates):
            scores[index] = max(0.0, min(10.0, value))
    if not scores:
        return list(hits)[:keep]

    ranked: List[Dict[str, Any]] = []
    for index, hit in enumerate(candidates, 1):
        copy = dict(hit)
        copy["judge_score"] = scores.get(index)
        ranked.append(copy)
    # A passage the model did not score keeps its place rather than sinking: a
    # missing score is the judge's failure, not evidence against the passage.
    ranked.sort(key=lambda h: (h.get("judge_score") if h.get("judge_score") is not None
                               else 5.0), reverse=True)
    return ranked[:keep]


def confidence(hits: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """How much the retrieval itself should be trusted, before the model speaks.

    This is what lets an honest answer say "I do not have anything on that" instead
    of writing a confident paragraph out of three weak matches.
    """
    if not hits:
        return {"level": "none", "top_score": 0.0, "margin": 0.0,
                "note": "Nothing in the knowledge base matched this question."}
    scores = sorted((h.get("score", 0.0) for h in hits), reverse=True)
    top = scores[0]
    margin = top - (scores[1] if len(scores) > 1 else 0.0)
    if top >= 0.45:
        level = "high"
        note = "A passage matches this question closely."
    elif top >= 0.22:
        level = "medium"
        note = "The matches are about the right topic but none is a direct answer."
    else:
        level = "low"
        note = ("Only weak matches were found. An answer built on these should say "
                "that the knowledge base does not really cover it.")
    return {"level": level, "top_score": round(top, 4), "margin": round(margin, 4),
            "note": note}
