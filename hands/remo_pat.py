from __future__ import annotations

from typing import Any, Dict, List


def list_devices_with_token(token: str, timeout_sec: int = 15) -> List[Dict[str, Any]]:
    """
    Try using 'nature-remo' library if available, otherwise fall back to direct HTTP.
    Returns list of device dicts.
    Raises Exception on failure.
    """
    token = (token or "").strip()
    if not token:
        raise ValueError("Empty token")
    # Try library
    try:
        import nature_remo  # type: ignore
        # The library API varies; try common patterns
        # 1) nature_remo.Cloud(token=...)
        if hasattr(nature_remo, "Cloud"):
            api = nature_remo.Cloud(token=token)
            devices = api.get_devices()
            # normalize to list of dict
            return [d if isinstance(d, dict) else getattr(d, "__dict__", {"id": str(d)}) for d in devices]
        # 2) nature_remo.NatureRemoAPI(token=...)
        if hasattr(nature_remo, "NatureRemoAPI"):
            api = nature_remo.NatureRemoAPI(token=token)  # type: ignore
            devices = api.get_devices()  # type: ignore
            return [d if isinstance(d, dict) else getattr(d, "__dict__", {"id": str(d)}) for d in devices]
    except Exception:
        # fall back to HTTP
        pass
    # Direct HTTP call
    import requests
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    resp = requests.get("https://api.nature.global/1/devices", headers=headers, timeout=timeout_sec)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected response: {data!r}")
    return data


def test_connection_with_pat(token: str) -> str:
    """
    Returns human-readable summary string on success.
    Raises Exception on error.
    """
    devices = list_devices_with_token(token)
    if not devices:
        return "接続成功（デバイス 0 件）"
    names = []
    for d in devices:
        name = ""
        if isinstance(d, dict):
            name = str(d.get("name") or d.get("device", {}).get("name") or d.get("id") or "")
        names.append(name or "(no-name)")
    return f"接続成功（デバイス {len(devices)} 件）: " + ", ".join(names[:5]) + (" ..." if len(names) > 5 else "")


def build_latest_sensor_message(devices: List[Dict[str, Any]]) -> str:
    """
    Build a concise Japanese message summarizing latest sensor values.
    Prefers the first device that has latest_events.
    """
    try:
        from datetime import datetime
    except Exception:
        datetime = None  # type: ignore
    # pick first device that has events (latest_events or newest_events)
    dev = None
    for d in devices or []:
        if isinstance(d, dict):
            ev = d.get("latest_events") or d.get("newest_events")
            if isinstance(ev, dict):
                dev = d
                break
        else:
            # object from nature-remo lib
            try:
                ev = getattr(d, "latest_events", None) or getattr(d, "newest_events", None)
                if isinstance(ev, dict):
                    dev = d
                    break
            except Exception:
                pass
    if not dev:
        return "Remo: センサー情報が見つかりません"
    # name
    def _get_name(obj) -> str:
        if isinstance(obj, dict):
            return str(obj.get("name") or obj.get("device", {}).get("name") or "").strip()
        try:
            nm = getattr(obj, "name", None)
            if nm:
                return str(nm).strip()
        except Exception:
            pass
        return ""
    name = _get_name(dev) or "Remo"
    # events map
    if isinstance(dev, dict):
        ev = dev.get("latest_events") or dev.get("newest_events") or {}
    else:
        ev = getattr(dev, "latest_events", None) or getattr(dev, "newest_events", None) or {}
    te = ev.get("te") or {}
    hu = ev.get("hu") or {}
    il = ev.get("il") or {}
    mo = ev.get("mo") or {}
    def _fmt_time(s: str | None) -> str:
        if not s or not datetime:
            return ""
        try:
            # Normalize ISO string
            ts = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            # convert to JST (+9h)
            try:
                from datetime import timedelta
                dt = dt + timedelta(hours=9)
            except Exception:
                pass
            # Show HH:MM in JST
            return dt.strftime("%H:%M")
        except Exception:
            return ""
    parts: List[str] = []
    try:
        if "val" in te:
            parts.append(f"温度{float(te.get('val')):.1f}℃")
    except Exception:
        pass
    try:
        if "val" in hu:
            v = float(hu.get("val"))
            parts.append(f"湿度{int(round(v))}%")
    except Exception:
        pass
    try:
        if "val" in il:
            v = float(il.get("val"))
            parts.append(f"照度{int(round(v))}lx")
    except Exception:
        pass
    try:
        if "val" in mo:
            v = int(mo.get("val"))
            parts.append(f"人感{'あり' if v else 'なし'}")
    except Exception:
        pass
    t_candidates = [te.get("created_at"), hu.get("created_at"), il.get("created_at"), mo.get("created_at")]
    t_candidates = [t for t in t_candidates if isinstance(t, str) and t]
    t_label = ""
    for t in t_candidates:
        t_label = _fmt_time(t)
        if t_label:
            break
    tail = f"（{t_label}）" if t_label else ""
    body = " ".join(parts) if parts else "センサー値なし"
    return f"{name}: {body}{tail}"


def describe_devices(token: str) -> str:
    """
    Build a multi-line description of devices and available latest events.
    Example:
      Living: 温度✓ 湿度✓ 照度✓ 人感×
    """
    devices = list_devices_with_token(token)
    if not devices:
        return "デバイスが見つかりません。"
    lines: List[str] = []
    for d in devices:
        # name
        if isinstance(d, dict):
            name = str(d.get("name") or d.get("device", {}).get("name") or d.get("id") or "Remo")
            ev = d.get("latest_events") or d.get("newest_events") or {}
        else:
            try:
                name = str(getattr(d, "name", None) or getattr(d, "id", None) or "Remo")
            except Exception:
                name = "Remo"
            ev = getattr(d, "latest_events", None) or getattr(d, "newest_events", None) or {}
        def has(k: str) -> bool:
            try:
                return isinstance(ev.get(k, {}), dict) and ("val" in ev.get(k, {}))
            except Exception:
                return False
        flags = [
            f"温度{'✓' if has('te') else '×'}",
            f"湿度{'✓' if has('hu') else '×'}",
            f"照度{'✓' if has('il') else '×'}",
            f"人感{'✓' if has('mo') else '×'}",
        ]
        lines.append(f"{name}: " + " ".join(flags))
    return "\n".join(lines)
