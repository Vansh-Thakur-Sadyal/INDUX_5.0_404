"""Which model does which job, and why.

Saarthi uses two language models, and the split is not a preference - it is what
each one measurably does better. Everything here was measured from the dashboard
host against this account, same prompts, connection already open.

**Talking to a farmer on the phone.** Sarvam, by a long way.

    sarvam-105b-conversations    286 ms median over four turns
    ministral-8b-latest          755 ms median  (+469 ms)

The latency is not the main point. Asked "namaste", ministral-8b replied
"Namaste ji, kaun hai aap? Mujhe pata chal raha hai ki aap kaun hai" - it asked
the caller who they were. Told "kal phir baat karte hain" it answered "aaj kaam
ka pressure hai... Kya aapka paan hai?". Sarvam answered both correctly and in
half the time.

**Speaking a number it was handed.** Sarvam again, with a check.

    sarvam-105b-conversations    312 ms, kept every figure
    ministral-8b-latest          735 ms, kept every figure but emitted markdown
                                 into a reply that was going to be read aloud

Sarvam is trusted to SAY the figures, never to work them out: the agents compute,
and ``unsupported_figures`` checks the spoken reply against what it was given. A
reply with a figure nobody supplied is discarded and Mistral phrases it instead.
That is the fusion that matters - the fast, natural model speaks, and it does not
get to invent.

**Turning a Hinglish or Devanagari question into English search terms.** Sarvam,
when there is time for it.

    sarvam-105b     3.3 s   "wheat lower leaf chlorosis", "wheat yellowing of
                             lower leaves"
    ministral-8b    1.6 s   "symptoms of wheat lodging caused by basal leaf
                             infection in rainfed conditions"

For "mera gehu pila ho raha hai neeche ki pattiyon se". Lodging is the crop
falling over; the farmer described yellowing. Mistral rewrote the question into a
different problem, which is worse than not rewriting it at all. Sarvam needs
``max_tokens`` of at least 900 - it is a reasoning model and spends the budget on
``reasoning_content`` before it writes anything, returning nothing at all below
that.

**Everything that reasons, cites, or returns JSON.** Mistral, alone.

Answer generation with citations, reranking, fact extraction, UI translation and
every embedding. Sarvam has no embedding endpoint, so retrieval is Mistral's
whichever way one would want it.

Each choice can be overridden in ``config/api_keys.env`` without touching code,
because the right answer here will change as both providers do - and the next
person to measure should be able to act on it in one line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..core.config import get_settings

MISTRAL = "mistral"
SARVAM = "sarvam"

# Sarvam's two chat models. The conversation one is fast and speaks like a
# person; the other reasons and is slow, and is only worth its latency off the
# critical path.
SARVAM_CONVERSATION = "sarvam-105b-conversations"
SARVAM_REASONING = "sarvam-105b"

# Mistral's, in the sizes this account can actually use.
MISTRAL_FAST = "ministral-8b-latest"
MISTRAL_MAIN = "ministral-14b-latest"

# sarvam-105b returns nothing at all below roughly this: it is a reasoning model
# and the budget goes on reasoning_content first. Measured: 400 tokens produced
# an empty reply, 900 produced the answer.
SARVAM_REASONING_TOKENS = 900


@dataclass(frozen=True)
class Choice:
    """A provider and model for one job, and whether its output is checked."""

    provider: str
    model: str
    fallback: Optional[str] = None
    fallback_model: str = ""
    verify_figures: bool = False
    min_tokens: int = 0

    @property
    def is_sarvam(self) -> bool:
        return self.provider == SARVAM


# A question written in an Indian script, or in Hinglish, is one Sarvam
# understands natively. Devanagari and the southern scripts are unambiguous;
# Hinglish is detected from words a farmer actually types, because "my wheat is
# yellow" and "mera gehu pila hai" need different rewriters and look alike to a
# language detector working on four words.
_INDIC_SCRIPT = re.compile(r"[ऀ-ൿ]")
_HINGLISH_HINTS = {
    "mera", "meri", "mere", "kya", "kaise", "kab", "kitna", "kitni", "hai", "hain",
    "karna", "karo", "chahiye", "nahi", "nahin", "ho", "raha", "rahi", "rahe",
    "gehu", "gehun", "sarson", "dhan", "chana", "kapas", "bajra", "makka",
    "khet", "mitti", "paani", "khad", "dawa", "fasal", "beej", "sinchai",
    "pila", "pili", "sookh", "keeda", "keede", "bhav", "mandi", "bigha",
}


def is_indic(text: str) -> bool:
    """True when the question is in an Indian script or in Hinglish."""
    body = (text or "").strip()
    if not body:
        return False
    if _INDIC_SCRIPT.search(body):
        return True
    words = set(re.findall(r"[a-z]+", body.lower()))
    return len(words & _HINGLISH_HINTS) >= 2


def sarvam_usable() -> bool:
    """False only when Sarvam is known to refuse - an account with no credit.

    Deliberately NOT "is a key configured". A missing key is the client's
    business and the fallback handles it in the usual way; these choices are a
    statement of which model is better at which job, and that stays true
    whatever this particular deployment has set up. What must change the
    routing is a refusal that costs real time: an empty account answers HTTP
    402 to everything, fast, and asking it first spends a third of a second of
    a two-second phone turn - twice, since speech asks too.
    """
    from .sarvam_client import out_of_credit
    return not out_of_credit()


def _override(key: str) -> str:
    """A provider named in config, or "" to use the measured default."""
    value = get_settings().get(key, "").strip().lower()
    return value if value in (MISTRAL, SARVAM) else ""


def voice_chat() -> Choice:
    """A spoken turn that is conversation rather than agronomy."""
    wanted = _override("VOICE_CHAT_PROVIDER") or SARVAM
    usable = sarvam_usable()
    if wanted == MISTRAL:
        # Still falls back to Sarvam when it is answering: the override is a
        # preference, not a ban. An account with no credit is a different
        # thing, and there is no point naming it as the safety net.
        return Choice(MISTRAL, MISTRAL_FAST,
                      fallback=SARVAM if usable else None,
                      fallback_model=SARVAM_CONVERSATION if usable else "")
    if not usable:
        return Choice(MISTRAL, MISTRAL_FAST)
    return Choice(SARVAM, SARVAM_CONVERSATION, fallback=MISTRAL,
                  fallback_model=MISTRAL_FAST)


def voice_data() -> Choice:
    """A spoken turn that carries figures the agents produced.

    Sarvam says them and is checked; a reply holding a figure nobody supplied is
    thrown away and Mistral phrases the same facts instead.
    """
    wanted = _override("VOICE_DATA_PROVIDER") or SARVAM
    if wanted == MISTRAL or not sarvam_usable():
        return Choice(MISTRAL, MISTRAL_FAST, verify_figures=True)
    return Choice(SARVAM, SARVAM_CONVERSATION, fallback=MISTRAL,
                  fallback_model=MISTRAL_FAST, verify_figures=True)


def query_rewrite(question: str) -> Choice:
    """The model that turns a question into search terms.

    Chosen by the language of the question, not by a global setting: Sarvam earns
    its extra 1.7 s on Hinglish and Devanagari and earns nothing on English.
    """
    forced = _override("REWRITE_PROVIDER")
    if forced == MISTRAL or not sarvam_usable():
        return Choice(MISTRAL, MISTRAL_FAST)
    if forced == SARVAM or is_indic(question):
        return Choice(SARVAM, SARVAM_REASONING, fallback=MISTRAL,
                      fallback_model=MISTRAL_FAST,
                      min_tokens=SARVAM_REASONING_TOKENS)
    return Choice(MISTRAL, MISTRAL_FAST)


def call_summary() -> Choice:
    """One sentence written at hang-up. Short, Indic, and nobody is waiting."""
    wanted = _override("SUMMARY_PROVIDER") or SARVAM
    if wanted == MISTRAL or not sarvam_usable():
        return Choice(MISTRAL, MISTRAL_FAST)
    return Choice(SARVAM, SARVAM_CONVERSATION, fallback=MISTRAL,
                  fallback_model=MISTRAL_FAST)


# These are Mistral's outright, and are functions rather than constants so the
# reason travels with them.
def fact_extraction() -> Choice:
    """Durable facts out of a sentence, as strict JSON. Mistral has a JSON mode."""
    return Choice(MISTRAL, MISTRAL_FAST)


def rag_answer() -> Choice:
    """The answer from retrieved passages, with citations, streamed."""
    return Choice(MISTRAL, MISTRAL_FAST)


def rerank() -> Choice:
    """Scoring passages 0-10 as JSON."""
    return Choice(MISTRAL, MISTRAL_FAST)


def describe() -> list:
    """What is routed where, for the dashboard and the console to show."""
    return [
        {"job": "Phone conversation", "provider": SARVAM, "model": SARVAM_CONVERSATION,
         "why": "286 ms against 755 ms, and it speaks like a person in Hindi."},
        {"job": "Phone answer with figures", "provider": SARVAM,
         "model": SARVAM_CONVERSATION,
         "why": "Fast and natural, and every figure it speaks is checked against "
                "what the agents produced."},
        {"job": "Query rewriting (Indian languages)", "provider": SARVAM,
         "model": SARVAM_REASONING,
         "why": "It reads Hinglish natively. Mistral rewrote 'yellowing lower "
                "leaves' as 'lodging', which is a different problem."},
        {"job": "Query rewriting (English)", "provider": MISTRAL, "model": MISTRAL_FAST,
         "why": "Half the latency, and no Indic advantage to pay for."},
        {"job": "Answers with citations", "provider": MISTRAL, "model": MISTRAL_FAST,
         "why": "Citation discipline, and it streams."},
        {"job": "Reranking and fact extraction", "provider": MISTRAL,
         "model": MISTRAL_FAST,
         "why": "Both want strict JSON back. Mistral has a JSON mode; Sarvam "
                "wraps its answer in prose and has to be unpicked."},
        {"job": "Embeddings and retrieval", "provider": MISTRAL, "model": "mistral-embed",
         "why": "Sarvam has no embedding endpoint."},
        {"job": "Speech in and out", "provider": SARVAM, "model": "bulbul / saarika",
         "why": "Indian-language voices are why it was chosen."},
    ]
