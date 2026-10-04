"""Train the on-device coffee leaf classifier (MobileNetV3-Small) and export it to ONNX.

Data (CC BY 4.0, both Arabica coffee leaves), placed under data/coffee/:
  * JMuBEN + JMuBEN2 (Jepkoech et al. 2021, Kenya), via Hugging Face
    Project-AgML/arabica_coffee_leaf_disease_classification -> data/coffee/jmuben_hf/*.parquet
  * BRACOL leaf set (Krohling/Esgario et al. 2019, Brazil), Mendeley doi:10.17632/yy2k5y8mxg.1
    -> data/coffee/bracol/coffee-datasets/coffee-datasets/leaf/{dataset.csv,images/}

JMuBEN is heavily pre-augmented (tens to hundreds of rotated/shifted/recoloured copies of each crop), so a
random split would leak. Images are grouped into near-duplicate clusters with a rotation/flip-invariant
ImageNet embedding (cosine > 0.95, single linkage) and whole clusters are assigned to one split. Clusters are
also capped (train <= TRAIN_CAP, val/test <= EVAL_CAP images per cluster) so a few source photos cannot dominate.

    .venv-ml/bin/python scripts/train_coffee.py [--epochs 14]

Outputs (models/pest_classifier/): model.onnx, model_int8.onnx, labels.json, preprocess.json, confusion.json,
metrics.json, splits.json (same contract as before).
"""
import argparse
import csv
import glob
import hashlib
import io
import json
import math
import os
import sys
import time

import numpy as np
import pyarrow.parquet as pq
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image
from torchvision import models

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(ROOT, "data", "coffee")
OUT = os.path.join(ROOT, "models", "pest_classifier")
BRACOL_DIR = os.path.join(DATA, "bracol", "coffee-datasets", "coffee-datasets", "leaf")

CLASSES = ["healthy", "leaf_rust", "leaf_miner", "cercospora", "phoma"]
# JMuBEN (HF label ids: 0 Cerscospora, 1 Healthy, 2 Leaf_rust, 3 Miner, 4 Phoma)
JMUBEN = {0: "cercospora", 1: "healthy", 2: "leaf_rust", 3: "leaf_miner", 4: "phoma"}
# BRACOL predominant_stress (dataset.csv columns miner, rust, phoma, cercospora): 0 healthy, 1 leaf miner,
# 2 rust, 3 brown leaf spot (Phoma), 4 Cercospora leaf spot. Code 5 (undocumented) is skipped.
BRACOL = {0: "healthy", 1: "leaf_miner", 2: "leaf_rust", 3: "phoma", 4: "cercospora"}
MEAN, STD, SIZE = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225], 224
SEED = 13
SIM_THR, TRAIN_CAP, EVAL_CAP = 0.95, 40, 16
BRACOL_REPEAT = 4  # BRACOL / RoCoLe leaves are few but independent photos: oversample them in training
# JMuBEN healthy is ~7-13 source photos, each cut into ~1.4k-1.8k augmented crops; at cosine 0.95 single linkage
# chains them into only 3 clusters, so healthy uses a stricter 0.97 threshold (7 clusters) and larger caps.
# Inspection shows these clusters are colour/crop variants of essentially one or two leaf photos, so JMuBEN
# healthy is NOT used for evaluation at all (any test score on it would be meaningless): all of its clusters go
# to training (capped), and healthy is evaluated on BRACOL + RoCoLe only.
CLASS_THR = {"healthy": 0.97}
CLASS_CAPS = {"healthy": (100, 0)}


def dev_():
    return torch.device("mps" if torch.backends.mps.is_available() else "cpu")


# ---------------------------------------------------------------- data
def embed(x_uint8, dev):
    """Rotation/flip-invariant ImageNet embedding (mean over the 8 dihedral views), L2-normalised."""
    net = models.mobilenet_v3_large(weights=models.MobileNet_V3_Large_Weights.IMAGENET1K_V2)
    net.classifier = nn.Identity()
    net.eval().to(dev)
    m = torch.tensor(MEAN, device=dev).view(1, 3, 1, 1)
    s = torch.tensor(STD, device=dev).view(1, 3, 1, 1)
    out = []
    with torch.no_grad():
        for i in range(0, len(x_uint8), 512):
            b = torch.from_numpy(x_uint8[i:i + 512]).to(dev).permute(0, 3, 1, 2).float() / 255
            b = (F.interpolate(b, size=160, mode="bilinear", align_corners=False) - m) / s
            f = 0
            for k in range(4):
                r = torch.rot90(b, k, (2, 3))
                f = f + net(r) + net(torch.flip(r, (3,)))
            out.append(F.normalize(f, dim=1).cpu().numpy().astype(np.float16))
    return np.concatenate(out)


