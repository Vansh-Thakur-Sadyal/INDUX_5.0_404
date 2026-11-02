"""What the farmer actually asked about, fetched live, in time to be said.

This exists because of one call that went wrong in a way nothing in the test
suite could see. Asked "tomato ka mandi bhav kya hai", Saarthi said *"theek hai,
main dekh kar batata hoon"* - and never came back. A few seconds later the line
re-greeted the farmer by name and the conversation started over.

Three separate things were wrong and this file fixes the first:

**Nothing ever looked tomato up.** The call path deliberately avoids the 15-agent
chain (11 seconds, past Twilio's patience) and reads a cached snapshot of the
farmer's own field instead. That snapshot knows their wheat. It has never heard
of tomato, so the prompt told the model it had no figures, and the model did as
it was told: promised to check. The data was there the whole time - one HTTP call
to Agmarknet returns tomato in under a second.

So: for a question that wants a number, work out what it is about, fetch exactly
that, and hand the real figures to the model in the same turn. A spoken answer
needs one lookup, not fifteen agents.

**Everything here is on the critical path, so everything here has a budget.** A
lookup that overruns is dropped and the turn carries on without it; a slow source
must cost the figure, never the answer.

**A failed lookup says why, out loud.** "No mandi reported tomato today" and "I
will check and come back" sound similar and are completely different: the first
is an answer, the second is a promise this system had no way to keep. When a
source has nothing, the line says so and the farmer can stop waiting.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from ..core.schemas import FarmContext
from ..rag import pipeline as rag
from . import screens
from ..services import market as market_service
from ..services import weather as weather_service

log = logging.getLogger("saarthi.lookups")

# How long to wait for live data, by where the person is.
#
# On a SCREEN, six seconds is a spinner. Twilio gives us about fifteen seconds
# for a whole turn, so this is what is left after the model call and the
# speech, rather than a round number.
BUDGET_SECONDS = 6.0

# On a PHONE it is six seconds of silence with somebody listening to it, and
# it sits in front of the three legs that cannot be avoided - identify the
# audio, ask the model, speak the answer - which measure 0.60 s, 0.70 s and
# 0.78 s from the deployed host. The whole turn is meant to land inside two
# seconds, so this is the room that is actually left.
#
# Overrunning is already handled properly: the lookup is dropped, the farmer is
# NOT told "the source had nothing" (which would be a different and untrue
# statement), and the background chain has the figure in hand for the next
# turn. A shorter fuse changes when that happens, not what happens.
#
# 0.7 s, not 1.2: measured end to end on a real call, the telephony leg either
# side of this server costs about two seconds that cannot be removed - Twilio
# ending the recording, posting it, and fetching the clip back to play - so
# what is left for everything here is smaller than it looks.
CALL_BUDGET_SECONDS = 0.7

# Every commodity Agmarknet is asked about by name. The keys are what a farmer
# says; `normalise_query` has already turned "tamatar" and "टमाटर" into "tomato"
# by the time this runs, which is why only English appears here.
PRICEABLE = sorted(set(market_service.COMMODITY_ALIASES) | {
    "bajra", "jowar", "millet", "sesame", "turmeric", "chilli", "garlic",
    "ginger", "banana", "coconut", "arhar", "tur", "moong", "urad", "jute",
    "cabbage", "cauliflower", "brinjal", "okra", "peas", "apple", "orange",
})

# The states Agmarknet files records under. Named in a question they narrow the
# search; unnamed, the farmer's own state is used, and the reply says which.
STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chandigarh",
    "Chattisgarh", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jammu and Kashmir", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Pondicherry", "Punjab", "Rajasthan", "Sikkim",
    "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttrakhand",
    "Uttarakhand", "West Bengal", "NCT of Delhi", "Delhi",
]

# Words that mean "right here", so a question carrying one is not asking about
# somewhere else and the farmer's own district is the right scope.
_HERE = ("mere", "meri", "hamare", "apne", "yahan", "idhar", "my", "our", "here",
         "मेरे", "मेरी", "हमारे", "अपने", "यहाँ", "यहां", "इधर")


@dataclass
class Lookup:
    """What was found, as lines the model may quote, plus what was not."""

    lines: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    # Set when a source was asked and had nothing. The model is told to say this
    # rather than promise to come back with it.
    gaps: List[str] = field(default_factory=list)
    # True only when this lookup IS the answer, not merely material for one.
    # A mandi price answers "what is tomato fetching" outright; a forecast is one
    # input to "when should I water", which still needs the soil, the stage and
    # ET - so the chain must still run for that one.
    complete: bool = False

    @property
    def found(self) -> bool:
        return bool(self.lines)

    def as_prompt(self) -> str:
        """The block that goes into the turn's system prompt."""
        parts: List[str] = []
        if self.lines:
            parts.append(
                "LIVE DATA FOR THIS QUESTION - say these numbers WITH THE UNIT "
                "EXACTLY AS WRITTEN, and never convert a unit. A price given per "
                "quintal is said per quintal, never per kilo.\n- "
                + "\n- ".join(self.lines))
        if self.gaps:
            # Phrased as an instruction because the failure mode is specific: a
            # model told only "no data" fills the silence with a promise.
            parts.append(
                "WHAT IS NOT AVAILABLE - tell the farmer this plainly, in one "
                "sentence, and do NOT say you will check or come back with it:\n- "
                + "\n- ".join(self.gaps))
        return "\n\n".join(parts)


