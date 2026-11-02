"""Translating the interface.

The guarantees that matter are about what happens when translation goes WRONG, because
the dashboard has to stay usable either way: a farmer reading an English label is mildly
inconvenienced, a farmer reading a blank button has a broken screen.
"""
from __future__ import annotations

import json

import pytest

from app.llm import mistral_client
from app.services import i18n, translator


@pytest.fixture(autouse=True)
def no_azure(monkeypatch):
    """Azure Translator off unless a test asks for it.

    Interface translation asks Azure first and only falls back to the language
    model. These tests are about the FALLBACK - what a farmer sees when
    translation goes wrong - so the first engine is turned off rather than
    left to answer, which would also put a real network call in the suite.
    """
    monkeypatch.setattr(translator, "configured", lambda: False)


@pytest.fixture
def scripted_azure(monkeypatch):
    """Azure answering with exactly what the test says, and counting calls."""
    seen = []

    def install(pairs):
        async def fake(target, texts):
            seen.append((target, list(texts)))
            return {t: pairs[t] for t in texts if t in pairs}
        monkeypatch.setattr(translator, "configured", lambda: True)
        monkeypatch.setattr(translator, "translate", fake)
        return seen
    return install


@pytest.fixture
def scripted_model(monkeypatch):
    """A model that returns exactly what the test tells it to."""
    def install(reply, record=None):
        async def fake_chat(messages, **kwargs):
            if record is not None:
                record.append(messages)
            return type("R", (), {"ok": reply is not None, "text": reply,
                                  "model": "test"})()
        monkeypatch.setattr(mistral_client.client, "chat", fake_chat)
        monkeypatch.setattr(mistral_client.client, "configured", lambda: True)
    return install


# ------------------------------------------------------------- the contract
def test_english_is_the_source_and_is_never_translated(client):
    body = client.post("/api/i18n/translate",
                       json={"language": "en", "strings": ["Soil Health"]}).json()
    assert body["translations"] == {}
    assert "source" in body["note"].lower()


def test_an_unknown_language_is_refused_rather_than_silently_english(client):
    """resolve() falls back to a default, so without this check an unknown code
    would return English and the farmer would see no change and no error."""
    assert client.post("/api/i18n/translate",
                       json={"language": "klingon", "strings": ["Soil"]}).status_code == 400


def test_a_missing_language_is_refused(client):
    assert client.post("/api/i18n/translate",
                       json={"strings": ["Soil"]}).status_code == 400


@pytest.mark.asyncio
async def test_a_translation_is_cached_and_the_model_is_called_once(client, scripted_model):
    calls = []
    scripted_model(json.dumps(["मिट्टी की सेहत", "पाइपलाइन चलाएँ"]), record=calls)
    strings = ["Soil Health", "Run pipeline"]

    first = await i18n.translate("hi", strings, lang_name="Hindi")
    assert first["translations"]["Soil Health"] == "मिट्टी की सेहत"
    assert first["fetched"] == 2

    second = await i18n.translate("hi", strings, lang_name="Hindi")
    assert second["translations"]["Soil Health"] == "मिट्टी की सेहत"
    assert second["fetched"] == 0, "the second call must come from the cache"
    assert len(calls) == 1, "a cached string must not reach the model again"


@pytest.mark.asyncio
async def test_every_requested_string_comes_back(client, scripted_model):
    """The caller applies the whole map without checking for holes, so a hole would
    leave a label blank."""
    scripted_model(json.dumps(["एक", "दो"]))
    out = await i18n.translate("hi", ["One", "Two"], lang_name="Hindi")
    assert set(out["translations"]) == {"One", "Two"}


# -------------------------------------------- what happens when it goes wrong
@pytest.mark.asyncio
async def test_a_failed_model_falls_back_to_english(client, scripted_model):
    scripted_model(None)
    out = await i18n.translate("hi", ["Soil Health"], lang_name="Hindi")
    assert out["translations"]["Soil Health"] == "Soil Health"


