from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from typing import Any, Dict, Optional

import requests


class SwitchBotClient:
    """
    Minimal client for SwitchBot Cloud API (token/secret/HMAC auth).
    Docs: https://github.com/OpenWonderLabs/SwitchBotAPI
    """

    def __init__(self, token: str, secret: str, base_url: str = "https://api.switch-bot.com") -> None:
        self.token = str(token).strip()
        self.secret = str(secret).strip()
        self.base_url = base_url.rstrip("/")

    def _auth_headers(self) -> Dict[str, str]:
        t = str(int(time.time() * 1000))
        nonce = str(uuid.uuid4())
        # sign content is token + timestamp + nonce
        content = (self.token + t + nonce).encode("utf-8")
        digest = hmac.new(self.secret.encode("utf-8"), msg=content, digestmod=hashlib.sha256).digest()
        sign = base64.b64encode(digest).decode("ascii")
        return {
            "Authorization": self.token,
            "sign": sign,
            "t": t,
            "nonce": nonce,
            "Content-Type": "application/json; charset=utf-8",
        }

    def _request(self, method: str, path: str, json_body: Optional[dict] = None, timeout_sec: int = 15) -> dict:
        url = f"{self.base_url}{path}"
        headers = self._auth_headers()
        resp = requests.request(method=method.upper(), url=url, headers=headers, json=json_body, timeout=timeout_sec)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and int(data.get("statusCode", 0)) != 100:
            # API-level error
            raise RuntimeError(f"SwitchBot error: {data}")
        return data

    # --- public APIs ---
    def list_devices(self) -> dict:
        return self._request("GET", "/v1.1/devices")

    def get_status(self, device_id: str) -> dict:
        return self._request("GET", f"/v1.1/devices/{device_id}/status")

    def send_command(self, device_id: str, command: str, parameter: str = "default", command_type: str = "command") -> dict:
        body = {"command": command, "parameter": parameter, "commandType": command_type}
        return self._request("POST", f"/v1.1/devices/{device_id}/commands", json_body=body)


def test_connection_message(token: str, secret: str, base_url: str = "https://api.switch-bot.com") -> str:
    """
    Return a short Japanese message summarizing the device list.
    Raises Exception on failure.
    """
    token = (token or "").strip()
    secret = (secret or "").strip()
    if not token or not secret:
        raise ValueError("Token / Secret が未設定です。")
    client = SwitchBotClient(token=token, secret=secret, base_url=base_url or "https://api.switch-bot.com")
    data = client.list_devices()
    body = data.get("body", {}) if isinstance(data, dict) else {}
    devs = []
    for key in ("deviceList", "infraredRemoteList"):
        arr = body.get(key, [])
        if isinstance(arr, list):
            devs.extend(arr)
    n = len(devs)
    if n == 0:
        return "接続成功（デバイス 0 件）"
    names = []
    for d in devs:
        if isinstance(d, dict):
            nm = str(d.get("deviceName") or d.get("deviceId") or d.get("remoteName") or "").strip()
            names.append(nm or "(no-name)")
    head = ", ".join(names[:5])
    if len(names) > 5:
        head += " ..."
    return f"接続成功（デバイス {n} 件）: {head}"


def collect_sensor_readings(token: str, secret: str, base_url: str = "https://api.switch-bot.com") -> Dict[str, Any]:
    """
    Fetch device list and gather simple sensor readings for common sensors.
    Returns: { "message": str, "rows": [ {device_id, device_name, temperature, humidity, illuminance, motion} ] }
    Note: SwitchBot API does not provide event timestamps for status; event_time is omitted.
    """
    client = SwitchBotClient(token=token, secret=secret, base_url=base_url or "https://api.switch-bot.com")
    data = client.list_devices()
    body = data.get("body", {}) if isinstance(data, dict) else {}
    device_list = body.get("deviceList", []) or []
    rows = []
    first_msg = None
    for d in device_list:
        if not isinstance(d, dict):
            continue
        dev_id = str(d.get("deviceId") or "")
        dev_name = str(d.get("deviceName") or d.get("remoteName") or dev_id)
        dev_type = str(d.get("deviceType") or "")
        # candidate sensor types
        sensor_types = {"Meter", "MeterPlus", "WoSensorTH", "Motion Sensor", "Contact Sensor"}
        if dev_type not in sensor_types:
            continue
        try:
            st = client.get_status(dev_id)
            st_body = st.get("body", {}) if isinstance(st, dict) else {}
        except Exception:
            st_body = {}
        temperature = None
        humidity = None
        illuminance = None
        motion = None
        # Meter family
        try:
            if "temperature" in st_body:
                temperature = float(st_body.get("temperature"))
        except Exception:
            pass
        try:
            if "humidity" in st_body:
                humidity = float(st_body.get("humidity"))
        except Exception:
            pass
        # Motion
        try:
            if "moveDetected" in st_body:
                motion = 1 if bool(st_body.get("moveDetected")) else 0
        except Exception:
            pass
        # Brightness is categorical ("bright"/"dim") - skip mapping to numeric
        # Contact sensors have openState; we don't map to motion here
        rows.append({
            "device_id": dev_id,
            "device_name": dev_name,
            "temperature": temperature,
            "humidity": humidity,
            "illuminance": illuminance,
            "motion": motion,
        })
        # build first-line message
        if first_msg is None:
            parts = []
            if temperature is not None:
                parts.append(f"温度{temperature:.1f}℃")
            if humidity is not None:
                parts.append(f"湿度{int(round(humidity))}%")
            if motion is not None:
                parts.append(f"人感{'あり' if motion else 'なし'}")
            body_msg = " ".join(parts) if parts else "センサー値なし"
            first_msg = f"{dev_name}: {body_msg}"
    if first_msg is None:
        first_msg = "SwitchBot: センサー情報が見つかりません"
    return {"message": first_msg, "rows": rows}


def describe_devices(token: str, secret: str, base_url: str = "https://api.switch-bot.com") -> str:
    """
    Multi-line device summary with available fields per device type.
    Example:
      Meter(寝室): 温度✓ 湿度✓
      Motion(廊下): 人感✓
    """
    client = SwitchBotClient(token=token, secret=secret, base_url=base_url or "https://api.switch-bot.com")
    try:
        data = client.list_devices()
    except Exception as ex:
        raise
    body = data.get("body", {}) if isinstance(data, dict) else {}
    device_list = body.get("deviceList", []) or []
    if not device_list:
        return "デバイスが見つかりません。"
    lines = []
    for d in device_list:
        if not isinstance(d, dict):
            continue
        dev_name = str(d.get("deviceName") or d.get("remoteName") or d.get("deviceId") or "")
        dev_type = str(d.get("deviceType") or "")
        # Only fetch status for common sensors
        temperature = humidity = None
        motion = None
        try:
            st = client.get_status(str(d.get("deviceId")))
            st_body = st.get("body", {}) if isinstance(st, dict) else {}
            if "temperature" in st_body:
                temperature = st_body.get("temperature")
            if "humidity" in st_body:
                humidity = st_body.get("humidity")
            if "moveDetected" in st_body:
                motion = bool(st_body.get("moveDetected"))
        except Exception:
            pass
        flags = []
        if temperature is not None:
            flags.append("温度✓")
        if humidity is not None:
            flags.append("湿度✓")
        if motion is not None:
            flags.append("人感✓")
        if not flags:
            flags.append("取得項目なし")
        lines.append(f"{dev_type}({dev_name}): " + " ".join(flags))
    return "\n".join(lines)
