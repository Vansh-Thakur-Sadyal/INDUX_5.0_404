"""Shared pieces for centralized and federated training: data, model, evaluation.

Keeping these in one place is what makes the two training runs comparable - the
federated result is only meaningful if it used the same splits, the same
augmentation and the same architecture as the centralized baseline.
"""
from __future__ import annotations

import csv
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image, ImageFile
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
from torchvision import transforms
from torchvision.models import efficientnet_b0, EfficientNet_B0_Weights

ImageFile.LOAD_TRUNCATED_IMAGES = True

DATASET_ROOT = Path(r"V:\Hackathon\INDUX 5.0\Dataset")
PREPARED = DATASET_ROOT / "prepared"
MANIFEST = PREPARED / "manifest.csv"
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
IMAGE_SIZE = 224
MEAN = [0.485, 0.456, 0.406]
STD = [0.229, 0.224, 0.225]
SEED = 1337


def set_seed(seed: int = SEED) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def device() -> torch.device:
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


# ------------------------------------------------------------------ data
@dataclass
class Row:
    path: str
    label: str
    dataset: str
    split: str


def load_manifest() -> List[Row]:
    if not MANIFEST.exists():
        raise SystemExit("Run ml/prepare_data.py first - %s is missing." % MANIFEST)
    with open(MANIFEST, newline="", encoding="utf-8") as fh:
        return [Row(**row) for row in csv.DictReader(fh)]


def class_names(rows: Sequence[Row]) -> List[str]:
    return sorted({r.label for r in rows})


def build_transforms(train: bool) -> transforms.Compose:
    if train:
        # Field photos are hand-held: rotation, colour and scale jitter matter more
        # than exotic augmentation. RandomErasing stands in for leaf occlusion.
        return transforms.Compose([
            transforms.RandomResizedCrop(IMAGE_SIZE, scale=(0.6, 1.0), ratio=(0.75, 1.33)),
            transforms.RandomHorizontalFlip(),
            transforms.RandomVerticalFlip(p=0.2),
            transforms.RandomRotation(20),
            transforms.ColorJitter(brightness=0.25, contrast=0.25, saturation=0.25, hue=0.03),
            transforms.ToTensor(),
            transforms.Normalize(MEAN, STD),
            transforms.RandomErasing(p=0.25, scale=(0.02, 0.12)),
        ])
    return transforms.Compose([
        transforms.Resize(int(IMAGE_SIZE * 1.14)),
        transforms.CenterCrop(IMAGE_SIZE),
        transforms.ToTensor(),
        transforms.Normalize(MEAN, STD),
    ])


class LeafDataset(Dataset):
    def __init__(self, rows: Sequence[Row], classes: Sequence[str], train: bool) -> None:
        self.rows = list(rows)
        self.index = {name: i for i, name in enumerate(classes)}
        self.tf = build_transforms(train)

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, i: int):
        row = self.rows[i]
        with Image.open(row.path) as im:
            image = im.convert("RGB")
        return self.tf(image), self.index[row.label]


def make_loader(rows: Sequence[Row], classes: Sequence[str], *, train: bool,
                batch_size: int = 64, workers: int = 8,
                balanced: bool = False) -> DataLoader:
    dataset = LeafDataset(rows, classes, train=train)
    sampler = None
    if balanced and train:
        # Cassava mosaic has 13k images, apple powdery mildew a few hundred. Without
        # balancing the model learns the majority classes and quietly ignores the rest.
        counts = Counter(r.label for r in rows)
        weights = [1.0 / counts[r.label] for r in rows]
        sampler = WeightedRandomSampler(weights, num_samples=len(rows), replacement=True)
    return DataLoader(
        dataset, batch_size=batch_size, shuffle=(train and sampler is None),
        sampler=sampler, num_workers=workers, pin_memory=True,
        persistent_workers=workers > 0, drop_last=train,
    )


# ----------------------------------------------------------------- model
def build_model(num_classes: int, pretrained: bool = True) -> nn.Module:
    """EfficientNet-B0: 5.3M parameters, strong on leaf datasets, and small enough
    to export to ONNX and run on the VPS CPU in well under a second."""
    weights = EfficientNet_B0_Weights.IMAGENET1K_V1 if pretrained else None
    model = efficientnet_b0(weights=weights)
    in_features = model.classifier[1].in_features
    model.classifier = nn.Sequential(nn.Dropout(0.3), nn.Linear(in_features, num_classes))
    return model


def state_to_cpu(model: nn.Module) -> Dict[str, torch.Tensor]:
    return {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}


