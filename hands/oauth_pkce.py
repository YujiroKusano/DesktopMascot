from __future__ import annotations

import base64
import hashlib
import os
import secrets
import string
from typing import Tuple


def generate_code_verifier(length: int = 64) -> str:
    """
    Create a high-entropy PKCE code_verifier.
    RFC 7636 recommends length between 43 and 128.
    """
    length = max(43, min(128, int(length)))
    alphabet = string.ascii_letters + string.digits + "-._~"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_code_challenge(code_verifier: str) -> str:
    """
    Compute S256 code_challenge for a code_verifier.
    """
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def generate_state() -> str:
    """
    Create CSRF state token.
    """
    return base64.urlsafe_b64encode(os.urandom(24)).decode("ascii").rstrip("=")


def build_auth_url(
    auth_endpoint: str,
    client_id: str,
    redirect_uri: str,
    scope: str | None,
    state: str,
    code_challenge: str,
) -> str:
    """
    Build an OAuth2 authorization URL (Authorization Code with PKCE).
    """
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    if scope:
        params["scope"] = scope
    return f"{auth_endpoint}?{urlencode(params)}"


def exchange_code_for_token(
    token_endpoint: str,
    client_id: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    timeout_sec: int = 20,
) -> dict:
    """
    Exchange authorization code for access token (PKCE).
    Returns token response dictionary.
    """
    import requests

    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    resp = requests.post(token_endpoint, data=data, timeout=timeout_sec)
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(
    token_endpoint: str,
    client_id: str,
    refresh_token: str,
    timeout_sec: int = 20,
) -> dict:
    """
    Refresh access token using a refresh_token.
    """
    import requests

    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    resp = requests.post(token_endpoint, data=data, timeout=timeout_sec)
    resp.raise_for_status()
    return resp.json()

