"""Five faults a farmer reported from a live call, each held shut by a test.

What they said, in their words:

    "Conversational ai miss in calling"
    "Too much delay in response ... reduce it to less than 2 second"
    "not shifting to correct language at once (i speak in punjabi it gives
     reply in old selected language)"
    "Language tone change (it is switching from female voice to male voice)"
    "Han ji mai sun rha hun (it keeps repeating this even if i speak)"

Four of the five were one-line faults in this repository. The fifth - the
conversational model itself - was the Sarvam account running out of credit,
which no code change can refill; what IS tested here is that the app stops
paying for the refusal on every turn and says out loud what happened.
"""
from __future__ import annotations

import base64

import pytest

from app.core import languages
from app.llm import router
from app.llm import sarvam_client
from app.routers import voice as voice_router
from app.speech import lid

TOKEN = {"X-Saarthi-Token": "test-secret"}
PHONE = "+91 90000 44444"


# ── "it is switching from female voice to male voice" ─────────────────────
# Azure's voice names do not carry a gender, so this is the published list for
# the thirteen locales in the registry. OjasNeural is the one that was in it.
MALE_VOICES = {
    "en-IN-PrabhatNeural", "hi-IN-MadhurNeural", "bn-IN-BashkarNeural",
    "ta-IN-ValluvarNeural", "te-IN-MohanNeural", "mr-IN-ManoharNeural",
    "gu-IN-NiranjanNeural", "kn-IN-GaganNeural", "ml-IN-MidhunNeural",
    "pa-IN-OjasNeural", "or-IN-SukantNeural", "as-IN-PriyomNeural",
    "ur-IN-SalmanNeural",
}


def test_one_voice_throughout():
    """A caller does not change language to change who is speaking to them."""
    male = [l.code for l in languages.LANGUAGES if l.voice in MALE_VOICES]
    assert not male, (
        "these languages answer in a male voice while the rest answer in a "
        "female one, so Saarthi changes sex mid-call: %s" % male)


def test_punjabi_in_particular():
    """The one that was actually reported."""
    assert languages.resolve("pa").voice == "pa-IN-VaaniNeural"


# ── "it keeps repeating 'han ji mai sun rha hun' even if i speak" ──────────
class _Found:
    def __init__(self, *, ok=True, text="", locale="", language="",
                 confidence=0.0, duration=3000, reason=""):
        self.ok, self.text, self.locale, self.reason = ok, text, locale, reason
        self.detail = {"language": language, "confidence": confidence,
                       "duration_ms": duration, "candidates": []}


@pytest.fixture
def azure(monkeypatch):
    from app.speech import azure_speech
    box = {"result": _Found(ok=False, reason="not configured")}

    async def identify(audio, **kwargs):
        box["seen"] = {"bytes": len(audio or b""), **kwargs}
        return box["result"]

    monkeypatch.setattr(azure_speech, "identify", identify)
    monkeypatch.setattr(azure_speech, "configured", lambda: True)
    return box


@pytest.fixture
def speaks(monkeypatch):
    """TTS that always succeeds, so a turn is about words and not about audio."""
    from app.speech import voice_io

    class _Spoken:
        ok, url, provider, voice, reason, path = (
            True, "/api/speech/audio/x.wav", "azure", "hi", "", None)

    async def fake(*args, **kwargs):
        return _Spoken()

    monkeypatch.setattr(voice_io, "synthesize", fake)


@pytest.mark.asyncio
async def test_words_survive_an_unsure_language(azure):
    """The caller spoke. That is not in doubt - only which of thirteen it was.

    Dropping the transcript along with the language is what sent a turn where
    somebody had just said a whole sentence into the silence branch.
    """
    azure["result"] = _Found(text="mujhe gehu ka bhav batao", language="hi",
                             confidence=0.21)
    decision = await lid.from_audio(b"x" * 2000, current="hi")

    assert decision.transcript == "mujhe gehu ka bhav batao"
    assert decision.source == "kept"
    assert decision.detected == "", "it claimed a detection it did not have"


