"""Jobs the farmer hands to the call bot: "ring me in an hour and tell me X".

The call-back scheduler already dials at a chosen moment. This is the other half:
remembering WHY the call was booked, and working out what to say at the moment it
rings rather than at the moment it was asked for.

That distinction is the whole feature. "Tell me the wheat price in half an hour" is
not a delayed playback of today's price - it is an instruction to go and look in half
an hour. Resolving it at booking time would hand the farmer a thirty-minute-old number
while sounding completely confident about it, which is the kind of quiet wrongness this
project tries hard to avoid. So a task stores the QUESTION, and the answer is fetched
when the phone rings.

Three kinds:

  reminder  say the farmer's own words back to them. No data, no cleverness, and
            nothing that can go stale: "give water to field 2".
  question  go and find something out at fire time, then say it. Prices and weather
            today, because those are the two the farm already has live sources for.
  watch     not a time at all - a condition. "Call me if rain is coming" or "call me
            when wheat passes 2400". Re-checked on every sweep until it happens or
            the watch expires.

The watch is the one worth having. A farmer who has to remember to ask is a farmer who
finds out after the rain; the whole point of having their number is that we can be the
one who remembers.

Rules it keeps:

  * Nothing is invented at fire time. If the price lookup fails, the call says the
    price could not be fetched. A farmer who is told a made-up number sells on it.
  * Nobody is rung in the middle of the night. A task that falls inside quiet hours
    waits for the morning, and the farmer is told that when they book it.
  * A watch that never triggers says so when it expires, instead of being silently
    forgotten. An unkept promise the farmer never hears about is worse than a refusal.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .storage import add_missing_columns, cursor

# India does not observe daylight saving, so a fixed offset is exact and saves a
# tzdata dependency. Everything is STORED in UTC; IST is only for reading and
# speaking wall-clock times, which is how a farmer says them.
IST = timezone(timedelta(hours=5, minutes=30))

KINDS = ("reminder", "question", "watch")
STATUSES = ("pending", "done", "cancelled", "expired", "failed")

# Quiet hours in IST, for calls the farmer did NOT choose the moment of - a watch
# that triggers overnight, or a watch expiring. A time they asked for explicitly is
# honoured whatever the hour: they are awake, and night irrigation is routine.
QUIET_START = time(21, 0)
QUIET_END = time(6, 30)
MORNING = time(7, 0)

# How long a condition is watched before it gives up and says so.
WATCH_DAYS = 7

# How many times a task may fail to be dialled before it is given up on. The sweep
# runs every twenty seconds, so this is roughly seven minutes of a busy line - long
# enough to ride out a call in progress, short enough that a permanently broken
# telephony setup does not retry for ever in silence.
MAX_DIAL_ATTEMPTS = 20

# Guard rails on a spoken delay. Under a minute is almost always a misheard number,
# and past a fortnight the farmer has forgotten they asked.
MIN_DELAY_MINUTES = 1
MAX_DELAY_MINUTES = 14 * 24 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id     TEXT PRIMARY KEY,
    phone       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    said        TEXT NOT NULL,
    subject     TEXT,
    watch_type  TEXT,
    threshold   REAL,
    language    TEXT NOT NULL,
    parcel_id   TEXT,
    due_at      TEXT NOT NULL,
    expires_at  TEXT,
    status      TEXT NOT NULL,
    spoken      TEXT,
    call_id     TEXT,
    source      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    fired_at    TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(status, due_at);
CREATE INDEX IF NOT EXISTS idx_tasks_phone ON tasks(phone, created_at DESC);
"""


# Columns added after the table first shipped. A database made before one of
# these existed is otherwise fine until the first booking, which fails with
# "table tasks has no column named attempts" - at runtime, on a live call.
LATER_COLUMNS = {
    "attempts": "INTEGER NOT NULL DEFAULT 0",
    "expires_at": "TEXT",
    "parcel_id": "TEXT",
    "source": "TEXT NOT NULL DEFAULT 'call'",
}


def ensure_schema() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)
    add_missing_columns("tasks", LATER_COLUMNS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ quiet hours

def in_quiet_hours(moment: datetime) -> bool:
    """Is this the middle of the night where the farmer is?"""
    local = moment.astimezone(IST).time()
    return local >= QUIET_START or local < QUIET_END


def hold_until_morning(moment: datetime) -> datetime:
    """Push a night-time call to the next morning, keeping the date right.

    A task booked at 11pm for "in two hours" should ring after breakfast, not at 1am.
    The farmer is told this when they book it, so the call is not a surprise.
    """
    if not in_quiet_hours(moment):
        return moment
    local = moment.astimezone(IST)
    morning = local.replace(hour=MORNING.hour, minute=MORNING.minute,
                            second=0, microsecond=0)
    # Before dawn: this morning. After the evening cut-off: tomorrow morning.
    if local.time() >= QUIET_START:
        morning += timedelta(days=1)
    return morning.astimezone(timezone.utc)


# --------------------------------------------------------------- time parsing

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "ek": 1, "do": 2, "two": 2, "teen": 3, "three": 3,
    "char": 4, "four": 4, "panch": 5, "five": 5, "chhe": 6, "six": 6,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6,
}

