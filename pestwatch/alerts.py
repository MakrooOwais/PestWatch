"""Turn detected clusters into per-farm alerts, rendered for any language and channel."""
from dataclasses import asdict, dataclass
from typing import Dict

from . import i18n
from .config import AlertConfig
from .i18n.sms import fit

RISK_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


@dataclass
class Alert:
    t: float
    farm: int
    risk: str
    cluster_id: int
    n_reports: int
    n_farms: int
    hours: int
    nearest_km: float
    basis: str = "reports"      # "reports" (photo/farmer evidence) or "traps" (moth flight caught, eggs likely)

    def messages(self) -> Dict[str, str]:
        return {lang: render(self, lang) for lang in i18n.languages()}

    def sms(self, lang: str = "en") -> str:
        return render_sms(self, lang)

    def to_json(self) -> dict:
        d = asdict(self)
        d["t"] = round(self.t, 3)
        d["nearest_km"] = round(self.nearest_km, 1)
        return d


def risk_for(distance_km: float, radius_km: float, strength: float, cfg: AlertConfig):
    """Risk tier for a farm at ``distance_km`` from a cluster centre (None = outside alert area)."""
    if distance_km <= radius_km:
        return "HIGH"
    if distance_km <= radius_km + cfg.buffer_km / 2:
        return "HIGH" if strength >= 2.0 else "MEDIUM"
    if distance_km <= radius_km + cfg.buffer_km:
        return "MEDIUM" if strength >= 2.0 else "LOW"
    return None


def _sentence(s: str) -> str:
    return s[:1].upper() + s[1:]


def _parts(a: Alert, lang: str) -> Dict[str, str]:
    t = i18n.t
    level = t(f"alert.level.{a.risk}", lang)
    if a.basis == "traps":
        return {"head": t("alert.forecast_head", lang), "counts": "",
                "risk": t("alert.risk", lang, level=level), "level": level,
                "action": t(f"alert.action.{a.risk}", lang), "tip": t("alert.tip_own", lang).split(".")[0] + "."}
    return {
        "head": t("alert.head", lang),
        "counts": _sentence(t("alert.counts", lang, reports=t("alert.reports", lang, count=a.n_reports),
                              farms=t("alert.farms", lang, count=a.n_farms), hours=a.hours)),
        "risk": t("alert.risk", lang, level=level),
        "level": level,
        "action": t(f"alert.action.{a.risk}", lang),
        "tip": t("alert.tip_own", lang) if a.nearest_km < 0.05
        else t("alert.tip", lang, km=i18n.number(a.nearest_km, lang)),
    }


def render(a: Alert, lang: str = "en") -> str:
    """Full app-push text."""
    p = _parts(a, lang)
    return " ".join(x for x in (p["head"], p["counts"], p["risk"], p["action"], p["tip"]) if x)


def render_sms(a: Alert, lang: str = "en") -> str:
    """Single-segment SMS (GSM-7 when possible) for feature phones."""
    key = "sms.forecast_template" if a.basis == "traps" else "sms.template"
    text = i18n.t(key, lang, r=a.n_reports, f=a.n_farms, h=a.hours,
                  level=i18n.t(f"alert.level.{a.risk}", lang), when=i18n.t(f"sms.when.{a.risk}", lang))
    return fit(text)[0]


def render_voice(a: Alert, lang: str = "en") -> str:
    """IVR / text-to-speech script for farmers who prefer calls (low literacy)."""
    return " ".join(i18n.t("voice.template", lang, **_parts(a, lang)).split())
