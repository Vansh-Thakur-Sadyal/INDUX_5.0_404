"""A small challenge in front of sign-up, sign-in and password reset.

Not an anti-bot measure in the Turnstile sense - a determined script will read the
question. What it stops is the cheap case: a loop hammering /api/auth/login with a
password list, or signing up a thousand accounts, without ever fetching a challenge
or keeping state.

Design notes:

* The answer is **never sent to the browser**. The challenge carries an id; the answer
  lives here, hashed, and is checked server-side. A captcha whose answer travels with
  the question is decoration.
* Each challenge is single-use and short-lived, so a correct answer cannot be replayed
  across a run of attempts.
* It is arithmetic in words rather than a distorted image: it has to work for a farmer
  on a cheap phone and a slow connection, and an image nobody can read is a worse
  barrier to the legitimate user than to the script.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .storage import cursor

SCHEMA = """
CREATE TABLE IF NOT EXISTS captcha_challenges (
    challenge_id TEXT PRIMARY KEY,
    answer_hash  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    used_at      TEXT
);
"""

TTL_MINUTES = 10
WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen", "twenty")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def ensure_schema() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)


def _digest(value: str) -> str:
    return hashlib.sha256(str(value).strip().lower().encode("utf-8")).hexdigest()


def issue() -> Dict[str, Any]:
    """A fresh challenge. The reply contains the question and an id - never the answer."""
    ensure_schema()
    left = secrets.randbelow(9) + 1
    right = secrets.randbelow(9) + 1
    # Addition only, and always under twenty: the point is to be answerable.
    question = "What is %s plus %s?" % (WORDS[left], WORDS[right])
    answer = left + right

    challenge_id = "cap-" + uuid.uuid4().hex[:12]
    with cursor() as cur:
        cur.execute("INSERT INTO captcha_challenges (challenge_id, answer_hash, "
                    "created_at, expires_at) VALUES (?,?,?,?)",
                    (challenge_id, _digest(str(answer)), _now().isoformat(),
                     (_now() + timedelta(minutes=TTL_MINUTES)).isoformat()))
        # Keep the table from growing without bound.
        cur.execute("DELETE FROM captcha_challenges WHERE expires_at < ?",
                    ((_now() - timedelta(hours=1)).isoformat(),))
    return {"challenge_id": challenge_id, "question": question,
            "expires_in_minutes": TTL_MINUTES}


def check(challenge_id: str, answer: str) -> str:
    """"" when the answer is right, otherwise why it is not.

    Accepts the digit or the word, because someone typing "five" has solved it.
    """
    ensure_schema()
    if not challenge_id:
        return "Answer the question above to continue."

    with cursor() as cur:
        cur.execute("SELECT * FROM captcha_challenges WHERE challenge_id=?",
                    (challenge_id,))
        row = cur.fetchone()
    if not row:
        return "That question expired. Here is a new one."
    if row["used_at"]:
        return "That question was already answered. Here is a new one."
    expires = _parse(row["expires_at"])
    if expires is None or expires <= _now():
        return "That question expired. Here is a new one."

    given = str(answer or "").strip().lower()
    if given in WORDS:
        given = str(WORDS.index(given))

    # Single use whether right or wrong: otherwise one challenge can be brute-forced
    # twenty times over.
    with cursor() as cur:
        cur.execute("UPDATE captcha_challenges SET used_at=? WHERE challenge_id=?",
                    (_now().isoformat(), challenge_id))

    if not hmac.compare_digest(row["answer_hash"], _digest(given)):
        return "That answer is not right. Here is a new question."
    return ""
