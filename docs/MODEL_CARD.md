# Model card: PestWatch on-device coffee leaf classifier

**Model:** MobileNetV3-Small (ImageNet-pretrained, torchvision), fine-tuned on coffee leaf photos. Exported to ONNX with temperature scaling and softmax baked in, so outputs are calibrated probabilities over 5 classes:

`["healthy", "leaf_rust", "leaf_miner", "cercospora", "phoma"]`

**Files** (`models/pest_classifier/`):

| File | Contents |
|---|---|
| `model.onnx` | fp32, 6.1 MB |
| `model_int8.onnx` | weight-only int8, 1.65 MB; used by the field app |
| `labels.json` | class order (`labels.json == pestwatch.config.CLASSES`) |
| `preprocess.json` | input size, normalization, which model the web app loads |
| `confusion.json` | held-out confusion matrix (contract below) |
| `metrics.json` | all metrics reported here, including per-source results |
| `splits.json` | train/val/test image ids (`jmuben:<file>\|<class>`, `bracol:<id>`, `rocole:<file>`) |

**Reproduce** (data goes in `data/coffee/`, which is git-ignored):

```
.venv-ml/bin/python scripts/train_coffee.py      # 8 epochs by default, ~6-8 min on Apple MPS
```

Download locations are listed in the docstring of `scripts/train_coffee.py`.

## Data

All three sources are licensed **CC BY 4.0**. Counts are usable images after label mapping.

| Source | Where / conditions | healthy | leaf_rust | leaf_miner | cercospora | phoma |
|---|---|---|---|---|---|---|
| **JMuBEN + JMuBEN2** (Kenya, Arabica) | Close-up 128 px crops of field photos; heavily pre-augmented by the authors | 18,983\* | 8,336 | 16,978 | 7,681 | 6,571 |
| **BRACOL**, leaf set (Brazil, Arabica) | Whole leaf, underside, on a white background, 5 smartphones | 142 | 465 | 253 | 136 | 346 |
| **RoCoLe** (Ecuador, Robusta) | Whole leaf in the field, natural background | 787 | 598 | 0 | 0 | 0 |

\* JMuBEN healthy is about 19k files but is essentially one or two leaf photos (see Splits).

- **JMuBEN:** Jepkoech, J., Mugo, D. M., Kenduiywo, B. K., Too, E. C. (2021). "Arabica coffee leaf images dataset for coffee leaf disease detection and classification." *Data in Brief* 36, 107142. Mendeley Data doi:10.17632/t2r6rszp5c.1 (rust, cercospora, phoma) and doi:10.17632/tgv3zb82nd.1 (healthy, miner). We used the Hugging Face copy `Project-AgML/arabica_coffee_leaf_disease_classification` (58,549 images, CC BY 4.0).
- **BRACOL:** Krohling, R. A., Esgario, J. G. M., Ventura, J. A. (2019). "BRACOL – A Brazilian Arabica Coffee Leaf images dataset to identification and quantification of coffee diseases and pests." Mendeley Data V1, doi:10.17632/yy2k5y8mxg.1. See also Esgario et al. (2020), *Computers and Electronics in Agriculture* 169, 105162.
  - The Mendeley zip is truncated (no central directory). We recovered 1,402 of the 1,747 leaf images; the symptom-crop subset is missing.
  - We skipped 343 missing or corrupt files and 62 rows with the undocumented stress code 5.
- **RoCoLe:** Parraga-Alava, J., Cusme, K., Loor, A., Santander, E. (2019). "RoCoLe: A robusta coffee leaf images dataset for evaluation of machine learning based methods in plant diseases recognition." *Data in Brief* 25, 104414. Mendeley Data V2, doi:10.17632/c5yvn32dzg.2.
  - `rust_level_1..4` maps to leaf_rust.
  - We skipped red spider mite (167 images) and 8 images whose leaf state contradicts their class.

**Class mapping:**

