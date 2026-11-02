"""Centralized baseline: all data in one place, one GPU.

This is the control arm. The federated run in ml/federated.py must be compared
against it on the same splits, or the comparison means nothing.

Run:  .venv-train\\Scripts\\python.exe ml\\train_central.py --epochs 6
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch
import torch.nn as nn

from common import (ARTIFACTS, class_names, collect_logits, confidence_table, device,
                    expected_calibration_error, fit_temperature, load_manifest,
                    make_loader, metrics_from_logits, build_model, save_json, set_seed,
                    split_rows)


def run_epoch(model, loader, optimizer, scaler, criterion, dev, scheduler=None):
    model.train()
    total_loss, seen, correct = 0.0, 0, 0
    for images, targets in loader:
        images = images.to(dev, non_blocking=True)
        targets = targets.to(dev, non_blocking=True)
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast("cuda", dtype=torch.float16, enabled=dev.type == "cuda"):
            logits = model(images)
            loss = criterion(logits, targets)
        scaler.scale(loss).backward()
        scaler.step(optimizer)
        scaler.update()
        if scheduler is not None:
            scheduler.step()
        total_loss += loss.item() * targets.size(0)
        correct += logits.argmax(1).eq(targets).sum().item()
        seen += targets.size(0)
    return total_loss / max(seen, 1), correct / max(seen, 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", default="central")
    args = parser.parse_args()

    set_seed()
    dev = device()
    print("device:", dev, torch.cuda.get_device_name(0) if dev.type == "cuda" else "")

    rows = load_manifest()
    classes = class_names(rows)
    splits = split_rows(rows)
    print("classes: %d | train %d | val %d | test %d"
          % (len(classes), len(splits["train"]), len(splits["val"]), len(splits["test"])))

    train_loader = make_loader(splits["train"], classes, train=True, balanced=True,
                               batch_size=args.batch_size, workers=args.workers)
    val_loader = make_loader(splits["val"], classes, train=False,
                             batch_size=args.batch_size * 2, workers=args.workers)
    test_loader = make_loader(splits["test"], classes, train=False,
                              batch_size=args.batch_size * 2, workers=args.workers)

    model = build_model(len(classes)).to(dev)
    criterion = nn.CrossEntropyLoss(label_smoothing=0.1)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        optimizer, max_lr=args.lr, epochs=args.epochs, steps_per_epoch=len(train_loader),
        pct_start=0.25)
    scaler = torch.amp.GradScaler("cuda", enabled=dev.type == "cuda")

    best_score, best_state, history = -1.0, None, []
    for epoch in range(1, args.epochs + 1):
        started = time.time()
        loss, train_acc = run_epoch(model, train_loader, optimizer, scaler, criterion,
                                    dev, scheduler)
        logits, targets = collect_logits(model, val_loader, dev)
        val = metrics_from_logits(logits, targets, classes)
        history.append({"epoch": epoch, "train_loss": round(loss, 4),
                        "train_acc": round(train_acc, 4), "val_accuracy": val["accuracy"],
                        "val_macro_recall": val["macro_recall"],
                        "seconds": round(time.time() - started, 1)})
        print("epoch %d/%d  loss %.3f  train %.3f  val %.3f  macro-recall %.3f  (%.0fs)"
              % (epoch, args.epochs, loss, train_acc, val["accuracy"],
                 val["macro_recall"], time.time() - started))
        # Macro recall, not accuracy: a model that nails cassava mosaic and ignores
        # apple powdery mildew would look fine on accuracy alone.
        if val["macro_recall"] > best_score:
            best_score = val["macro_recall"]
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    model.load_state_dict(best_state)

    val_logits, val_targets = collect_logits(model, val_loader, dev)
    temperature = fit_temperature(val_logits, val_targets)
    test_logits, test_targets = collect_logits(model, test_loader, dev)

    report = {
        "run": args.out,
        "mode": "centralized",
        "classes": classes,
        "epochs": args.epochs,
        "history": history,
        "temperature": temperature,
        "ece_before": expected_calibration_error(test_logits, test_targets),
        "ece_after": expected_calibration_error(test_logits, test_targets, temperature),
        "val": metrics_from_logits(val_logits, val_targets, classes, temperature),
        "test": metrics_from_logits(test_logits, test_targets, classes, temperature),
        "confidence_table": confidence_table(test_logits, test_targets, temperature=temperature),
    }
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": best_state, "classes": classes, "temperature": temperature},
               ARTIFACTS / ("%s.pt" % args.out))
    save_json(ARTIFACTS / ("%s_report.json" % args.out), report)

    print("\ntest accuracy %.4f | macro recall %.4f | temperature %.3f"
          % (report["test"]["accuracy"], report["test"]["macro_recall"], temperature))
    print("calibration error %.4f -> %.4f" % (report["ece_before"], report["ece_after"]))
    for row in report["confidence_table"]:
        print("  threshold %.2f: shows %.1f%% of images, %.2f%% correct when shown"
              % (row["threshold"], row["coverage"] * 100, row["accuracy_when_shown"] * 100))


if __name__ == "__main__":
    main()
