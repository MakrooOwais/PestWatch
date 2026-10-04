"""On-device pest classifier.

In production this is a quantised image model running on the phone (e.g. a
MobileNetV3-Small TFLite model, ~2-4 MB) that maps a crop photo to class
probabilities over ``CLASSES``. The aggregation layer only consumes that
probability vector, so any model honouring the contract below can be dropped in.

For the simulation we cannot run a real model on imaginary photos, so
``SimulatedClassifier`` produces probability vectors with realistic failure
modes: low confidence on faint early lesions, and confusion between coffee leaf
rust and look-alike leaf problems (Cercospora, leaf miner, Phoma).
"""
import json
import os
from typing import Protocol

import numpy as np

from .config import CLASSES, TARGET, ClassifierConfig


class PestClassifier(Protocol):
    def predict(self, image) -> np.ndarray:
        """Return probabilities over CLASSES (sums to 1)."""


class SimulatedClassifier:
    def __init__(self, cfg: ClassifierConfig):
        self.cfg = cfg
        self.base = np.array(cfg.confusion, dtype=float)
        self.target_early = np.array(cfg.target_early, dtype=float)
        self.target_late = np.array(cfg.target_late, dtype=float)
        self.source = "assumed"
        measured = load_measured_confusion(cfg) if cfg.use_measured else None
        if measured is not None:
            self.source = f"measured (smoothed, {cfg.field_shift:.0%} field-shift blend)"
            ok = measured.sum(1) > 0
            lam = cfg.field_shift
            self.base[ok] = (1 - lam) * measured[ok] + lam * self.base[ok]
            if ok[TARGET]:
                self.target_late = (1 - lam) * measured[TARGET] + lam * self.target_late
                # The dataset has no early-stage labels: assume early damage is halfway
                # between the measured performance and the pessimistic default.
                self.target_early = 0.5 * self.target_late + 0.5 * np.array(cfg.target_early)
        if cfg.degrade:
            k = len(CLASSES)
            mix = lambda r: (1 - cfg.degrade) * r + cfg.degrade / k  # noqa: E731
            self.base, self.target_early, self.target_late = mix(self.base), mix(self.target_early), mix(self.target_late)

    def predict_batch(self, rng: np.random.Generator, true_class: np.ndarray,
                      severity: np.ndarray) -> np.ndarray:
        c = self.cfg
        n, k = len(true_class), len(CLASSES)
        rows = self.base[true_class].copy()
        is_target = true_class == TARGET
        ramp = np.clip(severity / 0.3, 0.0, 1.0)[:, None]
        target_rows = self.target_early + (self.target_late - self.target_early) * ramp
        rows[is_target] = target_rows[is_target]
        rows /= rows.sum(1, keepdims=True)
        pred = (rng.random(n)[:, None] > np.cumsum(rows, 1)).sum(1).clip(0, k - 1)

        right = pred == true_class
        mean = np.where(right, c.conf_correct, c.conf_wrong)
        mean = np.where(right & is_target, c.conf_target_early + (c.conf_correct + 0.1 - c.conf_target_early) * ramp[:, 0], mean)
        mean = np.clip(mean, 0.3, 0.97)
        conf = rng.beta(mean * c.concentration, (1 - mean) * c.concentration)
        conf = np.maximum(conf, 1.0 / k + 0.02)

        probs = rng.dirichlet(np.ones(k), size=n)
        probs[np.arange(n), pred] = 0.0
        # When wrong, the true class usually gets the runner-up share.
        probs[np.arange(n)[~right], true_class[~right]] += 2.0
        probs /= probs.sum(1, keepdims=True)
        probs *= (1 - conf)[:, None]
        probs[np.arange(n), pred] = conf
        return probs


def load_measured_confusion(cfg: ClassifierConfig):
    """Row-normalised held-out confusion matrix of the trained model, reordered to CLASSES."""
    path = cfg.measured_confusion_path
    if not path or not os.path.exists(path):
        return None
    with open(path) as f:
        d = json.load(f)
    src = d["classes"]
    m = np.array(d["matrix"], dtype=float)
    n = d.get("per_class_n")
    if n:
        # Back to counts, then additive smoothing so tiny test classes don't claim 0% error.
        counts = m * np.array([n.get(c, 0) for c in src], dtype=float)[:, None]
        m = counts + cfg.measured_smoothing
        m /= m.sum(1, keepdims=True)
    out = np.zeros((len(CLASSES), len(CLASSES)))
    for i, ci in enumerate(CLASSES):
        if ci not in src:
            continue
        for j, cj in enumerate(CLASSES):
            if cj in src:
                out[i, j] = m[src.index(ci), src.index(cj)]
        if out[i].sum() > 0:
            out[i] /= out[i].sum()
    return out
