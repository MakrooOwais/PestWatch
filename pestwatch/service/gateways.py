"""Outbound SMS / voice gateways.

ConsoleGateway (default) only logs. AfricasTalkingGateway talks to Africa's
Talking; endpoints and headers follow AT's official Python SDK
(api[.sandbox].africastalking.com/version1/messaging, voice[.sandbox].africastalking.com/call,
header ``apiKey``). Select with PESTWATCH_GATEWAY=console|africastalking.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
import uuid
from typing import Tuple


class Gateway:
    name = "base"

    def send_sms(self, to: str, text: str) -> Tuple[str, str]:
        """Returns (gateway message id, status)."""
        raise NotImplementedError

    def call(self, to: str) -> Tuple[str, str]:
        """Start an outbound voice call; the call script is served by /gateway/voice."""
        raise NotImplementedError


class ConsoleGateway(Gateway):
    name = "console"

    def __init__(self, stream=None):
        self.stream = stream or sys.stderr
        self.sent = []

    def send_sms(self, to, text):
        mid = f"console-{uuid.uuid4().hex[:12]}"
        self.sent.append(("sms", to, text))
        self.stream.write(f"[sms -> {to}] {text}\n")
        return mid, "logged"

    def call(self, to):
        mid = f"console-{uuid.uuid4().hex[:12]}"
        self.sent.append(("voice", to, None))
        self.stream.write(f"[voice call -> {to}]\n")
        return mid, "logged"


class AfricasTalkingGateway(Gateway):
    name = "africastalking"

    def __init__(self, username: str, api_key: str, sender_id: str = None, voice_number: str = None,
                 timeout: float = 15.0):
        if not username or not api_key:
            raise ValueError("AT_USERNAME and AT_API_KEY are required for the Africa's Talking gateway")
        self.username, self.api_key = username, api_key
        self.sender_id, self.voice_number, self.timeout = sender_id, voice_number, timeout
        domain = "sandbox.africastalking.com" if username == "sandbox" else "africastalking.com"
        self.sms_url = f"https://api.{domain}/version1/messaging"
        self.voice_url = f"https://voice.{domain}/call"

    def _post(self, url, data):
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST", headers={
            "apiKey": self.api_key, "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read() or b"{}")

    def send_sms(self, to, text):
        data = {"username": self.username, "to": to, "message": text}
        if self.sender_id:
            data["from"] = self.sender_id
        res = self._post(self.sms_url, data)
        rec = (res.get("SMSMessageData", {}).get("Recipients") or [{}])[0]
        return rec.get("messageId", ""), rec.get("status", "unknown")

    def call(self, to):
        if not self.voice_number:
            raise ValueError("AT_VOICE_NUMBER is required for outbound calls")
        res = self._post(self.voice_url, {"username": self.username, "from": self.voice_number, "to": to})
        entry = (res.get("entries") or [{}])[0]
        return entry.get("sessionId", ""), entry.get("status", res.get("errorMessage", "unknown"))


def gateway_from_env() -> Gateway:
    kind = os.environ.get("PESTWATCH_GATEWAY", "console").lower()
    if kind == "africastalking":
        return AfricasTalkingGateway(os.environ.get("AT_USERNAME", ""), os.environ.get("AT_API_KEY", ""),
                                     os.environ.get("AT_SENDER_ID") or None, os.environ.get("AT_VOICE_NUMBER") or None)
    if kind != "console":
        raise ValueError(f"unknown PESTWATCH_GATEWAY {kind!r}")
    return ConsoleGateway()