# "half an hour", "aadha ghanta", "आधा घंटा"
_HALF = r"(?:half\s+an?\s+hour|aadha\s+ghant[aey]|आधा\s*घंट[ाे])"

_MINUTE_UNIT = r"(?:minutes?|mins?|mint|minut|मिनट)"
_HOUR_UNIT = r"(?:hours?|hrs?|ghant[aey]|घंट[ाेा]|घंटे|घंटा)"
_DAY_UNIT = r"(?:days?|din|दिन)"


# The number words, longest first so a short one cannot win inside a longer one.
_WORD_ALTERNATION = "|".join(
    re.escape(w) for w in sorted(_NUMBER_WORDS, key=len, reverse=True))

# A unit has to finish on a word boundary. Without this "mins?" matched the
# "min" inside "remind", took "re" as the count, and the booking was refused.
_UNIT_END = r"(?![a-zऀ-ॿ])"


def _amount(token: str) -> Optional[float]:
    token = (token or "").strip().lower()
    if not token:
        return None
    if token.isdigit():
        return float(token)
    return float(_NUMBER_WORDS[token]) if token in _NUMBER_WORDS else None


def parse_when(said: str, now: Optional[datetime] = None) -> Optional[Tuple[datetime, str]]:
    """A moment the farmer asked for, and how to say it back. None if unclear.

    Narrow on purpose, in the same spirit as the spoken-money parser: a time this
    guesses wrong is a call that rings at the wrong hour, and a farmer who asked for
    "after one hour" and got a call next Tuesday stops using the feature. When nothing
    matches, the caller asks rather than assuming.
    """
    text = (said or "").lower().strip()
    if not text:
        return None
    now = now or _now()

    # ---- "half an hour"
    if re.search(_HALF, text):
        return now + timedelta(minutes=30), "in half an hour"

    # ---- "<n> minutes/hours/days", in either word order. Hindi puts the unit before
    # the marker ("30 minute baad"), English puts a preposition first ("in 30 min").
    for unit_pattern, minutes_each, noun in (
        (_MINUTE_UNIT, 1, "minute"),
        (_HOUR_UNIT, 60, "hour"),
        (_DAY_UNIT, 60 * 24, "day"),
    ):
        match = re.search(r"(?<![\d.])(\d{1,4}|" + _WORD_ALTERNATION
                          + r")\s*" + unit_pattern + _UNIT_END, text)
        if not match:
            continue
        count = _amount(match.group(1))
        if count is None:
            continue
        total = count * minutes_each
        if not MIN_DELAY_MINUTES <= total <= MAX_DELAY_MINUTES:
            return None
        plural = "" if count == 1 else "s"
        return (now + timedelta(minutes=total),
                "in %g %s%s" % (count, noun, plural))

    # ---- a clock time: "at 6 pm", "6 baje", "शाम 6 बजे"
    clock = re.search(r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*"
                      r"(am|pm|baje|बजे|o.?clock)", text)
    if clock:
        hour = int(clock.group(1))
        minute = int(clock.group(2) or 0)
        marker = clock.group(3)
        evening = any(w in text for w in ("pm", "shaam", "sham", "शाम", "raat", "रात",
                                          "evening", "night", "dopahar", "दोपहर"))
        if marker == "pm" or (evening and hour < 12):
            hour = hour % 12 + 12
        elif marker == "am" and hour == 12:
            hour = 0
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            return None
        local = now.astimezone(IST).replace(hour=hour, minute=minute,
                                            second=0, microsecond=0)
        if local <= now.astimezone(IST):
            local += timedelta(days=1)
        return local.astimezone(timezone.utc), "at %d:%02d" % (hour, minute)

    # ---- named parts of the day
    tomorrow = any(w in text for w in ("tomorrow", "kal ", "kal,", "कल"))
    for words, at_hour, label in (
        (("morning", "subah", "सुबह", "savere"), 7, "morning"),
        (("afternoon", "dopahar", "दोपहर"), 14, "afternoon"),
        (("evening", "shaam", "sham", "शाम"), 17, "evening"),
        (("tonight", "night", "raat", "रात"), 20, "evening"),
    ):
        if not any(w in text for w in words):
            continue
        local = now.astimezone(IST).replace(hour=at_hour, minute=0,
                                            second=0, microsecond=0)
        if tomorrow or local <= now.astimezone(IST):
            local += timedelta(days=1)
        when = "tomorrow %s" % label if local.date() != now.astimezone(IST).date() \
            else "this %s" % label
        return local.astimezone(timezone.utc), when

    return None


