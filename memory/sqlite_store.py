from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.config import load_config


class SQLiteMemoryStore:
    def __init__(self) -> None:
        cfg = load_config()
        base = Path(__file__).resolve().parent.parent
        path_str = cfg.get("memory", {}).get("path", str(base / "data" / "edo.db"))
        self.path = Path(path_str)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._lock = threading.Lock()
        self._ensure_schema()
        self._maybe_import_legacy_json()

    def _ensure_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts INTEGER NOT NULL DEFAULT(strftime('%s','now')),
                  role TEXT NOT NULL,
                  content TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS queries (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts INTEGER NOT NULL DEFAULT(strftime('%s','now')),
                  text TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS summary (
                  id INTEGER PRIMARY KEY CHECK (id = 1),
                  text TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  text TEXT UNIQUE NOT NULL,
                  count INTEGER NOT NULL DEFAULT 1,
                  first_seen REAL NOT NULL DEFAULT (strftime('%s','now')),
                  last_seen REAL NOT NULL DEFAULT (strftime('%s','now'))
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS counters (
                  key TEXT PRIMARY KEY,
                  value INTEGER NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profile (
                  id INTEGER PRIMARY KEY CHECK (id = 1),
                  name TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sensor_readings (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts INTEGER NOT NULL DEFAULT(strftime('%s','now')),
                  source TEXT NOT NULL,                 -- 'remo' | 'switchbot' など
                  device_id TEXT,
                  device_name TEXT,
                  temperature REAL,
                  humidity REAL,
                  illuminance REAL,
                  motion INTEGER,                       -- 0/1
                  event_time TEXT                       -- ISO8601 from device
                )
                """
            )

    def _maybe_import_legacy_json(self) -> None:
        # Import once if DB is empty and legacy JSON exists
        try:
            cur = self._conn.execute("SELECT COUNT(1) FROM conversation")
            cnt = int(cur.fetchone()[0])
            if cnt > 0:
                return
            # try default legacy path
            base = Path(__file__).resolve().parent.parent
            legacy = base / "data" / "memory.json"
            if not legacy.exists():
                return
            data = {}
            try:
                with legacy.open("r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                return
            with self._conn:
                for t in data.get("conversation", []) or []:
                    if isinstance(t, dict) and t.get("role") and t.get("content"):
                        self._conn.execute("INSERT INTO conversation(role, content) VALUES (?, ?)", (t["role"], t["content"]))
                for q in data.get("queries", []) or []:
                    if isinstance(q, str) and q.strip():
                        self._conn.execute("INSERT INTO queries(text) VALUES (?)", (q.strip(),))
                s = data.get("summary", "")
                if isinstance(s, str) and s.strip():
                    self._conn.execute("INSERT OR REPLACE INTO summary(id, text) VALUES (1, ?)", (s,))
                name = None
                prof = data.get("profile", {})
                if isinstance(prof, dict):
                    nm = prof.get("name")
                    if isinstance(nm, str) and nm.strip():
                        name = nm.strip()
                if name:
                    self._conn.execute("INSERT OR REPLACE INTO profile(id, name) VALUES (1, ?)", (name,))
                for f in data.get("facts", []) or []:
                    if isinstance(f, dict) and isinstance(f.get("text"), str):
                        text = f["text"].strip()
                        if text:
                            self._conn.execute(
                                "INSERT OR IGNORE INTO facts(text, count, first_seen, last_seen) VALUES (?, ?, ?, ?)",
                                (
                                    text,
                                    int(f.get("count", 1)),
                                    float(f.get("first_seen", 0.0)),
                                    float(f.get("last_seen", 0.0)),
                                ),
                            )
        except Exception:
            pass

    # --- counters ---
    def inc_counter(self, key: str, inc: int = 1) -> None:
        with self._conn:
            cur = self._conn.execute("SELECT value FROM counters WHERE key = ?", (key,))
            row = cur.fetchone()
            cur_val = int(row[0]) if row else 0
            self._conn.execute("INSERT OR REPLACE INTO counters(key, value) VALUES (?, ?)", (key, cur_val + int(inc)))

    # --- queries ---
    def add_query(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        max_items = int(load_config().get("memory", {}).get("max_history", 20))
        with self._conn:
            self._conn.execute("INSERT INTO queries(text) VALUES (?)", (text,))
            # trim to last N
            self._conn.execute(
                "DELETE FROM queries WHERE id NOT IN (SELECT id FROM queries ORDER BY id DESC LIMIT ?)",
                (max_items,),
            )

    # --- conversation turns ---
    def add_turn(self, role: str, content: str) -> None:
        role = (role or "").strip()
        content = (content or "").strip()
        if not (role and content):
            return
        max_items = int(load_config().get("memory", {}).get("max_history", 20))
        with self._conn:
            self._conn.execute("INSERT INTO conversation(role, content) VALUES (?, ?)", (role, content))
            self._conn.execute(
                "DELETE FROM conversation WHERE id NOT IN (SELECT id FROM conversation ORDER BY id DESC LIMIT ?)",
                (max_items,),
            )

    def recent_turns(self, limit: int = 8) -> List[Dict[str, Any]]:
        limit = int(max(1, limit))
        cur = self._conn.execute(
            "SELECT role, content FROM conversation ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
        rows.reverse()
        return [{"role": r[0], "content": r[1]} for r in rows]

    # --- summary ---
    def get_summary(self) -> str:
        cur = self._conn.execute("SELECT text FROM summary WHERE id = 1")
        row = cur.fetchone()
        return str(row[0]) if row and isinstance(row[0], str) else ""

    def set_summary(self, summary: str) -> None:
        max_chars = int(load_config().get("learning", {}).get("max_summary_chars", 800))
        s = (summary or "").strip()
        if len(s) > max_chars:
            s = s[: max(0, max_chars - 1)] + "…"
        with self._conn:
            self._conn.execute("INSERT OR REPLACE INTO summary(id, text) VALUES (1, ?)", (s,))

    # --- facts ---
    def add_or_update_fact(self, fact: str) -> None:
        from time import time as _now
        fact = (fact or "").strip()
        if not fact:
            return
        ts = float(_now())
        with self._conn:
            # try update
            cur = self._conn.execute("SELECT count FROM facts WHERE text = ?", (fact,))
            row = cur.fetchone()
            if row:
                self._conn.execute(
                    "UPDATE facts SET count = ?, last_seen = ? WHERE text = ?",
                    (int(row[0]) + 1, ts, fact),
                )
            else:
                self._conn.execute(
                    "INSERT INTO facts(text, count, first_seen, last_seen) VALUES (?, ?, ?, ?)",
                    (fact, 1, ts, ts),
                )
            # trim oldest beyond limit
            max_items = int(load_config().get("learning", {}).get("max_facts", 50))
            self._conn.execute(
                """
                DELETE FROM facts
                WHERE id NOT IN (
                  SELECT id FROM facts ORDER BY last_seen DESC, count DESC LIMIT ?
                )
                """,
                (max_items,),
            )

    def recent_facts(self, limit: int = 5) -> List[Dict[str, Any]]:
        limit = int(max(1, limit))
        cur = self._conn.execute(
            "SELECT text, count, last_seen FROM facts ORDER BY count DESC, last_seen DESC LIMIT ?",
            (limit,),
        )
        out: List[Dict[str, Any]] = []
        for text, count, last_seen in cur.fetchall():
            out.append({"text": text, "count": int(count), "last_seen": float(last_seen)})
        return out

    # --- user profile ---
    def set_user_name(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            return
        with self._conn:
            self._conn.execute("INSERT OR REPLACE INTO profile(id, name) VALUES (1, ?)", (name,))

    def get_user_name(self) -> str | None:
        cur = self._conn.execute("SELECT name FROM profile WHERE id = 1")
        row = cur.fetchone()
        if row and isinstance(row[0], str) and row[0].strip():
            return row[0].strip()
        return None

    # --- sensors ---
    def add_sensor_reading(
        self,
        source: str,
        device_id: Optional[str],
        device_name: Optional[str],
        temperature: Optional[float],
        humidity: Optional[float],
        illuminance: Optional[float],
        motion: Optional[int],
        event_time: Optional[str],
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO sensor_readings(source, device_id, device_name, temperature, humidity, illuminance, motion, event_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source or "unknown",
                    device_id,
                    device_name,
                    temperature,
                    humidity,
                    illuminance,
                    motion,
                    event_time,
                ),
            )

    # --- snapshot (approximate JSON shape for compatibility) ---
    def snapshot(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        out["conversation"] = self.recent_turns(limit=int(load_config().get("memory", {}).get("max_history", 20)))
        # queries (last N)
        cur = self._conn.execute("SELECT text FROM queries ORDER BY id DESC LIMIT ?", (int(load_config().get("memory", {}).get("max_history", 20)),))
        q = [r[0] for r in cur.fetchall()]
        q.reverse()
        out["queries"] = q
        out["summary"] = self.get_summary()
        out["facts"] = self.recent_facts(limit=int(load_config().get("learning", {}).get("max_facts", 50)))
        name = self.get_user_name()
        out["profile"] = {"name": name} if name else {}
        # do not dump sensor_readings to keep snapshot small
        return out

