"""Generate the demo scenario consumed by the dashboard (web/data/scenario.js).

Picks a *representative* season (lead time closest to the median of the first
N seeds) rather than the most impressive one, unless --seed is given.

Usage: python3 scripts/run_demo.py [--seed 7] [--candidates 20]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pestwatch  # noqa: E402,F401  (pins BLAS threads before numpy loads)
import numpy as np  # noqa: E402
from pestwatch import i18n  # noqa: E402
from pestwatch.alerts import render_voice  # noqa: E402
from pestwatch.config import CLASSES, TARGET, Config  # noqa: E402
from pestwatch.farmers import generate_profiles  # noqa: E402
from pestwatch.classifier import SimulatedClassifier  # noqa: E402
from pestwatch.messaging import deliver, deliver_notice, render_notice  # noqa: E402
from pestwatch.simulation import SOURCE_NAMES  # noqa: E402
from pestwatch.metrics import compare  # noqa: E402
from pestwatch.simulation import SimResult, simulate  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def load_calibration(cfg: Config) -> Config:
    cfg.fusion.weights_path = os.path.join(ROOT, "data", "fusion.json")
    path = os.path.join(ROOT, "data", "calibration.json")
    if os.path.exists(path):
        with open(path) as f:
            c = json.load(f)
        cfg.scan.threshold = c["scan_threshold"]
        cfg.fusion.alert_p = c.get("fusion_alert_p", cfg.fusion.alert_p)
    else:
        print("No data/calibration.json; using defaults. Run scripts/evaluate.py first.")
    return cfg


def representative_seed(cfg: Config, n: int) -> int:
    leads = {}
    for s in range(n):
        m = compare(simulate(cfg, s, True), simulate(cfg, s, False))
        if m["lead_vs_conventional_days"] is not None and m["t_widespread"] is not None:
            leads[s] = m["lead_vs_conventional_days"]
    med = np.median(list(leads.values()))
    return min(leads, key=lambda s: abs(leads[s] - med))


def fin(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 3)


def r3(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 3)


def export(pw: SimResult, cf: SimResult, metrics: dict, evaluation) -> dict:
    cfg, reg = pw.cfg, pw.region
    latlon = reg.latlon(cfg.region)
    probs = pw.reports["probs"]
    top = probs.argmax(1)
    order = np.argsort(pw.reports["t_cap"])
    profiles = generate_profiles(reg, cfg.language, pw.seed)
    langs = i18n.languages()
    return {
        "languages": [{"code": c, "name": i18n.catalogs()[c]["meta"]["native_name"],
                       "review": i18n.catalogs()[c]["meta"]["review_status"]} for c in langs],
        "profiles": [p.to_json() for p in profiles],
        "meta": {
            "seed": pw.seed, "dt": cfg.dt_days, "days": cfg.days, "size_km": cfg.region.size_km,
            "threshold": cfg.scan.threshold, "watch": cfg.scan.threshold * cfg.scan.watch_fraction,
            "visible_threshold": cfg.pest.visible_threshold, "positive_p": cfg.scan.positive_p,
            "classes": CLASSES, "buffer_km": cfg.alert.buffer_km,
            "landing_farms": pw.seed_farms,
            "decision": "probability" if cfg.features.fusion else "llr",
            "alert_p": cfg.fusion.alert_p, "watch_p": cfg.scouting.watch_p, "watch_llr": cfg.scouting.watch_llr,
            "features": vars(cfg.features), "classifier": SimulatedClassifier(cfg.classifier).source,
            "dose_l_per_ha": cfg.response.control_dose_l_per_ha, "sources": SOURCE_NAMES,
            "trap_background_per_read": cfg.environment.trap_background_per_day * cfg.environment.trap_read_days,
        },
        "landings": [{"t": L.t, "x": round(float(L.centre[0]), 3), "y": round(float(L.centre[1]), 3),
                      "farms": L.farms} for L in pw.landings],
        "env": {"wind_dir": np.round(pw.env.wind_dir, 3).tolist(), "wind_speed": np.round(pw.env.wind_speed, 2).tolist(),
                "temp": np.round(pw.env.temp, 1).tolist(), "migration": np.round(pw.env.migration, 2).tolist()},
        "traps": [{"id": k, "x": round(float(x), 3), "y": round(float(y), 3), "village": int(v)}
                  for k, ((x, y), v) in enumerate(zip(pw.env.trap_xy, pw.env.trap_village))],
        "trap_readings": [{"k": int(k), "t": round(float(t), 3), "ts": round(float(ts), 3), "n": int(n)}
                          for k, t, ts, n in zip(pw.trap_readings["trap"], pw.trap_readings["t_read"],
                                                 pw.trap_readings["t_sync"], pw.trap_readings["count"])],
        "probability": None if pw.probability is None else np.round(pw.probability, 3).tolist(),
        "village_probability": None if pw.village_probability is None
        else np.round(pw.village_probability, 3).tolist(),
        "lead_farmers": pw.lead_farmers, "spammers": pw.spammers,
        "scout_requests": [{"t": r["t"], "cid": r["cluster_id"], "c": r["center_farm"], "farms": r["farms"]}
                           for r in pw.scout_requests],
        "notices": [{"t": round(n.t, 3), "f": n.farm, "kind": n.kind, "cid": n.cluster_id, "days": n.days,
                     "delivery": deliver_notice(n, profiles[n.farm]).to_json()} for n in pw.notices],
        "notice_text": {kind: {lg: {ch: render_notice(kind, lg, ch, int(cfg.alert.all_clear_days))
                                    for ch in ("app", "sms", "voice")} for lg in langs}
                        for kind in ("scout_request", "all_clear")},
        "triggers": [{**tr, "t": round(tr["t"], 3)} for tr in pw.triggers],
        "villages": [{"name": n, "x": round(float(x), 3), "y": round(float(y), 3)}
                     for n, (x, y) in zip(reg.village_names, reg.village_xy)],
        "farms": [{"id": i, "x": round(float(reg.farm_xy[i, 0]), 3), "y": round(float(reg.farm_xy[i, 1]), 3),
                   "lat": round(float(latlon[i, 0]), 5), "lon": round(float(latlon[i, 1]), 5),
                   "village": int(reg.farm_village[i]), "ha": round(float(reg.farm_ha[i]), 2),
                   "app": bool(reg.participates[i]),
                   "infected_pw": fin(pw.infected_at[i]), "infected_cf": fin(cf.infected_at[i]),
                   "visible_pw": fin(pw.visible_at[i]), "visible_cf": fin(cf.visible_at[i]),
                   "treated_pw": fin(pw.treated_at[i]), "treated_cf": fin(cf.treated_at[i])}
                  for i in range(reg.n_farms)],
        "times": [round(float(t), 3) for t in pw.times],
        "damage_pw": np.round(pw.damage, 3).tolist(),
        "damage_cf": np.round(cf.damage, 3).tolist(),
        "scores": np.round(pw.scores, 2).tolist(),
        "reports": [{"f": int(pw.reports["farm"][i]), "tc": round(float(pw.reports["t_cap"][i]), 3),
                     "ts": round(float(pw.reports["t_sync"][i]), 3), "p": round(float(probs[i, TARGET]), 2),
                     "top": int(top[i]), "conf": round(float(probs[i, top[i]]), 2),
                     "src": int(pw.reports["src"][i])} for i in order],
        "clusters": [[{"id": e.cluster_id, "c": e.center_farm, "r": e.radius_km, "w": e.window_h,
                       "llr": round(e.llr, 2), "rr": round(e.relative_risk, 2), "n": e.n_reports,
                       "nf": e.n_farms, "lvl": e.level, "p": r3(e.probability), "nconf": e.n_confirmed,
                       "tz": round(e.trap_z, 1), "dtv": r3(e.days_to_visible), "trap": e.trap_led}
                      for e in evs] for evs in pw.top_clusters],
        "alerts": [{**a.to_json(), "msg": a.messages(), "sms": {lg: a.sms(lg) for lg in langs},
                    "voice": {lg: render_voice(a, lg) for lg in langs},
                    "delivery": deliver(a, profiles[a.farm]).to_json()} for a in pw.alerts],
        "nuisance": pw.nuisance,
        "metrics": metrics,
        "evaluation": evaluation,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int)
    ap.add_argument("--candidates", type=int, default=20)
    a = ap.parse_args()
    cfg = load_calibration(Config())
    seed = a.seed if a.seed is not None else representative_seed(cfg, a.candidates)
    pw, cf = simulate(cfg, seed, True), simulate(cfg, seed, False)
    metrics = compare(pw, cf)
    ev_path = os.path.join(ROOT, "reports", "evaluation.json")
    evaluation = json.load(open(ev_path)) if os.path.exists(ev_path) else None
    data = export(pw, cf, metrics, evaluation)
    out = os.path.join(ROOT, "web", "data")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "scenario.json"), "w") as f:
        json.dump(data, f, separators=(",", ":"))
    # Also as a script so the dashboard works from file:// with no server.
    with open(os.path.join(out, "scenario.js"), "w", encoding="utf-8") as f:
        f.write("window.SCENARIO=")
        json.dump(data, f, separators=(",", ":"))
        f.write(";\n")
    # Field-app strings for every language, loadable offline / from file://.
    with open(os.path.join(out, "i18n.js"), "w", encoding="utf-8") as f:
        f.write("window.I18N=")
        json.dump(i18n.ui_bundle(), f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    print(f"Seed {seed}: first alert day {metrics['t_first_alert']}, "
          f"lead vs conventional {metrics['lead_vs_conventional_days']} d, "
          f"vs widespread {metrics['lead_vs_widespread_days']} d, "
          f"loss avoided ${metrics['loss_avoided_usd']:.0f}, {len(pw.alerts)} alerts, "
          f"{metrics['n_reports']} reports -> web/data/scenario.js")


if __name__ == "__main__":
    main()
