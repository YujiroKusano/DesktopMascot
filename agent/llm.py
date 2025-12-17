from __future__ import annotations

from typing import List, Dict, Optional
import json
import re
import requests
from agent.config import load_config
import logging


def chat(messages: List[Dict[str, str]]) -> Optional[str]:
    """
    LM Studio の OpenAI 互換 API へ問い合わせて返答本文を返す。
    失敗時は None を返す。
    """
    cfg = load_config().get("llm", {})
    if not bool(cfg.get("enabled", False)):
        return None
    base_url: str = str(cfg.get("base_url", "http://localhost:1234/v1")).rstrip("/")
    api_key: str = str(cfg.get("api_key", ""))
    model: str = str(cfg.get("model", "gpt-oss-20b"))
    temperature: float = float(cfg.get("temperature", 0.7))
    max_tokens: int = int(cfg.get("max_tokens", 256))
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        # 1) chat/completions
        resp = requests.post(f"{base_url}/chat/completions", json=payload, headers=headers, timeout=60)
        # 2xx 以外でも本文を見て取り出せる場合があるので raise は遅らせる
        data = {}
        try:
            data = resp.json()
        except Exception:
            pass
        if resp.ok and isinstance(data, dict):
            # OpenAI互換: choices[0].message.content
            ch = data.get("choices")
            if isinstance(ch, list) and ch:
                msg_obj = ch[0].get("message") or {}
                content = (msg_obj.get("content") or ch[0].get("text") or "").strip()
                if content:
                    return content
            logging.warning(
                "LLM chat/completions returned OK but no content (status=%s, body_keys=%s)",
                resp.status_code,
                list(data.keys()) if isinstance(data, dict) else type(data),
            )
        else:
            # 非200のときは本文の一部を記録（長すぎる場合は切り詰め）
            text_preview = ""
            try:
                raw = resp.text or ""
                text_preview = raw[:500]
            except Exception:
                pass
            logging.warning(
                "LLM chat/completions non-OK (status=%s) preview=%r",
                getattr(resp, "status_code", None),
                text_preview,
            )
        # 2) responses エンドポイント（LM Studioの別実装）
        # Responses API では "input" が必須。messages を素朴にテキストへ変換
        def _messages_to_text(msgs: List[Dict[str, str]]) -> str:
            parts: List[str] = []
            for m in msgs:
                role = m.get("role", "")
                content = m.get("content", "")
                if role and content:
                    parts.append(f"{role}: {content}")
            return "\n".join(parts)
        resp2 = requests.post(
            f"{base_url}/responses",
            json={
                "model": model,
                "input": _messages_to_text(messages),
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            },
            headers=headers,
            timeout=60,
        )
        data2 = {}
        try:
            data2 = resp2.json()
        except Exception:
            pass
        if resp2.ok and isinstance(data2, dict):
            # 代表的なフィールド: choices[0].message.content or output_text
            ch2 = data2.get("choices")
            if isinstance(ch2, list) and ch2:
                msg_obj2 = ch2[0].get("message") or {}
                content2 = (msg_obj2.get("content") or ch2[0].get("text") or "").strip()
                if content2:
                    return content2
            out = (data2.get("output_text") or "").strip()
            if out:
                return out
            logging.warning(
                "LLM responses returned OK but no content (status=%s, body_keys=%s)",
                resp2.status_code,
                list(data2.keys()) if isinstance(data2, dict) else type(data2),
            )
        else:
            text_preview2 = ""
            try:
                raw2 = resp2.text or ""
                text_preview2 = raw2[:500]
            except Exception:
                pass
            logging.warning(
                "LLM responses non-OK (status=%s) preview=%r",
                getattr(resp2, "status_code", None),
                text_preview2,
            )
        return None
    except Exception:
        logging.exception(
            "LLM request failed (base_url=%s, model=%s)", base_url, model
        )
        return None





def translate_to_japanese_if_needed(text: str) -> str:
    """
    応答が日本語をほとんど含まない場合、日本語に翻訳して返す。
    失敗時は元のテキストを返す。
    """
    if not text:
        return text
    has_jp = bool(re.search(r"[一-龥ぁ-んァ-ン]", text))
    has_lat = bool(re.search(r"[A-Za-z]", text))
    has_action = "*" in text  # 例: *stretches*
    # 日本語が含まれていても、英字の割合が高い/アクション記法がある場合は翻訳対象にする
    if has_jp and not has_lat and not has_action:
        return text
    if has_lat:
        letters = len(re.findall(r"[A-Za-z]", text))
        total = max(1, len(text))
        lat_ratio = letters / total
    else:
        lat_ratio = 0.0
    cfg = load_config().get("llm", {})
    if not bool(cfg.get("enabled", False)):
        return text
    system = (
        "次のテキストを自然な日本語に翻訳してください。"
        "箇条書きや改行は維持し、過度な絵文字や擬態語は控えめに。"
        "英語のフレーズやアクション記法（例:*stretches*）も日本語に言い換えてください。"
    )
    out = chat([{"role": "system", "content": system}, {"role": "user", "content": text}])
    return out or text

