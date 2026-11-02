"""A fast sanity check on the exported model: does it get obvious cases right?

Picks a handful of held-out test images per class and prints what the shipped
classifier says. Run it after every export - it catches a mis-ordered class list
or a broken label map in seconds, which accuracy numbers alone will not show.

    .venv\\Scripts\\python.exe ml\\smoke_test_model.py --per-class 2
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.vision.classifier import classifier          # noqa: E402

MANIFEST = Path(r"V:\Hackathon\INDUX 5.0\Dataset\prepared\manifest.csv")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-class", type=int, default=2)
    parser.add_argument("--seed", type=int, default=3)
    args = parser.parse_args()

    if not classifier.available:
        raise SystemExit("No model installed: %s" % classifier.status().get("reason"))

    with open(MANIFEST, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["split"] == "test"]
    by_class = defaultdict(list)
    for row in rows:
        by_class[row["label"]].append(row)

    rng = random.Random(args.seed)
    hits = total = undecided = 0
    print("%-34s %-34s %7s %s" % ("truth", "predicted", "conf", "shown?"))
    print("-" * 92)
    for label in sorted(by_class):
        for row in rng.sample(by_class[label], min(args.per_class, len(by_class[label]))):
            result = classifier.predict(Path(row["path"]).read_bytes())
            ranked = ([result.top] if result.top else []) + result.alternatives
            top = ranked[0]
            total += 1
            hits += int(top.label == label)
            undecided += int(not result.confident)
            print("%-34s %-34s %6.1f%% %s%s"
                  % (label, top.label, top.probability * 100,
                     "yes" if result.confident else "UNDECIDED",
                     "" if top.label == label else "   <-- wrong"))
    print("-" * 92)
    print("%d/%d correct (%.1f%%), %d withheld as undecided"
          % (hits, total, 100 * hits / max(total, 1), undecided))


if __name__ == "__main__":
    main()
