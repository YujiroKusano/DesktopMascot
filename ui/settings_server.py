from __future__ import annotations

import html
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, Any, Tuple

from agent.config import load_config, save_config


def _flatten(d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (d or {}).items():
        if not isinstance(k, str):
            continue
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, path))
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[path] = v
        # lists and others are skipped for simplicity
    return out


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


def _html_escape(s: str) -> str:
    return html.escape(s, quote=True)

def _render_checkbox(name: str, label: str, checked: bool) -> str:
    return f'<label><input type="checkbox" name="{_html_escape(name)}" value="on" {"checked" if checked else ""}> {_html_escape(label)}</label>'

def _render_text(name: str, label: str, value: str, placeholder: str = "") -> str:
    v = _html_escape(value or "")
    n = _html_escape(name)
    l = _html_escape(label)
    ph = f' placeholder="{_html_escape(placeholder)}"' if placeholder else ""
    return f'<label>{l}<br><input type="text" name="{n}" value="{v}"{ph} class="text"></label>'

def _render_password(name: str, label: str, value: str, placeholder: str = "") -> str:
    v = _html_escape(value or "")
    n = _html_escape(name)
    l = _html_escape(label)
    ph = f' placeholder="{_html_escape(placeholder)}"' if placeholder else ""
    return f'<label>{l}<br><input type="password" name="{n}" value="{v}"{ph} class="text"></label>'

def _render_textarea(name: str, label: str, value: str, placeholder: str = "") -> str:
    v = _html_escape(value or "")
    n = _html_escape(name)
    l = _html_escape(label)
    ph = f' placeholder="{_html_escape(placeholder)}"' if placeholder else ""
    return f'<label>{l}<br><textarea name="{n}" rows="5" class="text"{ph} style="height:auto"></textarea>'.replace("</textarea>", f"{v}</textarea>")

def _page(title: str, inner_html: str) -> str:
    head = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{_html_escape(title)}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Hiragino Kaku Gothic ProN',Meiryo,sans-serif;margin:18px;background:#f7f7f7;color:#222;}}
