"""Answering in the language the farmer just used.

Reported: "if I start in English continue in English; if in the same
conversation I switch to Punjabi, the output should come in Punjabi." The
language used to be fixed when the conversation opened - a dropdown on the page
or a keypad digit on the phone - and nothing ever revisited it, so a farmer who
switched got the rest of the conversation in the language they had stopped
speaking.

There are two channels and they have different evidence. The browser and the
app send AUDIO, so Azure identifies the language from the sound itself. The
phone line sends a TRANSCRIPT, so the only evidence is the script it is written
in - which settles it for eleven of the thirteen outright, and for the two
shared scripts leaves the conversation where it was.
"""
from __future__ import annotations

import pytest

from app.core import languages


# ---------------------------------------------------------------- the registry
def test_every_language_has_a_locale_and_a_voice():
    """A missing voice is a language that silently falls back to another one."""
    for lang in languages.LANGUAGES:
        assert lang.locale.count("-") == 1, lang.code
        assert lang.voice.startswith(lang.locale), lang.code
        assert lang.native_name and lang.english_name


def test_the_locales_are_unique():
    locales = [l.locale for l in languages.LANGUAGES]
    assert len(set(locales)) == len(locales)


# --------------------------------------------------- reading it off the script
@pytest.mark.parametrize("text,expected", [
    ("गेहूं का भाव क्या है", "hi"),
    ("ਕਣਕ ਦਾ ਭਾਅ ਕੀ ਹੈ ਜੀ", "pa"),
    ("கோதுமை விலை என்ன", "ta"),
    ("ఈ రోజు గోధుమ ధర ఎంత", "te"),
    ("ಇಂದು ಗೋಧಿ ಬೆಲೆ ಎಷ್ಟು", "kn"),
    ("ഇന്ന് ഗോതമ്പ് വില എത്ര", "ml"),
    ("આજે ઘઉંનો ભાવ શું છે", "gu"),
    ("ଆଜି ଗହମର ଦାମ କେତେ", "or"),
    ("گندم کی قیمت کیا ہے", "ur"),
    ("What is the wheat price today", "en"),
])
def test_each_script_names_its_own_language(text, expected):
    assert languages.detect(text, "hi" if expected != "hi" else "en") == expected


def test_a_shared_script_keeps_the_language_already_in_force():
    """Hindi and Marathi are both Devanagari; Bengali and Assamese share a script
    too. Switching a Marathi speaker to Hindi on every turn because the letters
    look the same would be worse than not switching at all."""
    assert languages.detect("गव्हाचा भाव काय आहे", "mr") == "mr"
    assert languages.detect("गेहूं का भाव क्या है", "hi") == "hi"
    assert languages.detect("আজ গমের দাম কত", "as") == "as"
    assert languages.detect("আজ গমের দাম কত", "bn") == "bn"
    # From outside the pair, the commoner of the two is the answer.
    assert languages.detect("गेहूं का भाव क्या है", "en") == "hi"
    assert languages.detect("আজ গমের দাম কত", "en") == "bn"


def test_romanised_hindi_is_not_english():
    """Half of rural India types Hindi in the Latin alphabet. Reading the
    alphabet as the language would switch them to English mid-sentence."""
    assert languages.detect("mandi rate kya hai", "hi") == "hi"
    assert languages.detect("mujhe gehu ka bhav batao", "hi") == "hi"
    assert languages.detect("kanak da bhaa ki hai ji", "pa") == "pa"


def test_a_grunt_is_not_a_language_change():
    for noise in ("ok", "haan", "", "   ", "7", "..."):
        assert languages.detect(noise, "hi") == ""


def test_mixed_text_is_decided_by_the_bulk_of_it():
    assert languages.detect("गेहूं का rate क्या है भाई", "en") == "hi"


# ------------------------------------------------------- the conversation turn
@pytest.mark.asyncio
async def test_the_reply_follows_the_language_of_the_turn(client, fake_llm, monkeypatch):
    """The whole point: one conversation, two languages."""
    from app.conversation import manager, voice_agent

    session_id = "lang-switch-1"
    manager.load(session_id, language="en", channel="dashboard")

    first = await voice_agent.voice_agent.respond(
        phone="9000000501", said="What is the soil moisture", call_id="c1",
        language="en", session_id=session_id, allow_agents=False)
    assert first.language == "en"

    second = await voice_agent.voice_agent.respond(
        phone="9000000501", said="ਮੈਨੂੰ ਮਿੱਟੀ ਦੀ ਨਮੀ ਦੱਸੋ ਜੀ", call_id="c1",
        language="en", session_id=session_id, allow_agents=False)
    assert second.language == "pa", "the farmer switched and was not followed"

    # And it STAYS switched: a language change that lasts one message is not
    # one. Read from storage rather than through `load`, because `load` lets a
    # caller's language win - which is exactly why the detection happens before
    # the session is loaded rather than after.
    assert manager.language_of(session_id) == "pa"

    # A page that keeps sending the language it had before does not undo it.
    third = await voice_agent.voice_agent.respond(
        phone="9000000501", said="ਹੋਰ ਕੀ ਦੱਸ ਸਕਦੇ ਹੋ ਜੀ", call_id="c1",
        language="en", session_id=session_id, allow_agents=False)
    assert third.language == "pa"


