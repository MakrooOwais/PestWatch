"""Admin API-key auth. Key from PESTWATCH_ADMIN_KEY, else a generated key persisted with 0600 perms."""
import hmac
import os
import secrets
import sys


def load_admin_key(key_file: str) -> str:
    env = os.environ.get("PESTWATCH_ADMIN_KEY")
    if env:
        return env
    if os.path.exists(key_file):
        with open(key_file) as f:
            return f.read().strip()
    key = secrets.token_urlsafe(24)
    os.makedirs(os.path.dirname(key_file) or ".", exist_ok=True)
    fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    sys.stderr.write(f"Generated admin API key (stored in {key_file}): {key}\n")
    return key


def is_admin(provided, key: str) -> bool:
    return bool(provided) and hmac.compare_digest(str(provided).encode(), key.encode())
