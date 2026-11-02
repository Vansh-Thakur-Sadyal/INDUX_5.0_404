"""Remembering what a farmer sounds like, so Saarthi answers them and not a radio.

What this is, and what it is not
--------------------------------
It is a **convenience filter**. It compares a new clip against the clips the
farmer deliberately enrolled, and returns how close they are. That is enough to
stop the wake word firing at a television, and enough to notice that the person
on the line is not the person whose number it is.

It is **not security**, and nothing in this project treats it as such. Three
reasons, all of them real:

* the features here are spectral statistics, not a trained speaker model - they
  separate a man from a woman reliably and two men of similar age much less so;
* a recording of the farmer's voice played back would pass, and so, increasingly,
  would a synthesised one;
* a cold, a bad line or a noisy field all move the score.

So a voice match never opens an account, never authorises money and never
unlocks data. The session cookie and the one-time code do that, as before. What
a voice match does is decide whether a wake word should wake, and whether to say
"this does not sound like you - is this your phone?" out loud.

Consent
-------
A voiceprint is biometric data about a person, and it is stored only when the
farmer has been asked, in their own language, and has said yes. The asking is in
``conversation/enrolment.py``; the consent and its timestamp are stored in the
row here beside the vectors, so no part of the system can use a print that
nobody agreed to. ``forget`` deletes it, and the dashboard and the app both
offer that.

Clips themselves are never kept. Only the derived numbers, which cannot be
played back as audio.
"""
from __future__ import annotations

import io
import json
import logging
import math
import struct
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import speakermodel, voicefeatures
from .storage import add_missing_columns, cursor

log = logging.getLogger("saarthi.voiceprint")

# Enough speech to describe a voice. Below this a "match" is noise: a one-word
# clip of anybody matches a one-word clip of anybody else.
MIN_SECONDS = 1.2
MIN_SAMPLES = 8000

# How many enrolled clips are kept. Three catches a normal voice, a tired voice
# and a shouted-over-a-tractor voice, which is most of the variation that matters.
MAX_CLIPS = 3

# Cosine similarity thresholds, chosen by measuring the same speaker against
# themselves and against others on this feature set. Deliberately generous:
# refusing the real farmer is a worse failure than occasionally accepting a
# similar voice, because the thing being protected is a convenience.
STRONG_MATCH = 0.86
WEAK_MATCH = 0.74

# --------------------------------------------------- the ECAPA decision points
# ECAPA embeddings are calibrated in a way the cepstral description never was:
# the cosine between two of them means roughly the same thing for every pair of
# speakers, which is why this path needs no cohort to normalise against.
#
# The numbers below sit inside a gap that was MEASURED, on real speech rather
# than on the synthesiser the unit tests use. Sixteen clips, two speakers, four
# different sentences each, through this exact ONNX graph:
#
#   same speaker, different sentences   0.774 to 0.882
#   different speakers                  0.106 to 0.276
#
# An empty band half a point wide, where the old cepstral matcher had an
# impostor at 0.896 against a threshold of 0.84 - no band at all. Two speakers
# of synthesised English is a narrow sample and the real range on Indian
# telephone audio will be wider at both ends, so every number here is placed
# nearer the impostors than the middle: refusing the farmer is the worse of the
# two failures, because the thing being protected is a convenience.
#
# SpeechBrain publishes 0.25 for this checkpoint as its equal-error point. The
# floor is set above it, because 0.276 - an impostor, in the measurement above -
# is above it too.
EMBEDDING_SAME = 0.35

# Above this a match is reported as STRONG rather than weak. It sits in the
# empty band, 0.27 below the lowest genuine score measured. Below it a clip is
# still a match, reported as "might have been you", and every caller treats
# weak as carry-on.
EMBEDDING_STRONG = 0.50

# How well three clips recorded one after another, in one room, on one handset,
# have to agree before the set is kept. The measurement above is exactly this
# case - one speaker, different sentences, one sitting - and it never fell
# below 0.774, so this has wide headroom while still catching the failure it
# is for: a different person, or a tractor, on one of the three.
EMBEDDING_CONSISTENT = 0.55

# How far below their own clips' agreement a later clip may fall. A different
# day, a different room and a cold all cost something, and this is the
# allowance - the same idea as ACCEPT_MARGIN on the cepstral path.
EMBEDDING_MARGIN = 0.15

# What a usable enrolment clip has to be. Every one of these was something the
# first version accepted and should not have: a clip of a quiet room, a clip of
# somebody breathing, a clip recorded with the microphone against a shirt.
MIN_RMS = 0.015              # below this it is a room, not a person
MAX_CLIPPED_SHARE = 0.02     # above this the recorder was overdriven
MIN_SPEECH_SHARE = 0.25      # below this the clip is mostly silence

# How much a farmer's own three clips must agree before the set is kept. Below
# this, something is wrong with the recording - a different person on one clip,
# a tractor on another - and keeping it would build a print that matches
# everybody a little and nobody well.
CONSISTENT_FLOOR = 0.62

# How far above the background a clip has to sit.
#
# Placed from measurement, in the gap between two populations rather than on top
# of either. Across the synthetic speakers used in the tests, a genuine clip
# lands between +0.78 and +1.62 standard deviations above the comparable
# background, and every impostor tried - including a different man five hertz
# away, the case that defeated the old matcher entirely - lands between -0.27
# and -0.01. The bar sits in the middle of that gap.
#
# It is nearer the impostors than the middle, deliberately: the genuine range
# has a floor at +0.78 with little room below it, and refusing the real farmer
# is the worse of the two failures.
# How far below their own enrolment a farmer's later clip may fall. Their three
# clips were recorded one after another in one place; a later one is a different
# day and a different room, so some slip is expected and allowed for.
Z_MARGIN = 0.40
Z_WEAK_MARGIN = 0.60

# A floor for the rare case of an account enrolled before this was measured.
Z_ACCEPT = 0.50
Z_WEAK = 0.20

# How far below their own clips' AGREEMENT a later clip may fall.
#
# Two signals are required rather than one, because across the two sets of
# voices used in testing neither separated genuine from impostor on its own:
#
#   voices A   genuine raw 0.992-0.995, impostor 0.67-0.70   raw separates
#              genuine z   0.78-0.87,   impostor -0.12--0.01  z separates
#   voices B   genuine raw 0.995-0.998, impostor 0.946-0.954  raw separates
#              genuine z   0.70-0.75,   impostor 0.67-0.68    z does NOT
#
# So acceptance needs both, and either one failing is a refusal.
RAW_MARGIN = 0.03

