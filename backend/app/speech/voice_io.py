"""One speech interface, two providers.

Sarvam (bulbul for TTS, saarika for STT) is preferred for this app: it is built for
Indian languages and the phone channel already uses Sarvam for the conversation, so
one key covers the whole voice path. Azure Speech stays as the alternative for
whoever has that key instead.

Whichever answers, the caller gets the same shape back - and when neither is
configured it says so rather than failing silently.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ..core.config import STORAGE_DIR, ensure_dirs
from ..llm.sarvam_client import client as sarvam
from . import azure_speech

log = logging.getLogger("saarthi.voice_io")

AUDIO_DIR = STORAGE_DIR / "audio"


def _clip_name(text: str, language: str, voice: str, telephony: bool) -> str:
    """A stable file name for one piece of spoken text.

    Everything that changes the audio is in the digest, so a hit is always the right
    clip, and a voice or sample-rate change simply misses instead of serving stale audio.
    """
    key = "sarvam|%s|%s|%s|%s" % (text.strip(), language, voice, telephony)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


@dataclass
class Spoken:
    ok: bool
    url: str = ""
    path: Optional[Path] = None
    provider: str = ""
    voice: str = ""
    reason: str = ""
    cached: bool = False
    fmt: str = ""


@dataclass
class Heard:
    ok: bool
    text: str = ""
    provider: str = ""
    reason: str = ""
    # Which of the thirteen it was spoken in, when that was worked out rather
    # than assumed, and whether the identification was confident enough to act
    # on. ``language`` empty means "nobody identified it; keep the one you had".
    language: str = ""
    confidence: float = 0.0
    sure: bool = False


def provider_name() -> str:
    # available(), not configured(): a key with no credit answers every request
    # with HTTP 402, and asking it first costs a quarter of a second of every
    # spoken turn before Azure is asked anyway.
    if sarvam.available():
        return "sarvam"
    if azure_speech.configured():
        return "azure"
    if sarvam.configured():
        return "sarvam"          # out of credit, and nothing else is set up
    return "none"


def status() -> Dict[str, Any]:
    name = provider_name()
    from ..llm.sarvam_client import out_of_credit
    blocked = out_of_credit()
    return {
        "provider": name,
        "available": name != "none",
        "sarvam_configured": sarvam.configured(),
        "sarvam_available": sarvam.available(),
        "azure_configured": azure_speech.configured(),
        "note": (blocked + (" Azure is speaking instead."
                            if azure_speech.configured() else "")) if blocked else
                ("" if name != "none" else
                 "No speech provider configured. Add SARVAM_API_KEY (preferred for Indian "
                 "languages) or AZURE_SPEECH_KEY to config/api_keys.env."),
    }


async def synthesize(text: str, language: str = "hi", *, telephony: bool = True) -> Spoken:
    """Text to a cached audio file, returning the URL the app serves it from."""
    if not (text or "").strip():
        return Spoken(False, reason="Nothing to speak.")
    name = provider_name()
    if name == "none":
        return Spoken(False, reason=status()["note"])

    if name == "sarvam" and sarvam.available():
        voice = sarvam.voice_for(language)
        ensure_dirs()
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        stem = _clip_name(text, language, voice, telephony)
        # Look in the cache BEFORE spending a second on the network. The digest used to
        # be computed from the API's reply, so identical words were synthesised again on
        # every call - and synthesis is the single slowest leg of a spoken turn.
        # Both extensions are checked because the format depends on which endpoint
        # answered, and a clip written as WAV yesterday is still the right audio today.
        for suffix in (".mp3", ".wav"):
            cached = AUDIO_DIR / (stem + suffix)
            if cached.exists() and cached.stat().st_size > 0:
                return Spoken(True, url="/api/speech/audio/" + cached.name, path=cached,
                              provider="sarvam", voice=voice, cached=True,
                              fmt=suffix.lstrip("."))
        result = await sarvam.speak(text, language, voice=voice, telephony=telephony)
        if not result.ok:
            # A dead TTS must not take the call down; try Azure if it is configured.
            if azure_speech.configured():
                return await _azure_speak(text, language, telephony, note=result.reason)
            return Spoken(False, reason=result.reason, provider="sarvam")
        path = AUDIO_DIR / (stem + "." + (result.fmt or "wav"))
        path.write_bytes(result.audio)
        return Spoken(True, url="/api/speech/audio/" + path.name, path=path,
                      provider="sarvam", voice=result.voice, fmt=result.fmt or "wav")

    return await _azure_speak(text, language, telephony)


async def _azure_speak(text: str, language: str, telephony: bool,
                       note: str = "") -> Spoken:
    result = await azure_speech.synthesize(text, language, telephony=telephony)
    if not result.ok:
        return Spoken(False, provider="azure",
                      reason=(note + " " if note else "") + result.reason)
    return Spoken(True, url=result.audio_url, path=result.audio_path, provider="azure",
                  voice=result.voice)


async def transcribe(audio: bytes, language: str = "hi", *, filename: str = "clip.wav",
                     content_type: str = "audio/wav", detect: bool = False) -> Heard:
    """What was said. With ``detect``, also which language it was said in.

    Detection goes to Azure whatever the configured provider is, because it is
    the only one of the two that will take a list of candidate languages and
    report back which it heard. It costs no extra round trip - the same call
    returns the words and the locale - so there is no reason to only do it
    sometimes.
    """
    name = provider_name()
    if detect and azure_speech.configured():
        found = await azure_speech.identify(audio, filename=filename,
                                            content_type=content_type)
        if found.ok:
            return Heard(True, text=found.text, provider="azure",
                         language=str(found.detail.get("language") or ""),
                         confidence=float(found.detail.get("confidence") or 0.0),
                         sure=bool(found.detail.get("sure")))
        # Identification failed; fall through to the ordinary path rather than
        # telling the farmer their microphone is broken.
        log.info("language identification unavailable: %s", found.reason[:160])

    if name == "none":
        return Heard(False, reason=status()["note"])
    if name == "sarvam" and sarvam.available():
        result = await sarvam.transcribe(audio, language, filename=filename,
                                         content_type=content_type)
        if result.ok:
            return Heard(True, text=result.text, provider="sarvam")
        if not azure_speech.configured():
            return Heard(False, reason=result.reason, provider="sarvam")
    azure_result = await azure_speech.recognize(audio, language, content_type)
    return Heard(azure_result.ok, text=azure_result.text, provider="azure",
                 reason=azure_result.reason)
