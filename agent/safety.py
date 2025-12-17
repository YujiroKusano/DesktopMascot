from __future__ import annotations

from typing import Tuple, List, Optional
from agent.config import load_config


def check_text_allowed(text: str) -> Tuple[bool, Optional[str]]:
    """
    簡易な安全チェック。禁止キーワードに該当すれば False と理由を返す。
    """
    cfg = load_config()
    banned: List[str] = [str(w).lower() for w in cfg.get("safety", {}).get("banned_keywords", [])]
    lower = text.lower()
    for w in banned:
        if w and w in lower:
            return False, f"安全のため内容に関する操作を行えません（キーワード: {w}）。"
    return True, None


