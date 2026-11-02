"""One spoken turn in one round trip: POST /api/chat/voice.

Why this endpoint exists
------------------------
A spoken turn on the dashboard used to be four round trips - the browser's own
SpeechRecognition, the voiceprint gate, /api/chat, then a GET for the audio -
and it took eleven to fourteen seconds. Worse, the browser recogniser is handed
ONE locale before it starts listening, taken from whatever the language picker
said, so a farmer who spoke Tamil into an English page was transcribed by an
English engine and answered in English. "Reply in the language I spoke" could
not work, because nothing in the chain ever listened to the sound.

So the clip is uploaded once, Azure identifies the language FROM THE AUDIO and
transcribes it in the same request, and the reply comes back spoken. The
response is NDJSON: the words on the first line, the audio URL on the second,
so the answer is readable while it is still being synthesised.
"""
from __future__ import annotations

import json

import pytest

from app.speech import lid


def _lines(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


@pytest.fixture
def heard(monkeypatch):
    """Azure identifying a turn as whatever the test says it heard."""
    def install(language, transcript, *, certain=True, confidence=0.9):
        async def fake(audio, **kwargs):
            return lid.Decision(language, "azure-lid" if certain else "kept",
                                confidence=confidence,
                                detected=language if certain else "",
                                transcript=transcript,
                                note="test")
        monkeypatch.setattr(lid, "from_audio", fake)
    return install


def _post(client, **extra):
    data = {"language": "en", "session_id": "voice-test", "speak": "false"}
    data.update(extra)
    return client.post("/api/chat/voice",
                       files={"audio": ("turn.webm", b"x" * 4096, "audio/webm")},
                       data=data)


def test_the_answer_is_in_the_language_that_was_spoken(client, heard):
    """The whole point. The page said English; the farmer spoke Punjabi."""
    heard("pa", "ਮੈਨੂੰ ਕਣਕ ਦਾ ਭਾਅ ਦੱਸੋ")
    body = _lines(_post(client))[0]
    assert body["ok"] is True
    assert body["transcript"].startswith("ਮੈਨੂੰ")
    assert body["language"] == "pa", "answered in the language of the picker, not the speaker"
    assert body["detected"] == "pa"
    assert body["language_source"] == "azure-lid"


def test_an_unsure_identification_does_not_switch_anybody(client, heard):
    """Uncertain means keep what you had - never guess, and never report a
    detection that did not happen."""
    heard("en", "hmm", certain=False, confidence=0.2)
    body = _lines(_post(client, language="hi"))[0]
    assert body["detected"] == "", "a guess was reported as a detection"


def test_silence_is_not_an_error(client, monkeypatch):
    """A cough, or a clip Azure could not read. The orb listens again; a red
    toast for somebody clearing their throat is noise."""
    async def nothing(audio, **kwargs):
        return lid.Decision("en", "kept", transcript="", note="No speech.")
    monkeypatch.setattr(lid, "from_audio", nothing)

    response = _post(client)
    assert response.status_code == 200
    body = _lines(response)[0]
    assert body["ok"] is False and body["reply"] == ""


def test_an_empty_upload_is_refused(client):
    response = client.post("/api/chat/voice",
                           files={"audio": ("turn.webm", b"", "audio/webm")},
                           data={"language": "en"})
    assert response.status_code == 400


def test_the_words_arrive_before_the_audio(client, heard, monkeypatch):
    """Two lines, in that order. One object holding both would mean waiting for
    speech synthesis before anything at all could be shown."""
    heard("hi", "मंडी भाव क्या है")

    from app.speech import voice_io

    async def fake_speak(text, language, **kwargs):
        return voice_io.Spoken(True, url="/api/speech/audio/test.mp3",
                               provider="test", voice="test")
    monkeypatch.setattr(voice_io, "synthesize", fake_speak)

    lines = _lines(_post(client, speak="true"))
    assert len(lines) == 2
    assert lines[0]["reply"] and "audio_url" not in lines[0]
    assert lines[1]["audio_url"].endswith(".mp3")
