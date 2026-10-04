"""SQLite persistence (stdlib only). One connection, serialized by a lock."""
import json
import sqlite3
import threading
import time
from typing import Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY, farm INTEGER NOT NULL, probs TEXT NOT NULL, captured_at TEXT,
  model TEXT, source TEXT, received_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS inbound (
  id INTEGER PRIMARY KEY AUTOINCREMENT, farm INTEGER NOT NULL, channel TEXT, text TEXT, intent TEXT,
  detected_lang TEXT, reply_lang TEXT, reply TEXT, matched TEXT, received_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS profiles (
  farm INTEGER PRIMARY KEY, language TEXT NOT NULL, channel TEXT NOT NULL, phone TEXT UNIQUE,
  consent INTEGER NOT NULL DEFAULT 1, subscribed INTEGER NOT NULL DEFAULT 1, updated_at REAL);
CREATE TABLE IF NOT EXISTS deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT UNIQUE, farm INTEGER, kind TEXT, risk TEXT,
  channel TEXT, language TEXT, text TEXT, encoding TEXT, segments INTEGER, gateway TEXT,
  gateway_id TEXT, status TEXT, created_at REAL NOT NULL);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(SCHEMA)
            self.db.commit()

    def _exec(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    def _all(self, sql, args=()) -> List[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    # observations
    def add_observation(self, o: dict, source: str = "app") -> bool:
        cur = self._exec("INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?)",
                         (o["id"], o["farm"], json.dumps(o["probs"]), o.get("captured_at", ""),
                          o.get("model", ""), source, time.time()))
        return cur.rowcount == 1

    def observations(self) -> List[dict]:
        rows = self._all("SELECT * FROM observations ORDER BY received_at, id")
        for r in rows:
            r["probs"] = json.loads(r["probs"])
        return rows

    # inbound
    def add_inbound(self, rec: dict):
        self._exec("INSERT INTO inbound (farm, channel, text, intent, detected_lang, reply_lang, reply, matched,"
                   " received_at) VALUES (?,?,?,?,?,?,?,?,?)",
                   (rec["farm"], rec["channel"], rec["text"], rec["intent"], rec["detected_lang"],
                    rec["reply_lang"], rec["reply"], json.dumps(rec["matched"]), time.time()))

    def inbound(self) -> List[dict]:
        rows = self._all("SELECT * FROM inbound ORDER BY id")
        for r in rows:
            r["matched"] = json.loads(r["matched"] or "[]")
        return rows

    # profiles
    def upsert_profile(self, p):
        self._exec("INSERT INTO profiles VALUES (?,?,?,?,?,?,?) ON CONFLICT(farm) DO UPDATE SET "
                   "language=excluded.language, channel=excluded.channel, phone=excluded.phone, "
                   "consent=excluded.consent, subscribed=excluded.subscribed, updated_at=excluded.updated_at",
                   (p.farm, p.language, p.channel, p.phone or None, int(p.consent), int(p.subscribed), time.time()))

    def profiles(self) -> Dict[int, dict]:
        return {r["farm"]: r for r in self._all("SELECT * FROM profiles")}

    # deliveries
    def add_delivery(self, dedupe_key: Optional[str], **d) -> bool:
        cols = ["dedupe_key", "farm", "kind", "risk", "channel", "language", "text", "encoding", "segments",
                "gateway", "gateway_id", "status", "created_at"]
        vals = [dedupe_key] + [d.get(c) for c in cols[1:-1]] + [time.time()]
        cur = self._exec(f"INSERT OR IGNORE INTO deliveries ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        return cur.rowcount == 1

    def deliveries(self, farm: Optional[int] = None, kind: Optional[str] = None) -> List[dict]:
        q, a = "SELECT * FROM deliveries WHERE 1=1", []
        if farm is not None:
            q, a = q + " AND farm=?", a + [farm]
        if kind is not None:
            q, a = q + " AND kind=?", a + [kind]
        return self._all(q + " ORDER BY id", a)

    def clear(self):
        with self.lock:
            for t in ("observations", "inbound", "profiles", "deliveries"):
                self.db.execute(f"DELETE FROM {t}")
            self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()
