"""Q1 of the architecture: turn what the farmer typed into what retrieval needs.

The question a farmer asks and the sentence a document uses are rarely the same
words. "mera gehu pila ho raha hai" has to find a passage about nitrogen
deficiency and yellowing in wheat, and no amount of embedding fixes a query that
never mentions either.

Three strategies, all from the architecture, and all optional because each one
costs a model call:

``expand``     multi-query. Two or three alternative phrasings, searched together
               and merged. The cheapest reliable win: it covers the case where the
               farmer's wording and the document's wording simply differ.
``hyde``       write a short hypothetical answer and search with THAT. An answer
               looks like the passage that contains it, so it matches far better
               than a question does. Best for vague questions.
``step_back``  ask the general question behind the specific one ("when does wheat
               need nitrogen" behind "why is my wheat yellow at 40 days"). Good
               when the specific question is too narrow to match anything.

None of them run on the phone line. A rewrite costs seconds, which is the whole
spoken turn budget, and the keyword normaliser already in ``conversation.intent``
handles the common cases for free. They run in the console, and on the dashboard
when the first search found nothing - which is exactly when spending the time is
worth it.

Which model rewrites is decided by the language of the question, in ``llm.router``,
and the gap is large enough to matter. Given "mera gehu pila ho raha hai neeche ki
pattiyon se", sarvam-105b returned "wheat lower leaf chlorosis"; ministral-8b
returned "symptoms of wheat lodging caused by basal leaf infection in rainfed
conditions". Lodging is the crop falling over - the farmer described yellowing.
A rewrite into a different problem is worse than no rewrite at all, so Indian
languages go to Sarvam even though it costs about 1.7 s more.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..llm import router
from ..llm.mistral_client import client as mistral
from ..llm.sarvam_client import client as sarvam

log = logging.getLogger("saarthi.rag.rewrite")

STRATEGIES = ("none", "expand", "hyde", "step_back")

_EXPAND_PROMPT = (
    "You rewrite an Indian farmer's question into search queries for an agricultural "
    "knowledge base.\n"
    "Return strict JSON: {\"queries\": [\"...\", \"...\"]}\n"
    "Rules:\n"
    "- 2 or 3 queries, each a short noun phrase a document heading would use.\n"
    "- Use the agronomic term for what the farmer described: yellow lower leaves "
    "becomes nitrogen deficiency; white powder on leaves becomes powdery mildew.\n"
    "- Keep the crop name, the stage and any number exactly as given.\n"
    "- Translate Hindi or Hinglish into English agronomic terms, keeping crop names.\n"
    "- Invent nothing the farmer did not imply. No advice, no answers."
)

_HYDE_PROMPT = (
    "Write the two-sentence passage that would ANSWER this Indian farmer's question, "
    "as an agricultural extension manual would word it.\n"
    "This text is used only to search a document collection - it is never shown to "
    "anyone, so being approximately right in the right vocabulary is what matters.\n"
    "Use no specific numbers: a dose or a price you made up would pull the search "
    "towards documents about that wrong figure. Describe the topic, not the values."
)

_STEP_BACK_PROMPT = (
    "Give the single more general question that lies behind this farmer's specific "
    "one. One short question, nothing else, no preamble.\n"
    "Example: \"why is my wheat yellow 40 days after sowing\" becomes \"when does "
    "wheat need nitrogen\"."
)


@dataclass
class Rewritten:
    strategy: str
    queries: List[str] = field(default_factory=list)
    note: str = ""
    model: str = ""
    latency_ms: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {"strategy": self.strategy, "queries": self.queries, "note": self.note,
                "model": self.model, "latency_ms": self.latency_ms}



async def _think(question: str, system: str, *, temperature: float, max_tokens: int,
                 want_json: bool) -> Tuple[Optional[Dict[str, Any]], str, str]:
    """Ask whichever model should rewrite THIS question.

    Returns the parsed JSON (when asked for), the raw text, and which provider
    answered. A failure returns empty rather than raising: a rewrite that did not
    happen costs recall, and the original question is always searched anyway.
    """
    choice = router.query_rewrite(question)
    attempts = [(choice.provider, choice.model)]
    if choice.fallback:
        attempts.append((choice.fallback, choice.fallback_model))

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": question}]
    budget = max(max_tokens, choice.min_tokens)

    for provider, model in attempts:
        if provider == router.SARVAM:
            if not sarvam.configured():
                continue
            # sarvam-105b is a reasoning model: below about 900 tokens it spends
            # the whole budget on reasoning_content and returns nothing at all.
            result = await sarvam.chat(messages, temperature=temperature,
                                       max_tokens=max(budget,
                                                      router.SARVAM_REASONING_TOKENS),
                                       model=model)
            ok, text = result.ok, result.text
        else:
            if not mistral.configured():
                continue
            reply = await mistral.chat(messages, temperature=temperature,
                                       max_tokens=max_tokens, model=model,
                                       json_mode=want_json)
            ok, text = reply.ok, reply.text

        if not ok or not (text or "").strip():
            log.info("%s produced no rewrite for %r", provider, question[:50])
            continue
        if not want_json:
            return None, text, provider
        parsed = _parse_json(text)
        if parsed is not None:
            return parsed, text, provider
        log.info("%s returned unparseable JSON for a rewrite", provider)

    return None, "", ""


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    """The object in the reply, however much prose is wrapped around it.

    Sarvam is not asked for a JSON mode because it has none, so it sometimes
    explains itself first. Taking the braces is more reliable than insisting.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _clean_query(text: str) -> str:
    """Strip the list markers and markdown a chat model adds by habit.

    The asterisks matter rather than being cosmetic: "**moderate irrigation**"
    tokenises differently from "moderate irrigation", so emphasis the model
    added for a reader it does not have changes what the search matches.
    """
    text = re.sub(r"^\s*[-*\d.)\]]+\s*", "", (text or "").strip())
    text = re.sub(r"[*_`#]+", "", text)
    text = text.strip(" \"'")
    return re.sub(r"\s+", " ", text)[:240]


