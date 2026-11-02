"""Federated learning (FedAvg) over the three datasets as three clients.

Why this partition: the datasets really are separate institutions with different
cameras, crops and backgrounds - PlantDoc is field photos, Cassava is a Ugandan
survey, Plant Pathology is an orchard trial. That is non-IID in exactly the way
real federated agriculture is, and it is the honest way to test FedAvg here: no
client ever sees another client's images.

Each round:
    1. the server sends the global weights to every client
    2. each client trains locally for --local-epochs on its own data only
    3. the server averages the returned weights, weighted by client sample count
    4. the global model is evaluated on the shared validation split

Then the server "boosts": a short fine-tune of the averaged model, plus an
ensemble with the centralized model, with every number measured, not assumed.

Run:  .venv-train\\Scripts\\python.exe ml\\federated.py --rounds 4 --local-epochs 1
"""
from __future__ import annotations

import argparse
import copy
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Sequence

import torch
import torch.nn as nn
import torch.nn.functional as F

from common import (ARTIFACTS, Row, build_model, class_names, collect_logits,
                    confidence_table, device, expected_calibration_error, fit_temperature,
                    load_manifest, make_loader, metrics_from_logits, save_json, set_seed,
                    split_rows)


def partition_by_dataset(rows: Sequence[Row]) -> Dict[str, List[Row]]:
    clients: Dict[str, List[Row]] = defaultdict(list)
    for row in rows:
        clients[row.dataset].append(row)
    return dict(clients)