# ------------------------------------------------------------ evaluation
@torch.no_grad()
def collect_logits(model: nn.Module, loader: DataLoader,
                   dev: torch.device) -> Tuple[torch.Tensor, torch.Tensor]:
    model.eval().to(dev)
    all_logits, all_targets = [], []
    for images, targets in loader:
        images = images.to(dev, non_blocking=True)
        with torch.autocast("cuda", dtype=torch.float16, enabled=dev.type == "cuda"):
            logits = model(images)
        all_logits.append(logits.float().cpu())
        all_targets.append(targets)
    return torch.cat(all_logits), torch.cat(all_targets)


def metrics_from_logits(logits: torch.Tensor, targets: torch.Tensor,
                        classes: Sequence[str],
                        temperature: float = 1.0) -> Dict[str, object]:
    probs = F.softmax(logits / temperature, dim=1)
    confidence, predicted = probs.max(dim=1)
    correct = predicted.eq(targets)
    top5 = logits.topk(min(5, len(classes)), dim=1).indices.eq(targets[:, None]).any(1)

    per_class: Dict[str, Dict[str, float]] = {}
    for i, name in enumerate(classes):
        mask = targets.eq(i)
        if mask.sum() == 0:
            continue
        tp = (correct & mask).sum().item()
        predicted_i = predicted.eq(i)
        per_class[name] = {
            "support": int(mask.sum().item()),
            "recall": round(tp / max(int(mask.sum().item()), 1), 4),
            "precision": round(tp / max(int(predicted_i.sum().item()), 1), 4),
        }
    macro_recall = float(np.mean([v["recall"] for v in per_class.values()])) if per_class else 0.0

    return {
        "accuracy": round(correct.float().mean().item(), 4),
        "top5_accuracy": round(top5.float().mean().item(), 4),
        "macro_recall": round(macro_recall, 4),
        "mean_confidence": round(confidence.mean().item(), 4),
        "per_class": per_class,
        "n": int(targets.numel()),
    }


def confidence_table(logits: torch.Tensor, targets: torch.Tensor,
                     thresholds: Sequence[float] = (0.5, 0.6, 0.7, 0.8, 0.9),
                     temperature: float = 1.0) -> List[Dict[str, float]]:
    """Accuracy and coverage at each confidence cut-off.

    This is the table that decides the production threshold: the app only shows a
    prediction it is confident about, and silence is better than a wrong diagnosis.
    """
    probs = F.softmax(logits / temperature, dim=1)
    confidence, predicted = probs.max(dim=1)
    correct = predicted.eq(targets)
    rows = []
    for threshold in thresholds:
        kept = confidence >= threshold
        coverage = kept.float().mean().item()
        accuracy = correct[kept].float().mean().item() if kept.any() else 0.0
        rows.append({
            "threshold": threshold,
            "coverage": round(coverage, 4),
            "accuracy_when_shown": round(accuracy, 4),
            "shown": int(kept.sum().item()),
            "wrong_shown": int((kept & ~correct).sum().item()),
        })
    return rows


def fit_temperature(logits: torch.Tensor, targets: torch.Tensor) -> float:
    """Temperature scaling on the validation split.

    A fine-tuned CNN is over-confident: it says 0.99 when it is right 90% of the
    time. Since the UI shows the number and the threshold gates on it, the
    probability has to mean something.
    """
    log_t = torch.zeros(1, requires_grad=True)
    optimizer = torch.optim.LBFGS([log_t], lr=0.1, max_iter=60)

    def closure():
        optimizer.zero_grad()
        loss = F.cross_entropy(logits / log_t.exp(), targets)
        loss.backward()
        return loss

    optimizer.step(closure)
    return round(float(log_t.exp().item()), 4)


def expected_calibration_error(logits: torch.Tensor, targets: torch.Tensor,
                               temperature: float = 1.0, bins: int = 15) -> float:
    probs = F.softmax(logits / temperature, dim=1)
    confidence, predicted = probs.max(dim=1)
    correct = predicted.eq(targets).float()
    edges = torch.linspace(0, 1, bins + 1)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (confidence > lo) & (confidence <= hi)
        if mask.sum() == 0:
            continue
        ece += (mask.float().mean() * (correct[mask].mean() - confidence[mask].mean()).abs()).item()
    return round(ece, 4)


def save_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def split_rows(rows: Sequence[Row]) -> Dict[str, List[Row]]:
    out: Dict[str, List[Row]] = defaultdict(list)
    for row in rows:
        out[row.split].append(row)
    return out