def commodity_in(question: str, normalised: str = "") -> Optional[str]:
    """The thing whose price is being asked about, or None.

    Matched on the normalised query, which is where the Hindi lives: spoken Hindi
    arrives as Devanagari, and "टमाटर" only becomes "tomato" after normalising.
    Longest name first, so "bengal gram" wins over "gram".
    """
    text = ((normalised or "") + " " + (question or "")).lower()
    for name in sorted(PRICEABLE, key=len, reverse=True):
        if re.search(r"\b" + re.escape(name) + r"\b", text):
            return name
    return None


def state_in(question: str) -> Optional[str]:
    """A state named in the question, or None for "wherever I am"."""
    text = (question or "").lower()
    for name in sorted(STATES, key=len, reverse=True):
        if name.lower() in text:
            return name
    return None


def about_here(question: str) -> bool:
    words = re.findall(r"[\wऀ-ॿ]+", (question or "").lower())
    return any(word in _HERE for word in words)


# --------------------------------------------------------------------- market
async def mandi(question: str, *, normalised: str, farm: FarmContext) -> Lookup:
    """Today's price for whatever was asked about.

    The commodity comes from the question first and the farmer's own field only
    as a fallback, which is the whole point: a farmer who grows wheat is still
    allowed to ask what tomatoes are fetching.
    """
    out = Lookup()
    crop = commodity_in(question, normalised) or (farm.crop or "")
    if not crop:
        out.gaps.append("No crop was named, so no price could be looked up. Ask "
                        "the farmer which crop's price they want.")
        return out

    # A named state beats the farmer's own. Asking for a district the farmer did
    # not name would narrow the search to somewhere they never mentioned.
    state = state_in(question)
    district = None
    if state is None:
        state = farm.state or None
        if about_here(question) or not commodity_in(question, normalised):
            district = farm.district or None

    summary = await market_service.get_summary(crop, state, district)
    out.sources.append(str(summary.get("source") or "data.gov.in Agmarknet"))

    if not summary.get("available"):
        out.gaps.append(
            "Mandi prices for %s are not available right now: %s"
            % (summary.get("commodity") or crop,
               summary.get("reason") or "no records were returned."))
        return out

    # Where the records are really from, not where they were asked for. These
    # two came apart badly: the API's state filter matches on tokens, so asking
    # for Uttar Pradesh returned Madhya Pradesh too, and the farmer was told
    # "today's tomato price across Uttar Pradesh" over records from three states.
    scope = market_service.scope_phrase(summary)
    highest, lowest = summary.get("highest") or {}, summary.get("lowest") or {}
    unit = str(highest.get("unit") or "INR per quintal")

    # A price question is finished here.
    out.complete = True
    out.lines.append(
        "MANDI PRICE of %s %s on %s, from %d market records (%s): typical "
        "(median modal) %s %s."
        % (summary.get("commodity") or crop, scope,
           highest.get("arrival_date") or "today",
           int(summary.get("record_count") or 0),
           summary.get("source") or "Agmarknet",
           summary.get("median_modal"), unit))
    if highest:
        out.lines.append(
            "Highest of those: %s %s at %s mandi, %s district, %s."
            % (highest.get("modal_price"), unit, highest.get("market"),
               highest.get("district"), highest.get("state")))
    if lowest and lowest.get("market") != highest.get("market"):
        out.lines.append(
            "Lowest of those: %s %s at %s mandi, %s district, %s."
            % (lowest.get("modal_price"), unit, lowest.get("market"),
               lowest.get("district"), lowest.get("state")))
    if summary.get("note"):
        # Which is a different number to sell on, so the farmer is told.
        out.lines.append("Scope note: %s" % summary["note"])
    return out


