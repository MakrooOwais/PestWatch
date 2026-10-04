"""Token-bucket rate limiting keyed by client IP, farm or phone number."""
import os
import threading
import time
from typing import Tuple


def _env_pair(name: str, default: Tuple[float, float]) -> Tuple[float, float]:
    v = os.environ.get(name)
    if not v:
        return default
    rate, burst = v.split(",")
    return float(rate), float(burst)


class RateLimiter:
    def __init__(self, rate_per_s: float, burst: float):
        self.rate, self.burst = rate_per_s, burst
        self.buckets = {}
        self.lock = threading.Lock()

    def allow(self, key) -> Tuple[bool, float]:
        """(allowed, seconds until next token)"""
        now = time.monotonic()
        with self.lock:
            tokens, last = self.buckets.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= 1:
                self.buckets[key] = (tokens - 1, now)
                return True, 0.0
            self.buckets[key] = (tokens, now)
            return False, (1 - tokens) / self.rate if self.rate > 0 else 60.0


def default_limiters():
    """Defaults are generous enough for a live demo, tight enough to stop floods.
    Override with PESTWATCH_RATE_IP / PESTWATCH_RATE_FARM = "rate_per_s,burst"."""
    return (RateLimiter(*_env_pair("PESTWATCH_RATE_IP", (10.0, 60.0))),
            RateLimiter(*_env_pair("PESTWATCH_RATE_FARM", (1.0, 30.0))))
