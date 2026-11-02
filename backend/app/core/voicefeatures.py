"""What a voice is, as numbers a comparison can actually use.

This replaced a description made of sixteen log band energies and a pitch. That
one failed in the way that matters most, and it was measurable: two different
men whose pitches were five hertz apart scored **0.896** against each other,
over an accept threshold of 0.84. It let the wrong person in, which is the only
failure this feature has.

The cause was the shape of the score rather than a loose threshold. Pitch was
sixty per cent of it, so anyone with a similar fundamental started at 0.6 before
the spectrum was consulted at all - and sixteen band averages are a poor
description of a vocal tract, so the remaining forty per cent could not tell the
two apart either.

What this computes instead
--------------------------
**Mel-frequency cepstral coefficients.** The standard description of a voice for
sixty years, and for a reason: the cepstrum separates the vocal TRACT (the shape
of a person's throat and mouth, which is what makes them sound like themselves)
from the excitation (the vocal folds, which is pitch). Coefficient zero is
loudness and is dropped - a voice is not a different voice for being recorded
quietly.

**Their spread as well as their average.** Two people can average the same
cepstrum over a sentence and still move through it differently; the standard
deviation per coefficient captures some of that.

**Deltas.** How the cepstrum CHANGES frame to frame - speaking rate and
articulation, which are personal and which a static average throws away.

**Pitch kept separately, as a gate.** It is genuinely informative - a voice an
octave away is a different person, with no further argument needed - but it must
not be allowed to carry the score. It is used to rule out, never to rule in.

What this is still not
----------------------
A trained speaker-verification model. Those are neural networks trained on
thousands of hours, and they are what a bank would use. This is signal
processing with no training data, in a farm advisory tool, deciding whether a
wake word should wake. It is better than what it replaced by a measurable
margin, and it is still a convenience filter - which is why nothing in this
project lets a voice match open an account or move money.
"""
from __future__ import annotations

import math
from typing import Any, List, Optional, Sequence, Tuple

# Frames of 25 ms every 10 ms: the convention, and about right for speech - long
# enough for a stable spectrum, short enough that a vowel does not change inside
# one.
FRAME_SECONDS = 0.025
STEP_SECONDS = 0.010

# 26 mel filters is the usual number for 16 kHz, and 13 coefficients is the
# usual keep. Coefficient 0 is loudness, so 1..12 survive.
MEL_FILTERS = 26
COEFFICIENTS = 13

PRE_EMPHASIS = 0.97

# A voice, in hertz. Below 70 is not a person and above 320 is a whistle.
PITCH_LOW_HZ = 70.0
PITCH_HIGH_HZ = 320.0


def _mel(hz: float) -> float:
    return 2595.0 * math.log10(1.0 + hz / 700.0)


def _hz(mel: float) -> float:
    return 700.0 * (10 ** (mel / 2595.0) - 1.0)


def _filterbank(rate: int, size: int, count: int = MEL_FILTERS):
    """Triangular mel filters, as a matrix to multiply the power spectrum by."""
    import numpy as np

    low, high = _mel(0.0), _mel(rate / 2.0)
    points = [_hz(low + (high - low) * i / (count + 1)) for i in range(count + 2)]
    bins = [int(math.floor((size - 1) * 2 * p / rate)) for p in points]

    bank = np.zeros((count, size))
    for i in range(1, count + 1):
        left, centre, right = bins[i - 1], bins[i], bins[i + 1]
        if centre == left:
            centre = left + 1
        if right <= centre:
            right = centre + 1
        for j in range(left, min(centre, size)):
            bank[i - 1, j] = (j - left) / (centre - left)
        for j in range(centre, min(right, size)):
            bank[i - 1, j] = (right - j) / (right - centre)
    return bank


def _dct(frames, count: int = COEFFICIENTS):
    """DCT-II, which is what turns a log mel spectrum into a cepstrum."""
    import numpy as np

    bands = frames.shape[1]
    k = np.arange(count)[:, None]
    n = np.arange(bands)[None, :]
    basis = np.cos(math.pi * k * (2 * n + 1) / (2 * bands))
    return frames @ basis.T


def cepstra(signal, rate: int):
    """One MFCC vector per frame of speech, or None if there is too little.

    Silent frames are dropped before anything else: a clip that is two thirds
    silence otherwise describes the room, and rooms are very similar to each
    other.
    """
    import numpy as np

    if rate <= 0 or len(signal) < int(rate * 0.3):
        return None

    # Pre-emphasis lifts the high frequencies the glottis rolls off, so the
    # formants that distinguish people are not buried under the fundamental.
    emphasised = np.append(signal[0], signal[1:] - PRE_EMPHASIS * signal[:-1])

    width = max(int(rate * FRAME_SECONDS), 8)
    step = max(int(rate * STEP_SECONDS), 1)
    count = 1 + (len(emphasised) - width) // step
    if count < 10:
        return None

    index = np.arange(width)[None, :] + step * np.arange(count)[:, None]
    frames = emphasised[index] * np.hamming(width)

    energy = np.sqrt((frames ** 2).mean(axis=1))
    speaking = energy > max(energy.mean() * 0.35, 1e-5)
    if speaking.sum() < 10:
        return None
    frames = frames[speaking]

    spectrum = np.abs(np.fft.rfft(frames, n=512)) ** 2 / 512
    bank = _filterbank(rate, spectrum.shape[1])
    melled = np.log(np.maximum(spectrum @ bank.T, 1e-10))
    coefficients = _dct(melled, COEFFICIENTS)

    # Cepstral mean normalisation: subtract the average across the clip. This is
    # what makes the description survive a different microphone or a different
    # line - the channel is roughly constant and lands in the mean, while the
    # speaker is in the variation around it.
    coefficients = coefficients - coefficients.mean(axis=0, keepdims=True)
    return coefficients


