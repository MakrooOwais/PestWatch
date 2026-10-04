import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pestwatch  # noqa: E402,F401  (pins BLAS threads before numpy loads)
import numpy as np  # noqa: E402
from pestwatch.alerts import Alert, render, risk_for  # noqa: E402
from pestwatch.classifier import SimulatedClassifier  # noqa: E402
from pestwatch.config import TARGET, AlertConfig, ClassifierConfig, Config, ScanConfig  # noqa: E402
from pestwatch.metrics import compare, first_alert_time  # noqa: E402
from pestwatch.scan import SpaceTimeScan  # noqa: E402
from pestwatch.simulation import simulate  # noqa: E402


def grid_dist(n=10, spacing=0.5):
    xy = np.array([(i * spacing, j * spacing) for i in range(n) for j in range(n)])
    return xy, np.sqrt(((xy[:, None] - xy[None]) ** 2).sum(-1))


def background(rng, F, days, photos_per_farm_day=1.0, p_mean=0.05, mult=None):
    farm, t, p = [], [], []
    for f in range(F):
        k = rng.poisson(photos_per_farm_day * days * (1 if mult is None else mult[f]))
        farm += [f] * k
        t += list(rng.uniform(0, days, k))
        p += list(np.clip(rng.beta(1, 1 / p_mean - 1, k), 0, 1))
    return np.array(farm), np.array(t), np.array(p)


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.xy, self.dist = grid_dist()
        self.scan = SpaceTimeScan(self.dist, ScanConfig())
        self.rng = np.random.default_rng(0)

    def test_background_only_scores_low(self):
        farm, t, p = background(self.rng, 100, 30)
        self.assertLess(self.scan.max_score(farm, t, t, p, 30.0), 3.0)

    def test_injected_cluster_found_at_right_place(self):
        farm, t, p = background(self.rng, 100, 30)
        centre = 55
        near = np.flatnonzero(self.dist[centre] <= 1.0)
        extra_f = np.repeat(near, 2)
        extra_t = self.rng.uniform(28.5, 30, len(extra_f))
        farm = np.concatenate([farm, extra_f])
        t = np.concatenate([t, extra_t])
        p = np.concatenate([p, np.full(len(extra_f), 0.6)])  # moderate-confidence reports
        best = self.scan.scan(farm, t, t, p, 30.0)[0]
        self.assertGreater(best.llr, 5.0)
        self.assertLessEqual(self.dist[best.center_farm, centre], 1.0)
        self.assertGreaterEqual(best.n_farms, 3)

    def test_photo_volume_surge_does_not_alarm(self):
        """More photos with the same per-photo rate must not look like an outbreak."""
        mult = np.ones(100)
        mult[self.dist[55] <= 1.5] = 4.0
        farm, t, p = background(self.rng, 100, 30, mult=mult)
        self.assertLess(self.scan.max_score(farm, t, t, p, 30.0), 3.0)

    def test_unsynced_reports_are_invisible(self):
        farm, t, p = background(self.rng, 100, 30)
        near = np.flatnonzero(self.dist[55] <= 1.0)
        extra_f = np.repeat(near, 3)
        extra_t = np.full(len(extra_f), 29.5)
        farm2 = np.concatenate([farm, extra_f])
        t2 = np.concatenate([t, extra_t])
        sync = np.concatenate([t, extra_t + 2.0])  # still offline at day 30
        p2 = np.concatenate([p, np.full(len(extra_f), 0.8)])
        self.assertLess(self.scan.max_score(farm2, t2, sync, p2, 30.0), 3.0)
        self.assertGreater(self.scan.max_score(farm2, t2, sync, p2, 31.6), 5.0)


class ClassifierTest(unittest.TestCase):
    def test_probabilities_valid_and_early_damage_less_confident(self):
        clf = SimulatedClassifier(ClassifierConfig())
        rng = np.random.default_rng(1)
        n = 4000
        cls = np.full(n, TARGET)
        early = clf.predict_batch(rng, cls, np.full(n, 0.01))
        late = clf.predict_batch(rng, cls, np.full(n, 0.5))
        for pr in (early, late):
            np.testing.assert_allclose(pr.sum(1), 1.0, atol=1e-9)
            self.assertTrue((pr >= 0).all())
        self.assertLess(early[:, TARGET].mean() + 0.15, late[:, TARGET].mean())
        healthy = clf.predict_batch(rng, np.zeros(n, int), np.zeros(n))
        self.assertLess(healthy[:, TARGET].mean(), 0.1)