def clusters(feat, y, thr=SIM_THR):
    """Single-linkage near-duplicate clusters within each class; returns a cluster id per image."""
    f = torch.from_numpy(feat.astype(np.float32))
    gid = np.full(len(y), -1, dtype=np.int64)
    nxt = 0
    for c in np.unique(y):
        idx = np.where(y == c)[0]
        fc = f[idx]
        thr_c = CLASS_THR.get(CLASSES[int(c)], thr)
        par = np.arange(len(idx))

        def find(a):
            while par[a] != a:
                par[a] = par[par[a]]
                a = par[a]
            return a
        for i in range(0, len(idx), 2048):
            ii, jj = torch.nonzero(fc[i:i + 2048] @ fc.T > thr_c, as_tuple=True)
            for a, b in zip((ii + i).tolist(), jj.tolist()):
                if a < b:
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        par[ra] = rb
        roots = np.array([find(a) for a in range(len(idx))])
        _, inv = np.unique(roots, return_inverse=True)
        gid[idx] = inv + nxt
        nxt += inv.max() + 1
    return gid


def load_jmuben(dev):
    cache = os.path.join(DATA, "jmuben_128.npz")
    if os.path.exists(cache):
        z = np.load(cache)
        key = json.dumps([SIM_THR, CLASS_THR])
        if "gid" in z and "cluster_key" in z and str(z["cluster_key"]) == key:
            return {k: z[k] for k in z.files}
        d = {k: z[k] for k in z.files if k not in ("gid", "cluster_key")}
    else:
        xs, ys, paths, md5s = [], [], [], []
        for p in sorted(glob.glob(os.path.join(DATA, "jmuben_hf", "*.parquet"))):
            t = pq.read_table(p)
            for lab, im in zip(t.column("label").to_pylist(), t.column("image").to_pylist()):
                img = Image.open(io.BytesIO(im["bytes"])).convert("RGB").resize((128, 128), Image.BILINEAR)
                xs.append(np.asarray(img))
                ys.append(lab)
                paths.append(im["path"])
                md5s.append(hashlib.md5(im["bytes"]).hexdigest())
        d = {"x": np.stack(xs), "y": np.array(ys), "path": np.array(paths), "md5": np.array(md5s)}
        d["feat"] = embed(d["x"], dev)
    # Map source labels -> contract indices, then cluster.
    if "yc" not in d:
        d["yc"] = np.array([CLASSES.index(JMUBEN[int(v)]) for v in d["y"]])
    d["gid"] = clusters(d["feat"], d["yc"])
    d["cluster_key"] = np.array(json.dumps([SIM_THR, CLASS_THR]))
    np.savez(cache, **d)
    return d


def load_bracol():
    cache = os.path.join(DATA, "bracol_224.npz")
    if os.path.exists(cache):
        z = np.load(cache)
        return {k: z[k] for k in z.files}
    xs, ys, ids, md5s, skipped = [], [], [], [], {"code5": 0, "missing_or_corrupt": 0}
    for r in csv.DictReader(open(os.path.join(BRACOL_DIR, "dataset.csv"))):
        code = int(r["predominant_stress"])
        if code not in BRACOL:
            skipped["code5"] += 1
            continue
        path = os.path.join(BRACOL_DIR, "images", r["id"] + ".jpg")
        try:
            raw = open(path, "rb").read()
            img = Image.open(io.BytesIO(raw))
            img.load()
            img = img.convert("RGB")
        except (OSError, FileNotFoundError):
            skipped["missing_or_corrupt"] += 1
            continue
        xs.append(np.asarray(img.resize((SIZE, SIZE), Image.BILINEAR), dtype=np.uint8))
        ys.append(CLASSES.index(BRACOL[code]))
        ids.append(int(r["id"]))
        md5s.append(hashlib.md5(raw).hexdigest())
    print("BRACOL skipped:", skipped)
    d = {"x": np.stack(xs), "yc": np.array(ys), "id": np.array(ids), "md5": np.array(md5s)}
    np.savez(cache, **d)
    return d


