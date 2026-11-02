"""What gets written into a farmer's books, and what must not.

Three entries from a real ledger, all wrong, all from the same screen:

    "no it's 43% rain on Thursday"          filed as 43 rupees SPENT
    "I have earn Rs 6000 from selling..."   filed as 6000 rupees SPENT
    "I earned 15000 from selling wheat"     never recorded at all

A ledger that invents entries from a weather question, or books a sale as a
loss, is worse than one that misses things: the farmer trusts the total.
"""
from __future__ import annotations

import pytest

from app.conversation.voice_agent import looks_like_money, parse_spoken_money


# ------------------------------------------- a measurement is not an amount
@pytest.mark.parametrize("said", [
    "no it is 43% rain on Thursday",
    "no it's 43% rain on Thursday",
    "kal 43 percent barish ka chance hai",
    "urea 250 kg daliye",
    "temperature 31 degrees hai",
    "upaj 20 quintal hogi",
    "soil ph 7.3 hai",
    "moisture 59 percent hai",
    "mere paas 4 acre zameen hai",
])
def test_a_number_wearing_a_unit_is_never_money(said):
    assert looks_like_money(said) is False, said
    assert parse_spoken_money(said) is None


def test_the_substring_that_caused_it():
    """"rs" matched inside "Thu-rs-day". Cues are whole words now."""
    assert looks_like_money("meeting on Thursday") is False
    assert looks_like_money("the horse is fine") is False
    assert looks_like_money("200 rs kharch hua") is True


# -------------------------------------------------- which way the money went
@pytest.mark.parametrize("said,amount", [
    ("I have earn Rs 6000 from selling my crops", 6000),
    ("I earned 15000 from selling wheat", 15000),
    ("12 hazaar mein gehu becha", 12000),
    ("gehu bechkar 8000 mila", 8000),
    ("fasal se 20 hazaar ki aamdani hui", 20000),
])
def test_money_coming_in_is_recorded_as_income(said, amount):
    """"earn" is not "earned" and "selling" is not "sold". The old list wanted
    whole words farmers do not use, so a sale was booked as a loss."""
    assert looks_like_money(said) is True, said
    parsed = parse_spoken_money(said)
    assert parsed["kind"] == "income", said
    assert parsed["amount_inr"] == amount


@pytest.mark.parametrize("said,amount", [
    ("I spent 4200 on urea", 4200),
    ("do hazaar ka urea liya", 2000),
    ("Rs 2000 is the cost", 2000),
    ("beej par 3500 kharch hua", 3500),
])
def test_money_going_out_is_recorded_as_an_expense(said, amount):
    parsed = parse_spoken_money(said)
    assert parsed["kind"] == "expense", said
    assert parsed["amount_inr"] == amount


def test_the_category_comes_from_the_farmers_own_word(said=None):
    assert parse_spoken_money("I spent 4200 on urea")["category"] == "fertiliser"
    assert parse_spoken_money("beej par 3500 kharch hua")["category"] == "seed"
    # Nothing recognised stays "other" rather than being guessed at.
    assert parse_spoken_money("Rs 2000 is the cost")["category"] == "other"


# ------------------------------------------- asking is not spending
@pytest.mark.parametrize("said", [
    "mandi me tomato 2500 rupaye quintal hai",
    "gehu ka bhav 2200 rupaye quintal chal raha hai",
    "kitna kharch hua is mahine",
    "what is the wheat rate today",
    "urea 1200 rupaye ka hai kya",
    "tomato ka rate 30 rupaye per kg hai",
])
def test_a_price_quote_or_a_question_is_not_an_entry(said):
    """The farmer asking what tomatoes fetch today had 2500 rupees written
    into their own spending for the day they asked."""
    assert parse_spoken_money(said) is None, said
    assert looks_like_money(said) is False, said


def test_but_a_rate_the_farmer_actually_got_is_an_entry():
    """"2500 a quintal" is a quote; "2500 a quintal mila" is a sale."""
    parsed = parse_spoken_money("mandi me 2500 rupaye quintal mila")
    assert parsed is not None
    assert parsed["kind"] == "income"
    assert parsed["amount_inr"] == 2500
