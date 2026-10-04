"""Impact metrics comparing a PestWatch run with its no-PestWatch counterfactual."""
from typing import Dict, Optional

import numpy as np

from .simulation import SimResult


def _first(times: np.ndarray, mask: np.ndarray) -> Optional[float]:
    return float(times[np.argmax(mask)]) if mask.any() else None


def first_alert_time(r: SimResult, after: float = -np.inf) -> Optional[float]:
    """First ALERT-level detection (in the counterfactual arm: when PestWatch *would* have alerted)."""
    ts = [e.t for evs in r.top_clusters for e in evs if e.level == "ALERT" and e.t >= after]
    return min(ts) if ts else None


def widespread_time(r: SimResult) -> Optional[float]:
    vis = (r.damage >= r.cfg.pest.visible_threshold).mean(1)
    return _first(r.times, vis >= r.cfg.pest.widespread_frac)


def conventional_time(r: SimResult) -> Optional[float]:
    k = r.cfg.response.conventional_farms
    noticed = np.sort(r.noticed_at)
    if len(noticed) < k or not np.isfinite(noticed[k - 1]):
        return None
    return float(noticed[k - 1] + r.cfg.response.conventional_lag_days)


def false_alert_events(r: SimResult) -> int:
    """ALERT cluster detections with no truly infested farm inside the cluster."""
    n, seen = 0, set()
    for evs in r.top_clusters:
        for e in evs:
            if e.level != "ALERT":
                continue
            step = int(round(e.t / r.cfg.dt_days))
            members = r.region.dist[e.center_farm] <= e.radius_km
            if not (r.damage[step][members] > 0).any() and (e.cluster_id, "fa") not in seen:
                seen.add((e.cluster_id, "fa"))
                n += 1
    return n


def crop_loss_usd(r: SimResult) -> Dict[str, float]:
    ec = r.cfg.economics
    value = r.region.farm_ha * ec.yield_t_per_ha * ec.price_usd_per_t
    loss = float((value * np.minimum(1.0, ec.loss_per_damage * r.peak_damage)).sum())
    treat = float((r.region.farm_ha * ec.treatment_cost_usd_per_ha)[np.isfinite(r.treated_at)].sum())
    return {"yield_loss_usd": loss, "treatment_cost_usd": treat, "total_usd": loss + treat,
            "crop_value_usd": float(value.sum())}


def farm_level(pw: SimResult, cf: SimResult = None, horizon_days: float = 14.0) -> Dict[str, float]:
    """Recall: infested farms warned before damage became visible.
    Precision: alerted farms that were, or within ``horizon_days`` would have become, infested.
    Judged against the counterfactual when given: a farm that stayed clean *because* it
    was warned and treated still counts as a correct alert."""
    truth = (cf if cf is not None else pw).infected_at
    first_alert = {}
    tier = {}
    for a in pw.alerts:
        if a.farm not in first_alert:
            first_alert[a.farm] = a.t
            tier[a.farm] = a.risk
    infected = np.flatnonzero(np.isfinite(pw.infected_at))
    warned = [f for f in infected if f in first_alert and first_alert[f] < pw.visible_at[f]]
    out = {"infested_farms": int(len(infected)), "warned_before_visible": len(warned),
           "recall": len(warned) / len(infected) if len(infected) else float("nan"),
           "alerted_farms": len(first_alert)}
    for name, keep in (("all", None), ("HIGH", "HIGH"), ("MEDIUM", "MEDIUM"), ("LOW", "LOW")):
        fs = [f for f in first_alert if keep is None or tier[f] == keep]
        tp = [f for f in fs if min(truth[f], pw.infected_at[f]) <= first_alert[f] + horizon_days]
        out[f"precision_{name}"] = len(tp) / len(fs) if fs else float("nan")
        out[f"n_alerted_{name}"] = len(fs)
    return out


def compare(pw: SimResult, cf: SimResult) -> Dict[str, object]:
    t0 = pw.t_intro
    t_alert = first_alert_time(pw, after=t0 if t0 is not None else -np.inf)
    t_wide = widespread_time(cf)
    t_conv = conventional_time(cf)
    loss_pw, loss_cf = crop_loss_usd(pw), crop_loss_usd(cf)
    vt = pw.cfg.pest.visible_threshold
    m = {
        "seed": pw.seed,
        "n_farms": pw.region.n_farms,
        "n_reports": int(len(pw.reports["farm"])),
        "t_intro": t0,
        "t_first_alert": t_alert,
        "t_first_farmer_notice": float(np.min(cf.noticed_at)) if np.isfinite(cf.noticed_at).any() else None,
        "t_conventional": t_conv,
        "t_widespread": t_wide,
        "lead_vs_widespread_days": None if t_alert is None or t_wide is None else t_wide - t_alert,
        "lead_vs_conventional_days": None if t_alert is None or t_conv is None else t_conv - t_alert,
        "detect_delay_days": None if t_alert is None or t0 is None else t_alert - t0,
        "false_alert_events": false_alert_events(pw),
        "farms_visible_end_pw": int((pw.damage[-1] >= vt).sum()),
        "farms_visible_end_cf": int((cf.damage[-1] >= vt).sum()),
        "farms_infested_pw": int(np.isfinite(pw.infected_at).sum()),
        "farms_infested_cf": int(np.isfinite(cf.infected_at).sum()),
        "loss_pw": loss_pw,
        "loss_cf": loss_cf,
        "loss_avoided_usd": loss_cf["total_usd"] - loss_pw["total_usd"],
        "loss_avoided_pct": (loss_cf["yield_loss_usd"] - loss_pw["yield_loss_usd"]) / loss_cf["yield_loss_usd"]
        if loss_cf["yield_loss_usd"] > 0 else 0.0,
    }
    m.update(farm_level(pw, cf))
    per_farm = np.bincount([a.farm for a in pw.alerts], minlength=pw.region.n_farms)
    m.update({
        "alerts_sent": len(pw.alerts),
        "alerts_per_farm_mean": float(per_farm.mean()),
        "alerts_per_farm_max": int(per_farm.max()) if len(per_farm) else 0,
        "alerts_suppressed_by_budget": pw.suppressed_alerts,
        "scout_requests": len(pw.scout_requests),
        "scout_messages": sum(1 for n in pw.notices if n.kind == "scout_request"),
        "all_clear_messages": sum(1 for n in pw.notices if n.kind == "all_clear"),
        "finance_triggers": len(pw.triggers),
        "t_first_action": pw.first_action_time,
    })
    from .costs import season_cost  # local import: costs depends on simulation
    cost = season_cost(pw)
    m["cost"] = cost
    m["benefit_cost_ratio"] = m["loss_avoided_usd"] / cost["total"] if cost["total"] else None
    return m
