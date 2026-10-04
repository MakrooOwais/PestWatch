"""Multi-signal fusion: photo scan + farmer confirmations + traps + forecast -> P(outbreak).

A small logistic model over interpretable features of each candidate zone,
fitted on simulated seasons (scripts/evaluate.py) and replaceable by one fitted
on real labelled seasons. Output is a calibrated probability that the zone
contains an active infestation, which is what extension staff and farmers see
("Outbreak probability 82%") instead of a raw scan score.
"""
import json
import os
from typing import Optional

import numpy as np

FEATURES = ["scan_llr", "farmer_confirmations", "trap_anomaly"]
# Hand-set fallback so the system runs before any fitting.
DEFAULT = {"bias": -2.0, "weights": [0.8, 4.0, 0.35], "features": FEATURES}
# The regional migration forecast is not a zone feature (it is confounded with time of
# season); it instead lowers the trap-catch level that triggers scouting (see simulation).


def featurize(llr: float, n_confirmed: float, trap_z: float) -> np.ndarray:
    return np.array([min(llr, 30.0), np.log1p(n_confirmed), float(np.clip(trap_z, -2.0, 15.0))])


class FusionModel:
    def __init__(self, bias: float, weights, meta: Optional[dict] = None):
        self.bias = float(bias)
        self.w = np.asarray(weights, dtype=float)
        self.meta = meta or {}

    @classmethod
    def load(cls, path: str) -> "FusionModel":
        if path and os.path.exists(path):
            with open(path) as f:
                d = json.load(f)
            return cls(d["bias"], d["weights"], d)
        return cls(DEFAULT["bias"], DEFAULT["weights"], {"source": "default"})

    def prob(self, x: np.ndarray) -> np.ndarray:
        z = self.bias + np.asarray(x) @ self.w
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))

    @classmethod
    def fit(cls, X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 50) -> "FusionModel":
        """L2-regularised logistic regression by Newton/IRLS (bias unpenalised)."""
        Xb = np.hstack([np.ones((len(X), 1)), X])
        beta = np.zeros(Xb.shape[1])
        reg = np.full(Xb.shape[1], l2)
        reg[0] = 0.0
        for _ in range(iters):
            p = 1 / (1 + np.exp(-np.clip(Xb @ beta, -30, 30)))
            g = Xb.T @ (p - y) + reg * beta
            H = (Xb * (p * (1 - p))[:, None]).T @ Xb + np.diag(reg)
            step = np.linalg.solve(H, g)
            beta -= step
            if np.abs(step).max() < 1e-8:
                break
        return cls(beta[0], beta[1:], {"features": FEATURES, "n": int(len(y)), "positives": int(y.sum())})

    def save(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({"bias": self.bias, "weights": self.w.tolist(), **{k: v for k, v in self.meta.items()
                                                                          if k not in ("bias", "weights")}}, f, indent=2)
