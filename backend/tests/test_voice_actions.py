"""Asking Saarthi to DO something, rather than to answer.

The rule this whole file exists to protect: **a question must never become an
action**. "gehu ka bhav kya hai" is a question for the agents; "gehu ka bhav
dikhao" opens the market page. Getting that wrong in one direction means the
farmer's screen jumps while they are reading; in the other it means the feature
does not work. The first is worse, so detection is by rule and never by a model
- a wrong match is a word somebody actually said, which can be explained and
fixed, where a model's guess cannot.
"""
from __future__ import annotations

import pytest

from app.conversation import actions
from app.core import accounts, commands, farmers


# ------------------------------------------------- an instruction, or a question
@pytest.mark.parametrize("said,expected", [
    ("mandi bhav dikhao", "open_page"),
    ("open my fields", "open_page"),
    ("mitti wala page kholo", "open_page"),
    ("मौसम दिखाओ", "open_page"),
    ("show me the weather", "open_page"),
    ("poori jaanch karo", "run_pipeline"),
    ("run the full check", "run_pipeline"),
    ("sensor se reading lo", "take_reading"),
    ("report banao", "make_report"),
    ("band karo", "stop"),
    ("stop", "stop"),
])
def test_an_instruction_is_recognised(said, expected):
    found = actions.detect(said)
    assert found.found, "%r produced no action" % said
    assert found.action.id == expected


@pytest.mark.parametrize("said", [
    "gehu ka bhav kya chal raha hai",
    "mera khet kaisa hai",
    "when should I irrigate my wheat",
    "mitti mein nami kitni hai",
    "aaj barish hogi kya",
    "kitni urea daalun",
    "namaste",
    # A verb with nothing recognisable after it. Opening something at random is
    # worse than answering the question.
    "dikhao",
    "kholo",
])
def test_a_question_is_left_to_the_agents(said):
    assert not actions.detect(said).found, "%r was hijacked into an action" % said


def test_the_longest_page_name_wins():
    """"farm support" is a page; "support" and "help" appear all over the
    vocabulary, and "mandi bhav" must not be read as the fields page."""
    assert actions.page_for("farm support kholo") == "help"
    assert actions.page_for("mandi bhav dikhao") == "market"
    assert actions.page_for("khet dikhao") == "fields"


def test_a_page_nobody_should_reach_by_voice_is_not_in_the_list():
    """The admin pages run the server. "open admin" from a farmer's phone should
    be a sentence Saarthi does not understand, not a redirect it refuses."""
    assert "admin" not in actions.PAGES
    assert "knowledge" not in actions.PAGES
    assert actions.page_for("admin panel kholo") is None


# ------------------------------------------------------------- carrying it out
def test_opening_a_page_produces_an_instruction_not_a_sentence():
    outcome = actions.run(actions.detect("mandi bhav dikhao"))
    assert outcome.ok
    assert outcome.ui == {"do": "navigate", "page": "market",
                          "label": "mandi prices"}
    assert "mandi prices" in outcome.spoken


def test_an_action_never_runs_a_model():
    """Actions are parsed and executed in code. If this ever needs a model, the
    latency budget and the failure modes both change completely."""
    import app.llm.mistral_client as mistral
    import app.llm.sarvam_client as sarvam

    calls = []
    mistral.set_transport(lambda url, payload: calls.append(url))
    sarvam.set_transport(lambda url, payload: calls.append(url))
    try:
        for said in ("mandi bhav dikhao", "poori jaanch karo", "band karo"):
            actions.run(actions.detect(said))
    finally:
        mistral.set_transport(None)
        sarvam.set_transport(None)
    assert calls == [], "an action reached a language model"


# -------------------------------------------------------------- yes, and no
@pytest.mark.parametrize("said", ["haan", "yes", "ji haan", "kar do", "ok",
                                  "हाँ", "theek hai"])
def test_a_clear_yes_is_a_yes(said):
    assert actions.is_yes(said)


@pytest.mark.parametrize("said", ["nahi", "no", "rehne do", "cancel", "mat karo",
                                  "नहीं"])
def test_a_no_is_a_no(said):
    assert actions.is_no(said)
    assert not actions.is_yes(said)


