from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any
from agent.config import load_config


class MemoryStore:
    def __init__(self) -> None:
        cfg = load_config()
        base = Path(__file__).resolve().parent.parent
        path_str = cfg.get("memory", {}).get("path", str(base / "data" / "memory.json"))
        self.path = Path(path_str)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as f:
                    self._data = json.load(f)
        except Exception:
            self._data = {}

    def _save(self) -> None:
        try:
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def inc_counter(self, key: str, inc: int = 1) -> None:
        self._data[key] = int(self._data.get(key, 0)) + inc
        self._save()

    def add_query(self, text: str) -> None:
        history = list(self._data.get("queries", []))
        max_items = int(load_config().get("memory", {}).get("max_history", 20))
        history.append(text)
        if len(history) > max_items:
            history = history[-max_items:]
        self._data["queries"] = history
        self._save()

    def add_turn(self, role: str, content: str) -> None:
        turns = list(self._data.get("conversation", []))
        max_items = int(load_config().get("memory", {}).get("max_history", 20))
        turns.append({"role": role, "content": content})
        if len(turns) > max_items:
            turns = turns[-max_items:]
        self._data["conversation"] = turns
        self._save()

    def recent_turns(self, limit: int = 8):
        turns = list(self._data.get("conversation", []))
        return turns[-limit:]

    # --- long-term summary ---
    def get_summary(self) -> str:
        s = self._data.get("summary", "")
        return s if isinstance(s, str) else ""

    def set_summary(self, summary: str) -> None:
        max_chars = int(load_config().get("learning", {}).get("max_summary_chars", 800))
        s = (summary or "").strip()
        if len(s) > max_chars:
            s = s[:max(0, max_chars - 1)] + "…"
        self._data["summary"] = s
        self._save()

    # --- user facts ---
    def add_or_update_fact(self, fact: str) -> None:
        """
        事実（短い日本語文）を保存。重複は last_seen/counter 更新。
        """
        from time import time as _now
        fact = fact.strip()
        if not fact:
            return
        facts = list(self._data.get("facts", []))
        # 既存に似たものがあれば更新（単純一致）
        for f in facts:
            if isinstance(f, dict) and f.get("text") == fact:
                f["count"] = int(f.get("count", 0)) + 1
                f["last_seen"] = _now()
                self._data["facts"] = facts
                self._save()
                return
        # 新規
        facts.append({"text": fact, "count": 1, "first_seen": _now(), "last_seen": _now()})
        # 上限でトリム
        max_items = int(load_config().get("learning", {}).get("max_facts", 50))
        if len(facts) > max_items:
            # 古いものから間引き
            facts = sorted(facts, key=lambda x: float(x.get("last_seen", 0.0)))[-max_items:]
        self._data["facts"] = facts
        self._save()

    def recent_facts(self, limit: int = 5):
        facts = list(self._data.get("facts", []))
        facts = sorted(facts, key=lambda x: (int(x.get("count", 0)), float(x.get("last_seen", 0.0))), reverse=True)
        return [f for f in facts[:limit] if isinstance(f, dict) and f.get("text")]

    # --- user profile (e.g., name) ---
    def set_user_name(self, name: str) -> None:
        name = name.strip()
        if not name:
            return
        prof = dict(self._data.get("profile", {}))
        prof["name"] = name
        self._data["profile"] = prof
        self._save()

    def get_user_name(self) -> str | None:
        prof = self._data.get("profile", {})
        if isinstance(prof, dict):
            n = prof.get("name")
            if isinstance(n, str) and n.strip():
                return n.strip()
        return None

    def snapshot(self) -> Dict[str, Any]:
        return dict(self._data)