ROCOLE_DIR = os.path.join(DATA, "rocole")


def load_rocole():
    """RoCoLe (Robusta, Ecuador, field photos): healthy and rust_level_1..4 -> leaf_rust; red_spider_mite and
    rows whose leaf state disagrees with the class are skipped. Grouped by plant (C<n>P<m>) to avoid leakage."""
    cache = os.path.join(DATA, "rocole_224.npz")
    if os.path.exists(cache):
        z = np.load(cache)
        return {k: z[k] for k in z.files}
    import re
    xs, ys, names, plants, skipped = [], [], [], [], {}
    for r in json.load(open(os.path.join(ROCOLE_DIR, "RoCoLE-json.json"))):
        cls = r["Label"].get("classification")
        states = {leaf.get("state") for leaf in r["Label"].get("Leaf", [])}
        lab = "healthy" if (cls == "healthy" and states == {"healthy"}) else \
            "leaf_rust" if (str(cls).startswith("rust_level") and states == {"unhealthy"}) else None
        if lab is None:
            skipped[f"{cls}/{sorted(states)}"] = skipped.get(f"{cls}/{sorted(states)}", 0) + 1
            continue
        name = r["External ID"]
        try:
            img = Image.open(os.path.join(ROCOLE_DIR, "img", name))
            img.load()
        except OSError:
            skipped["unreadable"] = skipped.get("unreadable", 0) + 1
            continue
        xs.append(np.asarray(img.convert("RGB").resize((SIZE, SIZE), Image.BILINEAR), dtype=np.uint8))
        ys.append(CLASSES.index(lab))
        names.append(name)
        plants.append(re.match(r"(C\d+P\d+)", name).group(1))
    print("RoCoLe skipped:", skipped)
    d = {"x": np.stack(xs), "yc": np.array(ys), "name": np.array(names), "plant": np.array(plants)}
    np.savez(cache, **d)
    return d


def split_groups(y, gid, rng):
    """Stratified 70/15/15 by cluster: whole clusters go to one split; greedy fill by image count."""
    out = {"train": [], "val": [], "test": []}
    for c in np.unique(y):
        gs = np.unique(gid[y == c])
        rng.shuffle(gs)
        sizes = {g: int((gid == g).sum()) for g in gs}
        total = sum(sizes.values())
        filled = {"test": 0, "val": 0, "train": 0}
        target = {"test": 0.15 * total, "val": 0.15 * total, "train": 0.70 * total}
        for g in gs:
            # put the cluster where it leaves the largest relative deficit; small classes still get val/test
            name = max(filled, key=lambda k: (target[k] - filled[k]) / target[k])
            out[name].append(g)
            filled[name] += sizes[g]
    return {k: set(v) for k, v in out.items()}


def build():
    dev = dev_()
    rng = np.random.default_rng(SEED)
    J, B = load_jmuben(dev), load_bracol()
    # JMuBEN: cluster split, then cap per cluster.
    jsp = split_groups(J["yc"], J["gid"], rng)
    hg = set(np.unique(J["gid"][J["yc"] == CLASSES.index("healthy")]).tolist())
    for name in ("val", "test"):
        jsp["train"] |= jsp[name] & hg
        jsp[name] -= hg
    jidx = {}
    for name, gs in jsp.items():
        sel = []
        for g in sorted(gs):
            members = np.where(J["gid"] == g)[0]
            caps = CLASS_CAPS.get(CLASSES[int(J["yc"][members[0]])], (TRAIN_CAP, EVAL_CAP))
            cap = caps[0] if name == "train" else caps[1]
            if len(members) > cap:
                members = rng.choice(members, cap, replace=False)
            sel += members.tolist()
        jidx[name] = np.array(sorted(sel))
    # BRACOL: one image per leaf; group byte-identical files (none expected).
    _, bg = np.unique(B["md5"], return_inverse=True)
    bsp = split_groups(B["yc"], bg, rng)
    bidx = {name: np.where(np.isin(bg, list(gs)))[0] for name, gs in bsp.items()}
    # RoCoLe: group by plant id; a plant can hold healthy and rust leaves, so split plants (stratify on the
    # plant's majority label) and keep all of its images together.
    R = load_rocole()
    plants, pinv = np.unique(R["plant"], return_inverse=True)
    pl_lab = np.array([np.bincount(R["yc"][pinv == k]).argmax() for k in range(len(plants))])
    psp = split_groups(pl_lab, np.arange(len(plants)), rng)
    ridx = {name: np.where(np.isin(pinv, list(gs)))[0] for name, gs in psp.items()}
    return {"j": J, "b": B, "r": R}, {"j": jidx, "b": bidx, "r": ridx}, jsp


