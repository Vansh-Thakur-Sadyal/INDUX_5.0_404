"""The phone conversation: remembers the farmer, talks like a person, calls the
agents only when the question actually needs them.

Two different jobs get confused in voice assistants, so they are separated here:

* **Talking** - greeting a returning caller by name, acknowledging, asking one
  question at a time, keeping turns short enough to say out loud. Mistral does
  this, with the farmer's memory in the prompt.
* **Knowing** - soil, irrigation, weather, prices, disease. That is the 15-agent
  chain, and it takes seconds. It runs only when the turn is genuinely agronomic,
  and its findings are handed to the conversation model to say in its own words.

A farmer should never hear "please hold" for a greeting, and should never hear an
invented number for a question about their field.

Two models, each on the job it is better at
-------------------------------------------
Sarvam speaks and Mistral reasons. Measured here, over four conversational turns:
Sarvam 286 ms, ministral-8b 755 ms - and asked "namaste", ministral-8b replied by
asking the caller who they were. Sarvam carries the talking.

What it is NOT allowed to do is work anything out. The agents compute; Sarvam is
handed the result and says it. Every spoken reply that should carry figures is
checked against the facts it was given, and one that holds a figure nobody
supplied is thrown away and phrased by Mistral instead. That check is the whole
reason the fast model can be trusted with a farmer's numbers.

Which model does which job lives in ``llm.router``, with the measurements that
decided it. Nothing in this file chooses a provider by name.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..core import commands as command_queue
from ..core import farmers, languages
from ..core import tasks as task_store
from ..core.schemas import FarmContext
from ..llm.mistral_client import client as mistral
from ..llm.sarvam_client import client as sarvam
from ..llm import router
from ..rag.guardrails import unsupported_figures, unsupported_memory
from . import actions as action_registry
from . import intent as intent_module
from . import lookups
from . import manager
from . import snapshot
from ..llm.mistral_client import support_block as support_prompt
from ..services import support as support_service
from ..core import ledger as ledger_store

# Questions a phone call asks that the fifteen agents do not cover.
SUPPORT_INTENTS = {"scheme", "finance", "support"}

# Spend and income the farmer dictates, and asks back.
LEDGER_INTENT = "ledger"

# Booking the bot to ring back later. Handled before the model sees the turn, because
# "I will call you at 4" has to be true, and only a stored row makes it true.
TASK_INTENT = "task"

# What a rupee was spent on, as a caller would say it. Only these words file an entry;
# anything else is recorded as "other" with the words kept in the note.
SPOKEN_CATEGORIES = {
    "seed": "seed", "beej": "seed",
    "fertiliser": "fertiliser", "fertilizer": "fertiliser", "urea": "fertiliser",
    "dap": "fertiliser", "khad": "fertiliser", "manure": "fertiliser",
    "pesticide": "pesticide", "spray": "pesticide", "dawa": "pesticide",
    "irrigation": "irrigation", "water": "irrigation", "pani": "irrigation",
    "diesel": "machinery", "tractor": "machinery", "machine": "machinery",
    "labour": "labour", "labor": "labour", "mazdoor": "labour", "majdoor": "labour",
    "transport": "transport", "freight": "transport",
    "storage": "storage",
}


# Words that make a sentence about money rather than about agronomy. Without
# one of these, a number next to "urea" is a dose and not a purchase: "urea 250
# kg daliye" must never be filed as spending 250 rupees on fertiliser.
#
# Matched on WORD BOUNDARIES, which is not a detail. As plain substrings, the
# two-letter cue "rs" matched inside "Thu-rs-day", so "no it is 43% rain on
# Thursday" was filed in the farmer's books as 43 rupees spent. A ledger that
# invents entries from a weather question is worse than one that misses a few.
MONEY_CUES = (
    "rupaye", "rupees", "rupee", "rs", "paisa", "paise",
    "hazaar", "hazar", "hajar", "sau", "lakh", "thousand", "hundred",
    "liya", "li", "kharida", "kharidi", "kharid", "becha", "bechi", "bech",
    "kharch", "kharcha", "diye", "diya",
    "bought", "sold", "sell", "selling", "spent", "paid", "cost", "price",
    # Income words belong here too. Without them "I earned 15000 from selling
    # wheat" was not a money sentence at all, so every sale a farmer reported
    # was silently dropped and only their spending was ever written down.
    "earn", "earned", "earning", "income", "profit", "kamaya", "kamai",
    "aamdani", "milay", "mile",
    "\u0930\u0941\u092a\u092f\u0947", "\u0939\u091c\u093e\u0930",
    "\u0932\u093f\u092f\u093e", "\u0916\u0930\u0940\u0926",
    "\u092c\u0947\u091a", "\u0916\u0930\u094d\u091a",
    "\u0915\u092e\u093e", "\u0906\u092e\u0926\u0928\u0940",
)

# Hindi verbs carry the suffix inside the word - bech, bechkar, bechne and
# bechdiya are one word to a farmer - so these match as STEMS. The short
# English cues deliberately do NOT: "rs" as a stem matches "Thursday", and
# that is how "43% rain on Thursday" was once filed as 43 rupees spent.
MONEY_STEMS = (
    "bech", "kharid", "kharch", "kamaa", "kamay", "kama",
    "mila", "mile", "milay",
    "\\u092c\\u0947\\u091a", "\\u0916\\u0930\\u0940\\u0926",
    "\\u0916\\u0930\\u094d\\u091a", "\\u0915\\u092e\\u093e",
)

_MONEY_CUE = re.compile(
    r"(?<![\\w\\u0900-\\u097f])(?:" + "|".join(re.escape(c) for c in MONEY_CUES)
    + r")(?![\\w\\u0900-\\u097f])"
    r"|(?<![\\w\\u0900-\\u097f])(?:" + "|".join(re.escape(c) for c in MONEY_STEMS)
    + r")[\\w\\u0900-\\u097f]*", re.IGNORECASE)

# A number wearing a unit is a measurement, not an amount. "43% rain", "20
# quintal", "7.3 pH", "31 degrees", "250 kg" - every one of these appeared in a
# sentence that also mentioned money at some point, and none of them is money.
_MEASURED = re.compile(
    r"\d+(?:\.\d+)?\s*(%|percent|pratishat|mm|cm|km|kg|kilo|quintal|qtl|ton|"
    r"tonne|acre|bigha|hectare|degree|degrees|celsius|ppm|ph\b|litre|liter|"
    r"\u092a\u094d\u0930\u0924\u093f\u0936\u0924|\u0915\u093f\u0932\u094b|"
    r"\u0921\u093f\u0917\u094d\u0930\u0940)", re.IGNORECASE)


def looks_like_money(said: str) -> bool:
    """Is this sentence about money at all?

    The intent router sends "do hazaar ka urea liya" to `nutrient`, because
    "urea" is a fertiliser word and it outscores everything else - so the spend
    was never written down. The money cue is checked separately from the intent
    for exactly that reason, and it is deliberately narrow: a number alone is
    not money, and a number beside a fertiliser name is usually a dose.
    """
    text = (said or "")
    if not _MONEY_CUE.search(text):
        return False
    # A sentence whose only numbers wear units is a measurement, whatever else
    # it mentions: "the cost of 43% rain" is still not 43 rupees. The rest of
    # the judgement lives in the parser, and asking it is how the two answers
    # are kept from disagreeing - one of them opens the books.
    return parse_spoken_money(text) is not None


def _is_measured(text: str, amount: float) -> bool:
    """Is the amount we found actually a measurement with a unit on it?"""
    for match in _MEASURED.finditer(text or ""):
        try:
            value = float(re.match(r"\d+(?:\.\d+)?", match.group(0)).group(0))
        except (TypeError, ValueError, AttributeError):
            continue
        if abs(value - amount) < 0.01:
            return True
    return False


# Spelled-out counts, and the multipliers that follow them. Both halves are
# needed: "do hazaar" is two thousand and "12 hazaar" is twelve thousand, and
# the old parser read the first as nothing and the second as twelve rupees.
SPOKEN_NUMBERS = {
    "ek": 1, "do": 2, "dho": 2, "teen": 3, "tin": 3, "char": 4, "chaar": 4,
    "paanch": 5, "panch": 5, "chhah": 6, "chhe": 6, "che": 6, "saat": 7,
    "aath": 8, "nau": 9, "das": 10, "gyarah": 11, "barah": 12, "pandrah": 15,
    "bees": 20, "pachchis": 25, "tees": 30, "chalis": 40, "pachas": 50,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20,
    "\u090f\u0915": 1, "\u0926\u094b": 2, "\u0924\u0940\u0928": 3,
    "\u091a\u093e\u0930": 4, "\u092a\u093e\u0901\u091a": 5,
    "\u0926\u0938": 10, "\u092c\u0940\u0938": 20,
}

MULTIPLIERS = {
    "sau": 100, "hundred": 100,
    "hazaar": 1000, "hazar": 1000, "hajar": 1000, "thousand": 1000,
    "\u0939\u091c\u093e\u0930": 1000, "\u0938\u094c": 100,
    "lakh": 100000, "lac": 100000, "\u0932\u093e\u0916": 100000,
}

_AMOUNT = re.compile(
    r"(?<![\w.])(\d{1,7}(?:\.\d{1,2})?|[a-z\u0900-\u097f]+)\s*"
    r"(sau|hundred|hazaar|hazar|hajar|thousand|lakh|lac|"
    r"\u0939\u091c\u093e\u0930|\u0938\u094c|\u0932\u093e\u0916)?"
    r"(?![\w])")


def spoken_amount(said: str):
    """Rupees from a spoken sentence, or None when it is not clear.

    Two failures this exists to prevent, both found by the call-coverage audit:

      "do hazaar ka urea liya"    parsed as nothing, because the digits were
                                  words, so the spend was never recorded
      "12 hazaar mein gehu becha" parsed as twelve rupees, because the
                                  multiplier was ignored - a thousandfold error
                                  in the farmer's own books

    When it cannot tell, it returns None and the farmer is asked again. A wrong
    figure in a ledger is worse than a missing one: the missing one is obvious.
    """
    text = (said or "").lower()
    for match in _AMOUNT.finditer(text):
        head, multiplier = match.group(1), match.group(2)
        if head in MULTIPLIERS and not multiplier:
            continue                      # "hazaar" alone is not an amount
        try:
            value = float(head)
        except ValueError:
            if head not in SPOKEN_NUMBERS:
                continue
            value = float(SPOKEN_NUMBERS[head])
        if multiplier:
            value *= MULTIPLIERS[multiplier]
        elif value < 10:
            # A bare "2" is far more likely to be two acres, two bags or two
            # days than two rupees. Asking again costs one turn.
            continue
        if 0 < value <= 10_000_000:
            return value
    return None


def parse_spoken_money(said: str):
    """An amount and what it was for, or None.

    Narrow on purpose. The amount has to be unambiguous and the category has to
    be a word the farmer actually said; anything else is recorded as "other"
    with their words kept in the note.
    """
    text = (said or "").lower()
    amount = spoken_amount(text)
    if amount is None:
        return None
    # Asking about money is not spending it, and a quoted rate is somebody
    # else's price. "mandi me tomato 2500 rupaye quintal" was being filed as
    # 2500 rupees of the farmer's own spending on the day they asked for the
    # day's rate - an entry they never made, in a month they have to trust.
    if _is_asking(text) or _is_a_quoted_rate(text, amount):
        return None
    category = "other"
    for word, canonical in SPOKEN_CATEGORIES.items():
        if re.search(r"\b" + re.escape(word), text):
            category = canonical
            break
    if _is_measured(text, amount):
        return None

    # Which DIRECTION the money went. The old list wanted whole words that
    # farmers do not use: "earn" is not "earned" and "selling" is not "sold",
    # so "I have earned Rs 6000 from selling my crops" was written down as six
    # thousand rupees SPENT - the ledger showing a loss on the day of a sale.
    earned = _INCOME.search(text) is not None
    return {"amount_inr": amount, "category": category,
            "kind": "income" if earned else "expense"}


# A question. "kitna kharch hua" and "what is the wheat rate" both carry a
# money cue and often a number, and neither is a transaction.
_ASKING = re.compile(
    r"\?|(?<![\w\u0900-\u097f])(kitn\w*|kya|kaise|batao|bata|bhav|bhaav|rate|"
    r"how much|what is|what was|whats|tell me|chal raha|chalu|"
    r"\u0915\u093f\u0924\u0928|\u0915\u094d\u092f\u093e|\u092c\u0924\u093e|"
    r"\u092d\u093e\u0935)", re.IGNORECASE)

# A price PER something. The farmer is repeating a market rate, not reporting
# what left their pocket.
_QUOTED_RATE = re.compile(
    r"(per|prati|/|\u092a\u094d\u0930\u0924\u093f)\s*"
    r"(kg|kilo|quintal|qtl|ton|tonne|acre|bigha|"
    r"\u0915\u093f\u0932\u094b|\u0915\u094d\u0935\u093f\u0902\u091f\u0932)"
    r"|(rupaye|rupees|rs|\u0930\u0941\u092a\u092f\u0947)\s*"
    r"(a |per )?(kg|kilo|quintal|qtl|ton|tonne)"
    r"|(mandi|market|agmarknet|\u092e\u0902\u0921\u0940)", re.IGNORECASE)


def _is_asking(text: str) -> bool:
    return _ASKING.search(text or "") is not None


def _is_a_quoted_rate(text: str, amount: float) -> bool:
    """A rate quote, unless the farmer also said the deal was theirs.

    "mandi me 2500 rupaye quintal mila" is a sale they made and belongs in the
    books; "mandi me tomato 2500 rupaye quintal hai" is today's price.
    """
    if not _QUOTED_RATE.search(text or ""):
        return False
    return not _MINE.search(text or "")


# The words that make a rate the farmer's own transaction rather than a quote.
_MINE = re.compile(
    r"(?<![\w\u0900-\u097f])(mila|mile|milay|becha|bechi|bech\w*|sold|"
    r"i sold|i got|kharida|kharidi|bought|liya|diya|"
    r"\u092c\u0947\u091a\w*|\u092e\u093f\u0932\w*|\u0916\u0930\u0940\u0926\w*)",
    re.IGNORECASE)

_INCOME = re.compile(
    r"(?<![\w\u0900-\u097f])(earn\w*|sold|sell\w*|income|profit|revenue|"
    r"kamay\w*|kamai|becha|bechi|bechkar|bech\w*|aamdani|"
    # "mila" is how a farmer says money came in. Spelled out rather than
    # stemmed, because "mil\w*" also matches "milk".
    r"mila|mile|mili|milay|"
    r"\u092c\u0947\u091a\w*|\u0915\u092e\u093e\w*|\u0906\u092e\u0926\u0928\u0940|"
    r"\u092e\u0941\u0928\u093e\u092b\u093e)(?![\w\u0900-\u097f])",
    re.IGNORECASE)

log = logging.getLogger("saarthi.voice")


def _log_background_failure(task: "asyncio.Task") -> None:
    """A fire-and-forget task that dies silently is how memory stops being written."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log.warning("background voice task failed: %s", error)