# And a floor on the raw similarity, so an unusually uniform background cannot
# promote a clip that is simply not very like the farmer.
RAW_FLOOR = 0.55

# Used only when there is too little background to normalise against: strict,
# because without a cohort this is the number that could not tell 0.994 from
# 0.954 in the first place.
RAW_ALONE = 0.985

# The accept threshold is calibrated per farmer from their own clips, then held
# inside these bounds: never so tight that a tired voice is refused, never so
# loose that it stops meaning anything.
MIN_ACCEPT = 0.60
MAX_ACCEPT = 0.84
# How far below their own consistency a farmer is allowed to fall and still be
# recognised. Their clips agreed at X among themselves; a fourth clip on a
# different day will be a little worse than that, and this is the allowance.
ACCEPT_MARGIN = 0.10

# Said to the farmer wherever this is offered - on the dashboard, in the app,
# and read aloud on a call. One copy, because a promise about biometric data
# that is worded three different ways is three different promises.
EXPLANATION = (
    "Your voice is remembered as a set of numbers, not as a recording - it "
    "cannot be played back. It helps Saarthi know it is you; it is not a "
    "password, and it never opens your account on its own."
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS voiceprints (
    phone        TEXT PRIMARY KEY,
    vectors_json TEXT NOT NULL DEFAULT '[]',
    clips        INTEGER NOT NULL DEFAULT 0,
    consent      INTEGER NOT NULL DEFAULT 0,
    consent_at   TEXT,
    consent_said TEXT NOT NULL DEFAULT '',
    language     TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- More than one voice per account, each with a name.
--
-- A farm is worked by more than one person. The first version stored exactly
-- one voiceprint per phone number, so a father and son sharing an account had
-- to choose which of them Saarthi would recognise - and the other one was
-- refused on their own farm.
--
-- `voiceprints` above is kept and migrated from rather than altered: SQLite
-- cannot change a primary key in place, and a table somebody's registration
-- lives in is not one to drop.
--
-- Consent is on the VOICE, not the account. It is agreement by the person
-- whose voice it is, and the second person is a different person; an account
-- holder cannot give it on their behalf.
CREATE TABLE IF NOT EXISTS voices (
    voice_id       TEXT PRIMARY KEY,
    phone          TEXT NOT NULL,
    label          TEXT NOT NULL DEFAULT '',
    vectors_json   TEXT NOT NULL DEFAULT '[]',
    clips          INTEGER NOT NULL DEFAULT 0,
    consent        INTEGER NOT NULL DEFAULT 0,
    consent_at     TEXT,
    consent_said   TEXT NOT NULL DEFAULT '',
    language       TEXT NOT NULL DEFAULT '',
    tightness      REAL NOT NULL DEFAULT 0,
    done           INTEGER NOT NULL DEFAULT 0,
    vector_version INTEGER NOT NULL DEFAULT 0,
    enrol_z        REAL NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voices_phone ON voices(phone, created_at);

-- Whether a voice has to match before Saarthi acts. Per account, and separate
-- from the voices themselves: turning the check off is not the same as
-- deleting a registration, and a farmer who switches it off for a week should
-- not have to record three clips again afterwards.
CREATE TABLE IF NOT EXISTS voice_settings (
    phone      TEXT PRIMARY KEY,
    enabled    INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
"""

# Added after the first version shipped, so they go on with a migration rather
# than in the CREATE above - which does nothing to a table that already exists.
# The shape of a stored vector. Bumped when the description changes, because a
# print made by the old one cannot be compared with the new one - the numbers
# mean different things and are not even the same length. A farmer whose print
# predates the change is asked to register again rather than being matched
# against nonsense.
#
# 1  sixteen log band energies blended with pitch (let an impostor in at 0.896)
# 2  cepstral statistics plus pitch, with cohort normalisation
# 3  the ECAPA-TDNN embedding
#
# Which one is current depends on whether the embedding model is on disk, so
# this is a function and not a constant. An installation without the model
# keeps working on version 2 rather than marking every print stale and asking
# every farmer to re-register for a model that is not there.
CEPSTRAL_VERSION = 2
EMBEDDING_VERSION = 3


def vector_version() -> int:
    return EMBEDDING_VERSION if speakermodel.available() else CEPSTRAL_VERSION


def describer() -> str:
    """Which description is being made, for the status and the logs."""
    return "ecapa-tdnn" if speakermodel.available() else "cepstral"

LATER_COLUMNS = {
    # How closely this farmer's own enrolment clips agree with each other. It is
    # what the accept threshold is calibrated from: a voice whose own three
    # clips only reach 0.80 cannot be asked to reach 0.86 on a fourth.
    "tightness": "REAL NOT NULL DEFAULT 0",
    # Enrolment is finished, not merely started. The first version called a
    # print "registered" after ONE clip, so pressing the button and saying
    # nothing useful produced a voiceprint that then gated everything.
    "done": "INTEGER NOT NULL DEFAULT 0",
    # 0 for everything that existed before versioning, which is exactly the set
    # that has to be re-registered.
    "vector_version": "INTEGER NOT NULL DEFAULT 0",
    # Where this farmer's OWN clips sit above the background, in standard
    # deviations. A later clip is asked to come within a margin of it. An
    # absolute bar cannot work: measured across two sets of voices, a genuine
    # clip landed at +0.78 in one and +1.45 in the other, and an impostor landed
    # at +0.67 in the second - above the genuine range of the first. There is no
    # single number, but there is a reliable DISTANCE from each farmer's own.
    "enrol_z": "REAL NOT NULL DEFAULT 0",
}


# How many voices one account may register. A farm has owners, not a crowd;
# a list that can grow without limit is a list somebody fills with noise.
MAX_VOICES = 5

# What the first voice is called when nobody has named it.
FIRST_LABEL = "Owner 1"


def ensure_schema() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)
    add_missing_columns("voiceprints", LATER_COLUMNS)
    _migrate_single_prints()


def _migrate_single_prints() -> None:
    """Carry every one-per-phone registration into the multi-voice table.

    Runs once per row and is safe to call on every request: a phone that
    already has a voice is left alone. The old table is NOT dropped - if
    anything here is wrong, the original registrations are still where they
    were.
    """
    with cursor() as cur:
        cur.execute("SELECT * FROM voiceprints")
        old_rows = [dict(r) for r in cur.fetchall()]
        if not old_rows:
            return
        cur.execute("SELECT DISTINCT phone FROM voices")
        already = {r[0] for r in cur.fetchall()}

    for row in old_rows:
        phone = str(row.get("phone") or "")
        if not phone or phone in already:
            continue
        now = _now()
        with cursor() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO voices (voice_id, phone, label,"
                " vectors_json, clips, consent, consent_at, consent_said,"
                " language, tightness, done, vector_version, enrol_z,"
                " created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_new_voice_id(), phone, FIRST_LABEL,
                 row.get("vectors_json") or "[]", int(row.get("clips") or 0),
                 int(row.get("consent") or 0), row.get("consent_at"),
                 str(row.get("consent_said") or ""), str(row.get("language") or ""),
                 float(row.get("tightness") or 0.0), int(row.get("done") or 0),
                 int(row.get("vector_version") or 0), float(row.get("enrol_z") or 0.0),
                 row.get("created_at") or now, row.get("updated_at") or now))


def _new_voice_id() -> str:
    return "vp-" + uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------ the audio
def _samples(audio: bytes) -> Tuple[List[float], int]:
    """Mono float samples and the sample rate, from a WAV.

    Only PCM WAV, which is what the recorder in the app and Sarvam's own clips
    produce. Anything else returns nothing rather than guessing at the bytes -
    a voiceprint built from misread audio would be worse than no voiceprint.
    """
    try:
        with wave.open(io.BytesIO(audio), "rb") as clip:
            channels = clip.getnchannels()
            width = clip.getsampwidth()
            rate = clip.getframerate()
            frames = clip.readframes(clip.getnframes())
    except (wave.Error, EOFError, OSError):
        return [], 0
    if width != 2 or not frames:
        return [], rate

    count = len(frames) // 2
    values = struct.unpack("<%dh" % count, frames[:count * 2])
    if channels > 1:
        values = [sum(values[i:i + channels]) / channels
                  for i in range(0, len(values) - channels + 1, channels)]
    peak = max((abs(v) for v in values), default=0) or 1
    return [v / peak for v in values], rate


def _samples_true(audio: bytes) -> Tuple[List[float], int]:
    """The same samples, at their REAL amplitude rather than peak-normalised.

    [_samples] divides by the peak, which is right for the matcher - a voice is
    the same voice recorded loudly or quietly - and quite wrong for judging
    whether a recording is any good. Measured on a peak-normalised clip, "how
    loud is it" always answers "as loud as its loudest moment", and a clip of a
    silent room comes back at full scale made entirely of quantisation steps:
    the first version of the quality check read that as a DISTORTED recording
    and told the farmer to hold the phone further away.
    """
    try:
        with wave.open(io.BytesIO(audio), "rb") as clip:
            channels = clip.getnchannels()
            width = clip.getsampwidth()
            rate = clip.getframerate()
            frames = clip.readframes(clip.getnframes())
    except (wave.Error, EOFError, OSError):
        return [], 0
    if width != 2 or not frames:
        return [], rate
    count = len(frames) // 2
    values = struct.unpack("<%dh" % count, frames[:count * 2])
    if channels > 1:
        values = [sum(values[i:i + channels]) / channels
                  for i in range(0, len(values) - channels + 1, channels)]
    return [v / 32768.0 for v in values], rate


def inspect(audio: bytes) -> Dict[str, Any]:
    """Is this clip good enough to learn a voice from, and if not, why not?

    Separate from [features] because the farmer needs a REASON. "That recording
    could not be used" sends them round the same loop doing the same thing;
    "that was too quiet, hold the phone closer" is something they can act on.

    Everything it refuses here is something the first version accepted: a clip
    of a quiet room, a clip that was three-quarters silence, a clip recorded so
    close that every sample hit the rails and the spectrum became a square wave.
    """
    try:
        import numpy as np
    except ImportError:                                            # pragma: no cover
        return {"ok": False, "reason": "unavailable",
                "detail": "Voice registration is not available on this server."}

    # True amplitudes, not peak-normalised ones: every judgement below is about
    # how the clip was RECORDED, and peak normalisation erases exactly that.
    values, rate = _samples_true(audio)
    if not values or rate <= 0:
        return {"ok": False, "reason": "unreadable",
                "detail": "That recording could not be read. It has to be a WAV clip."}

    seconds = len(values) / rate
    if seconds < MIN_SECONDS:
        return {"ok": False, "reason": "too_short", "seconds": round(seconds, 2),
                "detail": "That was %.1f seconds. Say a whole sentence - about "
                          "three seconds." % seconds}

    signal = np.asarray(values, dtype=np.float64)
    rms = float(np.sqrt((signal ** 2).mean()))
    clipped = float((np.abs(signal) > 0.995).mean())

    window = max(int(rate * 0.02), 1)
    frames = signal[:len(signal) // window * window].reshape(-1, window)
    energy = np.sqrt((frames ** 2).mean(axis=1))
    speaking = float((energy > max(energy.mean() * 0.35, 1e-4)).mean())

    report = {"seconds": round(seconds, 2), "rms": round(rms, 4),
              "clipped": round(clipped, 4), "speech_share": round(speaking, 3)}

    if rms < MIN_RMS:
        return {**report, "ok": False, "reason": "too_quiet",
                "detail": "That was too quiet. Hold the phone closer and speak "
                          "normally."}
    if clipped > MAX_CLIPPED_SHARE:
        return {**report, "ok": False, "reason": "too_loud",
                "detail": "That came out distorted. Hold the phone a little "
                          "further away."}
    if speaking < MIN_SPEECH_SHARE:
        return {**report, "ok": False, "reason": "mostly_silence",
                "detail": "Most of that was silence. Start speaking as soon as "
                          "the recording begins."}

    vector = features(audio)
    if vector is None:
        return {**report, "ok": False, "reason": "unusable",
                "detail": "There was not enough speech in that to describe a voice."}

    # Pitch, measured here rather than read out of the vector.
    #
    # It used to be `vector[0]`, which is where the cepstral description keeps
    # it. An ECAPA embedding has no such slot - element 0 is one of 192
    # dimensions with no meaning on its own - so reading it as hertz would have
    # refused good clips and passed silent ones, depending on a sign.
    pitch, _spread = voicefeatures.pitch_spread(signal, rate)
    if pitch <= 0:
        # An unvoiced clip describes a room. It would still look registered.
        return {**report, "ok": False, "reason": "not_voiced",
                "detail": "Saarthi could not hear a voice in that - only noise. "
                          "Try somewhere quieter."}

    return {**report, "ok": True, "pitch_hz": round(pitch, 1), "vector": vector}


def features(audio: bytes) -> Optional[List[float]]:
    """A description of the voice in a clip, or None if there is not enough.

    **ECAPA-TDNN when the model is on disk**, which is the whole point of
    ``speakermodel``: 192 numbers from a network trained on 7,205 speakers, in
    a space built for telling people apart.

    The cepstral description is kept as the fallback for an installation
    without the model file, and it is the measurement that explains why the
    model is there at all: thirteen cepstral means plus pitch let a different
    man five hertz away score 0.896 against an enrolled farmer, over a
    threshold of 0.84. The two are never mixed - a stored print records which
    one made it, and a print from the other is treated as unregistered.
    """
    try:
        import numpy as np
    except ImportError:                                            # pragma: no cover
        return None

    values, rate = _samples(audio)
    if len(values) < MIN_SAMPLES or rate <= 0:
        return None
    if len(values) / rate < MIN_SECONDS:
        return None

    embedded = speakermodel.embed(values, rate)
    if embedded is not None:
        return embedded
    return voicefeatures.describe(np.asarray(values, dtype=np.float64), rate)


def similarity(a: List[float], b: List[float]) -> float:
    """How alike two voices are.

    Dispatched on what the vectors ARE. An ECAPA embedding is compared by
    cosine, on the scale its published threshold is quoted in; a cepstral
    description is compared by ``voicefeatures.compare``, which is a
    correlation with a pitch penalty on it. Handing one to the other's
    comparison returns 0.0 rather than a plausible-looking number.

    On its own this number decides nothing. What it is compared AGAINST is in
    ``verify``, and for the cepstral path also in ``normalised_score``.
    """
    if speakermodel.is_embedding(a) or speakermodel.is_embedding(b):
        if not (speakermodel.is_embedding(a) and speakermodel.is_embedding(b)):
            return 0.0
        return speakermodel.compare(a, b)
    return voicefeatures.compare(a, b)


# ------------------------------------------------------------- the background
# Scoring a clip only against the farmer asks the wrong question. "How much does
# this sound like Ramesh" cannot separate 0.994 from 0.954; "how much MORE does
# this sound like Ramesh than it sounds like people in general" separates the
# same pair by two standard deviations.
#
# The cohort is a set of other voices. It ships with synthesised ones - there is
# no corpus of consented farmer voices to build it from, and collecting one to
# put in a repository would be wrong even if there were - and real enrolments
# are added to it as they arrive, anonymously and as vectors only. A synthetic
# background normalises more coarsely than a real one; that is stated here
# rather than hidden, and it improves as the real ones accumulate.
_COHORT_FILE = Path(__file__).with_name("voice_cohort.json")
_shipped_cohort: Optional[List[List[float]]] = None

# How many real enrolments to keep as background. Enough to normalise against,
# few enough that it stays cheap to score every turn.
COHORT_LIMIT = 48


def _shipped() -> List[List[float]]:
    global _shipped_cohort
    if _shipped_cohort is None:
        try:
            data = json.loads(_COHORT_FILE.read_text(encoding="utf-8"))
            _shipped_cohort = [list(map(float, v)) for v in data.get("vectors", [])]
        except (OSError, ValueError):                              # pragma: no cover
            log.warning("no background voice cohort; matching will be coarser")
            _shipped_cohort = []
    return _shipped_cohort


def cohort(exclude: str = "") -> List[List[float]]:
    """Background voices to normalise against, shipped plus real enrolments.

    ``exclude`` is the farmer being tested: their own vectors must not appear in
    the background they are being compared against, or they raise the bar on
    themselves.
    """
    ensure_schema()
    out = list(_shipped())
    with cursor() as cur:
        # From `voices`, and excluding EVERY voice on the account being
        # measured - not just one row of it. With two voices on one account,
        # leaving the second in the background would measure a farmer against
        # their own father and call them both unremarkable.
        cur.execute("SELECT phone, vectors_json FROM voices WHERE done = 1"
                    " AND phone != ? ORDER BY updated_at DESC LIMIT ?",
                    (str(exclude or ""), COHORT_LIMIT))
        for row in cur.fetchall():
            try:
                vectors = json.loads(row["vectors_json"] or "[]")
            except json.JSONDecodeError:
                continue
            if vectors:
                # One per person: three clips of the same voice in the
                # background would weight that voice three times.
                out.append(vectors[0])
    return out


def normalised_score(vector: List[float], known: List[List[float]],
                     background: List[List[float]]) -> Dict[str, Any]:
    """How far above the background this clip sits, in standard deviations.

    The raw similarity is kept too, as a floor: a clip can be comfortably above
    an unusually uniform background and still not be very like the farmer.
    """
    raw = max((similarity(vector, k) for k in known), default=0.0)

    # Only the background voices this clip could plausibly be CONFUSED with.
    #
    # A cohort spanning every pitch is mostly voices the pitch gate rules out at
    # zero, and a background of zeros has a huge spread - which flattens the
    # z-score and lets a near-miss through. Measured: with the whole cohort, an
    # impostor five hertz away scored z=+1.00 against the farmer's +1.07 and was
    # admitted. Normalising against the comparable voices only is what
    # cohort normalisation means in practice.
    others = [score for score in (similarity(vector, b) for b in background)
              if score > 0.0]
    if len(others) < 4:
        # Not enough background to normalise against. Say so rather than
        # inventing a z-score from two numbers.
        return {"raw": raw, "z": None, "background": len(others)}

    mean = sum(others) / len(others)
    variance = sum((o - mean) ** 2 for o in others) / len(others)
    spread = math.sqrt(variance) or 1e-6
    return {"raw": raw, "z": (raw - mean) / spread, "background": len(others)}


def consent_given(phone: str, said: str = "", language: str = "",
                  voice_id: str = "") -> Dict[str, Any]:
    """Record that the person was asked and agreed, before anything is stored.

    Their own words are kept with the flag. If anyone ever has to show that a
    person agreed, "they said haan on this date" is the evidence, and a boolean
    on its own is not.

    Per VOICE. The second voice on an account belongs to a second person, and
    the account holder cannot agree on their behalf.
    """
    ensure_schema()
    now = _now()
    record = get(phone, voice_id) if voice_id else get(phone)
    if record is None:
        # Nothing registered on this account yet: agreeing IS the first step,
        # so the first voice is created here rather than making the farmer
        # press "add a voice" before they can agree to anything.
        created = add_voice(phone, FIRST_LABEL)
        voice_id = str(created.get("voice_id") or "")
    else:
        voice_id = str(record.get("voice_id"))
    with cursor() as cur:
        cur.execute("UPDATE voices SET consent = 1, consent_at = ?,"
                    " consent_said = ?, language = ?, updated_at = ?"
                    " WHERE voice_id = ?",
                    (now, said[:200], language, now, voice_id))
    return {"phone": phone, "voice_id": voice_id, "consent": True, "at": now}


# What the farmer is asked to say, one phrase per clip.
#
# Prompted, and three DIFFERENT phrases, for a reason that is not obvious: a
# voiceprint built from one word repeated three times describes that word, not
# the person saying it. Different vowels and consonants move the vocal tract
# through more of its range, and the spread across the three clips is what the
# accept threshold is calibrated from.
#
# They are also ordinary sentences a farmer can read aloud without feeling
# tested, which matters more than it sounds: somebody self-conscious reads in a
# voice that is not the one they will use on a call.
PROMPTS = {
    "en": [
        "My name is, and this is my phone.",
        "I grow wheat on my land this season.",
        "Saarthi, tell me today's weather please.",
    ],
    "hi": [
        "मेरा नाम है, और यह मेरा फ़ोन है।",
        "इस बार मेरे खेत में गेहूँ लगा है।",
        "सारथी, आज का मौसम बताइए।",
    ],
}


def prompts(language: str = "en") -> List[str]:
    """The three phrases to read, in the farmer's language."""
    return list(PROMPTS.get((language or "en").lower(), PROMPTS["en"]))


def _agreement(vectors: List[List[float]]) -> float:
    """How closely a set of clips agree with each other.

    The MINIMUM of the pairwise scores, not the average: one clip that does not
    belong - a different person, a tractor going past - is exactly what this is
    for, and an average would hide it behind the two that are fine.
    """
    if len(vectors) < 2:
        return 0.0
    scores = [similarity(vectors[i], vectors[j])
              for i in range(len(vectors)) for j in range(i + 1, len(vectors))]
    return min(scores) if scores else 0.0


def accept_threshold(tightness: float, *, embedding: Optional[bool] = None) -> float:
    """How close a later clip has to be, for THIS farmer.

    Calibrated rather than fixed. A global 0.86 refuses a farmer whose own three
    clips only agree at 0.80 - they can never reach it, so the feature locks out
    the one person it is supposed to recognise. It also waves through a farmer
    whose clips agree at 0.98, where 0.86 is a far looser gate than it looks.

    The two describers work on different scales - a cepstral correlation runs
    high and tight, an ECAPA cosine runs lower and wider - so the bounds differ
    and the caller says which it means. ``embedding`` defaults to whichever
    describer is current.
    """
    if embedding is None:
        embedding = speakermodel.available()
    if embedding:
        # Never below the published same-speaker point, however loosely this
        # farmer's own clips happened to agree.
        if tightness <= 0:
            return EMBEDDING_STRONG
        return max(EMBEDDING_SAME, tightness - EMBEDDING_MARGIN)
    if tightness <= 0:
        return (STRONG_MATCH + WEAK_MATCH) / 2.0
    return max(MIN_ACCEPT, min(MAX_ACCEPT, tightness - ACCEPT_MARGIN))


def enrol(phone: str, audio: bytes, *, language: str = "",
          voice_id: str = "") -> Dict[str, Any]:
    """Add one clip to this farmer's voiceprint.

    Refuses outright unless consent is already on the row. That check is here,
    at the only place that writes, rather than at each caller - a consent rule
    enforced by remembering to call it is one that will eventually be forgotten.

    Enrolment is only FINISHED when three good clips are in and they agree with
    each other. The first version marked a print registered after one clip, so a
    farmer could press the button, say nothing useful, be thanked, and end up
    with a voiceprint that then decided whether they were allowed to speak.
    """
    ensure_schema()
    record = get(phone, voice_id)
    if not record or not record.get("consent"):
        return {"ok": False, "reason": "no_consent", "clips": 0, "done": False,
                "detail": "The farmer has not agreed to their voice being remembered."}
    voice_id = str(record.get("voice_id"))

    report = inspect(audio)
    vectors = list(record.get("vectors") or [])
    if not report["ok"]:
        # Nothing is stored, and the count does not move. The farmer is told
        # what was wrong and asked for the same clip again.
        return {**{k: v for k, v in report.items() if k != "vector"},
                "clips": len(vectors), "need": max(0, MAX_CLIPS - len(vectors)),
                "done": bool(record.get("done"))}

    # A clip arriving against a FINISHED print starts a new set rather than
    # being blended into the old one. The old version kept the last three of
    # four, so a re-registration was two old clips and one new - and when the
    # three disagreed, as they often did, the finished print was destroyed.
    # This is also the path the phone line takes, where nobody pressed a button.
    if record.get("done") and vectors:
        vectors = []

    vectors.append(report["vector"])
    vectors = vectors[-MAX_CLIPS:]
    done = len(vectors) >= MAX_CLIPS
    tightness = _agreement(vectors) if done else 0.0

    floor = (EMBEDDING_CONSISTENT if speakermodel.is_embedding(vectors[0])
             else CONSISTENT_FLOOR)
    if done and tightness < floor:
        # The three clips do not sound like one person. Keeping them would build
        # a print that matches everybody a little and nobody well - and the
        # farmer would never know why it stopped recognising them. Start again.
        _write(voice_id, [], tightness=0.0, done=False, language=language,
               previous=record)
        return {"ok": False, "reason": "inconsistent", "clips": 0,
                "need": MAX_CLIPS, "done": False, "agreement": round(tightness, 3),
                "detail": "Those recordings did not sound like the same voice. "
                          "Somewhere quieter, and let one person say all three."}

    # Where these clips sit above the background, which is what a later clip is
    # measured against. Each one is scored against the OTHERS, never itself: a
    # clip compared with itself scores 1.0 and would set a bar nobody can meet.
    #
    # Only for the cepstral description. An ECAPA cosine means the same thing
    # for every pair of speakers, so there is nothing for a cohort to correct
    # and a synthesised background would only add noise to a calibrated number.
    enrol_z = 0.0
    if done and not speakermodel.is_embedding(vectors[0]):
        background = cohort(exclude=phone)
        seen = []
        for index, one in enumerate(vectors):
            others = [v for i, v in enumerate(vectors) if i != index]
            scored = normalised_score(one, others, background)
            if scored["z"] is not None:
                seen.append(scored["z"])
        enrol_z = min(seen) if seen else 0.0

    _write(voice_id, vectors, tightness=tightness, done=done, language=language,
           previous=record, enrol_z=enrol_z)
    return {"ok": True, "voice_id": voice_id, "label": record.get("label", ""),
            "clips": len(vectors), "need": max(0, MAX_CLIPS - len(vectors)),
            "done": done, "agreement": round(tightness, 3) if done else None,
            "accept_above": round(accept_threshold(tightness), 3) if done else None,
            "pitch_hz": report.get("pitch_hz"),
            "detail": ("Your voice is registered." if done else
                       "Good. %d more to go." % (MAX_CLIPS - len(vectors)))}


def _write(voice_id: str, vectors: List[List[float]], *, tightness: float,
           done: bool, language: str, previous: Dict[str, Any],
           enrol_z: float = 0.0) -> None:
    now = _now()
    with cursor() as cur:
        cur.execute("UPDATE voices SET vectors_json = ?, clips = ?,"
                    " tightness = ?, done = ?, vector_version = ?, enrol_z = ?,"
                    " language = ?, updated_at = ? WHERE voice_id = ?",
                    (json.dumps(vectors), len(vectors), float(tightness),
                     1 if done else 0, vector_version(), float(enrol_z),
                     language or previous.get("language", ""), now, voice_id))


def _record(row) -> Dict[str, Any]:
    record = {k: row[k] for k in row.keys()}
    try:
        record["vectors"] = json.loads(record.pop("vectors_json") or "[]")
    except json.JSONDecodeError:
        record["vectors"] = []
    record["consent"] = bool(record.get("consent"))
    record["done"] = bool(record.get("done"))
    return record


def voices_for(phone: str) -> List[Dict[str, Any]]:
    """Every voice registered on this account, oldest first."""
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT * FROM voices WHERE phone = ? ORDER BY created_at",
                    (phone,))
        rows = cur.fetchall()
    return [_record(row) for row in rows]


def by_id(voice_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT * FROM voices WHERE voice_id = ?", (voice_id,))
        row = cur.fetchone()
    return _record(row) if row else None


def get(phone: str, voice_id: str = "") -> Optional[Dict[str, Any]]:
    """One voice: the one asked for, or the one being worked on.

    Without a ``voice_id`` this answers the question the single-voice version
    answered - "the voiceprint for this number" - by picking the one a farmer
    is most likely to mean: the registration in progress if there is one, and
    otherwise the most recently finished.
    """
    if voice_id:
        record = by_id(voice_id)
        return record if record and record.get("phone") == phone else None

    rows = voices_for(phone)
    if not rows:
        return None
    started = [r for r in rows if not r.get("done") and (r.get("clips") or 0) >= 0]
    in_progress = [r for r in started if (r.get("clips") or 0) > 0]
    if in_progress:
        return in_progress[-1]
    finished = [r for r in rows if r.get("done")]
    if finished:
        return finished[-1]
    return rows[-1]


def add_voice(phone: str, label: str = "") -> Dict[str, Any]:
    """Start a second (or third) voice on this account.

    It begins with NO consent, deliberately. The person whose voice this will
    be has to agree themselves - the account holder cannot agree on behalf of
    their son, and `enrol` refuses without it.
    """
    ensure_schema()
    existing = voices_for(phone)
    if len(existing) >= MAX_VOICES:
        return {"ok": False, "reason": "too_many",
                "detail": "This account already has %d voices." % MAX_VOICES}
    name = clean_label(label) or ("Owner %d" % (len(existing) + 1))
    now = _now()
    voice_id = _new_voice_id()
    with cursor() as cur:
        cur.execute(
            "INSERT INTO voices (voice_id, phone, label, vectors_json, clips,"
            " consent, consent_at, consent_said, language, created_at, updated_at)"
            " VALUES (?,?,?, '[]', 0, 0, NULL, '', '', ?, ?)",
            (voice_id, phone, name, now, now))
    return {"ok": True, "voice_id": voice_id, "label": name}


def clean_label(label: str) -> str:
    """A name a farmer typed, trimmed to something that fits on a card."""
    return " ".join(str(label or "").split())[:40]


def rename(phone: str, voice_id: str, label: str) -> Dict[str, Any]:
    record = get(phone, voice_id)
    if not record:
        return {"ok": False, "reason": "not_found"}
    name = clean_label(label)
    if not name:
        return {"ok": False, "reason": "empty", "detail": "Give the voice a name."}
    with cursor() as cur:
        cur.execute("UPDATE voices SET label = ?, updated_at = ? WHERE voice_id = ?",
                    (name, _now(), voice_id))
    return {"ok": True, "voice_id": voice_id, "label": name}


def enrolled(phone: str) -> bool:
    """A FINISHED registration, not a started one.

    This is what every gate asks, so it has to mean the whole thing: consent
    given, three good clips in, and those clips agreeing with each other. It
    used to mean "has at least one vector", which is how a half-finished
    registration came to decide whether its owner was allowed to speak.
    """
    return any(r.get("consent") and r.get("done") and r.get("vectors")
               for r in voices_for(phone))


# How the four verdicts rank, for picking the best of several voices.
_MATCH_RANK = {"strong": 3, "weak": 2, "unknown": 1, "no": 0}


def verify(phone: str, audio: bytes) -> Dict[str, Any]:
    """How much this clip sounds like ANY voice registered on this account.

    ``match`` is "strong", "weak", "no" or "unknown". **Unknown is the common
    case** - nobody enrolled, or the clip was too short - and every caller must
    treat it as "carry on as normal", not as a refusal. A feature that locks
    people out when it cannot tell is worse than not having it.

    With several voices on an account the answer is the BEST of them, and the
    result says which one it was. A farm worked by two people has to recognise
    both; refusing the son because the father registered first would be the
    same bug as before, one person further along.
    """
    usable = [r for r in voices_for(phone)
              if r.get("consent") and r.get("done") and r.get("vectors")
              and int(r.get("vector_version") or 0) == vector_version()]
    if not usable:
        # Say WHY there is nothing to compare against: a registration made by
        # an older describer is a different problem from no registration, and
        # the status line tells the farmer to redo it.
        stale = any(r.get("done") and int(r.get("vector_version") or 0)
                    != vector_version() for r in voices_for(phone))
        if stale:
            return {"match": "unknown", "score": 0.0, "enrolled": False,
                    "stale": True,
                    "note": "This voice was registered by an older version of "
                            "Saarthi. Please register it again."}
        return {"match": "unknown", "score": 0.0, "enrolled": False,
                "note": "No finished voice registration for this number."}

    vector = features(audio)
    if vector is None:
        return {"match": "unknown", "score": 0.0, "enrolled": True,
                "note": "That clip was too short or too quiet to compare."}

    best: Optional[Dict[str, Any]] = None
    for record in usable:
        outcome = _verify_one(vector, record, phone)
        outcome["voice_id"] = record.get("voice_id")
        outcome["label"] = record.get("label") or ""
        if best is None or _better(outcome, best):
            best = outcome
    best = best or {"match": "unknown", "score": 0.0, "enrolled": True}
    best["voices"] = len(usable)
    return best


def _better(candidate: Dict[str, Any], current: Dict[str, Any]) -> bool:
    """A clearer verdict wins; between equal verdicts, the higher score."""
    rank = _MATCH_RANK.get(str(candidate.get("match")), 0)
    theirs = _MATCH_RANK.get(str(current.get("match")), 0)
    if rank != theirs:
        return rank > theirs
    return float(candidate.get("score") or 0) > float(current.get("score") or 0)


def _verify_one(vector: List[float], record: Dict[str, Any],
                phone: str) -> Dict[str, Any]:
    """One clip against one registered voice."""

    if speakermodel.is_embedding(vector):
        return _verify_embedding(vector, record)

    scored = normalised_score(vector, record["vectors"], cohort(exclude=phone))
    raw, z = scored["raw"], scored["z"]

    if z is None:
        # Too little background to normalise against. Fall back to the raw
        # score with a deliberately strict bar, and say which was used - a
        # number that means two different things depending on the day is worse
        # than two numbers that each mean one thing.
        match = "strong" if raw >= RAW_ALONE else "no" if raw < RAW_FLOOR else "weak"
        return {"match": match, "score": round(raw, 4), "z": None,
                "enrolled": True, "clips": len(record["vectors"]),
                "background": scored["background"], "basis": "raw",
                "note": "A voice match is a convenience signal, never proof of identity."}

    # How far above the background, not how high in absolute terms. The raw
    # score is kept as a floor: a clip can sit comfortably above an unusually
    # uniform background and still not be much like the farmer.
    # Against where this farmer's own clips sat, not against a fixed number.
    # The fixed number cannot exist: an impostor in one set of voices scored
    # +0.67 while a genuine speaker in another scored +0.78, so any single bar
    # either lets the first in or shuts the second out.
    expected = float(record.get("enrol_z") or 0.0)
    accept = (expected - Z_MARGIN) if expected > 0 else Z_ACCEPT
    weak = (expected - Z_WEAK_MARGIN) if expected > 0 else Z_WEAK

    # And close to how well the farmer's own clips agreed with each other. A
    # clip that is further from them than they are from one another is not one
    # of them, however it scores against the background.
    tight = float(record.get("tightness") or 0.0)
    close_enough = raw >= (tight - RAW_MARGIN) if tight > 0 else raw >= RAW_FLOOR

    if z >= accept and close_enough and raw >= RAW_FLOOR:
        match = "strong"
    elif z >= weak and close_enough and raw >= RAW_FLOOR:
        match = "weak"
    else:
        match = "no"
    return {"match": match, "score": round(raw, 4), "z": round(z, 3),
            "enrolled": True, "clips": len(record["vectors"]),
            "background": scored["background"], "basis": "cohort",
            "expected_z": round(expected, 3), "accept_above": round(accept, 3),
            "note": "A voice match is a convenience signal, never proof of identity."}


def _verify_embedding(vector: List[float], record: Dict[str, Any]) -> Dict[str, Any]:
    """The ECAPA answer: one cosine against the farmer's own clips.

    Short, because the embedding does the work the old path needed a cohort,
    a pitch penalty and two separate signals to approximate. The score means
    the same thing for every pair of speakers, so there is nothing to
    normalise - and the one adjustment that still earns its place is the
    farmer's own: a voice whose three clips agreed at 0.55 is not asked to
    reach 0.55 on a different day in a different room.
    """
    best = max((speakermodel.compare(vector, known)
                for known in record.get("vectors") or []), default=0.0)
    tight = float(record.get("tightness") or 0.0)
    accept = accept_threshold(tight, embedding=True)

    if best >= max(accept, EMBEDDING_STRONG):
        match = "strong"
    elif best >= EMBEDDING_SAME:
        match = "weak"
    else:
        match = "no"
    return {"match": match, "score": round(best, 4), "enrolled": True,
            "clips": len(record.get("vectors") or []), "basis": "ecapa-tdnn",
            "accept_above": round(accept, 3),
            "same_speaker_above": EMBEDDING_SAME,
            "note": "A voice match is a convenience signal, never proof of identity."}


def forget(phone: str, voice_id: str = "") -> bool:
    """Delete a voice, or every voice on the account.

    Offered on the dashboard, in the app and by voice. Without a ``voice_id``
    it removes all of them, which is what "remove my voice" meant when there
    could only be one and is still what the voice command asks for.
    """
    ensure_schema()
    removed = 0
    with cursor() as cur:
        if voice_id:
            cur.execute("DELETE FROM voices WHERE voice_id = ? AND phone = ?",
                        (voice_id, phone))
            removed += cur.rowcount
        else:
            cur.execute("DELETE FROM voices WHERE phone = ?", (phone,))
            removed += cur.rowcount
            # The table this was migrated from, so "remove my voice" really
            # removes it. Counted separately: the second DELETE's rowcount
            # replaces the first, and reporting the last statement's count made
            # a successful deletion look like a no-op.
            cur.execute("DELETE FROM voiceprints WHERE phone = ?", (phone,))
            removed += cur.rowcount
    return removed > 0


def restart(phone: str, voice_id: str = "") -> Dict[str, Any]:
    """Throw away the clips and start the three again. Consent is kept.

    "Register again" needed this and did not have it. A farmer with a finished
    print who pressed it had their first new clip compared against two OLD
    ones; a different room or a cold was enough to fail the agreement check,
    and failing it threw the whole working print away. So the one button they
    would press to improve their registration was the one that deleted it.

    Consent survives deliberately: they are not being asked to agree again to
    something they already agreed to, and `enrol` still refuses without it.
    """
    ensure_schema()
    record = get(phone, voice_id)
    if not record:
        return status(phone)
    _write(str(record.get("voice_id")), [], tightness=0.0, done=False,
           language=str(record.get("language") or ""), previous=record)
    return status(phone, voice_id=str(record.get("voice_id")))


def _quality(tightness: float) -> Optional[str]:
    """How well the farmer's own three clips agreed, in a word.

    On the describer's own scale. A cepstral correlation of 0.80 is ordinary
    and an ECAPA cosine of 0.80 is excellent, so one pair of numbers for both
    would have called every good ECAPA registration "fair" and sent farmers
    back to a quiet room for nothing.
    """
    if speakermodel.available():
        return ("good" if tightness >= 0.65
                else "fair" if tightness >= EMBEDDING_CONSISTENT else None)
    return ("good" if tightness >= 0.80
            else "fair" if tightness >= CONSISTENT_FLOOR else None)


def voice_card(record: Dict[str, Any]) -> Dict[str, Any]:
    """One registered voice, as a card on a screen."""
    clips = int(record.get("clips") or 0)
    stale = (bool(record.get("done"))
             and int(record.get("vector_version") or 0) != vector_version())
    done = bool(record.get("done")) and not stale
    tight = float(record.get("tightness") or 0.0)
    return {
        "voice_id": record.get("voice_id"),
        "label": record.get("label") or "",
        "registered": done,
        "started": clips > 0 and not done and not stale,
        "stale": stale,
        "consent": bool(record.get("consent")),
        "consent_at": record.get("consent_at"),
        "clips": clips,
        "need": max(0, MAX_CLIPS - clips),
        "agreement": round(tight, 3) if done else None,
        "accept_above": round(accept_threshold(tight), 3) if done else None,
        "quality": _quality(tight) if done else None,
        "updated_at": record.get("updated_at"),
    }


def auth_enabled(phone: str) -> bool:
    """Is the voice check switched on for this account?

    On by default: a farmer who went to the trouble of registering a voice
    meant it to be used. Switching it off is immediate and deletes nothing.
    """
    ensure_schema()
    with cursor() as cur:
        cur.execute("SELECT enabled FROM voice_settings WHERE phone = ?", (phone,))
        row = cur.fetchone()
    return True if row is None else bool(row[0])


def set_auth(phone: str, enabled: bool) -> bool:
    ensure_schema()
    now = _now()
    with cursor() as cur:
        cur.execute(
            "INSERT INTO voice_settings (phone, enabled, updated_at) VALUES (?,?,?)"
            " ON CONFLICT(phone) DO UPDATE SET enabled = excluded.enabled,"
            " updated_at = excluded.updated_at",
            (phone, 1 if enabled else 0, now))
    return bool(enabled)


def status(phone: str, voice_id: str = "") -> Dict[str, Any]:
    """What to show a farmer about their voices.

    The top-level fields describe ONE voice - the one being registered, or the
    newest finished one - because that is what the registration flow on every
    screen is working on. ``voices`` is the whole list.
    """
    record = get(phone, voice_id)
    everyone = voices_for(phone)
    extras = {
        "voices": [voice_card(r) for r in everyone],
        "voice_count": len(everyone),
        "max_voices": MAX_VOICES,
        "room_for_more": max(0, MAX_VOICES - len(everyone)),
        # The switch is about whether the check RUNS, not about whether a voice
        # exists. Reported either way so a screen can show the toggle before
        # anybody has registered.
        "auth_enabled": auth_enabled(phone),
        "voice_id": record.get("voice_id") if record else None,
        "label": record.get("label") if record else None,
    }
    if not record:
        # The same explanation as the registered case. This is the status a
        # farmer sees while they are DECIDING, which is exactly when knowing
        # what it does and does not do matters most.
        return {**extras, "registered": False, "consent": False, "clips": 0,
                "need": MAX_CLIPS, "room_for": MAX_CLIPS, "done": False,
                "describer": describer(),
                "note": "Saarthi has not been asked to remember your voice. "
                        + EXPLANATION}
    clips = int(record.get("clips") or 0)
    stale = (bool(record.get("done"))
             and int(record.get("vector_version") or 0) != vector_version())
    done = bool(record.get("done")) and not stale
    tight = float(record.get("tightness") or 0.0)
    return {
        **extras,
        # "registered" means FINISHED. A half-done registration reported as
        # registered is how a farmer came to believe their voice was known when
        # one clip of a cough was all that was stored.
        "registered": done,
        "started": clips > 0 and not done and not stale,
        # Registered once, by a version whose numbers cannot be compared with
        # today's. Shown so the farmer is asked to do it again rather than
        # quietly finding that it stopped recognising them.
        "stale": stale,
        "done": done,
        "consent": record.get("consent", False),
        "consent_at": record.get("consent_at"),
        "clips": clips,
        "need": max(0, MAX_CLIPS - clips),
        "room_for": max(0, MAX_CLIPS - clips),
        # How well their own clips agreed, and what a later clip has to beat.
        # Shown because a farmer whose registration is weak should be able to
        # see that and do it again, rather than wonder why it keeps asking.
        "agreement": round(tight, 3) if done else None,
        "accept_above": round(accept_threshold(tight), 3) if done else None,
        "quality": _quality(tight) if done else None,
        # Which description made this print. Shown because it is the honest
        # answer to "how good is this at telling me apart from my brother", and
        # because a print and a describer that disagree is the one state the
        # farmer has to be asked to do something about.
        "describer": describer(),
        "updated_at": record.get("updated_at"),
        "note": EXPLANATION,
    }


# What a refusal sounds like, in the farmer's own language. Short, and it says
# what to do next: the point is not to accuse anybody, it is to stop acting on
# behalf of an account whose owner did not speak.
REFUSAL = {
    "en": ("That did not sound like the registered voice for this account, so I "
           "have stopped. The account holder can speak, or turn voice lock off."),
    "hi": ("Yeh aawaz is account ke registered farmer se mel nahi khati, isliye "
           "maine rok diya. Khud boliye, ya voice lock band kar dijiye."),
}


def gate(phone: str, audio: bytes) -> Dict[str, Any]:
    """May this clip act on this account?

    The rule the whole thing turns on is what happens when it CANNOT tell, and
    the answer is: carry on. Nobody enrolled, a clip too short to compare, a
    weak score - all of these allow the turn. A voiceprint built from band
    statistics and a pitch estimate is a convenience filter, and a convenience
    filter that locks a farmer out of their own helpline because they had a
    cold is worse than no filter at all.

    Only a clear mismatch refuses: enrolled, long enough to compare, and it
    scored below the weak threshold. That is the case the feature exists for -
    somebody else's voice on the farmer's account - and it returns ``allow:
    False`` so the caller cancels the operation rather than carrying it out.
    """
    if not auth_enabled(phone):
        # Switched off by the farmer. Nothing is compared and nothing is
        # refused - and the registration is left exactly where it was, so
        # switching it back on costs nothing.
        return {"match": "unknown", "score": 0.0, "enrolled": enrolled(phone),
                "allow": True, "checked": False,
                "reason": "the voice check is switched off for this account"}

    verdict = verify(phone, audio)
    match = verdict.get("match")
    if match == "no":
        return {**verdict, "allow": False, "reason": "voice did not match",
                "spoken": REFUSAL}
    return {**verdict, "allow": True,
            "reason": {"strong": "voice matched",
                       "weak": "voice probably matched",
                       "unknown": "nothing to compare against"}.get(match, "allowed")}
