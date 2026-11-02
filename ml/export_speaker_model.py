"""Export SpeechBrain's ECAPA-TDNN speaker encoder to ONNX, and prove it works.

Why this exists
---------------
Saarthi's own speaker comparison was 13 MFCC means and a pitch estimate. That
is a description of a *recording* - its room, its microphone, its loudness -
far more than of a *person*, and it showed: a different man five hertz away
scored 0.896 against a farmer's print and was let in. Cohort normalisation and
a pitch penalty bought enough margin to ship, and no amount of either turns
thirteen numbers into a speaker embedding.

ECAPA-TDNN is the model that does. `speechbrain/spkrec-ecapa-voxceleb` is
trained on VoxCeleb 1 + 2 (7,205 speakers) with additive-margin softmax and
reports 0.80% EER on VoxCeleb1-test. It returns a 192-dimensional embedding in
which the same person's recordings sit close together and other people's do
not, which is the property the old features never had.

What is exported
----------------
The whole front end, not just the network: 80-channel log-mel filterbanks,
SpeechBrain's own sentence-level mean-variance normalisation, then the encoder.
The graph therefore takes a RAW 16 kHz waveform and returns one 192-vector, so
nothing about the feature pipeline has to be reimplemented on the server - and
reimplementing it is exactly where a port like this silently goes wrong. The
server needs onnxruntime, which it already has, and not PyTorch, which would
add about 2 GB to the image for one 20 MB model.

What is verified before anything is written
-------------------------------------------
1. the ONNX graph reproduces the PyTorch embedding (cosine >= 0.9999)
2. two clips of one synthetic speaker score higher than two of different ones
3. the margin between those two populations is measured and written into the
   card beside the model, because the thresholds in `voiceprint.py` are chosen
   from it rather than from taste

Run:  .venv\\Scripts\\python.exe ml\\export_speaker_model.py
"""
from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "data" / "models"
ONNX_PATH = MODEL_DIR / "speaker_ecapa.onnx"
CARD_PATH = MODEL_DIR / "speaker_ecapa.json"
CACHE = ROOT / "ml" / "artifacts" / "ecapa"

SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
SAMPLE_RATE = 16000
EMBEDDING_DIMS = 192


# --------------------------------------------------------------- the wrapper
class SpeakerEncoder(torch.nn.Module):
    """Waveform in, one embedding out.

    SpeechBrain keeps the three stages apart - `mods.compute_features`,
    `mods.mean_var_norm`, `mods.embedding_model` - and a caller that forgets the
    middle one gets embeddings that look plausible and compare badly. Wrapping
    all three means the ONNX graph cannot be used wrongly.
    """

    def __init__(self, classifier):
        super().__init__()
        self.compute_features = classifier.mods.compute_features
        self.mean_var_norm = classifier.mods.mean_var_norm
        self.embedding_model = classifier.mods.embedding_model

    def forward(self, wav: torch.Tensor) -> torch.Tensor:
        # (batch, samples) -> (batch, frames, 80)
        features = self.compute_features(wav)
        lengths = torch.ones(wav.shape[0], dtype=wav.dtype, device=wav.device)
        features = self.mean_var_norm(features, lengths)
        embedding = self.embedding_model(features, lengths)
        # (batch, 1, 192) -> (batch, 192)
        return embedding.squeeze(1)


def load_encoder() -> SpeakerEncoder:
    from speechbrain.inference.speaker import EncoderClassifier

    classifier = EncoderClassifier.from_hparams(
        source=SOURCE, savedir=str(CACHE), run_opts={"device": "cpu"})
    model = SpeakerEncoder(classifier).eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    return model


# ----------------------------------------------------------- synthetic voices
# The same crude synthesiser the voiceprint tests use: a glottal buzz through
# fixed formants. It is not speech, and the margin measured on it is therefore a
# floor rather than a promise - which is why the card says so.
def voice(pitch: float, formants, seconds: float = 3.0, seed: int = 1,
          loudness: float = 1.0) -> np.ndarray:
    rng = random.Random(seed)
    frames = int(SAMPLE_RATE * seconds)
    out = np.empty(frames, dtype=np.float32)
    for i in range(frames):
        t = i / SAMPLE_RATE
        buzz = sum(math.sin(2 * math.pi * pitch * h * t) / h for h in range(1, 12))
        shaped = sum(math.sin(2 * math.pi * f * t) * a for f, a in formants)
        out[i] = (loudness * 0.45 * (buzz * 0.6 + shaped * 0.4)
                  + 0.01 * rng.uniform(-1, 1))
    return out


SPEAKERS = {
    "ramesh": dict(pitch=112.0, formants=[(520, .5), (1180, .32), (2500, .14)]),
    "sunita": dict(pitch=212.0, formants=[(780, .5), (1850, .32), (3100, .14)]),
    # The case that defeated the old matcher outright: another man, five hertz
    # away, with formants a little higher.
    "mohan": dict(pitch=117.0, formants=[(560, .5), (1240, .32), (2580, .14)]),
}


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float64).ravel()
    b = b.astype(np.float64).ravel()
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denominator) if denominator else 0.0