@pytest.mark.asyncio
async def test_an_unchanged_language_is_left_alone(client, fake_llm):
    from app.conversation import manager, voice_agent

    session_id = "lang-switch-2"
    manager.load(session_id, language="hi", channel="dashboard")
    turn = await voice_agent.voice_agent.respond(
        phone="9000000502", said="गेहूं का भाव क्या है", call_id="c2",
        language="hi", session_id=session_id, allow_agents=False)
    assert turn.language == "hi"


# ------------------------------------------- the phone line, where it was failing
def test_punjabi_written_in_devanagari_is_punjabi():
    """Reported live: English, then Hindi, then Punjabi on one call, and every
    reply after the Hindi one came back in Hindi.

    The cause is not in our code at all - a telephone recogniser is handed ONE
    locale for the whole call, so when the caller switched to Punjabi the Hindi
    engine wrote Punjabi words down in Devanagari. Reading the alphabet said
    "Hindi" every single turn.
    """
    # "mainu kanak da bhaa dasso ji" as a Hindi recogniser hears it
    assert languages.detect("मैनू कणक दा भा दस्सो जी", "hi") == "pa"
    assert languages.detect("तुहाडा खेत किवें है जी", "hi") == "pa"


def test_ordinary_hindi_is_not_mistaken_for_punjabi():
    """The other half of the same check: the markers have to be words a Hindi
    sentence does not contain, or every caller gets switched to Punjabi."""
    for said in ("गेहूं का भाव क्या है",
                 "मुझे गेहूं का भाव बताइए",
                 "मेरे खेत में पानी कब देना है",
                 "आज मौसम कैसा रहेगा बताइए"):
        assert languages.detect(said, "hi") == "hi", said


def test_the_prompt_names_one_language_and_no_other():
    """Measured against the live model: a paragraph explaining that "Punjabi can
    arrive written in Devanagari, so write Gurmukhi for Punjabi" made an ENGLISH
    question come back in Punjabi. The smaller conversational model took the
    hint from the example. Code decides the language; the prompt names it once.
    """
    from app.conversation.voice_agent import language_line

    line = language_line("pa")
    assert "Punjabi" in line
    assert "Gurmukhi" in line, "the script decides the voice that reads it out"
    for other in ("Hindi", "Tamil", "Devanagari", "Bengali"):
        assert other not in line, "%r in a prompt about Punjabi" % other

    english = language_line("en")
    assert "English" in english
    for other in ("Punjabi", "Hindi", "Gurmukhi"):
        assert other not in english


@pytest.mark.asyncio
async def test_the_voice_follows_the_answer_not_the_request(client, fake_llm,
                                                            monkeypatch):
    """n8n asks for a language; the agent may answer in a different one. The
    audio has to follow the answer, or Gurmukhi is read out in a Hindi voice."""
    from app.conversation import manager, voice_agent

    session_id = "lang-switch-3"
    manager.load(session_id, language="hi", channel="call")
    turn = await voice_agent.voice_agent.respond(
        phone="9000000503", said="ਮੈਨੂੰ ਕਣਕ ਦਾ ਭਾਅ ਦੱਸੋ ਜੀ", call_id="c3",
        language="hi", session_id=session_id, allow_agents=False)
    assert turn.language == "pa"
    assert manager.language_of(session_id) == "pa"


@pytest.mark.asyncio
async def test_an_answer_in_the_wrong_language_is_translated(monkeypatch, fake_llm):
    """The instruction is in the system prompt and repeated immediately before
    the model answers. Measured over twelve turns of a six-language
    conversation, that was right eleven times - and a farmer who gets the one
    wrong answer cannot read it to ask again.

    So the answer is checked, and a wrong one is TRANSLATED rather than
    regenerated: translating carries the same figures across, while
    regenerating is a second chance to invent one.
    """
    from app.conversation import voice_agent as module
    from app.conversation.voice_agent import voice_agent as agent

    asked = []

    async def fake_ask(self, choice, messages, **kwargs):
        asked.append(messages)
        return "गेहूं का भाव 2750 रुपये है।", "sarvam", []

    # The CLASS, not the singleton: patching the instance leaves the bound
    # method behind as an instance attribute, which shadows the class for every
    # test that runs after this one.
    monkeypatch.setattr(module.VoiceAgent, "_ask", fake_ask)
    out = await agent._in_the_asked_language("ਕਣਕ ਦਾ ਭਾਅ 2750 ਰੁਪਏ ਹੈ।", "hi", None)

    assert out == "गेहूं का भाव 2750 रुपये है।"
    assert len(asked) == 1, "one extra call, and only when it drifted"
    system = asked[0][0]["content"]
    assert "Hindi" in system and "Devanagari" in system
    assert "exactly as it is" in system, "the figures must survive the translation"


