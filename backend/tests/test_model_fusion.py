"""Two models, each on the job it is better at - and the check that makes it safe.

Sarvam speaks and Mistral reasons. That split is a measurement, not a taste:
over four conversational turns Sarvam answered in 286 ms against ministral-8b's
755 ms, and asked "namaste" ministral-8b replied by asking the caller who they
were. The measurements live in ``llm/router.py``.

The reason a fast, fluent model can be trusted with a farmer's numbers is that it
is not trusted: the agents compute, Sarvam says, and every spoken reply that
should carry figures is checked against the facts it was handed. Asked a wheat
price with no market data loaded, Sarvam answered "2200 se 2300 rupaye per
quintal" - a price from nowhere. These tests exist because of that reply.
"""
from __future__ import annotations

import json

import pytest

from app.conversation import voice_agent as voice_module
from app.conversation.voice_agent import NO_FIGURE_REPLY, voice_agent
from app.core import farmers
from app.llm import mistral_client, router, sarvam_client
from app.rag.guardrails import unsupported_figures

PHONE = "9812345679"


def _reply(content):
    return {"model": "test", "choices": [{"message": {"role": "assistant",
                                                      "content": content}}]}


@pytest.fixture
def both(monkeypatch):
    """Both providers, each saying what the test tells it to."""
    said = {"sarvam": "Haan ji, theek hai.", "mistral": "Haan ji, theek hai."}
    seen = {"sarvam": [], "mistral": []}

    def sarvam_transport(url, payload):
        seen["sarvam"].append(payload)
        return _reply(said["sarvam"])

    def mistral_transport(url, payload):
        seen["mistral"].append(payload)
        system = payload["messages"][0].get("content") or ""
        if "extract ONLY durable facts" in system:
            return _reply(json.dumps({"facts": []}))
        return _reply(said["mistral"])

    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral")
    sarvam_client.set_transport(sarvam_transport)
    mistral_client.set_transport(mistral_transport)
    yield said, seen
    sarvam_client.set_transport(None)
    mistral_client.set_transport(None)


async def _turn(said_text, call_id):
    farmers.ensure_schema()
    turn = await voice_agent.respond(phone=PHONE, said=said_text, call_id=call_id,
                                     language="hi")
    for task in turn.background:
        task.cancel()
    return turn


# ------------------------------------------------------------------ the split
@pytest.mark.asyncio
async def test_a_conversation_turn_goes_to_sarvam(both):
    """It is 469 ms faster and it answers "namaste" without asking who you are."""
    said, seen = both
    said["sarvam"] = "Namaste Ramesh ji."
    turn = await _turn("namaste", "fuse-1")
    assert turn.source == "sarvam"
    assert turn.reply == "Namaste Ramesh ji."
    assert seen["sarvam"], "the conversational model was never called"


@pytest.mark.asyncio
async def test_mistral_carries_the_call_when_sarvam_is_down(both, monkeypatch):
    """One provider failing must cost latency, not the call."""
    said, _ = both

    def broken(url, payload):
        raise RuntimeError("HTTP 503: sarvam down")

    sarvam_client.set_transport(broken)
    said["mistral"] = "Haan ji, bataiye."
    turn = await _turn("namaste", "fuse-2")
    assert turn.source == "mistral"
    assert turn.reply == "Haan ji, bataiye."


def test_the_jobs_are_split_by_what_each_is_better_at():
    assert router.voice_chat().provider == router.SARVAM
    assert router.voice_data().provider == router.SARVAM
    # Everything that reasons, cites or returns JSON stays with Mistral.
    assert router.rag_answer().provider == router.MISTRAL
    assert router.rerank().provider == router.MISTRAL
    assert router.fact_extraction().provider == router.MISTRAL


@pytest.mark.parametrize("question,expected", [
    ("mera gehu pila ho raha hai neeche ki pattiyon se", router.SARVAM),
    ("सरसों में कीड़े",
     router.SARVAM),
    ("why is my wheat yellowing at the lower leaves", router.MISTRAL),
    ("when should I irrigate cotton", router.MISTRAL),
])
def test_the_rewriter_is_chosen_by_the_language_of_the_question(question, expected):
    """Sarvam earns its extra 1.7 s on Hinglish and earns nothing on English.

    Given the Hinglish for "my wheat is yellowing from the lower leaves",
    sarvam-105b returned "wheat lower leaf chlorosis" and ministral-8b returned
    "wheat lodging caused by basal leaf infection". Lodging is the crop falling
    over. A rewrite into a different problem is worse than no rewrite.
    """
    assert router.query_rewrite(question).provider == expected


def test_a_short_english_question_is_not_mistaken_for_hinglish():
    """Two farm words are the threshold; one common word is not enough."""
    assert not router.is_indic("is my soil ok")
    assert not router.is_indic("wheat price")
    assert router.is_indic("mera khet kaisa hai")


# ------------------------------------------------- the check that makes it safe
@pytest.mark.asyncio
async def test_an_invented_figure_is_rejected_and_rephrased(both):
    """The reply that caused all of this: a price from nowhere."""
    said, _ = both
    said["sarvam"] = "Gehu ka rate 2200 se 2300 rupaye per quintal chal raha hai."
    said["mistral"] = "Main dekh kar bataata hoon."
    turn = await _turn("gehu ka bhav kya chal raha hai", "fuse-3")
    assert "2200" not in turn.reply and "2300" not in turn.reply
    assert turn.source == "mistral", "the invented figure was spoken anyway"
    assert any("not in the data" in n for n in turn.notices)