class AlertTest(unittest.TestCase):
    def test_tiers(self):
        cfg = AlertConfig(buffer_km=3.0)
        self.assertEqual(risk_for(0.5, 1.0, 1.0, cfg), "HIGH")
        self.assertEqual(risk_for(2.0, 1.0, 1.0, cfg), "MEDIUM")
        self.assertEqual(risk_for(2.0, 1.0, 3.0, cfg), "HIGH")
        self.assertEqual(risk_for(3.5, 1.0, 1.0, cfg), "LOW")
        self.assertIsNone(risk_for(4.5, 1.0, 1.0, cfg))

    def test_message_and_sms(self):
        a = Alert(t=20.0, farm=3, risk="HIGH", cluster_id=0, n_reports=18, n_farms=7, hours=72, nearest_km=1.2)
        self.assertTrue(render(a, "en").startswith(
            "Coffee leaf rust has been reported in your area. 18 reports from 7 farms in the last 72 hours. "
            "Risk: HIGH. Check your coffee within 24 hours."))
        self.assertIn("Hatari: JUU", render(a, "sw"))
        self.assertIn("Risque : ÉLEVÉ", render(a, "fr"))
        self.assertLessEqual(len(a.sms()), 160)


class SimulationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = Config()
        cls.cfg.scan.threshold = 2.4
        cls.pw = simulate(cls.cfg, 3, alerts_enabled=True)
        cls.cf = simulate(cls.cfg, 3, alerts_enabled=False)

    def test_deterministic(self):
        again = simulate(self.cfg, 3, alerts_enabled=True)
        np.testing.assert_array_equal(again.damage, self.pw.damage)
        self.assertEqual(len(again.alerts), len(self.pw.alerts))

    def test_arms_identical_until_first_pestwatch_action(self):
        t_act = self.pw.first_action_time
        self.assertIsNotNone(t_act)
        upto = self.pw.times <= t_act
        np.testing.assert_array_equal(self.pw.damage[upto], self.cf.damage[upto])
        # The counterfactual takes no PestWatch actions at all.
        self.assertEqual((len(self.cf.alerts), len(self.cf.notices), len(self.cf.scout_requests)), (0, 0, 0))

    def test_scouting_requests_are_throttled_and_answered(self):
        self.assertGreater(len(self.pw.scout_requests), 0)
        sc = self.cfg.scouting
        per_farm = {}
        for n in self.pw.notices:
            if n.kind == "scout_request":
                per_farm.setdefault(n.farm, []).append(n.t)
        for ts in per_farm.values():
            for t in ts:
                self.assertLessEqual(sum(1 for x in ts if t - 14 < x <= t), sc.max_requests_per_14d)
        from pestwatch.simulation import SCOUT_REPLY
        self.assertGreater(int((self.pw.reports["src"] == SCOUT_REPLY).sum()), 0)

    def test_alert_budget(self):
        cap = self.cfg.alert.max_alerts_per_14d
        by_farm = {}
        for a in self.pw.alerts:
            by_farm.setdefault(a.farm, []).append(a)
        for xs in by_farm.values():
            for i, a in enumerate(xs):
                window = [x for x in xs[:i + 1] if a.t - x.t < 14]
                # HIGH escalations may exceed the budget; nothing else may.
                non_escalation = [x for j, x in enumerate(window)
                                  if not (x.risk == "HIGH" and all(y.risk != "HIGH" for y in xs[:xs.index(x)]))]
                self.assertLessEqual(len(non_escalation), cap + 1)

    def test_fusion_probability_in_range(self):
        p = self.pw.probability
        self.assertIsNotNone(p)
        self.assertTrue(((p >= 0) & (p <= 1)).all())

    def test_pestwatch_reduces_damage(self):
        m = compare(self.pw, self.cf)
        self.assertGreater(m["loss_avoided_usd"], 0)
        self.assertGreater(m["recall"], 0.5)
        self.assertGreater(m["lead_vs_conventional_days"], 0)

    def test_no_outbreak_no_infestation(self):
        cfg = Config(outbreak=False)
        r = simulate(cfg, 5, alerts_enabled=False)
        self.assertEqual(r.damage.max(), 0.0)
        self.assertGreater(len(r.reports["farm"]), 1000)