# ---------------------------------------------------------------- augmentation (on device, per batch)
def to_tensor(x_uint8, dev):
    return torch.from_numpy(x_uint8).to(dev).permute(0, 3, 1, 2).float() / 255


def background_jitter(x, p=0.6):
    """Replace near-white background (BRACOL leaves are on white paper) with random colour/texture."""
    n = x.shape[0]
    mx, mn = x.max(1, keepdim=True)[0], x.min(1, keepdim=True)[0]
    mask = ((mn > 0.62) & (mx - mn < 0.15)).float()
    mask = F.avg_pool2d(mask, 5, 1, 2)
    base = torch.rand(n, 3, 1, 1, device=x.device) * torch.tensor([0.6, 0.7, 0.5], device=x.device).view(1, 3, 1, 1)
    noise = F.interpolate(torch.rand(n, 3, 14, 14, device=x.device), size=x.shape[-2:], mode="bilinear",
                          align_corners=False)
    bg = (base + 0.35 * noise).clamp(0, 1)
    use = (torch.rand(n, 1, 1, 1, device=x.device) < p).float()
    m = mask * use
    return x * (1 - m) + bg * m


def augment(x, bg_jitter):
    n, dev = x.shape[0], x.device
    if bg_jitter:
        x = background_jitter(x)
    ang = torch.rand(n, device=dev) * 2 * math.pi
    zoom = torch.empty(n, device=dev).uniform_(0.75, 1.15)        # <1 zooms in (crop), >1 zooms out
    flip = torch.where(torch.rand(n, device=dev) < 0.5, -1.0, 1.0)
    tx, ty = (torch.rand(n, device=dev) - 0.5) * 0.25, (torch.rand(n, device=dev) - 0.5) * 0.25
    cos, sin = torch.cos(ang) * zoom, torch.sin(ang) * zoom
    theta = torch.stack([torch.stack([cos * flip, -sin, tx], 1), torch.stack([sin * flip, cos, ty], 1)], 1)
    grid = F.affine_grid(theta, (n, 3, SIZE, SIZE), align_corners=False)
    x = F.grid_sample(x, grid, mode="bilinear", padding_mode="reflection", align_corners=False)
    # colour: brightness, contrast, saturation, white balance, blur-ish noise
    b = torch.empty(n, 1, 1, 1, device=dev).uniform_(0.65, 1.35)
    c = torch.empty(n, 1, 1, 1, device=dev).uniform_(0.7, 1.3)
    s = torch.empty(n, 1, 1, 1, device=dev).uniform_(0.6, 1.4)
    wb = torch.empty(n, 3, 1, 1, device=dev).uniform_(0.88, 1.12)
    x = x * b * wb
    mean = x.mean((1, 2, 3), keepdim=True)
    x = (x - mean) * c + mean
    gray = x.mean(1, keepdim=True)
    x = (x - gray) * s + gray
    x = x + torch.randn_like(x) * 0.02 * torch.rand(n, 1, 1, 1, device=dev)
    return x.clamp(0, 1)


def normalize(x):
    m = torch.tensor(MEAN, device=x.device).view(1, 3, 1, 1)
    s = torch.tensor(STD, device=x.device).view(1, 3, 1, 1)
    return (x - m) / s


def resize(x):
    return x if x.shape[-1] == SIZE else F.interpolate(x, size=SIZE, mode="bilinear", align_corners=False)