# Turns that need real data rather than conversation.
DATA_INTENTS = {"soil_health", "nutrient", "irrigation", "crop_selection", "crop_stage",
                "weather", "pest_disease", "weed", "seed_sowing", "crop_protection",
                "market", "economics", "residue", "sensor"}

PERSONA = (
    "You are Saarthi, a farm advisor speaking to an Indian farmer ON A PHONE CALL.\n"
    "How to talk:\n"
    "- ANSWER THE QUESTION THEY ASKED. If they ask which crop to sow, name crops. If "
    "they ask about a price, give the price. Asking them a question back instead of "
    "answering is only acceptable when you genuinely need one fact to answer at all.\n"
    "- You are having a conversation, not reading a report. One or two short sentences, "
    "then stop. Twelve spoken words is a good turn.\n"
    "- Ask ONE question at a time and wait for the answer.\n"
    "- Use what you remember about this farmer. If you know their name, use it. If they "
    "told you their crop last time, do not ask again - confirm it instead.\n"
    "- NEVER ask the caller who they are. Not 'aap kaun hain', not 'aapka naam', "
    "not 'who am I speaking to'. They rang a helpline for help, not to be "
    "identified. Without a name, help them anyway and let them offer it if "
    "they want to. Greeted with small talk, answer the small talk warmly and "
    "ask what you can help with on their farm.\n"
    "- Speak the way people speak on the phone: acknowledge first ('haan ji', 'theek hai'), "
    "then answer.\n"
    "- Never read out numbers in long lists. If there are several, give the one that "
    "matters now and offer the rest.\n"
    "What you must not do:\n"
    "- Never invent a price, a dose, a pesticide name or a measurement. If the findings "
    "below do not contain it, say plainly that you do not have that figure. Do NOT "
    "offer to check, look it up, come back to them or call them back: you cannot "
    "speak again until they say something, so that promise is never kept and the "
    "caller is left waiting in silence.\n"
    "- Never claim to remember something that is not in the memory given to you. "
    "Not their name, not their village, not a past conversation, not what they grow. "
    "If the memory section says you know nothing about this caller, then you have "
    "never spoken to them before and you must not imply otherwise - no 'pichli baar', "
    "no 'aapne bataya tha'. Asked something you were not told, say you do not have it.\n"
    "- Never change a unit. Mandi prices are per quintal unless the data says otherwise; "
    "saying 'per kilo' instead multiplies the farmer's expected income by a hundred.\n"
    "- Do not ask the same question twice in one call. If you already asked for the soil "
    "report or the crop stage and did not get it, move on and advise with what you have.\n"
    "- No markdown, no bullet points, no headings. This text is going to be spoken aloud.\n"
    "- HARD LIMIT: at most 20 spoken words when you are ANSWERING a question (naming crops, giving a figure, saying what to do), and at most 14 when you are just talking. Measured on this account, speech takes "
    "about 97 ms per word: 10 words is 1.0 s, 14 words is 1.4 s, 25 words is 2.4 s. "
    "The whole turn has to land inside two seconds and the model call already spends "
    "0.6 s of it, so every word past fourteen is silence on the line.\n"
)

