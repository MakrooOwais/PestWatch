"""Monte-Carlo evaluation of PestWatch.

1. Fit the multi-signal fusion model on separate training seasons.
2. Ablation: add one capability at a time (photos only -> + inspection replies ->
   + targeted scouting -> + evidence fusion -> + wind-aware zones ->
   + alert budget & all-clear). Every variant is recalibrated so that at most
   ``--target-fa`` of outbreak-free seasons raise a false alert.
3. Baselines without spatial / community aggregation, at the same false-alarm budget.
4. Sensitivity of the full system to participation, classifier quality,
   connectivity, look-alike pressure, spread speed, spammers, multiple introductions
   and incentives.
5. Costs and benefit-cost ratio.

Usage: python3 scripts/evaluate.py [--n-null 60] [--n-outbreak 60] [--n-sensitivity 16] [--quick]
"""
import argparse
import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pestwatch  # noqa: E402,F401  (pins BLAS threads before numpy loads)
import numpy as np  # noqa: E402
from pestwatch.baselines import regional_count_series, single_farm_series  # noqa: E402
from pestwatch.classifier import SimulatedClassifier  # noqa: E402
from pestwatch.config import Config  # noqa: E402
from pestwatch.fusion import FusionModel  # noqa: E402
from pestwatch.metrics import compare  # noqa: E402
from pestwatch.simulation import simulate  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
NULL_SEED0, TRAIN_SEED0 = 100_000, 200_000
FUSION_PATH = os.path.join(ROOT, "data", "fusion.json")
OFF = dict(inspection_replies=False, scouting=False, fusion=False, wind_zones=False, alert_budget=False, all_clear=False)
VARIANTS = [
    ("photos only", {}),
    ("+ farmer inspection replies", {"inspection_replies": True}),
    ("+ targeted scouting requests", {"inspection_replies": True, "scouting": True}),
    ("+ evidence fusion (scan + farmer confirmations)", {"inspection_replies": True, "scouting": True, "fusion": True}),
    ("+ wind-aware alert zones", {"inspection_replies": True, "scouting": True, "fusion": True, "wind_zones": True}),
    ("+ alert budget & all-clear (full)", {k: True for k in OFF}),
]
FULL = VARIANTS[-1][1]
SENSITIVITY = [
    ("App participation", "region.app_participation", [0.2, 0.4, 0.7, 0.9], "{:.0%}"),
    ("Classifier degraded toward chance", "classifier.degrade", [0.0, 0.25, 0.5], "{:.0%}"),
    ("Offline delay (h, min–max)", "reporting.offline_delay_h_range", [(3, 20), (6, 40), (12, 80), (24, 160)], "{}"),
    ("Look-alike damage share", "reporting.other_damage_range", [(0.02, 0.07), (0.04, 0.14), (0.08, 0.28)], "{}"),
    ("Spread rate (beta)", "pest.beta_per_day", [0.6, 1.2, 1.8], "{}"),
    ("Spammer farms", "behaviour.n_spammers", [0, 2, 5], "{}"),
    ("Disease introductions per season", "pest.n_landings", [1, 2], "{}"),
    ("Photo incentives (rate ×)", "behaviour.incentive_multiplier", [1.0, 1.5], "{}"),
]


def make_cfg(outbreak, flags, overrides=None, scan_thr=None, fusion_p=None) -> Config:
    c = Config(outbreak=outbreak)
    for k, v in {**OFF, **flags}.items():
        setattr(c.features, k, v)
    for path, v in (overrides or {}).items():
        sec, attr = path.split(".")
        setattr(getattr(c, sec), attr, tuple(v) if isinstance(v, list) else v)
    c.fusion.weights_path = FUSION_PATH
    if scan_thr is not None:
        c.scan.threshold = scan_thr
    if fusion_p is not None:
        c.fusion.alert_p = fusion_p
    return c


# ---------------- jobs (top-level so they pickle) ----------------
def train_job(args):
    seed, outbreak, scan_thr = args
    r = simulate(make_cfg(outbreak, {**FULL, "fusion": False}, scan_thr=scan_thr), seed, True)
    return [(x.tolist(), y) for x, y in r.training_rows]


