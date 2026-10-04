"""Simulated environment signals: wind, temperature, pheromone traps, migration forecast.

These are the non-photo evidence streams PestWatch can fuse:
- **Wind** carries spores (downwind farms are at higher risk) and shapes alert zones.
- **Temperature** sets how fast the disease develops, used to forecast when
  damage becomes visible.
- **Pheromone traps** (as in FAO FAMEWS) catch male moths: a migratory flight
  produces a spike days before any larval damage exists.
- **Migration forecast**: a regional moth-flight risk index (in practice from wind
  trajectory models), noisy and sometimes wrong.
"""
from dataclasses import dataclass
from typing import List

import numpy as np

from .config import Config
from .region import Region


@dataclass
class Landing:
    t: float
    centre: np.ndarray
    farms: List[int]


@dataclass
class Environment:
    wind_dir: np.ndarray        # (D,) radians, direction the wind blows towards
    wind_speed: np.ndarray      # (D,) m/s
    temp: np.ndarray            # (D,) daily mean °C
    migration: np.ndarray       # (D,) forecast moth-flight risk index, 0..1
    trap_xy: np.ndarray         # (T, 2) km
    trap_village: np.ndarray    # (T,)
    trap_farm_dist: np.ndarray  # (T, F) km
    bearing: np.ndarray         # (F, F) bearing from farm i (col) to farm j (row)
    flights: List[Landing]      # moth flights that pass over without laying eggs (trap & forecast only)

    def day(self, t: float) -> int:
        return int(min(max(t, 0), len(self.temp) - 1))

    def growth_mult(self, t: float, cfg: Config) -> float:
        e = cfg.environment
        # Bell-shaped temperature response: 1 at the optimum, 0 below the base temperature.
        T = self.temp[self.day(t)]
        if T <= e.pest_base_temp_c:
            return 0.0
        return max(0.0, 1.0 - ((T - e.pest_ref_temp_c) / e.pest_temp_width_c) ** 2)

    def recent_wind(self, t: float, days: int = 3):
        """Circular-mean direction and mean speed over the last ``days``."""
        d = self.day(t)
        sl = slice(max(0, d - days + 1), d + 1)
        ang = np.arctan2(np.sin(self.wind_dir[sl]).mean(), np.cos(self.wind_dir[sl]).mean())
        return float(ang), float(self.wind_speed[sl].mean())

    def dispersal_kernel(self, iso: np.ndarray, t: float, cfg: Config) -> np.ndarray:
        """Anisotropic kernel K[j, i]: inoculum from farm i reaching farm j, stronger downwind."""
        e = cfg.environment
        d = self.day(t)
        a = e.wind_anisotropy * min(1.5, self.wind_speed[d] / e.wind_speed_mean)
        return iso * np.clip(1.0 + a * np.cos(self.bearing - self.wind_dir[d]), 0.0, None)


def generate_environment(rng: np.random.Generator, cfg: Config, region: Region,
                         landings: List[Landing]) -> Environment:
    e = cfg.environment
    D = int(np.ceil(cfg.days)) + 1
    # Wind: persistent random walk around a seasonal prevailing direction.
    prevailing = rng.uniform(-np.pi, np.pi)
    wd = np.empty(D)
    wd[0] = prevailing
    for k in range(1, D):
        wd[k] = e.wind_persistence * wd[k - 1] + (1 - e.wind_persistence) * prevailing + rng.normal(0, 0.35)
    ws = rng.lognormal(np.log(e.wind_speed_mean), 0.35, D)
    temp = e.temp_mean_c + 1.5 * np.sin(np.arange(D) / D * np.pi) + rng.normal(0, e.temp_daily_sd, D)

    # Moth flights that pass through without establishing: they hit traps and the forecast
    # just like a real landing, which is why traps alone can't justify an alert.
    flights = []
    for _ in range(rng.poisson(e.passing_flights_mean)):
        centre = region.farm_xy[rng.integers(region.n_farms)] + rng.normal(0, 0.3, 2)
        flights.append(Landing(float(rng.uniform(1, cfg.days - 5)), centre, []))

    # Migration forecast: low noise, a bump before each flight (landing or passing), sometimes a false spike.
    mig = np.clip(rng.uniform(0.0, 0.25, D), 0, 1)
    days = np.arange(D)
    for L in list(landings) + flights:
        bump = rng.uniform(0.5, 0.9)
        on = (days >= L.t - e.migration_lead_days) & (days <= L.t + cfg.pest.landing_days)
        mig[on] = np.maximum(mig[on], bump + rng.normal(0, 0.05, on.sum()))
    if rng.random() < e.migration_false_spike_prob:
        s = rng.integers(0, D - 3)
        mig[s:s + 3] = np.maximum(mig[s:s + 3], rng.uniform(0.4, 0.75))
    mig = np.clip(mig, 0, 1)

    # Traps near village centres.
    T = e.traps_per_village * len(region.village_xy)
    trap_village = np.repeat(np.arange(len(region.village_xy)), e.traps_per_village)
    trap_xy = (region.village_xy[trap_village] + rng.normal(0, 0.3, (T, 2))) if T else np.zeros((0, 2))
    trap_farm_dist = np.sqrt(((trap_xy[:, None, :] - region.farm_xy[None]) ** 2).sum(-1))

    dx = region.farm_xy[:, None, 0] - region.farm_xy[None, :, 0]   # x_j - x_i  (row j, col i)
    dy = region.farm_xy[:, None, 1] - region.farm_xy[None, :, 1]
    bearing = np.arctan2(dy, dx)
    return Environment(wd, ws, temp, mig, trap_xy, trap_village, trap_farm_dist, bearing, flights)


def trap_rate(env: Environment, cfg: Config, t: float, I: np.ndarray, emission: np.ndarray,
              landings: List[Landing]) -> np.ndarray:
    """Expected male-moth catches per day at each trap."""
    e = cfg.environment
    lam = np.full(len(env.trap_xy), e.trap_background_per_day)
    lam += e.trap_local_gain * (np.exp(-env.trap_farm_dist / e.trap_reach_km) * (I * emission)).sum(1)
    for L in list(landings) + env.flights:
        if L.t <= t <= L.t + cfg.pest.landing_days:
            d2 = ((env.trap_xy - L.centre) ** 2).sum(1)
            lam += e.trap_flight_gain * np.exp(-d2 / (2 * (1.5 * cfg.pest.landing_sigma_km) ** 2))
    return lam


def trap_anomaly(readings: dict, env: Environment, cfg: Config, now: float, days: float = 3.0) -> np.ndarray:
    """Per-trap z-score of catches synced in the last ``days`` vs background."""
    T = len(env.trap_xy)
    if not len(readings["trap"]):
        return np.zeros(T)
    m = (readings["t_sync"] <= now) & (readings["t_read"] > now - days)
    c = np.bincount(readings["trap"][m], weights=readings["count"][m], minlength=T)
    n_reads = np.bincount(readings["trap"][m], minlength=T)
    mu = cfg.environment.trap_background_per_day * cfg.environment.trap_read_days * np.maximum(n_reads, 1)
    return np.where(n_reads > 0, (c - mu) / np.sqrt(mu), 0.0)
