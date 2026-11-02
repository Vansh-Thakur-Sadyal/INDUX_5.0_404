# Crop disease model (training)

Everything here runs on the workstation GPU and produces exactly two files that the
app ships: `data/models/leaf_disease.onnx` and `data/models/leaf_disease.json`.
The server never imports PyTorch.

```
Dataset/*.zip                      downloaded datasets
  -> ml/prepare_data.py            one label space, resized corpus, stratified splits
  -> ml/train_central.py           centralized baseline (the control arm)
  -> ml/federated.py               FedAvg over 3 clients, then a server-side boost
  -> ml/export_onnx.py             picks the winner on validation, exports + verifies
  -> data/models/leaf_disease.*    what the app loads
```

## Setup (once)

```bat
python -m venv .venv-train
.venv-train\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
.venv-train\Scripts\python.exe -m pip install onnxruntime
```

`cu128` matters: the RTX 5070 Ti is Blackwell (sm_120) and older CUDA wheels will not
run on it. Verify with
`.venv-train\Scripts\python.exe -c "import torch;print(torch.cuda.get_device_name(0))"`.

## Run

```bat
.venv-train\Scripts\python.exe ml\prepare_data.py
.venv-train\Scripts\python.exe ml\train_central.py --epochs 16
.venv-train\Scripts\python.exe ml\federated.py --rounds 6 --local-epochs 1 --boost-epochs 3
.venv-train\Scripts\python.exe ml\export_onnx.py
```

## Data

| Dataset | Images used | Character |
|---|---|---|
| Cassava Leaf Disease (Kaggle) | 21,397 | field photos, 5 cassava classes, very imbalanced |
| Plant Pathology 2021 (FGVC8) | 15,675 | orchard apple leaves, 4000x2672 originals |
| PlantDoc | 2,459 | in-the-wild photos across many crops, small |

**39,531 images, 32 classes, split 70/15/15 stratified per class.**

Decisions worth knowing:

* **One `crop__condition` vocabulary** (`ml/labels.py`). Every raw label must be in a
  mapping table; an unmapped PlantDoc folder raises rather than landing in the wrong class.
* **Multi-label rows are dropped**, not flattened: 2,957 Plant Pathology images carry
  several diseases at once (or "complex"), which would teach a single-label head the
  wrong thing and is not an actionable answer for a farmer.
* **Classes under 60 images are dropped** (cherry healthy 57, tomato mosaic 54, tomato
  spider mites 2). They cannot be learned or evaluated at that size.
* **Images are resized once** to a 320 px short side. The raw corpus is 22 GB and
  decoding full-size orchard JPEGs every epoch makes the GPU wait on the disk; prepared it
  is ~1.5 GB and an epoch takes 25 s.
* The corpus is **heavily imbalanced** (cassava mosaic 13,158 vs bell pepper healthy 61),
  so training uses a class-balanced sampler and models are selected on **macro recall**,
  never plain accuracy.

## Federated learning

`ml/federated.py` treats **each dataset as one client**: PlantDoc, Cassava and Plant
Pathology never pool their images. That is genuinely non-IID - different crops, cameras,
countries and backgrounds - which is the realistic test for federated agriculture, where
an institution shares weights but not photographs.

Each round: server broadcasts weights -> each client trains locally -> the server takes a
sample-count weighted average (FedAvg) -> the global model is scored on the shared
validation split. Afterwards the server runs a short fine-tune ("boost") of the averaged
model, and `export_onnx.py` compares all three candidates on the same splits.

Measured numbers live in `ml/artifacts/*_report.json` and `data/models/leaf_disease.json`.
They are written by the scripts; nothing in this README is hand-copied.

## Choosing the confidence threshold

`export_onnx.py` prints accuracy and coverage at each cut-off and picks the lowest
threshold whose accuracy-when-shown clears `--min-precision` (default 0.90). The app then
refuses to name a disease below that confidence - for a farmer, "undecided, send a clearer
photo" costs nothing while a wrong disease name costs a spray.

Probabilities are **temperature-scaled** on the validation split first, because a
fine-tuned CNN claims 0.99 when it is right 90% of the time, and the UI shows that number.

## The crop gate

The farmer picks the crop before uploading, and `ml/calibrate_crop_gate.py` measures how
much of the model's confidence lands on that crop's classes:

| situation | crop probability mass |
|---|---|
| the selected crop IS the crop in the photo | 0.905 at the 5th percentile |
| the farmer selected a different crop | 0.037 at the 99th percentile |

The gap is enormous, so the gate sits in the middle at **0.50**: it keeps 98.8% of correct
selections and catches 99.9% of wrong ones. Below the gate the API returns
`crop_mismatch`, names no disease, and tells the farmer what the photo looks like instead.
This is also what catches a photo that is not a leaf at all - without it, the model would
force a hand, a sack or a wheat field into one of its 32 classes.

## What the model is not

It screens one photo against 32 classes it has seen. It is not a diagnosis, it has not
been validated on Indian field conditions, and most of its training data is apple and
cassava. Agent 11 still refuses to name any product or dose.