def test_theek_hai_chhodo_is_not_consent():
    """It starts with a yes word and ends with a refusal. The refusal wins, or a
    farmer saying "fine, leave it" would have confirmed whatever was pending."""
    assert not actions.is_yes("theek hai chhodo")
    assert actions.is_no("theek hai chhodo")


def test_silence_is_not_consent():
    assert not actions.is_yes("")
    assert not actions.is_yes("   ")


# -------------------------------------------------------------- the queue
@pytest.fixture
def queue(client):
    commands.ensure_schema()
    return "9876500055"


def test_a_command_is_delivered_once(queue):
    """Two open tabs must not both navigate, or the page jumps twice."""
    commands.queue(queue, {"do": "navigate", "page": "soil"})
    first = commands.take(queue)
    second = commands.take(queue)
    assert len(first) == 1 and first[0]["page"] == "soil"
    assert second == [], "the same command was handed out twice"


def test_a_command_expires_rather_than_haunting_the_next_visit(queue, monkeypatch):
    """"Open the sensor page" arriving an hour after the call is not help."""
    from datetime import datetime, timedelta, timezone

    commands.queue(queue, {"do": "navigate", "page": "soil"})
    later = datetime.now(timezone.utc) + timedelta(seconds=commands.TTL_SECONDS + 5)
    monkeypatch.setattr(commands, "_now", lambda: later)
    assert commands.take(queue) == []


def test_one_account_cannot_be_sent_another_accounts_command(queue):
    commands.queue(queue, {"do": "navigate", "page": "soil"})
    assert commands.take("9999999999") == []
    assert len(commands.take(queue)) == 1


def test_stopping_clears_what_was_waiting(queue):
    commands.queue(queue, {"do": "navigate", "page": "soil"})
    commands.queue(queue, {"do": "navigate", "page": "weather"})
    assert commands.pending_count(queue) == 2
    commands.clear(queue)
    assert commands.pending_count(queue) == 0
    assert commands.take(queue) == []


# ---------------------------------------------------------------- the API
def _signed_in(client, sent_codes):
    from app.core import captcha as _captcha
    accounts.create_user(email="voice@example.com", phone="9876500056",
                         password="voice-pass-11", role="user", status="active")
    issued = client.get("/api/auth/captcha").json()
    words = _captcha.WORDS
    numbers = [words.index(w) for w in issued["question"].lower()
               .replace("?", "").split() if w in words]
    client.post("/api/auth/login", json={
        "identifier": "voice@example.com", "password": "voice-pass-11",
        "challenge_id": issued["challenge_id"], "captcha": str(sum(numbers))})
    done = client.post("/api/auth/login-verify",
                       json={"identifier": "voice@example.com",
                             "code": sent_codes[-1]["code"]})
    assert done.status_code == 200, done.text
    return {"Authorization": "Bearer " + done.json()["token"]}


@pytest.fixture
def sent_codes(monkeypatch):
    from app.core.config import get_settings
    from app.telephony import otp_client
    sent = []

    def poster(url, payload, headers):
        sent.append(dict(payload))
        return {"ok": True, "channel": payload.get("channel")}

    get_settings().values["N8N_BASE_URL"] = "https://n8n.example"
    get_settings().values["N8N_OTP_PATH"] = "/webhook/saarthi-otp"
    otp_client.set_poster(poster)
    yield sent
    otp_client.set_poster(None)


def test_the_command_routes_need_an_account(client):
    """The telephony token belongs to n8n, which has no business opening a
    farmer's dashboard."""
    assert client.get("/api/voice/commands").status_code in (401, 403)
    assert client.get("/api/voice/actions").status_code in (401, 403)
    assert client.post("/api/voice/command",
                       json={"ui": {"do": "navigate", "page": "soil"}}
                       ).status_code in (401, 403)


def test_a_signed_in_farmer_can_queue_and_collect(client, sent_codes):
    headers = _signed_in(client, sent_codes)
    pushed = client.post("/api/voice/command", headers=headers, json={
        "ui": {"do": "navigate", "page": "market", "label": "mandi prices"},
        "spoken": "Opening mandi prices."})
    assert pushed.status_code == 200, pushed.text

    waiting = client.get("/api/voice/commands", headers=headers).json()["commands"]
    assert len(waiting) == 1
    assert waiting[0]["do"] == "navigate" and waiting[0]["page"] == "market"
    # and only once
    assert client.get("/api/voice/commands",
                      headers=headers).json()["commands"] == []