@pytest.mark.asyncio
async def test_with_no_facts_at_all_no_figure_may_be_spoken(both):
    """The bug this nearly shipped with.

    The check used to run only when there WERE facts to check against, so the
    one case it skipped was the case where the model had nothing and was most
    likely to invent. No facts means no figures, not no checking.
    """
    said, _ = both
    said["sarvam"] = "Urea 250 kilo daal dijiye."
    said["mistral"] = "Urea 300 kilo daal dijiye."
    turn = await _turn("kitni urea daalun", "fuse-4")
    assert "250" not in turn.reply and "300" not in turn.reply
    assert turn.reply == NO_FIGURE_REPLY
    assert turn.source == "withheld"


@pytest.mark.asyncio
async def test_the_farmer_is_told_honestly_rather_than_left_in_silence(both):
    """When both models invent, the reply still has to be something sayable."""
    said, _ = both
    said["sarvam"] = "Bhav 2400 hai."
    said["mistral"] = "Bhav 2500 hai."
    turn = await _turn("bhav kya hai", "fuse-5")
    assert turn.reply == NO_FIGURE_REPLY
    assert not any(ch.isdigit() for ch in turn.reply)
    # It says what it does not have, and asks them to try again. It does NOT
    # offer to check and come back: nothing on a phone line can speak
    # unprompted, so that promise left the caller waiting in a silence Twilio
    # read as the end of the call, and the line re-greeted them by name.
    assert "dobara poochh" in turn.reply
    for promise in ("dekh kar", "check", "call back", "come back"):
        assert promise not in turn.reply.lower(), turn.reply


@pytest.mark.asyncio
async def test_a_conversation_turn_is_not_figure_checked(both):
    """"Call me on turn three" is not a price. Checking pleasantries for figures
    would reject half of normal speech and cost a second doing it."""
    said, seen = both
    said["sarvam"] = "Namaste, 2 baje phir baat karte hain."
    turn = await _turn("namaste", "fuse-6")
    assert turn.source == "sarvam"
    assert len(seen["mistral"]) == 0, "a greeting triggered a second model call"


# ------------------------------------------------------- the figure check itself
def test_a_number_written_as_a_word_counts_as_given():
    """Measured: handed "4 mm rain in two days", Sarvam said "2 din mein 4 mm".

    That is a correct translation, not an invention, and a checker that called it
    one would reject the good Hindi replies and keep the bad ones.
    """
    facts = "4 mm rain expected in two days, moisture 30.6 percent"
    assert unsupported_figures("2 din mein 4 mm barish hogi", facts) == []
    assert unsupported_figures("do din mein barish hogi", facts) == []
    assert unsupported_figures("दो दिन में "
                               "4 mm", facts) == []


def test_a_figure_that_was_never_given_is_caught():
    facts = "moisture 30.6 percent, wheat modal price 2325 rupees per quintal"
    assert unsupported_figures("paanch din baad paani dijiye", facts) == ["5"]
    assert unsupported_figures("urea 250 kg daliye", facts) == ["250"]
    assert unsupported_figures("bhav 2325 hai", facts) == []


def test_the_check_is_one_directional():
    """The source may hold figures the reply leaves out - a spoken turn should
    mention one thing, not six."""
    facts = "moisture 30.6, pH 7.2, nitrogen 240, price 2325, rain 4 mm"
    assert unsupported_figures("Mitti mein nami 30.6 percent hai.", facts) == []


def test_devanagari_digits_are_read_as_digits():
    """A model writing Hindi may well use them, and they are the same numbers."""
    assert unsupported_figures("२३२५ rupaye",
                               "price 2325 rupees") == []
    assert unsupported_figures("९९९ rupaye",
                               "price 2325 rupees") == ["999"]


# ------------------------------------------------------------------- the rest
def test_speech_is_still_sarvam_and_embeddings_still_mistral():
    """The two things neither model competes for."""
    assert "mistral-embed" in str(router.describe())
    speech = [row for row in router.describe() if "Speech" in row["job"]]
    assert speech and speech[0]["provider"] == router.SARVAM


def test_every_routed_job_says_why():
    """A split with no stated reason is one nobody can revisit when it changes."""
    for row in router.describe():
        assert row["why"] and len(row["why"]) > 20, row["job"]
        assert row["provider"] in (router.SARVAM, router.MISTRAL)


def test_the_provider_can_be_overridden_without_touching_code(monkeypatch):
    """Both providers will change. The next person to measure should need one line."""
    monkeypatch.setenv("VOICE_CHAT_PROVIDER", "mistral")
    assert router.voice_chat().provider == router.MISTRAL
    assert router.voice_chat().fallback == router.SARVAM
    monkeypatch.setenv("REWRITE_PROVIDER", "mistral")
    assert router.query_rewrite("mera gehu pila hai").provider == router.MISTRAL


def test_the_reasoning_model_gets_the_budget_it_needs():
    """sarvam-105b returns nothing at all below about 900 tokens: it is a
    reasoning model and spends the budget on reasoning_content first. Measured:
    400 tokens produced an empty reply, 900 produced the answer."""
    choice = router.query_rewrite("mera gehu pila ho raha hai")
    assert choice.model == router.SARVAM_REASONING
    assert choice.min_tokens >= 900


def test_the_withheld_line_is_fixed_text_not_a_model_call():
    """At that point both models have shown they will invent. Asking one for an
    apology is asking it for one more chance to add a figure."""
    assert not any(ch.isdigit() for ch in NO_FIGURE_REPLY)
    assert isinstance(voice_module.NO_FIGURE_REPLY, str)
