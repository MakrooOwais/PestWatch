import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pestwatch  # noqa: E402,F401  (pins BLAS threads before numpy loads)
import numpy as np  # noqa: E402
import server  # noqa: E402
from pestwatch import i18n  # noqa: E402
from pestwatch.service import privacy, ussd, voice  # noqa: E402
from pestwatch.service.gateways import AfricasTalkingGateway, ConsoleGateway, gateway_from_env  # noqa: E402
from pestwatch.service.ratelimit import RateLimiter  # noqa: E402

KEY = "test-admin-key"


def make_state(tmp, name="s.db", limiters=None):
    gw = ConsoleGateway(stream=io.StringIO())
    lim = limiters or (RateLimiter(1000, 1000), RateLimiter(1000, 1000))
    return server.LiveState(db_path=os.path.join(tmp, name), gateway=gw, limiters=lim, admin_key=KEY)


def outbreak_farms(st, centre=40, km=1.5):
    return [int(f) for f in np.flatnonzero(st.region.dist[centre] <= km)]


def inject_outbreak(st, centre=40, km=1.5, prefix="x"):
    farms = outbreak_farms(st, centre, km)
    st.add([{"id": f"{prefix}-{f}-{j}", "farm": f, "probs": server.CONFIRMED["PEST_FOUND"]}
            for f in farms for j in range(2)])
    return farms


