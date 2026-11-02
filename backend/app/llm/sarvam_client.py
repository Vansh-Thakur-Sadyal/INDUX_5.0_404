"""Sarvam AI client: conversational LLM, speech-to-text and text-to-speech.

Sarvam is the voice stack for the phone channel. Verified against this account on
2026-10-14:

    chat   POST /v1/chat/completions   sarvam-105b-conversations (dialogue, 32K)
                                       sarvam-105b              (reasoning, 128K)
    TTS    POST /text-to-speech        bulbul:v3, 37 Indian voices, base64 WAV
    STT    POST /speech-to-text        saarika:v2.5 (v2 and flash are deprecated)

Auth is the `api-subscription-key` header (a Bearer token also works).

Design rules are the same as everywhere else in this app: never fabricate, degrade
visibly, and let the test-suite swap the transport so nothing hits the network.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import httpx

from ..core import languages
from ..core.config import get_settings

log = logging.getLogger("saarthi.sarvam")

BASE = "https://api.sarvam.ai"
CHAT_URL = BASE + "/v1/chat/completions"
TTS_URL = BASE + "/text-to-speech"
# The same voices and the same model, but it returns MP3 as it encodes instead of
# assembling a base64 WAV first. Measured from the VPS on a fourteen-word Hindi reply:
# /text-to-speech 2289-2505 ms, /text-to-speech/stream 1037-1068 ms. That difference is
# what puts a spoken turn inside two seconds. Twilio plays audio/mpeg natively, and the
# MP3 is a quarter of the size, so Twilio also fetches it faster.
TTS_STREAM_URL = BASE + "/text-to-speech/stream"
STT_URL = BASE + "/speech-to-text"

CONVERSATION_MODEL = "sarvam-105b-conversations"
REASONING_MODEL = "sarvam-105b"
TTS_MODEL = "bulbul:v3"
STT_MODEL = "saarika:v2.5"

# bulbul:v3 voices, as the API itself lists them. A female and a male option per
# language keeps the helpline sounding consistent rather than random.
VOICES = {
    "default_female": "priya",
    "default_male": "amit",
    "available": ["aditya", "ritu", "ashutosh", "priya", "neha", "rahul", "pooja", "rohan",
                  "simran", "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun",
                  "manan", "sumit", "roopa", "kabir", "aayan", "shubh", "advait", "anand",
                  "tanya", "tarun", "sunny", "mani", "gokul", "vijay", "shruti", "suhani",
                  "mohit", "kavitha", "rehan", "soham", "rupali"],
}

# Sarvam speech takes BCP-47 codes; the app's 13 languages map straight across.
SPEECH_LOCALES = {
    "en": "en-IN", "hi": "hi-IN", "bn": "bn-IN", "ta": "ta-IN", "te": "te-IN",
    "mr": "mr-IN", "gu": "gu-IN", "kn": "kn-IN", "ml": "ml-IN", "pa": "pa-IN",
    "or": "od-IN", "as": "as-IN", "ur": "ur-IN",
}

Transport = Callable[[str, Dict[str, Any]], Any]
_transport: Optional[Transport] = None


def set_transport(transport: Optional[Transport]) -> None:
    """Install (or clear) a fake transport. Tests only."""
    global _transport
    _transport = transport


@dataclass
class SarvamReply:
    ok: bool
    text: str = ""
    reason: str = ""
    model: str = ""
    latency_ms: int = 0
    usage: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SpeechOut:
    ok: bool
    audio: bytes = b""
    reason: str = ""
    voice: str = ""
    locale: str = ""
    fmt: str = "wav"          # "mp3" from the streaming endpoint, "wav" from the batch one
    endpoint: str = ""        # which one answered, so latency can be attributed


@dataclass
class SpeechIn:
    ok: bool
    text: str = ""
    reason: str = ""
    locale: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)


def locale_for(code: str) -> str:
    return SPEECH_LOCALES.get(languages.resolve(code).code, "hi-IN")


# ── out of credit ─────────────────────────────────────────────────────────
# A key that is valid but has no balance answers HTTP 402 to everything, and
# answers it fast. Asking anyway costs a third of a second of a live phone
# turn, twice - once for the words, once for the voice - so the answer is
# remembered and the app routes around Sarvam until the cooldown is up.
#
# Fifteen minutes: long enough that a call never pays for it twice, short
# enough that topping the account up is noticed without a restart.
NO_CREDIT_COOLDOWN = 900.0
_no_credit: Dict[str, Any] = {"until": 0.0, "reason": ""}

# What a provider says when the bill is the problem, rather than the request.
_BROKE = ("insufficient_quota", "no credits available", "quota exceeded",
          "payment required")


def _note_refusal(status_code: int, body: str) -> None:
    """Remember a refusal that no retry can fix."""
    text = (body or "").lower()
    if status_code != 402 and not any(w in text for w in _BROKE):
        return
    _no_credit["until"] = time.time() + NO_CREDIT_COOLDOWN
    _no_credit["reason"] = ("Sarvam returned HTTP %s: the account has no credit "
                            "left." % status_code)
    log.warning("Sarvam is out of credit; routing elsewhere for %d minutes",
                int(NO_CREDIT_COOLDOWN // 60))


def out_of_credit() -> str:
    """The reason Sarvam is being skipped, or "" when it is not."""
    if time.time() < float(_no_credit["until"] or 0.0):
        return str(_no_credit["reason"])
    return ""


def forget_refusal() -> None:
    """Try Sarvam again now. For tests, and for the admin panel."""
    _no_credit["until"] = 0.0
    _no_credit["reason"] = ""


class SarvamClient:
    def __init__(self, timeout: float = 45.0, max_retries: int = 3) -> None:
        self.timeout = timeout
        self.max_retries = max_retries

    @property
    def settings(self):
        return get_settings()

    def configured(self) -> bool:
        return self.settings.has("SARVAM_API_KEY") or _transport is not None

    def available(self) -> bool:
        """Configured, and not known to be out of credit."""
        return self.configured() and not out_of_credit()

    @property
    def _headers(self) -> Dict[str, str]:
        return {"api-subscription-key": self.settings.get("SARVAM_API_KEY"),
                "Content-Type": "application/json"}

    def status(self) -> Dict[str, Any]:
        blocked = out_of_credit()
        return {
            "configured": self.configured(),
            "available": self.available(),
            # Said plainly, because "configured: true" on a provider that
            # refuses every request is how this went unnoticed.
            "note": blocked,
            "chat_model": self.settings.get("SARVAM_MODEL", CONVERSATION_MODEL),
            "fallback_model": self.settings.get("SARVAM_FALLBACK_MODEL", REASONING_MODEL),
            "tts_model": TTS_MODEL,
            "stt_model": STT_MODEL,
            "voice": self.settings.get("SARVAM_VOICE", VOICES["default_female"]),
            "languages": list(SPEECH_LOCALES),
        }

    # ------------------------------------------------------------- plumbing
    async def _post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if _transport is not None:
            result = _transport(url, payload)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        delay, last_error = 1.0, ""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(1, self.max_retries + 1):
                try:
                    response = await client.post(url, json=payload, headers=self._headers)
                except httpx.HTTPError as exc:
                    last_error = "network error: " + str(exc)[:140]
                else:
                    if response.status_code == 200:
                        return response.json()
                    if response.status_code in (401, 403):
                        raise PermissionError(
                            "Sarvam rejected the API key (HTTP %s). Check SARVAM_API_KEY."
                            % response.status_code)
                    last_error = "HTTP %s: %s" % (response.status_code, response.text[:160])
                    _note_refusal(response.status_code, response.text)
                    if response.status_code not in (408, 429, 500, 502, 503, 504):
                        break
                if attempt < self.max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2
        raise RuntimeError(last_error or "Sarvam request failed")

    async def _post_audio(self, url: str, payload: Dict[str, Any]) -> bytes:
        """POST for an endpoint that answers with audio bytes rather than JSON.

        Single attempt on purpose: this sits on the critical path of a live phone turn,
        and a retry would cost more than the whole budget. The caller falls back to the
        batch endpoint instead.
        """
        if _transport is not None:
            result = _transport(url, payload)
            if asyncio.iscoroutine(result):
                result = await result
            if isinstance(result, (bytes, bytearray)):
                return bytes(result)
            raise RuntimeError("fake transport returned no audio for %s" % url)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload, headers=self._headers)
        if response.status_code == 200:
            return response.content
        if response.status_code in (401, 403):
            raise PermissionError("Sarvam rejected the API key (HTTP %s). Check "
                                  "SARVAM_API_KEY." % response.status_code)
        _note_refusal(response.status_code, response.text)
        raise RuntimeError("HTTP %s: %s" % (response.status_code, response.text[:160]))

    # ----------------------------------------------------------------- chat
    async def chat(self, messages: Sequence[Dict[str, str]], *, temperature: float = 0.6,
                   max_tokens: int = 320, model: Optional[str] = None) -> SarvamReply:
        if not self.configured():
            return SarvamReply(False, reason="SARVAM_API_KEY is not configured.")
        chosen = model or self.settings.get("SARVAM_MODEL", CONVERSATION_MODEL)
        payload = {"model": chosen, "messages": list(messages),
                   "temperature": temperature, "max_tokens": max_tokens}
        started = time.perf_counter()
        try:
            data = await self._post_json(CHAT_URL, payload)
        except Exception as exc:                                  # noqa: BLE001
            log.warning("Sarvam chat failed: %s", exc)
            return SarvamReply(False, reason=str(exc)[:200])
        try:
            message = data["choices"][0]["message"]
            text = (message.get("content") or "").strip()
        except (KeyError, IndexError, TypeError):
            return SarvamReply(False, reason="Unexpected response shape from Sarvam.")
        if not text:
            # sarvam-105b is a reasoning model: with a small max_tokens it can spend the
            # whole budget on reasoning_content and return content=None.
            return SarvamReply(False, reason="Sarvam returned no spoken text (the "
                                             "reasoning model needs a larger max_tokens).")
        return SarvamReply(True, text=text, model=str(data.get("model", chosen)),
                           usage=data.get("usage", {}) or {},
                           latency_ms=int((time.perf_counter() - started) * 1000))

    # ------------------------------------------------------------------ TTS
    def voice_for(self, language: str = "hi") -> str:
        """The speaker this account will use - resolved without a network call.

        voice_io needs to know the voice before it synthesises, so it can look for a
        cached clip of the same words first.
        """
        return str(self.settings.get("SARVAM_VOICE", VOICES["default_female"]))

    async def speak(self, text: str, language: str = "hi", *,
                    voice: Optional[str] = None, telephony: bool = True,
                    fast: bool = True) -> SpeechOut:
        """Speak ``text``. ``fast`` prefers the streaming endpoint (MP3, ~2x quicker).

        The batch endpoint stays as the fallback, so an outage or a change on Sarvam's
        side costs latency rather than the call.
        """
        if not (text or "").strip():
            return SpeechOut(False, reason="Nothing to speak.")
        if not self.configured():
            return SpeechOut(False, reason="SARVAM_API_KEY is not configured.")
        locale = locale_for(language)
        chosen_voice = voice or self.settings.get("SARVAM_VOICE", VOICES["default_female"])
        payload = {
            "text": text[:2500], "target_language_code": locale,
            "model": TTS_MODEL, "speaker": chosen_voice,
            # 8 kHz is what a phone line carries anyway, and it keeps the file small
            # enough for Twilio to fetch quickly.
            "speech_sample_rate": 8000 if telephony else 22050,
        }

        note = ""
        if fast:
            try:
                audio = await self._post_audio(TTS_STREAM_URL, payload)
            except PermissionError:
                raise
            except Exception as exc:                              # noqa: BLE001
                note = "streaming TTS unavailable (%s); used the batch endpoint. " % (
                    str(exc)[:120])
                log.warning("Sarvam streaming TTS failed, falling back: %s", exc)
            else:
                if audio[:3] == b"ID3" or audio[:1] == b"\xff":
                    return SpeechOut(True, audio=audio, voice=chosen_voice, locale=locale,
                                     fmt="mp3", endpoint="stream")
                # Not MP3: rather than hand Twilio something it cannot play, say so and
                # let the batch endpoint answer.
                note = "streaming TTS returned %r, not MP3; used the batch endpoint. " % (
                    audio[:4],)
                log.warning("Sarvam streaming TTS returned unexpected bytes: %r", audio[:8])

        try:
            data = await self._post_json(TTS_URL, payload)
        except Exception as exc:                                  # noqa: BLE001
            log.warning("Sarvam TTS failed: %s", exc)
            return SpeechOut(False, reason=note + str(exc)[:200], voice=chosen_voice,
                             locale=locale)
        audios = data.get("audios") or []
        if not audios:
            return SpeechOut(False, reason=note + "Sarvam returned no audio.",
                             voice=chosen_voice, locale=locale)
        try:
            audio = base64.b64decode(audios[0])
        except Exception:                                         # noqa: BLE001
            return SpeechOut(False, reason=note + "Sarvam audio was not valid base64.",
                             voice=chosen_voice, locale=locale)
        return SpeechOut(True, audio=audio, voice=chosen_voice, locale=locale,
                         fmt="wav", endpoint="batch")

    # ------------------------------------------------------------------ STT
    async def transcribe(self, audio: bytes, language: str = "hi",
                         filename: str = "clip.wav",
                         content_type: str = "audio/wav") -> SpeechIn:
        if not audio:
            return SpeechIn(False, reason="No audio was received.")
        if not self.configured():
            return SpeechIn(False, reason="SARVAM_API_KEY is not configured.")
        locale = locale_for(language)
        if _transport is not None:
            data = _transport(STT_URL, {"language_code": locale, "bytes": len(audio)})
            if asyncio.iscoroutine(data):
                data = await data
            return SpeechIn(True, text=str(data.get("transcript", "")), locale=locale,
                            detail=data)
        files = {"file": (filename, audio, content_type)}
        form = {"model": STT_MODEL, "language_code": locale}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    STT_URL, data=form, files=files,
                    headers={"api-subscription-key": self.settings.get("SARVAM_API_KEY")})
        except Exception as exc:                                  # noqa: BLE001
            return SpeechIn(False, reason="Speech recognition failed: %s" % str(exc)[:140],
                            locale=locale)
        if response.status_code != 200:
            return SpeechIn(False, locale=locale,
                            reason="Sarvam STT returned HTTP %s: %s"
                                   % (response.status_code, response.text[:140]))
        try:
            data = response.json()
        except ValueError:
            return SpeechIn(False, reason="Sarvam STT returned an unreadable response.",
                            locale=locale)
        transcript = (data.get("transcript") or "").strip()
        if not transcript:
            return SpeechIn(False, locale=locale, detail=data,
                            reason="I could not make out any speech. Please say that again.")
        return SpeechIn(True, text=transcript, locale=locale, detail=data)


client = SarvamClient()
