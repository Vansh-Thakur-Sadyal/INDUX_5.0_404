"""The speaker embedding, from ECAPA-TDNN, with onnxruntime.

What this replaces
------------------
Saarthi's first speaker comparison was thirteen cepstral means and a pitch
estimate. Thirteen numbers describe a *recording* - its room, its handset, its
loudness - far more than they describe a *person*, and it showed: a different
man five hertz away from an enrolled farmer scored 0.896 against their print,
over a threshold of 0.84, and was let in. Cohort normalisation and a pitch
penalty bought enough margin to ship, and no amount of either turns thirteen
numbers into a speaker embedding.

``speechbrain/spkrec-ecapa-voxceleb`` is the model that does the job properly:
ECAPA-TDNN trained on VoxCeleb 1 + 2, 7,205 speakers, 0.80% equal-error rate on
VoxCeleb1-test. It returns 192 numbers in a space where one person's recordings
sit close together and other people's do not, which is the property the old
features never had. ``ml/export_speaker_model.py`` exports it - filterbank,
normalisation and encoder in one graph - and verifies the ONNX output matches
PyTorch before writing the file.

Three things this file is careful about
---------------------------------------
**It is optional.** If the model file is not there, ``available()`` is False and
the caller falls back to the cepstral description. A missing 84 MB file must
degrade voice matching, not break the server.

**It is loaded once, lazily, and only if someone uses it.** The graph is 84 MB
of weights; an account that never registers a voice should not pay for it.

**It never decides anything.** It returns an embedding. What counts as a match
is in ``voiceprint.py``, with the thresholds and the reasoning beside them.

The whole thing is still a convenience filter and not security: a recording of
the farmer played back would pass, and a good synthesis increasingly would too.
That limit is unchanged by a better model and is stated in ``voiceprint.py``.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import PROJECT_ROOT

log = logging.getLogger("saarthi.speakermodel")

MODEL_DIR = PROJECT_ROOT / "data" / "models"
ONNX_PATH = MODEL_DIR / "speaker_ecapa.onnx"
CARD_PATH = MODEL_DIR / "speaker_ecapa.json"

# What the graph was exported for. A clip at any other rate is resampled to it.
SAMPLE_RATE = 16000
EMBEDDING_DIMS = 192

# Below this there is not enough speech for an embedding to mean anything. The
# model will happily return 192 numbers for half a second of a cough.
MIN_SAMPLES = int(1.0 * SAMPLE_RATE)

_lock = threading.Lock()
_session: Any = None
_tried = False


def card() -> Dict[str, Any]:
    """What was shipped, as the export script recorded it."""
    try:
        return json.loads(CARD_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _load() -> Any:
    """The onnxruntime session, or None. Tried once; a failure is not retried.

    Retrying a load that failed once, on every clip, would turn a missing file
    into a stall on a path a farmer is waiting on.
    """
    global _session, _tried
    with _lock:
        if _tried:
            return _session
        _tried = True
        if not ONNX_PATH.exists():
            log.info("no speaker model at %s; voice matching uses the cepstral "
                     "description instead", ONNX_PATH)
            return None
        try:
            import onnxruntime as ort

            options = ort.SessionOptions()
            # One thread. This runs inside a request on a shared box, and a
            # model that grabs every core to save 40 ms is a model that makes
            # every other request wait.
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            _session = ort.InferenceSession(
                str(ONNX_PATH), sess_options=options,
                providers=["CPUExecutionProvider"])
            log.info("speaker model loaded: %s (%.0f MB)",
                     card().get("model") or ONNX_PATH.name,
                     ONNX_PATH.stat().st_size / 1e6)
        except Exception as exc:                        # noqa: BLE001 - never fatal
            log.warning("the speaker model would not load (%s); falling back to "
                        "the cepstral description", exc)
            _session = None
        return _session


def available() -> bool:
    """Is the embedding model usable? Decided by trying to load it, once."""
    return _load() is not None


def _resample(values, rate: int):
    """To 16 kHz, linearly.

    A telephone clip arrives at 8 kHz and the graph wants 16. Interpolating
    does not invent the missing top octave - nothing can - it presents a
    band-limited signal at the rate the model expects, which is exactly what
    telephone speech is. The alternative is refusing every call clip.
    """
    import numpy as np

    if rate == SAMPLE_RATE:
        return np.asarray(values, dtype=np.float32)
    wanted = int(round(len(values) * SAMPLE_RATE / float(rate)))
    if wanted <= 1:
        return np.zeros(0, dtype=np.float32)
    source = np.linspace(0.0, 1.0, num=len(values), dtype=np.float64)
    target = np.linspace(0.0, 1.0, num=wanted, dtype=np.float64)
    return np.interp(target, source, np.asarray(values, dtype=np.float64)
                     ).astype(np.float32)


def embed(values, rate: int) -> Optional[List[float]]:
    """One L2-normalised 192-vector for this clip, or None.

    Normalised here rather than at each comparison, so a cosine is a dot
    product and a stored print cannot be half normalised.
    """
    session = _load()
    if session is None:
        return None
    try:
        import numpy as np

        samples = _resample(values, rate)
        if samples.size < MIN_SAMPLES:
            return None
        out = session.run(["embedding"],
                          {"waveform": samples.reshape(1, -1)})[0]
        vector = np.asarray(out, dtype=np.float64).ravel()
        if vector.size != EMBEDDING_DIMS or not np.all(np.isfinite(vector)):
            return None
        norm = float(np.linalg.norm(vector))
        if norm <= 0:
            return None
        return [float(v) for v in (vector / norm)]
    except Exception as exc:                            # noqa: BLE001 - never fatal
        log.warning("the speaker model failed on a clip: %s", exc)
        return None


def is_embedding(vector: Any) -> bool:
    """Was this vector made by this model?

    By its length, which is how the two descriptions are told apart without a
    second column in the table. Comparing a 192-vector with a 27-number
    cepstral description would be comparing numbers that mean different things.
    """
    return isinstance(vector, (list, tuple)) and len(vector) == EMBEDDING_DIMS


def compare(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two embeddings, from -1 to 1.

    Returned as it is, NOT squashed into 0 to 1. The published decision point
    for this model is a cosine, so keeping the scale means the threshold in
    ``voiceprint.py`` is the published number rather than a transformation of
    it that nobody can check.
    """
    if not is_embedding(a) or not is_embedding(b):
        return 0.0
    total = sum(x * y for x, y in zip(a, b))
    # Both sides are stored normalised, so this IS the cosine. Clamped because
    # float error can put it a hair outside the range.
    return max(-1.0, min(1.0, total))