# -------------------------------------------------------------------- weather
def day_label(date_text: Any) -> str:
    """"Thursday 29 October (today + 2)", or the raw date if it will not parse.

    The relative part is kept because a forecast line reaching the model with
    only a weekday on it invites "this Thursday or next?"; the weekday is kept
    because that is the only way a farmer refers to a day.
    """
    raw = str(date_text or "")
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return raw or "an unknown day"
    ahead = (day - date.today()).days
    when = {0: "today", 1: "tomorrow", -1: "yesterday"}.get(
        ahead, "in %d days" % ahead if ahead > 0 else "%d days ago" % -ahead)
    return "%s %s (%s)" % (day.strftime("%A"), day.strftime("%d %B"), when)


async def forecast(farm: FarmContext) -> Lookup:
    """Rain and temperature where the farm is."""
    out = Lookup()
    summary = await weather_service.get_summary(farm.latitude, farm.longitude)
    out.sources.append(str(summary.get("source") or "Open-Meteo"))

    if not summary.get("available"):
        out.gaps.append("The weather forecast is not available right now: %s"
                        % (summary.get("reason") or "the service did not answer."))
        return out

    now = summary.get("current") or {}
    if now.get("temperature_c") is not None:
        out.lines.append(
            "WEATHER at %s, %s right now: %s, %s degrees C, humidity %s percent, "
            "wind %s kmph (%s)."
            % (farm.village, farm.district, now.get("condition") or "no reading",
               now.get("temperature_c"), now.get("humidity_pct"),
               now.get("wind_kmh"), summary.get("source") or "Open-Meteo"))

    # Seven days, each named by its WEEKDAY.
    #
    # Three days was not enough and the dates were not enough. Asked "what are
    # the rain chances on Thursday", the model had Today, Tomorrow and a bare
    # ISO date to work from, so it answered about a day it had not been given
    # and said nought millimetres for every one of them - while the Weather
    # page beside it showed the real chance. A farmer asks about days by name.
    for day in list(summary.get("forecast") or [])[:7]:
        out.lines.append(
            "%s: chance of rain %s percent, rainfall %s mm, %s, %s to %s degrees C."
            % (day_label(day.get("date")), day.get("rain_probability"),
               day.get("rain_mm"), day.get("condition") or "no forecast",
               day.get("t_min"), day.get("t_max")))
    if summary.get("stale"):
        out.lines.append("Forecast note: this reading is %s seconds old."
                         % summary.get("age_seconds"))
    return out


# ------------------------------------------------- the data the pages are drawn from
# Asked about their soil, their schemes or a crop, the assistant said it did not
# have the information - on the phone and on the dashboard, with the same answer
# sitting on the page the farmer was looking at. The knowledge base was the only
# source these intents consulted, and it has nothing on most of them.
#
# These three read the SAME services the pages read, through `screens`, so the
# spoken answer and the screen cannot disagree.
async def soil_reading(farm: FarmContext, parcel_id: str = "") -> Lookup:
    """This field's soil: the measured reading if there is one, the model if not."""
    out = Lookup()
    lines = await screens.soil(farm, parcel_id or str(farm.parcel_id or ""))
    if not lines:
        out.gaps.append("There is no soil data for this field at all - no reading "
                        "and no background estimate.")
        return out
    out.lines.extend(lines)
    out.sources.append("the soil page's own data")
    return out