# The spoken-turn budget, in one place. VOICE_MAX_WORDS is what the caller hears;
# VOICE_MAX_TOKENS is only a ceiling on generation. They are deliberately not the
# same size: at 45 tokens the model was cut off mid-word and a live call said
# "...temperature hai aur 30.6 prati". 70 tokens lets it finish its sentence, and
# the word cap then trims at a sentence boundary instead of inside a number.
VOICE_MAX_WORDS = 14

# A question that wants information needs room to carry it. Asked which crops to sow,
# fourteen words produced "...लेकिन अगर आप अगली फसल." - a sentence that stops before the
# answer. At roughly 100 ms of speech per word this costs about 0.6 s more, so a data
# turn lands near 2.5 s rather than under 2 s. That is the right trade: a farmer will
# wait half a second for crop names, and will not thank us for a fast non-answer.
VOICE_MAX_WORDS_DATA = 20
VOICE_MAX_TOKENS = 70
VOICE_MAX_TOKENS_DATA = 100

# One attempt, no backoff, and a deadline shorter than the silence a caller will
# tolerate. A retry that lands at 3 s is worse than a fallback that lands at 1.
VOICE_TIMEOUT_SECONDS = 6.0

# What a farmer hears when every model that tried put a number in the answer
# that the agents never produced. Deliberately a fixed sentence and not a model
# call: at this point the models have each shown they will invent, and asking
# one of them for an apology is asking it for one more chance to add a figure.
NO_FIGURE_REPLY = ("Yeh aankda abhi mere paas pakka nahi hai, aur galat number "
                   "batana theek nahi hoga. Thodi der baad dobara poochh lijiye.")

EXTRACT_PROMPT = (
    "From the farmer's last message, extract ONLY durable facts they stated about "
    "themselves or their farm. Return strict JSON: {\"facts\": [{\"key\": ..., "
    "\"value\": ...}]}.\n"
    "Allowed keys: crop, previous_crop, next_crop, sowing_date, field_name, field_size, "
    "irrigation_method, water_source, soil_type, problem, pest_or_disease, "
    "fertiliser_used, last_irrigation, harvest_plan, market, livestock, equipment, "
    "preferred_time_to_call, language.\n"
    "Also return \"name\", \"village\", \"land_acres\" at the top level if the farmer "
    "stated them.\n"
    "Rules: only what the farmer actually said in THIS message; no guesses, no inferences, "
    "no restating what the assistant said. If nothing durable was said, return "
    "{\"facts\": []}."
)


@dataclass
class VoiceTurn:
    reply: str
    language: str
    call_id: str
    session_id: str
    used_agents: bool = False
    agents_invoked: List[int] = field(default_factory=list)
    intent: str = "general"
    remembered: List[Dict[str, str]] = field(default_factory=list)
    recalled_facts: int = 0
    returning_caller: bool = False
    latency_ms: int = 0
    source: str = "mistral"
    # What the farmer asked Saarthi to DO, and what a screen should do about it.
    # Empty for an ordinary question, which is most turns.
    action: str = ""
    ui: Optional[Dict[str, Any]] = None
    awaiting_confirmation: bool = False
    notices: List[str] = field(default_factory=list)
    # Fire-and-forget work (memory extraction, snapshot refresh, the agent chain).
    # Exposed so tests can await it and so a shutdown can cancel it - it is never
    # serialised into the reply.
    background: List[Any] = field(default_factory=list, repr=False)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "reply": self.reply, "language": self.language, "call_id": self.call_id,
            "session_id": self.session_id, "used_agents": self.used_agents,
            "agents_invoked": self.agents_invoked, "intent": self.intent,
            "remembered": self.remembered, "recalled_facts": self.recalled_facts,
            "returning_caller": self.returning_caller, "latency_ms": self.latency_ms,
            "source": self.source, "notices": self.notices,
            "action": self.action, "ui": self.ui,
            "awaiting_confirmation": self.awaiting_confirmation,
        }


# Everything the farmer can fill in on the dashboard, in the order it is worth saying
# aloud. The voice agent used to see six of these; a farmer who had filled in their
# whole profile was still asked their village on every call.
PROFILE_LINES = (
    ("name", "name"), ("full name", "full_name"), ("age", "age"),
    ("village", "village"), ("tehsil", "tehsil"), ("district", "district"),
    ("state", "state"), ("PIN", "pincode"), ("landmark", "landmark"),
    ("total land (acres)", "land_acres"), ("soil", "soil_type"),
    ("irrigation", "irrigation"), ("farming since", "farming_since"),
    ("farmer type", "farmer_type"), ("email", "email"),
)


# "What is your name", in the forms this line actually says it. Used to notice
# that it has already been asked, so it is not asked again on the next turn.
_ASKED_A_NAME = re.compile(
    r"(aapka naam|apna naam|naam bata|naam kya|your name|आपका नाम|नाम बता|नाम क्या)",
    re.IGNORECASE)