# ------------------------------------------------------------ what to find out

# Commodities a question or a watch can be about. Kept to what Agmarknet actually
# reports under these names, because a watch on a crop the source does not carry
# would wait for ever without ever saying why.
WATCHABLE_CROPS = ("wheat", "rice", "paddy", "maize", "mustard", "onion", "potato",
                   "tomato", "cotton", "chickpea", "gram", "lentil", "soybean",
                   "bajra", "jowar", "garlic", "barley")

_CROP_ALIASES = {
    "gehu": "wheat", "gehun": "wheat", "गेहू": "wheat", "गेहूं": "wheat",
    "dhan": "rice", "धान": "rice", "chawal": "rice", "चावल": "rice",
    "sarson": "mustard", "सरसों": "mustard", "सरसो": "mustard",
    "pyaz": "onion", "प्याज": "onion", "aloo": "potato", "आलू": "potato",
    "tamatar": "tomato", "टमाटर": "tomato", "makka": "maize", "मक्का": "maize",
    "chana": "chickpea", "चना": "chickpea", "kapas": "cotton", "कपास": "cotton",
    "lehsun": "garlic", "लहसुन": "garlic", "jau": "barley", "जौ": "barley",
}

_PRICE_WORDS = ("price", "rate", "bhav", "daam", "keemat", "mandi", "भाव", "दाम",
                "कीमत", "मंडी", "sell", "bech", "बेच")
_WEATHER_WORDS = ("weather", "rain", "mausam", "barish", "baarish", "मौसम", "बारिश",
                  "बरसात", "forecast", "paala", "frost", "पाला")


def find_crop(said: str) -> str:
    """The crop named in the sentence, canonical, or empty.

    Whole words only. As a substring match, "dhan" was found inside "dhaniya"
    and inside "धानिया" - so a farmer asking how to grow CORIANDER was answered
    about rice, and a price watch they set on coriander would have waited for
    ever on a crop they never named.
    """
    text = (said or "").lower()
    for alias, canonical in _CROP_ALIASES.items():
        if re.search(_WHOLE_WORD % re.escape(alias), text):
            return canonical
    for crop in WATCHABLE_CROPS:
        if re.search(_WHOLE_WORD % re.escape(crop), text):
            return "rice" if crop == "paddy" else crop
    return ""


# A word boundary that holds for Devanagari as well: Python's \b does, but only
# because these letters are word characters, and spelling it out keeps the two
# halves of `find_crop` matching the same way.
_WHOLE_WORD = r"(?<![\wऀ-ॿ])%s(?![\wऀ-ॿ])"


def classify_question(said: str) -> str:
    """What a question-task should go and look up: price, weather, or neither."""
    text = (said or "").lower()
    if any(w in text for w in _PRICE_WORDS):
        return "price"
    if any(w in text for w in _WEATHER_WORDS):
        return "weather"
    return ""


def parse_watch(said: str) -> Optional[Dict[str, Any]]:
    """A condition to wait for, rather than a time. None if there is not one.

    Only two, and both are backed by a live source the farm already uses. A watch we
    cannot actually evaluate would be a promise with nothing behind it.
    """
    text = (said or "").lower()

    # "when wheat crosses 2400", "agar gehu 2400 se upar jaye", and the commonest
    # form of all - "call me when wheat goes above 2400", which never says "price".
    threshold = re.search(r"(?<![\d.])(\d{3,6})(?![\d])", text)
    if threshold:
        above = any(w in text for w in ("above", "over", "cross", "more than",
                                        "upar", "ऊपर", "zyada", "ज्यादा",
                                        "se upar", "badh", "बढ़"))
        below = any(w in text for w in ("below", "under", "less than", "neeche",
                                        "नीचे", "kam ho", "कम", "gir", "गिर"))
        crop = find_crop(text)
        # A crop and a direction and a number is a price watch whether or not the
        # word "price" appears; nothing else about wheat is "above 2400".
        if crop and (above or below):
            return {"watch_type": "price_below" if below and not above
                    else "price_above",
                    "subject": crop, "threshold": float(threshold.group(1))}

    # "call me if it rains", "barish ho to batana"
    if any(w in text for w in ("rain", "barish", "baarish", "बारिश", "बरसात")):
        if any(w in text for w in ("if", "when", "agar", "अगर", "jab", "जब",
                                   "ho to", "hone", "होने", "aaye", "आए")):
            return {"watch_type": "rain", "subject": "", "threshold": 2.0}
    return None


