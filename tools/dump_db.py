from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import List, Tuple


def main() -> None:
    base_dir = Path(__file__).resolve().parent.parent
    db_path = base_dir / "data" / "edo.db"
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]
        print("=== tables ===")
        print(", ".join(tables) if tables else "(none)")
        for t in tables:
            try:
                c = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                print(f"{t}: {c} rows")
            except Exception as e:
                print(f"{t}: error: {e}")
        if "app_settings" in tables:
            print("\n=== app_settings.json (id=1) ===")
            row = con.execute("SELECT json FROM app_settings WHERE id=1").fetchone()
            if row and row[0]:
                try:
                    data = json.loads(row[0])
                except Exception:
                    data = {"_raw": row[0]}
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print("(empty)")
        if "conversation" in tables:
            print("\n=== conversation (last 5) ===")
            for r in con.execute(
                "SELECT id, ts, role, content FROM conversation ORDER BY id DESC LIMIT 5"
            ):
                content = r[3]
                if isinstance(content, str) and len(content) > 160:
                    content = content[:160] + "…"
                print(f"{r[0]} | {r[1]} | {r[2]} | {content}")
        if "sensor_readings" in tables:
            print("\n=== sensor_readings (last 5) ===")
            for r in con.execute(
                """
                SELECT id, ts, source, device_name, temperature, humidity, illuminance, motion, event_time
                FROM sensor_readings
                ORDER BY id DESC LIMIT 5
                """
            ):
                print(r)
    finally:
        con.close()


if __name__ == "__main__":
    main()