async def schemes(farm: FarmContext) -> Lookup:
    """Government schemes, ranked for this farm. Local data, so no latency."""
    out = Lookup()
    lines = await asyncio.to_thread(screens.schemes, farm)
    if not lines:
        out.gaps.append("No government schemes are loaded.")
        return out
    out.lines.extend(lines)
    out.sources.append("the schemes catalogue")
    # This IS the answer to "what schemes can I get", not material for one.
    out.complete = True
    return out


# The crops that have a profile but are in nobody's alias list, under the names
# a farmer says. Kept HERE rather than in `core.tasks`, whose vocabulary decides
# what a price watch may be set on: coriander has a growing guide and may well
# have no Agmarknet record, and a watch that can never fire is worse than one
# that was refused.
ADVISORY_ALIASES = {
    "dhaniya": "coriander", "dhania": "coriander", "धनिया": "coriander",
    "methi": "fenugreek", "मेथी": "fenugreek",
    "matar": "pea", "मटर": "pea",
    "masoor": "lentil", "मसूर": "lentil",
    "tarbooj": "watermelon", "tarbuj": "watermelon",
    "तरबूज": "watermelon",
    "kharbooja": "muskmelon", "kharbuja": "muskmelon",
    "खरबूजा": "muskmelon",
    "kheera": "cucumber", "khira": "cucumber", "खीरा": "cucumber",
    "bhindi": "okra", "भिंडी": "okra",
    "arhar": "pigeonpea", "अरहर": "pigeonpea",
}


def crop_in(said: str, normalised: str = "") -> Optional[str]:
    """The crop being asked about, canonical, or None.

    Reuses the call-task crop vocabulary first, because that is where the Hindi
    and the Devanagari live, then the profile names themselves, then the extra
    spoken names above. Only a crop that HAS a profile is returned: naming one
    that does not would promise a guide this system cannot produce.
    """
    from ..agents import crop_data
    from ..core import tasks

    text = ((normalised or "") + " " + (said or "")).lower()
    found = tasks.find_crop(text)
    if found and crop_data.profile(found):
        return found
    for name, spec in crop_data.CROP_PROFILES.items():
        for word in (name, str(spec.get("label") or "").lower()):
            if word and re.search(_WHOLE % re.escape(word), text):
                return name
    for alias, canonical in ADVISORY_ALIASES.items():
        if re.search(_WHOLE % re.escape(alias), text) and crop_data.profile(canonical):
            return canonical
    return None


_WHOLE = r"(?<![\wऀ-ॿ])%s(?![\wऀ-ॿ])"


async def agronomy(said: str, *, normalised: str, farm: FarmContext) -> Lookup:
    """What is known about growing one crop, or what suits this season.

    Asked about a particular crop, Saarthi said it had no information while
    nineteen crop profiles - sowing window, seed rate, spacing, water need and
    every growth stage - sat in `crop_data` unread. The Crop Advisor PAGE has
    since been removed; this is the knowledge behind a spoken crop question,
    which is a different thing and the farmer asked for it.
    """
    from datetime import datetime

    from ..agents import crop_data

    out = Lookup()
    named = crop_in(said, normalised)
    season = crop_data.current_season(datetime.now().month)

    if not named:
        suitable = crop_data.crops_for_season(season)
        if not suitable:
            out.gaps.append("No crop profiles are loaded for the %s season." % season)
            return out
        out.lines.append(
            "SEASON: it is %s now. Crops with a profile for this season: %s."
            % (season, ", ".join(crop_data.CROP_PROFILES[c]["label"] for c in suitable)))
        out.lines.append(
            "THIS FARM: %s acres at %s, %s; soil %s; irrigation %s; growing %s."
            % (farm.area_acres, farm.village, farm.district, farm.soil_type,
               farm.irrigation_method, farm.crop or "nothing recorded"))
        out.sources.append("Saarthi crop profiles")
        return out

    spec = crop_data.profile(named)
    low, high = spec["duration_days"]
    water_low, water_high = spec["water_need_mm"]
    seed_low, seed_high = spec["seed_rate_kg_ha"]
    out.lines.append(
        "CROP PROFILE for %s: a %s crop, %d to %d days to harvest, needs %d to %d mm "
        "of water, sown %s."
        % (spec["label"], " and ".join(spec["seasons"]), low, high,
           water_low, water_high, spec["sowing_window"]))
    out.lines.append(
        "Sowing %s: seed rate %s to %s kg per hectare, rows %s cm apart, %s cm deep. "
        "Suits pH %s to %s."
        % (spec["label"], seed_low, seed_high, spec["row_spacing_cm"][0],
           spec["sowing_depth_cm"][0], spec["ph"][1], spec["ph"][2]))
    out.lines.append(
        "Growth stages of %s, in order: %s."
        % (spec["label"], "; ".join(
            "%s (day %d to %d) - %s" % (stage["name"].replace("_", " "),
                                        stage["start"], stage["end"], stage["note"])
            for stage in spec["stages"])))
    if season not in spec["seasons"]:
        # Said plainly rather than left for the farmer to work out: the profile
        # is real, and so is the fact that this is the wrong time of year.
        out.lines.append(
            "SEASON WARNING: %s is a %s crop and it is %s now, so this is not its "
            "sowing season." % (spec["label"], "/".join(spec["seasons"]), season))
    out.sources.append("Saarthi crop profiles")
    out.complete = True
    return out


