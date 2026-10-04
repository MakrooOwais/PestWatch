import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pestwatch import i18n  # noqa: E402
from pestwatch.alerts import Alert, render, render_sms, render_voice  # noqa: E402
from pestwatch.farmers import FarmerProfile  # noqa: E402
from pestwatch.i18n.inbound import parse  # noqa: E402
from pestwatch.i18n.sms import fit, segments  # noqa: E402
from pestwatch.messaging import deliver  # noqa: E402

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def leaves(node, prefix=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from leaves(v, f"{prefix}.{k}" if prefix else k)
    else:
        yield prefix, node


class CatalogTest(unittest.TestCase):
    def test_every_language_has_every_key_with_same_placeholders(self):
        cats = i18n.catalogs()
        en = dict(leaves({k: v for k, v in cats["en"].items() if k not in ("meta", "intents")}))
        for code, c in cats.items():
            got = dict(leaves({k: v for k, v in c.items() if k not in ("meta", "intents")}))
            self.assertEqual(set(got), set(en), f"{code}: key mismatch {set(en) ^ set(got)}")
            for key, v in en.items():
                want, have = set(PLACEHOLDER.findall(str(v))), set(PLACEHOLDER.findall(str(got[key])))
                if key.endswith(".one"):
                    want.discard("n")   # singular may spell the number out ("d'une exploitation")
                    have.discard("n")
                self.assertEqual(want, have, f"{code}:{key} placeholders differ")
            self.assertEqual(set(c["intents"]), set(cats["en"]["intents"]), code)
            self.assertIn("review_status", c["meta"])

    def test_plurals_and_numbers(self):
        self.assertEqual(i18n.t("alert.reports", "en", count=1), "1 report")
        self.assertEqual(i18n.t("alert.reports", "en", count=18), "18 reports")
        self.assertEqual(i18n.t("alert.farms", "sw", count=1), "shamba 1")
        self.assertEqual(i18n.t("alert.farms", "sw", count=7), "mashamba 7")
        self.assertEqual(i18n.t("alert.farms", "fr", count=1), "d'une exploitation")
        self.assertEqual(i18n.number(1.25, "fr"), "1,2")

    def test_unknown_language_falls_back(self):
        self.assertEqual(i18n.resolve("xx"), "en")
        self.assertEqual(i18n.resolve("sw-KE"), "sw")
        a = Alert(1, 0, "HIGH", 0, 3, 2, 72, 1.0)
        self.assertEqual(render(a, "xx"), render(a, "en"))


class OutboundTest(unittest.TestCase):
    def test_sms_single_segment_worst_case_every_language_and_tier(self):
        for lang in i18n.languages():
            for risk in ("HIGH", "MEDIUM", "LOW"):
                txt = render_sms(Alert(1, 0, risk, 0, 999, 999, 72, 9.9), lang)
                enc, seg = segments(txt)
                self.assertEqual((enc, seg), ("GSM-7", 1), f"{lang}/{risk}: {enc} {seg} {txt!r}")

    def test_non_gsm_text_is_transliterated_to_fit(self):
        text, enc, seg = fit("Ça a été très français, bientôt: ê î ô û ç " * 2)
        self.assertEqual((enc, seg), ("GSM-7", 1))
        self.assertIn("Ç", text)        # GSM-7 has Ç and é, so they are kept
        self.assertIn("é", text)
        self.assertNotIn("ê", text)

    def test_delivery_follows_profile(self):
        a = Alert(1, 4, "MEDIUM", 0, 18, 7, 72, 1.2)
        app = deliver(a, FarmerProfile(4, "fr", "app"))
        self.assertTrue(app.text.startswith("Rouille orangée du caféier"))
        self.assertIn("18 signalements provenant de 7 exploitations", app.text)
        self.assertIn("1,2 km", app.text)
        sms = deliver(a, FarmerProfile(4, "sw", "sms"))
        self.assertEqual((sms.encoding, sms.segments), ("GSM-7", 1))
        self.assertIn("Hatari WASTANI", sms.text)
        voice = deliver(a, FarmerProfile(4, "sw", "voice"))
        self.assertIn("bonyeza 1", voice.text)
        self.assertEqual(voice.text, render_voice(a, "sw"))


class InboundTest(unittest.TestCase):
    CASES = [
        # text, farmer's profile language, expected intent, expected reply language
        ("1", "sw", "PEST_FOUND", "sw"),
        ("2", "fr", "NO_PEST", "fr"),
        ("Nimeona kutu kwenye majani!", "en", "PEST_FOUND", "sw"),
        ("sijaona kutu", "en", "NO_PEST", "sw"),              # negation beats "kutu"
        ("Hakuna kutu shambani", "en", "NO_PEST", "sw"),
        ("J'ai trouvé de la rouille", "sw", "PEST_FOUND", "fr"),
        ("pas de rouille", "en", "NO_PEST", "fr"),             # negation beats "rouille"
        ("FOUND RUST!!", "sw", "PEST_FOUND", "en"),
        ("no rust here", "sw", "NO_PEST", "en"),
        ("rouile", "en", "PEST_FOUND", "fr"),                  # typo
        ("nimeonaa", "en", "PEST_FOUND", "sw"),                # typo
        ("STOP", "fr", "STOP", "fr"),
        ("msaada", "en", "HELP", "sw"),
        ("asdfgh", "sw", "UNKNOWN", "sw"),
        ("Please ask a person to check my farm", "sw", "REFERRAL", "en"),   # fail-safe: route to a human
        ("Tafadhali mtu aje kukagua shamba langu", "en", "REFERRAL", "sw"),
    ]

    def test_cases(self):
        for text, prof, intent, lang in self.CASES:
            p = parse(text, prof)
            self.assertEqual((p.intent, p.lang), (intent, lang), f"{text!r}: {p}")
            self.assertEqual(p.reply, i18n.t(f"reply.{intent}", lang))

    def test_language_switch(self):
        for text, code in (("Kiswahili", "sw"), ("francais", "fr"), ("lugha english", "en"), ("FR", "fr")):
            p = parse(text, "en")
            self.assertEqual((p.intent, p.new_language), ("LANGUAGE", code), text)


if __name__ == "__main__":
    unittest.main()
