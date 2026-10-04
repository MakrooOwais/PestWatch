"""Prospective space-time outbreak detection.

Expectation-based Poisson scan statistic (Neill et al. 2005, a prospective
variant of Kulldorff's space-time scan). Candidate zones are cylinders: a
circle around each farm (several radii) x a recent time window ending now.

For each zone:
    C = sum of P(TARGET) over photos captured in the window (soft counts, so many
        weak low-confidence detections add up)
    E = sum over photos of the farm's baseline P(TARGET), estimated from that
        farm's own history shrunk toward the regional rate (empirical Bayes),
        so persistently "noisy" farms/villages do not trigger alerts
    LLR = C log(C/E) - (C - E)   if C > E else 0

Conditioning E on the photos actually taken makes the statistic robust to
changes in reporting effort and to offline sync delays.
"""
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from .config import ScanConfig


@dataclass
class Cluster:
    center_farm: int
    radius_km: float
    window_h: float
    llr: float
    observed: float
    expected: float
    n_reports: int          # photos with P(TARGET) >= positive_p
    n_farms: int            # distinct farms with such a photo
    members: np.ndarray     # farm indices inside the circle

    @property
    def relative_risk(self) -> float:
        return self.observed / max(self.expected, 1e-9)


class _DenseMembership:
    def __init__(self, dist, radii):
        self.m = np.concatenate([(dist <= r).astype(np.float64) for r in radii], axis=0)

    def dot(self, v):
        return self.m @ v

    def row(self, z):
        return np.flatnonzero(self.m[z] > 0)


class _SparseMembership:
    """CSR zone->farm lists built with a grid index: O(F * neighbours) memory, for national scale."""

    def __init__(self, xy, radii):
        rmax = max(radii)
        cell = np.floor(xy / rmax).astype(np.int64)
        buckets = {}
        for i, key in enumerate(map(tuple, cell)):
            buckets.setdefault(key, []).append(i)
        buckets = {k: np.array(v) for k, v in buckets.items()}
        rows = [[] for _ in radii]
        for i, (cx, cy) in enumerate(cell):
            cand = np.concatenate([buckets.get((cx + a, cy + b), np.zeros(0, np.int64))
                                   for a in (-1, 0, 1) for b in (-1, 0, 1)])
            d = np.sqrt(((xy[cand] - xy[i]) ** 2).sum(1))
            for k, r in enumerate(radii):
                rows[k].append(np.sort(cand[d <= r]))
        flat = [row for per_r in rows for row in per_r]
        self.indptr = np.concatenate([[0], np.cumsum([len(r) for r in flat])])
        self.indices = np.concatenate(flat)

    def dot(self, v):
        return np.add.reduceat(np.asarray(v, dtype=float)[self.indices], self.indptr[:-1])

    def row(self, z):
        return self.indices[self.indptr[z]:self.indptr[z + 1]]