@pytest.mark.asyncio
async def test_a_short_array_is_rejected_whole(client, scripted_model):
    """A model that returns three translations for four labels has dropped one, and
    zipping them would put the wrong text on the wrong control."""
    scripted_model(json.dumps(["एक", "दो"]))
    out = await i18n.translate("hi", ["One", "Two", "Three", "Four"], lang_name="Hindi")
    assert all(out["translations"][k] == k for k in ("One", "Two", "Three", "Four"))


@pytest.mark.asyncio
async def test_junk_from_the_model_is_discarded(client, scripted_model):
    scripted_model("I'm sorry, I cannot do that.")
    out = await i18n.translate("hi", ["Soil Health"], lang_name="Hindi")
    assert out["translations"]["Soil Health"] == "Soil Health"


@pytest.mark.asyncio
async def test_nothing_is_stored_when_the_translation_equals_the_source(client,
                                                                        scripted_model):
    scripted_model(json.dumps(["Soil Health"]))
    await i18n.translate("hi", ["Soil Health"], lang_name="Hindi")
    assert i18n.cached("hi", ["Soil Health"]) == {}


# --------------------------------------------------------------- the prompt
def test_the_prompt_protects_numbers_units_and_scheme_names():
    """A model that "translates" 1800, pH or PM-KISAN is worse than no translation."""
    messages = i18n._prompt("Hindi", ["Soil Health"])
    system = messages[0]["content"]
    for guard in ("numbers", "units", "PM-KISAN", "Saarthi"):
        assert guard in system, guard
    assert "JSON object" in system
    # Keyed by index, so a dropped entry costs one label instead of the whole batch.
    assert json.loads(messages[1]["content"]) == {"0": "Soil Health"}


@pytest.mark.asyncio
async def test_long_strings_are_left_out(client):
    """A paragraph is content, not a label; it is translated where it is generated."""
    long_text = "x" * (i18n.MAX_LEN + 10)
    out = await i18n.translate("hi", [long_text], lang_name="Hindi")
    assert long_text not in out["translations"]


@pytest.mark.asyncio
async def test_duplicates_are_asked_for_once(client, scripted_model):
    calls = []
    scripted_model(json.dumps({"0": "खेत"}), record=calls)
    out = await i18n.translate("hi", ["Field", "Field", "Field"], lang_name="Hindi")
    assert out["translations"]["Field"] == "खेत"
    sent = json.loads(calls[0][1]["content"])
    assert sent == {"0": "Field"}, "the same string must not be paid for three times"


# ------------------------------------------------------------- the listing
def test_the_language_list_names_english_as_the_source(client):
    body = client.get("/api/i18n/languages").json()
    assert body["source"] == "en"
    codes = [l["code"] for l in body["languages"]]
    assert "hi" in codes and "en" in codes
    for entry in body["languages"]:
        assert entry["native_name"], entry["code"]


@pytest.mark.asyncio
async def test_a_reply_missing_one_entry_still_delivers_the_rest(client,
                                                                scripted_model):
    """A dropped line must cost one label, not the whole screen.

    With a plain array there was no safe way to use a short reply - every value
    after the gap would land on the wrong control - so the only option was to
    discard all of it. A batch is forty labels, so one bad line left forty strings
    in English with nothing in the interface to say why. Keyed by index there is no
    ambiguity: the missing key stays English and everything else is applied.
    """
    scripted_model(json.dumps({"0": "खेत", "2": "मौसम"}))
    out = await i18n.translate("hi", ["Field", "Soil", "Weather"], lang_name="Hindi")

    assert out["translations"]["Field"] == "खेत"
    assert out["translations"]["Weather"] == "मौसम"
    # The one it skipped comes back as English, never blank.
    assert out["translations"]["Soil"] == "Soil"


