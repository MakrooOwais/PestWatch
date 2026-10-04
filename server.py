"""PestWatch service: field-app sync API, farmer channels (SMS/USSD/voice), dashboard host.

Stdlib only. Live observations are scanned together with the simulated season's
background reports, frozen just before the first outbreak alert, so a few live
reports from the right area tip the cluster into an alert on stage. Everything
live (observations, replies, profiles, deliveries) is persisted in SQLite.

  python3 server.py [--port 8000] [--db path.db]
  open http://localhost:8000/            dashboard
  open http://localhost:8000/field.html  field app (works offline once loaded)

Farmer API (open, validated, rate-limited)
  GET  /api/farms                      farms + suggested demo farms
  POST /api/observations               {"observations": [{id, farm, captured_at, probs:[...], model}]}
  GET  /api/status?farm=<id>           area status + alert for that farm, in the farmer's language/channel
  POST /api/inbound                    {"farm": id, "text": "nimeona kutu"}  farmer reply in any language
  POST /api/profile                    {"farm": id, "language": "sw"}  (other fields need admin)
  GET  /api/i18n                       field-app strings for every served language
  GET  /api/public/risk                grid-level risk GeoJSON (coarsened, consenting farmers only)
Gateway webhooks (Africa's Talking-style form posts)
  POST /gateway/ussd                   sessionId, serviceCode, phoneNumber, text  -> "CON ..." / "END ..."
  POST /gateway/sms                    from, to, text, id, date                    -> parsed, reply via gateway
  POST /gateway/voice                  isActive, sessionId, callerNumber, dtmfDigits -> voice XML
Admin API (header X-API-Key)
  POST /api/reset                      clear live data and profile changes
  POST /api/admin/dispatch             send pending alerts now
  GET  /api/admin/deliveries           delivery log
  GET  /api/export/geojson             exact farm data
  GET  /api/export/csv                 live observations
"""
import argparse
import json
import math
import os
import sys
import threading
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pestwatch  # noqa: F401  (pins BLAS threads before numpy loads)
import numpy as np

from pestwatch import i18n
from pestwatch.alerts import Alert, render_voice, risk_for
from pestwatch.config import CLASSES, TARGET, Config
from pestwatch.environment import trap_anomaly
from pestwatch.farmers import CHANNELS, generate_profiles
from pestwatch.fusion import FusionModel, featurize
from pestwatch.i18n.inbound import parse
from pestwatch.messaging import deliver, deliver_notice, render_notice
from pestwatch.scan import SpaceTimeScan, reports_in_area
from pestwatch.service import privacy, ussd, voice
from pestwatch.service.auth import is_admin, load_admin_key
from pestwatch.service.dispatch import Dispatcher
from pestwatch.service.gateways import gateway_from_env
from pestwatch.service.ratelimit import default_limiters
from pestwatch.service.store import Store
from pestwatch.simulation import Notice, simulate, wind_distance

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
DEFAULT_DB = os.path.join(ROOT, "data", "pestwatch.db")
# Legacy name kept for callers that redirect live storage (e.g. scripts/walkthrough.py):
# if changed, the SQLite DB is placed next to it.
_DEFAULT_LIVE_LOG = os.path.join(ROOT, "data", "live_observations.jsonl")
LIVE_LOG = _DEFAULT_LIVE_LOG
MAX_BODY = 256 * 1024
MAX_TEXT = 480


def _vec(main: int, p: float):
    v = [(1 - p) / (len(CLASSES) - 1)] * len(CLASSES)
    v[main] = p
    return v


# What a farmer's inspection report means as evidence, in the same format as a photo result.
CONFIRMED = {"PEST_FOUND": _vec(TARGET, 0.95), "NO_PEST": _vec(CLASSES.index("healthy"), 0.95)}


def _db_path(explicit=None):
    if explicit:
        return explicit
    if os.environ.get("PESTWATCH_DB"):
        return os.environ["PESTWATCH_DB"]
    if LIVE_LOG != _DEFAULT_LIVE_LOG:
        return os.path.splitext(LIVE_LOG)[0] + ".db"
    return DEFAULT_DB