def null_job(args):
    """Max decision score in an outbreak-free season, with alerting disabled (threshold at infinity)."""
    seed, flags, overrides, scan_thr = args
    fusion_on = flags.get("fusion", False)
    r = simulate(make_cfg(False, flags, overrides, scan_thr=scan_thr if fusion_on else 1e9, fusion_p=2.0), seed, True)
    out = {"decision": float(np.nanmax(r.decision_series()))}
    if not flags:
        out["regional_count"] = float(regional_count_series(r).max())
        out["single_farm"] = float(single_farm_series(r).max())
    return out


def pair_job(args):
    seed, flags, overrides, scan_thr, fusion_p, base_thr = args
    cfg = make_cfg(True, flags, overrides, scan_thr, fusion_p)
    pw, cf = simulate(cfg, seed, True), simulate(cfg, seed, False)
    m = compare(pw, cf)
    if base_thr:
        after = cf.times >= cf.t_intro
        for k, fn in (("regional_count", regional_count_series), ("single_farm", single_farm_series)):
            hit = (fn(cf) >= base_thr[k]) & after
            m[f"detect_{k}"] = float(cf.times[np.argmax(hit)]) if hit.any() else None
    return m


# ---------------- helpers ----------------
def stats(xs):
    v = np.array([x for x in xs if x is not None and np.isfinite(x)], dtype=float)
    if not len(v):
        return {"n": 0}
    return {"n": int(len(v)), "median": float(np.median(v)), "mean": float(v.mean()),
            "p25": float(np.percentile(v, 25)), "p75": float(np.percentile(v, 75))}


FLOOR = {"scan LLR": 1.0, "fused P(outbreak)": 0.5}   # never alert on a barely-there signal


def quantile_thr(vals, target, floor):
    return max(float(np.quantile(np.array(vals), 1 - target)) + 1e-6, floor)


def summarise(pairs):
    keys = ["detect_delay_days", "lead_vs_conventional_days", "lead_vs_widespread_days", "recall", "precision_all",
            "precision_HIGH", "alerts_per_farm_mean", "scout_messages", "all_clear_messages",
            "alerts_suppressed_by_budget", "loss_avoided_usd", "loss_avoided_pct", "farms_visible_end_pw",
            "farms_visible_end_cf", "benefit_cost_ratio", "false_alert_events", "finance_triggers"]
    out = {k: stats([p.get(k) for p in pairs]) for k in keys}
    out["detection_rate"] = float(np.mean([p["t_first_alert"] is not None for p in pairs]))
    out["cost_total_usd"] = stats([p["cost"]["total"] for p in pairs])
    out["cost_per_farm_usd"] = stats([p["cost"]["per_farm"] for p in pairs])
    out["widespread_reached"] = float(np.mean([p["t_widespread"] is not None for p in pairs]))
    return out


