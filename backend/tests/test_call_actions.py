"""The calling bot doing things, not just talking about them.

A phone call is not a screen, and the same sentence means different things down
a line. Before this, a caller got:

    "sensor se reading lo"   -> "Opening the sensor page so you can take a
                                reading."   (in English, to a Hindi caller,
                                holding a phone with no page to open)
    "mujhe report bhej do"   -> "Ji, report toh main bhej doonga."
                                (I will send it - which it could not do)
    "dobara boliye"          -> a brand new sentence, which is the one thing
                                that does not help somebody who did not catch
                                the first one

Each of those is a test below. The theme is the same throughout: on a call, an
honest "I cannot do that from here, and here is where you can" beats a confident
sentence about something that will never happen.
"""
from __future__ import annotations

import pytest

from app.conversation import actions
from app.conversation.voice_agent import voice_agent
from app.core import commands, farmers


def _run(said, *, channel=actions.CALL, language="hi", **context):
    found = actions.detect(said)
    assert found.found, "%r was not recognised as an instruction" % said
    return found, actions.run(found, channel=channel, language=language,
                              context=context)


# ------------------------------------------------- it speaks the right language
@pytest.mark.parametrize("said,expected_id", [
    ("mandi bhav dikhao", "open_page"),
    ("sensor se reading lo", "take_reading"),
    ("report bhej do", "make_report"),
    ("band karo", "stop"),
])
def test_a_hindi_caller_is_answered_in_hindi(said, expected_id):
    """It answered "Opening mandi prices" in English, to a Hindi caller."""
    found, outcome = _run(said, language="hi")
    assert found.action.id == expected_id
    assert outcome.spoken
    # Crude but decisive: the Hindi lines here are Hinglish in Latin script, so
    # the test is that the ENGLISH sentence is not what came back.
    english = actions.run(actions.detect(said), channel=actions.CALL,
                          language="en").spoken
    assert outcome.spoken != english, "%r was answered in English" % said


def test_an_unwritten_language_falls_back_to_english_not_to_nothing():
    """A key read aloud teaches a farmer nothing. English is a worse answer than
    Tamil and a far better one than silence."""
    outcome = actions.run(actions.detect("band karo"), channel=actions.CALL,
                          language="ta")
    assert outcome.spoken == actions.say("stopped", "en")
    assert "stopped" not in outcome.spoken.lower() or outcome.spoken.strip()


# ----------------------------------------------- it does not promise the moon
def test_it_does_not_offer_to_read_a_sensor_it_cannot_reach():
    """The probe pairs with the app over Bluetooth. Nothing on a call can reach
    it, and a farmer told otherwise waits for a number that never comes."""
    _, outcome = _run("sensor se reading lo", language="hi")
    assert outcome.data.get("possible") is False
    assert "app" in outcome.spoken.lower()
    assert not outcome.ui, "it tried to open a page on a feature phone"


def test_it_does_not_promise_to_send_a_report():
    """The model answered "main bhej doonga". There is no SMS path out of this
    server - the only outbound channel is the call itself."""
    _, outcome = _run("mujhe report bhej do", language="hi")
    assert outcome.data.get("sent") is False
    assert "dashboard" in outcome.spoken.lower()


def test_opening_a_page_says_where_it_opened_it():
    """A caller has no screen in their hand. The page still opens - on whatever
    screen they have - but the sentence must not imply they can see it."""
    _, outcome = _run("mandi bhav dikhao", language="hi")
    assert "dashboard" in outcome.spoken.lower()
    assert outcome.ui == {"do": "navigate", "page": "market",
                          "label": "mandi prices"}

    # On a screen the same instruction says something different.
    _, on_screen = _run("mandi bhav dikhao", channel=actions.SCREEN, language="en")
    assert "dashboard" not in on_screen.spoken.lower()


# ------------------------------------------------------------------- repeat
@pytest.mark.parametrize("said", ["dobara boliye", "phir se boliye", "kya kaha",
                                  "say that again", "repeat", "suna nahi"])
def test_say_that_again_repeats_the_same_words(said):
    """Not a new sentence. They asked again because they did not catch it."""
    found, outcome = _run(said, last_reply="Mitti mein nami 30.6 percent hai.")
    assert found.action.id == "repeat"
    assert outcome.spoken == "Mitti mein nami 30.6 percent hai."


@pytest.mark.parametrize("said", [
    # "I am speaking to you again" - a greeting. This one cost a test: the word
    # for AGAIN on its own was enough, and a returning caller was answered by
    # repeating the previous call's last sentence.
    "phir se baat kar raha hoon",
    # A request for a call-back, which the task parser handles.
    "dobara call karna",
])
def test_the_word_for_again_is_not_enough_on_its_own(said):
    found = actions.detect(said)
    assert not found.found or found.action.id != "repeat",         "%r was read as 'say that again'" % said