if __name__ == "__main__":
    unittest.main()


class EnvironmentAndFusionTest(unittest.TestCase):
    def test_wind_zone_preserves_area(self):
        from pestwatch.simulation import simulate as sim, wind_distance
        cfg = Config()
        r = sim(cfg, 1, alerts_enabled=False)
        centre = int(np.argmin(((r.region.farm_xy - r.region.farm_xy.mean(0)) ** 2).sum(1)))
        # Monte-Carlo area check on a dense synthetic ring of points around the centre.
        ang, speed = r.env.recent_wind(20.0)
        k = cfg.alert.wind_stretch * min(1.0, speed / cfg.environment.wind_speed_mean)
        th = np.linspace(-np.pi, np.pi, 3601)
        radius = (1 - k * k) ** 0.75 / (1 - k * np.cos(th - ang))
        area = 0.5 * np.trapz(radius ** 2, th)
        self.assertAlmostEqual(area, np.pi, places=2)
        d = wind_distance(cfg, r.region, r.env, centre, 20.0)
        self.assertEqual(d.shape, (r.region.n_farms,))

    def test_trap_spike_on_landing(self):
        from pestwatch.environment import trap_anomaly
        cfg = Config()
        cfg.environment.traps_per_village = 1   # traps are optional (off for coffee leaf rust)
        r = simulate(cfg, 2, alerts_enabled=False)
        z_before = trap_anomaly(r.trap_readings, r.env, cfg, r.t_intro - 0.5).max()
        z_after = trap_anomaly(r.trap_readings, r.env, cfg, r.t_intro + cfg.pest.landing_days + 1.0).max()
        self.assertGreater(z_after, z_before + 2)

    def test_fusion_fit_recovers_signal(self):
        from pestwatch.fusion import FusionModel
        rng = np.random.default_rng(0)
        X = rng.normal(size=(3000, 3))
        y = (rng.random(3000) < 1 / (1 + np.exp(-(0.5 + X @ np.array([2.0, -1.0, 0.0]))))).astype(float)
        m = FusionModel.fit(X, y, l2=0.1)
        np.testing.assert_allclose(m.w, [2.0, -1.0, 0.0], atol=0.25)

    def test_sparse_scan_matches_dense(self):
        rng = np.random.default_rng(3)
        xy = rng.uniform(0, 10, (120, 2))
        d = np.sqrt(((xy[:, None] - xy[None]) ** 2).sum(-1))
        farm = rng.integers(0, 120, 2000)
        t = rng.uniform(0, 30, 2000)
        p = rng.beta(1, 10, 2000)
        hot = np.flatnonzero(d[7] < 1.0)
        farm[:30], t[:30], p[:30] = rng.choice(hot, 30), 29.5, 0.8
        a = SpaceTimeScan(d, ScanConfig()).scan(farm, t, t, p, 30.0)
        b = SpaceTimeScan(None, ScanConfig(), xy=xy).scan(farm, t, t, p, 30.0)
        self.assertEqual([(c.center_farm, round(c.llr, 6)) for c in a], [(c.center_farm, round(c.llr, 6)) for c in b])

    def test_spammer_alone_cannot_raise_alert(self):
        xy, dist = grid_dist()
        scan = SpaceTimeScan(dist, ScanConfig())
        rng = np.random.default_rng(1)
        farm = rng.integers(0, 100, 3000)
        t = rng.uniform(0, 30, 3000)
        p = rng.beta(1, 15, 3000)
        farm = np.concatenate([farm, np.full(200, 55)])     # one farm floods 200 bogus reports
        t = np.concatenate([t, rng.uniform(28, 30, 200)])
        p = np.concatenate([p, np.full(200, 0.9)])
        self.assertLess(scan.max_score(farm, t, t, p, 30.0), 2.0)