# ------------------------------------------------------------------- the store

def create(phone: str, said: str, kind: str, due_at: datetime, *,
           language: str = "hi", subject: str = "", watch_type: str = "",
           threshold: Optional[float] = None, parcel_id: str = "",
           source: str = "call", now: Optional[datetime] = None) -> Dict[str, Any]:
    """Record a task. The caller has already decided when it is due.

    ``now`` is the moment the booking was made, and the only reason it is a
    parameter is that a watch expires a fixed number of days after it. Reading
    the wall clock here instead meant a task booked against one reference time
    got an expiry measured from another - the due date came from the caller and
    the expiry from `datetime.now()`. In production the two agree, so the only
    thing it broke was the test that moves the clock forward to prove a watch
    says so when it runs out. A promise that it expires in seven days should be
    seven days from when the promise was made, whoever is counting.
    """
    ensure_schema()
    if kind not in KINDS:
        raise ValueError("unknown task kind: %s" % kind)

    now = now or _now()
    expires = now + timedelta(days=WATCH_DAYS) if kind == "watch" else None
    row = {
        "task_id": "task-" + uuid.uuid4().hex[:12],
        "phone": phone,
        "kind": kind,
        "said": (said or "")[:400],
        "subject": subject,
        "watch_type": watch_type,
        "threshold": threshold,
        "language": language,
        "parcel_id": parcel_id,
        "due_at": due_at.isoformat(),
        "expires_at": expires.isoformat() if expires else None,
        "status": "pending",
        "spoken": None,
        "call_id": None,
        "source": source,
        "created_at": now.isoformat(),
        "fired_at": None,
        "attempts": 0,
    }
    with cursor() as cur:
        cur.execute(
            "INSERT INTO tasks (task_id, phone, kind, said, subject, watch_type,"
            " threshold, language, parcel_id, due_at, expires_at, status, spoken,"
            " call_id, source, created_at, fired_at, attempts)"
            " VALUES (:task_id, :phone, :kind, :said, :subject, :watch_type,"
            " :threshold, :language, :parcel_id, :due_at, :expires_at, :status,"
            " :spoken, :call_id, :source, :created_at, :fired_at, :attempts)", row)
    return row


def _row(record: Any) -> Dict[str, Any]:
    return {k: record[k] for k in record.keys()}


def list_for(phone: str, limit: int = 40,
             include_finished: bool = True) -> List[Dict[str, Any]]:
    ensure_schema()
    clause = "" if include_finished else " AND status = 'pending'"
    with cursor() as cur:
        cur.execute("SELECT * FROM tasks WHERE phone = ?" + clause
                    + " ORDER BY created_at DESC LIMIT ?", (phone, limit))
        return [_row(r) for r in cur.fetchall()]


def get(task_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        found = cur.fetchone()
    return _row(found) if found else None


def pending_count(phone: str) -> int:
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM tasks WHERE phone = ? AND status = 'pending'",
                    (phone,))
        return int(cur.fetchone()[0])


def cancel(task_id: str, phone: str = "") -> bool:
    """Call off a task. Kept as a cancelled row, not deleted - what the farmer asked
    for is part of the record even when it did not happen."""
    ensure_schema()
    with cursor() as cur:
        if phone:
            cur.execute("UPDATE tasks SET status = 'cancelled' WHERE task_id = ?"
                        " AND phone = ? AND status = 'pending'", (task_id, phone))
        else:
            cur.execute("UPDATE tasks SET status = 'cancelled' WHERE task_id = ?"
                        " AND status = 'pending'", (task_id,))
        return cur.rowcount > 0


def mark(task_id: str, status: str, spoken: str = "", call_id: str = "") -> None:
    ensure_schema()
    with cursor() as cur:
        cur.execute("UPDATE tasks SET status = ?, spoken = ?, call_id = ?, fired_at = ?"
                    " WHERE task_id = ?",
                    (status, spoken[:600] or None, call_id or None,
                     _now().isoformat(), task_id))


def note_attempt(task_id: str) -> int:
    """Count a failed dial, and say how many there have now been."""
    ensure_schema()
    with cursor() as cur:
        cur.execute("UPDATE tasks SET attempts = attempts + 1 WHERE task_id = ?",
                    (task_id,))
        cur.execute("SELECT attempts FROM tasks WHERE task_id = ?", (task_id,))
        found = cur.fetchone()
    return int(found[0]) if found else 0