def test_repeating_nothing_says_so():
    _, outcome = _run("dobara boliye", last_reply="")
    assert outcome.data.get("repeated") is False
    assert outcome.spoken


def test_repeat_is_checked_before_anything_else():
    """A farmer who missed the answer is not asking a new question, and running
    one would bury the answer they wanted."""
    found = actions.detect("dobara mandi bhav boliye")
    assert found.action.id == "repeat"


# ---------------------------------------------------------------- language
@pytest.mark.parametrize("said,expected", [
    ("english mein baat karo", "en"),
    ("speak in Hindi", "hi"),
    ("tamil mein boliye", "ta"),
    ("बंगाली में बात "
     "करो", "bn"),
])
def test_a_caller_can_switch_language_mid_call(said, expected):
    found, outcome = _run(said, language="hi")
    assert found.action.id == "set_language"
    assert outcome.data.get("language") == expected


def test_the_confirmation_is_spoken_in_the_new_language():
    """It is the only way the farmer can tell it worked."""
    _, outcome = _run("english mein baat karo", language="hi")
    assert outcome.spoken == actions.say("language_changed", "en")
    _, back = _run("hindi mein baat karo", language="en")
    assert back.spoken == actions.say("language_changed", "hi")


def test_naming_a_language_is_not_enough_on_its_own():
    """"Hindi mein kya likha hai" is a question about a document."""
    assert not actions.detect("hindi mein kya likha hai").found
    assert actions.language_named("hindi mein kya likha hai") == "hi"


def test_a_language_the_voice_cannot_speak_is_not_offered():
    """Switching into a language the TTS has no voice for turns the call silent."""
    from app.core import languages
    for code in actions._LANGUAGE_NAMES:
        assert languages.resolve(code).code == code, code


# ---------------------------------------------------------------- helpline
@pytest.mark.parametrize("said", ["kisi aadmi se baat karani hai",
                                  "helpline number batao",
                                  "kisan call centre ka number"])
def test_asking_for_a_person_gives_a_real_number(said):
    """It used to answer "I can connect you. Which person?" - which it cannot do.

    The number given is the national Kisan Call Centre, not this deployment's
    own line, which is a demo number and the wrong thing to hand someone who
    needs help.
    """
    found, outcome = _run(said, language="hi")
    assert found.action.id == "helpline"
    assert "1800 180 1551" in outcome.spoken
    assert outcome.data.get("number") == "1800 180 1551"


# ------------------------------------------------------- the whole spoken turn
@pytest.mark.asyncio
async def test_a_caller_asking_for_the_chain_actually_starts_it(monkeypatch):
    """"Poori jaanch chalu kar di hai" has to be true.

    On a screen the page runs the chain. On a call there is no page, so saying
    it was started and not starting it would be a plain lie.
    """
    import app.llm.mistral_client as mistral
    import app.llm.sarvam_client as sarvam

    farmers.ensure_schema()
    commands.ensure_schema()
    mistral.set_transport(lambda url, payload: {"model": "t", "choices": [
        {"message": {"role": "assistant", "content": "ok"}}]})
    sarvam.set_transport(lambda url, payload: {"model": "t", "choices": [
        {"message": {"role": "assistant", "content": "ok"}}]})
    try:
        turn = await voice_agent.respond(phone="9876500071",
                                         said="poori jaanch karo",
                                         call_id="chain-call", language="hi")
    finally:
        mistral.set_transport(None)
        sarvam.set_transport(None)

    assert turn.action == "run_pipeline"
    assert turn.background, "it said the check had started and started nothing"
    for task in turn.background:
        task.cancel()


@pytest.mark.asyncio
async def test_switching_language_outlives_the_turn_that_asked(monkeypatch):
    """Otherwise the next turn is back in the old language and they ask again."""
    farmers.ensure_schema()
    commands.ensure_schema()
    turn = await voice_agent.respond(phone="9876500072",
                                     said="english mein baat karo",
                                     call_id="lang-call", language="hi")
    for task in turn.background:
        task.cancel()
    assert turn.action == "set_language"
    # n8n reads this field to choose the voice for everything after it.
    assert turn.language == "en", "the call stayed in the old language"


@pytest.mark.asyncio
async def test_a_caller_instruction_still_reaches_no_model():
    """The whole reason it answers in milliseconds."""
    import app.llm.mistral_client as mistral
    import app.llm.sarvam_client as sarvam

    farmers.ensure_schema()
    commands.ensure_schema()
    reached = []
    mistral.set_transport(lambda url, payload: reached.append("mistral"))
    sarvam.set_transport(lambda url, payload: reached.append("sarvam"))
    try:
        turn = await voice_agent.respond(phone="9876500073",
                                         said="helpline number batao",
                                         call_id="helpline-call", language="hi")
    finally:
        mistral.set_transport(None)
        sarvam.set_transport(None)
    for task in turn.background:
        task.cancel()
    assert turn.action == "helpline"
    assert reached == []


