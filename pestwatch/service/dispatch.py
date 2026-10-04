"""Outbound alert dispatch: once per farm per escalation, over the farmer's own channel."""
import threading

from ..alerts import RISK_ORDER
from ..messaging import deliver


class Dispatcher:
    def __init__(self, state, gateway):
        self.state, self.gateway = state, gateway
        self.lock = threading.Lock()

    def _send(self, prof, channel, text):
        if channel == "app":
            return "", "stored"      # the field app fetches it from /api/status
        if not prof.phone:
            return "", "failed: no phone"
        try:
            if channel == "sms":
                return self.gateway.send_sms(prof.phone, text)
            return self.gateway.call(prof.phone)   # script is served by /gateway/voice
        except Exception as e:  # gateway/network errors are recorded, never raised to clients
            return "", f"failed: {type(e).__name__}"

    def run(self) -> dict:
        store = self.state.store
        sent = skipped = failed = 0
        with self.lock:
            alerts = self.state.evaluate()["alerts"]
            for farm, alert in sorted(alerts.items()):
                prof = self.state.profiles[farm]
                if not prof.subscribed:
                    skipped += 1
                    continue
                prev = [d["risk"] for d in store.deliveries(farm, kind="alert") if not d["status"].startswith("failed")]
                if prev and RISK_ORDER[alert.risk] <= max(RISK_ORDER[r] for r in prev):
                    skipped += 1
                    continue
                d = deliver(alert, prof)
                gid, status = self._send(prof, d.channel, d.text)
                ok = not status.startswith("failed")
                store.add_delivery(f"alert:{farm}:{alert.risk}" if ok else None, farm=farm, kind="alert",
                                   risk=alert.risk, channel=d.channel, language=d.language, text=d.text,
                                   encoding=d.encoding, segments=d.segments, gateway=self.gateway.name,
                                   gateway_id=gid, status=status)
                sent += ok
                failed += not ok
        return {"sent": sent, "skipped": skipped, "failed": failed}

    def reply(self, farm: int, text: str, language: str, channel: str = "sms") -> str:
        """Send an SMS reply to an inbound message (USSD/voice reply inline instead)."""
        prof = self.state.profiles[farm]
        gid, status = self._send(prof, "sms", text) if channel == "sms" else ("", "inline")
        self.state.store.add_delivery(None, farm=farm, kind="reply", channel=channel, language=language,
                                      text=text, encoding=None, segments=None, gateway=self.gateway.name,
                                      gateway_id=gid, status=status)
        return status
