"""USSD menu (Africa's Talking style: text is the '*'-joined history of inputs).

Unknown numbers get a polite END asking them to register with their extension
officer (no self-registration: linking a phone to a farm should be verified).
"""
from .. import i18n

MAX_SCREEN = 182


def _fit(s: str) -> str:
    return s if len(s) <= MAX_SCREEN - 4 else s[:MAX_SCREEN - 5] + "…"


def con(s):
    return "CON " + _fit(s)


def end(s):
    return "END " + _fit(s)


def handle(state, session_id: str, service_code: str, phone: str, text: str) -> str:
    prof = state.profile_by_phone(phone)
    if prof is None:
        return end(" / ".join(i18n.t("ussd.unregistered", lg) for lg in ("en", "sw")))
    lang = prof.language
    steps = [s for s in (text or "").strip().split("*")] if text else []
    if not steps:
        return con(i18n.t("ussd.menu", lang))
    choice = steps[0].strip()
    if choice in ("1", "2"):
        state.inbound(prof.farm, choice, channel="ussd")
        return end(i18n.t("ussd.thanks_found" if choice == "1" else "ussd.thanks_none", lang))
    if choice == "3":
        st = state.status(prof.farm)
        if st["alert"]:
            a = st["alert"]
            return end(i18n.t("ussd.status_alert", lang, level=i18n.t(f"alert.level.{a['risk']}", lang),
                              action=i18n.t(f"alert.action.{a['risk']}", lang)))
        if any(c["level"] in ("WATCH", "ALERT") for c in st["clusters"]
               if prof.farm in state.cluster_members(c)):
            return end(i18n.t("ussd.status_watch", lang))
        return end(i18n.t("ussd.status_quiet", lang))
    if choice == "4":
        langs = i18n.languages()
        if len(steps) == 1:
            opts = "\n".join(f"{k + 1}. {i18n.catalogs()[c]['meta']['native_name']}" for k, c in enumerate(langs))
            return con(i18n.t("ussd.choose_language", lang) + "\n" + opts)
        try:
            new = langs[int(steps[1]) - 1]
        except (ValueError, IndexError):
            return end(i18n.t("ussd.invalid", lang))
        state.set_profile(prof.farm, language=new)
        return end(i18n.t("reply.LANGUAGE", new))
    if choice == "0":
        return end(i18n.t("ussd.help", lang))
    return end(i18n.t("ussd.invalid", lang))