| PestWatch class | JMuBEN | BRACOL `predominant_stress` | RoCoLe |
|---|---|---|---|
| healthy | Healthy | 0 healthy | healthy |
| leaf_rust | Leaf_rust | 2 rust | rust_level_1–4 |
| leaf_miner | Miner | 1 leaf miner | – |
| cercospora | Cerscospora | 4 cercospora leaf spot | – |
| phoma | Phoma | 3 brown leaf spot (CSV column `phoma`) | – |

All 5 classes are present. leaf_miner, cercospora and phoma come from Arabica only (JMuBEN and BRACOL).

**Splits (stratified ≈70/15/15, seed 13) and de-duplication:**

- JMuBEN ships many rotated, shifted and recoloured copies of each crop, so a random split would leak. We embedded every image with ImageNet MobileNetV3-Large, averaging over the 8 rotations and flips. Images with cosine similarity > 0.95 (healthy: > 0.97) were linked by single linkage, giving 826 near-duplicate clusters. Each cluster goes wholly into one split.
- Clusters are capped at 40 images in train and 16 in val/test, so a few source photos can't dominate training or the metrics.
- **JMuBEN healthy is not used for evaluation.** Inspection shows its ~19k images are colour and crop variants of essentially one or two leaf photos (7 clusters). It is used for training only (capped at 700 images). Healthy is evaluated on BRACOL and RoCoLe leaves.
- BRACOL: one image per leaf. RoCoLe: split by plant (`C<n>P<m>`), so photos of the same plant never cross splits. Both are oversampled ×4 in training.
- Result: 18,902 train / 2,498 val / 2,066 test images.
- **Leakage check:** 0 of 1,654 JMuBEN test images (0 of 2,090 val) has a train image with cosine > 0.95. The median nearest-train cosine is 0.90: the images are similar, but not duplicates.

## Training

- 224 px input.
- On-device (MPS) batch augmentation:
  - full 0–360° rotation, zoom 0.75–1.15, shift, mirror
  - brightness, contrast, saturation and white-balance jitter, plus noise
  - **background jitter** for BRACOL: the white paper is replaced by random colours or textures, so "white background" can't become a cue.
- Class-balanced, label-smoothed (0.05) cross-entropy. AdamW with lr 5e-4 and a one-cycle schedule.
- 8 epochs; the epoch with the best validation macro-F1 is kept. Train time 6.2 min (375 s) on Apple MPS.
- Calibration: temperature fitted on the validation split, T = 0.81.

## Results (held-out test split, n = 2,066; evaluated on the exported ONNX graph)

| Metric | fp32 | int8 (weight-only) |
|---|---|---|
| Accuracy | **0.738** | 0.734 |
| Macro-F1 | **0.671** | 0.684 |
| Leaf rust precision / recall | **0.79 / 0.93** (n = 914) | 0.78 / 0.90 |
| Healthy precision / recall | **0.79 / 0.94** (n = 139) | 0.78 / 0.94 |
| ECE (15 bins), before → after temperature scaling | 0.095 → **0.063** | 0.097 |
| NLL, before → after | 0.73 → 0.71 | n/a |
| Size | 6.1 MB | **1.65 MB** |
| Agreement with fp32 predictions | n/a | 94.7% |
| Latency (ONNX Runtime CPU, batch 1, laptop) | 2.9 ms | 3.0 ms |

| Class (fp32) | Precision | Recall | n |
|---|---|---|---|
| healthy | 0.79 | 0.94 | 139 |
| leaf_rust | 0.79 | 0.93 | 914 |
| leaf_miner | 0.67 | 0.80 | 438 |
| cercospora | 0.46 | 0.60 | 149 |
| phoma | 0.99 | 0.24 | 426 |

**By source (fp32):**