class LiveState:
    def __init__(self, db_path=None, gateway=None, limiters=None, admin_key=None):
        cfg = Config()
        cfg.fusion.weights_path = os.path.join(ROOT, "data", "fusion.json")
        cal = os.path.join(ROOT, "data", "calibration.json")
        if os.path.exists(cal):
            with open(cal) as f:
                c = json.load(f)
            cfg.scan.threshold = c["scan_threshold"]
            cfg.fusion.alert_p = c.get("fusion_alert_p", cfg.fusion.alert_p)
        seed = 0
        scen = os.path.join(WEB, "data", "scenario.json")
        if os.path.exists(scen):
            with open(scen) as f:
                seed = json.load(f)["meta"]["seed"]
        self.cfg, self.seed = cfg, seed
        self.fusion = FusionModel.load(cfg.fusion.weights_path) if cfg.features.fusion else None
        self.sim = simulate(cfg, seed, alerts_enabled=False)
        self.region = self.sim.region
        self.scan = SpaceTimeScan(self.region.dist, cfg.scan)
        self.t_live, self.focus = self._freeze_time()
        rep = self.sim.reports
        keep = rep["t_cap"] <= self.t_live
        self.bg = {"farm": rep["farm"][keep], "t_cap": rep["t_cap"][keep],
                   "t_sync": rep["t_sync"][keep], "p": rep["probs"][keep, TARGET]}
        self.lock = threading.RLock()
        self._version, self._cache = 0, None

        path = _db_path(db_path)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.store = Store(path)
        self.admin_key = admin_key or load_admin_key(os.path.join(os.path.dirname(os.path.abspath(path)), "admin_key"))
        self.ip_limiter, self.farm_limiter = limiters or default_limiters()
        self.gateway = gateway or gateway_from_env()
        self.dispatcher = Dispatcher(self, self.gateway)
        self._load()

    def _freeze_time(self):
        """Freeze the season at the last watch-level moment before the first alert."""
        sim, dt = self.sim, self.cfg.dt_days
        k_alert = next((i for i, evs in enumerate(sim.top_clusters)
                        if sim.times[i] >= (sim.t_intro or 0) and any(e.level == "ALERT" for e in evs)), None)
        if k_alert is None:
            return self.cfg.days / 2, None
        focus = next(e for e in sim.top_clusters[k_alert] if e.level == "ALERT")
        for j in range(k_alert - 1, max(0, k_alert - 12), -1):
            if any(e.level == "WATCH" and not e.trap_led for e in sim.top_clusters[j]):
                return float(sim.times[j]), focus
        return float(sim.times[k_alert] - dt), focus

    # ---- persistence ----
    def _load(self):
        self.profiles = generate_profiles(self.region, self.cfg.language, self.seed)
        stored = self.store.profiles()
        if not stored:
            for p in self.profiles:
                self.store.upsert_profile(p)
        for p in self.profiles:
            r = stored.get(p.farm)
            if r:
                p.language, p.channel, p.phone = r["language"], r["channel"], r["phone"] or ""
                p.consent, p.subscribed = bool(r["consent"]), bool(r["subscribed"])
        self._by_phone = {_norm_phone(p.phone): p for p in self.profiles if p.phone}
        self.live = {o["id"]: o for o in self.store.observations()}
        self.inbox = self.store.inbound()

    def add(self, observations, source="app"):
        accepted = []
        with self.lock:
            for o in observations:
                o = validate(o, self.region.n_farms)
                if o["id"] not in self.live:
                    self.live[o["id"]] = o
                    self.store.add_observation(o, source)
                    self._version += 1
                accepted.append(o["id"])
        return accepted

    def reset(self):
        with self.lock:
            self.store.clear()
            self._version += 1
            self._load()

    # ---- detection ----
    def evaluate(self):
        """Clusters and per-farm alerts for background + live data at t_live (cached per data version)."""
        with self.lock:
            if self._cache and self._cache[0] == self._version:
                return self._cache[1]
            live = list(self.live.values())
            version = self._version
        now = self.t_live + 1e-6
        lf = np.array([o["farm"] for o in live], dtype=int)
        lp = np.array([o["probs"][TARGET] for o in live], dtype=float)
        lt = np.full(len(live), self.t_live)
        farm_a = np.concatenate([self.bg["farm"], lf]).astype(int)
        tc = np.concatenate([self.bg["t_cap"], lt])
        ts = np.concatenate([self.bg["t_sync"], lt])
        p = np.concatenate([self.bg["p"], lp])
        thr = self.cfg.scan.threshold
        # Farmer confirmations (inspection replies) and trap catches feed the fusion model.
        conf_farms = np.array([o["farm"] for o in live if o.get("model") == "farmer-inspection"
                               and o["probs"][TARGET] >= 0.9], dtype=int)
        tz = trap_anomaly(self.sim.trap_readings, self.sim.env, self.cfg, now)
        clusters, alerts = [], {}
        for c in self.scan.scan(farm_a, tc, ts, p, now):
            near = self.sim.env.trap_farm_dist[:, c.center_farm] <= c.radius_km + 3.0
            c_tz = float(tz[near].max()) if near.any() else 0.0
            n_conf = int(np.isin(conf_farms, c.members).sum())
            prob = float(self.fusion.prob(featurize(c.llr, n_conf, c_tz))) if self.fusion else None
            if self.fusion:
                is_alert, is_watch = prob >= self.cfg.fusion.alert_p, prob >= self.cfg.scouting.watch_p
            else:
                is_alert, is_watch = c.llr >= thr, False
            is_watch = is_watch or c.llr >= self.cfg.scouting.watch_llr
            if not (is_alert or is_watch):
                continue
            n_rep, n_farm = reports_in_area(farm_a, tc, ts, p, now, c.members, 72, self.cfg.scan.positive_p)
            level = "ALERT" if is_alert else "WATCH"
            clusters.append({"level": level, "center_farm": c.center_farm, "radius_km": c.radius_km,
                             "village": self.region.village_names[self.region.farm_village[c.center_farm]],
                             "llr": round(c.llr, 2), "threshold": round(thr, 2), "n_reports": n_rep,
                             "probability": None if prob is None else round(prob, 3),
                             "alert_probability": round(self.cfg.fusion.alert_p, 3) if self.fusion else None,
                             "n_confirmed": n_conf, "trap_anomaly": round(c_tz, 1),
                             "n_farms": n_farm, "relative_risk": round(c.relative_risk, 2),
                             "members": [int(m) for m in c.members]})
            if level != "ALERT":
                continue
            pos = (p >= self.cfg.scan.positive_p) & np.isin(farm_a, c.members) & (tc > now - 3) & (ts <= now)
            pf = np.unique(farm_a[pos])
            d_eff = wind_distance(self.cfg, self.region, self.sim.env, c.center_farm, self.t_live)
            for f in range(self.region.n_farms):
                if f in alerts:
                    continue
                d = float(self.region.dist[f, c.center_farm])
                risk = risk_for(float(d_eff[f]), c.radius_km, c.llr / thr, self.cfg.alert)
                if risk:
                    nearest = float(self.region.dist[f, pf].min()) if len(pf) else d
                    basis = "traps" if (c.llr < thr and c_tz >= self.cfg.scouting.trap_anomaly) else "reports"
                    alerts[f] = Alert(self.t_live, f, risk, len(clusters) - 1, n_rep, n_farm, 72, nearest, basis)
        result = {"clusters": clusters, "alerts": alerts, "arrays": (farm_a, tc, ts, p, now),
                  "n_live": len(live)}
        with self.lock:
            self._cache = (version, result)
        return result

    def status(self, farm=None):
        ev = self.evaluate()
        alert = None
        a = ev["alerts"].get(farm) if farm is not None else None
        if a is not None:
            prof = self.profiles[farm]
            alert = {**a.to_json(), "msg": a.messages(),
                     "sms": {lg: a.sms(lg) for lg in i18n.languages()},
                     "voice": {lg: render_voice(a, lg) for lg in i18n.languages()},
                     "delivery": deliver(a, prof).to_json() if prof.subscribed else None}
        clusters = [{k: v for k, v in c.items() if k != "members"} for c in ev["clusters"]]
        notice = None
        if farm is not None and alert is None:
            # Near a watch-level signal: ask this farmer to check 10 plants (targeted scouting).
            near = [c for c in ev["clusters"] if c["level"] == "WATCH"
                    and self.region.dist[farm, c["center_farm"]] <= c["radius_km"] + 1.0]
            if near:
                prof = self.profiles[farm]
                notice = {"kind": "scout_request",
                          "msg": {lg: render_notice("scout_request", lg, "app") for lg in i18n.languages()},
                          "delivery": deliver_notice(Notice(self.t_live, farm, "scout_request", 0), prof).to_json()}
        return {"t_live": round(self.t_live, 2), "live_observations": ev["n_live"], "clusters": clusters,
                "alert": alert, "notice": notice,
                "profile": self.profiles[farm].to_json() if farm is not None else None}

    def cluster_members(self, c):
        return set(np.flatnonzero(self.region.dist[c["center_farm"]] <= c["radius_km"]).tolist())

    # ---- farmers ----
    def profile_by_phone(self, phone):
        return self._by_phone.get(_norm_phone(phone))

    def inbound(self, farm: int, text: str, channel: str = "sms"):
        """A farmer's reply in any language -> intent, side effects, reply in their language."""
        if not 0 <= farm < self.region.n_farms:
            raise ValueError("bad farm")
        text = str(text)[:MAX_TEXT]
        channel = str(channel)[:16]
        prof = self.profiles[farm]
        p = parse(text, prof.language)
        if p.intent in CONFIRMED:
            self.add([{"id": f"inbound-{farm}-{len(self.inbox)}-{p.intent}", "farm": farm,
                       "probs": CONFIRMED[p.intent], "model": "farmer-inspection"}], source=f"inbound:{channel}")
        elif p.intent == "STOP":
            self.set_profile(farm, subscribed=False)
        elif p.intent == "START":
            self.set_profile(farm, subscribed=True)
        elif p.intent == "LANGUAGE":
            self.set_profile(farm, language=p.new_language)
        rec = {"farm": farm, "channel": channel, "text": text, "intent": p.intent,
               "detected_lang": p.detected_lang, "reply_lang": p.lang, "reply": p.reply, "matched": p.matched}
        with self.lock:
            self.inbox.append(rec)
        self.store.add_inbound(rec)
        return {**rec, "profile": prof.to_json()}

    def set_profile(self, farm: int, language=None, channel=None, consent=None, subscribed=None, phone=None):
        with self.lock:
            prof = self.profiles[farm]
            if language is not None:
                prof.language = i18n.resolve(language)
            if channel is not None:
                if channel not in CHANNELS:
                    raise ValueError(f"channel must be one of {CHANNELS}")
                prof.channel = channel
            if consent is not None:
                prof.consent = bool(consent)
            if subscribed is not None:
                prof.subscribed = bool(subscribed)
            if phone is not None:
                ph = _norm_phone(phone)
                other = self._by_phone.get(ph)
                if other is not None and other.farm != farm:
                    raise ValueError("phone already registered to another farm")
                self._by_phone.pop(_norm_phone(prof.phone), None)
                prof.phone = ph
                self._by_phone[ph] = prof
            self.store.upsert_profile(prof)
            return prof.to_json()

    def farms(self):
        near = set()
        for c in self.evaluate()["clusters"]:
            near |= set(c["members"])
        if not near and self.focus is not None:
            near = set(np.flatnonzero(self.region.dist[self.focus.center_farm] <= self.focus.radius_km).tolist())
        return [{"id": i, "label": f"Farm {i + 1} · {self.region.village_names[self.region.farm_village[i]]}",
                 "app": bool(self.region.participates[i]), "suggested": i in near,
                 "language": self.profiles[i].language, "channel": self.profiles[i].channel}
                for i in range(self.region.n_farms)]


