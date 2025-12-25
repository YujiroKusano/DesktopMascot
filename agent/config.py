from __future__ import annotations

import json
import os
import logging
from pathlib import Path
try:
    import sqlite3  # type: ignore
    _HAS_SQLITE = True
except Exception:
    sqlite3 = None  # type: ignore
    _HAS_SQLITE = False
from typing import Any, Dict

_CFG_CACHE: Dict[str, Any] | None = None


def _default_config() -> Dict[str, Any]:
    return {
        "mascot": {
            "name": "エド",
            "icon_size_px": 160,
            "timer_ms": 33,
            "base_speed_px": 0.6,
            # 素材検索のルートディレクトリ（相対/絶対パス）。空なら material/ を自動探索
            "sprite_dir": "",
            "move_interval_ms": [2000, 4000],
            "stop_probability": 0.30,
            "stop_interval_ms": [1200, 2000],
            "sleep_idle_sec": 60,
            "sleep_min_duration_sec": 15,
            "speed_eps": 0.02,
            "anim_interval_ms": {"idle": 800, "walk": 120, "sleep": 1000, "float": 140},
            # 速度を目標値へ滑らかに近づける係数（0..1）
            "velocity_smooth_alpha": 0.12,
            # walk -> idle へ切り替えるまでの最小無動作保持時間（ms）
            "idle_hold_ms": 250,
            # スプライトを共通キャンバスに収める（揺れ防止・底辺合わせ）
            "sprite_canvas_w_px": 160,
            "sprite_canvas_h_px": 160,
            # 透過余白を自動トリミングする際のアルファ閾値（0..255）
            "sprite_trim_alpha_threshold": 8,
            # スケール基準: "height" か "width"
            "sprite_scale_basis": "height",
            # 向きの反転にヒステリシスを導入（小刻みな符号反転を防ぐ）
            "orientation_flip_threshold_px": 0.08,
            "orientation_hold_ms": 250
        },
        "profile": {
            # ユーザー名（空なら未設定）
            "user_name": ""
        },
        "talk": {
            "enabled": True,
            # 右下にチャット画面を表示（吹き出しは使わない）
            "chat_mode": False,
            # チャットパネルの既定サイズ
            "chat_panel_width_px": 320,
            "chat_panel_height_px": 280,
            # 吹き出し表示中に停止するか（false で追従しながら歩く）
            "freeze_while_bubble": False,
            # 吹き出しの表示時間（文字数に応じて加算）
            "bubble_time_base_ms": 2000,
            "bubble_time_per_char_ms": 30,
            "bubble_time_max_ms": 15000,
            # 自発トークで学習済み事実を混ぜる確率（0.0〜1.0）。既定は無効
            "auto_talk_facts_rate": 0.0,
            # 不明時の既定応答
            "unknown_reply": "ごめん、今はわからないよ。",
            # 自発トークの間隔（秒）
            "auto_talk_min_sec": 30,
            "auto_talk_max_sec": 120,
            # なで判定のしきい値（短時間にどれだけカーソルが動けば「なで」とみなすか）
            "petting_threshold_px": 120.0,
            "petting_window_sec": 1.2,
            # デフォルトメッセージ（上書き可能）
            "messages": [
                "にゃーん",
                "おつかれさま",
                "今日もがんばってるね",
                "少し休憩しよ？",
            ],
        },
        "net": {
            "answer_max_chars": 220,
            "answer_timeout_ms": 45000,
            "answer_max_wait_ms": 180000
        },
        "safety": {
            "banned_keywords": [
                "違法",
                "ハッキング",
                "個人情報",
                "テロ",
                "暴力"
            ]
        },
        "memory": {
            "path": str(Path(__file__).resolve().parent.parent / "data" / "memory.json"),
            "max_history": 20
        },
        "learning": {
            "enabled": True,
            "max_facts": 50,
            "summarize_enabled": True,
            "max_summary_chars": 800
        },
        "context": {
            "include_time": False,
            "include_location": True,
            "location_text": ""  # 例: "東京都渋谷区"（空なら無視）
        }
    }


def _llm_default() -> Dict[str, Any]:
    return {
        # LM Studio の OpenAI 互換エンドポイント想定
        "enabled": False,
        "base_url": "http://localhost:1234/v1",
        "api_key": "",
        "model": "gpt-oss-20b",
        "temperature": 0.7,
        "max_tokens": 256,
        "context_turns": 10,
        "system_prompt": "あなたはデスクトップの猫アシスタント『エド』です。常に日本語で、簡潔かつ親切に答えてください。"
    }



