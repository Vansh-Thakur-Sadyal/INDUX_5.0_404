"""Call-backs the farmer books: parsing, timing, and what gets said at fire time.

The guarantees worth protecting here are about trust rather than correctness in the
abstract. A farmer who answers a booked call has no screen to check it against, so the
tests below lock down the three ways this feature could quietly lie to them: giving a
stale number, inventing one when the lookup failed, and promising a call that was
never actually booked.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core import tasks
from app.llm import sarvam_client
from app.services import task_runner


@pytest.fixture
def scripted_sarvam(monkeypatch):
    """A Sarvam that answers without a network call.

    The booking under test is made by our own code before the model is consulted, so
    what it replies does not matter here - only that it replies at all.
    """
    def install(reply="Haan ji, theek hai."):
        def transport(url, payload):
            system = payload["messages"][0].get("content") or ""
            if "extract ONLY durable facts" in system:
                content = json.dumps({"facts": []})
            elif "Summarise this farm helpline call" in system:
                content = "Call summary."
            else:
                content = reply
            return {"model": payload["model"],
                    "choices": [{"message": {"role": "assistant",
                                             "content": content}}]}

        monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key")
        sarvam_client.set_transport(transport)

    yield install
    sarvam_client.set_transport(None)

IST = tasks.IST

# A fixed afternoon, so "this evening" and quiet hours are not whatever today is.
NOON = datetime(2026, 10, 19, 6, 30, tzinfo=timezone.utc)        # 12:00 IST
LATE = datetime(2026, 10, 19, 17, 30, tzinfo=timezone.utc)       # 23:00 IST


# ------------------------------------------------------------------ parsing

@pytest.mark.parametrize("said, minutes", [
    ("tell me wheat price after 30 minutes", 30),
    ("call me in 30 min", 30),
    ("gehu ka bhav 30 minute baad batana", 30),
    ("remind me after 2 hours to spray", 120),
    ("call me in 1 hour", 60),
    ("half an hour baad call karna", 30),
    ("ek ghante baad batana", 60),
])
def test_a_spoken_delay_becomes_the_right_moment(said, minutes):
    parsed = tasks.parse_when(said, NOON)
    assert parsed is not None, said
    due, _phrase = parsed
    assert due == NOON + timedelta(minutes=minutes)


def test_devanagari_delays_are_understood():
    """The phone line transcribes spoken Hindi into Devanagari, so this is the form
    most bookings actually arrive in - romanised Hinglish is the dashboard case."""
    parsed = tasks.parse_when("मुझे 1 घंटे बाद पानी देने की याद दिलाना", NOON)
    assert parsed is not None
    assert parsed[0] == NOON + timedelta(hours=1)


def test_an_unclear_time_is_refused_rather_than_guessed():
    """A wrong hour is worse than one more question: the farmer either misses the
    call or is woken by it, and either way stops using the feature."""
    for said in ("call me later", "call me sometime", "ring me when you can", ""):
        assert tasks.parse_when(said, NOON) is None


def test_an_absurd_delay_is_refused():
    """Almost always a misheard number. Booking it would hide the mishearing for
    a month."""
    assert tasks.parse_when("call me in 90000 minutes", NOON) is None
    assert tasks.parse_when("call me in 0 minutes", NOON) is None


def test_a_clock_time_in_the_past_moves_to_tomorrow():
    parsed = tasks.parse_when("call me at 9 am", NOON)          # 12:00 IST already
    assert parsed is not None
    assert parsed[0].astimezone(IST).hour == 9
    assert parsed[0] > NOON


def test_evening_hours_are_read_as_evening():
    """"6 baje" from a farmer at noon means this evening, not six in the morning."""
    parsed = tasks.parse_when("shaam 6 baje call karna", NOON)
    assert parsed is not None
    assert parsed[0].astimezone(IST).hour == 18


# ------------------------------------------------------------------- watches

def test_a_price_watch_is_recognised_without_the_word_price():
    """How a farmer actually says it. "Price" is obvious to them, so they omit it."""
    watch = tasks.parse_watch("call me when wheat goes above 2400")
    assert watch == {"watch_type": "price_above", "subject": "wheat",
                     "threshold": 2400.0}


def test_a_falling_price_watch_is_not_read_as_a_rising_one():
    watch = tasks.parse_watch("tell me if tomato falls below 800")
    assert watch["watch_type"] == "price_below"
    assert watch["subject"] == "tomato"


def test_a_rain_watch_needs_a_condition_word():
    """"It will rain tomorrow" is a statement, not a request to be telephoned."""
    assert tasks.parse_watch("agar barish aaye to batana") is not None
    assert tasks.parse_watch("barish kab hogi") is None


def test_a_plain_delay_is_not_mistaken_for_a_price_watch():
    """The number in "call me in 2400 minutes" is a delay. Reading it as a price
    threshold would book a watch that never fires instead of a call in two days."""
    assert tasks.parse_watch("call me in 2400 minutes") is None


def test_a_watch_beats_the_clock_when_both_could_match(client, account):
    """"when wheat crosses 2400" has a number the time parser would happily take."""
    outcome = tasks.book_from_speech(account.phone,
                                     "call me when wheat crosses 2400", now=NOON)
    assert outcome["booked"]
    assert outcome["task"]["kind"] == "watch"
    assert outcome["task"]["threshold"] == 2400.0


# -------------------------------------------------------------- quiet hours

def test_a_time_the_farmer_chose_is_honoured_even_at_night(client, account):
    """Booked at 23:00 for "in two hours", and it rings at 1am.

    The earlier rule pushed this to the morning, which sounded protective and was
    not: the farmer is awake at 23:00, they know what time it will be in two hours,
    and night irrigation is routine here because pump electricity is often supplied
    at night. A reminder moved to 07:00 is a reminder that arrives after the water
    has been and gone.
    """
    outcome = tasks.book_from_speech(account.phone, "remind me in 2 hours", now=LATE)
    assert outcome["booked"]
    due = tasks._parse_dt(outcome["task"]["due_at"])
    assert due == LATE + timedelta(hours=2)


def test_the_confirmation_names_the_time_it_actually_booked(client, account):
    """The farmer's only check on the parser. If it heard the wrong number, this is
    where they catch it - not when the phone fails to ring."""
    outcome = tasks.book_from_speech(account.phone, "remind me in 2 hours", now=NOON)
    due = tasks._parse_dt(outcome["task"]["due_at"]).astimezone(IST)
    assert due.strftime("%H:%M") in outcome["reply"]


def test_a_daytime_booking_is_left_alone(client, account):
    outcome = tasks.book_from_speech(account.phone, "remind me in 2 hours", now=NOON)
    due = tasks._parse_dt(outcome["task"]["due_at"])
    assert due == NOON + timedelta(hours=2)


# ---------------------------------------------------------------- the store

def test_a_booking_is_written_down_before_anything_is_promised(client, account):
    """The spoken confirmation is only true because of the row behind it."""
    outcome = tasks.book_from_speech(account.phone,
                                     "remind me in 30 minutes to irrigate", now=NOON)
    assert outcome["booked"]
    stored = tasks.get(outcome["task"]["task_id"])
    assert stored is not None
    assert stored["status"] == "pending"
    assert stored["kind"] == "reminder"


def test_a_question_is_stored_as_a_question_not_a_reminder(client, account):
    """The difference decides whether anything is looked up at fire time."""
    outcome = tasks.book_from_speech(account.phone,
                                     "tell me the wheat price in 30 minutes", now=NOON)
    assert outcome["task"]["kind"] == "question"
    assert outcome["task"]["subject"] == "price"


def test_an_unparseable_booking_books_nothing(client, account):
    """It must not half-succeed: no row, and a reply that asks for the missing bit."""
    before = tasks.pending_count(account.phone)
    outcome = tasks.book_from_speech(account.phone, "call me later", now=NOON)
    assert not outcome["booked"]
    assert "when" in outcome["reply"].lower()
    assert tasks.pending_count(account.phone) == before


def test_cancelling_keeps_the_row(client, account):
    """What the farmer asked for is part of the record even when it is called off."""
    outcome = tasks.book_from_speech(account.phone, "remind me in 1 hour", now=NOON)
    task_id = outcome["task"]["task_id"]
    assert tasks.cancel(task_id, account.phone) is True
    assert tasks.get(task_id)["status"] == "cancelled"
    # And it cannot be cancelled twice into a different state.
    assert tasks.cancel(task_id, account.phone) is False


def test_one_farmer_cannot_cancel_another_ones_call(client, account):
    outcome = tasks.book_from_speech(account.phone, "remind me in 1 hour", now=NOON)
    assert tasks.cancel(outcome["task"]["task_id"], "9999999999") is False


def test_only_tasks_whose_time_has_come_are_due(client, account):
    tasks.book_from_speech(account.phone, "remind me in 5 minutes", now=NOON)
    tasks.book_from_speech(account.phone, "remind me in 5 hours", now=NOON)
    due = tasks.due_now(NOON + timedelta(minutes=10))
    assert len(due) == 1


# ------------------------------------------------------- what gets said later

@pytest.mark.asyncio
async def test_a_reminder_says_the_farmers_own_words_back(client, account):
    outcome = tasks.book_from_speech(
        account.phone, "remind me in 1 hour to give water to my field", now=NOON)
    spoken = await task_runner.resolve(tasks.get(outcome["task"]["task_id"]))
    assert "water" in spoken.lower()


@pytest.mark.asyncio
async def test_a_failed_lookup_says_so_instead_of_inventing_a_price(client, account,
                                                                    monkeypatch):
    """The single most important test here.

    Nobody is watching when this call is placed, and a farmer who hears a number on
    the phone will sell on it. An unavailable market service must produce a sentence
    that says the price could not be fetched - never the last price we happened to
    have, and never a plausible-sounding figure.
    """
    async def unavailable(*args, **kwargs):
        return {"available": False, "reason": "Market service unavailable"}

    monkeypatch.setattr(task_runner.market_service, "get_summary", unavailable)
    outcome = tasks.book_from_speech(
        account.phone, "tell me the wheat price in 30 minutes", now=NOON)
    spoken = await task_runner.resolve(tasks.get(outcome["task"]["task_id"]))

    assert "could not" in spoken.lower()
    assert not any(ch.isdigit() for ch in spoken), spoken


@pytest.mark.asyncio
async def test_a_price_is_fetched_when_the_call_fires_not_when_it_is_booked(
        client, account, monkeypatch):
    """The whole point of the feature.

    Resolving at booking time would hand the farmer a half-hour-old number while
    sounding completely certain about it.
    """
    prices = {"value": 2000}

    async def moving_market(*args, **kwargs):
        return {"available": True, "commodity": "wheat", "scope": "district",
                "median_modal": prices["value"], "record_count": 12, "highest": {}}

    monkeypatch.setattr(task_runner.market_service, "get_summary", moving_market)
    outcome = tasks.book_from_speech(
        account.phone, "tell me the wheat price in 30 minutes", now=NOON)

    # The market moves between the booking and the call.
    prices["value"] = 2450
    spoken = await task_runner.resolve(tasks.get(outcome["task"]["task_id"]))
    assert "2450" in spoken
    assert "2000" not in spoken


@pytest.mark.asyncio
async def test_a_watch_does_not_fire_when_the_lookup_fails(client, account,
                                                           monkeypatch):
    """A watch that fires on an outage is a false alarm, and false alarms are how a
    farmer learns to ignore the phone."""
    async def unavailable(*args, **kwargs):
        return {"available": False, "reason": "down"}

    monkeypatch.setattr(task_runner.market_service, "get_summary", unavailable)
    outcome = tasks.book_from_speech(account.phone,
                                     "call me when wheat goes above 2400", now=NOON)
    fired, spoken = await task_runner.check_watch(tasks.get(outcome["task"]["task_id"]))
    assert fired is False
    assert spoken == ""


@pytest.mark.asyncio
async def test_a_price_watch_fires_only_once_the_threshold_is_passed(
        client, account, monkeypatch):
    level = {"value": 2300}

    async def market(*args, **kwargs):
        return {"available": True, "commodity": "wheat", "scope": "district",
                "median_modal": level["value"], "record_count": 9, "highest": {}}

    monkeypatch.setattr(task_runner.market_service, "get_summary", market)
    outcome = tasks.book_from_speech(account.phone,
                                     "call me when wheat goes above 2400", now=NOON)
    task = tasks.get(outcome["task"]["task_id"])

    fired, _ = await task_runner.check_watch(task)
    assert fired is False

    level["value"] = 2460
    fired, spoken = await task_runner.check_watch(task)
    assert fired is True
    assert "2460" in spoken


@pytest.mark.asyncio
async def test_an_expired_watch_tells_the_farmer_it_stopped(client, account,
                                                            monkeypatch):
    """A watch that quietly stops is a promise the farmer still believes is running."""
    dialled = []

    async def never(*args, **kwargs):
        return {"available": True, "commodity": "wheat", "scope": "district",
                "median_modal": 1800, "record_count": 4, "highest": {}}

    async def capture(task, spoken):
        dialled.append(spoken)
        tasks.mark(task["task_id"], "done", spoken=spoken)
        return True

    monkeypatch.setattr(task_runner.market_service, "get_summary", never)
    monkeypatch.setattr(task_runner, "_dial", capture)

    outcome = tasks.book_from_speech(account.phone,
                                     "call me when wheat goes above 2400", now=NOON)
    later = NOON + timedelta(days=tasks.WATCH_DAYS + 1)
    await task_runner.run_due(later)

    assert dialled, "an expiring watch must say so"
    assert "stopping" in dialled[0].lower()


@pytest.mark.asyncio
async def test_a_task_that_cannot_be_dialled_stays_pending(client, account,
                                                           monkeypatch):
    """The line being busy is not a reason to drop a promise on the floor."""
    async def refused(task, spoken):
        return False

    monkeypatch.setattr(task_runner, "_dial", refused)
    outcome = tasks.book_from_speech(account.phone, "remind me in 5 minutes",
                                     now=NOON)
    await task_runner.run_due(NOON + timedelta(minutes=10))
    assert tasks.get(outcome["task"]["task_id"])["status"] == "pending"


@pytest.mark.asyncio
async def test_a_reminder_the_farmer_timed_rings_at_night(client, account,
                                                           monkeypatch):
    """They asked for this hour. Holding it would be the software overruling them."""
    dialled = []

    async def capture(task, spoken):
        dialled.append(spoken)
        return True

    monkeypatch.setattr(task_runner, "_dial", capture)
    tasks.create(account.phone, "move the pump pipe", "reminder", LATE)
    result = await task_runner.run_due(LATE)

    assert len(dialled) == 1
    assert result["fired"] == 1


@pytest.mark.asyncio
async def test_a_watch_does_not_telephone_in_the_night(client, account, monkeypatch):
    """Nobody chose this moment - the market did - so it waits for a civil hour.

    The condition is still true in the morning, and a farmer woken at 3am about the
    price of wheat is a farmer who turns the whole feature off.
    """
    dialled = []

    async def over_threshold(*args, **kwargs):
        return {"available": True, "commodity": "wheat", "scope": "district",
                "median_modal": 2600, "record_count": 8, "highest": {}}

    async def capture(task, spoken):
        dialled.append(spoken)
        return True

    monkeypatch.setattr(task_runner.market_service, "get_summary", over_threshold)
    monkeypatch.setattr(task_runner, "_dial", capture)
    tasks.book_from_speech(account.phone, "call me when wheat goes above 2400",
                           now=LATE)

    night = await task_runner.run_due(LATE)
    assert dialled == []
    assert night["held_for_morning"] == 1

    # Morning comes and the same watch goes through.
    morning = LATE + timedelta(hours=9)
    await task_runner.run_due(morning)
    assert len(dialled) == 1
    assert "2600" in dialled[0]


# ------------------------------------------------------------------- the API

def test_the_dashboard_books_in_plain_language(client, account):
    """The same parser the phone uses, so a phrase that works in one works in both."""
    response = client.post("/api/tasks",
                           json={"said": "tell me the wheat price in 45 minutes"},
                           headers=account.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["booked"] is True
    assert body["task"]["kind"] == "question"
    assert body["task"]["due_label"]


def test_the_api_refuses_a_booking_it_could_not_understand(client, account):
    response = client.post("/api/tasks", json={"said": "call me later"},
                           headers=account.headers)
    assert response.status_code == 400
    assert "when" in response.json()["detail"].lower()


def test_the_listing_never_returns_the_phone_number(client, account):
    client.post("/api/tasks", json={"said": "remind me in 1 hour"},
                headers=account.headers)
    body = client.get("/api/tasks", headers=account.headers).json()
    assert body["tasks"]
    assert all("phone" not in row for row in body["tasks"])


def test_a_signed_out_visitor_cannot_see_or_book_call_backs(client):
    assert client.get("/api/tasks").status_code in (401, 403)
    assert client.post("/api/tasks", json={"said": "remind me in 1 hour"}
                       ).status_code in (401, 403)


def test_cancelling_through_the_api(client, account):
    booked = client.post("/api/tasks", json={"said": "remind me in 1 hour"},
                         headers=account.headers).json()
    task_id = booked["task"]["task_id"]
    assert client.delete("/api/tasks/" + task_id,
                         headers=account.headers).status_code == 200
    assert client.delete("/api/tasks/" + task_id,
                         headers=account.headers).status_code == 404


# ------------------------------------------------------------- from the phone

@pytest.mark.asyncio
async def test_saying_it_on_the_phone_books_it(client, account, scripted_sarvam):
    """End to end from spoken words: router, voice agent, stored row.

    The booking is written by code, not decided by the model. A model asked to
    "remember" something has no memory between calls, so a confirmation it invented
    would leave the farmer waiting for a call that was never scheduled.
    """
    from app.conversation.voice_agent import voice_agent

    scripted_sarvam()
    turn = await voice_agent.respond(
        phone=account.phone, said="gehu ka bhav 30 minute baad batana",
        call_id="phone-task-1", language="hi")
    for background in turn.background:
        background.cancel()

    assert turn.intent == "task"
    booked = tasks.list_for(account.phone, include_finished=False)
    assert len(booked) == 1
    assert booked[0]["kind"] == "question"
    assert booked[0]["subject"] == "price"
    # And the farmer is told, in the same turn, that it was booked.
    assert any("call you" in n.lower() for n in turn.notices), turn.notices


@pytest.mark.asyncio
async def test_a_vague_request_on_the_phone_books_nothing_and_asks(client, account,
                                                                   scripted_sarvam):
    """A half-booking is the worst outcome: the farmer believes a call is coming."""
    from app.conversation.voice_agent import voice_agent

    scripted_sarvam()
    turn = await voice_agent.respond(phone=account.phone, said="call me later",
                                     call_id="phone-task-2", language="hi")
    for background in turn.background:
        background.cancel()

    assert tasks.list_for(account.phone, include_finished=False) == []
    assert any("when" in n.lower() for n in turn.notices), turn.notices


@pytest.mark.asyncio
async def test_a_task_that_can_never_be_dialled_is_eventually_given_up_on(
        client, account, monkeypatch):
    """A busy line is worth waiting through; a broken telephony setup is not.

    The sweep runs every twenty seconds, so a task that stays pending on a
    permanently failing dial would retry about four thousand times a day and the
    farmer would never find out the call was not coming. It has to reach a state the
    dashboard can show.
    """
    async def always_refused(request):
        return {"accepted": False, "code": "telephony_failed",
                "reason": "Telephony did not accept the call", "call": None}

    monkeypatch.setattr(task_runner.scheduler, "request_call", always_refused)
    outcome = tasks.book_from_speech(account.phone, "remind me in 5 minutes",
                                     now=NOON)
    task_id = outcome["task"]["task_id"]
    later = NOON + timedelta(minutes=10)

    for _ in range(tasks.MAX_DIAL_ATTEMPTS - 1):
        await task_runner.run_due(later)
    assert tasks.get(task_id)["status"] == "pending", "gave up too early"

    await task_runner.run_due(later)
    final = tasks.get(task_id)
    assert final["status"] == "failed"
    assert "could not place" in (final["spoken"] or "").lower()


@pytest.mark.asyncio
async def test_a_dial_that_succeeds_resets_nothing_and_completes(client, account,
                                                                  monkeypatch):
    """The ordinary path: one attempt, one call, done."""
    placed = []

    async def accepted(request):
        placed.append(request.topic)
        return {"accepted": True, "call": {"call_id": "call-test-1"}}

    monkeypatch.setattr(task_runner.scheduler, "request_call", accepted)
    outcome = tasks.book_from_speech(
        account.phone, "remind me in 5 minutes to move the pipe", now=NOON)
    await task_runner.run_due(NOON + timedelta(minutes=10))

    stored = tasks.get(outcome["task"]["task_id"])
    assert stored["status"] == "done"
    assert stored["call_id"] == "call-test-1"
    assert len(placed) == 1
    assert "pipe" in placed[0].lower()


# ---------------------------------------------------- a database from before
def test_a_table_made_before_a_column_existed_is_migrated(client):
    """Found by the call-coverage audit, as a 500 on booking a call-back.

    CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so
    "attempts" was simply absent on every database made before it was added -
    and the first booking failed with "table tasks has no column named
    attempts", at runtime, on a live call. Tests never caught it because they
    build a fresh schema every time.
    """
    from app.core.storage import cursor

    with cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS tasks")
        # The table exactly as an older version created it.
        cur.execute(
            "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, phone TEXT NOT NULL,"
            " kind TEXT NOT NULL, said TEXT, subject TEXT, watch_type TEXT,"
            " threshold REAL, language TEXT, due_at TEXT, status TEXT,"
            " spoken TEXT, call_id TEXT, created_at TEXT, fired_at TEXT)")

    tasks.ensure_schema()

    with cursor() as cur:
        cur.execute("PRAGMA table_info(tasks)")
        columns = {row["name"] for row in cur.fetchall()}
    for name in tasks.LATER_COLUMNS:
        assert name in columns, "%s was not added" % name

    # And a booking actually works afterwards, which is the thing that broke.
    outcome = tasks.book_from_speech("9876500090", "shaam ko paanch baje call karna",
                                     now=NOON)
    assert outcome["booked"] is True