# ------------------------------------------------------------------ knowledge
async def knowledge(said: str, *, normalised: str, topics: Any = None,
                    channel: str = "call") -> Lookup:
    """What the knowledge base has on this, for a question whose answer is advice.

    Not every spoken question wants a number. "Paddy mein stem borer ka ilaaj
    kya hai" wants a recommendation, and before this it was handled as though a
    missing figure were the problem - the caller was told the figure was not
    ready and to ask again, for a question that had no figure in it.

    Retrieval only, never a second generation. The chunks go into the same turn's
    prompt and the one model call phrases them, which is the difference between
    adding about 400 ms and adding another two seconds.

    ``audience`` is the access rule the knowledge console enforces: a document
    somebody marked for the phone line must not reach a dashboard answer, or the
    other way round. The channel decides it, never the caller.
    """
    out = Lookup()
    audience = "call" if channel == "call" else "chat"
    chunks = await rag.retrieve(normalised or said, top_k=3,
                               topics=list(topics or []) or None, audience=audience)
    if not chunks:
        # Said out loud rather than swallowed: a question the knowledge base has
        # nothing for is the most useful thing to know about it, and the caller
        # deserves "I do not have guidance on that" over a confident guess.
        out.gaps.append("The knowledge base has nothing on this question. Say you "
                        "do not have guidance on it and offer to connect them to "
                        "the helpline.")
        return out

    out.sources.extend(chunk.source for chunk in chunks if chunk.source)
    for chunk in chunks:
        # Trimmed hard: this is going to be spoken, and the model has to fit its
        # answer into about fourteen words whatever it is given.
        text = " ".join(chunk.text.split())[:420]
        out.lines.append("GUIDANCE from %s (%s): %s"
                         % (chunk.title, chunk.source or "knowledge base", text))
    return out


# ----------------------------------------------------------------- the router
# Which intents are worth a live call, and which lookup answers them. Anything
# not listed here is served by the farm snapshot and the knowledge base as
# before - this is for the questions whose answer is a number that moves today.
BY_INTENT = {
    "market": ("mandi",),
    # Schemes as well as prices. "loan" is a keyword of both `economics` and
    # `finance` and `economics` is declared first, so "I need a loan, what can
    # I get" lands here - and with only the mandi lookup attached it was
    # answered "I do not have loan details" while the Kisan Credit Card terms
    # sat in the catalogue. A question about farm money gets both.
    "economics": ("mandi", "schemes"),
    "weather": ("forecast",),
    "irrigation": ("forecast", "soil", "knowledge"),
    # Questions whose answer is advice, not a number. These used to be handled as
    # though a missing figure were the problem: the caller was told the number
    # was not ready for a question that contained no number.
    "pest_disease": ("knowledge",),
    "weed": ("knowledge",),
    "crop_protection": ("knowledge",),
    # And then handled as though the KNOWLEDGE BASE were the only source, which
    # is why "tell me about mustard", "how is my soil" and "which schemes can I
    # get" were all answered with "I do not have that information" - three
    # questions whose answers the dashboard was displaying at the time.
    "seed_sowing": ("crop", "knowledge"),
    "crop_selection": ("crop", "soil", "knowledge"),
    "crop_stage": ("crop", "knowledge"),
    "nutrient": ("soil", "knowledge"),
    "soil_health": ("soil", "knowledge"),
    "scheme": ("schemes",),
    "finance": ("schemes", "mandi"),
    "support": ("schemes",),
    "residue": ("knowledge",),
    "general": ("knowledge",),
}


