"""Language registry for Saarthi 2.0.

13 languages (English + 12 Indian languages). Each entry carries the Azure Speech locale
and neural voice used for STT/TTS, so the UI, the chatbot and the telephony
layer all agree on one list.

Voice names are Azure's published neural voices for these locales. If a voice
is unavailable on a given Speech resource, Azure returns an error and the
caller surfaces it; we never silently substitute a different language.

**Every voice here is female, and that is a requirement, not a coincidence.**
A caller does not change language to change who is speaking to them. Punjabi
was pa-IN-OjasNeural, the one male voice in the table, so switching to Punjabi
mid-call turned Saarthi into a man - on a line where the same assistant is
supposed to be remembering your last call. ``test_one_voice_throughout`` holds
this shut; if a language is ever added with a male voice it fails.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Language:
    code: str           # internal short code
    english_name: str
    native_name: str
    locale: str         # Azure Speech locale
    voice: str          # Azure neural voice
    dtmf: str           # keypad digit for feature-phone IVR


LANGUAGES: List[Language] = [
    Language("en", "English",   "English",   "en-IN", "en-IN-NeerjaNeural",     "1"),
    Language("hi", "Hindi",     "हिन्दी",      "hi-IN", "hi-IN-SwaraNeural",      "2"),
    Language("bn", "Bengali",   "বাংলা",      "bn-IN", "bn-IN-TanishaaNeural",   "3"),
    Language("ta", "Tamil",     "தமிழ்",      "ta-IN", "ta-IN-PallaviNeural",    "4"),
    Language("te", "Telugu",    "తెలుగు",     "te-IN", "te-IN-ShrutiNeural",     "5"),
    Language("mr", "Marathi",   "मराठी",      "mr-IN", "mr-IN-AarohiNeural",     "6"),
    Language("gu", "Gujarati",  "ગુજરાતી",     "gu-IN", "gu-IN-DhwaniNeural",     "7"),
    Language("kn", "Kannada",   "ಕನ್ನಡ",      "kn-IN", "kn-IN-SapnaNeural",      "8"),
    Language("ml", "Malayalam", "മലയാളം",    "ml-IN", "ml-IN-SobhanaNeural",    "9"),
    Language("pa", "Punjabi",   "ਪੰਜਾਬੀ",      "pa-IN", "pa-IN-VaaniNeural",      "10"),
    Language("or", "Odia",      "ଓଡ଼ିଆ",       "or-IN", "or-IN-SubhasiniNeural",  "11"),
    Language("as", "Assamese",  "অসমীয়া",    "as-IN", "as-IN-YashicaNeural",    "12"),
    Language("ur", "Urdu",      "اردو",       "ur-IN", "ur-IN-GulNeural",        "13"),
    # Nepali is the fourteenth, and it writes Devanagari - so it joins Hindi
    # and Marathi in the pair below, where the script cannot decide and the
    # language already in force wins.
    Language("ne", "Nepali",    "नेपाली",      "ne-NP", "ne-NP-HemkalaNeural",    "14"),
]

BY_CODE: Dict[str, Language] = {lang.code: lang for lang in LANGUAGES}
BY_LOCALE: Dict[str, Language] = {lang.locale: lang for lang in LANGUAGES}
DEFAULT = BY_CODE["en"]


def resolve(code: Optional[str]) -> Language:
    """Accept a short code, a locale, an English name or a native name."""
    if not code:
        return DEFAULT
    raw = str(code).strip()
    low = raw.lower()
    if low in BY_CODE:
        return BY_CODE[low]
    for lang in LANGUAGES:
        if low in (lang.locale.lower(), lang.english_name.lower()) or raw == lang.native_name:
            return lang
    return BY_CODE.get(low.split("-")[0], DEFAULT)


def by_dtmf(digits: str) -> Optional[Language]:
    for lang in LANGUAGES:
        if lang.dtmf == str(digits).strip():
            return lang
    return None


def as_dicts() -> List[dict]:
    return [
        {
            "code": l.code,
            "english_name": l.english_name,
            "native_name": l.native_name,
            "locale": l.locale,
            "voice": l.voice,
            "dtmf": l.dtmf,
        }
        for l in LANGUAGES
    ]


# --------------------------------------------------- which language is this text in
# Every Indian language here has its own Unicode block, with two pairs that
# share one: Hindi and Marathi both write Devanagari, Bengali and Assamese both
# write Bengali script. For those two pairs the script cannot decide, so the
# language already in force wins - a Marathi speaker typing Marathi stays in
# Marathi rather than being switched to Hindi on every turn.
SCRIPTS = (
    ((0x0900, 0x097F), "hi", ("hi", "mr", "ne")),
    ((0x0980, 0x09FF), "bn", ("bn", "as")),
    ((0x0A00, 0x0A7F), "pa", ("pa",)),
    ((0x0A80, 0x0AFF), "gu", ("gu",)),
    ((0x0B00, 0x0B7F), "or", ("or",)),
    ((0x0B80, 0x0BFF), "ta", ("ta",)),
    ((0x0C00, 0x0C7F), "te", ("te",)),
    ((0x0C80, 0x0CFF), "kn", ("kn",)),
    ((0x0D00, 0x0D7F), "ml", ("ml",)),
    ((0x0600, 0x06FF), "ur", ("ur",)),
)

# Below this share of the letters, the text is too mixed to call. A farmer who
# says "mandi rate kya hai" with an English word in it has not changed language.
SCRIPT_SHARE = 0.6

# Latin letters are not evidence of English on their own: half of rural India
# types Hindi and Punjabi in the Latin alphabet, and a transcript can come back
# romanised. Two of these in a sentence and it is not English, whatever the
# alphabet says.
ROMANISED = {
    "kya", "hai", "hain", "kitna", "kitne", "mujhe", "mera", "meri", "batao",
    "bata", "bhav", "bhaav", "mandi", "khet", "fasal", "kal", "aaj", "nahi",
    "nahin", "haan", "kaise", "kaisa", "kab", "kyun", "chahiye", "karo", "do",
    "hoga", "raha", "rahe", "ke", "ka", "ki", "se", "mein", "par", "accha",
    "theek", "paani", "beej", "khad", "dawa", "zameen", "kisan", "sahab",
    "ji", "bhai", "dekho", "sunno", "suno", "bolo", "da", "di", "nu", "aje",
}


# Punjabi, as a Hindi recogniser writes it down. Chosen to be words a Hindi
# sentence would not use: the postpositions and the address forms, which are
# where the two languages differ most and which appear in almost any sentence.
PUNJABI_WORDS = {
    "मैनू", "मैनूं",      # mainu
    "तुहाडा", "तुसीं",  # tuhada, tusi
    "दसो", "दस्सो",            # dasso
    "कीहनूं",                               # kihnu
    "अते", "नहीं जी",
    "कणक",                                                   # kanak (wheat)
    "सति", "अकाल",                   # sat sri akal
    "हैगा", "किवें",      # haiga, kiven
    "डा", "डी", "डे",                      # da, di, de
    "वेखो", "आखो",                   # vekho, aakho
    "किंना", "किंनी",
}


def now_or_blank(current: Optional[str]) -> str:
    """The language in force, when the text is not evidence of a change."""
    now = (current or "").lower()
    return now if now in BY_CODE else ""


def detect(text: str, current: Optional[str] = None) -> str:
    """Which of our languages this text is written in, or "" for no opinion.

    Used on the telephony path, where the transcript arrives as text and there
    is no audio to identify. The browser and the app have the audio and use
    Azure's own language identification, which is better; this is the answer
    for the channel that cannot.
    """
    body = str(text or "")
    if not body.strip():
        return ""
    counts: Dict[str, int] = {}
    latin = 0
    for ch in body:
        point = ord(ch)
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            latin += 1
            continue
        for (low, high), code, _pair in SCRIPTS:
            if low <= point <= high:
                counts[code] = counts.get(code, 0) + 1
                break
    letters = latin + sum(counts.values())
    if letters < 3:
        return ""                      # "ok", "haan", a digit: not evidence
    if not counts:
        if latin / letters < SCRIPT_SHARE:
            return ""
        words = {w.strip(".,?!;:").lower() for w in body.split()}
        if len(words & ROMANISED) >= 2:
            # Romanised Hindi or Punjabi. The script cannot say which, so the
            # language already in force stands.
            return now_or_blank(current)
        if len(body.split()) < 3:
            # "ok", "haan ji", "yes" - an acknowledgement in any language looks
            # like English in the Latin alphabet, and is not a change of
            # language. Indic scripts need no such guard: one word of Tamil is
            # still unmistakably Tamil.
            return ""
        return "en"

    code = max(counts, key=lambda k: counts[k])
    if counts[code] / letters < SCRIPT_SHARE:
        return ""

    # Devanagari is not proof of Hindi on a phone line.
    #
    # A telephone recogniser is given ONE locale for the whole call, so a
    # farmer who switches to Punjabi is still transcribed by a Hindi engine:
    # Punjabi words come back in Devanagari letters. Reading the alphabet said
    # "Hindi" every time, which is exactly how a caller who went English ->
    # Hindi -> Punjabi got Hindi for the rest of the call. These are words a
    # Hindi sentence does not contain.
    if code == "hi":
        words = {w.strip(".,?!;:।").lower() for w in body.split()}
        if len(words & PUNJABI_WORDS) >= 2:
            return "pa"

    pair = next(p for (_r, c, p) in SCRIPTS if c == code)
    now = (current or "").lower()
    if now in pair:
        return now                     # the shared script keeps the current one
    return code


# The script each language is written in, by its English name. Used in the
# prompt: "reply in Punjabi, written in Gurmukhi" is followed; "reply in
# Punjabi" alone is sometimes answered in Devanagari, and the voice that reads
# it out is chosen from the script.
SCRIPT_NAME = {
    "en": "Latin", "hi": "Devanagari", "mr": "Devanagari", "ne": "Devanagari",
    "bn": "Bengali", "as": "Assamese", "pa": "Gurmukhi",
    "gu": "Gujarati", "or": "Odia", "ta": "Tamil", "te": "Telugu",
    "kn": "Kannada", "ml": "Malayalam", "ur": "Urdu",
}


def script_of(code: Optional[str]) -> str:
    return SCRIPT_NAME.get(resolve(code).code, "Devanagari")


# A reply can pass `detect` and still be in the wrong language. Measured live:
# "Soil moisture 79.0 % ਹੈ, ਜੋ ਕਿ high ਹੈ" is two thirds Latin letters, so the
# share rule called it English - and it was answering an English question, so
# nothing looked wrong. A farmer who reads only English cannot read it.
#
# Mixing English WORDS into an Indian-language sentence is normal and wanted;
# mixing an Indian SCRIPT into an English sentence is not, and neither is
# mixing the wrong Indian script into any of them.
FOREIGN_SCRIPT_SHARE = 0.2


def dominant_indic(text: str) -> tuple:
    """The Indian script most present in a text, and what share of it that is.

    Returns ``("", 0.0)`` for text with no Indian script in it at all.
    """
    body = str(text or "")
    counts: Dict[str, int] = {}
    letters = 0
    for ch in body:
        point = ord(ch)
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            letters += 1
            continue
        for (low, high), code, _pair in SCRIPTS:
            if low <= point <= high:
                counts[code] = counts.get(code, 0) + 1
                letters += 1
                break
    if not counts or not letters:
        return "", 0.0
    code = max(counts, key=lambda k: counts[k])
    return code, counts[code] / letters