@pytest.mark.asyncio
async def test_a_turn_with_words_is_never_a_silent_turn(client, azure, speaks,
                                                        fake_llm):
    azure["result"] = _Found(text="mujhe gehu ka bhav batao", language="hi",
                             confidence=0.21)
    body = client.post("/api/voice/turn", json={
        "phone": PHONE, "call_id": "Q-1", "language": "hi", "transcript": "",
        "audio_b64": base64.b64encode(b"x" * 2000).decode(),
    }, headers=TOKEN).json()

    assert body.get("greeted") is not True
    for code in ("en", "hi", "pa"):
        assert voice_router.NUDGE[code] not in body["reply"], (
            "a caller who spoke was told Saarthi was listening")


def test_the_nudge_speaks_every_language():
    """It existed in two. Everyone else heard the Hindi one."""
    missing = [l.code for l in languages.LANGUAGES
               if l.code not in voice_router.NUDGE]
    assert not missing, "no nudge in: %s" % missing

    # And each one is in its own script, not a copy of the Hindi.
    for lang in languages.LANGUAGES:
        if lang.code in ("en", "hi"):
            continue
        line = voice_router.nudge_for(lang.code)
        assert line != voice_router.NUDGE["hi"], "%s got the Hindi line" % lang.code
        found = languages.detect(line, lang.code)
        assert found == lang.code, (
            "the %s nudge reads as %s" % (lang.code, found or "nothing"))


def test_an_unknown_language_falls_back_to_english_not_hindi():
    assert voice_router.nudge_for("klingon") == voice_router.NUDGE["en"]


# ── "i speak in punjabi it gives reply in old selected language" ──────────
@pytest.mark.asyncio
async def test_the_script_switches_the_language_on_the_same_turn(azure):
    """Azure heard Punjabi but scored it 0.4. The words are in Gurmukhi.

    Waiting for a confident turn means the caller spends a whole exchange
    being answered in a language they have stopped speaking.
    """
    azure["result"] = _Found(text="ਮੈਨੂੰ ਕਣਕ "
                                  "ਦਾ ਭਾਅ ਦੱਸੋ",
                             language="pa", confidence=0.40)
    decision = await lid.from_audio(b"x" * 2000, current="hi")

    assert decision.language == "pa"
    assert decision.source == "script"
    assert decision.certain is True
    assert decision.detected == "pa"


@pytest.mark.asyncio
async def test_a_shared_script_still_cannot_switch_anything(azure):
    """Hindi and Marathi both write Devanagari, so the writing proves nothing."""
    azure["result"] = _Found(text="मुझे गेहूँ "
                                  "का भाव",
                             language="mr", confidence=0.30)
    decision = await lid.from_audio(b"x" * 2000, current="hi")

    assert decision.language == "hi"
    assert decision.source == "kept"
    assert decision.detected == ""


@pytest.mark.asyncio
async def test_latin_letters_do_not_switch_anybody_to_english(azure):
    """A romanised transcript is exactly the case the script rule must not act on."""
    azure["result"] = _Found(text="mujhe kanak da bhaa dasso", language="pa",
                             confidence=0.30)
    decision = await lid.from_audio(b"x" * 2000, current="pa")
    assert decision.language == "pa"


# ── "too much delay" and "conversational ai miss" ─────────────────────────
@pytest.fixture(autouse=True)
def _credit():
    sarvam_client.forget_refusal()
    yield
    sarvam_client.forget_refusal()


def test_a_402_is_remembered_rather_than_paid_for_every_turn():
    """Sarvam answers HTTP 402 in ~0.3 s. Twice a turn, that is most of a second
    spent being told no, on a line with a two-second budget."""
    assert not sarvam_client.out_of_credit()
    sarvam_client._note_refusal(402, '{"error":{"code":"insufficient_quota_error"}}')

    blocked = sarvam_client.out_of_credit()
    assert blocked and "credit" in blocked.lower()
    assert sarvam_client.client.available() is False


def test_the_router_goes_straight_to_the_other_model():
    sarvam_client._note_refusal(402, "No credits available.")
    for choice in (router.voice_chat(), router.voice_data(), router.call_summary()):
        assert choice.provider == router.MISTRAL
        assert choice.fallback != router.SARVAM, (
            "it would still try the provider that has no credit")


def test_speech_goes_to_azure_rather_than_asking_twice(monkeypatch):
    from app.speech import azure_speech, voice_io
    monkeypatch.setattr(azure_speech, "configured", lambda: True)
    monkeypatch.setattr(sarvam_client.client, "configured", lambda: True)

    assert voice_io.provider_name() == "sarvam"
    sarvam_client._note_refusal(402, "No credits available.")
    assert voice_io.provider_name() == "azure"