class Server:
    def __init__(self, st):
        handler = type("H", (server.Handler,), {"state": st, "log_message": lambda *a: None})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def req(self, method, path, body=None, form=None, headers=None):
        data, h = None, dict(headers or {})
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read().decode(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), dict(e.headers)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.st = make_state(cls.tmp)
        cls.farms = inject_outbreak(cls.st)
        cls.srv = Server(cls.st)

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()
        cls.st.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def phone(self, farm):
        return self.st.profiles[farm].phone

    # ---- USSD ----
    def test_ussd_every_language_and_screen_fits(self):
        f = self.farms[0]
        for lang in i18n.languages():
            self.st.set_profile(f, language=lang)
            for text in ["", "3", "4", "4*1", "0", "9", "1", "2"]:
                if text == "4*1":
                    out = ussd.handle(self.st, "s", "*384#", self.phone(f), text)
                    self.st.set_profile(f, language=lang)
                else:
                    out = ussd.handle(self.st, "s", "*384#", self.phone(f), text)
                self.assertTrue(out.startswith(("CON ", "END ")), (lang, text, out))
                self.assertLessEqual(len(out), ussd.MAX_SCREEN, (lang, text, out))
        self.assertTrue(ussd.handle(self.st, "s", "*384#", self.phone(f), "").startswith("CON "))
        self.st.set_profile(f, language="en")

    def test_ussd_language_change_and_status(self):
        f = self.farms[1]
        self.assertIn("1. English", ussd.handle(self.st, "s", "*384#", self.phone(f), "4"))
        idx = i18n.languages().index("sw") + 1
        out = ussd.handle(self.st, "s", "*384#", self.phone(f), f"4*{idx}")
        self.assertEqual(out, "END " + i18n.t("reply.LANGUAGE", "sw"))
        self.assertEqual(self.st.profiles[f].language, "sw")
        self.assertIn("Hatari", ussd.handle(self.st, "s", "*384#", self.phone(f), "3"))   # in an ALERT area
        self.st.set_profile(f, language="en")

    def test_phone_normalization(self):
        f = self.farms[0]
        ph = self.phone(f)
        for variant in (ph, " " + ph[1:], ph[1:], ph[:4] + " " + ph[4:7] + "-" + ph[7:]):
            self.assertEqual(self.st.profile_by_phone(variant).farm, f, variant)

    def test_ussd_unregistered(self):
        out = ussd.handle(self.st, "s", "*384#", "+19995550000", "")
        self.assertTrue(out.startswith("END "))
        self.assertLessEqual(len(out), ussd.MAX_SCREEN)

    def test_ussd_over_http(self):
        code, body, _ = self.srv.req("POST", "/gateway/ussd", form={
            "sessionId": "ATx1", "serviceCode": "*384#", "phoneNumber": self.phone(self.farms[-1]), "text": ""})
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith("CON PestWatch"))

    # ---- SMS ----
    def test_sms_webhook_parses_and_replies(self):
        f = self.farms[3 % len(self.farms)]
        before = len(self.st.store.inbound())
        code, body, _ = self.srv.req("POST", "/gateway/sms", form={
            "from": self.phone(f), "to": "12345", "text": "Nimeona kutu shambani", "id": "m1", "date": "2026-10-01"})
        self.assertEqual((code, body), (200, "OK"))
        rec = self.st.store.inbound()[before]
        self.assertEqual((rec["farm"], rec["intent"], rec["reply_lang"]), (f, "PEST_FOUND", "sw"))
        reply = [d for d in self.st.store.deliveries(f, kind="reply")][-1]
        self.assertEqual(reply["text"], i18n.t("reply.PEST_FOUND", "sw"))
        self.assertEqual(reply["status"], "logged")
        self.assertIn(("sms", self.phone(f), reply["text"]), self.st.gateway.sent)

    # ---- voice ----
    def test_voice_xml_say_and_play(self):
        f = self.farms[4 % len(self.farms)]
        xml = voice.handle(self.st, {"isActive": "1", "callerNumber": self.phone(f)}, "https://pw.example")
        root = ET.fromstring(xml)
        self.assertEqual(root.tag, "Response")
        self.assertIsNotNone(root.find("GetDigits/Say"))
        audio = tempfile.mkdtemp()
        lang = self.st.profiles[f].language
        risk = self.st.status(f)["alert"]["risk"]
        os.makedirs(os.path.join(audio, lang))
        open(os.path.join(audio, lang, f"alert_{risk}.mp3"), "wb").close()
        root = ET.fromstring(voice.handle(self.st, {"isActive": "1", "callerNumber": self.phone(f)},
                                          "https://pw.example", audio_dir=audio))
        play = root.find("GetDigits/Play")
        self.assertEqual(play.get("url"), f"https://pw.example/audio/{lang}/alert_{risk}.mp3")
        shutil.rmtree(audio)
        root = ET.fromstring(voice.handle(self.st, {"isActive": "1", "callerNumber": self.phone(f),
                                                    "dtmfDigits": "2"}, "https://pw.example"))
        self.assertIn(i18n.t("reply.NO_PEST", lang), root.find("Say").text)
        self.assertEqual(voice.handle(self.st, {"isActive": "0"}, "x"), "<Response/>")

    def test_voice_escapes_xml(self):
        f = self.farms[5 % len(self.farms)]
        xml = voice.handle(self.st, {"isActive": "1", "callerNumber": self.phone(f)}, 'https://a"b&<c>')
        ET.fromstring(xml)   # must stay well-formed with hostile base URL

    # ---- auth & errors ----
    def test_admin_endpoints_require_key(self):
        for method, path in (("POST", "/api/admin/dispatch"), ("GET", "/api/export/geojson"),
                             ("GET", "/api/export/csv"), ("GET", "/api/admin/deliveries")):
            code, _, _ = self.srv.req(method, path, body={} if method == "POST" else None)
            self.assertEqual(code, 401, path)
            code, _, _ = self.srv.req(method, path, body={} if method == "POST" else None,
                                      headers={"X-API-Key": "wrong"})
            self.assertEqual(code, 401, path)
            code, _, _ = self.srv.req(method, path, body={} if method == "POST" else None,
                                      headers={"X-API-Key": KEY})
            self.assertEqual(code, 200, path)

    def test_profile_language_open_channel_admin(self):
        f = self.farms[0]
        self.assertEqual(self.srv.req("POST", "/api/profile", body={"farm": f, "language": "fr"})[0], 200)
        self.assertEqual(self.st.profiles[f].language, "fr")
        self.assertEqual(self.srv.req("POST", "/api/profile", body={"farm": f, "channel": "sms"})[0], 401)
        code, body, _ = self.srv.req("POST", "/api/profile", body={"farm": f, "channel": "sms", "language": "en"},
                                     headers={"X-API-Key": KEY})
        self.assertEqual(code, 200)
        self.assertNotIn("phone", json.loads(body))

    def test_bad_requests(self):
        self.assertEqual(self.srv.req("POST", "/api/inbound", body=b"{not json")[0], 400)
        self.assertEqual(self.srv.req("POST", "/api/observations",
                                      body={"observations": [{"id": "a", "farm": 99999, "probs": [1]}]})[0], 400)
        self.assertEqual(self.srv.req("GET", "/api/nope")[0], 404)
        code, _, h = self.srv.req("GET", "/api/farms")
        self.assertEqual(code, 200)
        self.assertEqual(h.get("X-Content-Type-Options"), "nosniff")
        self.assertNotIn("Access-Control-Allow-Origin", h)

    # ---- privacy ----
    def test_public_risk_has_no_ids_or_exact_coordinates(self):
        code, body, _ = self.srv.req("GET", "/api/public/risk")
        self.assertEqual(code, 200)
        geo = json.loads(body)
        self.assertGreater(len(geo["features"]), 0)
        self.assertNotIn('"farm"', body)
        self.assertNotIn("phone", body)
        lat0 = self.st.cfg.region.origin_lat
        exact = {(round(float(lon), 4), round(float(lat), 4))
                 for lat, lon in self.st.region.latlon(self.st.cfg.region)}
        for feat in geo["features"]:
            lon, lat = feat["geometry"]["coordinates"]
            clat, clon = privacy.coarsen(lat, lon, lat0)
            self.assertAlmostEqual(clat, lat, places=3)
            self.assertAlmostEqual(clon, lon, places=3)
            self.assertNotIn((lon, lat), exact)
        self.assertTrue(any(f["properties"]["risk"] in ("HIGH", "MEDIUM", "LOW") for f in geo["features"]))

    def test_consent_withdrawn_cell_disappears(self):
        geo = privacy.public_risk(self.st)
        cell = geo["features"][0]["properties"]["cell"]
        latlon = self.st.region.latlon(self.st.cfg.region)
        lat0 = self.st.cfg.region.origin_lat
        in_cell = [p.farm for p in self.st.profiles
                   if "%d_%d" % privacy.cell_of(latlon[p.farm, 0], latlon[p.farm, 1], lat0) == cell]
        try:
            for f in in_cell:
                self.st.set_profile(f, consent=False)
            cells = {f["properties"]["cell"] for f in privacy.public_risk(self.st)["features"]}
            self.assertNotIn(cell, cells)
            # ...but they still get alerts.
            self.assertTrue(all(self.st.profiles[f].subscribed for f in in_cell))
        finally:
            for f in in_cell:
                self.st.set_profile(f, consent=True)


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = make_state(self.tmp)

    def tearDown(self):
        self.st.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_once_per_escalation_and_respects_unsubscribe(self):
        self.st.dispatcher.run()   # whatever the frozen background scenario already alerts
        farms = inject_outbreak(self.st)
        quiet = farms[0]
        self.st.set_profile(quiet, subscribed=False)
        first = self.st.dispatcher.run()
        self.assertGreater(first["sent"], 0)
        self.assertEqual(self.st.dispatcher.run()["sent"], 0)            # idempotent
        self.assertEqual(self.st.store.deliveries(quiet, kind="alert"), [])
        alerted = self.st.evaluate()["alerts"]
        channels = {d["channel"] for d in self.st.store.deliveries(kind="alert")}
        self.assertTrue(channels <= {"app", "sms", "voice"})
        sms = [d for d in self.st.store.deliveries(kind="alert") if d["channel"] == "sms"]
        for d in sms:
            self.assertEqual((d["encoding"], d["segments"]), ("GSM-7", 1))
        # Escalation: a farm that got MEDIUM/LOW gets a new message when its risk rises.
        low = [f for f, a in alerted.items() if a.risk != "HIGH" and f != quiet]
        if low:
            f = low[0]
            self.st.add([{"id": f"esc-{f}-{j}", "farm": int(g), "probs": server.CONFIRMED["PEST_FOUND"]}
                         for g in np.flatnonzero(self.st.region.dist[f] <= 0.8) for j in range(3)])
            if self.st.evaluate()["alerts"][f].risk == "HIGH":
                self.st.dispatcher.run()
                risks = [d["risk"] for d in self.st.store.deliveries(f, kind="alert")]
                self.assertEqual(risks[-1], "HIGH")
                self.assertEqual(len(risks), 2)

    def test_persistence_across_restart(self):
        f = inject_outbreak(self.st)[0]
        self.st.inbound(f, "lugha kiswahili", "sms")
        self.st.set_profile(f, consent=False)
        n_obs, n_in = len(self.st.live), len(self.st.inbox)
        self.st.store.close()
        st2 = make_state(self.tmp)
        try:
            self.assertEqual((len(st2.live), len(st2.inbox)), (n_obs, n_in))
            self.assertEqual(st2.profiles[f].language, "sw")
            self.assertFalse(st2.profiles[f].consent)
            self.assertEqual(st2.profile_by_phone(st2.profiles[f].phone).farm, f)
            self.assertIn("ALERT", [c["level"] for c in st2.evaluate()["clusters"]])
            st2.reset()
            self.assertEqual(len(st2.live), 0)
            self.assertTrue(st2.profiles[f].consent)
        finally:
            st2.store.close()
        self.st = make_state(self.tmp, "other.db")

    def test_rate_limit_429(self):
        st = make_state(self.tmp, "rl.db", limiters=(RateLimiter(0.01, 2), RateLimiter(1000, 1000)))
        srv = Server(st)
        try:
            codes = [srv.req("POST", "/api/inbound", body={"farm": 0, "text": "2"}) for _ in range(3)]
            self.assertEqual([c[0] for c in codes], [200, 200, 429])
            self.assertGreaterEqual(int(codes[2][2]["Retry-After"]), 1)
        finally:
            srv.close()
            st.store.close()

    def test_admin_key_generated_with_private_permissions(self):
        os.environ.pop("PESTWATCH_ADMIN_KEY", None)
        st = server.LiveState(db_path=os.path.join(self.tmp, "k.db"), gateway=ConsoleGateway(io.StringIO()))
        try:
            path = os.path.join(self.tmp, "admin_key")
            self.assertTrue(os.path.exists(path))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(open(path).read().strip(), st.admin_key)
        finally:
            st.store.close()


