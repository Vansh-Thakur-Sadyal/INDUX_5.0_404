"""Identifying the caller's language from the audio, on a live call.

The phone line's problem is not in our code: a telephone recogniser is handed
ONE locale when the call connects and transcribes the whole call through it. A
caller who switches to Punjabi is still written down by a Hindi engine, so the
transcript is Devanagari and the reply comes back in Hindi.

When the telephony layer can hand over the caller's audio, Azure's own
language identification decides instead, and everything here is about the
rules around that decision - most of all what happens when it is NOT sure,
because a system that guesses a language is worse than one that keeps the last
one it was certain about.
"""
from __future__ import annotations

import base64

import pytest

from app.speech import lid

TOKEN = {"X-Saarthi-Token": "test-secret"}
PHONE = "+91 90000 33333"


class _Found:
    """What `azure_speech.identify` returns, as this module reads it."""

    def __init__(self, *, ok=True, text="", locale="", language="",
                 confidence=0.0, duration=3000, reason=""):
        self.ok = ok
        self.text = text
        self.locale = locale
        self.reason = reason
        self.detail = {"language": language, "confidence": confidence,
                       "duration_ms": duration, "candidates": []}


@pytest.fixture
def azure(monkeypatch):
    """A scripted Azure. Returns a setter for what it should report."""
    from app.speech import azure_speech

    box = {"result": _Found(ok=False, reason="not configured")}

    async def identify(audio, **kwargs):
        box["seen"] = {"bytes": len(audio or b""), **kwargs}
        return box["result"]

    monkeypatch.setattr(azure_speech, "identify", identify)
    monkeypatch.setattr(azure_speech, "configured", lambda: True)
    return box


# --------------------------------------------------------------- the decision
@pytest.mark.asyncio
async def test_a_confident_identification_sets_the_language(azure):
    azure["result"] = _Found(text="ਕਣਕ ਦਾ ਭਾਅ ਦੱਸੋ", locale="pa-IN",
                             language="pa", confidence=0.84)
    decision = await lid.from_audio(b"x" * 900, current="hi")

    assert decision.language == "pa"
    assert decision.source == "azure-lid"
    assert decision.certain is True
    assert decision.detected == "pa"
    assert decision.transcript.startswith("ਕਣਕ")


@pytest.mark.asyncio
async def test_an_uncertain_identification_keeps_the_language(azure):
    """Rule six, and the one that matters most: never guess, and never say a
    language was detected when it was not."""
    azure["result"] = _Found(text="something", language="ta", confidence=0.31)
    decision = await lid.from_audio(b"x" * 900, current="hi")

    assert decision.language == "hi", "it guessed"
    assert decision.source == "kept"
    assert decision.certain is False
    assert decision.detected == "", "it claimed a detection it did not have"
    assert "confidence" in decision.note


@pytest.mark.asyncio
async def test_a_short_noise_never_changes_the_language(azure):
    """"Haan", a cough, one word. A caller agreeing is not a caller switching."""
    azure["result"] = _Found(text="haan", language="en", confidence=0.95,
                             duration=600)
    decision = await lid.from_audio(b"x" * 400, current="pa")

    assert decision.language == "pa"
    assert decision.source == "kept"
    assert decision.detected == ""


@pytest.mark.asyncio
async def test_azure_being_unreachable_keeps_the_language(azure):
    azure["result"] = _Found(ok=False, reason="Azure returned HTTP 503.")
    decision = await lid.from_audio(b"x" * 900, current="bn")

    assert decision.language == "bn"
    assert decision.source == "kept"
    assert "503" in decision.note


@pytest.mark.asyncio
async def test_with_nothing_established_it_uses_the_default(azure):
    azure["result"] = _Found(ok=False, reason="no")
    decision = await lid.from_audio(b"x" * 900, current="")
    assert decision.source == "default"


@pytest.mark.asyncio
async def test_no_audio_is_not_an_identification(azure):
    decision = await lid.from_audio(b"", current="ta")
    assert decision.language == "ta"
    assert decision.source == "kept"
    assert decision.detected == ""


@pytest.mark.asyncio
async def test_an_enormous_file_is_refused_rather_than_uploaded(azure):
    decision = await lid.from_audio(b"x" * (lid.MAX_AUDIO_BYTES + 1), current="hi")
    assert decision.source == "kept"
    assert "seen" not in azure, "it sent a huge file to Azure anyway"


@pytest.mark.asyncio
async def test_the_language_in_force_is_always_a_candidate(azure):
    """Azure's fast-transcription endpoint takes TEN candidate locales and
    there are fourteen languages, so ten have to be chosen. The one rule that
    cannot be broken: the language the conversation is already being held in
    must be among them, or a farmer speaking Odia to an Odia conversation is
    identified as something else and switched out of their own language."""
    from app.core import languages

    for code in [l.code for l in languages.LANGUAGES]:
        offered = lid.candidates_for(code)
        assert len(offered) <= 10, "Azure refuses more than ten"
        assert languages.BY_CODE[code].locale in offered, (
            "%s was dropped from its own candidate list" % code)

    azure["result"] = _Found(text="x", language="hi", confidence=0.9)
    await lid.from_audio(b"x" * 900, current="hi")
    assert azure["seen"]["candidates"], "the shortlist must be passed through"
    assert "hi-IN" in azure["seen"]["candidates"]