def test_status_says_so_in_words(monkeypatch):
    from app.speech import azure_speech, voice_io
    monkeypatch.setattr(azure_speech, "configured", lambda: True)
    monkeypatch.setattr(sarvam_client.client, "configured", lambda: True)
    sarvam_client._note_refusal(402, "No credits available.")

    note = voice_io.status()["note"]
    assert "credit" in note.lower(), (
        "a provider that refuses every request reported itself as fine")
    assert voice_io.status()["sarvam_available"] is False


def test_it_tries_again_later():
    """A top-up should not need a deploy."""
    sarvam_client._note_refusal(402, "No credits available.")
    assert sarvam_client.out_of_credit()
    sarvam_client._no_credit["until"] = sarvam_client.time.time() - 1.0
    assert not sarvam_client.out_of_credit()


def test_only_a_billing_refusal_counts():
    """A 503 is a bad minute, not an empty account. Blocking on it would take
    the better provider out of service for a quarter of an hour."""
    sarvam_client._note_refusal(503, "upstream unavailable")
    assert not sarvam_client.out_of_credit()
    sarvam_client._note_refusal(429, "slow down")
    assert not sarvam_client.out_of_credit()


# ── the connection that was re-made on every spoken word ─────────────────
def test_azure_speech_shares_the_pooled_connection():
    """Opening a client per request cost a measured 265 ms of every Mistral
    answer, which is why the pool exists. Speech was still doing it, twice a
    turn, to a region on another continent."""
    import inspect
    from app.speech import azure_speech

    source = inspect.getsource(azure_speech)
    assert "httpx.AsyncClient(" not in source, (
        "Azure Speech is opening its own client again; use llm.pool so the "
        "handshake is paid once")
    assert "pool.client(" in source


# ── it does not interrogate the caller ───────────────────────────────────
def test_it_never_asks_who_is_calling():
    from app.conversation import voice_agent

    persona = voice_agent.PERSONA.lower()
    assert "never ask the caller who they are" in persona
    assert "aap kaun hain" in persona, (
        "the exact phrase it produced on a live call should be named, or the "
        "instruction is advice rather than a rule")


# ── "too much delay in response ... less than 2 second" ──────────────────
def test_a_phone_turn_does_not_wait_six_seconds_for_a_price():
    """The three legs that cannot be avoided measure 0.60 s (identify the
    audio), 0.70 s (the model) and 0.78 s (speak it) on the deployed host.
    A live lookup allowed six seconds in front of them is eight seconds of
    silence with a farmer listening to it."""
    from app.conversation import lookups

    assert lookups.CALL_BUDGET_SECONDS <= 1.5
    assert lookups.CALL_BUDGET_SECONDS < lookups.BUDGET_SECONDS, (
        "the phone should not wait as long as a page that has already drawn")


def test_the_screen_keeps_the_longer_one():
    from app.conversation import lookups
    assert lookups.BUDGET_SECONDS >= 5.0


@pytest.mark.asyncio
async def test_the_call_channel_is_the_one_that_gets_the_short_budget(monkeypatch):
    from app.conversation import lookups, voice_agent as agent_module

    seen = {}

    async def spy(said, **kwargs):
        seen[kwargs.get("channel")] = kwargs.get("budget")
        return lookups.Lookup()

    monkeypatch.setattr(agent_module.lookups, "for_question", spy)
    monkeypatch.setattr(agent_module.lookups, "CALL_BUDGET_SECONDS",
                        lookups.CALL_BUDGET_SECONDS)

    async def quiet(*args, **kwargs):
        return ("theek hai ji", "test")

    monkeypatch.setattr(agent_module.VoiceAgent, "_speak_turn",
                        lambda self, **kw: quiet())

    for channel, expected in (("call", lookups.CALL_BUDGET_SECONDS),
                              ("web", lookups.BUDGET_SECONDS)):
        await agent_module.voice_agent.respond(
            phone=PHONE, said="gehu ka bhav kya hai", call_id="B-" + channel,
            language="hi", channel=channel)
        assert seen.get(channel) == expected, (
            "%s waited %s for live data" % (channel, seen.get(channel)))