async def rewrite(question: str, strategy: str = "expand",
                  language: str = "en") -> Rewritten:
    """Produce the queries to search with. The original is always one of them.

    Keeping the original is not a formality. A rewrite can drift - asked about
    "jhulsa" it once produced queries about leaf blight in rice when the farmer
    grows wheat - and the farmer's own words are the one query guaranteed to be
    about what they asked.
    """
    original = (question or "").strip()
    if not original:
        return Rewritten(strategy="none", queries=[], note="There was no question.")
    if strategy not in STRATEGIES or strategy == "none":
        return Rewritten(strategy="none", queries=[original],
                         note="Searched with the question as typed.")
    if not (mistral.configured() or sarvam.configured()):
        return Rewritten(strategy="none", queries=[original],
                         note="Query rewriting needs a language model; searched with "
                              "the question as typed.")

    try:
        if strategy == "expand":
            payload, _, provider = await _think(
                original, _EXPAND_PROMPT, temperature=0.2, max_tokens=180,
                want_json=True)
            extra = [_clean_query(q) for q in (payload or {}).get("queries", [])
                     if isinstance(q, str)]
            extra = [q for q in extra if q and q.lower() != original.lower()][:3]
            if not extra:
                return Rewritten(strategy="expand", queries=[original],
                                 note="The rewriter produced nothing usable; searched "
                                      "with the question as typed.", model=provider)
            return Rewritten(strategy="expand", queries=[original] + extra,
                             model=provider,
                             note="Searched with the question plus %d rephrasing(s), "
                                  "written by %s." % (len(extra), provider or "nobody"))

        if strategy == "hyde":
            _, text, provider = await _think(original, _HYDE_PROMPT, temperature=0.3,
                                             max_tokens=140, want_json=False)
            if not text.strip():
                return Rewritten(strategy="none", queries=[original],
                                 note="The hypothetical answer could not be written; "
                                      "searched with the question as typed.")
            return Rewritten(strategy="hyde",
                             queries=[original, _clean_query(text)[:600]],
                             model=provider,
                             note="Searched with the question and with a hypothetical "
                                  "answer written for the purpose, by %s." % provider)

        _, text, provider = await _think(original, _STEP_BACK_PROMPT, temperature=0.2,
                                         max_tokens=60, want_json=False)
        broader = _clean_query(text)
        if not broader:
            return Rewritten(strategy="none", queries=[original],
                             note="No broader question was produced; searched with the "
                                  "question as typed.")
        return Rewritten(strategy="step_back", queries=[original, broader],
                         model=provider,
                         note="Searched with the question and with the broader question "
                              "behind it: " + broader)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("query rewrite failed: %s", exc)
        return Rewritten(strategy="none", queries=[original],
                         note="Query rewriting failed (%s); searched with the question "
                              "as typed." % str(exc)[:90])
