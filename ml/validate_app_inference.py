"""Validate the SHIPPED inference path, not the training one.

Training-time metrics are computed with torchvision transforms and PyTorch. The
app uses PIL + numpy + onnxruntime. A silent mismatch between the two (a different
resize, a forgotten normalisation, the temperature applied twice) would quietly
degrade every prediction a farmer sees while every training number still looked
perfect.

So this script re-measures accuracy on the held-out test split by calling the exact
code the server runs - `app.vision.classifier` - and compares it with the numbers in
the model card.

Run with the APP venv, not the training one:
    .venv\\Scripts\\python.exe ml\\validate_app_inference.py --limit 1500
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.vision.classifier import classifier          # noqa: E402

MANIFEST = Path(r"V:\Hackathon\INDUX 5.0\Dataset\prepared\manifest.csv")
CARD = ROOT / "data" / "models" / "leaf_disease.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1500,
                        help="test images to score (the whole split takes a few minutes)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    if not classifier.available:
        raise SystemExit("No model installed: %s" % classifier.status().get("reason"))
    if not MANIFEST.exists():
        raise SystemExit("Missing %s - run ml/prepare_data.py first." % MANIFEST)

    with open(MANIFEST, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["split"] == "test"]
    random.Random(args.seed).shuffle(rows)
    rows = rows[:args.limit]
    print("scoring %d held-out test images through the app's own code path" % len(rows))

    correct = shown = shown_correct = 0
    per_class_total: Counter = Counter()
    per_class_hit: Counter = Counter()
    latencies = []
    confusion = defaultdict(Counter)

    started = time.time()
    for i, row in enumerate(rows, 1):
        image_bytes = Path(row["path"]).read_bytes()
        result = classifier.predict(image_bytes)
        if not result.available:
            raise SystemExit("classifier stopped being available: %s" % result.reason)
        ranked = ([result.top] if result.top else []) + result.alternatives
        predicted = ranked[0].label if ranked else ""
        truth = row["label"]
        per_class_total[truth] += 1
        latencies.append(result.inference_ms)
        if predicted == truth:
            correct += 1
            per_class_hit[truth] += 1
        else:
            confusion[truth][predicted] += 1
        if result.confident:
            shown += 1
            shown_correct += int(result.top.label == truth)
        if i % 250 == 0:
            print("  %4d/%d" % (i, len(rows)))

    n = len(rows)
    accuracy = correct / n
    coverage = shown / n
    precision = (shown_correct / shown) if shown else 0.0
    macro_recall = sum(per_class_hit[c] / per_class_total[c]
                       for c in per_class_total) / len(per_class_total)
    latencies.sort()

    card = json.loads(CARD.read_text(encoding="utf-8"))
    claimed = card["metrics"][card["selected_run"]]
    rationale = card.get("threshold_rationale", {})

    print("\n--- measured through app.vision.classifier (ONNX + PIL + numpy) ---")
    print("accuracy          %.4f   (model card claims %.4f)"
          % (accuracy, claimed["test_accuracy"]))
    print("macro recall      %.4f   (model card claims %.4f)"
          % (macro_recall, claimed["test_macro_recall"]))
    print("coverage          %.4f   (card claims %.4f at threshold %.2f)"
          % (coverage, rationale.get("coverage_at_threshold", 0),
             card["confidence_threshold"]))
    print("accuracy if shown %.4f   (card claims %.4f)"
          % (precision, rationale.get("accuracy_when_shown", 0)))
    print("latency           median %d ms, p95 %d ms (CPU, with flip TTA)"
          % (latencies[len(latencies) // 2], latencies[int(len(latencies) * 0.95)]))

    worst = sorted(((per_class_hit[c] / per_class_total[c], c, per_class_total[c])
                    for c in per_class_total))[:5]
    print("\nweakest classes in this sample:")
    for recall, name, support in worst:
        top_confusion = confusion[name].most_common(1)
        print("  %-34s recall %.2f  (n=%d)%s"
              % (name, recall, support,
                 "  most often called %s" % top_confusion[0][0] if top_confusion else ""))

    # The shipped path must not be materially worse than the training measurement.
    drift = abs(accuracy - claimed["test_accuracy"])
    print("\naccuracy drift vs training measurement: %.4f" % drift)
    if drift > 0.03:
        raise SystemExit("FAIL: the shipped inference path disagrees with training by "
                         "more than 3 points - check preprocessing.")
    print("PASS: the shipped path reproduces the training measurement.")
    print("total %.1fs" % (time.time() - started))


if __name__ == "__main__":
    main()