@pytest.mark.asyncio
async def test_a_value_is_never_put_on_the_wrong_label(client, scripted_model):
    """An array of the wrong length is still refused outright.

    This is the failure the keyed form was introduced to avoid, and the fallback
    path must not reintroduce it: shifting values up by one would quietly label the
    irrigation button with the fertiliser text, which is worse than English.
    """
    scripted_model(json.dumps(["खेत", "मौसम"]))
    out = await i18n.translate("hi", ["Field", "Soil", "Weather"], lang_name="Hindi")

    for source in ("Field", "Soil", "Weather"):
        assert out["translations"][source] == source


# =============================================== Azure Translator is asked first
#
# It replaced a language-model prompt on 2026-11-02. The three things that had
# to become true, and are asserted here, are the three things that were wrong
# with the prompt: a rate limit left screens half translated, a batch of forty
# took seconds, and a reply that was not valid JSON lost the whole batch.
@pytest.mark.asyncio
async def test_azure_is_asked_before_the_model(scripted_azure, scripted_model):
    asked = scripted_azure({"Soil Health": "मिट्टी की सेहत"})
    calls = []
    scripted_model(json.dumps({"0": "नहीं"}), record=calls)

    body = await i18n.translate("hi", ["Soil Health"], lang_name="Hindi")

    assert body["translations"]["Soil Health"] == "मिट्टी की सेहत"
    assert body["engine"] == "azure-translator"
    assert asked == [("hi", ["Soil Health"])]
    assert not calls, "the model was asked for something Azure had already done"


@pytest.mark.asyncio
async def test_the_model_finishes_what_azure_could_not(scripted_azure,
                                                       scripted_model):
    """A partial answer is not a failed one.

    Azure translates most of a batch and drops one - an empty string back, a
    term it would not touch. That one string goes to the model rather than
    being shown in English, and the engine says both were involved.
    """
    scripted_azure({"Soil Health": "मिट्टी की सेहत"})
    scripted_model(json.dumps({"0": "पाइपलाइन चलाएँ"}))

    body = await i18n.translate("hi", ["Soil Health", "Run pipeline"],
                                lang_name="Hindi")

    assert body["translations"]["Soil Health"] == "मिट्टी की सेहत"
    assert body["translations"]["Run pipeline"] == "पाइपलाइन चलाएँ"
    assert body["engine"] == "azure-translator+model"


@pytest.mark.asyncio
async def test_a_second_request_asks_nobody(scripted_azure, scripted_model):
    """Translated once, ever. The cache is the reason this is affordable at all."""
    asked = scripted_azure({"Market Prices": "बाजार भाव"})
    scripted_model(None)

    await i18n.translate("hi", ["Market Prices"], lang_name="Hindi")
    again = await i18n.translate("hi", ["Market Prices"], lang_name="Hindi")

    assert again["translations"]["Market Prices"] == "बाजार भाव"
    assert again["fetched"] == 0
    assert len(asked) == 1, "the same string was sent to Azure twice"


@pytest.mark.asyncio
async def test_azure_refusing_outright_falls_back_to_the_model(scripted_azure,
                                                               scripted_model):
    """Azure configured and answering nothing at all - a bad key, a dead region.

    The model takes the whole batch, and the engine says so. This is the case
    that decides whether a Translator outage is a slower dashboard or an English
    one, so it is asserted rather than assumed.
    """
    scripted_azure({})                       # configured, translates nothing
    scripted_model(json.dumps({"0": "मौसम", "1": "नाइट्रोजन"}))

    body = await i18n.translate("hi", ["Weather", "Nitrogen"], lang_name="Hindi")

    assert body["translations"]["Weather"] == "मौसम"
    assert body["translations"]["Nitrogen"] == "नाइट्रोजन"
    assert body["engine"] == "model"


def test_nepali_is_offered(client):
    """The fourteenth. Added with the Azure switch, because a picker that lists a
    language the server will refuse is worse than one that does not list it."""
    body = client.get("/api/i18n/languages").json()
    codes = [l["code"] for l in body["languages"]]
    assert "ne" in codes and len(codes) == 14
    assert client.post("/api/i18n/translate",
                       json={"language": "ne", "strings": ["Soil"]}).status_code == 200