# ---------------------------------------------------------------- metrics, calibration, export (as train_classifier.py)
def fit_temperature(logits, y):
    log_t = torch.zeros(1, requires_grad=True)
    opt = torch.optim.LBFGS([log_t], lr=0.1, max_iter=200)

    def closure():
        opt.zero_grad()
        loss = F.cross_entropy(logits / log_t.exp(), y)
        loss.backward()
        return loss
    opt.step(closure)
    return float(log_t.exp())


def metrics(probs, y):
    k = len(CLASSES)
    pred = probs.argmax(1)
    cm = np.zeros((k, k), dtype=int)
    for t, p in zip(y, pred):
        cm[t, p] += 1
    per, f1s = {}, []
    for c in range(k):
        tp, fp, fn = cm[c, c], cm[:, c].sum() - cm[c, c], cm[c, :].sum() - cm[c, c]
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        if cm[c].sum():
            f1s.append(f1)
        per[CLASSES[c]] = {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4), "n": int(cm[c].sum())}
    conf, correct = probs.max(1), pred == y
    ece, bins = 0.0, np.linspace(0, 1, 16)
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            ece += m.mean() * abs(correct[m].mean() - conf[m].mean())
    rown = cm / np.maximum(cm.sum(1, keepdims=True), 1)
    return {"accuracy": round(float(correct.mean()), 4), "macro_f1": round(float(np.mean(f1s)), 4),
            "ece_15bin": round(float(ece), 4),
            "nll": round(float(-np.log(np.maximum(probs[np.arange(len(y)), y], 1e-12)).mean()), 4),
            "per_class": per}, cm, rown


class Calibrated(nn.Module):
    def __init__(self, net, temperature):
        super().__init__()
        self.net, self.t = net, temperature

    def forward(self, x):
        return torch.softmax(self.net(x) / self.t, dim=1)


def to_input(imgs):
    """list of uint8 HWC arrays (any size) -> normalized NCHW 224px via PIL bilinear (as pestwatch.vision)."""
    a = [np.asarray(Image.fromarray(i).resize((SIZE, SIZE), Image.BILINEAR), dtype=np.float32) / 255.0 for i in imgs]
    x = (np.stack(a) - np.array(MEAN, np.float32)) / np.array(STD, np.float32)
    return np.ascontiguousarray(x.transpose(0, 3, 1, 2), dtype=np.float32)


def ort_probs(path, imgs):
    import onnxruntime as ort
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    return np.concatenate([sess.run(None, {name: to_input(imgs[i:i + 64])})[0] for i in range(0, len(imgs), 64)])