def describe(signal, rate: int) -> Optional[List[float]]:
    """The whole description of one clip: cepstral statistics plus pitch."""
    import numpy as np

    frames = cepstra(signal, rate)
    if frames is None:
        return None

    # Coefficient 0 is loudness; a voice is not a different voice for being
    # recorded quietly.
    kept = frames[:, 1:COEFFICIENTS]
    deltas = np.diff(kept, axis=0) if kept.shape[0] > 1 else np.zeros((1, kept.shape[1]))

    shape = np.concatenate([kept.mean(axis=0), kept.std(axis=0),
                            np.abs(deltas).mean(axis=0)])
    low, high = pitch_spread(signal, rate)
    return [float(low), float(high)] + [float(v) for v in shape]


def pitch_spread(signal, rate: int) -> Tuple[float, float]:
    """Median pitch and how much it moves, in hertz. (0, 0) when unvoiced.

    The frame is mean-centred before the autocorrelation, and that one line is
    the difference between this working and not: without it a DC offset
    dominates and a 118 Hz voice measures as 21 Hz.
    """
    import numpy as np

    window = max(int(rate * 0.04), 1)
    usable = len(signal) // window * window
    if usable < window:
        return 0.0, 0.0
    frames = signal[:usable].reshape(-1, window)
    energy = np.sqrt((frames ** 2).mean(axis=1))
    voiced = frames[energy > max(energy.mean() * 0.35, 1e-5)]

    low, high = int(rate / PITCH_HIGH_HZ), int(rate / PITCH_LOW_HZ)
    found: List[float] = []
    for frame in voiced[:300]:
        frame = frame - frame.mean()
        corr = np.correlate(frame, frame, mode="full")[len(frame) - 1:]
        if high >= len(corr) or high <= low:
            continue
        peak = int(np.argmax(corr[low:high])) + low
        if peak > 0 and corr[peak] > corr[0] * 0.35:
            found.append(rate / peak)
    if not found:
        return 0.0, 0.0
    values = np.asarray(found)
    return float(np.median(values)), float(np.percentile(values, 75)
                                            - np.percentile(values, 25))


# ---------------------------------------------------------------- comparing
# How far apart two pitches may be and still be one person.
#
# Proportional rather than flat, because the same number of hertz means
# different things at different pitches: 24 Hz is a fifth of a 118 Hz voice and
# a ninth of a 215 Hz one. Measured here, one speaker's median pitch moved about
# 3 Hz across clips; real speech moves more - ten to twenty between a calm
# sentence and one raised over a tractor - which is what SOFT allows for.
#
# And graded, not a cliff. A single threshold has to be either loose enough to
# let a different man through (a 24 Hz gap was being treated as the same person)
# or tight enough to refuse a farmer who raised their voice. Between SOFT and
# HARD the score is scaled down instead, so a moderate gap weakens the match
# rather than deciding it outright.
PITCH_SOFT_SHARE = 0.12
PITCH_HARD_SHARE = 0.25
PITCH_SOFT_FLOOR_HZ = 12.0


def pitch_penalty(pitch_a: float, spread_a: float,
                  pitch_b: float, spread_b: float) -> float:
    """1.0 when two pitches are compatible, 0.0 when they cannot be one person."""
    if pitch_a <= 0 or pitch_b <= 0:
        return 1.0                      # nothing measurable; let the cepstra decide
    middle = (pitch_a + pitch_b) / 2.0
    # Each voice's own wobble is allowed for before any gap is called real.
    slack = 0.5 * (spread_a + spread_b)
    soft = max(PITCH_SOFT_FLOOR_HZ, middle * PITCH_SOFT_SHARE) + slack
    hard = max(soft + 1.0, middle * PITCH_HARD_SHARE + slack)

    gap = abs(pitch_a - pitch_b)
    if gap <= soft:
        return 1.0
    if gap >= hard:
        return 0.0
    return (hard - gap) / (hard - soft)


def compare(a: Sequence[float], b: Sequence[float]) -> float:
    """How alike two voices are, from 0 to 1.

    The cepstral statistics do the work. Pitch only RULES OUT - a fundamental
    far enough away is a different person and no spectral agreement should
    rescue it - which is the opposite of the arrangement this replaced, where
    pitch was sixty per cent of the score and a similar fundamental carried a
    stranger over the line at 0.896.
    """
    if not a or not b or len(a) != len(b) or len(a) < 4:
        return 0.0

    penalty = pitch_penalty(a[0], a[1], b[0], b[1])
    if penalty <= 0.0:
        return 0.0

    shape_a, shape_b = list(a[2:]), list(b[2:])
    # Correlation rather than a raw cosine: these numbers are already centred
    # per clip, and correlation ignores what is left of overall scale while
    # keeping the pattern, which is the part that belongs to the speaker.
    mean_a = sum(shape_a) / len(shape_a)
    mean_b = sum(shape_b) / len(shape_b)
    da = [x - mean_a for x in shape_a]
    db = [x - mean_b for x in shape_b]
    na = math.sqrt(sum(x * x for x in da)) or 1e-9
    nb = math.sqrt(sum(x * x for x in db)) or 1e-9
    correlation = sum(x * y for x, y in zip(da, db)) / (na * nb)

    # -1..1 onto 0..1. A negative correlation is not "a bit alike", it is
    # unalike, so the bottom half of the range is squashed rather than kept.
    # The pitch penalty then scales what is left: a moderate gap weakens the
    # match rather than deciding it.
    return max(0.0, correlation) * penalty