def _resolve_config_path() -> Path:
    # 環境変数があれば最優先
    env_path = os.environ.get("EDO_CONFIG")
    if env_path:
        return Path(env_path)
    # 既定 config/mascot.json
    base_dir = Path(__file__).resolve().parent.parent
    return base_dir / "config" / "mascot.json"

def _resolve_db_path() -> Path:
    base_dir = Path(__file__).resolve().parent.parent
    return base_dir / "data" / "edo.db"

def _db_available() -> bool:
    return bool(_HAS_SQLITE)

def _db_load_config() -> Dict[str, Any] | None:
    try:
        db_path = _resolve_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db_path))
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS app_settings (id INTEGER PRIMARY KEY CHECK (id=1), json TEXT NOT NULL)"
            )
            cur = con.execute("SELECT json FROM app_settings WHERE id=1")
            row = cur.fetchone()
            if not row or not row[0]:
                return None
            data = json.loads(row[0])
            return data if isinstance(data, dict) else {}
        finally:
            con.close()
    except Exception:
        logging.exception("Failed to load settings from DB")
        return None

def _db_save_config(cfg: Dict[str, Any]) -> bool:
    try:
        db_path = _resolve_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db_path))
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS app_settings (id INTEGER PRIMARY KEY CHECK (id=1), json TEXT NOT NULL)"
            )
            js = json.dumps(cfg, ensure_ascii=False, indent=2)
            con.execute("INSERT OR REPLACE INTO app_settings(id, json) VALUES(1, ?)", (js,))
            con.commit()
            return True
        finally:
            con.close()
    except Exception:
        logging.exception("Failed to save settings to DB")
        return False


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)  # type: ignore[index]
        else:
            result[k] = v
    return result

def _set_by_path(root: Dict[str, Any], path: str, value: Any) -> None:
    cur: Any = root
    parts = [p for p in str(path).split(".") if p]
    if not parts:
        return
    for key in parts[:-1]:
        if key not in cur or not isinstance(cur.get(key), dict):
            cur[key] = {}
        cur = cur[key]  # type: ignore[index]
    cur[parts[-1]] = value  # type: ignore[index]

def _apply_ui_field_values(cfg: Dict[str, Any]) -> None:
    """
    ui.tabs[].fields[].value を path に反映する。
    - UI定義に value があれば、実値として上書き
    - 型変換は行わず JSON 値をそのまま使用
    """
    try:
        ui = cfg.get("ui", {})
        tabs = ui.get("tabs", [])
        if not isinstance(tabs, list):
            return
        for tab in tabs:
            fields = tab.get("fields", [])
            if not isinstance(fields, list):
                continue
            for f in fields:
                if not isinstance(f, dict):
                    continue
                path = f.get("path")
                has_value = "value" in f
                if isinstance(path, str) and has_value:
                    _set_by_path(cfg, path, f.get("value", None))
    except Exception:
        # UI定義が不正でもローダは失敗させないが、原因は記録する
        logging.exception("Failed to apply UI field values to config")


def load_config(force_reload: bool = False) -> Dict[str, Any]:
    """
    設定は常に SQLite（data/edo.db）の app_settings から読み込みます。
    - レコードが無い場合はデフォルトを作成して DB に保存します。
    - JSON ファイルは一切使用しません。
    """
    global _CFG_CACHE
    if _CFG_CACHE is not None and not force_reload:
        return _CFG_CACHE

    if not _db_available():
        raise RuntimeError("sqlite3 が使用できません。設定はDB専用です。Python の sqlite3 を有効にしてください。")

    db_cfg = _db_load_config()
    if isinstance(db_cfg, dict) and db_cfg:
        cfg = db_cfg
    else:
        # DB 初期化（デフォルトを保存）
        cfg = _default_config()
        cfg["llm"] = _llm_default()
        ok = _db_save_config(cfg)
        if not ok:
            raise RuntimeError("設定の初期保存に失敗しました（DB）。")
    # UI 定義の value を反映（存在する場合のみ無害に反映）
    _apply_ui_field_values(cfg)
    _CFG_CACHE = cfg
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    """
    設定は常に SQLite に保存します。JSON には保存しません。
    """
    if not _db_available():
        raise RuntimeError("sqlite3 が使用できません。設定はDB専用です。Python の sqlite3 を有効にしてください。")
    ok = _db_save_config(cfg)
    if not ok:
        raise RuntimeError("設定の保存に失敗しました（DB）。")
    global _CFG_CACHE
    _CFG_CACHE = cfg

