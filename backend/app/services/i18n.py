"""Translating the interface itself.

The answers Saarthi writes already come back in the farmer's language - the model is
told which one to use. The interface around them did not: a farmer who set Hindi still
read "Crop growing in this field" and "Run pipeline" in English, which is most of what
is on screen before a single answer appears.

Hand-writing a catalogue for thirteen languages was the obvious approach and the wrong
one: it is thousands of strings, it goes stale the moment any label changes, and it
cannot cover a language that gets added later. So the strings are translated on demand
and **cached in the database**, keyed by the exact source text. A string is translated
once per language, ever.

Three things keep this honest:

  * English is the source of truth and is never translated or stored. Switching back
    to English restores the originals exactly, so nothing is lost in a round trip.
  * a translation that fails returns the ENGLISH string, not an empty one or a
    mangled one. A farmer seeing an English label has a minor annoyance; a farmer
    seeing a blank button has a broken dashboard.
  * numbers, units and proper nouns are left alone by instruction, because a model
    that "translates" 1800 or pH into something else is worse than no translation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from ..core.storage import cursor
from ..llm import mistral_client
from . import translator

log = logging.getLogger("saarthi.services")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ui_strings (
    lang        TEXT NOT NULL,
    digest      TEXT NOT NULL,
    source      TEXT NOT NULL,
    translated  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (lang, digest)
);
"""

# How many strings go to the model in one request. Large enough that a whole screen
# is one call, small enough that a failure does not cost the entire page.
BATCH = 40

# The pipeline advisory is a several-paragraph answer and is the single most
# important block on the screen for a farmer reading in Hindi or Bengali. At 400
# it was silently dropped, so the page translated around it and left the advice
# itself in English. The client sends anything this long in a request of its own.
MAX_LEN = 3000


def ensure_schema() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def cached(lang: str, texts: List[str]) -> Dict[str, str]:
    """Whatever is already translated for this language."""
    ensure_schema()
    if not texts:
        return {}
    digests = {_digest(t): t for t in texts}
    out: Dict[str, str] = {}
    marks = ",".join("?" * len(digests))
    with cursor() as cur:
        cur.execute(
            "SELECT digest, translated FROM ui_strings WHERE lang=? AND digest IN (%s)"
            % marks, [lang, *digests.keys()])
        for row in cur.fetchall():
            source = digests.get(row[0])
            if source is not None:
                out[source] = row[1]
    return out


def store(lang: str, pairs: Dict[str, str]) -> None:
    from datetime import datetime, timezone

    ensure_schema()
    now = datetime.now(timezone.utc).isoformat()
    with cursor() as cur:
        for source, translated in pairs.items():
            if not translated or translated == source:
                continue
            cur.execute(
                "INSERT OR REPLACE INTO ui_strings (lang, digest, source, translated,"
                " created_at) VALUES (?,?,?,?,?)",
                (lang, _digest(source), source, translated, now))


def _prompt(lang_name: str, items: List[str]) -> List[Dict[str, str]]:
    """Ask for an object keyed by index, not a bare array.

    An array has to come back at exactly the right length or it cannot be trusted -
    one dropped line shifts every label after it onto the wrong control, so the only
    safe response was to throw the whole batch away. That cost thirty-six labels at a
    time. Keyed by index, a missing entry costs exactly that one label: everything
    else still lands, and nothing can be misfiled.
    """
    system = (
        "You translate short user-interface labels for an Indian farming dashboard "
        "into %s.\n"
        "The user sends a JSON object: each key is a number, each value is English "
        "text.\n"
        "Rules:\n"
        "1. Return ONLY a JSON object with the SAME keys. Each value is that "
        "string translated. Do not add, drop or renumber keys.\n"
        "2. Translate the meaning, not word by word. Most of these are buttons, "
        "headings and field labels, so keep them SHORT - a label that doubles in "
        "length breaks the layout. A few are full paragraphs of advice; translate "
        "those completely, keeping the line breaks.\n"
        "3. Do NOT translate: numbers, units (kg, mm, dS/m, pH, quintal, acre), "
        "scheme names and their abbreviations (PM-KISAN, KCC, PMFBY), place names, "
        "or the product name Saarthi.\n"
        "4. Use the words a farmer uses, not formal or literary vocabulary.\n"
        "5. If a string should stay as it is, return it unchanged."
        % lang_name)

    payload = {str(i): text for i, text in enumerate(items)}
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _parse_reply(text: str, chunk: List[str]) -> Dict[str, str]:
    """Pull whatever the model got right out of its reply.

    Accepts the keyed object, and still accepts a plain array of the exact right
    length so an older cached shape or a model that ignores the instruction is not
    wasted. Anything it cannot place with certainty is simply left out.
    """
    try:
        values = json.loads(text.strip().strip("`").lstrip("json").strip())
    except (ValueError, AttributeError):
        return {}

    pairs: Dict[str, str] = {}
    if isinstance(values, dict):
        for key, val in values.items():
            try:
                index = int(str(key).strip())
            except ValueError:
                continue
            if not 0 <= index < len(chunk):
                continue
            if isinstance(val, (str, int, float)) and str(val).strip():
                pairs[chunk[index]] = str(val)
    elif isinstance(values, list) and len(values) == len(chunk):
        # Only safe at the exact length: a short array shifts every later label
        # onto the wrong control.
        for src, val in zip(chunk, values):
            if isinstance(val, (str, int, float)) and str(val).strip():
                pairs[src] = str(val)
    return pairs