def _norm_phone(phone) -> str:
    """E.164-style key: '+' and digits only. Tolerates spaces/dashes and a '+' lost to form decoding."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())[:15]
    return "+" + digits if digits else ""


def validate(o, n_farms):
    if not isinstance(o, dict):
        raise ValueError("observation must be an object")
    oid = str(o.get("id", ""))[:64]
    farm = int(o.get("farm", -1))
    probs = o.get("probs")
    if not oid or not 0 <= farm < n_farms:
        raise ValueError("bad id or farm")
    if not isinstance(probs, list) or len(probs) != len(CLASSES):
        raise ValueError(f"probs must be a list of {len(CLASSES)} numbers")
    probs = [float(x) for x in probs]
    s = sum(probs)
    if any(x < 0 or x != x for x in probs) or not 0.98 <= s <= 1.02:
        raise ValueError("probs must be non-negative and sum to 1")
    return {"id": oid, "farm": farm, "probs": [x / s for x in probs],
            "captured_at": str(o.get("captured_at", ""))[:40], "model": str(o.get("model", ""))[:40]}


class HTTPError(Exception):
    def __init__(self, code, msg, headers=None):
        super().__init__(msg)
        self.code, self.msg, self.headers = code, msg, headers or {}


class Handler(SimpleHTTPRequestHandler):
    state: LiveState = None

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB, **kw)

    def log_message(self, fmt, *args):
        if any(s in str(args[0] if args else "") for s in ("/api/", "/gateway/")):
            sys.stderr.write("%s\n" % (fmt % args))

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    # ---- responses ----
    def _send(self, code, body: bytes, ctype, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj, headers=None):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", headers)

    def _text(self, code, text, ctype="text/plain; charset=utf-8"):
        self._send(code, text.encode(), ctype)

    # ---- request helpers ----
    def _raw(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > MAX_BODY:
            raise HTTPError(413, "body too large")
        return self.rfile.read(n) if n else b""

    def _body(self):
        raw = self._raw()
        ctype = self.headers.get("Content-Type", "")
        if "application/x-www-form-urlencoded" in ctype:
            return {k: v[-1] for k, v in parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}
        try:
            body = json.loads(raw or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPError(400, "invalid JSON")
        if not isinstance(body, dict):
            raise HTTPError(400, "body must be a JSON object")
        return body

    def _limit(self, limiter, key):
        ok, wait = limiter.allow(key)
        if not ok:
            raise HTTPError(429, "rate limit exceeded", {"Retry-After": str(max(1, math.ceil(wait)))})

    def _admin(self):
        if not is_admin(self.headers.get("X-API-Key"), self.state.admin_key):
            raise HTTPError(401, "admin API key required")

    def _base_url(self):
        return os.environ.get("PESTWATCH_PUBLIC_URL") or f"http://{self.headers.get('Host', 'localhost')}"

    def _guard(self, fn):
        try:
            fn()
        except HTTPError as e:
            self._json(e.code, {"error": e.msg}, e.headers)
        except (ValueError, KeyError, TypeError) as e:
            self._json(400, {"error": f"bad request: {e}"[:200]})
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._json(500, {"error": "internal error"})

    # ---- routes ----
    def do_GET(self):
        u = urlparse(self.path)
        if not (u.path.startswith("/api/") or u.path.startswith("/gateway/")):
            return super().do_GET()
        self._guard(lambda: self._get(u))

    def _get(self, u):
        st, q = self.state, parse_qs(u.query)
        if u.path == "/api/farms":
            return self._json(200, {"farms": st.farms(), "classes": CLASSES})
        if u.path == "/api/i18n":
            return self._json(200, i18n.ui_bundle())
        if u.path == "/api/status":
            farm = int(q["farm"][0]) if "farm" in q else None
            if farm is not None and not 0 <= farm < st.region.n_farms:
                raise HTTPError(400, "bad farm")
            return self._json(200, st.status(farm))
        if u.path == "/api/public/risk":
            return self._json(200, privacy.public_risk(st))
        if u.path == "/api/export/geojson":
            self._admin()
            return self._json(200, privacy.export_geojson(st))
        if u.path == "/api/export/csv":
            self._admin()
            return self._text(200, privacy.export_csv(st.store), "text/csv; charset=utf-8")
        if u.path == "/api/admin/deliveries":
            self._admin()
            return self._json(200, {"deliveries": st.store.deliveries()})
        raise HTTPError(404, "not found")

    def do_HEAD(self):
        u = urlparse(self.path)
        if u.path.startswith("/api/") or u.path.startswith("/gateway/"):
            return self._json(405, {"error": "method not allowed"}, {"Allow": "GET, POST"})
        return super().do_HEAD()

    def do_POST(self):
        self._guard(lambda: self._post(urlparse(self.path)))

    def _post(self, u):
        st = self.state
        self._limit(st.ip_limiter, ("ip", self.client_address[0]))
        if u.path == "/api/observations":
            body = self._body()
            obs = body.get("observations", [])
            if not isinstance(obs, list) or len(obs) > 200:
                raise ValueError("observations must be a list (max 200)")
            for f in {int(o.get("farm", -1)) for o in obs if isinstance(o, dict)}:
                self._limit(st.farm_limiter, ("farm", f))
            accepted = st.add(obs)
            st.dispatcher.run()
            farm = obs[-1]["farm"] if obs else None
            return self._json(200, {"accepted": accepted, **st.status(int(farm) if farm is not None else None)})
        if u.path == "/api/inbound":
            b = self._body()
            farm = int(b["farm"])
            self._limit(st.farm_limiter, ("farm", farm))
            rec = st.inbound(farm, b.get("text", ""), str(b.get("channel", "app")))
            st.dispatcher.run()
            return self._json(200, rec)
        if u.path == "/api/profile":
            b = self._body()
            farm = int(b["farm"])
            if not 0 <= farm < st.region.n_farms:
                raise ValueError("bad farm")
            self._limit(st.farm_limiter, ("farm", farm))
            privileged = {k: b[k] for k in ("channel", "consent", "subscribed", "phone") if k in b}
            if privileged:
                self._admin()
            return self._json(200, st.set_profile(farm, b.get("language"), **privileged))
        if u.path == "/api/reset":
            self._admin()
            st.reset()
            return self._json(200, {"ok": True})
        if u.path == "/api/admin/dispatch":
            self._admin()
            return self._json(200, st.dispatcher.run())
        if u.path == "/gateway/ussd":
            f = self._body()
            phone = str(f.get("phoneNumber", ""))[:20]
            self._limit(st.farm_limiter, ("phone", _norm_phone(phone)))
            out = ussd.handle(st, str(f.get("sessionId", ""))[:64], str(f.get("serviceCode", ""))[:32],
                              phone, str(f.get("text", ""))[:MAX_TEXT])
            st.dispatcher.run()
            return self._text(200, out)
        if u.path == "/gateway/sms":
            f = self._body()
            phone = str(f.get("from", ""))[:20]
            self._limit(st.farm_limiter, ("phone", _norm_phone(phone)))
            prof = st.profile_by_phone(phone)
            if prof is None:
                st.gateway.send_sms(phone, i18n.t("ussd.unregistered", "en"))
                return self._text(200, "OK")
            rec = st.inbound(prof.farm, str(f.get("text", ""))[:MAX_TEXT], channel="sms")
            st.dispatcher.reply(prof.farm, rec["reply"], rec["reply_lang"], "sms")
            st.dispatcher.run()
            return self._text(200, "OK")
        if u.path == "/gateway/voice":
            f = self._body()
            phone = str(f.get("callerNumber", ""))[:20]
            self._limit(st.farm_limiter, ("phone", _norm_phone(phone)))
            xml = voice.handle(st, {k: str(v)[:MAX_TEXT] for k, v in f.items()}, self._base_url())
            st.dispatcher.run()
            return self._text(200, xml, "application/xml; charset=utf-8")
        raise HTTPError(404, "not found")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--db", help="SQLite path (default: $PESTWATCH_DB or data/pestwatch.db)")
    a = ap.parse_args()
    Handler.state = LiveState(db_path=a.db)
    st = Handler.state.status()
    print(f"Live demo frozen at day {st['t_live']} (just before first alert); "
          f"clusters: {[(c['level'], c['village'], c['llr']) for c in st['clusters']]}; "
          f"db: {Handler.state.store.path}; gateway: {Handler.state.gateway.name}", flush=True)
    print(f"Dashboard: http://{a.host}:{a.port}/   Field app: http://{a.host}:{a.port}/field.html", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