class SpaceTimeScan:
    DENSE_MAX_FARMS = 3000

    def __init__(self, dist: np.ndarray = None, cfg: ScanConfig = None, xy: np.ndarray = None):
        """Pass ``dist`` (F x F km) for small regions, or ``xy`` (F x 2 km) to use a sparse spatial index."""
        self.cfg = cfg
        self.radii = np.array(cfg.radii_km)
        if xy is not None and (dist is None or len(xy) > self.DENSE_MAX_FARMS):
            self.F = len(xy)
            self.member = _SparseMembership(np.asarray(xy, float), list(self.radii))
        else:
            self.F = dist.shape[0]
            self.member = _DenseMembership(dist, self.radii)
        self.zone_center = np.tile(np.arange(self.F), len(self.radii))
        self.zone_radius = np.repeat(self.radii, self.F)

    def baseline_rates(self, farm, t_cap, p, t_end) -> np.ndarray:
        c = self.cfg
        h = (t_cap >= t_end - c.history_days) & (t_cap < t_end)
        n_f = np.bincount(farm[h], minlength=self.F).astype(float)
        c_f = np.bincount(farm[h], weights=p[h], minlength=self.F)
        regional = (c_f.sum() + c.prior_rate * c.prior_strength) / (n_f.sum() + c.prior_strength)
        return (c_f + regional * c.farm_shrinkage) / (n_f + c.farm_shrinkage)

    def zone_table(self, farm: np.ndarray, t_cap: np.ndarray, t_sync: np.ndarray, p: np.ndarray,
                   now: float) -> dict:
        """Best-window statistics for every zone (centre x radius): llr, C, E, NF, K, w."""
        c = self.cfg
        seen = t_sync <= now
        farm, t_cap, p = farm[seen], t_cap[seen], p[seen]
        sig = np.where(p >= c.min_signal_p, p, 0.0)
        best = None
        for w_h in c.windows_h:
            w = w_h / 24.0
            # Baseline from history strictly before the window, so the outbreak
            # being tested does not inflate its own expectation.
            rate = self.baseline_rates(farm, t_cap, sig, now - w - c.history_gap_days)
            inw = t_cap > now - w
            fw, pw = farm[inw], p[inw]
            n_f = np.bincount(fw, minlength=self.F).astype(float)
            # Cap each farm's contribution so one farm (or a spammer) can't make a cluster alone.
            c_f = np.minimum(np.bincount(fw, weights=sig[inw], minlength=self.F), c.max_per_farm)
            pos = pw >= c.positive_p
            k_f = np.bincount(fw[pos], minlength=self.F).astype(float)
            C = self.member.dot(c_f)
            E = self.member.dot(np.minimum(n_f * rate, c.max_per_farm))
            NF = self.member.dot((k_f > 0).astype(float))
            K = self.member.dot(k_f)
            with np.errstate(divide="ignore", invalid="ignore"):
                llr = np.where(C > E, C * np.log(C / np.maximum(E, 1e-9)) - (C - E), 0.0)
            llr = np.where(NF >= c.min_farms, llr, 0.0)
            rec = dict(llr=llr, C=C, E=E, NF=NF, K=K)
            if best is None:
                best = {k: v.copy() for k, v in rec.items()}
                best["w"] = np.full(len(llr), w_h)
            else:
                better = llr > best["llr"]
                for key in rec:
                    best[key] = np.where(better, rec[key], best[key])
                best["w"] = np.where(better, w_h, best["w"])
        return best

    def zone_index(self, centre: int, radius_km: float) -> int:
        k = int(np.argmin(np.abs(self.radii - radius_km)))
        return k * self.F + centre

    def scan(self, farm: np.ndarray, t_cap: np.ndarray, t_sync: np.ndarray, p: np.ndarray,
             now: float) -> List[Cluster]:
        """Return non-overlapping zones ranked by LLR (only data synced by ``now``)."""
        c = self.cfg
        best = self.zone_table(farm, t_cap, t_sync, p, now)
        order = np.argsort(-best["llr"])
        clusters: List[Cluster] = []
        covered = np.zeros(self.F, dtype=bool)
        for z in order:
            if best["llr"][z] <= 0 or len(clusters) >= c.max_clusters:
                break
            members = self.member.row(z)
            if covered[members].any():
                continue
            covered[members] = True
            clusters.append(Cluster(
                center_farm=int(self.zone_center[z]), radius_km=float(self.zone_radius[z]),
                window_h=float(best["w"][z]), llr=float(best["llr"][z]),
                observed=float(best["C"][z]), expected=float(best["E"][z]),
                n_reports=int(best["K"][z]), n_farms=int(best["NF"][z]), members=members))
        return clusters

    def max_score(self, farm, t_cap, t_sync, p, now) -> float:
        cl = self.scan(farm, t_cap, t_sync, p, now)
        return cl[0].llr if cl else 0.0


def reports_in_area(farm, t_cap, t_sync, p, now, members: np.ndarray, hours: float,
                    positive_p: float) -> (int, int):
    """Positive reports and distinct farms within ``members`` over the last ``hours``."""
    m = (t_sync <= now) & (t_cap > now - hours / 24.0) & (p >= positive_p) & np.isin(farm, members)
    return int(m.sum()), int(len(np.unique(farm[m])))


def best_or_none(clusters: List[Cluster]) -> Optional[Cluster]:
    return clusters[0] if clusters else None
