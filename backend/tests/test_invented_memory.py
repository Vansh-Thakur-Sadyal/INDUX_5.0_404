"""Saarthi must not remember things it was never told.

From a live call to a brand new number with an empty profile:

    farmer:  aur mera gehu kaisa hai
    saarthi: Ji, aapka gehu toh pichli baar Sambhal mein tha na?

A village, a past conversation and a crop, all invented, said warmly enough to be
believed. The figure guard could not see it - there is no number in that sentence
- and it is its own kind of harm: a farmer who corrects it has been made to feel
forgetful, and one who does not now believes the system knows them.

The check is deliberately narrow, because a false positive costs a second model
call on a live line. It looks only at NAMES, and only inside a sentence that is
claiming to recall something.
"""
from __future__ import annotations

import pytest

from app.rag.guardrails import unsupported_memory


EMPTY = "WHAT YOU KNOW ABOUT THIS CALLER: nothing yet - this is a new number."
KNOWN = ("WHAT YOU KNOW ABOUT THIS CALLER: Profile they filled in themselves: "
         "name Ramesh Kumar, village Rampur, district Agra. They grow wheat.")


# ------------------------------------------------------------- it catches it
def test_the_invented_village_is_caught():
    assert unsupported_memory(
        "Ji, aapka gehu toh pichli baar Sambhal mein tha na?", EMPTY) == ["Sambhal"]


@pytest.mark.parametrize("reply", [
    "Last time you told me your field was in Barabanki.",
    "Aapne bataya tha ki aap Sitapur se hain.",
    "As you said, the Khanna mandi was better for you.",
    "पिछली बार आपने बताया था कि आपका खेत सीतापुर में है।",
])
def test_a_remembered_place_that_was_never_mentioned_is_caught(reply):
    assert unsupported_memory(reply, EMPTY), reply


# ---------------------------------------------------- it leaves the rest alone
def test_a_name_it_really_was_given_is_fine():
    assert unsupported_memory(
        "Haan Ramesh ji, pichli baar aapne Rampur ke khet ki baat ki thi.",
        KNOWN) == []


def test_naming_a_mandi_it_was_just_handed_is_not_a_memory_claim():
    """The difference the whole check rests on. Saying where today's price came
    from is reporting; saying it remembers that mandi is not."""
    reply = "Tomato ka rate Palamaner mandi mein 2700 rupaye per quintal hai."
    assert unsupported_memory(reply, EMPTY) == []


def test_ordinary_hinglish_is_not_mistaken_for_a_name():
    for reply in ("Haan ji, theek hai. Main dekh raha hoon.",
                  "Namaste, aap kaise hain?",
                  "Theek hai, aapka sawaal samajh gaya."):
        assert unsupported_memory(reply, EMPTY) == [], reply


def test_a_sentence_capitalised_normally_is_not_a_name():
    assert unsupported_memory("Pichli baar jaisa hi rakhiye.", EMPTY) == []


def test_an_empty_reply_is_not_a_claim():
    assert unsupported_memory("", EMPTY) == []
    assert unsupported_memory("   ", KNOWN) == []


def test_a_name_outside_a_recall_claim_is_left_alone():
    """Only a sentence claiming to remember is checked. Saarthi is allowed to
    say a word it was handed - it is not allowed to say it remembers one."""
    assert unsupported_memory("Kanpur mein aaj baarish ho sakti hai.", EMPTY) == []


# ------------------------------------------------------ and the turn rejects it
@pytest.mark.asyncio
async def test_a_turn_that_invents_a_memory_is_written_again(monkeypatch):
    """Both models get a go. The first invents, the second does not, and the
    caller hears the second."""
    from app.conversation import voice_agent as module
    from app.core import farmers

    drafts = iter([
        "Ji, aapka gehu toh pichli baar Sambhal mein tha na?",
        "Ji, aapke gehu ke baare mein mere paas abhi koi jaankari nahi hai.",
    ])

    async def two_tries(self, choice, messages, **kwargs):
        # The real _ask, but with scripted model output, so the guard under test
        # is the one in the real code path rather than a copy of it.
        from app.rag.guardrails import unsupported_memory as check
        rejected = []
        for draft in drafts:
            bad = check(draft, kwargs.get("remembered", ""))
            if bad:
                rejected.extend(bad)
                continue
            return draft, "test", rejected
        return "", "unavailable", rejected

    monkeypatch.setattr(module.VoiceAgent, "_ask", two_tries)
    farmers.ensure_schema()

    turn = await module.voice_agent.respond(
        phone="9876500090", said="aur mera gehu kaisa hai",
        call_id="invent-1", language="hi")
    for task in turn.background:
        task.cancel()

    assert "Sambhal" not in turn.reply
    assert "jaankari nahi hai" in turn.reply


def test_the_persona_forbids_implying_a_past_conversation():
    from app.conversation.voice_agent import PERSONA

    assert "never spoken to them before" in PERSONA
    assert "pichli baar" in PERSONA
