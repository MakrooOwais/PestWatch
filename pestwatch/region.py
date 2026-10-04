"""Synthetic farming region: villages with clustered smallholder farms."""
import math
from dataclasses import dataclass
from typing import List

import numpy as np

from .config import RegionConfig, ReportingConfig

VILLAGE_NAMES = ["Namasoli", "Kabula", "Sirende", "Mukuyu", "Lutonyi", "Chebukwa",
                 "Matili", "Siboti", "Naitiri", "Kamukuywa"]


@dataclass
class Region:
    village_names: List[str]
    village_xy: np.ndarray        # (V, 2) km
    farm_xy: np.ndarray           # (F, 2) km
    farm_village: np.ndarray      # (F,) int
    farm_ha: np.ndarray           # (F,)
    participates: np.ndarray      # (F,) bool, takes photos with the app
    photo_rate: np.ndarray        # (F,) sessions/day (0 for non-participants)
    other_damage: np.ndarray      # (F,) background non-TARGET damage share
    online_share: np.ndarray      # (F,) P(report syncs immediately)
    offline_delay_h: np.ndarray   # (F,) mean delay when offline
    dist: np.ndarray              # (F, F) km

    @property
    def n_farms(self) -> int:
        return len(self.farm_xy)

    def latlon(self, cfg: RegionConfig) -> np.ndarray:
        lat = cfg.origin_lat + self.farm_xy[:, 1] / 110.574
        lon = cfg.origin_lon + self.farm_xy[:, 0] / (111.320 * math.cos(math.radians(cfg.origin_lat)))
        return np.stack([lat, lon], axis=1)


def generate_region(rng: np.random.Generator, rc: RegionConfig, rep: ReportingConfig,
                    photo_sessions_per_day: float) -> Region:
    # Villages: rejection-sample centres with minimum spacing.
    margin = 1.5
    centres: List[np.ndarray] = []
    while len(centres) < rc.n_villages:
        c = rng.uniform(margin, rc.size_km - margin, size=2)
        if all(np.linalg.norm(c - o) > 3.2 for o in centres):
            centres.append(c)
    village_xy = np.array(centres)

    xy, vil = [], []
    for v, c in enumerate(village_xy):
        n = int(rng.integers(rc.farms_per_village[0], rc.farms_per_village[1] + 1))
        pts = c + rng.normal(0, rc.village_spread_km, size=(n, 2))
        xy.append(np.clip(pts, 0.2, rc.size_km - 0.2))
        vil.extend([v] * n)
    farm_xy = np.vstack(xy)
    farm_village = np.array(vil)
    F = len(farm_xy)

    participates = rng.random(F) < rc.app_participation
    # Heterogeneous engagement: gamma with mean photo_sessions_per_day.
    photo_rate = rng.gamma(2.0, photo_sessions_per_day / 2.0, size=F) * participates

    V = rc.n_villages
    v_other = rng.uniform(*rep.other_damage_range, size=V)
    v_online = rng.uniform(*rep.online_share_range, size=V)
    v_delay = rng.uniform(*rep.offline_delay_h_range, size=V)

    diff = farm_xy[:, None, :] - farm_xy[None, :, :]
    dist = np.sqrt((diff ** 2).sum(-1))

    return Region(
        village_names=VILLAGE_NAMES[:V],
        village_xy=village_xy,
        farm_xy=farm_xy,
        farm_village=farm_village,
        farm_ha=rng.uniform(*rc.farm_ha, size=F),
        participates=participates,
        photo_rate=photo_rate,
        other_damage=v_other[farm_village],
        online_share=v_online[farm_village],
        offline_delay_h=v_delay[farm_village],
        dist=dist,
    )