def due_now(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Pending tasks whose moment has arrived, plus every watch (always re-checked)."""
    ensure_schema()
    now = now or _now()
    with cursor() as cur:
        cur.execute("SELECT * FROM tasks WHERE status = 'pending'"
                    " ORDER BY due_at ASC LIMIT 200")
        rows = [_row(r) for r in cur.fetchall()]

    ready = []
    for row in rows:
        if row["kind"] == "watch":
            # A watch has no due time to wait for; it is tested on every sweep until
            # it fires or runs out.
            ready.append(row)
            continue
        due = _parse_dt(row["due_at"])
        if due and due <= now:
            ready.append(row)
    return ready


def expire_stale(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Watches that ran out of time. Returned so the farmer can be told."""
    ensure_schema()
    now = now or _now()
    out = []
    for row in due_now(now):
        if row["kind"] != "watch":
            continue
        expires = _parse_dt(row["expires_at"])
        if expires and expires <= now:
            mark(row["task_id"], "expired")
            out.append(row)
    return out


# ------------------------------------------------------------ speaking it back

def describe_when(due: datetime) -> str:
    """The due time as a farmer would say it, in their own clock."""
    local = due.astimezone(IST)
    today = _now().astimezone(IST).date()
    if local.date() == today:
        return "today at %s" % local.strftime("%H:%M")
    if local.date() == today + timedelta(days=1):
        return "tomorrow at %s" % local.strftime("%H:%M")
    return local.strftime("%d %b at %H:%M")


def confirmation(task: Dict[str, Any]) -> str:
    """What to tell the farmer at the moment they ask for it.

    It names the time back to them, because a booking they cannot verify is one they
    have to trust blindly - and if the parser took "2" out of the wrong part of the
    sentence, this is where they catch it.
    """
    if task["kind"] == "watch":
        if task["watch_type"] == "rain":
            return ("I will keep an eye on the forecast and call you if rain is "
                    "coming in the next three days.")
        direction = "goes above" if task["watch_type"] == "price_above" else "falls below"
        return ("I will watch the %s price and call you when it %s %d rupees a "
                "quintal. I will check for the next %d days."
                % (task["subject"], direction, round(task["threshold"] or 0), WATCH_DAYS))

    due = _parse_dt(task["due_at"])
    when = describe_when(due) if due else "shortly"
    if task["kind"] == "question":
        return "I will call you %s with that." % when
    return "I will call you %s to remind you." % when


# ------------------------------------------------------- booking it in one go

def book_from_speech(phone: str, said: str, *, language: str = "hi",
                     parcel_id: str = "", source: str = "call",
                     now: Optional[datetime] = None) -> Dict[str, Any]:
    """Turn a spoken request into a task, or say what is missing.

    Returns either ``{"booked": True, "task": ..., "reply": ...}`` or
    ``{"booked": False, "reply": <one short question>}``. It never guesses a time:
    a call that rings at the wrong hour teaches the farmer not to trust the feature,
    and asking "when?" costs one turn.
    """
    now = now or _now()
    text = (said or "").strip()
    if not text:
        return {"booked": False,
                "reply": "What would you like me to call you about, and when?"}

    # A condition beats a clock. "Call me when wheat crosses 2400" has a number in it
    # that is a price, not a delay, so the watch has to be tested first or the time
    # parser would book a call in 2400 minutes.
    watch = parse_watch(text)
    if watch:
        task = create(phone, text, "watch", now, now=now, language=language,
                      parcel_id=parcel_id, source=source, **watch)
        return {"booked": True, "task": task, "reply": confirmation(task)}

    when = parse_when(text, now)
    if not when:
        return {"booked": False,
                "reply": "When should I call you? You can say something like "
                         "in thirty minutes, or tomorrow morning."}

    due, _phrase = when
    if due <= now:
        return {"booked": False,
                "reply": "That time has already passed. When should I call you?"}

    # The time the farmer asked for is the time they get. They are awake now, they
    # know what time it will be in half an hour, and night irrigation is ordinary -
    # pump electricity is often supplied at night. Quiet hours are for calls nobody
    # chose the moment for: a watch triggering, or a watch expiring.
    subject = classify_question(text)
    kind = "question" if subject else "reminder"
    task = create(phone, text, kind, due, now=now, language=language,
                  subject=subject or find_crop(text), parcel_id=parcel_id,
                  source=source)
    return {"booked": True, "task": task, "reply": confirmation(task)}