.container{{max-width:960px;margin:0 auto;}}
.nav{{display:flex;gap:10px;margin:0 0 12px 0}}
.nav a{{text-decoration:none;color:#1976d2}}
.card{{background:#fff;border:1px solid #ddd;border-radius:10px;padding:14px;margin:12px 0}}
fieldset{{margin:14px 0;padding:14px;border:1px solid #ddd;border-radius:10px;background:#fff;}}
legend{{font-weight:700;padding:0 .4em;}}
label{{display:block;margin:8px 0;}}
.row{{display:flex;gap:12px;flex-wrap:wrap}}
.col{{flex:1;min-width:240px;}}
.text{{width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;background:#fff;color:#222;}}
.help{{color:#666;font-size:12px;margin-top:4px}}
.header{{display:flex;align-items:center;gap:12px;justify-content:space-between}}
.actions{{margin-top:12px}}
button{{padding:8px 14px;border:0;border-radius:8px;background:#1976d2;color:#fff;cursor:pointer}}
button:hover{{background:#135da6}}
.note{{color:#666;font-size:12px}}
.kvs{{display:grid;grid-template-columns: 220px 1fr; gap:6px 12px;}}
.kvs .k{{color:#666}}
.linkbtn a{{display:inline-block;padding:8px 12px;border:1px solid #1976d2;border-radius:8px;color:#1976d2;text-decoration:none}}
.linkbtn a:hover{{background:#e6f0fb}}
.tabbar button{{background:transparent;border:none;padding:8px 10px;color:#222;cursor:pointer;border-bottom:2px solid transparent;border-radius:6px 6px 0 0}}
.tabbar button:hover{{background:#f0f6ff}}
</style>
</head><body><div class="container">
<div class="nav"><a href="/">Home</a> / <a href="/settings">Settings</a> / <a href="/status">Status</a></div>
"""
    tail = "</div></body></html>"
    return head + inner_html + tail

def _masked_cfg_view(cfg: Dict[str, Any]) -> Dict[str, Any]:
    # 簡易マスキング（tokens/secrets系）
    def mask_value(k: str, v: Any) -> Any:
        key = k.lower()
        if any(t in key for t in ("token", "secret", "api_key", "pat")) and isinstance(v, str) and v:
            if len(v) <= 6:
                return "****"
            return v[:2] + "****" + v[-2:]
        return v
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: walk(mask_value(k, v)) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node
    return walk(cfg)  # type: ignore[return-value]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:
        # keep quiet (we use app logger elsewhere if needed)
        return

    def _respond(self, code: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
        body_bytes = body.encode("utf-8", errors="replace")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        try:
            self.wfile.write(body_bytes)
            self.wfile.flush()
        except Exception:
            pass

    def do_GET(self):
        try:
            if self.path in ("/", "/index"):
                self._handle_home()
                return
            if self.path.startswith("/settings"):
                self._handle_settings()
                return
            if self.path.startswith("/status"):
                self._handle_status()
                return
            self._respond(404, "<h1>404 Not Found</h1>")
        except Exception as ex:
            self._respond(500, f"<h1>500</h1><pre>{_html_escape(str(ex))}</pre>")

    def do_POST(self):
        try:
            if self.path.startswith("/apply"):
                self._handle_apply()
                return
            self._respond(404, "<h1>404 Not Found</h1>")
        except Exception as ex:
            self._respond(500, f"<h1>500</h1><pre>{_html_escape(str(ex))}</pre>")

    def _handle_home(self) -> None:
        cfg = load_config()
        ic = cfg.get("integrations", {}) or {}
        rc = ic.get("remo", {}) or {}
        sc = ic.get("switchbot", {}) or {}
        kvs = []
        kvs.append(f'<div class="k">センサー取得間隔</div><div>{_html_escape(str(ic.get("poll_interval_min", 5)))} 分</div>')
        kvs.append(f'<div class="k">Nature Remo</div><div>{"有効" if rc.get("enabled") else "無効"} / {"喋る" if rc.get("announce", True) else "喋らない"}</div>')
        kvs.append(f'<div class="k">SwitchBot</div><div>{"有効" if sc.get("enabled") else "無効"} / {"喋る" if sc.get("announce") else "喋らない"}</div>')
        body = []
        body.append('<div class="card"><div class="header"><h2>ホーム</h2></div>')
        body.append('<p>デスクトップ猫「エド」の各種設定・状態を確認できます。</p>')
        body.append('<div class="kvs">' + "".join(kvs) + "</div>")
        body.append('<div class="actions linkbtn" style="margin-top:14px">')
        body.append('<a href="/settings">設定を開く</a> ')
        body.append('<a href="/status">状態を見る</a>')
        body.append("</div></div>")
        html = _page("Home", "".join(body))
        self._respond(200, html)

    def _handle_settings(self) -> None:
        cfg = load_config()
        ic = cfg.get("integrations", {}) or {}
        rc = ic.get("remo", {}) or {}
        sc = ic.get("switchbot", {}) or {}
        poll = str(ic.get("poll_interval_min", 5))

        # build sections
        # basic
        p = cfg.get("profile", {}) or {}
        cx = cfg.get("context", {}) or {}
        n = cfg.get("net", {}) or {}
        basic = []
        basic.append("<fieldset><legend>共通</legend>")
        basic.append(_render_text("integrations.poll_interval_min", "センサー取得間隔（分）", poll, "例: 5"))
        basic.append('<div class="help">1〜120の範囲で設定</div>')
        basic.append("</fieldset>")
        basic.append("<fieldset><legend>プロフィール</legend>")
        basic.append(_render_text("profile.user_name", "ユーザー名", str(p.get("user_name", "")), "あなたの名前"))
        basic.append("</fieldset>")
        basic.append("<fieldset><legend>コンテキスト</legend>")
        basic.append(_render_checkbox("context.include_time", "時刻をコンテキストに含める", bool(cx.get("include_time", False))))
        basic.append(_render_checkbox("context.include_location", "場所をコンテキストに含める", bool(cx.get("include_location", True))))
        basic.append(_render_text("context.location_text", "場所のテキスト", str(cx.get("location_text", "")), "例: 東京都渋谷区"))
        basic.append("</fieldset>")
        basic.append("<fieldset><legend>応答</legend>")
        basic.append(_render_text("net.answer_max_chars", "応答文字数上限", str(n.get("answer_max_chars", 220))))
        basic.append(_render_text("net.answer_timeout_ms", "応答タイムアウト(ms)", str(n.get("answer_timeout_ms", 45000))))
        basic.append(_render_text("net.answer_max_wait_ms", "応答最大待機(ms)", str(n.get("answer_max_wait_ms", 180000))))
        basic.append("</fieldset>")
        # mascot
        m = cfg.get("mascot", {}) or {}
        mascot = []
        mascot.append("<fieldset><legend>マスコット</legend>")
        mascot.append(_render_text("mascot.icon_size_px", "アイコンサイズ(px)", str(m.get("icon_size_px", 160)), "例: 160"))
        mascot.append(_render_text("mascot.timer_ms", "更新間隔(ms)", str(m.get("timer_ms", 33)), "例: 33"))
        mascot.append(_render_text("mascot.base_speed_px", "基準速度(px/tick)", str(m.get("base_speed_px", 0.6)), "例: 0.6"))
        mascot.append(_render_text("mascot.sprite_dir", "スプライトフォルダ（任意）", str(m.get("sprite_dir", "")), "material/move_cat など"))
        mascot.append("</fieldset>")
        # talk
        t = cfg.get("talk", {}) or {}
        talk = []
        talk.append("<fieldset><legend>会話・表示</legend>")
        talk.append(_render_checkbox("talk.enabled", "会話機能を有効化", bool(t.get("enabled", True))))
        talk.append(_render_checkbox("talk.chat_mode", "右下にチャットパネルを表示", bool(t.get("chat_mode", False))))
        talk.append(_render_checkbox("talk.freeze_while_bubble", "吹き出し表示中は停止する", bool(t.get("freeze_while_bubble", False))))
        talk.append('<div class="row"><div class="col">')
        talk.append(_render_text("talk.bubble_time_base_ms", "吹き出し基本時間(ms)", str(t.get("bubble_time_base_ms", 2000))))
        talk.append(_render_text("talk.bubble_time_per_char_ms", "文字ごとの加算(ms)", str(t.get("bubble_time_per_char_ms", 30))))
        talk.append('</div><div class="col">')
        talk.append(_render_text("talk.bubble_time_max_ms", "吹き出し最大(ms)", str(t.get("bubble_time_max_ms", 15000))))
        talk.append(_render_text("talk.petting_threshold_px", "なで判定しきい値(px)", str(t.get("petting_threshold_px", 120.0))))
        talk.append('</div></div>')
        talk.append(_render_text("talk.auto_talk_min_sec", "自発トーク間隔(最小,秒)", str(t.get("auto_talk_min_sec", 30))))
        talk.append(_render_text("talk.auto_talk_max_sec", "自発トーク間隔(最大,秒)", str(t.get("auto_talk_max_sec", 120))))
        talk.append("</fieldset>")
        # llm
        llm = cfg.get("llm", {}) or {}
        llmsec = []
        llmsec.append("<fieldset><legend>LLM</legend>")
        llmsec.append(_render_checkbox("llm.enabled", "LLM を有効化", bool(llm.get("enabled", False))))
        llmsec.append(_render_text("llm.base_url", "Base URL", str(llm.get("base_url", "http://localhost:1234/v1")), "例: http://localhost:1234/v1"))
        llmsec.append(_render_password("llm.api_key", "API Key（必要に応じて）", str(llm.get("api_key", ""))))
        llmsec.append(_render_text("llm.model", "モデル名", str(llm.get("model", "gpt-oss-20b"))))
        llmsec.append(_render_text("llm.temperature", "温度", str(llm.get("temperature", 0.7))))
        llmsec.append(_render_text("llm.max_tokens", "最大トークン", str(llm.get("max_tokens", 256))))
        llmsec.append(_render_text("llm.context_turns", "コンテキストとして渡すターン数", str(llm.get("context_turns", 10))))
        llmsec.append(_render_textarea("llm.system_prompt", "システムプロンプト", str(llm.get("system_prompt", ""))))
        llmsec.append('<div class="help">APIキーやトークンはDBに平文で保存されます。ご注意ください。</div>')
        llmsec.append("</fieldset>")
        # iot
        iot = []
        iot.append("<fieldset><legend>Nature Remo</legend>")
        iot.append(_render_checkbox("integrations.remo.enabled", "Remo 連携を有効化", bool(rc.get("enabled", False))))
        iot.append(_render_checkbox("integrations.remo.announce", "最新センサーを5分ごとに喋る", bool(rc.get("announce", True))))
        iot.append(_render_text("integrations.remo.device_name_filter", "対象デバイス名（部分一致/正規表現）", rc.get("device_name_filter", ""), "例: Living|Bedroom"))
        iot.append('<div class="row"><div class="col">')
        iot.append(_render_checkbox("integrations.remo.announce_temperature", "温度を含める", bool(rc.get("announce_temperature", True))))
        iot.append(_render_checkbox("integrations.remo.announce_humidity", "湿度を含める", bool(rc.get("announce_humidity", True))))
        iot.append('</div><div class="col">')
        iot.append(_render_checkbox("integrations.remo.announce_illuminance", "照度を含める", bool(rc.get("announce_illuminance", True))))
        iot.append(_render_checkbox("integrations.remo.announce_motion", "人感を含める", bool(rc.get("announce_motion", True))))
        iot.append("</div></div>")
        iot.append(_render_password("integrations.remo.pat_token", "Personal Access Token（PAT）", rc.get("pat_token", ""), "コピー＆貼り付け"))
        iot.append("</fieldset>")
        iot.append("<fieldset><legend>SwitchBot</legend>")
        iot.append(_render_checkbox("integrations.switchbot.enabled", "SwitchBot 連携を有効化", bool(sc.get("enabled", False))))
        iot.append(_render_checkbox("integrations.switchbot.announce", "最新センサーを5分ごとに喋る", bool(sc.get("announce", False))))
        iot.append(_render_text("integrations.switchbot.device_name_filter", "対象デバイス名（部分一致/正規表現）", sc.get("device_name_filter", ""), "例: Meter|Motion"))
        iot.append('<div class="row"><div class="col">')
        iot.append(_render_checkbox("integrations.switchbot.announce_temperature", "温度を含める", bool(sc.get("announce_temperature", True))))
        iot.append(_render_checkbox("integrations.switchbot.announce_humidity", "湿度を含める", bool(sc.get("announce_humidity", True))))
        iot.append('</div><div class="col">')
        iot.append(_render_checkbox("integrations.switchbot.announce_illuminance", "照度を含める", bool(sc.get("announce_illuminance", False))))
        iot.append(_render_checkbox("integrations.switchbot.announce_motion", "人感を含める", bool(sc.get("announce_motion", True))))
        iot.append("</div></div>")
        iot.append(_render_text("integrations.switchbot.base_url", "Base URL", sc.get("base_url", "https://api.switch-bot.com"), "通常は既定のまま"))
        iot.append(_render_password("integrations.switchbot.token", "Token", sc.get("token", ""), "SwitchBot App で取得"))
        iot.append(_render_password("integrations.switchbot.secret", "Secret", sc.get("secret", ""), "SwitchBot App で取得"))
        iot.append("</fieldset>")
        # advanced
        lg = cfg.get("learning", {}) or {}
        sf = cfg.get("safety", {}) or {}
        banned = "\n".join(sf.get("banned_keywords", []) or [])
        adv = []
        adv.append("<fieldset><legend>学習</legend>")
        adv.append(_render_checkbox("learning.enabled", "学習を有効化", bool(lg.get("enabled", True))))
        adv.append(_render_checkbox("learning.summarize_enabled", "要約を有効化", bool(lg.get("summarize_enabled", True))))
        adv.append(_render_text("learning.max_facts", "保存する事実の最大数", str(lg.get("max_facts", 50))))
        adv.append(_render_text("learning.max_summary_chars", "要約の最大文字数", str(lg.get("max_summary_chars", 800))))
        adv.append("</fieldset>")
        adv.append("<fieldset><legend>セーフティ</legend>")
        adv.append(_render_textarea("safety.banned_keywords", "禁止キーワード（1行に1つ）", banned))
        adv.append("</fieldset>")

        tab_html = """
<div class="card">
  <div class="header"><h2>設定（ローカル）</h2><div><a href="/settings">更新</a></div></div>
  <div class="tabbar" style="display:flex;gap:8px;border-bottom:1px solid #ddd;margin-bottom:6px">
    <button type="button" data-tab="basic">🧰 一般</button>
    <button type="button" data-tab="mascot">🐾 マスコット</button>
    <button type="button" data-tab="talk">💬 会話/表示</button>
    <button type="button" data-tab="llm">🤖 AI</button>
    <button type="button" data-tab="iot">🔗 連携</button>
    <button type="button" data-tab="adv">🛡️ 学習/安全</button>
  </div>
  <div class="tabdesc" style="color:#555;font-size:12px;margin:0 0 10px 4px"></div>
  <form method="POST" action="/apply">
    <div class="tab-section" data-tab="basic"></div>
    <div class="tab-section" data-tab="mascot" style="display:none"></div>
    <div class="tab-section" data-tab="talk" style="display:none"></div>
    <div class="tab-section" data-tab="llm" style="display:none"></div>
    <div class="tab-section" data-tab="iot" style="display:none"></div>
    <div class="tab-section" data-tab="adv" style="display:none"></div>
    <div class="actions"><button type="submit">保存</button> <span class="note">保存すると即時DBへ反映されます。</span></div>
  </form>
</div>
<script>
(function(){
  var desc = {
    basic: "概要・ユーザー・コンテキスト・応答の共通設定",
    mascot: "見た目や移動など、マスコット本体の設定",
    talk: "吹き出し・自発トーク・チャット表示など会話関連",
    llm: "AIの接続先やモデル、プロンプトなどの設定",
    iot: "Nature Remo / SwitchBot などの連携設定",
    adv: "学習の保持量や安全性（禁止ワード）"
  };
  function setActive(name){
    document.querySelectorAll('.tab-section').forEach(function(el){
      el.style.display = (el.getAttribute('data-tab')===name)?'':'none';
    });
    document.querySelectorAll('.tabbar button').forEach(function(btn){
      var on = btn.getAttribute('data-tab')===name;
      btn.style.borderBottom = on ? '2px solid #1976d2' : '2px solid transparent';
      btn.style.background = on ? '#e6f0fb' : 'transparent';
    });
    localStorage.setItem('edo_tab', name);
    var d = document.querySelector('.tabdesc');
    if(d){ d.textContent = desc[name] || ""; }
  }
  var last = localStorage.getItem('edo_tab') || 'basic';
  setActive(last);
  document.querySelectorAll('.tabbar button').forEach(function(btn){
    btn.addEventListener('click', function(){ setActive(btn.getAttribute('data-tab')); });
  });
  var sections = {
    basic: `%BASIC%`,
    mascot: `%MASCOT%`,
    talk: `%TALK%`,
    llm: `%LLM%`,
    iot: `%IOT%`,
    adv: `%ADV%`
  };
  Object.keys(sections).forEach(function(k){
    var el = document.querySelector('.tab-section[data-tab=\"'+k+'\"]');
    if(el){ el.innerHTML = sections[k]; }
  });
})();
</script>
"""
        html = _page("Settings", tab_html.replace("%BASIC%", "".join(basic))
                                         .replace("%MASCOT%", "".join(mascot))
                                         .replace("%TALK%", "".join(talk))
                                         .replace("%LLM%", "".join(llmsec))
                                         .replace("%IOT%", "".join(iot))
                                         .replace("%ADV%", "".join(adv)))
        self._respond(200, html)

    def _handle_status(self) -> None:
        cfg = load_config()
        masked = _masked_cfg_view(cfg)
        import json as _json
        s = _html_escape(_json.dumps(masked, ensure_ascii=False, indent=2))
        inner = []
        inner.append('<div class="card"><div class="header"><h2>状態</h2></div>')
        inner.append('<p class="note">機微情報は一部マスク表示しています。</p>')
        inner.append(f"<pre style='white-space:pre-wrap;background:#fafafa;border:1px solid #eee;padding:12px;border-radius:8px'>{s}</pre>")
        inner.append("</div>")
        html = _page("Status", "".join(inner))
        self._respond(200, html)

    def _handle_apply(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else b""
        from urllib.parse import parse_qs
        form = parse_qs(body.decode("utf-8", errors="ignore"), keep_blank_values=True)
        def get(name: str) -> str:
            return (form.get(name) or [""])[0]
        def getb(name: str) -> bool:
            # checkbox present => "on"
            return name in form

        cfg = load_config()
        # small helpers
        def to_int(s: str, default: int) -> int:
            try:
                return int(str(s).strip())
            except Exception:
                return default
        def to_float(s: str, default: float) -> float:
            try:
                return float(str(s).strip())
            except Exception:
                return default

        # マスコット
        m = cfg.setdefault("mascot", {})
        m["icon_size_px"] = to_int(get("mascot.icon_size_px"), int(m.get("icon_size_px", 160)))
        m["timer_ms"] = to_int(get("mascot.timer_ms"), int(m.get("timer_ms", 33)))
        m["base_speed_px"] = to_float(get("mascot.base_speed_px"), float(m.get("base_speed_px", 0.6)))
        m["sprite_dir"] = get("mascot.sprite_dir")
        # 会話・表示
        t = cfg.setdefault("talk", {})
        t["enabled"] = getb("talk.enabled")
        t["chat_mode"] = getb("talk.chat_mode")
        t["freeze_while_bubble"] = getb("talk.freeze_while_bubble")
        t["bubble_time_base_ms"] = to_int(get("talk.bubble_time_base_ms"), int(t.get("bubble_time_base_ms", 2000)))
        t["bubble_time_per_char_ms"] = to_int(get("talk.bubble_time_per_char_ms"), int(t.get("bubble_time_per_char_ms", 30)))
        t["bubble_time_max_ms"] = to_int(get("talk.bubble_time_max_ms"), int(t.get("bubble_time_max_ms", 15000)))
        t["petting_threshold_px"] = to_float(get("talk.petting_threshold_px"), float(t.get("petting_threshold_px", 120.0)))
        t["auto_talk_min_sec"] = to_int(get("talk.auto_talk_min_sec"), int(t.get("auto_talk_min_sec", 30)))
        t["auto_talk_max_sec"] = to_int(get("talk.auto_talk_max_sec"), int(t.get("auto_talk_max_sec", 120)))
        ic = cfg.setdefault("integrations", {})
        # common
        try:
            poll = int((get("integrations.poll_interval_min") or "5").strip())
            ic["poll_interval_min"] = max(1, min(120, poll))
        except Exception:
            pass
        # プロファイル
        prof = cfg.setdefault("profile", {})
        prof["user_name"] = get("profile.user_name")
        # コンテキスト
        cx = cfg.setdefault("context", {})
        cx["include_time"] = getb("context.include_time")
        cx["include_location"] = getb("context.include_location")
        cx["location_text"] = get("context.location_text")
        # 応答
        nn = cfg.setdefault("net", {})
        nn["answer_max_chars"] = to_int(get("net.answer_max_chars"), int(nn.get("answer_max_chars", 220)))
        nn["answer_timeout_ms"] = to_int(get("net.answer_timeout_ms"), int(nn.get("answer_timeout_ms", 45000)))
        nn["answer_max_wait_ms"] = to_int(get("net.answer_max_wait_ms"), int(nn.get("answer_max_wait_ms", 180000)))
        # 学習
        lg = cfg.setdefault("learning", {})
        lg["enabled"] = getb("learning.enabled")
        lg["summarize_enabled"] = getb("learning.summarize_enabled")
        lg["max_facts"] = to_int(get("learning.max_facts"), int(lg.get("max_facts", 50)))
        lg["max_summary_chars"] = to_int(get("learning.max_summary_chars"), int(lg.get("max_summary_chars", 800)))
        # セーフティ
        sf = cfg.setdefault("safety", {})
        banned_raw = get("safety.banned_keywords")
        sf["banned_keywords"] = [s for s in (banned_raw or "").splitlines() if s.strip()]
        # LLM
        llm = cfg.setdefault("llm", {})
        llm["enabled"] = getb("llm.enabled")
        llm["base_url"] = get("llm.base_url").strip() or llm.get("base_url", "")
        if get("llm.api_key").strip():
            llm["api_key"] = get("llm.api_key").strip()
        llm["model"] = get("llm.model").strip() or llm.get("model", "")
        llm["temperature"] = to_float(get("llm.temperature"), float(llm.get("temperature", 0.7)))
        llm["max_tokens"] = to_int(get("llm.max_tokens"), int(llm.get("max_tokens", 256)))
        llm["context_turns"] = to_int(get("llm.context_turns"), int(llm.get("context_turns", 10)))
        if get("llm.system_prompt"):
            llm["system_prompt"] = get("llm.system_prompt")
        # Remo
        rc = ic.setdefault("remo", {})
        rc["enabled"] = getb("integrations.remo.enabled")
        rc["announce"] = getb("integrations.remo.announce")
        rc["device_name_filter"] = get("integrations.remo.device_name_filter")
        rc["announce_temperature"] = getb("integrations.remo.announce_temperature")
        rc["announce_humidity"] = getb("integrations.remo.announce_humidity")
        rc["announce_illuminance"] = getb("integrations.remo.announce_illuminance")
        rc["announce_motion"] = getb("integrations.remo.announce_motion")
        pat = get("integrations.remo.pat_token")
        if pat.strip():
            rc["pat_token"] = pat.strip()
        # SwitchBot
        sc = ic.setdefault("switchbot", {})
        sc["enabled"] = getb("integrations.switchbot.enabled")
        sc["announce"] = getb("integrations.switchbot.announce")
        sc["device_name_filter"] = get("integrations.switchbot.device_name_filter")
        sc["announce_temperature"] = getb("integrations.switchbot.announce_temperature")
        sc["announce_humidity"] = getb("integrations.switchbot.announce_humidity")
        sc["announce_illuminance"] = getb("integrations.switchbot.announce_illuminance")
        sc["announce_motion"] = getb("integrations.switchbot.announce_motion")
        base_url = get("integrations.switchbot.base_url").strip() or "https://api.switch-bot.com"
        sc["base_url"] = base_url
        tok = get("integrations.switchbot.token")
        sec = get("integrations.switchbot.secret")
        if tok.strip():
            sc["token"] = tok.strip()
        if sec.strip():
            sc["secret"] = sec.strip()

        save_config(cfg)
        self.send_response(303)
        self.send_header("Location", "/settings")
        self.end_headers()


class LocalSettingsServer:
    def __init__(self, port: int = 8766) -> None:
        self._port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            return
        port = self._port
        server = None
        # Try preferred port, then an ephemeral one.
        for p in (port, 0):
            try:
                server = HTTPServer(("127.0.0.1", p), _Handler)
                break
            except OSError:
                continue
        if server is None:
            raise OSError("Failed to bind settings server")
        self._server = server
        self._port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        self._thread = t

    def stop(self) -> None:
        try:
            if self._server:
                self._server.shutdown()
                self._server.server_close()
        finally:
            self._server = None
            self._thread = None

    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/settings"


_SERVER_SINGLETON: LocalSettingsServer | None = None


def get_or_start(port: int = 8766) -> LocalSettingsServer:
    global _SERVER_SINGLETON
    if _SERVER_SINGLETON is None:
        srv = LocalSettingsServer(port)
        srv.start()
        _SERVER_SINGLETON = srv
    return _SERVER_SINGLETON

