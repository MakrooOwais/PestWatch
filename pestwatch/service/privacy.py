"""Privacy for shared outputs: coarsened locations, consent filtering, small-cell suppression."""
import csv
import io
import math
from collections import Counter
from typing import Dict, Tuple

import numpy as np

from ..alerts import RISK_ORDER

GRID_KM = 1.0
MIN_CELL_FARMS = 3   # cells with fewer consenting farms are not published


def cell_of(lat: float, lon: float, lat0: float, km: float = GRID_KM) -> Tuple[int, int]:
    dlat = km / 110.574
    dlon = km / (111.320 * math.cos(math.radians(lat0)))
    return int(math.floor(lat / dlat)), int(math.floor(lon / dlon))


def cell_center(cell: Tuple[int, int], lat0: float, km: float = GRID_KM) -> Tuple[float, float]:
    dlat = km / 110.574
    dlon = km / (111.320 * math.cos(math.radians(lat0)))
    return (cell[0] + 0.5) * dlat, (cell[1] + 0.5) * dlon


def coarsen(lat: float, lon: float, lat0: float, km: float = GRID_KM) -> Tuple[float, float]:
    return cell_center(cell_of(lat, lon, lat0, km), lat0, km)


def public_risk(state) -> dict:
    """Grid-level GeoJSON: no farm ids, no exact coordinates, consenting farmers only."""
    ev = state.evaluate()
    reg, cfg = state.region, state.cfg
    latlon = reg.latlon(cfg.region)
    lat0 = cfg.region.origin_lat
    consent = np.array([p.consent for p in state.profiles])
    cells: Dict[Tuple[int, int], list] = {}
    for f in np.flatnonzero(consent):
        cells.setdefault(cell_of(latlon[f, 0], latlon[f, 1], lat0), []).append(int(f))

    farm_a, tc, ts, p, now = ev["arrays"]
    recent = (ts <= now) & (tc > now - 3) & (p >= cfg.scan.positive_p) & consent[farm_a]
    watch = set()
    for c in ev["clusters"]:
        if c["level"] == "WATCH":
            watch |= set(c["members"])
    features = []
    for cell, farms in sorted(cells.items()):
        if len(farms) < MIN_CELL_FARMS:
            continue
        risks = [ev["alerts"][f].risk for f in farms if f in ev["alerts"]]
        level = max(risks, key=RISK_ORDER.get) if risks else ("WATCH" if watch & set(farms) else "QUIET")
        in_cell = recent & np.isin(farm_a, farms)
        lat, lon = cell_center(cell, lat0)
        village = Counter(reg.village_names[reg.farm_village[f]] for f in farms).most_common(1)[0][0]
        features.append({"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": [round(lon, 4), round(lat, 4)]},
                         "properties": {"cell": f"{cell[0]}_{cell[1]}", "village": village, "risk": level,
                                        "n_reports_72h": int(in_cell.sum()),
                                        "n_farms_reporting": int(len(np.unique(farm_a[in_cell])))}})
    return {"type": "FeatureCollection", "grid_km": GRID_KM, "min_cell_farms": MIN_CELL_FARMS,
            "features": features}


def export_geojson(state) -> dict:
    """Admin-only: exact farm locations and status."""
    ev = state.evaluate()
    reg, cfg = state.region, state.cfg
    latlon = reg.latlon(cfg.region)
    feats = []
    for prof in state.profiles:
        f = prof.farm
        a = ev["alerts"].get(f)
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [round(float(latlon[f, 1]), 6),
                                                                    round(float(latlon[f, 0]), 6)]},
                      "properties": {"farm": f, "village": reg.village_names[reg.farm_village[f]],
                                     "language": prof.language, "channel": prof.channel,
                                     "consent": prof.consent, "subscribed": prof.subscribed,
                                     "risk": a.risk if a else None}})
    return {"type": "FeatureCollection", "features": feats}


def export_csv(store) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "farm", "source", "model", "captured_at", "received_at", "p_target", "probs"])
    for o in store.observations():
        w.writerow([o["id"], o["farm"], o["source"], o["model"], o["captured_at"], round(o["received_at"], 3),
                    round(o["probs"][1], 4), " ".join(f"{x:.4f}" for x in o["probs"])])
    return buf.getvalue()