async def _translate_chunk(lang: str, lang_name: str,
                           chunk: List[str]) -> Dict[str, str]:
    """One model call, retried. Returns {} if it could not be translated at all.

    The free tier answers 429 Rate limit exceeded under load, and a rate-limited
    chunk used to fall straight through to the English fallback - which is how a
    screen ended up half translated with no error anywhere. A short wait and a
    second attempt clears almost all of them.
    """
    # Budget by characters, not by how many strings there are. Sixty tokens per
    # string is plenty for a button and far too little for a paragraph, so a long
    # advisory came back truncated, failed to parse as JSON, and fell through to
    # English. Indic scripts tokenise badly too - Devanagari costs several tokens a
    # character - hence the generous multiplier.
    budget = int(sum(len(t) for t in chunk) * 3) + 300
    max_tokens = max(600, min(8000, budget))

    for attempt in range(3):
        result = await mistral_client.client.chat(
            _prompt(lang_name or lang, chunk), temperature=0.1,
            max_tokens=max_tokens)
        if result.ok and result.text:
            pairs = _parse_reply(result.text, chunk)
            # Most of the batch is enough to be worth keeping; the stragglers stay
            # English and will be asked for again on the next pass.
            if len(pairs) >= len(chunk) * 0.6:
                if len(pairs) < len(chunk):
                    log.info("Translation to %s returned %d of %d string(s); the "
                             "rest stay in English.", lang, len(pairs), len(chunk))
                return pairs
        if attempt < 2:
            await asyncio.sleep(1.5 * (attempt + 1))
    log.info("Translation to %s failed for %d string(s) after 3 attempts; "
             "they stay in English.", lang, len(chunk))
    return {}


async def translate(lang: str, texts: List[str],
                    lang_name: str = "") -> Dict[str, Any]:
    """Translate what is not already cached. Returns every string either way.

    Anything that cannot be translated comes back as its English self, so the caller
    can apply the whole map without checking for holes.
    """
    clean = [t.strip() for t in texts if t and t.strip() and len(t) <= MAX_LEN]
    clean = list(dict.fromkeys(clean))                 # de-duplicate, keep order
    if not clean or lang in ("", "en"):
        return {"language": lang, "translations": {}, "cached": 0, "fetched": 0,
                "note": "English is the source; nothing to translate."}

    have = cached(lang, clean)
    missing = [t for t in clean if t not in have]
    fetched = 0
    engine = ""

    # Azure Translator first: one request takes eighty strings, answers in
    # under half a second, and returns exactly one translation per input in
    # order - none of which is true of a language model asked for JSON. The
    # model stays as the fallback for whatever Azure could not do, so a
    # missing key or a refused request costs latency and not the screen.
    if missing and translator.configured():
        pairs = await translator.translate(lang, missing)
        if pairs:
            store(lang, pairs)
            have.update(pairs)
            # A name that came back as itself was HANDLED, not missed. It is not
            # counted as fetched - nothing was translated - but it must not go on
            # to the model, which would translate the name.
            fetched += sum(1 for s, t in pairs.items() if t != s)
            engine = "azure-translator"
            missing = [t for t in missing if t not in pairs]

    if missing and mistral_client.client.configured():
        for start in range(0, len(missing), BATCH):
            chunk = missing[start:start + BATCH]
            pairs = await _translate_chunk(lang, lang_name, chunk)
            if pairs:
                store(lang, pairs)
                have.update(pairs)
                fetched += len(pairs)
                engine = (engine + "+model") if engine else "model"

    # Every requested string gets an entry, falling back to English.
    full = {t: have.get(t, t) for t in clean}
    return {
        "language": lang,
        "translations": full,
        "cached": len(clean) - fetched,
        "fetched": fetched,
        "complete": all(full[t] != t or t in have for t in clean),
        "engine": engine or "cache",
        "note": ("Translated by Azure Translator and cached, so each string is "
                 "translated once. Anything it could not translate is shown in "
                 "English rather than left blank."),
    }