async def for_question(said: str, *, detection: Any, farm: FarmContext,
                       channel: str = "call", page: str = "", phone: str = "",
                       budget: float = BUDGET_SECONDS) -> Lookup:
    """Everything worth fetching for this one question, or as much as fits.

    Lookups run together, because two HTTP calls one after another will not fit
    inside a spoken turn and the same two in parallel will. A lookup that
    overruns the budget is abandoned and its absence is NOT reported as a gap -
    a gap is "the source had nothing", which is an answer, while a timeout is
    this system being slow, which is not the farmer's problem to hear about.
    """
    intent = str(getattr(detection, "intent", "") or "")
    normalised = str(getattr(detection, "normalised_query", "") or said)

    # "What does this page say?" - answered from the data the page is drawn
    # from, which Saarthi can read even though it cannot see the screen. A
    # named page wins over the one the farmer is standing on, so "what does the
    # weather page say" works from anywhere.
    if screens.asks_to_read(said):
        wanted_page = screens.page_in(said, normalised) or page
        if wanted_page:
            lines = await screens.read(wanted_page, farm=farm, phone=phone,
                                       parcel_id=str(farm.parcel_id or ""))
            if lines:
                out = Lookup(lines=list(lines), complete=True)
                out.sources.append("the page's own data")
                return out
            return Lookup(gaps=[
                "There is nothing on the %s page to read out yet. Say what it is "
                "for and offer to open it." % wanted_page])

    wanted = BY_INTENT.get(intent, ())

    # A price asked inside any other question still gets looked up: "gehu pila ho
    # raha hai, aur bhav kya hai" is two questions in one breath.
    if "mandi" not in wanted and commodity_in(said, normalised) and _asks_price(normalised):
        wanted = wanted + ("mandi",)
    if not wanted:
        return Lookup()

    # A crop named in any question gets its profile, whatever the intent was.
    # "mere gehu mein kitna paani" is a water question about a specific crop,
    # and the profile carries the figure.
    if "crop" not in wanted and crop_in(said, normalised):
        wanted = wanted + ("crop",)

    jobs = {}
    if "mandi" in wanted:
        jobs["mandi"] = mandi(said, normalised=normalised, farm=farm)
    if "forecast" in wanted:
        jobs["forecast"] = forecast(farm)
    if "soil" in wanted:
        jobs["soil"] = soil_reading(farm, str(farm.parcel_id or ""))
    if "schemes" in wanted:
        jobs["schemes"] = schemes(farm)
    if "crop" in wanted:
        jobs["crop"] = agronomy(said, normalised=normalised, farm=farm)
    if "knowledge" in wanted:
        jobs["knowledge"] = knowledge(said, normalised=normalised,
                                      topics=getattr(detection, "topics", None),
                                      channel=channel)

    merged = Lookup()
    names = list(jobs)
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(jobs[name] for name in names), return_exceptions=True),
            timeout=budget)
    except asyncio.TimeoutError:
        log.warning("live lookup(s) %s did not finish inside %.1fs; answering without",
                    names, budget)
        return merged

    for name, result in zip(names, results):
        if isinstance(result, BaseException):
            log.warning("live lookup %s failed: %s", name, result)
            continue
        merged.lines.extend(result.lines)
        merged.gaps.extend(result.gaps)
        merged.complete = merged.complete or result.complete
        for source in result.sources:
            if source not in merged.sources:
                merged.sources.append(source)

    # One lookup finding nothing is not worth telling the farmer about when
    # another one answered the question. "Here is the price, and by the way the
    # knowledge base has nothing on this" is noise on a phone call.
    if merged.lines:
        merged.gaps = []
    return merged


_PRICE_WORDS = re.compile(
    r"\b(price|prices|rate|rates|mandi|market|bhav|daam|keemat|sell|selling)\b")


def _asks_price(normalised: str) -> bool:
    return bool(_PRICE_WORDS.search((normalised or "").lower()))
