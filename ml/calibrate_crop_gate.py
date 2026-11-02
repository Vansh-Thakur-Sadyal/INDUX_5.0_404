"""Choose the crop-mismatch threshold from data, not from taste.

The farmer selects the crop before uploading. If the photo is of a different crop -
or not a leaf the model knows at all - the app must say so instead of reporting a
disease. The signal is the probability mass the model puts on the selected crop's
classes.

This script measures that mass in two situations on the held-out test split:
  * the crop the farmer selected IS the crop in the photo (should be high)
  * the farmer selected some other crop (should be low)
and prints the separation, so the gate in the serving code is a measured number.

    .venv\\Scripts\\python.exe ml\\calibrate_crop_gate.py --limit 1500
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.vision.classifier import classifier          # noqa: E402

MANIFEST = Path(r"V:\Hackathon\INDUX 5.0\Dataset\prepared\manifest.csv")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()

    if not classifier.available:
        raise SystemExit("No model installed.")

    with open(MANIFEST, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["split"] == "test"]
    rng = random.Random(args.seed)
    rng.shuffle(rows)
    rows = rows[:args.limit]

    classes = classifier.status()["class_list"]
    crops = sorted({c.split("__")[0] for c in classes})
    crop_index = defaultdict(list)
    for i, name in enumerate(classes):
        crop_index[name.split("__")[0]].append(i)

    matched, mismatched = [], []
    for row in rows:
        probs = classifier.probabilities(Path(row["path"]).read_bytes())
        if probs is None:
            continue
        true_crop = row["label"].split("__")[0]
        matched.append(float(probs[crop_index[true_crop]].sum()))
        other = rng.choice([c for c in crops if c != true_crop])
        mismatched.append(float(probs[crop_index[other]].sum()))

    matched_arr = np.array(matched)
    mismatched_arr = np.array(mismatched)
    print("images scored:", len(matched_arr))
    print("\ncrop mass when the SELECTED crop is the right one:")
    for q in (5, 10, 25, 50):
        print("   p%-3d %.3f" % (q, np.percentile(matched_arr, q)))
    print("crop mass when the farmer selected the WRONG crop:")
    for q in (50, 75, 90, 95, 99):
        print("   p%-3d %.3f" % (q, np.percentile(mismatched_arr, q)))

    print("\n%-10s %-12s %-12s %s" % ("gate", "correct kept", "wrong caught", "verdict"))
    best = None
    for gate in (0.20, 0.30, 0.40, 0.50, 0.60, 0.70):
        kept = float((matched_arr >= gate).mean())
        caught = float((mismatched_arr < gate).mean())
        score = kept + caught
        print("%-10.2f %-12.3f %-12.3f %s"
              % (gate, kept, caught, "<- best so far" if best is None or score > best[1] else ""))
        if best is None or score > best[1]:
            best = (gate, score, kept, caught)

    print("\nchosen gate %.2f: keeps %.1f%% of correctly-selected crops, catches %.1f%% of "
          "wrong-crop selections" % (best[0], best[2] * 100, best[3] * 100))
    out = {
        "crop_gate": best[0],
        "kept_when_correct": round(best[2], 4),
        "caught_when_wrong": round(best[3], 4),
        "images": len(matched_arr),
        "matched_percentiles": {str(q): round(float(np.percentile(matched_arr, q)), 4)
                                for q in (5, 10, 25, 50)},
        "mismatched_percentiles": {str(q): round(float(np.percentile(mismatched_arr, q)), 4)
                                   for q in (50, 75, 90, 95, 99)},
    }
    path = ROOT / "ml" / "artifacts" / "crop_gate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("written", path)


if __name__ == "__main__":
    main()
