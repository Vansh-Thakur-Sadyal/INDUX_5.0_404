"""What the farmer can ask Saarthi to DO, rather than tell them.

Until now a spoken turn could only produce words. Two exceptions grew up on
their own - "note down two thousand for urea" wrote a ledger row, "call me at
four" booked a task - and both worked because the sentence was parsed in code
before any model saw it. This generalises that, because the pattern was right:

    the model plans, the application enforces.

A model never executes anything here. It is asked, at most, which action a
sentence meant; the parameters are re-read from the sentence by code, the
account is checked by code, and the handler is a plain function. A model that
hallucinated ``delete_field(field="all")`` would produce a dict that fails
validation, not a deleted field.

Three risk levels, and they are the whole safety story:

``READ``     answer or show something. Run it.
``WRITE``    change the farmer's own data. Run it, and say exactly what changed.
``CONFIRM``  costs money, rings a phone, leaves the system, or cannot be undone.
             Ask first, in one sentence, and do it only on a clear yes.

Navigation is a special case of READ: the server cannot open a page, so the
action returns a ``ui`` instruction that whichever surface is listening carries
out - the dashboard, the app, or neither if nobody is watching.

A phone call is not a screen
---------------------------
The same sentence means different things down a phone line. "mandi bhav dikhao"
on the dashboard opens a page; on a feature phone there is no page to open, and
answering "Opening mandi prices" - in English, to a Hindi caller, who is holding
a phone with no screen - is three failures in one sentence. So every handler is
told which channel it is on and which language the farmer is speaking, and a few
actions exist only on a call: repeat what you just said, switch language, give me
the helpline number.

The replies are written out in English and Hindi and fall back to English for the
other eleven languages. Not because the others do not matter, but because a
confident sentence in a language nobody here can check is worse than a plain one
the farmer can puzzle through - and this is text that goes straight into a
farmer's ear with no chance to re-read it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

READ = "read"
WRITE = "write"
CONFIRM = "confirm"

# Channels. A screen can be navigated; a phone call can only be told things.
SCREEN = "screen"
CALL = "call"

# What each action says, per language. English and Hindi are written out;
# everything else falls back to English - see the module docstring for why.
#
# Hindi here is Hinglish in Latin script on purpose: it is what the TTS voice
# reads most naturally, it is what the rest of the spoken replies use, and a
# farmer hearing it cannot tell the difference. Devanagari is for the screen.
PHRASES: Dict[str, Dict[str, str]] = {
    "opened": {
        "en": "Opening %s.",
        "hi": "%s khol raha hoon.",
    },
    "opened_elsewhere": {
        "en": "I have opened %s on your Saarthi dashboard.",
        "hi": "Maine aapke Saarthi dashboard par %s khol diya hai.",
    },
    "pipeline_started": {
        "en": "Starting the full check. It takes about half a minute.",
        "hi": "Poori jaanch shuru kar raha hoon. Lagbhag aadha minute lagega.",
    },
    "pipeline_on_call": {
        "en": "I am running the full check now. Ask me again in a minute and I "
              "will have the answer.",
        "hi": "Poori jaanch chalu kar di hai. Ek minute baad phir poochhiye, "
              "jawab taiyar mil jayega.",
    },
    "reading_screen": {
        "en": "Opening the sensor page so you can take a reading.",
        "hi": "Sensor ka page khol raha hoon, wahan se reading le lijiye.",
    },
    "reading_on_call": {
        "en": "The soil probe talks to the Saarthi app on your phone, not to me "
              "on this call. Open the app and press Fetch.",
        "hi": "Mitti ka sensor aapke phone ke Saarthi app se judta hai, is call "
              "se nahi. App kholiye aur Fetch dabaiye.",
    },
    "report_screen": {
        "en": "Opening your reports.",
        "hi": "Aapki report khol raha hoon.",
    },
    "report_on_call": {
        "en": "I cannot send a report from this call. It is ready on your Saarthi "
              "dashboard whenever you open it.",
        "hi": "Is call se report bhej nahi sakta. Woh aapke Saarthi dashboard par "
              "taiyar hai, jab kholiye dekh lijiye.",
    },
    "stopped": {
        "en": "Stopped.",
        "hi": "Theek hai, rok diya.",
    },
    "nothing_to_repeat": {
        "en": "I have not said anything yet on this call.",
        "hi": "Is call par maine abhi kuch kaha hi nahi hai.",
    },
    "language_changed": {
        "en": "I will speak in English now.",
        "hi": "Ab main Hindi mein baat karoonga.",
    },
    "language_unknown": {
        "en": "I did not catch which language. Say Hindi, or English, or your "
              "own language.",
        "hi": "Samajh nahi aaya kaunsi bhasha. Hindi, English, ya apni bhasha "
              "ka naam boliye.",
    },
    "helpline": {
        "en": "The Kisan Call Centre is 1800 180 1551. It is free, and it is open "
              "from six in the morning to ten at night.",
        "hi": "Kisan Call Centre ka number hai 1800 180 1551. Yeh free hai, subah "
              "chhah baje se raat das baje tak khula rehta hai.",
    },
    "cannot_here": {
        "en": "I cannot do that from this call.",
        "hi": "Yeh main is call se nahi kar sakta.",
    },
    "chain_ready": {
        "en": "Here is what the check found.",
        "hi": "Jaanch mein yeh mila hai.",
    },
    "photo_on_call": {
        "en": "I cannot see a photo on this call. Open the Saarthi app, take "
              "the picture there, and it will check the leaf for you.",
        "hi": "Is call par main photo nahi dekh sakta. Saarthi app kholiye, "
              "wahan photo lijiye, wahi patte ki jaanch kar dega.",
    },
    "chain_not_ready": {
        "en": "The check is still running. Ask me again in a moment.",
        "hi": "Jaanch abhi chal rahi hai. Thodi der baad phir poochhiye.",
    },
    "not_your_voice": {
        "en": "This does not sound like your voice. I will still help, but I "
              "will not change anything on the account.",
        "hi": "Yeh aapki awaaz jaisi nahi lag rahi. Madad to karoonga, par "
              "account mein kuch badloonga nahi.",
    },
}


def say(key: str, language: str = "en", *args: Any) -> str:
    """The sentence for [key] in the farmer's language.

    Falls back to English rather than to the key, because a farmer hearing
    "reading_on_call" read aloud learns nothing at all.
    """
    phrases = PHRASES.get(key) or {}
    text = phrases.get((language or "en").lower()[:2]) or phrases.get("en") or ""
    return (text % args) if args else text


# The pages a voice command may open. A list and not "whatever the model said",
# because "open /admin" from a farmer's phone should be a sentence Saarthi does
# not understand rather than a redirect it refuses.
PAGES: Dict[str, Dict[str, Any]] = {
    "overview": {"label": "your farm overview",
                 "words": ("overview", "home", "dashboard", "ghar", "mukhya",
                           "मुख्य", "होम")},
    # Every item in the sidebar belongs here. "Pipeline results" and "AI agents"
    # were missing, so asking for them got "sorry, I cannot open that" - about
    # two pages sitting on the screen the farmer is looking at.
    "pipeline": {"label": "the pipeline results",
                 "words": ("pipeline", "pipeline results", "chain", "full check",
                           "jaanch result", "poori jaanch ka result", "results",
                           "nateeja", "पाइपलाइन", "नतीजा", "परिणाम")},
    # No "agents" page any more: asking about the fifteen takes the farmer to
    # the run that produced their findings, not to a list of the machinery.
    "profile": {"label": "your profile",
                "words": ("profile", "my profile", "mera profile",
                          "प्रोफाइल", "प्रोफ़ाइल")},
    "soil": {"label": "soil health",
             "words": ("soil", "mitti", "mati", "soil health", "मिट्टी", "मृदा")},
    "weather": {"label": "the weather",
                "words": ("weather", "mausam", "barish", "rain", "मौसम", "बारिश")},
    "pests": {"label": "pests and disease",
              "words": ("pest", "disease", "keeda", "keede", "rog", "bimari",
                        "कीड़ा", "कीड़े", "रोग")},
    "market": {"label": "mandi prices",
               "words": ("market", "mandi", "price", "bhav", "rate", "मंडी", "भाव")},
    "fields": {"label": "your fields",
               "words": ("field", "fields", "khet", "khets", "plot", "खेत")},
    "devices": {"label": "the sensor",
                "words": ("sensor", "device", "probe", "yantra", "सेंसर")},
    "finance": {"label": "your farm money",
                "words": ("finance", "money", "kharcha", "hisab", "paisa", "ledger",
                          "खर्च", "हिसाब", "पैसा")},
    "schemes": {"label": "government schemes",
                "words": ("scheme", "yojana", "sarkari", "subsidy", "योजना",
                          "सरकारी")},
    "reports": {"label": "your reports",
                "words": ("report", "reports", "riport", "रिपोर्ट")},
    "assistant": {"label": "the assistant",
                  "words": ("assistant", "chat", "sahayak", "सहायक")},
    "calls": {"label": "your calls",
              "words": ("calls", "call history", "baat", "कॉल")},
    "help": {"label": "farm support",
             "words": ("help", "support", "madad", "मदद", "सहायता")},
}

# "yes" in the languages a farmer actually answers in. Deliberately strict: a
# confirmation has to be a clear yes, and "theek hai chhodo" (fine, leave it) is
# not one, which is why the refusals are checked first.
_YES = {"yes", "yeah", "yep", "ok", "okay", "haan", "han", "ha", "ji", "ji haan",
        "theek hai", "thik hai", "kar do", "kar dijiye", "karo", "sure", "please",
        "हाँ", "हां", "जी", "ठीक है", "कर दो", "कीजिए"}
_NO = {"no", "nope", "nahi", "nahin", "mat", "rehne do", "chhodo", "chodo",
       "cancel", "ruko", "नहीं", "मत", "रहने दो", "छोड़ो", "रुको"}


@dataclass
class ActionResult:
    """What happened, and what to say about it."""

    ok: bool
    spoken: str = ""
    ui: Optional[Dict[str, Any]] = None
    data: Dict[str, Any] = field(default_factory=dict)
    needs_confirmation: bool = False
    pending: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "spoken": self.spoken, "ui": self.ui,
                "data": self.data, "needs_confirmation": self.needs_confirmation}


@dataclass
class Action:
    """One thing Saarthi can do, and how to recognise someone asking for it."""

    id: str
    risk: str
    summary: str
    # Phrases that mean "do something" rather than "tell me something". An action
    # only fires when one of these is present: "mandi bhav kya hai" is a question
    # for the agents, "mandi bhav dikhao" is a request to open a page.
    verbs: Sequence[str] = ()
    nouns: Sequence[str] = ()
    handler: Optional[Callable[..., ActionResult]] = None
    confirm_question: str = ""
    examples: Sequence[str] = ()


# --------------------------------------------------------------- recognising
# Words that turn a question into an instruction, across the languages in use.
_OPEN_WORDS = ("open", "show", "show me", "go to", "take me to", "dikhao",
               "dikha do", "dikhaiye", "kholo", "khol do", "kholiye", "batao page",
               "दिखाओ", "दिखाइए", "खोलो", "खोल", "दिखा")
_RUN_WORDS = ("run", "start", "chalao", "chala do", "shuru karo", "jaanch karo",
              "check karo", "poori jaanch", "चलाओ", "शुरू", "जाँच", "जांच")
# "reading" means one thing in this app, so it stands on its own. "fetch" and
# "padho" do not - "fetch the price", "report padho" - so those need a sensor
# word with them.
_READING_STRONG = ("reading", "naya reading", "sensor se", "रीडिंग")
_READING_WEAK = ("fetch", "padho", "le lo", "lo", "पढ़ो")
_READING_WORDS = _READING_STRONG + _READING_WEAK
_REPORT_WORDS = ("report", "riport", "रिपोर्ट")
# "Send me the report" counts as asking for one. It matters that it does: with
# this missing, the sentence reached the model and the model answered "main bhej
# doonga" - I will send it - which it had no way of doing. An action that says
# honestly where the report is beats a promise nobody can keep.
_MAKE_WORDS = _OPEN_WORDS + ("make", "make me", "generate", "create", "banao",
                             "bana do", "banaiye", "nikalo", "nikal do",
                             "chahiye", "send", "send me", "bhejo", "bhej do",
                             "bhej dijiye", "bhejiye", "बनाओ", "निकालो",
                             "भेजो", "भेज")
_STOP_WORDS = ("stop", "cancel", "band karo", "ruk jao", "rehne do", "बंद करो",
               "रुको")

# "Say that again." Said when the line broke up, which is most of why anybody
# says anything twice.
#
# "dobara" and "phir se" are NOT enough on their own, and finding that out cost
# a test: "phir se baat kar raha hoon" means "I am speaking to you again" - a
# greeting - and it was being answered by repeating the last sentence. "dobara
# call karna" is a request for a call-back. So the word for AGAIN has to arrive
# with a word about SAYING before it counts.
_REPEAT_STRONG = ("repeat", "repeat that", "say again", "say that again",
                  "come again", "pardon", "kya kaha", "kya bola",
                  "suna nahi", "sunai nahi", "samajh nahi aaya",
                  "क्या कहा")
_AGAIN_WORDS = ("dobara", "dubara", "phir se", "firse", "wapas",
                "दोबारा", "फिर से")
_SAYING_WORDS = ("boliye", "bolo", "bol", "batao", "bataiye", "kaho", "kahiye",
                 "sunao", "sunaiye", "say", "tell",
                 "बोलिए", "बताओ",
                 "सुनाओ")
_REPEAT_WORDS = _REPEAT_STRONG + _AGAIN_WORDS

# Switching language mid-call. The name of the language is the parameter.
_LANGUAGE_WORDS = ("language", "bhasha", "mein baat", "me baat", "bolo",
                   "boliye", "speak in", "talk in", "भाषा", "में बात")

# Wanting to send a picture of a leaf. On a screen that opens the pest page;
# on a call it is a thing that cannot happen, and has to be said so.
_PHOTO_WORDS = ("photo", "foto", "tasveer", "picture", "image", "camera",
                "\u092b\u094b\u091f\u094b", "\u0924\u0938\u094d\u0935\u0940\u0930")
# Offering to send or take one. Every form a farmer uses, written out: the
# matching is on whole words, so "kheech" does not cover "kheechni".
_PHOTO_VERBS = _MAKE_WORDS + ("bhejni", "bhejna", "bhejun", "bhej dun",
                              "lena", "leni", "lunga", "loon", "lu",
                              "kheech", "kheechni", "kheechna", "khichni",
                              "khichwani", "take", "upload", "click",
                              "भेज", "खींच")

# Asking what the chain found, after it was told to run.
_CHAIN_RESULT_WORDS = ("jaanch ka result", "jaanch ka natija", "result batao",
                       "natija batao", "kya mila", "kya nikla", "what did the "
                       "check find", "check result", "jaanch ho gayi",
                       "\u091c\u093e\u0901\u091a \u0915\u093e",
                       "\u0915\u094d\u092f\u093e \u092e\u093f\u0932\u093e")

# Asking for a human, or for the government helpline.
_HELPLINE_WORDS = ("helpline", "help line", "kisan call", "call centre",
                   "call center", "customer care", "aadmi se baat",
                   "insaan se baat", "kisi se baat", "operator", "agent se",
                   "हेल्पलाइन", "किसान कॉल", "आदमी से बात")

# The languages a caller names, and what they are called when they name them.
# Only the ones the project actually supports - offering a language the TTS
# cannot speak would switch the call into silence.
_LANGUAGE_NAMES = {
    "hi": ("hindi", "हिंदी", "हिन्दी"),
    "en": ("english", "angrezi", "angreji", "अंग्रेजी", "इंग्लिश"),
    "bn": ("bengali", "bangla", "बंगाली", "বাংলা"),
    "ta": ("tamil", "तमिल", "தமிழ்"),
    "te": ("telugu", "तेलुगु", "తెలుగు"),
    "mr": ("marathi", "मराठी"),
    "gu": ("gujarati", "गुजराती", "ગુજરાતી"),
    "kn": ("kannada", "कन्नड़", "ಕನ್ನಡ"),
    "ml": ("malayalam", "മലയാളം"),
    "pa": ("punjabi", "panjabi", "ਪੰਜਾਬੀ"),
    "or": ("odia", "oriya", "ଓଡ଼ିଆ"),
    "as": ("assamese", "অসমীয়া"),
    "ur": ("urdu", "उर्दू"),
}


def language_named(text: str) -> str:
    """The language code a sentence asks for, or "".

    Longest name first so "hindi" is not found inside a longer word and so the
    two-word names win where they overlap.
    """
    best, best_len = "", 0
    for code, names in _LANGUAGE_NAMES.items():
        for name in names:
            if _has(text, (name,)) and len(name) > best_len:
                best, best_len = code, len(name)
    return best


def _has(text: str, words: Sequence[str]) -> bool:
    body = " " + re.sub(r"\s+", " ", (text or "").lower().strip()) + " "
    for word in words:
        needle = word.lower()
        if " " in needle:
            if needle in body:
                return True
        elif re.search(r"(?<![\wऀ-ॿ])" + re.escape(needle)
                       + r"(?![\wऀ-ॿ])", body):
            return True
    return False


def page_for(text: str) -> Optional[str]:
    """Which dashboard page a sentence is asking for, if any.

    Longest match wins, so "farm support" is not read as "support" and then as
    a different page: the two-word names are the specific ones.
    """
    best: Optional[str] = None
    best_len = 0
    for page, spec in PAGES.items():
        for word in spec["words"]:
            if _has(text, (word,)) and len(word) > best_len:
                best, best_len = page, len(word)
    return best


def is_yes(text: str) -> bool:
    """A clear yes. Anything ambiguous is not one."""
    body = (text or "").strip().lower().strip(".!?,")
    if not body:
        return False
    if _has(body, tuple(_NO)):
        return False               # "theek hai chhodo" is a no, not a yes
    return body in _YES or _has(body, tuple(_YES))


def is_no(text: str) -> bool:
    return _has(text or "", tuple(_NO))


# ------------------------------------------------------------------ handlers
def _open_page(page: str, channel: str = SCREEN, language: str = "en",
               **_: Any) -> ActionResult:
    spec = PAGES[page]
    # On a call the page still opens - on whichever screen the farmer has - but
    # saying "Opening mandi prices" to someone holding a feature phone is a
    # promise about something they cannot see.
    key = "opened_elsewhere" if channel == CALL else "opened"
    return ActionResult(
        ok=True,
        spoken=say(key, language, spec["label"]),
        ui={"do": "navigate", "page": page, "label": spec["label"]},
        data={"page": page})


def _run_pipeline(channel: str = SCREEN, language: str = "en",
                  **_: Any) -> ActionResult:
    # The chain takes seconds and the farmer is on a call. It is started, not
    # waited for, and the answer arrives on the page or on the next turn.
    return ActionResult(
        ok=True,
        spoken=say("pipeline_on_call" if channel == CALL else "pipeline_started",
                   language),
        ui={"do": "run_pipeline"},
        data={"started": True, "run_now": channel == CALL})


def _take_reading(channel: str = SCREEN, language: str = "en",
                  **_: Any) -> ActionResult:
    if channel == CALL:
        # Honest: the probe pairs with the app over Bluetooth. Nothing on this
        # call can reach it, and pretending otherwise leaves a farmer waiting
        # for a number that is never coming.
        return ActionResult(ok=True, spoken=say("reading_on_call", language),
                            data={"possible": False})
    return ActionResult(
        ok=True,
        spoken=say("reading_screen", language),
        ui={"do": "navigate", "page": "devices", "then": "fetch_reading"},
        data={"page": "devices"})


def _make_report(channel: str = SCREEN, language: str = "en",
                 **_: Any) -> ActionResult:
    if channel == CALL:
        # There is no SMS path out of this server - the only outbound channel is
        # the call itself, through n8n. Saying so is better than "main bhej
        # doonga", which is what the model said before this existed and which it
        # had no way of doing.
        return ActionResult(ok=True, spoken=say("report_on_call", language),
                            ui={"do": "navigate", "page": "reports"},
                            data={"sent": False})
    return ActionResult(
        ok=True,
        spoken=say("report_screen", language),
        ui={"do": "navigate", "page": "reports", "then": "make_report"},
        data={"page": "reports"})


def _stop(language: str = "en", **_: Any) -> ActionResult:
    return ActionResult(ok=True, spoken=say("stopped", language),
                        ui={"do": "stop"}, data={"stopped": True})


def _repeat(language: str = "en", last_reply: str = "", **_: Any) -> ActionResult:
    """Say the last thing again - the same words, not a new sentence.

    Asked "dobara boliye", the model used to generate a fresh reply, which is
    not repeating: the farmer asked again precisely because they did not catch
    it the first time, and a different sentence is the one thing that does not
    help.
    """
    words = (last_reply or "").strip()
    if not words:
        return ActionResult(ok=True, spoken=say("nothing_to_repeat", language),
                            data={"repeated": False})
    return ActionResult(ok=True, spoken=words, data={"repeated": True})


def _set_language(language: str = "en", wanted: str = "", **_: Any) -> ActionResult:
    """Switch the language of the call.

    The confirmation is spoken in the NEW language, which is the only way the
    farmer can tell it worked.
    """
    if not wanted:
        return ActionResult(ok=True, spoken=say("language_unknown", language),
                            data={"changed": False})
    return ActionResult(ok=True, spoken=say("language_changed", wanted),
                        data={"changed": True, "language": wanted})


def _send_photo(channel: str = SCREEN, language: str = "en",
                **_: Any) -> ActionResult:
    """Where a leaf photo can go.

    On a call the honest answer is "not here". The model, left to itself,
    answered "theek hai, aap photo bhej dijiye" - fine, send the photo - down a
    voice line, which is a promise nobody could keep and a farmer left waiting.
    """
    if channel == CALL:
        return ActionResult(ok=True, spoken=say("photo_on_call", language),
                            data={"possible": False})
    return ActionResult(ok=True, spoken=say("opened", language, "pest and disease"),
                        ui={"do": "navigate", "page": "pests"},
                        data={"page": "pests"})


def _chain_result(language: str = "en", findings: Sequence[str] = (),
                  **_: Any) -> ActionResult:
    """Say what the fifteen agents found, on a call.

    The chain writes its findings into the farm snapshot when it finishes; this
    reads them back. Two or three lines, not fifteen - a spoken turn can carry
    about that much before the farmer has lost the beginning of it, and the rest
    is on the dashboard.
    """
    lines = [str(line).strip() for line in (findings or []) if str(line).strip()]
    if not lines:
        return ActionResult(ok=True, spoken=say("chain_not_ready", language),
                            data={"ready": False})
    spoken = say("chain_ready", language) + " " + " ".join(lines[:3])
    return ActionResult(ok=True, spoken=spoken,
                        data={"ready": True, "lines": len(lines)})


def _helpline(language: str = "en", **_: Any) -> ActionResult:
    """The Kisan Call Centre number, said slowly enough to write down.

    A real, national, free number - not this deployment's own line, which is a
    demo number and would be the wrong thing to give a farmer who needs help.
    """
    return ActionResult(ok=True, spoken=say("helpline", language),
                        data={"number": "1800 180 1551"})


REGISTRY: Dict[str, Action] = {
    "open_page": Action(
        id="open_page", risk=READ, handler=_open_page,
        summary="Open a page of the dashboard or the app",
        verbs=_OPEN_WORDS,
        examples=("mandi bhav dikhao", "open my fields", "mitti wala page kholo")),
    "run_pipeline": Action(
        id="run_pipeline", risk=READ, handler=_run_pipeline,
        summary="Run the full fifteen-agent check",
        verbs=_RUN_WORDS, nouns=("pipeline", "full check", "poori jaanch",
                                 "sab kuch", "jaanch", "जाँच"),
        examples=("poori jaanch karo", "run the full check")),
    "take_reading": Action(
        id="take_reading", risk=READ, handler=_take_reading,
        summary="Open the sensor page to take a soil reading",
        verbs=_READING_WORDS, nouns=("sensor", "probe", "mitti", "सेंसर"),
        examples=("sensor se reading lo", "take a new reading")),
    "make_report": Action(
        id="make_report", risk=READ, handler=_make_report,
        summary="Open the reports page",
        verbs=_REPORT_WORDS, examples=("report banao", "make me a report")),
    "stop": Action(
        id="stop", risk=READ, handler=_stop,
        summary="Stop whatever is running",
        verbs=_STOP_WORDS, examples=("band karo", "stop")),
    # ---- these three earn their place on a phone call, where there is no
    # screen to fall back on and no way to scroll back.
    "repeat": Action(
        id="repeat", risk=READ, handler=_repeat,
        summary="Say the last answer again, in the same words",
        verbs=_REPEAT_WORDS, examples=("dobara boliye", "say that again")),
    "set_language": Action(
        id="set_language", risk=READ, handler=_set_language,
        summary="Switch the language of this call",
        verbs=_LANGUAGE_WORDS,
        examples=("english mein baat karo", "speak in Hindi")),
    "helpline": Action(
        id="helpline", risk=READ, handler=_helpline,
        summary="Give the Kisan Call Centre number",
        verbs=_HELPLINE_WORDS,
        examples=("kisi aadmi se baat karani hai", "helpline number batao")),
    "send_photo": Action(
        id="send_photo", risk=READ, handler=_send_photo,
        summary="Say where a leaf photo can actually be sent",
        verbs=_PHOTO_WORDS,
        examples=("patte ki photo bhejni hai", "send a photo of the leaf")),
    "chain_result": Action(
        id="chain_result", risk=READ, handler=_chain_result,
        summary="Read out what the fifteen-agent check found",
        verbs=_CHAIN_RESULT_WORDS,
        examples=("jaanch ka result batao", "what did the check find")),
}


# ----------------------------------------------------------------- detection
@dataclass
class Detected:
    action: Optional[Action] = None
    params: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0

    @property
    def found(self) -> bool:
        return self.action is not None


def detect(said: str) -> Detected:
    """Which action a sentence is asking for - by rule, never by a model.

    An action changes something or moves the farmer somewhere, so the cost of
    getting it wrong is higher than for an intent. Every match here is a word the
    farmer actually said, which means a wrong match can be explained and fixed;
    a model's guess cannot.

    The order matters. "stop" is checked first because it must work while
    something else is running, and navigation last because its vocabulary is the
    broadest and would otherwise swallow the others.
    """
    text = (said or "").strip()
    if not text:
        return Detected()

    if _has(text, _STOP_WORDS):
        return Detected(REGISTRY["stop"], {}, 0.9)

    # Before everything else: a farmer who did not catch the last answer is not
    # asking a new question, and running one would bury the answer they wanted.
    if _has(text, _REPEAT_STRONG) or (
            _has(text, _AGAIN_WORDS) and _has(text, _SAYING_WORDS)):
        return Detected(REGISTRY["repeat"], {}, 0.9)

    # A named language plus a word about speaking. The language name alone is
    # not enough - "Hindi mein kya likha hai" is a question about a document.
    wanted = language_named(text)
    if wanted and _has(text, _LANGUAGE_WORDS):
        return Detected(REGISTRY["set_language"], {"wanted": wanted}, 0.85)

    # Only when they are offering to SEND one. "photo mein patte pile dikh
    # rahe hain" is a description of a problem, and an answer to it.
    # Offering to send or take one. Matching is on whole words, so every form a
    # farmer uses has to be listed - "kheech" does not match "kheechni", which
    # is how most people say it.
    if _has(text, _PHOTO_WORDS) and _has(text, _PHOTO_VERBS):
        return Detected(REGISTRY["send_photo"], {}, 0.8)

    if _has(text, _CHAIN_RESULT_WORDS):
        return Detected(REGISTRY["chain_result"], {}, 0.8)

    if _has(text, _HELPLINE_WORDS):
        return Detected(REGISTRY["helpline"], {}, 0.8)

    if _has(text, _RUN_WORDS) and _has(text, REGISTRY["run_pipeline"].nouns):
        return Detected(REGISTRY["run_pipeline"], {}, 0.85)

    if _has(text, _READING_STRONG) or (
            _has(text, _READING_WEAK)
            and _has(text, REGISTRY["take_reading"].nouns)):
        return Detected(REGISTRY["take_reading"], {}, 0.8)

    # A report is a thing you ask to be MADE; without a verb "report" is just a
    # word in a sentence about something else ("the report says my pH is low").
    if _has(text, _REPORT_WORDS) and _has(text, _MAKE_WORDS):
        return Detected(REGISTRY["make_report"], {}, 0.8)

    if _has(text, _OPEN_WORDS):
        page = page_for(text)
        if page:
            return Detected(REGISTRY["open_page"], {"page": page}, 0.85)
        # "dikhao" with nothing recognisable after it. Better to answer the
        # question than to open a page at random.
        return Detected()

    return Detected()


def run(detected: Detected, *, channel: str = SCREEN, language: str = "en",
        context: Optional[Dict[str, Any]] = None) -> ActionResult:
    """Carry out a detected action. Never raises.

    ``channel`` and ``language`` reach the handler because the same instruction
    means different things on a screen and down a phone line, and because a
    reply that goes straight into a farmer's ear has to be in their language.
    ``context`` carries what a handler cannot work out for itself - the last
    thing said on this call, for instance.
    """
    if not detected.found or detected.action.handler is None:
        return ActionResult(ok=False, spoken="")
    action = detected.action
    if action.risk == CONFIRM:
        return ActionResult(
            ok=True, needs_confirmation=True,
            spoken=action.confirm_question,
            pending={"action": action.id, "params": dict(detected.params)})
    try:
        return action.handler(channel=channel, language=language,
                              **dict(context or {}), **detected.params)
    except Exception:                                              # noqa: BLE001
        return ActionResult(ok=False, spoken=say("cannot_here", language))


def run_pending(pending: Dict[str, Any], *, channel: str = SCREEN,
                language: str = "en",
                context: Optional[Dict[str, Any]] = None) -> ActionResult:
    """Carry out something the farmer has just said yes to."""
    action = REGISTRY.get(str(pending.get("action") or ""))
    if action is None or action.handler is None:
        return ActionResult(ok=False, spoken="")
    try:
        return action.handler(channel=channel, language=language,
                              **dict(context or {}),
                              **dict(pending.get("params") or {}))
    except Exception:                                              # noqa: BLE001
        return ActionResult(ok=False, spoken=say("cannot_here", language))


def describe() -> List[Dict[str, Any]]:
    """Everything the voice can do, for the dashboard to list and for the docs."""
    # Where each one is useful. "repeat" and "set_language" exist because a
    # phone call cannot be scrolled back or re-read; "take_reading" is listed
    # for both because a caller asking for it gets a real answer, just not the
    # one they wanted.
    CALL_ONLY = {"repeat", "set_language", "helpline"}
    return [
        {"id": a.id, "risk": a.risk, "summary": a.summary,
         "examples": list(a.examples),
         "channels": ["call"] if a.id in CALL_ONLY else ["screen", "call"]}
        for a in REGISTRY.values()
    ] + [
        {"id": "record_expense", "risk": WRITE,
         "summary": "Write down money spent or earned",
         "channels": ["screen", "call"],
         "examples": ["do hazaar ka urea liya", "sold wheat for twelve thousand"]},
        {"id": "book_call", "risk": WRITE,
         "summary": "Ring you back later, or watch a price and ring when it moves",
         "channels": ["screen", "call"],
         "examples": ["shaam ko paanch baje call karna",
                      "call me when wheat crosses 2400"]},
    ]


def pages() -> List[Dict[str, str]]:
    return [{"page": page, "label": spec["label"]} for page, spec in PAGES.items()]
