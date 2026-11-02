"""Filling a form by speaking it, instead of tapping eleven boxes in a field.

The form this was built for is the one the chain puts up before it runs: crop,
sowing date, stage, ten symptom checkboxes, free text, spend, expected yield,
expected price. A farmer standing in a field with one hand on a phone is not
going to fill that in, and the agents that need those answers say "missing
input" instead of advising - so the most useful part of the system was gated
behind the least usable part of it.

**Nothing here guesses.** A field the farmer did not mention is left empty, and
left empty it is honest: the agent says what it is missing, which is the whole
design. The failure mode to avoid is not an empty box, it is a box filled with
something plausible that the farmer never said and will not read back.

So every value is checked against the field it claims to fill, and a value that
does not survive is dropped rather than corrected:

* **a choice** must be one of the options, matched against the farmer's words
  rather than taken from the model - so "peele patte" fills "yellowing leaves"
  and nothing fills it unless something like it was said
* **a number** must actually appear in what was said, as digits or as words
  ("pandrah hazaar"), so the model can decide WHICH box fifteen thousand goes
  in but cannot decide that it was fifteen thousand
* **a date** must parse, and a date in the future for a sowing date is refused

The model's job is narrow on purpose: it maps what was said onto which field,
in a language and word order no rule set is going to cover. Everything it
returns is then verified against the farmer's own sentence.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..llm.mistral_client import client as mistral
from . import intent as intent_module
from .voice_agent import MULTIPLIERS, SPOKEN_NUMBERS

log = logging.getLogger("saarthi.formfill")

# One model call, short, off a farmer's tap. Longer than a spoken turn's budget
# because nobody is listening to silence here - they are watching boxes fill.
TIMEOUT_SECONDS = 12.0
MAX_TOKENS = 400


def _spec_of(field: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": str(field.get("key") or ""),
        "label": str(field.get("label") or field.get("key") or ""),
        "type": str(field.get("type") or "text"),
        "options": [str(o) for o in (field.get("options") or [])],
        "placeholder": str(field.get("placeholder") or ""),
    }


# ----------------------------------------------------------------- the numbers
_DIGITS = re.compile(r"\d{1,9}(?:\.\d{1,3})?")


def numbers_said(said: str) -> List[float]:
    """Every number in the sentence, written or spoken, with its multiplier.

    This is the list a filled number has to come from. The model may decide that
    fifteen thousand is the spend rather than the yield; it may not decide that
    there was a fifteen thousand.
    """
    text = (said or "").lower()
    found: List[float] = []

    for match in _DIGITS.finditer(text):
        value = float(match.group(0))
        tail = text[match.end():match.end() + 14]
        for word, factor in MULTIPLIERS.items():
            if re.match(r"\s*" + re.escape(word) + r"\b", tail):
                value *= factor
                break
        found.append(value)

    words = re.findall(r"[a-zऀ-ॿ]+", text)
    for i, word in enumerate(words):
        if word not in SPOKEN_NUMBERS:
            continue
        value = float(SPOKEN_NUMBERS[word])
        if i + 1 < len(words) and words[i + 1] in MULTIPLIERS:
            value *= MULTIPLIERS[words[i + 1]]
        found.append(value)
    return found


def _number_was_said(value: float, said: str) -> bool:
    """Was this number in the sentence? Within a rounding of it."""
    for spoken in numbers_said(said):
        if abs(spoken - value) < 0.01 or (spoken and abs(spoken - value) / spoken < 0.001):
            return True
    return False


# ------------------------------------------------------------------ the dates
_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    "janvari": 1, "farvari": 2, "march_hi": 3, "aprail": 4, "mai": 5, "jun_hi": 6,
    "julai": 7, "agast": 8, "sitambar": 9, "aktubar": 10, "navambar": 11,
    "disambar": 12,
    "जनवरी": 1, "फरवरी": 2,
    "मार्च": 3, "अप्रैल": 4,
    "मई": 5, "जून": 6, "जुलाई": 7,
    "अगस्त": 8, "सितंबर": 9,
    "अक्टूबर": 10,
    "नवंबर": 11, "दिसंबर": 12,
}


def _as_date(value: str, today: Optional[date] = None) -> Optional[str]:
    """An ISO date, or None. Never a guess: an unparseable date is no date."""
    text = (value or "").strip()
    if not text:
        return None
    today = today or date.today()
    for pattern in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %B %Y",
                    "%d %b %Y", "%B %d %Y", "%b %d %Y"):
        try:
            parsed = datetime.strptime(text, pattern).date()
        except ValueError:
            continue
        return _sane_sowing(parsed, today)
    # "7 November" with no year. Which November is a real question: on 25
    # October, rabi wheat sown "saat November" is a fortnight away, not eleven
    # months ago. This year if that lands inside the window a sowing date may
    # sit in, last year otherwise.
    match = re.match(r"^\s*(\d{1,2})\s+([a-zऀ-ॿ]+)\s*$", text.lower())
    if match:
        month = _MONTHS.get(match.group(2))
        if month:
            day = int(match.group(1))
            for year in (today.year, today.year - 1):
                try:
                    parsed = date(year, month, day)
                except ValueError:
                    return None
                iso = _sane_sowing(parsed, today)
                if iso:
                    return iso
    return None


# "7 November", "7 नवंबर", "saat November" - a day and a month, anywhere in the
# sentence. The spoken-number words are already known, so "saat" counts as 7.
_DAY_MONTH = re.compile(r"(?<![\w])(\d{1,2}|[a-zऀ-ॿ]+)\s+([a-zऀ-ॿ]+)")


def date_in(said: str, normalised: str = "",
            today: Optional[date] = None) -> Optional[str]:
    """A sowing date read straight out of what the farmer said, or None.

    Separate from [_as_date], which reads a value the model produced. This reads
    the sentence itself, and it is the more trustworthy of the two: the farmer
    said a day and a month, the season decides the year, and nothing in between
    had the chance to invent one.
    """
    today = today or date.today()
    for text in (said or "", normalised or ""):
        for match in _DAY_MONTH.finditer(text.lower()):
            month = _MONTHS.get(match.group(2))
            if not month:
                continue
            head = match.group(1)
            try:
                day = int(head)
            except ValueError:
                if head not in SPOKEN_NUMBERS:
                    continue
                day = int(SPOKEN_NUMBERS[head])
            if not 1 <= day <= 31:
                continue
            for year in (today.year, today.year - 1):
                try:
                    parsed = date(year, month, day)
                except ValueError:
                    break
                iso = _sane_sowing(parsed, today)
                if iso:
                    return iso
    return None


# How far ahead a sowing date may sit. Not zero: in late October a rabi farmer
# is about to sow, and "7 November" is the sowing they are planning. Refusing it
# would push them to type a date they do not mean.
SOWING_AHEAD_DAYS = 60
SOWING_BEHIND_DAYS = 550


def _sane_sowing(parsed: date, today: date) -> Optional[str]:
    """A sowing date within this season, ahead of today or behind it.

    The far future is refused because the agents work the crop stage out from
    this date, and a date in 2027 makes every stage answer nonsense; the distant
    past is refused for the same reason in the other direction.
    """
    if parsed > today + timedelta(days=SOWING_AHEAD_DAYS):
        return None
    if parsed < today - timedelta(days=SOWING_BEHIND_DAYS):
        return None
    return parsed.isoformat()


# ---------------------------------------------------------------- the choices
# Words that are in an option's label only to make it read as English. Requiring
# them cost a real match: a farmer who said "patte pe safed powder hai" was
# refused "white powder on the leaves", because "the" is four letters long and
# nothing in Hinglish was ever going to contain it.
_JOINING = {"the", "and", "all", "any", "for", "from", "with", "across", "over",
            "onto", "into", "per", "out"}


def _matches_option(option: str, said: str, normalised: str) -> bool:
    """Did the farmer say something that means this option?

    Matched on the farmer's words and not on the model's answer, so a choice can
    only be ticked by something that was actually said. The normalised query
    carries the Hindi across: "peele patte" arrives here with "yellow leaf"
    attached to it.
    """
    haystack = (said + " " + normalised).lower()
    words = [w for w in re.split(r"[^a-z]+", option.lower())
             if len(w) > 2 and w not in _JOINING]
    if not words:
        return option.lower() in haystack
    # Every meaningful word of a two-word option, or the single word of a
    # one-word one. "yellowing leaves" needs both yellow and leaf; requiring
    # only one would tick "curling leaves" as well.
    hits = 0
    for word in words:
        stem = word[:-3] if word.endswith("ing") else word
        stem = stem[:-1] if stem.endswith("s") and len(stem) > 3 else stem
        if re.search(r"\b" + re.escape(stem), haystack):
            hits += 1
    return hits == len(words)


def _choices_in(options: Sequence[str], said: str, normalised: str) -> List[str]:
    return [option for option in options if _matches_option(option, said, normalised)]


# ------------------------------------------------------------------- the model
PROMPT = (
    "You map a farmer's spoken sentence onto the fields of a form. You are a "
    "parser, not an advisor.\n"
    "Return STRICT JSON: {\"values\": {\"<field key>\": \"<value>\"}}.\n"
    "Rules, all of them absolute:\n"
    "- Include a key ONLY if the farmer actually said something for it. Leave "
    "everything else out. An empty form is a correct answer.\n"
    "- NEVER invent, estimate, convert or complete a value. No default, no "
    "typical value, no 'about'.\n"
    "- For a choice field, the value must be copied EXACTLY from its options.\n"
    "- For a multiple-choice field, join the chosen options with ', '.\n"
    "- For a number field, give digits only - no units, no words.\n"
    "- For a date, give YYYY-MM-DD.\n"
    "- The farmer may speak Hindi, Hinglish or English. The field values stay in "
    "the form's own words.\n"
)


def _field_lines(fields: Sequence[Dict[str, Any]]) -> str:
    lines = []
    for field in fields:
        bits = ["%s (%s)" % (field["key"], field["type"])]
        bits.append("label: " + field["label"])
        if field["options"]:
            bits.append("options: " + " | ".join(field["options"]))
        elif field["placeholder"]:
            bits.append("about: " + field["placeholder"])
        lines.append("- " + "; ".join(bits))
    return "\n".join(lines)


async def fill(said: str, fields: Sequence[Dict[str, Any]], *,
               language: str = "en", today: Optional[date] = None) -> Dict[str, Any]:
    """What the farmer just said, as values for these fields.

    Returns the values that survived checking, which fields are still empty, and
    a note for anything that was dropped - because a value that was heard and
    then refused is worth saying out loud, not swallowing.
    """
    specs = [_spec_of(f) for f in fields if (f or {}).get("key")]
    heard = (said or "").strip()
    if not heard or not specs:
        return {"values": {}, "unfilled": [s["key"] for s in specs],
                "heard": heard, "notes": []}

    normalised = intent_module.normalise_query(heard)
    values: Dict[str, str] = {}
    notes: List[str] = []

    # The choices first, straight from the farmer's words. The model is not
    # asked to pick an option it could get subtly wrong when matching the words
    # is both cheaper and verifiable.
    for spec in specs:
        if not spec["options"]:
            continue
        picked = _choices_in(spec["options"], heard, normalised)
        if not picked:
            continue
        values[spec["key"]] = (", ".join(picked) if spec["type"] == "multiselect"
                               else picked[0])

    # Then the model, for everything words alone cannot place: which number is
    # the spend and which is the yield, and where the date is hiding.
    remaining = [s for s in specs if s["key"] not in values]
    if remaining and mistral.configured():
        guessed, failure = await _ask_model(heard, remaining, language)
        if failure:
            notes.append(failure)
        for key, raw in guessed.items():
            spec = next((s for s in remaining if s["key"] == key), None)
            if spec is None:
                continue
            value, why = _check(spec, raw, heard, normalised, today)
            if value is None:
                if why:
                    notes.append(why)
                continue
            values[key] = value

    unfilled = [s["key"] for s in specs if s["key"] not in values]
    return {"values": values, "unfilled": unfilled, "heard": heard, "notes": notes}


async def _ask_model(said: str, fields: Sequence[Dict[str, Any]],
                     language: str) -> Tuple[Dict[str, str], str]:
    messages = [
        {"role": "system", "content": PROMPT + "\nThe form:\n" + _field_lines(fields)},
        {"role": "user", "content": said},
    ]
    reply = await mistral.chat(messages, temperature=0.0, max_tokens=MAX_TOKENS,
                               json_mode=True, timeout=TIMEOUT_SECONDS)
    if not reply.ok or not (reply.text or "").strip():
        return {}, "Could not read that just now, so nothing was filled in."
    try:
        parsed = json.loads(reply.text)
    except json.JSONDecodeError:
        log.warning("form fill returned something that was not JSON")
        return {}, "Could not read that just now, so nothing was filled in."
    raw = parsed.get("values") if isinstance(parsed, dict) else None
    if not isinstance(raw, dict):
        return {}, ""
    return {str(k): ("" if v is None else str(v)) for k, v in raw.items()}, ""


def _check(spec: Dict[str, Any], raw: str, said: str, normalised: str,
           today: Optional[date]) -> Tuple[Optional[str], str]:
    """One value, verified against what the farmer actually said."""
    value = (raw or "").strip()
    if not value:
        return None, ""

    kind = spec["type"]
    label = spec["label"]

    if spec["options"]:
        wanted = [v.strip() for v in value.split(",")] if kind == "multiselect" else [value]
        kept = [o for o in spec["options"]
                if any(o.lower() == w.lower() for w in wanted)]
        if not kept:
            return None, "%s: “%s” is not one of the choices." % (label, value)
        # Being on the list is not enough. It only proves the model picked a
        # legal value; it has to be one the farmer actually said.
        #
        # Live, from one real sentence - "gehu hai, saat November ko boya tha,
        # peele patte dikh rahe hain, kharcha pandrah hazaar aaya" - the stage
        # came back as "germination". Nothing in that sentence is about a growth
        # stage; the model simply chose a plausible one off the list, and a
        # plausible wrong stage is what two of the agents would reason from.
        spoken = [o for o in kept if _matches_option(o, said, normalised)]
        if not spoken:
            return None, ("%s: nothing in what you said matched “%s”, so it was "
                          "left empty." % (label, value))
        return (", ".join(spoken) if kind == "multiselect" else spoken[0]), ""

    if kind == "number":
        cleaned = re.sub(r"[^\d.]", "", value)
        try:
            number = float(cleaned)
        except ValueError:
            return None, "%s: “%s” is not a number." % (label, value)
        if not _number_was_said(number, said):
            # The one that matters. A model asked for a spend will happily
            # produce a typical one, and a farmer who does not re-read the box
            # has just sent the agents somebody else's costs.
            return None, ("%s: %s was not in what you said, so it was left empty."
                          % (label, cleaned))
        return (str(int(number)) if number == int(number) else str(number)), ""

    if kind == "date":
        iso = _as_date(value, today)
        if iso is None:
            # The year is the part the model gets wrong - "saat November" came
            # back as 2023-11-07 - so before giving up, read the date out of the
            # farmer's own words, where no year was stated and the season
            # decides which one it is.
            iso = date_in(said, normalised, today)
        if iso is None:
            return None, "%s: “%s” did not look like a sowing date." % (label, value)
        return iso, ""

    # Free text is the farmer's own words; there is nothing to verify it against
    # except length.
    return value[:300], ""
