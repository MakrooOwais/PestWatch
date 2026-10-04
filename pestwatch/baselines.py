"""Simpler detectors used as comparisons for the space-time scan."""
import numpy as np

from .simulation import SimResult


def _visible(r: SimResult, now: float, hours: float, p_min: float):
    m = (r.reports["t_sync"] <= now) & (r.reports["t_cap"] > now - hours / 24) & (r.p_target >= p_min)
    return r.reports["farm"][m]


def regional_count_series(r: SimResult, hours: float = 72, p_min: float = 0.7) -> np.ndarray:
    """No spatial aggregation: count of confident TARGET reports anywhere in the region."""
    return np.array([len(_visible(r, t, hours, p_min)) for t in r.times], dtype=float)


def single_farm_series(r: SimResult, hours: float = 72, p_min: float = 0.7) -> np.ndarray:
    """Individual-farm rule: most confident TARGET reports from any one farm."""
    out = np.zeros(len(r.times))
    for i, t in enumerate(r.times):
        f = _visible(r, t, hours, p_min)
        out[i] = np.bincount(f).max() if len(f) else 0
    return out