def memory_prompt(memory: Dict[str, Any]) -> str:
    """Render the caller's whole account into the prompt.

    Short enough to sit in every turn, complete enough that the farmer never has to
    repeat something they already typed into the dashboard.
    """
    profile = memory.get("profile") or {}
    lines: List[str] = []
    if not memory.get("known"):
        # The name is worth having and it is never worth more than the answer.
        # Asked for a tomato price, a stem borer treatment and a forecast, this
        # line produced "...Kya aap apna naam bataenge?" on every single turn,
        # and once produced "first tell me your name, then I will tell you the
        # treatment" - withholding an answer it already had, from a farmer who
        # had rung a helpline, over a courtesy.
        asked = bool(memory.get("name_already_asked"))
        return ("WHAT YOU KNOW ABOUT THIS CALLER: nothing yet - this is a new "
                "number. ANSWER WHAT THEY ASKED FIRST; their name is not needed "
                "to answer anything and must never be a condition for answering. "
                + ("You have already asked for their name on this call. Do NOT "
                   "ask again." if asked else
                   "If they have not actually asked you anything, you may ask "
                   "their name once, politely."))
    bits = []
    for label, key in PROFILE_LINES:
        if profile.get(key):
            bits.append("%s %s" % (label, profile[key]))
    if bits:
        lines.append("Profile they filled in themselves: " + ", ".join(str(b) for b in bits) + ".")

    # The plots they drew on the map, with what grows on each.
    for field in (memory.get("fields") or [])[:6]:
        parts = [str(field.get("name") or "a field")]
        if field.get("crop"):
            parts.append("growing %s" % field["crop"])
        if field.get("area_acres"):
            parts.append("%s acres" % field["area_acres"])
        if field.get("soil_type"):
            parts.append("%s soil" % field["soil_type"])
        if field.get("irrigation"):
            parts.append("%s irrigation" % field["irrigation"])
        if field.get("sowing_date"):
            parts.append("sown %s" % field["sowing_date"])
        lines.append("Field: " + ", ".join(parts) + ".")

    facts = memory.get("facts") or []
    if facts:
        lines.append("They have told you: " + "; ".join(
            "%s = %s" % (f["key"].replace("_", " "), f["value"]) for f in facts[:12]) + ".")
    previous = memory.get("previous_calls") or []
    if previous:
        last = previous[0]
        when = str(last.get("at", ""))[:10]
        if last.get("summary"):
            lines.append("Last call (%s): %s" % (when, last["summary"]))
        elif last.get("last_exchange"):
            exchange = last["last_exchange"]
            lines.append("Last call (%s) ended with them saying: %s"
                         % (when, str(exchange.get("farmer", ""))[:140]))
    calls = int(profile.get("calls") or 0)
    lines.append("This is call number %d from this number." % max(calls, 1))
    # Context, not an instruction to talk about it. The earlier wording ("talk about
    # their crop and their field") made the model steer every reply back to the stored
    # field: asked which crops to sow, it answered by asking how their wheat was doing.
    lines.append("Everything above is already known - do not ask for it again. Use it "
                 "where it is relevant to what they actually asked, and answer THAT "
                 "question. Do not change the subject to their stored crop or field.")
    return "WHAT YOU KNOW ABOUT THIS CALLER:\n" + "\n".join(lines)


def language_line(code: str) -> str:
    """Name one language and forbid the rest.

    This used to say "Speak in Hindi", from whatever the session was set to, and
    nothing ever revisited the setting - so a caller who went English, Hindi,
    Punjabi was answered in Hindi for the rest of the call.

    The first attempt at a fix explained the problem to the model instead:
    "answer in the same language they used; a phone line transcribes Punjabi
    into Devanagari; write Gurmukhi for Punjabi". Measured against the live
    model, that was worse than what it replaced - an ENGLISH question came back
    in Punjabi, because the paragraph named Punjabi four times and the smaller
    conversational model took the hint. An instruction with examples in it is an
    instruction competing with its own examples.

    So the decision is made in code - the script of what was said, the Punjabi
    vocabulary a Hindi recogniser writes in Devanagari, and Azure's own language
    identification where there is audio - and the model is told one thing, with
    no other language mentioned anywhere in it.

    The script is named too. "Reply in Punjabi" alone sometimes comes back in
    Devanagari, and the voice that reads the answer out is chosen from the
    script it is written in.
    """
    lang = languages.resolve(code)
    if lang.code == "en":
        return ("Reply in simple Indian English only, whatever language the "
                "question arrived in. Keep crop names, place names and numbers "
                "exactly as they are.")
    return ("Reply ONLY in %s, written in the %s script, whatever language the "
            "question arrived in. Do not reply in any other language. Keep crop "
            "names, place names and numbers exactly as they are."
            % (lang.english_name, languages.script_of(lang.code)))


def apply_profile(session: manager.Session, memory: Dict[str, Any]) -> None:
    """Point the 15 agents at THIS farmer's farm.

    Module-level because both channels need it: the phone line and the dashboard. The
    agents were otherwise reasoning about the demo farm while the farmer's own village,
    soil, acreage and crop sat in the database unread.

    Order matters. The profile is what the farmer typed, so it goes on first; a mapped
    field then supplies the crop and sowing date, because a plot on the map is a more
    specific statement than a general profile; and facts heard on a call fill whatever
    is still blank.
    """
    profile = memory.get("profile") or {}
    farm = session.farm
    for field_name, key in (("farmer_name", "name"), ("village", "village"),
                            ("district", "district"), ("state", "state"),
                            ("soil_type", "soil_type"),
                            ("irrigation_method", "irrigation")):
        if profile.get(key):
            setattr(farm, field_name, profile[key])
    if profile.get("land_acres"):
        farm.area_acres = float(profile["land_acres"])
    if profile.get("latitude") and profile.get("longitude"):
        farm.latitude = float(profile["latitude"])
        farm.longitude = float(profile["longitude"])

    for mapped in memory.get("fields") or []:
        if mapped.get("crop"):
            farm.crop = mapped["crop"]
        if mapped.get("sowing_date"):
            farm.sowing_date = mapped["sowing_date"]
        if mapped.get("soil_type"):
            farm.soil_type = mapped["soil_type"]
        if mapped.get("irrigation"):
            farm.irrigation_method = mapped["irrigation"]
        if mapped.get("area_acres") and not profile.get("land_acres"):
            farm.area_acres = float(mapped["area_acres"])
        break                          # the first plot is the farmer's main one

    for fact in memory.get("facts") or []:
        if fact["key"] == "crop":
            farm.crop = fact["value"]
        elif fact["key"] == "sowing_date":
            farm.sowing_date = fact["value"]
        elif fact["key"] == "irrigation_method":
            farm.irrigation_method = fact["value"]


def _surface(channel: str) -> str:
    """Which kind of surface the farmer is on.

    Anything that is not a telephone has a screen, and the difference changes
    what the reply should say: a caller is told where a page was opened, a
    person looking at it is simply told it opened.
    """
    return (action_registry.CALL if (channel or "call") == "call"
            else action_registry.SCREEN)


@dataclass
class _ActionTurn:
    """An instruction that was carried out, on its way to becoming a VoiceTurn.

    A separate type so ``respond`` has one obvious early return and the fields a
    normal turn needs - latency, source - are filled in one place rather than at
    each branch.
    """

    reply: str
    session: Any
    action: str
    ui: Optional[Dict[str, Any]]
    detection: Any
    awaiting: bool = False
    background: List[Any] = field(default_factory=list)

    def finish(self, started: float) -> "VoiceTurn":
        return VoiceTurn(
            # The session's language, not the one the turn arrived in: a turn
            # that switched it must report the new one, because n8n reads this
            # field to choose the voice for everything after it.
            reply=self.reply, language=self.session.language,
            call_id="", session_id=self.session.session_id,
            intent=getattr(self.detection, "intent", "general"),
            latency_ms=int((time.perf_counter() - started) * 1000),
            source="action", action=self.action, ui=self.ui,
            awaiting_confirmation=self.awaiting, background=self.background)