def quantize(fp32_path, int8_path):
    """Weight-only int8 (per-output-channel) Conv/Gemm weights + DequantizeLinear; activations stay float.
    Full static QDQ quantization collapses MobileNetV3-Small accuracy (hardswish / squeeze-excite)."""
    import onnx
    from onnx import helper, numpy_helper
    m = onnx.load(fp32_path)
    g = m.graph
    inits = {i.name: i for i in g.initializer}
    targets = {n.input[1] for n in g.node if n.op_type in ("Conv", "Gemm") and len(n.input) > 1 and n.input[1] in inits}
    dq = []
    for name in sorted(targets):
        w = numpy_helper.to_array(inits[name]).astype(np.float32)
        if w.size < 512:
            continue
        flat = w.reshape(w.shape[0], -1)
        scale = (np.maximum(np.abs(flat).max(1), 1e-8) / 127.0).astype(np.float32)
        q = np.clip(np.round(flat / scale[:, None]), -127, 127).astype(np.int8).reshape(w.shape)
        g.initializer.remove(inits[name])
        g.initializer.extend([numpy_helper.from_array(q, name + "_q"), numpy_helper.from_array(scale, name + "_scale"),
                              numpy_helper.from_array(np.zeros(w.shape[0], np.int8), name + "_zp")])
        dq.append(helper.make_node("DequantizeLinear", [name + "_q", name + "_scale", name + "_zp"], [name], axis=0))
    nodes = list(g.node)
    del g.node[:]
    g.node.extend(dq + nodes)
    onnx.checker.check_model(m)
    onnx.save(m, int8_path)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--batch", type=int, default=96)
    ap.add_argument("--lr", type=float, default=5e-4)
    a = ap.parse_args()
    torch.manual_seed(SEED)
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    dev = dev_()

    SRC, IDX, jsp = build()
    J, B = SRC["j"], SRC["b"]
    SRCNAME = {"j": "jmuben", "b": "bracol", "r": "rocole"}
    print(f"data ready ({time.time() - t0:.0f}s)")
    # Unified item list: (source, index). Images stored at 128 px (JMuBEN) or 224 px (BRACOL).
    jidx = IDX["j"]
    items = {s: [(k, int(i)) for k in SRC for i in IDX[k][s]] for s in ("train", "val", "test")}
    train_items = items["train"] + [(k, int(i)) for k in ("b", "r") for i in IDX[k]["train"]] * (BRACOL_REPEAT - 1)
    lab = lambda it: int(SRC[it[0]]["yc"][it[1]])  # noqa: E731
    img = lambda it: SRC[it[0]]["x"][it[1]]  # noqa: E731
    ys = {s: np.array([lab(it) for it in items[s]]) for s in items}
    src = {s: np.array([it[0] for it in items[s]]) for s in items}
    stats = {s: {SRCNAME[k]: {CLASSES[c]: int(((ys[s] == c) & (src[s] == k)).sum()) for c in range(5)} for k in SRC}
             for s in items}
    print(json.dumps(stats))
    jclusters = {s: {CLASSES[c]: int(len({g for g in jsp[s] if J["yc"][np.argmax(J["gid"] == g)] == c})) for c in range(5)}
                 for s in jsp}
    print("JMuBEN clusters per split:", jclusters)

    # Leakage check: max cosine similarity of each test/val JMuBEN image to any train JMuBEN image.
    ftr = torch.from_numpy(J["feat"][jidx["train"]].astype(np.float32))
    leak = {}
    for s in ("val", "test"):
        fs = torch.from_numpy(J["feat"][jidx[s]].astype(np.float32))
        mx = (fs @ ftr.T).max(1)[0].numpy()
        leak[s] = {"jmuben_near_dup_of_train_cos_gt_0.95": int((mx > 0.95).sum()), "of": int(len(fs)),
                   "median_max_cos": round(float(np.median(mx)), 4)}
    print("leakage:", leak)

    net = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.IMAGENET1K_V1)
    net.classifier[3] = nn.Linear(net.classifier[3].in_features, len(CLASSES))
    net.to(dev)
    freq = np.bincount([lab(it) for it in train_items], minlength=len(CLASSES)).astype(float)
    w = torch.tensor(np.where(freq > 0, freq.sum() / np.maximum(freq, 1) / len(CLASSES), 0.0), dtype=torch.float32)
    lossf = nn.CrossEntropyLoss(weight=w.to(dev), label_smoothing=0.05)

    def batches(split, bs, shuffle, rng=None):
        pool = train_items if split == "train" else items[split]
        order = np.arange(len(pool))
        if shuffle:
            rng.shuffle(order)
        for i in range(0, len(order), bs):
            sel = [pool[k] for k in order[i:i + bs]]
            yb = torch.tensor([lab(it) for it in sel])
            parts, flags = [], []
            for s_, arr in ((k, SRC[k]["x"]) for k in SRC):
                ii = [it[1] for it in sel if it[0] == s_]
                if ii:
                    parts.append((s_, to_tensor(arr[np.array(ii)], dev)))
            # keep label order consistent with concatenation order
            yb = torch.tensor([lab(it) for s_ in SRC for it in sel if it[0] == s_])
            yield parts, yb

    def forward_eval(split):
        net.eval()
        out, yy = [], []
        with torch.no_grad():
            for parts, yb in batches(split, 256, False):
                x = torch.cat([resize(p) for _, p in parts])
                out.append(net(normalize(x)).float().cpu())
                yy.append(yb)
        return torch.cat(out), torch.cat(yy)

    steps = a.epochs * math.ceil(len(train_items) / a.batch)
    opt = torch.optim.AdamW(net.parameters(), lr=a.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=steps)
    rng = np.random.default_rng(SEED)
    best, best_state = -1, None
    for ep in range(a.epochs):
        net.train()
        tl = 0.0
        for parts, yb in batches("train", a.batch, True, rng):
            x = torch.cat([augment(p, bg_jitter=(s_ == "b")) for s_, p in parts])
            opt.zero_grad()
            loss = lossf(net(normalize(x)), yb.to(dev))
            loss.backward()
            opt.step()
            sched.step()
            tl += float(loss.detach()) * len(yb)
        lv, yv = forward_eval("val")
        pv = lv.argmax(1)
        acc = float((pv == yv).float().mean())
        mf1 = metrics(torch.softmax(lv, 1).numpy(), yv.numpy())[0]["macro_f1"]
        print(f"epoch {ep + 1}/{a.epochs} loss {tl / len(train_items):.3f} val_acc {acc:.3f} val_macroF1 {mf1:.3f}"
              f" ({time.time() - t0:.0f}s)", flush=True)
        if mf1 > best:
            best, best_state = mf1, {k: v.detach().cpu().clone() for k, v in net.state_dict().items()}
    net.load_state_dict(best_state)
    lv, yv = forward_eval("val")
    net.cpu().eval()
    T = fit_temperature(lv, yv)
    print(f"temperature {T:.3f}")

    fp32 = os.path.join(OUT, "model.onnx")
    torch.onnx.export(Calibrated(net, T), torch.zeros(1, 3, SIZE, SIZE), fp32, input_names=["image"],
                      output_names=["probs"], dynamic_axes={"image": {0: "n"}, "probs": {0: "n"}},
                      opset_version=17, dynamo=False)

    # Evaluate the exported ONNX graph on the test split (PIL bilinear resize, as the Python/web clients).
    order = [it for s_ in SRC for it in items["test"] if it[0] == s_]
    timgs = [img(it) for it in order]
    yt = np.array([lab(it) for it in order])
    st = np.array([it[0] for it in order])
    pt = ort_probs(fp32, timgs)
    m, cm, rown = metrics(pt, yt)
    # uncalibrated reference: undo temperature  (log p * T, softmax)
    pu = torch.softmax(torch.log(torch.from_numpy(pt).clamp_min(1e-12)) * T, 1).numpy()
    mu, _, _ = metrics(pu, yt)
    m["ece_before_temperature"], m["nll_before_temperature"] = mu["ece_15bin"], mu["nll"]
    m["temperature"] = round(T, 4)
    lr_ = m["per_class"]["leaf_rust"]
    m["leaf_rust"] = {"precision": lr_["precision"], "recall": lr_["recall"]}
    m["by_source"] = {}
    for s_, name in SRCNAME.items():
        sel = st == s_
        ms, _, _ = metrics(pt[sel], yt[sel])
        m["by_source"][name] = {"n": int(sel.sum()), "accuracy": ms["accuracy"], "macro_f1": ms["macro_f1"],
                                "per_class": ms["per_class"]}
    m["size_mb"] = round(os.path.getsize(fp32) / 1e6, 2)
    result = {"fp32": m}

    int8 = os.path.join(OUT, "model_int8.onnx")
    try:
        quantize(fp32, int8)
        pq8 = ort_probs(int8, timgs)
        m8, _, _ = metrics(pq8, yt)
        m8["method"] = "weight-only int8 (per-channel), float activations"
        m8["size_mb"] = round(os.path.getsize(int8) / 1e6, 2)
        m8["accuracy_delta_vs_fp32"] = round(m8["accuracy"] - m["accuracy"], 4)
        m8["prediction_agreement_with_fp32"] = round(float((pq8.argmax(1) == pt.argmax(1)).mean()), 4)
        lr8 = m8["per_class"]["leaf_rust"]
        m8["leaf_rust"] = {"precision": lr8["precision"], "recall": lr8["recall"]}
        result["int8"] = m8
    except Exception as e:  # report, don't fail the run
        result["int8"] = {"error": repr(e)}
        if os.path.exists(int8):
            os.remove(int8)

    import onnxruntime as ort
    for key, path in (("fp32", fp32), ("int8", int8)):
        if not os.path.exists(path):
            continue
        s = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        inp = to_input(timgs[:1])
        for _ in range(5):
            s.run(None, {"image": inp})
        t1 = time.time()
        for _ in range(50):
            s.run(None, {"image": inp})
        result[key]["latency_ms_cpu_batch1"] = round((time.time() - t1) / 50 * 1000, 2)

    counts_raw = {"jmuben": {CLASSES[c]: int((J["yc"] == c).sum()) for c in range(5)},
                  "bracol": {CLASSES[c]: int((B["yc"] == c).sum()) for c in range(5)},
                  "rocole": {CLASSES[c]: int((SRC["r"]["yc"] == c).sum()) for c in range(5)}}
    result.update({
        "dataset": ("JMuBEN + JMuBEN2 (Jepkoech et al. 2021, Kenya; CC BY 4.0; via Hugging Face "
                    "Project-AgML/arabica_coffee_leaf_disease_classification) + BRACOL leaf set "
                    "(Krohling/Esgario et al. 2019, Brazil; CC BY 4.0; Mendeley doi:10.17632/yy2k5y8mxg.1) + RoCoLe "
                    "(Parraga-Alava et al. 2019, Ecuador, Robusta; CC BY 4.0; Mendeley doi:10.17632/c5yvn32dzg.2)"),
        "class_counts_raw": counts_raw, "jmuben_clusters": int(len(np.unique(J["gid"]))),
        "jmuben_clusters_per_split": jclusters, "split_counts": stats,
        "splits": {k: int(len(v)) for k, v in items.items()}, "leakage": leak,
        "dedupe": f"JMuBEN near-duplicate clusters (dihedral-invariant MobileNetV3-Large embedding, cosine > {SIM_THR}; healthy {CLASS_THR['healthy']}, "
                  f"single linkage) kept within one split; capped at {TRAIN_CAP}/cluster (train) and "
                  f"{EVAL_CAP}/cluster (val/test); healthy caps {CLASS_CAPS['healthy']}. BRACOL: byte-identical files grouped; RoCoLe: grouped by "
                  f"plant; BRACOL and RoCoLe oversampled x{BRACOL_REPEAT} in training.",
        "train_seconds": round(time.time() - t0, 1), "epochs": a.epochs, "device": str(dev),
        "best_val_macro_f1": round(best, 4),
    })
    json.dump(CLASSES, open(os.path.join(OUT, "labels.json"), "w"))
    json.dump({"size": SIZE, "resize": "bilinear, full image to size x size", "mean": MEAN, "std": STD,
               "layout": "NCHW float32 RGB in [0,1] then normalized", "input": "image", "output": "probs",
               "web_model": "model_int8.onnx" if os.path.exists(int8) else "model.onnx"},
              open(os.path.join(OUT, "preprocess.json"), "w"), indent=2)
    json.dump({"classes": CLASSES, "matrix": np.round(rown, 4).tolist(), "counts": cm.tolist(),
               "n_test": int(len(yt)), "per_class_n": {CLASSES[c]: int(cm[c].sum()) for c in range(len(CLASSES))},
               "source": "held-out test split (JMuBEN cluster-split + BRACOL), model.onnx (fp32, temperature-calibrated)"},
              open(os.path.join(OUT, "confusion.json"), "w"), indent=2)
    json.dump(result, open(os.path.join(OUT, "metrics.json"), "w"), indent=2)
    sid = lambda it: f"jmuben:{J['path'][it[1]]}|{JMUBEN[int(J['y'][it[1]])]}" if it[0] == "j" else (f"bracol:{int(B['id'][it[1]])}" if it[0] == "b" else f"rocole:{SRC['r']['name'][it[1]]}")  # noqa: E731
    json.dump({k: [sid(it) for it in v] for k, v in items.items()}, open(os.path.join(OUT, "splits.json"), "w"))
    # test predictions for sample picking
    json.dump([{"id": sid(it), "label": CLASSES[int(t)], "pred": CLASSES[int(p.argmax())], "conf": round(float(p.max()), 4)}
               for it, t, p in zip(order, yt, pt)], open(os.path.join(DATA, "test_predictions.json"), "w"))
    print(json.dumps({k: result[k] for k in ("fp32", "int8")}, indent=1)[:4000])
    print(f"total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