def run_variant(pool, flags, overrides, n_null, n_out, target, scan_thr_for_strength=None, base=False, label=""):
    """Calibrate on outbreak-free seasons, then evaluate paired outbreak seasons."""
    nulls = pool.map(null_job, [(NULL_SEED0 + i, flags, overrides, scan_thr_for_strength) for i in range(n_null)])
    fusion_on = flags.get("fusion", False)
    thr = quantile_thr([n["decision"] for n in nulls], target, FLOOR["fused P(outbreak)" if fusion_on else "scan LLR"])
    base_thr = None
    if base:
        base_thr = {k: float(np.floor(np.quantile([n[k] for n in nulls], 1 - target)) + 1)
                    for k in ("regional_count", "single_farm")}
    scan_thr = scan_thr_for_strength if fusion_on else thr
    fusion_p = thr if fusion_on else None
    pairs = pool.map(pair_job, [(i, flags, overrides, scan_thr, fusion_p, base_thr) for i in range(n_out)])
    s = summarise(pairs)
    print(f"  {label}: threshold {thr:.3f} | lead vs conventional {fmt(s['lead_vs_conventional_days'])} | "
          f"precision HIGH {fmt(s['precision_HIGH'], pct=True)}", flush=True)
    return {"threshold": thr, "decision": "fused P(outbreak)" if fusion_on else "scan LLR",
            "summary": s, "base_thr": base_thr, "pairs": pairs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-null", type=int, default=60)
    ap.add_argument("--n-outbreak", type=int, default=60)
    ap.add_argument("--n-train", type=int, default=40)
    ap.add_argument("--n-sensitivity", type=int, default=16)
    ap.add_argument("--target-fa", type=float, default=0.10)
    ap.add_argument("--quick", action="store_true", help="small run for smoke testing")
    ap.add_argument("--no-sensitivity", action="store_true")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    a = ap.parse_args()
    if a.quick:
        a.n_null = a.n_outbreak = a.n_train = 12
        a.n_sensitivity = 6
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    clf_source = SimulatedClassifier(Config().classifier).source

    with Pool(a.workers) as pool:
        print("1/3 Ablation (photos only also calibrates the baselines)", flush=True)
        results = {}
        results[VARIANTS[0][0]] = run_variant(pool, VARIANTS[0][1], None, a.n_null, a.n_outbreak, a.target_fa,
                                              base=True, label=VARIANTS[0][0])
        scan_thr = results[VARIANTS[0][0]]["threshold"]

        print(f"  fitting fusion model on {2 * a.n_train} training seasons", flush=True)
        rows = pool.map(train_job, [(TRAIN_SEED0 + i, i % 2 == 0, scan_thr) for i in range(2 * a.n_train)])
        X = np.array([x for rs in rows for x, _ in rs])
        y = np.array([yy for rs in rows for _, yy in rs], dtype=float)
        model = FusionModel.fit(X, y, l2=1.0)
        model.save(FUSION_PATH)
        print(f"  fusion: bias {model.bias:.2f}, weights {np.round(model.w, 2).tolist()} "
              f"({len(y)} rows, {int(y.sum())} positive)", flush=True)

        for name, flags in VARIANTS[1:]:
            results[name] = run_variant(pool, flags, None, a.n_null, a.n_outbreak, a.target_fa,
                                        scan_thr_for_strength=scan_thr, label=name)
        full = results[VARIANTS[-1][0]]

        sens = []
        if not a.no_sensitivity:
            print("2/3 Sensitivity of the full system", flush=True)
            for title, path, values, vfmt in SENSITIVITY:
                for v in values:
                    r = run_variant(pool, FULL, {path: v}, a.n_sensitivity, a.n_sensitivity, a.target_fa,
                                    scan_thr_for_strength=scan_thr, label=f"{title}={v}")
                    s = r["summary"]
                    sens.append({"factor": title, "path": path, "value": list(v) if isinstance(v, tuple) else v,
                                 "label": vfmt.format(v), "detection_rate": s["detection_rate"],
                                 "lead_vs_conventional_days": s["lead_vs_conventional_days"],
                                 "recall": s["recall"], "precision_HIGH": s["precision_HIGH"],
                                 "loss_avoided_pct": s["loss_avoided_pct"]})

    print("3/3 Writing reports", flush=True)
    p0 = results[VARIANTS[0][0]]["pairs"]
    baselines = {}
    for k in ("regional_count", "single_farm"):
        det = [p[f"detect_{k}"] for p in p0]
        baselines[k] = {
            "threshold": results[VARIANTS[0][0]]["base_thr"][k],
            "detection_rate": float(np.mean([d is not None for d in det])),
            "detect_delay_days": stats([None if d is None else d - p["t_intro"] for d, p in zip(det, p0)]),
            "lead_vs_conventional_days": stats([None if d is None or p["t_conventional"] is None
                                                else p["t_conventional"] - d for d, p in zip(det, p0)]),
        }
    cost_items = {k: stats([p["cost"][k] for p in full["pairs"]]) for k in full["pairs"][0]["cost"]}
    summary = {
        "settings": {"n_null": a.n_null, "n_outbreak": a.n_outbreak, "n_train": a.n_train,
                     "n_sensitivity": a.n_sensitivity, "target_false_alarm_season_rate": a.target_fa,
                     "season_days": Config().days, "app_participation": Config().region.app_participation,
                     "classifier_confusion": clf_source},
        "fusion_model": {"bias": model.bias, "weights": dict(zip(model.meta["features"], model.w.tolist())),
                         "n_rows": int(len(y))},
        "ablation": {k: {kk: vv for kk, vv in v.items() if kk not in ("pairs", "base_thr")} for k, v in results.items()},
        "baselines": baselines,
        "calibration": {"scan_threshold": scan_thr, "fusion_alert_p": full["threshold"]},
        "costs": cost_items,
        "sensitivity": sens,
    }
    # Compact keys used by the dashboard.
    fs = full["summary"]
    summary["detection"] = {"scan": {"lead_vs_conventional_days": fs["lead_vs_conventional_days"],
                                     "lead_vs_widespread_days": fs["lead_vs_widespread_days"],
                                     "delay_after_landing_days": fs["detect_delay_days"],
                                     "detection_rate": fs["detection_rate"]}}
    summary["farm_level"] = {"recall": fs["recall"], "precision_all": fs["precision_all"],
                             "precision_HIGH": fs["precision_HIGH"]}
    summary["impact"] = {"loss_avoided_usd": fs["loss_avoided_usd"], "loss_avoided_pct": fs["loss_avoided_pct"],
                         "benefit_cost_ratio": fs["benefit_cost_ratio"]}
    with open(os.path.join(ROOT, "reports", "evaluation.json"), "w") as f:
        json.dump(summary, f, indent=2)
    with open(os.path.join(ROOT, "data", "calibration.json"), "w") as f:
        json.dump({"scan_threshold": scan_thr, "fusion_alert_p": full["threshold"], "target_fa": a.target_fa,
                   "n_null": a.n_null}, f, indent=2)
    md = render_md(summary)
    with open(os.path.join(ROOT, "reports", "EVALUATION.md"), "w") as f:
        f.write(md)
    print(md)


def fmt(s, unit=" d", pct=False, digits=1):
    if not s or s.get("n", 0) == 0:
        return "n/a"
    f = (lambda x: f"{x:.0%}") if pct else (lambda x: f"{x:,.{digits}f}{unit}")
    return f"{f(s['median'])} ({f(s['p25'])}–{f(s['p75'])})"


def render_md(s):
    cfg = Config()
    st = s["settings"]
    L = [
        "# PestWatch evaluation (simulated)",
        "",
        f"{st['n_outbreak']} outbreak and {st['n_null']} outbreak-free seasons of {st['season_days']:.0f} days per "
        f"variant (~145 farms, 6 villages, {st['app_participation']:.0%} app participation). Every variant is "
        f"recalibrated so that at most {st['target_false_alarm_season_rate']:.0%} of outbreak-free seasons raise a "
        f"false alert. Classifier confusion matrix: **{st['classifier_confusion']}** "
        "(measured = held-out test set of the trained model; assumed = hand-set).",
        "",
        "All numbers come from simulation with assumed parameters (`pestwatch/config.py`), not field data. "
        "Medians with interquartile ranges.",
        "",
        "## Ablation: what each capability adds",
        "",
        "| Variant | Days after rust arrives | Lead vs conventional | Recall (warned before visible) | Precision, HIGH | Alerts / farm | Yield loss avoided |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, v in s["ablation"].items():
        x = v["summary"]
        L.append(f"| {name} | {fmt(x['detect_delay_days'])} | {fmt(x['lead_vs_conventional_days'])} | "
                 f"{fmt(x['recall'], pct=True)} | {fmt(x['precision_HIGH'], pct=True)} | "
                 f"{fmt(x['alerts_per_farm_mean'], unit='')} | {fmt(x['loss_avoided_pct'], pct=True)} |")
    fm = s["fusion_model"]
    L += ["", "Fusion model (logistic regression on simulated training seasons): "
          + ", ".join(f"{k} {w:+.2f}" for k, w in fm["weights"].items()) + f", bias {fm['bias']:+.2f}.", ""]
    b = s["baselines"]
    a0 = s["ablation"]["photos only"]["summary"]
    L += ["## Baselines at the same false-alarm budget (photos only)", "",
          "| Detector | Detected | Days after rust arrives | Lead vs conventional |", "|---|---|---|---|",
          f"| PestWatch scan (photos only) | {a0['detection_rate']:.0%} | {fmt(a0['detect_delay_days'])} | "
          f"{fmt(a0['lead_vs_conventional_days'])} |"]
    for k, name in (("regional_count", "Regional count, no spatial aggregation"),
                    ("single_farm", "Single-farm rule, no community aggregation")):
        L.append(f"| {name} | {b[k]['detection_rate']:.0%} | {fmt(b[k]['detect_delay_days'])} | "
                 f"{fmt(b[k]['lead_vs_conventional_days'])} |")
    full = list(s["ablation"].values())[-1]["summary"]
    c = s["costs"]
    usd = dict(unit=" USD", digits=0)
    L += ["", f"Conventional route = {cfg.response.conventional_farms} farmers notice damage + "
          f"{cfg.response.conventional_lag_days:.0f}-day reporting lag. Widespread = {cfg.pest.widespread_frac:.0%} of "
          "farms visibly damaged (counterfactual).", "",
          "## Full system: impact and cost", "",
          f"- Lead vs widespread infestation: {fmt(full['lead_vs_widespread_days'])}",
          f"- Farms visibly damaged at season end: {fmt(full['farms_visible_end_pw'], unit='', digits=0)} with "
          f"PestWatch vs {fmt(full['farms_visible_end_cf'], unit='', digits=0)} without",
          f"- Yield loss avoided: {fmt(full['loss_avoided_usd'], **usd)} per region-season "
          f"({fmt(full['loss_avoided_pct'], pct=True)})",
          f"- Running cost: {fmt(c['total'], **usd)} per region-season ({fmt(c['per_farm'], unit=' USD', digits=2)} "
          f"per farm): messaging {fmt(c['messaging'], **usd)}, platform {fmt(c['platform'], **usd)}, lead-farmer "
          f"stipends {fmt(c['lead_farmers'], **usd)}, extension visits "
          f"{fmt(c['extension_visits'], **usd)}",
          f"- **Benefit-cost ratio: {fmt(full['benefit_cost_ratio'], unit='×', digits=0)}** (yield loss avoided ÷ running "
          "cost; excludes development, devices and farmers' own control costs)",
          f"- Alert load: {fmt(full['alerts_per_farm_mean'], unit='')} alerts per farm per season "
          f"({fmt(full['alerts_suppressed_by_budget'], unit='', digits=0)} suppressed by the budget), "
          f"{fmt(full['scout_messages'], unit='', digits=0)} scouting requests, "
          f"{fmt(full['all_clear_messages'], unit='', digits=0)} all-clear messages",
          f"- Anticipatory-finance triggers per outbreak season: {fmt(full['finance_triggers'], unit='', digits=0)}",
          ""]
    if s["sensitivity"]:
        L += ["## Sensitivity of the full system", "",
              f"{st['n_sensitivity']} outbreak + {st['n_sensitivity']} outbreak-free seasons per point, each point "
              "recalibrated to the same false-alarm budget (so a change can move the threshold as well as the "
              "signal). Differences of a few days are within noise at this sample size; read for direction and "
              "breaking points, not decimals.", "",
              "| Factor | Value | Detected | Lead vs conventional | Recall | Precision HIGH | Loss avoided |",
              "|---|---|---|---|---|---|---|"]
        for x in s["sensitivity"]:
            L.append(f"| {x['factor']} | {x['label']} | {x['detection_rate']:.0%} | "
                     f"{fmt(x['lead_vs_conventional_days'])} | {fmt(x['recall'], pct=True)} | "
                     f"{fmt(x['precision_HIGH'], pct=True)} | {fmt(x['loss_avoided_pct'], pct=True)} |")
        L.append("")
    L += ["Unit costs are assumptions (`CostConfig`): SMS $0.008, voice call $0.04, platform $0.30/farm/season, "
          "lead-farmer stipend $5/season, extension visit $5 per alerted village."]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
