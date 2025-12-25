from __future__ import annotations

import http.server
import json
import socket
import threading
import time
import webbrowser
from typing import Callable, Optional

from hands.oauth_pkce import (
    generate_code_verifier,
    generate_code_challenge,
    generate_state,
    build_auth_url,
    exchange_code_for_token,
    refresh_access_token,
)


class _OnceCodeHandler(http.server.BaseHTTPRequestHandler):
    """
    Minimal local HTTP handler to capture ?code=...&state=...
    """
    server_version = "EdoOAuth/1.0"
    code: Optional[str] = None
    state: Optional[str] = None
    expected_state: Optional[str] = None
    stop_server_cb: Optional[Callable[[], None]] = None

    def log_message(self, format, *args):  # noqa: N802
        return  # quiet

    def do_GET(self):  # noqa: N802
        from urllib.parse import urlparse, parse_qs

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]

        ok = bool(code) and bool(state) and (state == self.expected_state)
        if ok:
            _OnceCodeHandler.code = code
            _OnceCodeHandler.state = state
            body = "<h3>認証が完了しました。ウィンドウを閉じてください。</h3>"
            status = 200
        else:
            body = "<h3>認証に失敗しました。アプリに戻ってやり直してください。</h3>"
            status = 400
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
        finally:
            # stop server after responding once
            if self.stop_server_cb:
                threading.Thread(target=self.stop_server_cb, daemon=True).start()


def _free_port(preferred: int = 8765) -> int:
    """
    Find an available TCP port (try preferred first).
    """
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except Exception:
                continue
    raise RuntimeError("No free port found")


def authorize_interactive(
    auth_endpoint: str,
    token_endpoint: str,
    client_id: str,
    scope: str = "",
    redirect_port: int = 8765,
    open_browser: bool = True,
    timeout_sec: int = 180,
) -> dict:
    """
    Run interactive OAuth2 Authorization Code with PKCE on localhost.
    Returns token response dict (access_token, refresh_token, expires_in, ...).
    """
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)
    state = generate_state()
    port = _free_port(redirect_port)
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    auth_url = build_auth_url(
        auth_endpoint=auth_endpoint,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state,
        code_challenge=code_challenge,
    )

    # Start temp server
    server = http.server.HTTPServer(("127.0.0.1", port), _OnceCodeHandler)
    def _stop():
        try:
            server.shutdown()
        except Exception:
            pass
    _OnceCodeHandler.expected_state = state
    _OnceCodeHandler.stop_server_cb = _stop
    thr = threading.Thread(target=server.serve_forever, daemon=True)
    thr.start()

    if open_browser:
        webbrowser.open(auth_url)

    # Wait for code
    start = time.monotonic()
    while time.monotonic() - start < timeout_sec:
        if _OnceCodeHandler.code and _OnceCodeHandler.state == state:
            break
        time.sleep(0.2)
    # Ensure server is closed
    _stop()

    if not _OnceCodeHandler.code:
        raise TimeoutError("Timeout while waiting for authorization code.")

    tokens = exchange_code_for_token(
        token_endpoint=token_endpoint,
        client_id=client_id,
        code=_OnceCodeHandler.code,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
    )
    return tokens


def refresh_tokens(
    token_endpoint: str,
    client_id: str,
    refresh_token_value: str,
    timeout_sec: int = 20,
) -> dict:
    """
    Refresh and return tokens. Wrapper for convenience.
    """
    return refresh_access_token(
        token_endpoint=token_endpoint,
        client_id=client_id,
        refresh_token=refresh_token_value,
        timeout_sec=timeout_sec,
    )

