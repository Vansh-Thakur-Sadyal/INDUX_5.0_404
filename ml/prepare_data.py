"""Turn three downloaded datasets into one training-ready corpus.

Plant Pathology ships 4000x2672 originals: 16 GB for 18k images. Decoding those
every epoch makes the GPU wait on the disk, so every image is resized once to a
short side of 320 px and written as quality-88 JPEG. The corpus drops from ~22 GB
to roughly 1.5 GB and an epoch becomes GPU-bound instead of disk-bound.

Outputs
    Dataset/prepared/<label>/<dataset>_<n>.jpg
    Dataset/prepared/manifest.csv      path,label,dataset,split
    Dataset/prepared/summary.json      class counts, dropped rows and why

Run:  .venv-train\\Scripts\\python.exe ml\\prepare_data.py
"""
from __future__ import annotations

import csv
import json
import random
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from labels import (UnknownLabel, map_cassava, map_plant_pathology,  # noqa: E402
                    map_plantdoc)

from PIL import Image, ImageFile                                     # noqa: E402

ImageFile.LOAD_TRUNCATED_IMAGES = True

DATASET_ROOT = Path(r"V:\Hackathon\INDUX 5.0\Dataset")
EXTRACTED = DATASET_ROOT / "extracted"
PREPARED = DATASET_ROOT / "prepared"

SHORT_SIDE = 320
JPEG_QUALITY = 88
MIN_PER_CLASS = 60          # below this a class cannot be learned or evaluated
SPLITS = (("train", 0.70), ("val", 0.15), ("test", 0.15))
SEED = 1337


def collect_plantdoc() -> List[Tuple[Path, str]]:
    root = EXTRACTED / "PlantDoc-Dataset-master" / "PlantDoc-Dataset-master"
    if not root.exists():
        root = EXTRACTED / "PlantDoc-Dataset-master"
    records: List[Tuple[Path, str]] = []
    unknown: Counter = Counter()
    for split in ("train", "test"):
        split_dir = root / split
        if not split_dir.is_dir():
            continue
        for class_dir in sorted(split_dir.iterdir()):
            if not class_dir.is_dir():
                continue
            try:
                label = map_plantdoc(class_dir.name)
            except UnknownLabel:
                unknown[class_dir.name] += 1
                continue
            for image in class_dir.iterdir():
                if image.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    records.append((image, label))
    if unknown:
        print("  ! unmapped PlantDoc folders (skipped):", dict(unknown))
    return records


def collect_cassava() -> List[Tuple[Path, str]]:
    root = EXTRACTED / "cassava-leaf-disease-classification"
    csv_path, images = root / "train.csv", root / "train_images"
    if not csv_path.exists():
        return []
    records: List[Tuple[Path, str]] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            path = images / row["image_id"]
            if path.exists():
                records.append((path, map_cassava(int(row["label"]))))
    return records


def collect_plant_pathology() -> Tuple[List[Tuple[Path, str]], Dict[str, int]]:
    root = EXTRACTED / "plant-pathology-2021-fgvc8"
    csv_path, images = root / "train.csv", root / "train_images"
    if not csv_path.exists():
        return [], {}
    records: List[Tuple[Path, str]] = []
    dropped = Counter()
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            label = map_plant_pathology(row["labels"])
            if label is None:
                dropped["multi_label_or_complex"] += 1
                continue
            path = images / row["image"]
            if path.exists():
                records.append((path, label))
            else:
                dropped["file_missing"] += 1
    return records, dict(dropped)


def _resize_one(args: Tuple[str, str, str]) -> Optional[Tuple[str, str]]:
    src, dest, _label = args
    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            w, h = im.size
            scale = SHORT_SIDE / min(w, h)
            if scale < 1:
                im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                               Image.BICUBIC)
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, "JPEG", quality=JPEG_QUALITY, optimize=True)
        return dest, _label
    except Exception as exc:                                    # noqa: BLE001
        print("  ! skipped %s (%s)" % (src, str(exc)[:80]))
        return None


def main() -> None:
    random.seed(SEED)
    print("Collecting source records...")
    sources: Dict[str, List[Tuple[Path, str]]] = {}
    sources["plantdoc"] = collect_plantdoc()
    sources["cassava"] = collect_cassava()
    pp_records, pp_dropped = collect_plant_pathology()
    sources["plant_pathology"] = pp_records
    for name, rows in sources.items():
        print("  %-16s %6d images, %2d classes" % (name, len(rows), len({r[1] for r in rows})))
    if pp_dropped:
        print("  plant_pathology dropped:", pp_dropped)

    counts = Counter(label for rows in sources.values() for _, label in rows)
    too_small = {c: n for c, n in counts.items() if n < MIN_PER_CLASS}
    if too_small:
        print("  dropping classes with <%d images: %s" % (MIN_PER_CLASS, too_small))
    keep = {c for c, n in counts.items() if n >= MIN_PER_CLASS}

    jobs: List[Tuple[str, str, str]] = []
    meta: List[Tuple[str, str, str]] = []        # dest, label, dataset
    per_class = Counter()
    for dataset, rows in sources.items():
        for src, label in rows:
            if label not in keep:
                continue
            index = per_class[label]
            per_class[label] += 1
            dest = PREPARED / label / ("%s_%06d.jpg" % (dataset, index))
            jobs.append((str(src), str(dest), label))
            meta.append((str(dest), label, dataset))

    print("Resizing %d images to short side %dpx ..." % (len(jobs), SHORT_SIDE))
    PREPARED.mkdir(parents=True, exist_ok=True)
    written = set()
    with ProcessPoolExecutor() as pool:
        futures = [pool.submit(_resize_one, job) for job in jobs]
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result:
                written.add(result[0])
            if i % 2500 == 0:
                print("  %6d / %d" % (i, len(jobs)))

    # Stratified split per class, so every class appears in train, val and test.
    by_class: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
    for dest, label, dataset in meta:
        if dest in written:
            by_class[label].append((dest, label, dataset))

    manifest: List[Tuple[str, str, str, str]] = []
    for label, rows in by_class.items():
        random.shuffle(rows)
        n = len(rows)
        n_train = int(n * SPLITS[0][1])
        n_val = int(n * SPLITS[1][1])
        for i, (dest, lab, dataset) in enumerate(rows):
            split = "train" if i < n_train else "val" if i < n_train + n_val else "test"
            manifest.append((dest, lab, dataset, split))

    manifest_path = PREPARED / "manifest.csv"
    with open(manifest_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["path", "label", "dataset", "split"])
        writer.writerows(manifest)

    split_counts = Counter(row[3] for row in manifest)
    class_counts = Counter(row[1] for row in manifest)
    summary = {
        "images": len(manifest),
        "classes": len(class_counts),
        "splits": dict(split_counts),
        "class_counts": dict(sorted(class_counts.items())),
        "by_dataset": dict(Counter(row[2] for row in manifest)),
        "dropped_small_classes": too_small,
        "plant_pathology_dropped": pp_dropped,
        "short_side": SHORT_SIDE,
        "seed": SEED,
    }
    (PREPARED / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\nPrepared %d images in %d classes" % (len(manifest), len(class_counts)))
    print("splits:", dict(split_counts))
    for label, n in sorted(class_counts.items()):
        print("  %-38s %5d" % (label, n))
    print("\nmanifest:", manifest_path)


if __name__ == "__main__":
    main()
