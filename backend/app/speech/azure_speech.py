"""Azure Speech: multilingual speech-to-text and text-to-speech.

Four paths, all through the same key:

* ``synthesize``  - REST TTS. Used by the dashboard voice replies and by the
  telephony layer, which hands n8n a URL to the generated audio.
* ``recognize``   - REST STT for a short audio clip, against ONE locale.
* ``identify``    - the same clip against the WHOLE language list, returning
  what was said AND which language it was said in. This is what lets a farmer
  start a conversation in English and finish it in Punjabi.
* ``issue_token`` - a 10-minute token so the browser Speech SDK can stream
  recognition directly without ever seeing the subscription key.

If the key is absent every function returns a SpeechResult with ok=False and a
plain reason; the UI disables the microphone and says why rather than failing
silently.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

import httpx

from ..core import languages
from ..core.config import STORAGE_DIR, ensure_dirs, get_settings
# Speech is not a model call, but it is the same problem: a short request to a
# far-away host, on the critical path of a live call, several times a turn.
from ..llm import pool

log = logging.getLogger("saarthi.speech")

AUDIO_DIR = STORAGE_DIR / "audio"
TOKEN_TTL_SECONDS = 540                     # Azure tokens last 10 minutes
# Output formats: mp3 for browsers, 8 kHz mono wav for telephony.
FORMAT_BROWSER = "audio-24khz-48kbitrate-mono-mp3"
FORMAT_TELEPHONY = "riff-8khz-16bit-mono-pcm"

# The Fast Transcription API, which is the only one of Azure's speech endpoints
# that takes a list of candidate languages and tells you which one it heard.
FAST_API_VERSION = "2024-11-15"

# Below this, the identification is a guess and the farmer keeps the language
# they were already in. Measured on the thirteen voices: a full sentence scores
# .69 to .93, and the one reading that came back wrong was a four-word clip at
# .71. Switching somebody into Malayalam because they coughed is worse than not
# switching at all.
MIN_IDENTIFY_CONFIDENCE = 0.55

_token_cache: Dict[str, Tuple[float, str]] = {}


@dataclass
class SpeechResult:
    ok: bool
    reason: str = ""
    text: str = ""
    audio_path: Optional[Path] = None
    audio_url: str = ""
    locale: str = ""
    voice: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)


def configured() -> bool:
    settings = get_settings()
    return settings.has("AZURE_SPEECH_KEY") and settings.has("AZURE_SPEECH_REGION")


def status() -> Dict[str, Any]:
    settings = get_settings()
    return {
        "configured": configured(),
        "region": settings.azure_speech_region if configured() else "",
        "languages": languages.as_dicts(),
        "note": ("" if configured() else
                 "Add AZURE_SPEECH_KEY and AZURE_SPEECH_REGION to config/api_keys.env to "
                 "enable the microphone and spoken replies."),
    }


def _endpoint(kind: str) -> str:
    region = get_settings().azure_speech_region
    if kind == "fast":
        return ("https://%s.api.cognitive.microsoft.com/speechtotext/transcriptions"
                ":transcribe?api-version=%s" % (region, FAST_API_VERSION))
    if kind == "tts":
        return "https://%s.tts.speech.microsoft.com/cognitiveservices/v1" % region
    if kind == "stt":
        return ("https://%s.stt.speech.microsoft.com/speech/recognition/conversation/"
                "cognitiveservices/v1" % region)
    return "https://%s.api.cognitive.microsoft.com/sts/v1.0/issueToken" % region


# ------------------------------------------------------------------- token
async def issue_token() -> SpeechResult:
    """Short-lived token for the browser Speech SDK; the key never leaves the server."""
    if not configured():
        return SpeechResult(False, "Azure Speech is not configured.")
    settings = get_settings()
    cached = _token_cache.get(settings.azure_speech_region)
    if cached and time.time() - cached[0] < TOKEN_TTL_SECONDS:
        return SpeechResult(True, text=cached[1], detail={"region": settings.azure_speech_region,
                                                          "cached": True})
    try:
        client = await pool.client(20.0)
        resp = await client.post(
                _endpoint("token"), content=b"",
                headers={"Ocp-Apim-Subscription-Key": settings.azure_speech_key,
                         "Content-Length": "0"})
    except Exception as exc:                                     # noqa: BLE001
        return SpeechResult(False, "Could not reach Azure Speech: %s" % str(exc)[:160])
    if resp.status_code != 200:
        return SpeechResult(False, "Azure Speech rejected the key (HTTP %s)." % resp.status_code)
    token = resp.text
    _token_cache[settings.azure_speech_region] = (time.time(), token)
    return SpeechResult(True, text=token, detail={"region": settings.azure_speech_region,
                                                  "expires_in": TOKEN_TTL_SECONDS})


# --------------------------------------------------------------------- TTS
def build_ssml(text: str, language: str, style: Optional[str] = None,
               rate: str = "0%") -> str:
    lang = languages.resolve(language)
    body = escape((text or "").strip())
    inner = "<prosody rate=\"%s\">%s</prosody>" % (rate, body)
    if style:
        inner = ("<mstts:express-as style=\"%s\">%s</mstts:express-as>" % (style, inner))
    return (
        "<speak version=\"1.0\" xmlns=\"http://www.w3.org/2001/10/synthesis\" "
        "xmlns:mstts=\"https://www.w3.org/2001/mstts\" xml:lang=\"%s\">"
        "<voice name=\"%s\">%s</voice></speak>" % (lang.locale, lang.voice, inner)
    )


async def synthesize(text: str, language: str = "en", *, telephony: bool = False,
                     rate: str = "0%") -> SpeechResult:
    """Render text to speech and cache the file by content hash."""
    lang = languages.resolve(language)
    if not (text or "").strip():
        return SpeechResult(False, "Nothing to speak.")
    if not configured():
        return SpeechResult(False, "Azure Speech is not configured, so spoken replies are "
                                   "unavailable. The text reply is still available.",
                            locale=lang.locale, voice=lang.voice)
    ensure_dirs()
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    fmt = FORMAT_TELEPHONY if telephony else FORMAT_BROWSER
    suffix = ".wav" if telephony else ".mp3"
    digest = hashlib.sha256(("%s|%s|%s|%s" % (text, lang.voice, fmt, rate)).encode("utf-8")).hexdigest()[:24]
    path = AUDIO_DIR / (digest + suffix)
    url = "/api/speech/audio/" + path.name
    if path.exists() and path.stat().st_size > 0:
        return SpeechResult(True, audio_path=path, audio_url=url, locale=lang.locale,
                            voice=lang.voice, detail={"cached": True})

    settings = get_settings()
    ssml = build_ssml(text, language, rate=rate)
    try:
        client = await pool.client(60.0)
        resp = await client.post(
                _endpoint("tts"), content=ssml.encode("utf-8"),
                headers={
                    "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": fmt,
                    "User-Agent": "Saarthi-2.0",
                })
    except Exception as exc:                                     # noqa: BLE001
        return SpeechResult(False, "Speech synthesis failed: %s" % str(exc)[:160])
    if resp.status_code != 200:
        return SpeechResult(
            False, "Azure TTS returned HTTP %s. Check the key, the region and whether the "
                   "voice %s is available there." % (resp.status_code, lang.voice))
    path.write_bytes(resp.content)
    return SpeechResult(True, audio_path=path, audio_url=url, locale=lang.locale,
                        voice=lang.voice, detail={"bytes": len(resp.content), "format": fmt})


# --------------------------------------------------------------------- STT
async def recognize(audio: bytes, language: str = "en",
                    content_type: str = "audio/webm; codecs=opus") -> SpeechResult:
    """Transcribe a short clip. The browser sends webm/opus; telephony sends wav."""
    lang = languages.resolve(language)
    if not configured():
        return SpeechResult(False, "Azure Speech is not configured, so the microphone is "
                                   "disabled. Type your question instead.", locale=lang.locale)
    if not audio:
        return SpeechResult(False, "No audio was received.", locale=lang.locale)
    settings = get_settings()
    params = {"language": lang.locale, "format": "detailed", "profanity": "raw"}
    try:
        client = await pool.client(60.0)
        resp = await client.post(
                _endpoint("stt"), params=params, content=audio,
                headers={
                    "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
                    "Content-Type": content_type,
                    "Accept": "application/json",
                })
    except Exception as exc:                                     # noqa: BLE001
        return SpeechResult(False, "Speech recognition failed: %s" % str(exc)[:160],
                            locale=lang.locale)
    if resp.status_code != 200:
        return SpeechResult(False, "Azure STT returned HTTP %s." % resp.status_code,
                            locale=lang.locale)
    try:
        data = resp.json()
    except ValueError:
        return SpeechResult(False, "Azure STT returned an unreadable response.",
                            locale=lang.locale)
    recognition = str(data.get("RecognitionStatus", ""))
    if recognition != "Success":
        reason = {
            "NoMatch": "I could not make out any speech. Please say that again.",
            "InitialSilenceTimeout": "I did not hear anything. Please try again.",
            "BabbleTimeout": "There was too much background noise. Please try again.",
        }.get(recognition, "Speech was not recognised (%s)." % recognition)
        return SpeechResult(False, reason, locale=lang.locale, detail=data)
    text = data.get("DisplayText") or ""
    confidence = None
    best = (data.get("NBest") or [{}])[0]
    if isinstance(best, dict):
        confidence = best.get("Confidence")
        text = text or best.get("Display") or best.get("Lexical") or ""
    return SpeechResult(True, text=text, locale=lang.locale,
                        detail={"confidence": confidence, "status": recognition})


# ------------------------------------------------- STT with the language unknown
async def identify(audio: bytes, *, filename: str = "clip.wav",
                   content_type: str = "audio/wav",
                   candidates: Optional[List[str]] = None,
                   timeout: float = 60.0) -> SpeechResult:
    """What was said, and which of our languages it was said in.

    The ordinary recognition endpoint is told one locale and will happily
    return Devanagari for a sentence spoken in Punjabi - which is why a farmer
    who switched language mid-call used to get an answer in the language they
    had stopped speaking. This endpoint takes the whole list and reports the
    locale it actually heard, with a confidence, in the SAME round trip. There
    is no second call and so no extra delay.

    ``locale`` on the result is the language identified; the caller decides
    whether the confidence is good enough to switch to it.
    """
    # Azure's fast-transcription endpoint accepts TEN candidate locales, and
    # there are fourteen languages. The caller picks which ten; sending the
    # whole list is a request Azure refuses outright.
    lang_codes = (candidates or [l.locale for l in languages.LANGUAGES])[:10]
    if not configured():
        return SpeechResult(False, "Azure Speech is not configured.")
    if not audio:
        return SpeechResult(False, "No audio was received.")

    settings = get_settings()
    definition = {"locales": lang_codes, "profanityFilterMode": "None"}
    try:
        client = await pool.client(60.0)
        resp = await client.post(
                _endpoint("fast"),
                headers={"Ocp-Apim-Subscription-Key": settings.azure_speech_key},
                files={"audio": (filename, audio, content_type)},
                data={"definition": json.dumps(definition)},
                # Per-request, because the pooled client is shared with calls
                # that are allowed to take a minute. A spoken turn is not.
                timeout=timeout)
    except Exception as exc:                                     # noqa: BLE001
        return SpeechResult(False, "Speech recognition failed: %s" % str(exc)[:160])
    if resp.status_code != 200:
        # The body says which of the two it is - throttled, or a request the
        # endpoint would never accept - and the caller falls back either way.
        log.info("azure fast transcription HTTP %s: %s", resp.status_code,
                 resp.text[:200])
        return SpeechResult(
            False, "Azure fast transcription returned HTTP %s." % resp.status_code,
            detail={"body": resp.text[:300], "status": resp.status_code})
    try:
        data = resp.json()
    except ValueError:
        return SpeechResult(False, "Azure returned an unreadable response.")

    phrases = [p for p in (data.get("phrases") or []) if isinstance(p, dict)]
    combined = " ".join(str(p.get("text") or "")
                        for p in (data.get("combinedPhrases") or [])
                        if isinstance(p, dict)).strip()
    text = combined or " ".join(str(p.get("text") or "") for p in phrases).strip()
    if not text:
        return SpeechResult(False, "I could not make out any speech. Please say that again.",
                            detail=data)

    # The longest phrase decides. A clip is usually one phrase; where it is not,
    # a two-word aside should not set the language for the whole answer.
    best = max(phrases, key=lambda p: float(p.get("durationMilliseconds") or 0),
               default={})
    locale = str(best.get("locale") or "")
    confidence = float(best.get("confidence") or 0.0)
    lang = languages.BY_LOCALE.get(locale)
    return SpeechResult(
        True, text=text, locale=lang.locale if lang else locale,
        detail={"confidence": confidence,
                "language": lang.code if lang else "",
                "sure": bool(lang) and confidence >= MIN_IDENTIFY_CONFIDENCE,
                "candidates": lang_codes,
                "duration_ms": data.get("durationMilliseconds")})


def audio_file(name: str) -> Optional[Path]:
    """Resolve a cached audio file by name, refusing anything outside the folder."""
    candidate = (AUDIO_DIR / Path(name).name).resolve()
    try:
        candidate.relative_to(AUDIO_DIR.resolve())
    except ValueError:
        return None
    return candidate if candidate.exists() else None