@pytest.mark.parametrize("instruction", [
    {"do": "navigate", "page": "admin"},
    {"do": "navigate", "page": "../../etc/passwd"},
    {"do": "eval", "code": "alert(1)"},
    {"do": "navigate"},
    {"nothing": True},
])
def test_an_instruction_the_server_did_not_invent_is_refused(client, sent_codes,
                                                             instruction):
    """A browser is going to act on whatever comes back from this route, so it
    returns only the instructions this server knows how to produce."""
    headers = _signed_in(client, sent_codes)
    response = client.post("/api/voice/command", headers=headers,
                           json={"ui": instruction})
    assert response.status_code == 400, response.text


def test_the_help_a_farmer_reads_comes_from_the_parser(client, sent_codes):
    """So the "you can say" list cannot drift from the phrases that work."""
    headers = _signed_in(client, sent_codes)
    body = client.get("/api/voice/actions", headers=headers).json()
    ids = {a["id"] for a in body["actions"]}
    assert {"open_page", "run_pipeline", "stop"} <= ids
    for entry in body["actions"]:
        assert entry["summary"] and entry["risk"] in ("read", "write", "confirm")
    # every example in the help must actually be recognised
    for entry in body["actions"]:
        for example in entry["examples"]:
            if entry["id"] in ("record_expense", "book_call"):
                continue              # handled by the ledger and task parsers
            found = actions.detect(example)
            assert found.found and found.action.id == entry["id"], \
                "the help says %r works, and it does not" % example


# ------------------------------------------------------- the spoken turn path
@pytest.mark.asyncio
async def test_a_spoken_instruction_is_carried_out_without_a_model(monkeypatch):
    """4 to 19 ms measured, because nothing is generated - the words are fixed."""
    from app.conversation.voice_agent import voice_agent
    import app.llm.mistral_client as mistral
    import app.llm.sarvam_client as sarvam

    farmers.ensure_schema()
    commands.ensure_schema()
    reached = []
    mistral.set_transport(lambda url, payload: reached.append("mistral"))
    sarvam.set_transport(lambda url, payload: reached.append("sarvam"))
    try:
        turn = await voice_agent.respond(phone="9876500057",
                                         said="mandi bhav dikhao",
                                         call_id="action-call", language="hi")
    finally:
        mistral.set_transport(None)
        sarvam.set_transport(None)
    for task in turn.background:
        task.cancel()

    assert turn.source == "action"
    assert turn.action == "open_page"
    assert turn.ui == {"do": "navigate", "page": "market", "label": "mandi prices"}
    assert reached == [], "an instruction reached a language model"


@pytest.mark.asyncio
async def test_the_instruction_is_also_left_for_another_screen(monkeypatch):
    """A farmer on the phone with the dashboard open on a laptop: the surface
    that should act is not the one that was spoken to."""
    from app.conversation.voice_agent import voice_agent

    farmers.ensure_schema()
    commands.ensure_schema()
    phone = "9876500058"
    turn = await voice_agent.respond(phone=phone, said="mitti wala page kholo",
                                     call_id="action-call-2", language="hi")
    for task in turn.background:
        task.cancel()
    waiting = commands.take(farmers.normalise_phone(phone))
    assert waiting and waiting[0]["page"] == "soil"

# ------------------------------------------------- the dashboard obeys them too
def test_typing_an_instruction_into_the_dashboard_works(client, fake_llm):
    """It used to work only on the phone.

    The action parser lived inside the voice agent, and the dashboard went
    straight to the fifteen-agent chain - so "mandi bhav dikhao" typed into the
    assistant produced a full price advisory instead of opening the page.
    """
    body = client.post("/api/chat", json={"message": "mandi bhav dikhao",
                                          "language": "hi"}).json()
    assert body["action"] == "open_page"
    assert body["ui"] == {"do": "navigate", "page": "market",
                          "label": "mandi prices"}
    assert body["advisory"] is None, "an instruction ran the whole chain"
    assert "mandi prices" in body["reply"]


