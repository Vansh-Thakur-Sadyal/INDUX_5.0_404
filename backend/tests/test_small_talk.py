"""A greeting must be answered as a greeting.

"hello" used to fall through to the `general` intent, which invokes agents
1, 2, 4, 5 and 15 - soil, soil health, crop stage, weather and validation. The
farmer got a full farm advisory in reply to saying hi, complete with a moisture
figure they had not asked for and a question about their sowing date.

The two halves of this are equally important, so both are tested: a pleasantry
must not run the chain, and anything that is actually a question must still
reach the agents that answer it.
"""
from __future__ import annotations

import pytest

from app.conversation import intent as intent_module


PLEASANTRIES = [
    "hello", "Hello!", "hi", "Hi.", "hey", "HELLO",
    "namaste", "Namaste ji", "namaskar", "ram ram",
    "good morning", "Good Evening",
    "thanks", "thank you", "dhanyavad", "shukriya",
    "bye", "ok", "theek hai",
    "who are you", "what can you do",
    "नमस्ते", "धन्यवाद",
]


REAL_QUESTIONS = [
    "when should I irrigate",
    "wheat price today",
    "my soil is dry",
    "what crop should I sow",
    "should I spray today",
    "how much have I spent this season",
    # The one that matters most: a greeting in front of a real question is a
    # question. Reading this as small talk would be worse than the bug.
    "hello, my wheat is yellowing",
    "hi, when should I sow mustard",
    "namaste, mera gehu pila ho raha hai",
]


@pytest.mark.parametrize("text", PLEASANTRIES)
def test_pleasantries_run_no_agents(text: str) -> None:
    result = intent_module.detect(text)
    assert result.intent == "small_talk", f"{text!r} routed to {result.intent}"
    assert result.agents == [], f"{text!r} would still run agents {result.agents}"


@pytest.mark.parametrize("text", REAL_QUESTIONS)
def test_questions_still_reach_the_agents(text: str) -> None:
    result = intent_module.detect(text)
    assert result.intent != "small_talk", f"{text!r} was dismissed as small talk"
    assert result.agents, f"{text!r} reached no agents"


def test_a_greeting_before_a_question_keeps_the_question() -> None:
    """The strictness is the point: only a bare pleasantry counts."""
    assert intent_module.detect("hello").intent == "small_talk"
    assert intent_module.detect("hello, my wheat is yellowing").intent == "pest_disease"


def test_small_talk_kind_is_reported() -> None:
    """The reply differs between a greeting, a thank-you and a goodbye."""
    assert intent_module.small_talk_kind("hello") == "greeting"
    assert intent_module.small_talk_kind("thank you") == "thanks"
    assert intent_module.small_talk_kind("bye") == "farewell"
    assert intent_module.small_talk_kind("who are you") == "about"
    assert intent_module.small_talk_kind("when should I irrigate") is None


def test_a_long_message_is_never_small_talk() -> None:
    """A farmer who writes a paragraph is not making conversation."""
    long_text = "hello " + "my wheat is yellowing at the tips and spreading " * 2
    assert intent_module.small_talk_kind(long_text) is None