# ------------------------------------------------------------- what is sent
def test_inline_audio_is_decoded():
    raw = b"RIFF....WAVEfmt "
    assert lid.decode(base64.b64encode(raw).decode()) == raw


def test_a_data_url_is_decoded_too():
    raw = b"RIFF....WAVEfmt "
    encoded = "data:audio/wav;base64," + base64.b64encode(raw).decode()
    assert lid.decode(encoded) == raw


def test_rubbish_is_not_audio():
    assert lid.decode("") is None
    assert lid.decode("   ") is None


@pytest.mark.asyncio
async def test_only_http_urls_are_fetched():
    """A file: or data: URL here would be this server reading its own disk on
    somebody else's say-so."""
    assert await lid.fetch("file:///etc/passwd") is None
    assert await lid.fetch("") is None


# ------------------------------------------------------------- the whole turn
@pytest.mark.asyncio
async def test_the_turn_reports_the_language_and_how_it_was_decided(
        client, azure, fake_llm, monkeypatch):
    """n8n reads these back: the locale to listen in next, and the voice to
    read the reply with."""
    from app.speech import voice_io

    class _Spoken:
        ok, url, provider, voice, reason, path = (
            True, "/api/speech/audio/x.wav", "azure", "pa", "", None)

    async def fake_tts(*args, **kwargs):
        return _Spoken()

    monkeypatch.setattr(voice_io, "synthesize", fake_tts)
    azure["result"] = _Found(text="ਮੈਨੂੰ ਕਣਕ ਦਾ ਭਾਅ ਦੱਸੋ ਜੀ", locale="pa-IN",
                             language="pa", confidence=0.86)

    body = client.post("/api/voice/turn", json={
        "phone": PHONE, "call_id": "LID-1", "language": "hi",
        "transcript": "मैनू कणक दा भा दस्सो जी",
        "audio_b64": base64.b64encode(b"x" * 2000).decode(),
    }, headers=TOKEN).json()

    assert body["language"] == "pa"
    assert body["language_source"] == "azure-lid"
    assert body["detected_language"] == "pa"
    assert body["language_confidence"] >= 0.8
    assert body["listen_locale"] == "pa-IN"
    assert body["voice"].startswith("pa-IN-")


@pytest.mark.asyncio
async def test_an_unsure_turn_says_so_rather_than_claiming_a_detection(
        client, azure, fake_llm, monkeypatch):
    from app.speech import voice_io

    class _Spoken:
        ok, url, provider, voice, reason, path = (True, "/a.wav", "azure", "hi", "", None)

    async def fake_tts(*args, **kwargs):
        return _Spoken()

    monkeypatch.setattr(voice_io, "synthesize", fake_tts)
    azure["result"] = _Found(text="hmm", language="ta", confidence=0.22)

    body = client.post("/api/voice/turn", json={
        "phone": PHONE, "call_id": "LID-2", "language": "hi",
        "transcript": "गेहूं का भाव क्या है",
        "audio_b64": base64.b64encode(b"x" * 2000).decode(),
    }, headers=TOKEN).json()

    assert body["language_source"] == "kept"
    assert body["detected_language"] == ""
    assert body["language"] == "hi"


@pytest.mark.asyncio
async def test_a_turn_with_no_audio_still_works(client, fake_llm, monkeypatch):
    """Which is every turn today: n8n sends a transcript. The text heuristics
    carry it, and nothing claims an identification."""
    from app.speech import voice_io

    class _Spoken:
        ok, url, provider, voice, reason, path = (True, "/a.wav", "azure", "hi", "", None)

    async def fake_tts(*args, **kwargs):
        return _Spoken()

    monkeypatch.setattr(voice_io, "synthesize", fake_tts)
    body = client.post("/api/voice/turn", json={
        "phone": PHONE, "call_id": "LID-3", "language": "hi",
        "transcript": "ਮੈਨੂੰ ਮੌਸਮ ਬਾਰੇ ਦੱਸੋ ਜੀ",
    }, headers=TOKEN).json()

    assert "language_source" not in body, "it reported an identification it never made"
    assert body["language"] == "pa", "the script should still have been read"
    assert body["listen_locale"] == "pa-IN"


@pytest.mark.asyncio
async def test_the_opening_greeting_is_not_touched(client, fake_llm, monkeypatch):
    """Rule one. The call opens exactly as configured; identification starts
    with the caller's first real answer, not before it."""
    from app.speech import voice_io

    class _Spoken:
        ok, url, provider, voice, reason, path = (True, "/a.wav", "azure", "hi", "", None)

    async def fake_tts(*args, **kwargs):
        return _Spoken()

    monkeypatch.setattr(voice_io, "synthesize", fake_tts)
    opening = client.post("/api/voice/turn", json={
        "phone": PHONE, "call_id": "LID-4", "language": "hi", "transcript": "",
    }, headers=TOKEN).json()

    assert opening["greeted"] is True
    assert "सारथी" in opening["reply"] or "Saarthi" in opening["reply"]
