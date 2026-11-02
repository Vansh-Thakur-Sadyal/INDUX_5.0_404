"""Pick the best trained model, prove it, and export it for the app.

The app must not carry PyTorch: the VPS image would grow by ~2 GB for one 5 MB
model. So the winner is exported to ONNX and served with onnxruntime, and this
script verifies the ONNX graph reproduces the PyTorch logits before shipping it.

It also decides the production confidence threshold from measured data rather
than taste: the smallest cut-off whose accuracy-when-shown clears --min-precision.

Run:  .venv-train\\Scripts\\python.exe ml\\export_onnx.py
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import torch

from common import (ARTIFACTS, IMAGE_SIZE, MEAN, STD, build_model, class_names,
                    collect_logits, confidence_table, device, load_manifest, make_loader,
                    metrics_from_logits, save_json, set_seed, split_rows)
from labels import pretty

MODEL_DIR = Path(__file__).resolve().parents[1] / "data" / "models"
CANDIDATES = ["central", "federated", "federated_boosted"]


def load_candidate(name: str):
    path = ARTIFACTS / ("%s.pt" % name)
    if not path.exists():
        return None
    blob = torch.load(path, map_location="cpu", weights_only=False)
    return blob


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-precision", type=float, default=0.90,
                        help="required accuracy among the predictions actually shown")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    set_seed()
    dev = device()
    rows = load_manifest()
    classes = class_names(rows)
    splits = split_rows(rows)
    val_loader = make_loader(splits["val"], classes, train=False, batch_size=128,
                             workers=args.workers)
    test_loader = make_loader(splits["test"], classes, train=False, batch_size=128,
                              workers=args.workers)

    # ---- compare every candidate on the same held-out splits ----
    comparison: Dict[str, Dict] = {}
    best_name, best_score, best_blob = None, -1.0, None
    for name in CANDIDATES:
        blob = load_candidate(name)
        if blob is None:
            continue
        model = build_model(len(blob["classes"]), pretrained=False)
        model.load_state_dict(blob["state_dict"])
        temperature = float(blob.get("temperature", 1.0))
        val_logits, val_targets = collect_logits(model, val_loader, dev)
        test_logits, test_targets = collect_logits(model, test_loader, dev)
        val = metrics_from_logits(val_logits, val_targets, classes, temperature)
        test = metrics_from_logits(test_logits, test_targets, classes, temperature)
        comparison[name] = {
            "val_macro_recall": val["macro_recall"], "val_accuracy": val["accuracy"],
            "test_macro_recall": test["macro_recall"], "test_accuracy": test["accuracy"],
            "temperature": temperature,
            "confidence_table": confidence_table(test_logits, test_targets,
                                                 temperature=temperature),
            "per_class": test["per_class"],
        }
        print("%-20s val macro-recall %.4f | test accuracy %.4f | test macro-recall %.4f"
              % (name, val["macro_recall"], test["accuracy"], test["macro_recall"]))
        # Selection on VALIDATION only; the test split stays an untouched referee.
        if val["macro_recall"] > best_score:
            best_name, best_score, best_blob = name, val["macro_recall"], blob

    if best_blob is None:
        raise SystemExit("No trained checkpoints in %s - run the training scripts first."
                         % ARTIFACTS)
    print("\nselected:", best_name)

    chosen = comparison[best_name]
    threshold, coverage, precision = 0.9, 0.0, 0.0
    for row in chosen["confidence_table"]:
        if row["accuracy_when_shown"] >= args.min_precision:
            threshold = row["threshold"]
            coverage = row["coverage"]
            precision = row["accuracy_when_shown"]
            break
    print("confidence threshold %.2f -> shows %.1f%% of images, %.1f%% correct when shown"
          % (threshold, coverage * 100, precision * 100))

    # ---- export ----
    model = build_model(len(classes), pretrained=False)
    model.load_state_dict(best_blob["state_dict"])
    model.eval()
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    onnx_path = MODEL_DIR / "leaf_disease.onnx"
    dummy = torch.randn(1, 3, IMAGE_SIZE, IMAGE_SIZE)
    torch.onnx.export(
        model, dummy, str(onnx_path), input_names=["image"], output_names=["logits"],
        dynamic_axes={"image": {0: "batch"}, "logits": {0: "batch"}},
        # external_data=False keeps the weights INSIDE the .onnx file. The default
        # writes a 16 MB .onnx.data sidecar, and a model split across two files that
        # must stay together is a deployment accident waiting to happen.
        external_data=False,
        # No opset pin: torch 2.11 exports at a newer opset and its down-converter
        # fails on EfficientNet's squeeze nodes. onnxruntime >=1.17 reads the default.
    )

    # ---- prove the exported graph matches PyTorch before shipping it ----
    import onnxruntime as ort                                   # noqa: WPS433
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    probe = torch.randn(4, 3, IMAGE_SIZE, IMAGE_SIZE)
    with torch.no_grad():
        torch_logits = model(probe).numpy()
    onnx_logits = session.run(["logits"], {"image": probe.numpy()})[0]
    max_difference = float(np.abs(torch_logits - onnx_logits).max())
    agree = int((torch_logits.argmax(1) == onnx_logits.argmax(1)).sum())
    print("ONNX check: max logit difference %.6f, %d/4 predictions identical"
          % (max_difference, agree))
    if max_difference > 1e-3 or agree != 4:
        raise SystemExit("ONNX export does not reproduce the PyTorch model - not shipping it.")

    summary = json.loads((Path(r"V:\Hackathon\INDUX 5.0\Dataset\prepared") /
                          "summary.json").read_text(encoding="utf-8"))
    card = {
        "name": "saarthi-leaf-disease",
        "architecture": "efficientnet_b0",
        "input": {"size": IMAGE_SIZE, "layout": "NCHW", "mean": MEAN, "std": STD,
                  "range": "0-1 before normalisation"},
        "selected_run": best_name,
        "selection_metric": "validation macro recall",
        "temperature": float(best_blob.get("temperature", 1.0)),
        "confidence_threshold": threshold,
        "threshold_rationale": {
            "min_precision_required": args.min_precision,
            "coverage_at_threshold": coverage,
            "accuracy_when_shown": precision,
        },
        "classes": classes,
        "class_display": {c: pretty(c) for c in classes},
        "metrics": {k: {m: v[m] for m in ("val_accuracy", "val_macro_recall",
                                          "test_accuracy", "test_macro_recall")}
                    for k, v in comparison.items()},
        "confidence_table": chosen["confidence_table"],
        "per_class_test": chosen["per_class"],
        "training_data": {
            "images": summary["images"], "classes": summary["classes"],
            "splits": summary["splits"], "by_dataset": summary["by_dataset"],
            "sources": ["PlantDoc", "Cassava Leaf Disease (Kaggle)",
                        "Plant Pathology 2021 FGVC8 (Kaggle)"],
            "excluded": {"multi_label_or_complex": summary["plant_pathology_dropped"],
                         "classes_under_60_images": summary["dropped_small_classes"]},
        },
        "onnx_check": {"max_logit_difference": max_difference, "argmax_agreement": agree},
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "limits": [
            "Screening aid only. It reports a likely condition from one photo; it is not "
            "a diagnosis and it has not been validated on Indian field conditions.",
            "It only knows the %d classes listed here; anything else will be forced into "
            "one of them, which is why the confidence threshold exists." % len(classes),
            "Trained mostly on apple and cassava leaves, so other crops have far less "
            "support - check per_class_test before trusting a specific class.",
        ],
    }
    save_json(MODEL_DIR / "leaf_disease.json", card)
    save_json(ARTIFACTS / "model_comparison.json", comparison)

    size_mb = onnx_path.stat().st_size / 1e6
    print("\nexported %s (%.1f MB) + leaf_disease.json" % (onnx_path, size_mb))


if __name__ == "__main__":
    main()
