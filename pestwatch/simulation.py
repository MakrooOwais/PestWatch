"""End-to-end simulation of one season in a synthetic region.

Loop per 6-hour step:
  1. evidence arrives: farmer photos (on-device classifier), lead-farmer scouting
     rounds, pheromone-trap readings, replies to scouting requests and alerts;
     all of it queues offline and syncs later
  2. disease dynamics advance (temperature-driven growth, wind-driven spore dispersal)
  3. synced evidence is scanned for space-time clusters and fused with traps,
     confirmations and the migration forecast into P(outbreak)
  4. WATCH-level signals trigger targeted scouting requests; ALERT-level ones send
     localized, wind-aware alerts (within each farmer's alert budget); quiet
     clusters send an all-clear

``alerts_enabled=False`` is the counterfactual: no PestWatch actions at all
(no alerts, scouting requests or re-engagement), farmers act only when they
notice damage. Both arms share common random numbers, so they are identical
until PestWatch first acts.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .alerts import RISK_ORDER, Alert, risk_for
from .classifier import SimulatedClassifier
from .config import CLASSES, OTHER_DAMAGE, Config
from .environment import Environment, Landing, generate_environment, trap_anomaly, trap_rate
from .fusion import FusionModel, featurize
from .region import Region, generate_region
from .scan import SpaceTimeScan, reports_in_area

# Evidence sources (reports["src"])
PHOTO, LEAD_FARMER, SCOUT_REPLY, INSPECTION_REPLY, SPAM = range(5)
SOURCE_NAMES = ["photo", "lead_farmer", "scout_reply", "inspection_reply", "spam"]


@dataclass
class ClusterEvent:
    t: float
    cluster_id: int
    center_farm: int
    radius_km: float
    window_h: float
    llr: float
    relative_risk: float
    n_reports: int
    n_farms: int
    level: str              # "ALERT" or "WATCH"
    probability: float = float("nan")   # fused P(outbreak); nan when fusion is off
    n_confirmed: int = 0
    trap_z: float = 0.0
    days_to_visible: float = float("nan")
    trap_led: bool = False


@dataclass
class Notice:
    """Non-alert farmer messages."""
    t: float
    farm: int
    kind: str               # "scout_request" | "all_clear"
    cluster_id: int
    days: int = 0           # all_clear: quiet days


@dataclass
class SimResult:
    cfg: Config
    seed: int
    region: Region
    alerts_enabled: bool
    times: np.ndarray                   # (S+1,)
    damage: np.ndarray                  # (S+1, F) share of plants damaged
    seed_farms: List[int]
    t_intro: Optional[float]
    infected_at: np.ndarray             # (F,) inf if never
    visible_at: np.ndarray
    noticed_at: np.ndarray
    treated_at: np.ndarray
    peak_damage: np.ndarray
    reports: Dict[str, np.ndarray]      # farm, t_cap, t_sync, probs, true_class, src
    scores: np.ndarray                  # (S+1,) max scan LLR at each step
    top_clusters: List[List[ClusterEvent]]
    alerts: List[Alert] = field(default_factory=list)
    nuisance: List[dict] = field(default_factory=list)
    env: Optional[Environment] = None
    landings: List[Landing] = field(default_factory=list)
    trap_readings: Dict[str, np.ndarray] = field(default_factory=dict)
    probability: Optional[np.ndarray] = None     # (S+1,) max fused P(outbreak)
    notices: List[Notice] = field(default_factory=list)
    scout_requests: List[dict] = field(default_factory=list)
    triggers: List[dict] = field(default_factory=list)
    suppressed_alerts: int = 0
    lead_farmers: List[int] = field(default_factory=list)
    spammers: List[int] = field(default_factory=list)
    training_rows: List[tuple] = field(default_factory=list)   # (features, label) for fusion fitting
    village_probability: Optional[np.ndarray] = None   # (S+1, V) fused P(outbreak) of each village zone

    @property
    def p_target(self) -> np.ndarray:
        return self.reports["probs"][:, self.target]

    @property
    def target(self) -> int:
        return CLASSES.index(self.cfg.pest.target_class)

    def decision_series(self) -> np.ndarray:
        """What the alert threshold is applied to."""
        return self.probability if self.cfg.features.fusion and self.probability is not None else self.scores

    @property
    def first_action_time(self) -> Optional[float]:
        ts = [a.t for a in self.alerts[:1]] + [r["t"] for r in self.scout_requests[:1]]
        return min(ts) if ts else None


def _spawn(seed: int, n: int):
    return [np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(n)]


def _vec(main: int, p: float) -> np.ndarray:
    v = np.full(len(CLASSES), (1 - p) / (len(CLASSES) - 1))
    v[main] = p
    return v


def simulate(cfg: Config, seed: int, alerts_enabled: bool = True,
             fusion_model: Optional[FusionModel] = None) -> SimResult:
    (r_region, r_pest, r_crn, r_rep, r_clf, r_nuis,
     r_env, r_trap, r_scout, r_beh) = _spawn(seed, 10)
    pc, rc, resp, beh, sc, ft = cfg.pest, cfg.reporting, cfg.response, cfg.behaviour, cfg.scouting, cfg.features
    TGT = CLASSES.index(pc.target_class)
    HEALTHY = CLASSES.index("healthy")
    region = generate_region(r_region, cfg.region, rc, rc.photo_sessions_per_day)
    F, S, dt = region.n_farms, cfg.n_steps, cfg.dt_days
    clf = SimulatedClassifier(cfg.classifier)
    scan = SpaceTimeScan(region.dist, cfg.scan)
    fusion = fusion_model or (FusionModel.load(cfg.fusion.weights_path) if ft.fusion else None)
    acting = alerts_enabled   # PestWatch actions only happen in the PestWatch arm

    # Common random numbers shared by both arms.
    U_inf = r_crn.random((S, F))
    U_notice = r_crn.random((S, F))
    U_scout = r_crn.random((S, F))
    U_comply = r_crn.random(F)
    U_reply = r_crn.random(F)

    # Disease introductions (spores arriving from outside the region).
    landings: List[Landing] = []
    land_step = np.full(F, -1)
    k_intro = int(round(pc.introduce_day / dt)) if cfg.outbreak else None
    if cfg.outbreak:
        t_l = pc.introduce_day
        for k in range(pc.n_landings):
            if k:
                t_l += r_pest.uniform(*pc.second_landing_gap_days)
            centre = region.farm_xy[r_pest.integers(F)] + r_pest.normal(0, 0.3, 2)
            d2 = ((region.farm_xy - centre) ** 2).sum(1)
            landed = r_pest.random(F) < pc.landing_peak_prob * np.exp(-d2 / (2 * pc.landing_sigma_km ** 2))
            if not landed.any():
                landed[np.argmin(d2)] = True
            ks = int(round(t_l / dt)) + (r_pest.random(F) * pc.landing_days / dt).astype(int)
            new = landed & (land_step < 0)
            land_step[new] = ks[new]
            landings.append(Landing(float(t_l), centre, [int(f) for f in np.flatnonzero(landed)]))
    seed_farms = sorted({f for L in landings for f in L.farms})

    env = generate_environment(r_env, cfg, region, landings)
    nuisance = _nuisance_events(cfg, region, r_nuis)

    # Lead farmers (weekly 10-plant rounds + trap reading) and optional spammers.
    lead = []
    for v in range(len(region.village_xy)):
        cand = np.flatnonzero((region.farm_village == v) & region.participates)
        lead += list(r_beh.choice(cand, size=min(beh.lead_farmers_per_village, len(cand)), replace=False))
    lead_day = {int(f): int(r_beh.integers(7)) for f in lead}
    spammers = list(r_beh.choice(F, size=beh.n_spammers, replace=False)) if beh.n_spammers else []
    spam_start = {int(f): float(r_beh.uniform(0, cfg.days / 2)) for f in spammers}
    trap_reader = [int(np.flatnonzero(region.farm_village == v)[0]) for v in env.trap_village]
    for i, v in enumerate(env.trap_village):
        lv = [f for f in lead if region.farm_village[f] == v]
        if lv:
            trap_reader[i] = int(lv[0])

    village_centres = [int(np.argmin(((region.farm_xy - v) ** 2).sum(1))) for v in region.village_xy]
    village_p = np.zeros((S + 1, len(village_centres)))
    iso = np.exp(-region.dist / pc.kernel_km)
    np.fill_diagonal(iso, 0.0)
    iso /= iso.sum(1).mean()  # normalise so beta is comparable across layouts

    I = np.zeros(F)
    emission = np.ones(F)
    suscept = np.ones(F)
    inf = np.full(F, np.inf)
    infected_at, visible_at, noticed_at, treated_at = inf.copy(), inf.copy(), inf.copy(), inf.copy()
    treat_due, inspect_due = inf.copy(), inf.copy()
    engaged_until = np.full(F, -np.inf)
    asked_at = np.full(F, -np.inf)
    peak = np.zeros(F)

    times = np.arange(S + 1) * dt
    damage = np.zeros((S + 1, F))
    scores = np.zeros(S + 1)
    prob = np.zeros(S + 1)
    top_clusters: List[List[ClusterEvent]] = [[]]
    alerts: List[Alert] = []
    notices: List[Notice] = []
    scout_requests: List[dict] = []
    triggers: List[dict] = []
    training: List[tuple] = []
    last_alert: Dict[int, Alert] = {}
    alert_log: Dict[int, List[float]] = defaultdict(list)
    track_alert_t: Dict[int, float] = {}
    cleared = set()
    triggered = set()
    pending_scouts: List[tuple] = []   # (t_resp, farm, cluster_id)
    suppressed = 0
    tracks: List[dict] = []

    rep = {k: [] for k in ("farm", "t_cap", "t_sync", "probs", "true_class", "src")}
    flat = {"farm": np.zeros(0, int), "t_cap": np.zeros(0), "t_sync": np.zeros(0), "p": np.zeros(0),
            "src": np.zeros(0, int)}
    traps = {"trap": np.zeros(0, int), "t_read": np.zeros(0), "t_sync": np.zeros(0), "count": np.zeros(0)}
    other_classes = np.array([CLASSES.index(c) for c in OTHER_DAMAGE])
    other_w = np.array(list(OTHER_DAMAGE.values()))
    other_w = other_w / other_w.sum()

    def sync_delay(farms, rng):
        online = rng.random(len(farms)) < region.online_share[farms]
        return np.where(online, rng.exponential(0.3, len(farms)), rng.exponential(region.offline_delay_h[farms])) / 24.0

    def add_reports(farms, t_cap, probs, cls, src, rng):
        farms = np.asarray(farms, int)
        t_sync = t_cap + sync_delay(farms, rng)
        for k, v in (("farm", farms), ("t_cap", t_cap), ("t_sync", t_sync), ("probs", probs),
                     ("true_class", cls), ("src", np.full(len(farms), src))):
            rep[k].append(v)
        flat["farm"] = np.concatenate([flat["farm"], farms])
        flat["t_cap"] = np.concatenate([flat["t_cap"], t_cap])
        flat["t_sync"] = np.concatenate([flat["t_sync"], t_sync])
        flat["p"] = np.concatenate([flat["p"], probs[:, TGT]])
        flat["src"] = np.concatenate([flat["src"], np.full(len(farms), src)])

    def photograph(farms, t, rng, targeting=True):
        u = rng.random(len(farms))
        cls = np.zeros(len(farms), int)
        tf = rc.symptom_targeting if targeting else 1.5
        to = rc.other_targeting if targeting else 1.0
        p_t = np.minimum(I[farms] * tf, 0.95)
        is_t = u < p_t
        is_o = ~is_t & (u < p_t + region.other_damage[farms] * other_mult[farms] * to)
        cls[is_t] = TGT
        cls[is_o] = rng.choice(other_classes, size=is_o.sum(), p=other_w)
        return cls, clf.predict_batch(r_clf, cls, I[farms])

    def reply(farms, t, found, src, rng):
        if not len(farms):
            return
        probs = np.array([_vec(TGT, 0.95) if f else _vec(HEALTHY, 0.95) for f in found])
        cls = np.where(I[farms] > 0, TGT, HEALTHY)
        add_reports(farms, np.full(len(farms), t), probs, cls, src, rng)

    for s in range(S):
        t, t1 = times[s], times[s + 1]
        landing = (land_step == s) & (I == 0)
        I[landing] = pc.i0
        infected_at[landing & np.isinf(infected_at)] = t

        # 1a. Farmer photos (engagement fades; alerts/requests re-engage farmers).
        rate_mult, other_mult = _nuisance_multipliers(nuisance, region, t, F)
        fade = beh.engagement_floor + (1 - beh.engagement_floor) * 0.5 ** (t / beh.engagement_halflife_days)
        boost = np.where(engaged_until > t, 1 + beh.alert_reengagement, 1.0)
        sessions = r_rep.poisson(region.photo_rate * rate_mult * fade * boost * beh.incentive_multiplier * dt)
        n_photos = np.where(sessions > 0, sessions + r_rep.poisson(rc.photos_per_session_extra * sessions), 0)
        if n_photos.sum():
            pf = np.repeat(np.arange(F), n_photos)
            cls, probs = photograph(pf, t, r_rep)
            add_reports(pf, t + r_rep.random(len(pf)) * dt, probs, cls, PHOTO, r_rep)

        # 1b. Lead farmers' weekly systematic scouting round.
        day_start = abs(t - round(t)) < 1e-9
        if day_start:
            dnum = int(round(t))
            for f in lead:
                if dnum % 7 == lead_day[int(f)]:
                    pf = np.full(beh.lead_farmer_plants, f)
                    cls, probs = photograph(pf, t, r_beh, targeting=False)
                    add_reports(pf, t + r_beh.random(len(pf)) * 0.2, probs, cls, LEAD_FARMER, r_beh)

        # 1c. Spammers: bogus confident reports.
        for f in spammers:
            if t >= spam_start[int(f)]:
                k = r_beh.poisson(beh.spam_rate_per_day * dt)
                if k:
                    probs = np.array([_vec(TGT, r_beh.uniform(0.7, 0.95)) for _ in range(k)])
                    add_reports(np.full(k, f), t + r_beh.random(k) * dt, probs, np.full(k, HEALTHY), SPAM, r_beh)

        # 1d. Traps (if any), read by lead farmers.
        if len(trap_reader) and day_start and int(round(t)) % max(1, int(cfg.environment.trap_read_days)) == 0:
            lam = trap_rate(env, cfg, t, I, emission, landings) * cfg.environment.trap_read_days
            cnt = r_trap.poisson(lam)
            readers = np.array(trap_reader)
            traps["trap"] = np.concatenate([traps["trap"], np.arange(len(cnt))])
            traps["t_read"] = np.concatenate([traps["t_read"], np.full(len(cnt), t)])
            traps["t_sync"] = np.concatenate([traps["t_sync"], t + sync_delay(readers, r_trap)])
            traps["count"] = np.concatenate([traps["count"], cnt.astype(float)])

        # 1e. Replies to scouting requests that are due.
        due = [x for x in pending_scouts if x[0] <= t1]
        pending_scouts = [x for x in pending_scouts if x[0] > t1]
        if due:
            fs = np.array([x[1] for x in due])
            p_find = 1 - (1 - np.minimum(1.0, I[fs] * sc.per_plant_detect)) ** sc.plants_checked
            p_find = np.where(I[fs] > 0, p_find, sc.false_find)
            found = r_scout.random(len(fs)) < p_find
            reply(fs, t1, found, SCOUT_REPLY, r_scout)
            # A scout who finds rust acts on it like an alerted farmer.
            treat_due[fs[found]] = np.minimum(treat_due[fs[found]], t1 + 1.0)

        # 2. Pest dynamics to t1 (wind-driven dispersal, temperature-driven growth).
        if k_intro is not None and s >= k_intro:
            kernel = env.dispersal_kernel(iso, t, cfg)
            force = pc.beta_per_day * kernel @ (I * emission)
            force += pc.long_jump_per_day / F * (I > 0).any()
            p_inf = 1.0 - np.exp(-force * suscept * dt)
            new = (I == 0) & (U_inf[s] < p_inf)
            I[new] = pc.i0
            infected_at[new & np.isinf(infected_at)] = t1
            treated = np.isfinite(treated_at)
            g = np.exp(pc.growth_per_day * env.growth_mult(t, cfg) * dt)
            grow = (I > 0) & ~treated
            I[grow] = I[grow] * g / (1 - I[grow] + I[grow] * g)
            I[treated] *= np.exp(-resp.treated_decline_per_day * dt)
            I[I < 1e-4] = 0.0
            peak = np.maximum(peak, I)
            visible_at[(I >= pc.visible_threshold) & np.isinf(visible_at)] = t1

        # 3. Farmer responses due by t1.
        can_notice = (I >= pc.visible_threshold) & np.isinf(noticed_at)
        noticed = can_notice & (U_notice[s] < 1 - np.exp(-resp.self_notice_per_day * dt))
        noticed_at[noticed] = t1
        treat_due[noticed] = np.minimum(treat_due[noticed], t1 + resp.treat_delay_days)

        insp = inspect_due <= t1
        if insp.any():
            found = insp & (I > 0) & (U_scout[s] < resp.scout_detect_base + resp.scout_detect_slope * I)
            # Alerted farmers are primed: extension pre-positions inputs, so control is applied fast.
            treat_due[found] = np.minimum(treat_due[found], t1 + 1.0)
            if ft.inspection_replies:
                rf = np.flatnonzero(insp & (U_reply < beh.inspection_reply_rate))
                reply(rf, t1, found[rf], INSPECTION_REPLY, r_scout)
            inspect_due[insp] = np.inf

        apply = (treat_due <= t1) & np.isinf(treated_at)
        treated_at[apply] = t1
        emission[apply] = resp.treated_emission
        suscept[apply] = resp.treated_susceptibility

        # 4. Detection on everything synced by t1.
        clusters = scan.scan(flat["farm"], flat["t_cap"], flat["t_sync"], flat["p"], t1)
        scores[s + 1] = clusters[0].llr if clusters else 0.0
        tz = trap_anomaly(traps, env, cfg, t1)
        mig = float(env.migration[env.day(t1)])
        conf_mask = (flat["t_sync"] <= t1) & (flat["t_cap"] > t1 - 3) & (flat["p"] >= 0.9) \
            & np.isin(flat["src"], [SCOUT_REPLY, INSPECTION_REPLY])
        conf_farms = flat["farm"][conf_mask]
        step_events: List[ClusterEvent] = []
        best_p = 0.0
        for c in clusters:
            near_traps = env.trap_farm_dist[:, c.center_farm] <= c.radius_km + 3.0
            c_tz = float(tz[near_traps].max()) if near_traps.any() else 0.0
            n_conf = int(np.isin(conf_farms, c.members).sum())
            x = featurize(c.llr, n_conf, c_tz)
            p_out = float(fusion.prob(x)) if fusion else float("nan")
            training.append((x, int((I[c.members] > 0).any())))
            if fusion:
                best_p = max(best_p, p_out)
            is_alert = (p_out >= cfg.fusion.alert_p) if ft.fusion else (c.llr >= cfg.scan.threshold)
            is_watch = c.llr >= sc.watch_llr or (ft.fusion and p_out >= sc.watch_p)
            if not (is_alert or is_watch):
                continue
            level = "ALERT" if is_alert else "WATCH"
            cid = _track(tracks, region, c.center_farm, t1, cfg.alert.active_days)
            n_rep, n_farm = reports_in_area(flat["farm"], flat["t_cap"], flat["t_sync"], flat["p"], t1,
                                            c.members, 72, cfg.scan.positive_p)
            ev = ClusterEvent(t1, cid, c.center_farm, c.radius_km, c.window_h, c.llr, c.relative_risk,
                              n_rep, n_farm, level, p_out, n_conf, c_tz,
                              _days_to_visible(cfg, env, t1, c, flat))
            step_events.append(ev)
            if level == "WATCH" and acting and ft.scouting:
                _request_scouting(cfg, region, c.center_farm, cid, t1, asked_at, pending_scouts,
                                  scout_requests, notices, engaged_until, r_scout)
            if level == "ALERT":
                track_alert_t[cid] = t1
                basis = "traps" if (c.llr < cfg.scan.threshold and c_tz >= sc.trap_anomaly) else "reports"
                suppressed += _issue_alerts(cfg, region, env, c, cid, t1, n_rep, n_farm, flat, alerts,
                                            last_alert, alert_log, acting, U_comply, inspect_due, treated_at,
                                            engaged_until, basis)
                if ft.fusion and p_out >= cfg.fusion.trigger_p and n_farm >= cfg.fusion.trigger_min_farms \
                        and cid not in triggered and acting:
                    triggered.add(cid)
                    near = region.dist[c.center_farm] <= c.radius_km + cfg.alert.buffer_km
                    ha = float(region.farm_ha[near].sum())
                    triggers.append({"t": t1, "cluster_id": cid, "probability": p_out, "farms_at_risk": int(near.sum()),
                                     "ha_at_risk": ha, "payout_usd": ha * cfg.response.finance_usd_per_ha})
        prob[s + 1] = best_p

        # Fixed village zones every 12 h: negative (and positive) examples for fitting the fusion model.
        if s % 2 == 1:
            table = scan.zone_table(flat["farm"], flat["t_cap"], flat["t_sync"], flat["p"], t1)
            for vi, vc in enumerate(village_centres):
                z = scan.zone_index(vc, 1.5)
                members = np.flatnonzero(region.dist[vc] <= 1.5)
                near_traps = env.trap_farm_dist[:, vc] <= 1.5 + 3.0
                c_tz = float(tz[near_traps].max()) if near_traps.any() else 0.0
                x = featurize(float(table["llr"][z]), int(np.isin(conf_farms, members).sum()), c_tz)
                training.append((x, int((I[members] > 0).any())))
                if fusion:
                    village_p[s + 1, vi] = float(fusion.prob(x))
        elif s > 0:
            village_p[s + 1] = village_p[s]

        # Trap-led watch: a catch spike with no photo cluster yet -> ask farmers near the trap to check.
        if acting and ft.scouting:
            # A high-risk forecast lowers the catch level worth checking.
            trap_level = sc.trap_anomaly * (sc.forecast_trap_factor if mig >= sc.forecast_high else 1.0)
            for k in np.flatnonzero(tz >= trap_level):
                centre = int(np.argmin(env.trap_farm_dist[k]))
                if any(region.dist[centre, e.center_farm] <= 2.5 for e in step_events):
                    continue
                cid = _track(tracks, region, centre, t1, cfg.alert.active_days)
                step_events.append(ClusterEvent(t1, cid, centre, 1.5, 72, 0.0, 0.0, 0, 0, "WATCH",
                                                float("nan"), 0, float(tz[k]), trap_led=True))
                _request_scouting(cfg, region, centre, cid, t1, asked_at, pending_scouts, scout_requests,
                                  notices, engaged_until, r_scout)

        # All-clear for clusters that have gone quiet.
        if acting and ft.all_clear:
            for cid, ta in track_alert_t.items():
                if cid in cleared or t1 - ta < cfg.alert.all_clear_days:
                    continue
                cleared.add(cid)
                for f, a in last_alert.items():
                    if a.cluster_id == cid and t1 - a.t >= cfg.alert.all_clear_days:
                        notices.append(Notice(t1, f, "all_clear", cid, int(cfg.alert.all_clear_days)))

        top_clusters.append(step_events)
        damage[s + 1] = I

    reports = {k: (np.concatenate(v) if v else np.zeros((0, len(CLASSES)) if k == "probs" else 0))
               for k, v in rep.items()}
    return SimResult(cfg, seed, region, alerts_enabled, times, damage, seed_farms,
                     None if k_intro is None else times[k_intro], infected_at, visible_at,
                     noticed_at, treated_at, peak, reports, scores, top_clusters,
                     alerts if acting else [], nuisance, env, landings, traps,
                     prob if fusion else None, notices, scout_requests, triggers, suppressed,
                     [int(f) for f in lead], [int(f) for f in spammers], training,
                     village_p if fusion else None)


def _days_to_visible(cfg: Config, env: Environment, t: float, c, flat) -> float:
    """Degree-day forecast of when damage in the cluster becomes visible to farmers."""
    m = (flat["t_sync"] <= t) & (flat["t_cap"] > t - 3) & np.isin(flat["farm"], c.members) & (flat["src"] == PHOTO)
    if not m.any():
        return float("nan")
    share = float(np.clip(flat["p"][m].mean() / cfg.reporting.symptom_targeting, cfg.pest.i0, 0.24))
    logit = lambda x: np.log(x / (1 - x))  # noqa: E731
    r = cfg.pest.growth_per_day * max(env.growth_mult(t, cfg), 0.05)
    return float(max(0.0, (logit(cfg.pest.visible_threshold) - logit(share)) / r))


def _request_scouting(cfg, region, centre, cid, t, asked_at, pending, requests, notices, engaged_until, rng):
    sc = cfg.scouting
    # One round per cluster per cooldown; each farmer at most max_requests_per_14d.
    if any(r["cluster_id"] == cid and t - r["t"] < sc.cooldown_days for r in requests):
        return
    asked = defaultdict(int)
    for n in notices:
        if n.kind == "scout_request" and t - n.t < 14:
            asked[n.farm] += 1
    order = np.argsort(region.dist[centre])
    chosen = [int(f) for f in order if t - asked_at[f] >= sc.cooldown_days
              and asked[int(f)] < sc.max_requests_per_14d][:sc.farmers_per_request]
    if not chosen:
        return
    for f in chosen:
        asked_at[f] = t
        engaged_until[f] = max(engaged_until[f], t + 7)
        notices.append(Notice(t, f, "scout_request", cid))
        if rng.random() < sc.compliance:
            pending.append((t + rng.exponential(sc.response_delay_days), f, cid))
    requests.append({"t": t, "cluster_id": cid, "center_farm": int(centre), "farms": chosen})


def _nuisance_events(cfg: Config, region: Region, rng) -> List[dict]:
    n = cfg.nuisance
    V = len(region.village_xy)
    events = []
    for kind, count, days, mult in (("engagement_surge", n.engagement_surges, n.surge_days, n.surge_multiplier),
                                    ("confounder_flare", n.confounder_flares, n.flare_days, n.flare_multiplier)):
        for _ in range(count):
            start = float(rng.uniform(0, cfg.days - days))
            events.append({"kind": kind, "village": int(rng.integers(V)), "start": start,
                           "end": start + days, "multiplier": mult})
    return events


def _nuisance_multipliers(events: List[dict], region: Region, t: float, F: int):
    rate, other = np.ones(F), np.ones(F)
    for e in events:
        if e["start"] <= t < e["end"]:
            m = region.farm_village == e["village"]
            (rate if e["kind"] == "engagement_surge" else other)[m] *= e["multiplier"]
    return rate, other


def _track(tracks: List[dict], region: Region, center: int, t: float, active_days: float) -> int:
    for tr in tracks:
        if t - tr["last_t"] <= active_days and region.dist[tr["center"], center] <= 2.5:
            tr["last_t"], tr["center"] = t, center
            return tr["id"]
    tracks.append({"id": len(tracks), "center": center, "last_t": t})
    return len(tracks) - 1


def wind_distance(cfg: Config, region: Region, env: Environment, centre: int, t: float) -> np.ndarray:
    """Distance from a cluster centre, shrunk downwind (spores drift with the wind)."""
    d = region.dist[centre]
    if not cfg.features.wind_zones:
        return d
    ang, speed = env.recent_wind(t)
    bearing = env.bearing[:, centre]
    k = cfg.alert.wind_stretch * min(1.0, speed / cfg.environment.wind_speed_mean)
    # Reshape, don't enlarge: (1-k^2)^(-3/4) keeps the zone's area equal to the circle's.
    return d * (1 - k * np.cos(bearing - ang)) * (1 - k * k) ** -0.75


def _issue_alerts(cfg, region, env, c, cid, t, n_rep, n_farm, flat, alerts, last_alert, alert_log, enabled,
                  U_comply, inspect_due, treated_at, engaged_until, basis="reports") -> int:
    strength = c.llr / max(cfg.scan.threshold, 1e-9)
    d_eff = wind_distance(cfg, region, env, c.center_farm, t)
    pos = (flat["t_sync"] <= t) & (flat["t_cap"] > t - 3.0) & (flat["p"] >= cfg.scan.positive_p) \
        & np.isin(flat["farm"], c.members)
    pos_farms = np.unique(flat["farm"][pos])
    suppressed = 0
    for f in range(region.n_farms):
        risk = risk_for(d_eff[f], c.radius_km, strength, cfg.alert)
        if risk is None:
            continue
        prev = last_alert.get(f)
        escalation = prev is None or RISK_ORDER[risk] > RISK_ORDER[prev.risk]
        if not escalation:
            # Avoid alert fatigue: only escalations, or periodic reminders for HIGH risk.
            if risk != "HIGH" or t - prev.t < cfg.alert.resend_hours / 24:
                continue
        if cfg.features.alert_budget and not (escalation and risk == "HIGH"):
            recent = [x for x in alert_log[f] if t - x < 14]
            if len(recent) >= cfg.alert.max_alerts_per_14d:
                suppressed += 1
                continue
        nearest = float(region.dist[f, pos_farms].min()) if len(pos_farms) else float(region.dist[f, c.center_farm])
        a = Alert(t, f, risk, cid, n_rep, n_farm, 72, nearest, basis)
        last_alert[f] = a
        if not enabled:
            continue
        alerts.append(a)
        alert_log[f].append(t)
        engaged_until[f] = max(engaged_until[f], t + 7)
        if U_comply[f] < cfg.response.compliance and np.isinf(treated_at[f]):
            delay = cfg.response.inspect_delay_days[{"HIGH": 0, "MEDIUM": 1, "LOW": 2}[risk]]
            inspect_due[f] = min(inspect_due[f], t + delay)
    return suppressed
