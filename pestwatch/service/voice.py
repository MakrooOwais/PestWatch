"""Voice (IVR) call handling: Africa's Talking-style XML with recorded prompts when available."""
import os
from xml.sax.saxutils import escape, quoteattr

from .. import i18n
from ..alerts import Alert, render_voice

AUDIO_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "web", "audio")


def _prompt(text: str, lang: str, key: str, base_url: str, audio_dir: str) -> str:
    """<Play> a recorded prompt web/audio/<lang>/<key>.mp3 if it exists, else <Say> TTS."""
    if os.path.exists(os.path.join(audio_dir, lang, f"{key}.mp3")):
        return f"<Play url={quoteattr(f'{base_url}/audio/{lang}/{key}.mp3')}/>"
    return f'<Say voice="woman" playBeep="false">{escape(text)}</Say>'


def _menu(inner: str, base_url: str) -> str:
    return (f'<Response><GetDigits timeout="20" finishOnKey="#" numDigits="1" '
            f'callbackUrl={quoteattr(base_url + "/gateway/voice")}>{inner}</GetDigits></Response>')


def handle(state, form: dict, base_url: str, audio_dir: str = AUDIO_DIR) -> str:
    if str(form.get("isActive", "1")) == "0":
        return "<Response/>"   # end-of-call notification
    prof = state.profile_by_phone(form.get("callerNumber", "") or form.get("destinationNumber", ""))
    if prof is None:
        t = i18n.t("voice_menu.unregistered", "en")
        return f"<Response>{_prompt(t, 'en', 'unregistered', base_url, audio_dir)}</Response>"
    lang, digits = prof.language, str(form.get("dtmfDigits", "") or "").strip()
    if digits in ("1", "2"):
        rec = state.inbound(prof.farm, digits, channel="voice")
        bye = i18n.t("voice_menu.goodbye", lang)
        return (f"<Response>{_prompt(rec['reply'], lang, 'reply_' + rec['intent'], base_url, audio_dir)}"
                f"{_prompt(bye, lang, 'goodbye', base_url, audio_dir)}</Response>")
    st = state.status(prof.farm)
    if st["alert"]:
        a = st["alert"]
        alert = Alert(a["t"], a["farm"], a["risk"], a["cluster_id"], a["n_reports"], a["n_farms"], a["hours"],
                      a["nearest_km"])
        body = _prompt(render_voice(alert, lang), lang, f"alert_{a['risk']}", base_url, audio_dir)
    else:
        body = (_prompt(i18n.t("voice_menu.no_alert", lang), lang, "no_alert", base_url, audio_dir)
                + _prompt(i18n.t("voice_menu.menu", lang), lang, "menu", base_url, audio_dir))
    return _menu(body, base_url)
