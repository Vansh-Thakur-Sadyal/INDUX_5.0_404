"""Which language the caller is speaking, decided from the audio itself.

The phone line's problem, stated plainly: a telephone recogniser is handed ONE
locale when the call connects and transcribes the whole call through it. A
caller who switches to Punjabi is still written down by a Hindi engine, so the
transcript says Devanagari and the reply comes back in Hindi. Reading the text
can recover a lot of that - and does, as a fallback - but the only thing that
really knows what was said is the sound.

So when the telephony layer can hand over the caller's audio, Azure's own
language identification decides, and everything else gives way to it.

The rules, in the order they are applied
----------------------------------------
1. **The greeting is never touched.** The call opens exactly as configured, in
   whatever language it was configured in. Identification starts with the
   caller's first real answer, not before it.
2. **Audio wins.** Azure is given the whole language list and returns the
   locale it heard with a confidence. Above the bar, that is the language.
3. **A short noise is not a language.** An "mm", a cough, one word - these are
   not evidence of a switch, and a clip under `MIN_SPEECH_MS` is never allowed
   to change anything.
4. **Uncertain means keep what you had.** Not "guess", not "fall back to
   English", and never "say it was detected". The caller stays in the language
   they were already being answered in.
5. **Script is evidence too.** Azure writes its transcript in the script of
   whatever it heard. Gurmukhi is Punjabi and Tamil is Tamil - there is nothing
   to be unsure about - so when the confidence score is short of the bar but
   the words came back in a different script, that settles it on this turn
   rather than the next one. The two scripts shared by two languages each
   (Devanagari, Bengali) cannot settle anything, and `languages.detect` keeps
   the language in force for those.
6. **Nothing is claimed that did not happen.** The result says where the
   decision came from - `azure-lid`, `script`, `kept` or `default` - and the
   caller-facing layer reports it as-is.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from ..core import languages
from . import azure_speech

log = logging.getLogger("saarthi.lid")

# Below this, the identification is a guess. Measured across the thirteen Azure
# voices: a full sentence identifies at 0.69 to 0.94, and the one reading that
# came back wrong was a four-word clip at 0.71.
MIN_CONFIDENCE = 0.55

# And below this much speech, nothing is allowed to change the language at all.
# "Haan", "mm", a cough: a caller agreeing is not a caller switching language.
MIN_SPEECH_MS = 1200

# A spoken turn is budgeted at three seconds end to end, and synthesis wants
# most of what is left after the model. Identification that has not answered
# by now is identification the farmer is paying for in silence: measured, a
# throttled request took 7.4 s to come back and refuse.
IDENTIFY_BUDGET = 2.5

# Azure takes ten candidate locales and there are fourteen languages, so ten
# have to be chosen. The language already in force is always among them - a
# conversation being held in Odia must be able to stay in Odia - and the rest
# are in order of how many farmers speak them. The three that fall off are
# still reachable: the picker sets them outright, and the script of the
# transcript can still move the answer to one of them.
POPULAR = ("hi", "en", "pa", "bn", "mr", "ta", "te", "gu", "kn", "ml",
           "or", "as", "ur", "ne")
MAX_CANDIDATES = 10


def candidates_for(held: str) -> list:
    """Ten locales to offer Azure, with the language in force first."""
    order = [languages.resolve(held).code] if held else []
    order += [code for code in POPULAR if code not in order]
    return [languages.BY_CODE[code].locale
            for code in order[:MAX_CANDIDATES] if code in languages.BY_CODE]

# A recording is a few seconds of 8 kHz telephony audio. Anything much larger
# is not a turn, and fetching it would be somebody else's bandwidth bill.
MAX_AUDIO_BYTES = 8 * 1024 * 1024
FETCH_TIMEOUT = 12.0


@dataclass
class Decision:
    """Which language to answer this turn in, and how that was arrived at."""

    language: str
    source: str                 # azure-lid | script | kept | default
    confidence: float = 0.0
    detected: str = ""          # what was identified, even when not acted on
    transcript: str = ""        # Azure's own words, when it produced any
    note: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def certain(self) -> bool:
        # Script counts. A transcript in Gurmukhi is not a guess about whether
        # the caller is speaking Punjabi.
        return self.source in ("azure-lid", "script")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "language": self.language,
            "language_source": self.source,
            "language_confidence": round(self.confidence, 3),
            # Empty when nothing was identified. It is NOT filled in with the
            # language in force: saying a language was detected when it was not
            # is the one thing this must never do.
            "detected_language": self.detected,
            "language_note": self.note,
        }


def configured() -> bool:
    return azure_speech.configured()


async def from_audio(audio: bytes, *, current: str = "",
                     filename: str = "turn.wav",
                     content_type: str = "audio/wav",
                     candidates: Optional[List[str]] = None) -> Decision:
    """Identify the language of one spoken turn.

    ``current`` is the language the conversation is already being held in, and
    it is what comes back whenever the audio does not clearly say otherwise.
    """
    held = languages.resolve(current).code if current else ""

    if not audio:
        return Decision(held or languages.DEFAULT.code,
                        "kept" if held else "default",
                        note="No audio was supplied with this turn.")
    if len(audio) > MAX_AUDIO_BYTES:
        return Decision(held or languages.DEFAULT.code,
                        "kept" if held else "default",
                        note="That recording was too large to identify.")
    if not azure_speech.configured():
        return Decision(held or languages.DEFAULT.code,
                        "kept" if held else "default",
                        note="Azure Speech is not configured, so the language "
                             "was not identified from the audio.")

    found = await azure_speech.identify(audio, filename=filename,
                                        content_type=content_type,
                                        candidates=candidates or candidates_for(held),
                                        timeout=IDENTIFY_BUDGET)
    if not found.ok:
        log.info("language identification unavailable: %s", found.reason[:160])
        # Azure refused or was too slow. There is still a farmer waiting and
        # still a clip to transcribe, so ask the other recogniser: Sarvam
        # returns words but no locale, and the SCRIPT of those words settles
        # eleven of the fourteen on its own.
        return await _from_other_recogniser(
            audio, held, filename=filename, content_type=content_type,
            why=found.reason[:200])

    detail = found.detail or {}
    code = str(detail.get("language") or "")
    confidence = float(detail.get("confidence") or 0.0)
    duration = int(detail.get("duration_ms") or 0)
    text = (found.text or "").strip()

    # Too little speech to call it. Said as "kept", not as a detection -
    # unless the words themselves are in another script, which is not a guess.
    if duration and duration < MIN_SPEECH_MS:
        return _from_script(text, held, confidence, duration,
                            "Too short to identify a language from.")

    if not code or confidence < MIN_CONFIDENCE:
        return _from_script(
            text, held, confidence, duration,
            "The language was not identified with enough confidence, so the "
            "conversation stays in %s."
            % (languages.resolve(held).english_name if held
               else languages.DEFAULT.english_name))

    if held and code != held:
        log.info("caller switched %s -> %s (%.2f) mid-call", held, code, confidence)
    return Decision(code, "azure-lid", confidence=confidence, detected=code,
                    transcript=text,
                    note="Identified from the audio.",
                    detail={"duration_ms": duration,
                            "locale": found.locale,
                            "candidates": detail.get("candidates")})


def _from_script(text: str, held: str, confidence: float, duration: int,
                 why: str) -> Decision:
    """The audio did not settle it. See whether the writing does.

    This is the difference between a caller switching to Punjabi and being
    answered in Punjabi on the SAME turn, or on the one after it - and on a
    phone call the one after it is a whole exchange spent in the wrong
    language. It is only ever allowed to move to a language whose script says
    so outright: `languages.detect` returns the language already in force for
    Devanagari and Bengali, which two languages each share.
    """
    # An INDIAN script, and enough of one to mean it. Latin letters are not
    # evidence of anything: "mujhe kanak da bhaa dasso" is Punjabi written in
    # the Latin alphabet, and "hmm" is not English.
    script, share = languages.dominant_indic(text or "")
    if not script or share < languages.FOREIGN_SCRIPT_SHARE:
        return Decision(held or languages.DEFAULT.code,
                        "kept" if held else "default",
                        confidence=confidence, detected="", transcript=text,
                        note=why, detail={"duration_ms": duration})

    # detect() rather than the script's own default language, because
    # Devanagari is Hindi AND Marathi and Bengali is Bengali AND Assamese; for
    # those two it returns the language already in force, so nothing moves.
    guess = languages.detect(text, held)
    if guess and held and guess != held:
        log.info("caller switched %s -> %s mid-call, from the script of the "
                 "transcript", held, guess)
        return Decision(guess, "script", confidence=confidence, detected=guess,
                        transcript=text,
                        note="Identified from the script of the transcript.",
                        detail={"duration_ms": duration})
    return Decision(held or languages.DEFAULT.code,
                    "kept" if held else "default",
                    confidence=confidence, detected="", transcript=text,
                    note=why, detail={"duration_ms": duration})


async def _from_other_recogniser(audio: bytes, held: str, *, filename: str,
                                 content_type: str, why: str) -> Decision:
    """Azure could not identify it. Transcribe with Sarvam and read the script.

    This is the difference between a throttled minute costing accuracy and a
    throttled minute costing the feature. The language is reported as coming
    from the script, never as "identified from the audio", because it did not
    come from the audio - Sarvam does not say what it heard.
    """
    from . import voice_io                     # local: voice_io imports this

    try:
        heard = await voice_io.transcribe(audio, held or languages.DEFAULT.code,
                                          filename=filename,
                                          content_type=content_type)
    except Exception as exc:                                      # noqa: BLE001
        log.info("fallback recogniser failed: %s", str(exc)[:160])
        heard = None
    text = (heard.text or "").strip() if (heard and heard.ok) else ""
    if not text:
        return Decision(held or languages.DEFAULT.code,
                        "kept" if held else "default", note=why)

    guess = languages.detect(text, held)
    if guess and guess != held:
        return Decision(guess, "script", detected=guess, transcript=text,
                        note="Identified from the script of the transcript.",
                        detail={"recogniser": heard.provider, "why": why})
    return Decision(held or languages.DEFAULT.code,
                    "kept" if held else "default", transcript=text,
                    note=why, detail={"recogniser": heard.provider})


async def fetch(url: str, *, auth: Optional[tuple] = None) -> Optional[bytes]:
    """Download a recording the telephony layer pointed at.

    Twilio's recording URLs need the account's own credentials, which this
    server does not hold - n8n does. So a URL that comes back unauthorised is
    reported as nothing rather than retried: the fix is for n8n to send the
    bytes, and pretending otherwise would hide that.
    """
    if not url or not str(url).lower().startswith(("http://", "https://")):
        return None
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT,
                                     follow_redirects=True) as client:
            response = await client.get(url, auth=auth)
    except Exception as exc:                                      # noqa: BLE001
        log.info("could not fetch the turn audio: %s", str(exc)[:160])
        return None
    if response.status_code != 200:
        log.info("turn audio came back HTTP %s", response.status_code)
        return None
    data = response.content
    if not data or len(data) > MAX_AUDIO_BYTES:
        return None
    return data


def decode(encoded: str) -> Optional[bytes]:
    """Audio sent inline as base64, which is how n8n can pass it directly."""
    text = str(encoded or "").strip()
    if not text:
        return None
    # A data: URL is accepted too, because that is what a browser produces and
    # an n8n Function node often passes through unchanged.
    if text.startswith("data:"):
        _, _, text = text.partition(",")
    try:
        data = base64.b64decode(text, validate=False)
    except Exception:                                             # noqa: BLE001
        return None
    return data if data and len(data) <= MAX_AUDIO_BYTES else None