class VoiceAgent:
    async def respond(self, *, phone: str, said: str, call_id: str,
                      language: str = "hi", session_id: Optional[str] = None,
                      allow_agents: bool = True,
                      channel: str = "call",
                      farm: Optional[FarmContext] = None,
                      language_known: bool = False,
                      page: str = "") -> VoiceTurn:
        """One spoken turn, budgeted for under two seconds.

        Only ONE model call and one speech synthesis sit on the critical path. The
        fact extraction and the 15-agent chain are fired afterwards and land in time
        for the next turn: measured, running the chain inline cost 11 seconds, which
        is past Twilio's patience and is why callers heard silence.
        """
        started = time.perf_counter()
        phone_key = farmers.normalise_phone(phone)
        farmers.ensure_schema()
        memory = farmers.memory_block(phone_key)
        notices: List[str] = []

        # The farmer's OWN land, when the caller handed it over.
        #
        # Without it the session used the default farm, which is in Agra - so
        # the assistant answered a weather question with Agra's forecast while
        # the Weather page beside it showed the farmer's registered land. Two
        # different numbers on one screen, and the spoken one was wrong: 21.5 to
        # 33.0 degrees against the page's 20 to 31. It also said "wahan" - over
        # there - because as far as it knew, it was somewhere else.
        # Answer in the language this turn was SPOKEN in, not the one the
        # conversation opened in.
        #
        # A farmer who starts in English and switches to Punjabi halfway through
        # was getting English back: the language was fixed when the call
        # connected and nothing ever revisited it. The caller handing us audio
        # has already identified it (Azure returns the locale it heard); a
        # caller handing us a transcript has only the text, and for eleven of
        # the thirteen the script settles it outright.
        #
        # This runs BEFORE the session is loaded on purpose. `load` lets the
        # caller's language win, and the caller is a page that will keep sending
        # whatever it had before - so detecting afterwards switched the reply and
        # then had the switch overwritten on the next turn.
        sid = session_id or ("call-" + call_id)
        if language_known:
            # Identified from the sound itself by Azure, which is better
            # evidence than anything the words can offer - a transcript can
            # arrive in the wrong script entirely. The text heuristics are for
            # the turns where there is no audio.
            spoken = languages.resolve(language).code
        else:
            spoken = languages.detect(said, manager.language_of(sid) or language)
        if spoken:
            language = spoken

        session = manager.load(sid, language=language,
                               channel="call", phone=phone_key, farm=farm)
        self._apply_profile(session, memory)
        if session.language != language:
            manager.set_language(session, language)
        elif spoken:
            manager.set_language(session, spoken)

        detection = manager.resolve_turn(session, said)
        wants_data = detection.intent in DATA_INTENTS and detection.confidence >= 0.6

        # Was this an instruction rather than a question? Answered in code, before
        # any model is involved, for the same reason the ledger and the call-back
        # are: "open my soil page" has to open the soil page, not produce a
        # sentence about opening it. A model that was asked to decide could decide
        # differently on the next identical sentence.
        done = self._maybe_act(said, session, phone_key, detection,
                               call_id=call_id, language=language,
                               channel=channel)
        if done is not None:
            return done.finish(started)

        # Whatever is cached, even if stale - a slightly old number beats a silent line.
        snap = snapshot.peek(session.farm)
        facts_block = snap.as_prompt() if snap else ""

        # Then the thing the farmer actually asked about, fetched now.
        #
        # The snapshot is a picture of THIS farm, so it answered "how is my
        # wheat" well and "what are tomatoes fetching" not at all: there was no
        # tomato in it, the prompt said so, and the model promised to check and
        # come back. Nothing could keep that promise. One lookup, inside a
        # budget, answers it in the same breath instead.
        live = await lookups.for_question(
            said, detection=detection, farm=session.farm, channel=channel,
            page=page, phone=phone_key,
            # A caller is listening to silence while this runs; a reader is
            # looking at a page that has already drawn.
            budget=(lookups.CALL_BUDGET_SECONDS if channel == "call"
                    else lookups.BUDGET_SECONDS))
        if live.found or live.gaps:
            facts_block = (facts_block + "\n\n" + live.as_prompt()).strip()

        # Schemes, credit terms and helplines. Local data, so this costs no latency;
        # it is attached only when the question is near it, because every line here
        # is prompt the spoken answer has to share.
        # Money talk: record it if an amount was spoken, and always hand the agent
        # the running total so "how much have I spent" is answerable in one turn.
        # Either the router called it money, or the sentence plainly is. The
        # second half is what catches "do hazaar ka urea liya", which the
        # router files under fertiliser.
        if detection.intent == LEDGER_INTENT or looks_like_money(said):
            parsed = parse_spoken_money(said)
            if parsed:
                ledger_store.add_entry(
                    phone_key, source="call", note=said[:200],
                    field_id=str(session.farm.parcel_id or ""),
                    crop=str(session.farm.crop or ""), **parsed)
                notices.append("Recorded %d rupees for %s."
                               % (round(parsed["amount_inr"]), parsed["category"]))
            facts_block = (facts_block + "\nFARM MONEY RECORD: "
                           + ledger_store.spoken_summary(phone_key)).strip()

        # A booking is a promise, so it is written down before anything is said. The
        # confirmation names the time back to the farmer: if the parser took the
        # wrong number out of the sentence, this is where they hear it.
        if detection.intent == TASK_INTENT:
            booking = task_store.book_from_speech(
                phone_key, said, language=language,
                parcel_id=str(session.farm.parcel_id or ""), source="call")
            notices.append(booking["reply"])
            facts_block = (facts_block + "\nCALL-BACK BOOKING: " + booking["reply"]
                           + " Tell the farmer exactly this and do not invent a "
                             "different time.").strip()

        if detection.intent in SUPPORT_INTENTS:
            block = support_prompt(support_service.summary_for_agents(
                session.farm.model_dump(mode="json")))
            if block:
                facts_block = (facts_block + "\n" + block).strip()

        reply, source = await self._speak_turn(
            said=said, memory=memory, language=language, call_id=call_id,
            agent_digest=facts_block, notices=notices, wants_data=wants_data)

        farmers.append_turn(call_id, phone_key, said, reply,
                            {"intent": detection.intent, "source": source})
        manager.record_user_turn(session, said, detection.intent)
        manager.record_assistant_turn(session, reply, {"call_id": call_id}, detection.intent)
        manager.update_memory(session, detection)
        manager.save(session)

        # Off the critical path: remember what was said, and refresh the farm picture.
        background: List[asyncio.Task] = [
            asyncio.create_task(self._extract_and_store(said, phone_key, call_id, memory)),
            asyncio.create_task(snapshot.get(session.farm, refresh=not (snap and snap.fresh))),
        ]
        if wants_data and allow_agents and not live.complete:
            # Only while the question is still open. A price question is finished
            # by the lookup, so the chain there is eleven seconds of work whose
            # findings nobody will ever hear. An irrigation question is NOT
            # finished by a forecast - that is one input out of several - so the
            # chain still runs and its findings reach the next turn.
            background.append(asyncio.create_task(
                self._run_agents_in_background(said, session)))
        for task in background:
            task.add_done_callback(_log_background_failure)

        # Which language to SAY this in.
        #
        # What the farmer said comes first: those are their own words and they
        # have already set the session language above. The answer is consulted
        # only when their words gave no opinion - a transcript so mangled that
        # neither the script nor the vocabulary settled it - because the model
        # was told to mirror them and its answer is then the better witness.
        # Getting this precedence backwards means a model that drifts into
        # English drags the farmer with it.
        if not spoken:
            answered_in = languages.detect(reply, session.language)
            if answered_in and answered_in != session.language:
                log.info("answered in %s though the call was in %s",
                         answered_in, session.language)
                manager.set_language(session, answered_in)
        language = session.language

        return VoiceTurn(
            reply=reply, language=language, call_id=call_id, session_id=session.session_id,
            used_agents=False, agents_invoked=[], intent=detection.intent,
            remembered=[], recalled_facts=len(memory.get("facts") or []),
            returning_caller=bool(memory.get("returning")),
            latency_ms=int((time.perf_counter() - started) * 1000),
            source=source, notices=notices, background=background,
        )


    # --------------------------------------------------------------- actions
    def _maybe_act(self, said: str, session, phone: str, detection,
                   call_id: str = "", language: str = "hi",
                   channel: str = "call") -> Optional["_ActionTurn"]:
        """Carry out an instruction, or return None and let the turn proceed.

        Confirmation lives on the session rather than in memory, so a farmer can
        be asked "shall I?" on one call, hang up, and not have a half-finished
        instruction fire on the next one - the session is the call.
        """
        # What a handler cannot work out for itself. ``last_reply`` is what
        # makes "say that again" repeat rather than re-answer: the words come
        # out of the transcript, not out of a model.
        spoken_before = ""
        if call_id:
            previous = farmers.last_transcript(call_id)
            if previous:
                spoken_before = str(previous[-1].get("saarthi") or "")
        # What the fifteen agents found last time they ran for this farm, so
        # "jaanch ka result batao" reads back real findings instead of being
        # answered by a model that has not seen them.
        snap = snapshot.peek(session.farm)
        context = {"last_reply": spoken_before,
                   "findings": list(getattr(snap, "agent_lines", []) or [])
                   if snap else []}

        waiting = (session.memory or {}).get("pending_action")

        if waiting:
            # The previous turn asked a yes/no question. Nothing else is parsed
            # until it is answered, because "haan" after any other question would
            # otherwise be read as consent to the forgotten one.
            session.memory.pop("pending_action", None)
            if action_registry.is_yes(said):
                outcome = action_registry.run_pending(
                    waiting, channel=_surface(channel), language=language,
                    context=context)
            elif action_registry.is_no(said):
                outcome = action_registry.ActionResult(
                    ok=True, spoken="Theek hai, rehne deta hoon.")
            else:
                # Neither. Treat it as a new turn rather than guessing, and say
                # the old one was dropped so nobody is left expecting it.
                manager.save(session)
                return None
            return self._as_turn(outcome, session, phone, said, detection,
                                 channel=channel)

        found = action_registry.detect(said)
        if not found.found:
            return None

        # A screen is a screen. Saying "I have opened it on your dashboard"
        # to somebody who is looking at that dashboard is the phone line's
        # sentence said in the wrong place.
        outcome = action_registry.run(found, channel=_surface(channel),
                                      language=language, context=context)
        if not outcome.ok and not outcome.spoken:
            return None                        # not really an instruction after all
        if outcome.needs_confirmation and outcome.pending:
            session.memory["pending_action"] = outcome.pending

        # A language change has to outlive the sentence that asked for it, or
        # the next turn goes back to the old one and the farmer has to ask again.
        wanted = str(outcome.data.get("language") or "")
        if wanted:
            manager.set_language(session, wanted)

        # "Poori jaanch chalu kar di hai" has to be true. On a screen the page
        # runs the chain; on a call there is no page, so it is started here and
        # its findings land in the snapshot for the next turn to read out.
        started_work: List[Any] = []
        if outcome.data.get("run_now"):
            task = asyncio.create_task(
                self._run_agents_in_background(said, session))
            task.add_done_callback(_log_background_failure)
            # Carried on the turn like every other background task: it is how a
            # shutdown cancels it, and how a test can await it instead of
            # guessing whether it ran.
            started_work.append(task)

        return self._as_turn(outcome, session, phone, said, detection,
                             action_id=found.action.id, background=started_work,
                             channel=channel)

    def _as_turn(self, outcome, session, phone: str, said: str, detection,
                 action_id: str = "",
                 background: Optional[List[Any]] = None,
                 channel: str = "call") -> "_ActionTurn":
        """Record the instruction the way an ordinary turn is recorded."""
        reply = outcome.spoken or "Theek hai."
        if outcome.ui and _surface(channel) == action_registry.CALL:
            # Queued only for a caller, because a caller has no screen of their
            # own: the instruction waits for the dashboard or the app to pick it
            # up. A surface that asked already has the instruction in this reply
            # and acts on it at once, so queueing a copy for it made it act
            # twice - navigate, and then get dragged back a second later when it
            # collected its own copy.
            command_queue.queue(phone, outcome.ui, source="call", spoken=reply)
        if outcome.ui and outcome.ui.get("do") == "stop":
            command_queue.clear(phone)

        manager.record_user_turn(session, said, detection.intent)
        manager.record_assistant_turn(session, reply, {"action": action_id},
                                      detection.intent)
        manager.save(session)
        return _ActionTurn(reply=reply, session=session, action=action_id,
                           ui=outcome.ui, detection=detection,
                           awaiting=outcome.needs_confirmation,
                           background=list(background or []))

    async def _run_agents_in_background(self, said: str, session) -> None:
        """Run the full chain after the farmer has already been answered.

        Its findings go into the farm snapshot, so the NEXT turn - and the dashboard -
        carry the validated numbers without anyone waiting on the phone.
        """
        from ..orchestrator import orchestrator          # local: avoids a cycle
        advisory = await orchestrator.run(said, session, use_rag=True, run_agents=True)
        lines: List[str] = []
        for result in advisory.agent_results:
            if result.status.value == "unavailable" or not result.findings:
                continue
            lines.append("%s: %s" % (result.agent_name.split(" and ")[0],
                                     result.findings[0][:160]))
        snapshot.store_agent_findings(session.farm, lines)
        log.info("background chain finished for call session %s: %d agents, %d ms",
                 session.session_id, len(advisory.agents_invoked), advisory.duration_ms)

    # ------------------------------------------------------------- helpers
    def _apply_profile(self, session: manager.Session, memory: Dict[str, Any]) -> None:
        apply_profile(session, memory)

    async def _speak_turn(self, *, said: str, memory: Dict[str, Any], language: str,
                          call_id: str, agent_digest: str, notices: List[str],
                          wants_data: bool = False) -> Tuple[str, str]:
        """Produce the words to say. Normally one model call.

        A farm answer is phrased FROM the snapshot's numbers, so the model never
        has to know agronomy - it only has to say what it was given. That is why
        the fast conversational model is the right one here, and why its output
        is checked rather than trusted: speaking is a different skill from being
        right, and only one of the two can be verified cheaply.

        A second call happens only when the first reply states a figure that was
        never supplied. On this account that is rare, and when it does happen a
        second of silence is a far better outcome than an invented price.
        """
        # Has the name already been asked for on this call? Read off what was
        # actually said, not tracked separately: the transcript is the record,
        # and anything else can drift out of step with it.
        history = farmers.last_transcript(call_id)
        if not memory.get("known"):
            memory = dict(memory)
            memory["name_already_asked"] = any(
                _ASKED_A_NAME.search(str(turn.get("saarthi") or ""))
                for turn in history)

        system = PERSONA + "\n" + language_line(language) + "\n\n" + memory_prompt(memory)
        if agent_digest:
            system += "\n\n" + agent_digest
        elif wants_data:
            # The question wants a figure and nothing produced one. Saying
            # nothing leaves the model to guess, and a fluent one will: asked a
            # wheat price with no market data loaded, Sarvam answered "2200 se
            # 2300 rupaye per quintal" out of nowhere. The checker catches that,
            # but catching it costs a second model call - so the cheaper fix is
            # to tell the model plainly that it has nothing, before it writes
            # anything.
            #
            # What it is told to say instead matters just as much. "Say you will
            # check and come back with it" was the old instruction, and it was a
            # promise this system cannot keep: nothing on this line can speak
            # unprompted. The caller waited in silence, Twilio heard the silence,
            # and the call re-greeted them by name. The chain IS still running
            # and its findings do reach the next turn - so the honest sentence
            # asks them to ask again, which is a thing that actually works.
            system += ("\n\nYOU HAVE NO FIGURES FOR THIS QUESTION. No price, no "
                       "dose and no measurement has been given to you. Do NOT "
                       "state any number at all. Say in ONE short sentence that "
                       "you do not have that figure yet and they should ask again "
                       "in a moment. Do NOT say you will check, look it up, come "
                       "back to them, or call them back.")
        messages = [{"role": "system", "content": system}]
        for exchange in history[-4:]:
            # A turn where the caller said nothing - a quiet moment that Saarthi
            # answered anyway - has no user half. Sending an empty user message
            # is both meaningless and rejected outright by some providers.
            farmer = str(exchange.get("farmer", ""))[:300]
            if not farmer.strip():
                continue
            messages.append({"role": "user", "content": farmer})
            messages.append({"role": "assistant",
                             "content": str(exchange.get("saarthi", ""))[:300]})
        messages.append({"role": "user", "content": said})

        # The language instruction again, LAST.
        #
        # It is in the system prompt already, and on a live call that was not
        # enough: four turns of Punjabi history followed by an English question
        # came back in Punjabi, because the conversation above is longer and
        # closer to hand than one line at the top of the system message. A
        # short reminder immediately before the model answers is the one the
        # model follows. This is the whole reason it is repeated, and it is
        # repeated verbatim so the two can never say different things.
        messages.append({"role": "system", "content": language_line(language)})

        if wants_data:
            choice = router.voice_data()
            cap, tokens, temperature = VOICE_MAX_WORDS_DATA, VOICE_MAX_TOKENS_DATA, 0.3
        else:
            choice = router.voice_chat()
            cap, tokens, temperature = VOICE_MAX_WORDS, VOICE_MAX_TOKENS, 0.6

        spoken, source, rejected = await self._ask(
            choice, messages, temperature=temperature, max_tokens=tokens, cap=cap,
            check_figures=choice.verify_figures, facts=agent_digest,
            remembered=system)
        if rejected:
            # Worth a line in the log and in the call record: a model that speaks
            # a figure nobody gave it, or a memory nobody gave it, is the one
            # failure this project cannot tolerate quietly, and it should be
            # visible if it starts happening.
            log.warning("%s spoke unsupported %s; re-phrased by the fallback",
                        choice.provider, rejected)
            notices.append("Something in the first draft was not in the data, so the "
                           "reply was written again.")
        if spoken:
            spoken = await self._in_the_asked_language(spoken, language, choice)
            return spoken, source

        if rejected:
            # Every model that tried invented. Saying so is the only honest
            # option left: a farmer who is told a price acts on it, and "I will
            # find out" costs them a day while a wrong price can cost them the
            # crop.
            return (NO_FIGURE_REPLY, "withheld")

        notices.append("No language model was reachable.")
        return ("Maaf kijiye, abhi jawab taiyar nahi ho paya. Thodi der baad dobara "
                "koshish kijiye.", "unavailable")

    async def _in_the_asked_language(self, spoken: str, language: str, choice) -> str:
        """The last word on which language the answer is in.

        The instruction is in the system prompt and repeated immediately before
        the model answers, and measured over twelve turns of a six-language
        conversation that was right eleven times. Eleven out of twelve is not a
        guarantee, and a farmer who gets one answer in a language they do not
        read has no way to ask for it again except by saying so.

        So the answer is CHECKED, and when it is in the wrong language it is
        translated rather than regenerated. Translating is the smaller task and
        the safer one: a regenerated answer is a second chance to invent a
        figure, while a translation carries the same figures across - and the
        instruction says to keep them unchanged.

        A failed translation returns the original. A right answer in the wrong
        language still beats silence.
        """
        want = languages.resolve(language)
        wrote = languages.detect(spoken, want.code)
        if not wrote or wrote == want.code:
            # A reply can pass that and still be unreadable to the farmer it is
            # for. Live: "Soil moisture 79.0 % hai, jo ki high hai" with the
            # three Punjabi words in Gurmukhi, answering an English question -
            # two thirds Latin letters, so it counted as English. English words
            # inside an Indian sentence are normal; an Indian script inside an
            # English sentence is not, and nor is the wrong Indian script.
            other, share = languages.dominant_indic(spoken)
            if not (other and other != want.code
                    and share >= languages.FOREIGN_SCRIPT_SHARE):
                return spoken
            wrote = other

        log.info("answer came back in %s though %s was asked for; translating",
                 wrote, want.code)
        instruction = (
            "Translate the message into %s, written in the %s script. "
            "Keep every number, crop name and place name exactly as it is. "
            "Reply with the translation and nothing else."
            % (want.english_name, languages.script_of(want.code)))
        try:
            again, _source, _rejected = await self._ask(
                choice,
                [{"role": "system", "content": instruction},
                 {"role": "user", "content": spoken}],
                temperature=0.0, max_tokens=VOICE_MAX_TOKENS, cap=VOICE_MAX_WORDS)
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not translate the reply: %s", exc)
            return spoken

        again = (again or "").strip()
        if not again:
            return spoken
        # The translation may not bring a figure with it. The answer it was
        # given has already been checked against the facts; a "translation" that
        # arrives carrying a number the original did not have is not a
        # translation, and accepting it would let this step undo the one check
        # this project cannot do without.
        invented = unsupported_figures(again, spoken)
        if invented:
            log.warning("the translation added figures %s; keeping the original",
                        invented)
            return spoken
        return again

    async def _ask(self, choice, messages, *, temperature: float, max_tokens: int,
                   cap: int, check_figures: bool = False,
                   facts: str = "", remembered: str = "") -> Tuple[str, str, List[str]]:
        """Ask the chosen model, check what it said, fall back if it will not do.

        Returns the words, which provider produced them, and whatever was
        rejected. A reply is rejected for exactly two reasons: it states a number
        that is not in ``facts``, or it claims to remember something that is not
        in ``remembered``. Everything else - clumsy phrasing, an odd word choice
        - is left alone, because a second model call costs a second of silence on
        a live call and clumsy is not wrong.

        ``check_figures`` is a flag and not "is ``facts`` non-empty", and the
        difference is the whole point. An empty ``facts`` means the agents
        produced nothing for this question, which is precisely when a fluent
        model is most likely to fill the gap - asked a wheat price with no market
        data loaded, Sarvam answered "2200 se 2300 rupaye per quintal", a figure
        from nowhere. With no facts, no figure may be spoken at all.
        """
        attempts = [(choice.provider, choice.model, max_tokens)]
        if choice.fallback:
            attempts.append((choice.fallback, choice.fallback_model, max_tokens))

        rejected: List[str] = []
        for provider, model, budget in attempts:
            budget = max(budget, choice.min_tokens)
            if provider == router.SARVAM:
                if not sarvam.configured():
                    continue
                result = await sarvam.chat(messages, temperature=temperature,
                                           max_tokens=budget, model=model)
                ok, text, reason = result.ok, result.text, result.reason
            else:
                if not (sarvam.configured() or mistral.configured()):
                    continue
                reply = await mistral.chat(messages, temperature=temperature,
                                           max_tokens=budget, model=model,
                                           urgent=True, timeout=VOICE_TIMEOUT_SECONDS)
                ok, text, reason = reply.ok, reply.text, reply.reason

            if not ok or not text.strip():
                log.info("%s could not answer (%s)", provider, str(reason)[:90])
                continue

            words = self._tidy(text, cap)
            if check_figures:
                invented = unsupported_figures(words, facts)
                if invented:
                    rejected.extend(invented)
                    continue           # let the next model phrase the same facts
            # A claim about the farmer is checked on EVERY turn, not only the
            # ones carrying data. The invented village came out of a friendly
            # chat turn, which is exactly where a fluent model relaxes - and
            # being wrongly remembered is its own kind of harm, separate from
            # being told a wrong number.
            imagined = unsupported_memory(words, remembered)
            if imagined:
                rejected.extend(imagined)
                continue
            return words, provider, rejected

        return "", "unavailable", rejected

    def _tidy(self, text: str, max_words: int = 0) -> str:
        """Strip anything that sounds wrong when a TTS engine reads it aloud.

        ``max_words`` is a hard cap for the phone path. Asking the model nicely is
        not enough: given a 25-word limit it returned 28-word answers, and at 97 ms
        of speech per word that alone put the turn at 3.9 s. Truncation prefers the
        last sentence boundary, so the caller hears a finished thought.
        """
        cleaned = re.sub(r"[*_#`]+", "", text or "")
        cleaned = re.sub(r"(?m)^\s*[-•]\s*", "", cleaned)
        cleaned = re.sub(r"\n{2,}", " ", cleaned)
        cleaned = cleaned.strip()
        if max_words and len(cleaned.split()) > max_words:
            cleaned = self._clip_to_words(cleaned, max_words)
        return cleaned

    @staticmethod
    def _clip_to_words(text: str, max_words: int) -> str:
        words = text.split()
        head = " ".join(words[:max_words])
        # Cut back to the last finished sentence, so the caller hears a whole thought
        # instead of a clipped clause. U+0964 is the Devanagari full stop. Four words is
        # the floor: "Haan ji." on its own answers nothing, but any real sentence is
        # better than the same number of words ending mid-phrase.
        #
        # The stop must be followed by a space or the end of the text. Matching a bare
        # "." treated the decimal point in "30.6" as the end of a sentence, and a live
        # call told the farmer "...ka temperature hai aur 30."
        ends = [m.end() for m in re.finditer(r"[.?!\u0964](?=\s|$)", head)]
        if ends and len(head[:ends[-1]].split()) >= 4:
            return head[:ends[-1]].strip()
        return VoiceAgent._drop_dangling(head.split()) + "."

    # A word the sentence cannot end on. Live calls produced "...temperature hai aur 30."
    # and "...bhav aaj per quintal 1950 rupee se 2000." - a number cut mid-range is worse
    # than no number, because the farmer hears a figure that was never meant to stand
    # alone. So trailing connectors and the numbers they introduce come off together.
    # Connectors only. "hai" and "kya" are NOT here: they end perfectly good Hindi
    # sentences ("mitti theek hai", "paani dena hai kya"), and trimming them would
    # break correct replies to fix broken ones.
    _DANGLING = {"aur", "se", "ka", "ki", "ke", "ko", "mein", "me", "par", "ya", "toh",
                 "to", "tak", "and", "or", "of", "at", "in", "from", "about", "with",
                 "\u0914\u0930", "\u0938\u0947", "\u0915\u093e", "\u0915\u0940",
                 "\u0915\u0947", "\u092e\u0947\u0902", "\u092f\u093e", "\u0924\u0915"}

    @staticmethod
    def _drop_dangling(words: List[str]) -> str:
        """Trim trailing fragments that leave the sentence hanging."""
        kept = list(words)
        while len(kept) > 3:
            last = kept[-1].strip(",;:-\u2014").lower()
            # a bare number, or a connector with nothing after it
            if last in VoiceAgent._DANGLING or re.fullmatch(r"[\d.,%]+", last):
                kept.pop()
                continue
            break

        # A connector near the end means the clause it opened never finished:
        # "...temperature hai aur 30.6 pratishat" promises a second fact and stops.
        # Only the last three words are examined, so "mitti aur paani theek hai" -
        # where the coordination completes - is left alone.
        for index in range(max(3, len(kept) - 3), len(kept)):
            if kept[index].strip(",;:-\u2014").lower() in VoiceAgent._DANGLING:
                kept = kept[:index]
                break
        return " ".join(kept).rstrip(",;:-\u2014 ")

    async def _extract_and_store(self, said: str, phone: str, call_id: str,
                                 memory: Dict[str, Any]) -> List[Dict[str, str]]:
        """Pull durable facts out of what the farmer just said, and store them."""
        if len(said.split()) < 3 or not mistral.configured():
            return []
        messages = [
            {"role": "system", "content": EXTRACT_PROMPT},
            {"role": "user", "content": said},
        ]
        # Off the critical path, so it can afford the retries and ask for strict
        # JSON - which is Mistral's, whatever is speaking on the call.
        payload = await mistral.chat_json(messages, temperature=0.0, max_tokens=220,
                                          model=router.fact_extraction().model)
        if not payload:
            return []

        stored: List[Dict[str, str]] = []
        profile_update: Dict[str, Any] = {}
        existing = memory.get("profile") or {}
        for key, column in (("name", "name"), ("village", "village")):
            value = payload.get(key)
            if not isinstance(value, str) or not (1 < len(value.strip()) < 60):
                continue
            # Identity is written once. An extraction model asked for a name will
            # find one in almost any sentence - it once read "bijai" (sowing) as the
            # farmer's name and overwrote "Ramesh Kumar". Corrections go through the
            # farmer API, not through a guess on turn three.
            if existing.get(column):
                continue
            profile_update[column] = value.strip()
        acres = payload.get("land_acres")
        try:
            if acres is not None and 0 < float(acres) < 10000:
                profile_update["land_acres"] = float(acres)
        except (TypeError, ValueError):
            pass
        if profile_update:
            farmers.upsert_farmer(phone, **profile_update)
            stored.extend({"key": k, "value": str(v)} for k, v in profile_update.items())

        for fact in payload.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            key, value = str(fact.get("key", "")), str(fact.get("value", ""))
            if farmers.remember(phone, key, value, call_id=call_id):
                stored.append({"key": key, "value": value})
        return stored

    def _parse_json(self, text: str) -> Optional[Dict[str, Any]]:
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

    # --------------------------------------------------------- call close
    async def summarise_call(self, call_id: str, phone: str) -> str:
        """One line written at hang-up, so the next call can open with context."""
        transcript = farmers.last_transcript(call_id)
        if not transcript:
            return ""
        if not mistral.configured():
            summary = "; ".join(str(t.get("farmer", ""))[:60] for t in transcript[:3])
            farmers.close_call(call_id, summary[:300])
            return summary[:300]
        conversation = "\n".join(
            "Farmer: %s\nSaarthi: %s" % (t.get("farmer", ""), t.get("saarthi", ""))
            for t in transcript[-8:])
        messages = [
            {"role": "system", "content":
                "Summarise this farm helpline call in ONE sentence, under 30 words: what "
                "the farmer asked and what was advised. Only what was actually said."},
            {"role": "user", "content": conversation},
        ]
        # The call is over and nobody is waiting, but the summary is in the
        # farmer's language and that is Sarvam's. No figure check: it summarises
        # what was already said, and the saying was checked at the time.
        summary, _, _ = await self._ask(router.call_summary(), messages,
                                        temperature=0.2, max_tokens=90, cap=0)
        farmers.close_call(call_id, summary[:300])
        return summary


voice_agent = VoiceAgent()