| Source | n | Accuracy | Macro-F1 | Notes |
|---|---|---|---|---|
| JMuBEN (Kenya crops) | 1,654 | 0.72 | 0.60 | rust P/R 0.78 / 0.98; JMuBEN phoma recall only 0.15 |
| BRACOL (Brazil, whole leaf) | 203 | 0.77 | 0.74 | healthy P/R 0.85 / 0.77; rust 0.86 / 0.73; phoma 0.98 / 0.94 |
| RoCoLe (Ecuador field) | 209 | 0.83 | 0.82 | healthy P/R 0.78 / 0.97; rust 0.94 / 0.66 |

**Main confusions (test counts):**

- JMuBEN phoma is predicted as leaf_miner (168), leaf_rust (102) or cercospora (52).
- leaf_miner → leaf_rust (63).
- cercospora → leaf_rust (54).
- leaf_rust → healthy (31), mostly early rust (level 1) in RoCoLe.
- Healthy → leaf_rust is rare: 4 of 139.

Most rust false positives come from the other leaf diseases, not from healthy leaves.

The healthy, cercospora and BRACOL/RoCoLe per-class numbers rest on 20–140 images and have wide uncertainty (±5–15 points). The JMuBEN numbers rest on 8–69 source clusters per class, so they are less precise than the image counts suggest.

**Full static int8 quantization (QDQ) is not used.** It collapsed MobileNetV3-Small accuracy in earlier tests (hardswish and squeeze-excite layers are fragile under it). Weight-only per-channel int8 keeps fp32-level accuracy at 3.7× smaller size.

## How it is used

- **Field app** (`web/model.js`, `web/vendor/ort`):
  - Runs `model_int8.onnx` with onnxruntime-web: single-threaded wasm, offline once cached.
  - The full photo is resized to 224×224.
  - Only the 5 probabilities leave the phone, never the photo.
- **Python** (`pestwatch/vision.py`): `OnnxPestClassifier().predict(image) -> probs over CLASSES`.
- **Simulation:** `confusion.json` contains `{"classes", "matrix" (row-normalised, true × pred), "counts", "n_test", "per_class_n"}`. The simulation uses the measured matrix.

## Limitations (read before quoting numbers)

- **The test set comes from the same three collections as training.** The cluster split removes near-duplicates, but this is still an in-collection estimate. Accuracy on a new farm, phone or season is unknown and probably lower.
- **Background and framing differ by source.** JMuBEN is tight close-up crops, BRACOL is single leaves on white paper, and RoCoLe is single leaves in the field.
  - Whole-plant or canopy photos, multiple leaves, or upper-side-only shots are out of distribution.
  - The app should ask for one leaf filling the frame.
- **Geography and variety.** Only JMuBEN is East African (Kenya), and its healthy class is effectively one or two photos. Healthy-leaf behaviour is learned mainly from Brazil (Arabica) and Ecuador (Robusta). There are no Ugandan or Ethiopian images, and no Robusta images for miner, cercospora or phoma.
- **Weak classes.**
  - Phoma recall is 0.24 overall (0.15 on JMuBEN).
  - Cercospora precision is 0.46.
  - Leaf rust precision is 0.79: other leaf diseases are sometimes called rust.
  - Early rust (RoCoLe level 1) is often called healthy: RoCoLe rust recall is 0.66.
- **Closed set and leaves only.**
  - No coffee berry disease, coffee berry borer, berries, stems, wilt, nutrient deficiency or red spider mite (RoCoLe mite images were excluded).
  - Anything outside the 5 classes will be forced into one of them. Calibration does not detect out-of-distribution inputs.
- **Mixed infections.** BRACOL and RoCoLe leaves with several stresses are labelled by the predominant one. The model outputs one distribution and does not report co-infection.
- **Data quality.**
  - BRACOL's public zip is truncated (about 20% of leaves missing).
  - JMuBEN's pre-augmentation means its effective sample size is the number of clusters (826), not 58k images.

## Intended use

Decision support for coffee farmers and extension officers, feeding a community aggregation layer designed to tolerate an imperfect per-photo classifier. A single photo's prediction is a prompt to look closer or ask an extension officer. It is **not** a diagnosis for fungicide or pesticide decisions without human confirmation.