@pytest.mark.asyncio
async def test_the_caller_can_hear_the_last_answer_again():
    """End to end: the words come out of the transcript, not out of a model."""
    farmers.ensure_schema()
    commands.ensure_schema()
    phone, call = "9876500074", "repeat-call"
    farmers.append_turn(call, farmers.normalise_phone(phone),
                        "mitti kaisi hai", "Mitti mein nami 30.6 percent hai.",
                        {"intent": "soil_health"})
    turn = await voice_agent.respond(phone=phone, said="dobara boliye",
                                     call_id=call, language="hi")
    for task in turn.background:
        task.cancel()
    assert turn.action == "repeat"
    assert turn.reply == "Mitti mein nami 30.6 percent hai."


# =================================================== found by the coverage audit
#
# Three things an audit of "what can a feature-phone caller actually reach"
# turned up, each of which looked fine until the sentence was actually said.

@pytest.mark.parametrize("said,rupees", [
    # The common Hindi phrasing, which parsed as nothing at all - so a farmer
    # saying "I bought two thousand of urea" had it written down nowhere.
    ("do hazaar ka urea liya", 2000),
    # And the one that was worse: twelve thousand recorded as twelve rupees.
    ("12 hazaar mein gehu becha", 12000),
    ("ek lakh ka tractor liya", 100000),
    ("paanch sau ka diesel", 500),
    ("1500 rupaye mazdoori di", 1500),
    ("2000 ka beej kharida", 2000),
])
def test_a_spoken_amount_is_read_at_the_right_size(said, rupees):
    """A thousandfold error in a farmer's own books, silently."""
    from app.conversation.voice_agent import spoken_amount
    assert spoken_amount(said) == rupees


@pytest.mark.parametrize("said", [
    "urea 250 kg daliye",          # a dose, not a purchase
    "do bigha zameen hai",         # an area
    "paanch quintal gehu hua",     # a yield
    "teen din baad paani dena",    # a count of days
])
def test_a_number_that_is_not_money_is_not_filed_as_money(said):
    """"urea 250 kg daliye" must never become 250 rupees of fertiliser."""
    from app.conversation.voice_agent import looks_like_money
    assert not looks_like_money(said), said


def test_a_spend_is_recorded_even_when_the_router_calls_it_fertiliser():
    """The audit's finding: "do hazaar ka urea liya" routes to `nutrient`,
    because "urea" outscores everything else - and the ledger path only ran for
    the `ledger` intent, so the spend was dropped on the floor."""
    from app.conversation.intent import detect
    from app.conversation.voice_agent import looks_like_money, parse_spoken_money

    said = "do hazaar ka urea liya"
    assert detect(said).intent != "ledger", "the premise of this test has changed"
    assert looks_like_money(said)
    parsed = parse_spoken_money(said)
    assert parsed["amount_inr"] == 2000
    assert parsed["category"] == "fertiliser"
    assert parsed["kind"] == "expense"


@pytest.mark.parametrize("said", ["patte ki photo bhejni hai",
                                  "send a photo of the leaf",
                                  "photo kheechni hai"])
def test_it_does_not_offer_to_look_at_a_photo_on_a_call(said):
    """The model answered "theek hai, aap photo bhej dijiye" - send the photo -
    down a voice line. Nobody can, and the farmer is left waiting."""
    found, outcome = _run(said, language="hi")
    assert found.action.id == "send_photo"
    assert outcome.data.get("possible") is False
    assert "app" in outcome.spoken.lower()


def test_describing_a_photo_is_still_a_question():
    """"photo mein patte pile dikh rahe hain" is a description of a problem."""
    assert not actions.detect("photo mein patte pile dikh rahe hain").found


# ============================================ a screen is not a telephone
def test_a_browser_is_told_the_page_opened_not_where_it_opened(client, fake_llm):
    """Agent mode runs the phone engine, because that is the one that answers
    in fourteen spoken words instead of a hundred written ones. But the farmer
    is looking at the screen, and "I have opened it on your Saarthi dashboard"
    is the call's sentence said in the wrong place."""
    body = client.post("/api/chat", json={
        "message": "mandi bhav dikhao", "language": "en",
        "conversational": True, "channel": "dashboard", "speak": False}).json()
    assert body["action"] == "open_page"
    assert body["ui"]["page"] == "market"
    assert "dashboard" not in body["reply"].lower(), body["reply"]


@pytest.mark.asyncio
async def test_a_call_is_still_told_where_it_opened():
    """The other half: a caller has no screen in their hand."""
    farmers.ensure_schema()
    commands.ensure_schema()
    turn = await voice_agent.respond(phone="9876500081", said="mandi bhav dikhao",
                                     call_id="surface-call", language="hi",
                                     channel="call")
    for task in turn.background:
        task.cancel()
    assert "dashboard" in turn.reply.lower()