class GatewayTest(unittest.TestCase):
    def test_selection_and_urls_without_network(self):
        os.environ.pop("PESTWATCH_GATEWAY", None)
        self.assertIsInstance(gateway_from_env(), ConsoleGateway)
        sb = AfricasTalkingGateway("sandbox", "k")
        self.assertEqual(sb.sms_url, "https://api.sandbox.africastalking.com/version1/messaging")
        self.assertEqual(sb.voice_url, "https://voice.sandbox.africastalking.com/call")
        live = AfricasTalkingGateway("myapp", "k")
        self.assertEqual(live.sms_url, "https://api.africastalking.com/version1/messaging")
        with self.assertRaises(ValueError):
            AfricasTalkingGateway("", "")


class UntranslatedLanguageTest(unittest.TestCase):
    def test_skeleton_not_served_until_reviewed(self):
        import new_language
        tmp = tempfile.mkdtemp()
        old = i18n.CATALOG_DIR
        try:
            for name in os.listdir(old):
                shutil.copy(os.path.join(old, name), tmp)
            path = new_language.write("luy", "Luhya", "Oluluhya", catalog_dir=tmp)
            i18n.CATALOG_DIR = tmp
            i18n.all_catalogs.cache_clear()
            i18n.catalogs.cache_clear()
            self.assertIn("luy", i18n.all_catalogs())
            self.assertNotIn("luy", i18n.languages())
            self.assertEqual(i18n.resolve("luy"), "en")
            self.assertNotIn("luy", i18n.ui_bundle())
            with self.assertRaises(FileExistsError):
                new_language.write("luy", "Luhya", "Oluluhya", catalog_dir=tmp)
            with open(path) as fh:
                cat = json.load(fh)
            cat["meta"]["review_status"] = "draft"
            with open(path, "w") as fh:
                json.dump(cat, fh)
            i18n.all_catalogs.cache_clear()
            i18n.catalogs.cache_clear()
            self.assertEqual(i18n.resolve("luy"), "luy")
        finally:
            i18n.CATALOG_DIR = old
            i18n.all_catalogs.cache_clear()
            i18n.catalogs.cache_clear()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