def measure(run) -> Dict[str, object]:
    """Same-speaker scores against different-speaker scores, and the gap."""
    prints: Dict[str, List[np.ndarray]] = {}
    for name, spec in SPEAKERS.items():
        prints[name] = [run(voice(seed=seed, **spec)) for seed in (1, 2, 3)]

    same, other = [], []
    names = list(prints)
    for name in names:
        clips = prints[name]
        for i in range(len(clips)):
            for j in range(i + 1, len(clips)):
                same.append(cosine(clips[i], clips[j]))
    for i, one in enumerate(names):
        for two in names[i + 1:]:
            for a in prints[one]:
                for b in prints[two]:
                    other.append(cosine(a, b))

    return {
        "same_speaker_lowest": round(min(same), 4),
        "same_speaker_mean": round(float(np.mean(same)), 4),
        "different_speaker_highest": round(max(other), 4),
        "different_speaker_mean": round(float(np.mean(other)), 4),
        "margin": round(min(same) - max(other), 4),
        "pairs": {"same": len(same), "different": len(other)},
    }


# --------------------------------------------------------------------- export
def main() -> int:
    # The exporter prints a tick on success, and a Windows console in cp1252
    # raises on it - so a successful export ended in a UnicodeEncodeError
    # traceback that looked exactly like a failed one.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):                 # pragma: no cover
            pass

    parser = argparse.ArgumentParser()
    parser.add_argument("--opset", type=int, default=18,
                        help="18 is the lowest this torch has implementations for")
    parser.add_argument("--seconds", type=float, default=3.0,
                        help="length of the tracing example; the axis stays dynamic")
    args = parser.parse_args()

    print("loading %s (this downloads about 20 MB the first time)" % SOURCE)
    model = load_encoder()

    example = torch.from_numpy(voice(seconds=args.seconds, **SPEAKERS["ramesh"]))
    example = example.unsqueeze(0)

    with torch.no_grad():
        reference = model(example).numpy()
    if reference.shape != (1, EMBEDDING_DIMS):
        print("unexpected embedding shape %r" % (reference.shape,), file=sys.stderr)
        return 1
    print("pytorch embedding: %r" % (reference.shape,))

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    staged = ONNX_PATH.with_suffix(".onnx.new")
    print("exporting to ONNX (opset %d)" % args.opset)
    torch.onnx.export(
        model, (example,), str(staged),
        input_names=["waveform"], output_names=["embedding"],
        # Only the sample count varies. Batching is not wanted: a clip is a
        # clip, and a dynamic batch axis would let a caller hand in a ragged
        # pair whose shorter half is padded with silence it never recorded.
        dynamic_axes={"waveform": {1: "samples"}},
        opset_version=args.opset, do_constant_folding=True,
        # One file, weights included.
        #
        # Left to itself the exporter wrote the weights to a sibling
        # `<name>.data` and the graph referenced it BY FILE NAME - so renaming
        # the graph from its staged name into place left it pointing at a file
        # that no longer existed, and the model loaded on this machine only
        # because the staged copy happened to still be beside it.
        external_data=False)

    import onnxruntime as ort

    session = ort.InferenceSession(str(staged), providers=["CPUExecutionProvider"])

    def run(samples: np.ndarray) -> np.ndarray:
        wav = np.asarray(samples, dtype=np.float32).reshape(1, -1)
        return session.run(["embedding"], {"waveform": wav})[0]

    agreement = cosine(reference, run(example.numpy()[0]))
    print("onnx vs pytorch cosine: %.6f" % agreement)
    if agreement < 0.9999:
        print("the ONNX graph does not reproduce the PyTorch embedding; "
              "nothing was shipped", file=sys.stderr)
        staged.unlink(missing_ok=True)
        return 1

    # A different length, because a graph that only works at the traced length
    # is a graph that fails on the first real clip.
    longer = run(voice(seconds=4.5, **SPEAKERS["ramesh"]))
    if longer.shape != (1, EMBEDDING_DIMS):
        print("the graph did not accept a different length", file=sys.stderr)
        staged.unlink(missing_ok=True)
        return 1

    print("measuring the margin on synthetic speakers")
    scores = measure(run)
    for key, value in scores.items():
        print("  %-26s %s" % (key, value))
    if scores["margin"] <= 0:
        print("same-speaker and different-speaker scores overlap; not shipping",
              file=sys.stderr)
        staged.unlink(missing_ok=True)
        return 1

    shutil.move(str(staged), str(ONNX_PATH))
    card = {
        "model": SOURCE,
        "architecture": "ECAPA-TDNN",
        "trained_on": "VoxCeleb 1 + VoxCeleb 2 (7205 speakers)",
        "published_eer_voxceleb1_test_percent": 0.80,
        "embedding_dims": EMBEDDING_DIMS,
        "sample_rate": SAMPLE_RATE,
        "input": "raw mono waveform, float32, shape (1, samples)",
        "includes": ["80-channel log-mel filterbank",
                     "sentence-level mean-variance normalisation",
                     "ECAPA-TDNN encoder"],
        "opset": args.opset,
        "onnx_vs_pytorch_cosine": round(agreement, 6),
        "measured_on_synthetic_voices": scores,
        "limitation": ("The margin above was measured on synthesised voices, "
                       "not on telephone audio. It is a floor, not a promise: "
                       "real recordings carry room, handset and codec "
                       "differences this synthesiser does not reproduce."),
        "bytes": ONNX_PATH.stat().st_size,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    CARD_PATH.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
    print("wrote %s (%.1f MB) and %s"
          % (ONNX_PATH, ONNX_PATH.stat().st_size / 1e6, CARD_PATH.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