def test_a_question_typed_into_the_dashboard_still_runs_the_chain(client, fake_llm):
    body = client.post("/api/chat", json={"message": "what is my soil like",
                                          "language": "en"}).json()
    assert not body["action"]
    assert body["ui"] is None
    assert body["advisory"] is not None


def test_the_streamed_endpoint_carries_the_instruction(client, fake_llm):
    import json as _json

    response = client.post("/api/chat/stream",
                           json={"message": "poori jaanch karo", "language": "hi"})
    assert response.status_code == 200
    events = []
    for frame in response.text.split("\n\n"):
        line = next((l for l in frame.split("\n") if l.startswith("data:")), None)
        if line:
            events.append(_json.loads(line[5:].strip()))
    assert any(e.get("ui", {}).get("do") == "run_pipeline" for e in events)
    # No advisory: the chain was not run, which is the point.
    assert not any("advisory" in e for e in events)


def test_an_instruction_is_not_queued_back_to_the_surface_that_asked(client,
                                                                    sent_codes):
    """The bug this caused: the dashboard navigated inline, then collected its
    own queued copy a second later and navigated again - so a question asked
    straight after an instruction was dragged back to the previous page."""
    headers = _signed_in(client, sent_codes)
    phone = farmers.normalise_phone("9876500056")
    commands.clear(phone)

    body = client.post("/api/chat", headers=headers,
                       json={"message": "mandi bhav dikhao", "language": "hi"}).json()
    assert body["ui"]["page"] == "market"       # answered inline
    assert commands.pending_count(phone) == 0, "it queued a copy to itself"


def test_a_spoken_instruction_from_a_screen_is_not_queued_back_either(client,
                                                                     sent_codes):
    """The same rule, on the path the farmer actually talks to.

    The plain endpoint had learnt not to queue a copy to the surface that asked.
    The conversational one had not - and that is the one agent mode in the
    browser and the microphone in the app both use. So every spoken instruction
    opened the page, and then a second or two later the poller collected the
    queued copy and opened it again, dragging the farmer back from wherever they
    had gone next.
    """
    headers = _signed_in(client, sent_codes)
    phone = farmers.normalise_phone("9876500056")
    commands.clear(phone)

    body = client.post("/api/chat", headers=headers,
                       json={"message": "mandi bhav dikhao", "language": "hi",
                             "conversational": True, "channel": "app"}).json()

    assert body["ui"]["page"] == "market", body
    assert body["action"] == "open_page"
    assert commands.pending_count(phone) == 0, "it queued a copy to itself"


@pytest.mark.asyncio
async def test_a_caller_still_gets_the_instruction_written_down():
    """And the case the queue exists for is untouched: somebody on a phone has
    no screen of their own, so it has to wait for one."""
    from app.conversation.voice_agent import voice_agent

    farmers.ensure_schema()
    commands.ensure_schema()
    phone = "9876500059"
    commands.clear(farmers.normalise_phone(phone))

    turn = await voice_agent.respond(phone=phone, said="mandi bhav dikhao",
                                     call_id="action-call-3", language="hi",
                                     channel="call")
    for task in turn.background:
        task.cancel()

    waiting = commands.take(farmers.normalise_phone(phone))
    assert waiting and waiting[0]["page"] == "market"


@pytest.mark.asyncio
async def test_the_app_is_told_it_opened_not_that_somebody_else_did():
    """A screen that asked is looking at the page. "I have opened it on your
    Saarthi dashboard" is the phone line's sentence, said in the wrong place."""
    from app.conversation.voice_agent import voice_agent

    farmers.ensure_schema()
    on_screen = await voice_agent.respond(
        phone="9876500060", said="mandi bhav dikhao", call_id="action-app-1",
        language="en", channel="app")
    on_phone = await voice_agent.respond(
        phone="9876500061", said="mandi bhav dikhao", call_id="action-call-4",
        language="en", channel="call")
    for turn in (on_screen, on_phone):
        for task in turn.background:
            task.cancel()

    assert on_screen.reply != on_phone.reply
    assert "dashboard" not in on_screen.reply.lower(), on_screen.reply
    assert "dashboard" in on_phone.reply.lower(), on_phone.reply
