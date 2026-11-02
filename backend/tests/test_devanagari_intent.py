"""Spoken Hindi has to reach the same intents as typed Hinglish.

The phone line transcribes Hindi speech into DEVANAGARI. The keyword router and the
farm lexicon were Latin-script only, so every spoken Hindi question scored nothing,
fell through to "general", and was answered as small talk - while the same question
typed as Hinglish on the dashboard worked perfectly. That is why it went unnoticed:
the broken path was the one nobody could type into.
"""
from __future__ import annotations

import pytest

from app.conversation import intent as intent_module

# (what a farmer says, what it is about)
SPOKEN = [
    ("मुझे यह जानना है कि खेतों में कौन सी कौन सी फसल उगाने चाहिए?", "crop_selection"),
    ("इस मौसम में कौन सी फसल लगाऊं", "crop_selection"),
    ("मिट्टी कैसी है", "soil_health"),
    ("मिट्टी की जांच करवानी है", "soil_health"),
    ("मेरे खेत में पानी कब देना चाहिए", "irrigation"),
    ("सिंचाई करनी चाहिए क्या", "irrigation"),
    ("कौन सी खाद डालनी चाहिए", "nutrient"),
    ("यूरिया कितना डालें", "nutrient"),
    ("गेहूं का मंडी भाव क्या है", "market"),
    ("फसल कितने दाम में बिकेगी", "market"),
    ("पत्तों पर पीले धब्बे आ गए हैं", "pest_disease"),
    ("फसल में कीड़े लग गए हैं", "pest_disease"),
    ("मौसम कैसा रहेगा", "weather"),
    ("बारिश होगी क्या", "weather"),
    ("खरपतवार कैसे हटाऊं", "weed"),
    ("पराली का क्या करें", "residue"),
]


@pytest.mark.parametrize("said,expected", SPOKEN)
def test_spoken_hindi_reaches_the_right_intent(said, expected):
    result = intent_module.detect(said)
    assert result.intent == expected, "%r -> %s (scores %s)" % (
        said, result.intent, result.scores)
    assert result.confidence >= 0.6, "too unsure to consult the farm data"


@pytest.mark.parametrize("said,expected", SPOKEN)
def test_the_same_question_in_hinglish_agrees(said, expected):
    """Both scripts must route the same way, or the phone and the dashboard disagree."""
    # a representative Hinglish phrasing per intent
    hinglish = {
        "crop_selection": "kaun si fasal ugaye",
        "soil_health": "mitti kaisi hai",
        "irrigation": "khet me paani kab dena hai",
        "nutrient": "kaun si khad daale",
        "market": "gehu ka mandi bhav kya hai",
        "pest_disease": "patte par peela rog lag gaya",
        "weather": "mausam kaisa rahega barish hogi",
        "weed": "kharpatwar kaise hataye",
        "residue": "parali ka kya kare",
    }[expected]
    assert intent_module.detect(hinglish).intent == expected


def test_small_talk_is_still_small_talk():
    """The fix must not turn every greeting into a farm query."""
    for greeting in ("नमस्ते कैसे हैं आप", "हाँ जी ठीक हूँ", "धन्यवाद"):
        # Greetings used to land in "general", which invokes agents 1, 2, 4, 5
        # and 15 - so saying hello produced a full farm advisory. They now have
        # an intent of their own that runs nothing.
        result = intent_module.detect(greeting)
        assert result.intent == "small_talk"
        assert result.agents == []


def test_the_lexicon_handles_hindi_inflection():
    """खेत becomes खेतों, फसल becomes फसलें: stems, not whole words."""
    for word in ("खेत", "खेतों", "खेती"):
        assert "field" in intent_module.normalise_query(word)
    for word in ("फसल", "फसलें", "फसलों"):
        assert "crop" in intent_module.normalise_query(word)


def test_a_devanagari_question_is_routed_to_the_data_path():
    """general intents never consult the farm data; that was the visible symptom."""
    from app.conversation.voice_agent import DATA_INTENTS
    result = intent_module.detect("खेतों में कौन सी फसल उगाने चाहिए")
    assert result.intent in DATA_INTENTS
    assert result.confidence >= 0.6
