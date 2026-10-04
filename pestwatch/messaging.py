"""Outbound pipeline: alert + farmer profile -> the message actually delivered."""
from dataclasses import asdict, dataclass

from .alerts import Alert, render, render_sms, render_voice
from .farmers import FarmerProfile
from .i18n import resolve, t
from .i18n.sms import fit


@dataclass
class Delivery:
    farm: int
    channel: str        # app | sms | voice
    language: str
    text: str
    encoding: str       # app: UTF-8, sms: GSM-7/UCS-2, voice: TTS script
    segments: int

    def to_json(self):
        return asdict(self)


def deliver(alert: Alert, profile: FarmerProfile) -> Delivery:
    lang = resolve(profile.language)
    if profile.channel == "sms":
        text, enc, seg = fit(render_sms(alert, lang))
        return Delivery(alert.farm, "sms", lang, text, enc, seg)
    if profile.channel == "voice":
        return Delivery(alert.farm, "voice", lang, render_voice(alert, lang), "TTS", 1)
    text = render(alert, lang) + " " + t("alert.reply_hint", lang)
    return Delivery(alert.farm, "app", lang, text, "UTF-8", 1)


NOTICE_KEY = {"all_clear": "all_clear", "scout_request": "scout"}


def render_notice(kind: str, lang: str, channel: str, days: int = 0) -> str:
    """Scouting request or all-clear text for a language and channel."""
    text = t(f"{NOTICE_KEY[kind]}.{channel}", lang, days=days)
    return fit(text)[0] if channel == "sms" else text


def deliver_notice(notice, profile: FarmerProfile) -> Delivery:
    lang = resolve(profile.language)
    text = render_notice(notice.kind, lang, profile.channel, getattr(notice, "days", 0))
    if profile.channel == "sms":
        _, enc, seg = fit(text)
        return Delivery(notice.farm, "sms", lang, text, enc, seg)
    enc = "TTS" if profile.channel == "voice" else "UTF-8"
    return Delivery(notice.farm, profile.channel, lang, text, enc, 1)