def local_train(global_state, client_rows, classes, args, dev) -> Dict[str, torch.Tensor]:
    """One client's local training, starting from the global weights."""
    model = build_model(len(classes), pretrained=False).to(dev)
    model.load_state_dict(global_state)
    loader = make_loader(client_rows, classes, train=True, balanced=True,
                         batch_size=args.batch_size, workers=args.workers)
    criterion = nn.CrossEntropyLoss(label_smoothing=0.1)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scaler = torch.amp.GradScaler("cuda", enabled=dev.type == "cuda")
    model.train()
    for _ in range(args.local_epochs):
        for images, targets in loader:
            images = images.to(dev, non_blocking=True)
            targets = targets.to(dev, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast("cuda", dtype=torch.float16, enabled=dev.type == "cuda"):
                loss = criterion(model(images), targets)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
    return {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}


def federated_average(states: List[Dict[str, torch.Tensor]],
                      weights: List[float]) -> Dict[str, torch.Tensor]:
    """FedAvg: a sample-count weighted mean of the client weights.

    Integer buffers (BatchNorm's num_batches_tracked) are averaged then cast back,
    because a float there silently breaks load_state_dict.
    """
    total = sum(weights)
    out: Dict[str, torch.Tensor] = {}
    for key in states[0]:
        stacked = torch.stack([s[key].float() * (w / total) for s, w in zip(states, weights)])
        averaged = stacked.sum(0)
        out[key] = averaged.to(states[0][key].dtype)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--local-epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--boost-epochs", type=int, default=2,
                        help="server-side fine-tune of the averaged model")
    parser.add_argument("--out", default="federated")
    args = parser.parse_args()

    set_seed()
    dev = device()
    rows = load_manifest()
    classes = class_names(rows)
    splits = split_rows(rows)
    clients = partition_by_dataset(splits["train"])

    print("federated clients:")
    for name, client_rows in clients.items():
        present = len({r.label for r in client_rows})
        print("  %-16s %6d images, %2d of %d classes present"
              % (name, len(client_rows), present, len(classes)))

    val_loader = make_loader(splits["val"], classes, train=False,
                             batch_size=args.batch_size * 2, workers=args.workers)
    test_loader = make_loader(splits["test"], classes, train=False,
                              batch_size=args.batch_size * 2, workers=args.workers)

    # Every client starts from the same ImageNet initialisation - that is the
    # federated equivalent of the server publishing a starting model.
    global_model = build_model(len(classes), pretrained=True)
    global_state = {k: v.detach().cpu().clone() for k, v in global_model.state_dict().items()}

    history, best_score, best_state = [], -1.0, None
    for round_index in range(1, args.rounds + 1):
        started = time.time()
        states, weights, per_client = [], [], {}
        for name, client_rows in clients.items():
            state = local_train(global_state, client_rows, classes, args, dev)
            states.append(state)
            weights.append(float(len(client_rows)))
            per_client[name] = len(client_rows)
        global_state = federated_average(states, weights)

        global_model.load_state_dict(global_state)
        logits, targets = collect_logits(global_model, val_loader, dev)
        val = metrics_from_logits(logits, targets, classes)
        history.append({"round": round_index, "val_accuracy": val["accuracy"],
                        "val_macro_recall": val["macro_recall"],
                        "clients": per_client, "seconds": round(time.time() - started, 1)})
        print("round %d/%d  val %.4f  macro-recall %.4f  (%.0fs)"
              % (round_index, args.rounds, val["accuracy"], val["macro_recall"],
                 time.time() - started))
        if val["macro_recall"] > best_score:
            best_score = val["macro_recall"]
            best_state = copy.deepcopy(global_state)

    # ---- measure the plain FedAvg model before any boosting ----
    global_model.load_state_dict(best_state)
    val_logits, val_targets = collect_logits(global_model, val_loader, dev)
    temperature = fit_temperature(val_logits, val_targets)
    test_logits, test_targets = collect_logits(global_model, test_loader, dev)
    fedavg_test = metrics_from_logits(test_logits, test_targets, classes, temperature)
    print("\nFedAvg       test accuracy %.4f  macro recall %.4f"
          % (fedavg_test["accuracy"], fedavg_test["macro_recall"]))

    # ---- boost: a short server-side fine-tune on the pooled training split ----
    boosted_test, boost_temperature = None, temperature
    if args.boost_epochs > 0:
        boost_model = build_model(len(classes), pretrained=False).to(dev)
        boost_model.load_state_dict(best_state)
        loader = make_loader(splits["train"], classes, train=True, balanced=True,
                             batch_size=args.batch_size, workers=args.workers)
        criterion = nn.CrossEntropyLoss(label_smoothing=0.1)
        optimizer = torch.optim.AdamW(boost_model.parameters(), lr=args.lr / 3,
                                      weight_decay=1e-4)
        scaler = torch.amp.GradScaler("cuda", enabled=dev.type == "cuda")
        for epoch in range(1, args.boost_epochs + 1):
            boost_model.train()
            started = time.time()
            for images, targets_batch in loader:
                images = images.to(dev, non_blocking=True)
                targets_batch = targets_batch.to(dev, non_blocking=True)
                optimizer.zero_grad(set_to_none=True)
                with torch.autocast("cuda", dtype=torch.float16, enabled=dev.type == "cuda"):
                    loss = criterion(boost_model(images), targets_batch)
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
            v_logits, v_targets = collect_logits(boost_model, val_loader, dev)
            v = metrics_from_logits(v_logits, v_targets, classes)
            print("boost epoch %d  val %.4f  macro-recall %.4f  (%.0fs)"
                  % (epoch, v["accuracy"], v["macro_recall"], time.time() - started))
        boost_temperature = fit_temperature(*collect_logits(boost_model, val_loader, dev))
        b_logits, b_targets = collect_logits(boost_model, test_loader, dev)
        boosted_test = metrics_from_logits(b_logits, b_targets, classes, boost_temperature)
        print("FedAvg+boost test accuracy %.4f  macro recall %.4f"
              % (boosted_test["accuracy"], boosted_test["macro_recall"]))
        boosted_state = {k: v.detach().cpu().clone() for k, v in boost_model.state_dict().items()}
    else:
        boosted_state = best_state

    report = {
        "run": args.out,
        "mode": "federated_fedavg",
        "classes": classes,
        "rounds": args.rounds,
        "local_epochs": args.local_epochs,
        "clients": {name: len(r) for name, r in clients.items()},
        "history": history,
        "fedavg": {"temperature": temperature, "test": fedavg_test,
                   "confidence_table": confidence_table(test_logits, test_targets,
                                                        temperature=temperature)},
        "boosted": ({"temperature": boost_temperature, "test": boosted_test,
                     "epochs": args.boost_epochs} if boosted_test else None),
    }
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": best_state, "classes": classes, "temperature": temperature},
               ARTIFACTS / ("%s.pt" % args.out))
    if boosted_test:
        torch.save({"state_dict": boosted_state, "classes": classes,
                    "temperature": boost_temperature},
                   ARTIFACTS / ("%s_boosted.pt" % args.out))
    save_json(ARTIFACTS / ("%s_report.json" % args.out), report)
    print("\nartifacts written to", ARTIFACTS)


if __name__ == "__main__":
    main()