@pytest.mark.asyncio
async def test_a_right_answer_costs_no_second_call(monkeypatch, fake_llm):
    from app.conversation import voice_agent as module
    from app.conversation.voice_agent import voice_agent as agent

    calls = []

    async def fake_ask(self, choice, messages, **kwargs):
        calls.append(messages)
        return "never used", "sarvam", []

    monkeypatch.setattr(module.VoiceAgent, "_ask", fake_ask)
    said = "गेहूं का भाव 2750 रुपये है।"
    assert await agent._in_the_asked_language(said, "hi", None) == said
    assert calls == [], "a correct answer was sent back to the model"


@pytest.mark.asyncio
async def test_a_failed_translation_keeps_the_answer(monkeypatch, fake_llm):
    """A right answer in the wrong language still beats silence."""
    from app.conversation import voice_agent as module
    from app.conversation.voice_agent import voice_agent as agent

    async def explode(self, choice, messages, **kwargs):
        raise RuntimeError("the model is down")

    monkeypatch.setattr(module.VoiceAgent, "_ask", explode)
    said = "ਕਣਕ ਦਾ ਭਾਅ 2750 ਰੁਪਏ ਹੈ।"
    assert await agent._in_the_asked_language(said, "hi", None) == said


@pytest.mark.asyncio
async def test_a_translation_may_not_bring_a_figure_with_it(monkeypatch, fake_llm):
    """The translation step must not undo the figure check.

    The answer handed to it has already been checked against the facts. A
    "translation" that comes back carrying a number the original did not have
    is not a translation, and taking it would be a way round the one rule this
    project cannot do without.
    """
    from app.conversation import voice_agent as module
    from app.conversation.voice_agent import voice_agent as agent

    async def invents(self, choice, messages, **kwargs):
        return "गेहूं का भाव 2200 से 2300 रुपये है।", "sarvam", []

    monkeypatch.setattr(module.VoiceAgent, "_ask", invents)
    said = "ਕਣਕ ਦਾ ਭਾਅ 2750 ਰੁਪਏ ਹੈ।"
    assert await agent._in_the_asked_language(said, "hi", None) == said


@pytest.mark.asyncio
async def test_an_english_answer_with_gurmukhi_in_it_is_put_right(monkeypatch,
                                                                  fake_llm):
    """Measured on the live server: an English question, right after two
    Punjabi turns, was answered "Soil moisture 79.0 % hai, jo ki high hai" with
    the Punjabi words in Gurmukhi. Two thirds of the letters are Latin, so it
    counted as English and nothing looked wrong - except to the farmer, who
    reads only one of the two.

    English WORDS inside an Indian sentence are normal and wanted. An Indian
    SCRIPT inside an English sentence is not.
    """
    from app.conversation import voice_agent as module
    from app.conversation.voice_agent import voice_agent as agent

    async def translate(self, choice, messages, **kwargs):
        return "Soil moisture is 79.0 %, which is high.", "sarvam", []

    monkeypatch.setattr(module.VoiceAgent, "_ask", translate)
    mixed = "Soil moisture 79.0 % ਹੈ, ਜੋ ਕਿ high ਹੈ।"
    assert await agent._in_the_asked_language(mixed, "en", None) == (
        "Soil moisture is 79.0 %, which is high.")


@pytest.mark.asyncio
async def test_english_words_inside_a_hindi_answer_are_left_alone(monkeypatch,
                                                                  fake_llm):
    """The other half. "Soil moisture 79 percent hai" in Devanagari is how a
    farmer actually talks, and rewriting it would be worse."""
    from app.conversation import voice_agent as module
    from app.conversation.voice_agent import voice_agent as agent

    called = []

    async def fake_ask(self, choice, messages, **kwargs):
        called.append(messages)
        return "never used", "sarvam", []

    monkeypatch.setattr(module.VoiceAgent, "_ask", fake_ask)
    said = "अभी soil moisture 79 percent है, जो high है।"
    assert await agent._in_the_asked_language(said, "hi", None) == said
    assert called == []
