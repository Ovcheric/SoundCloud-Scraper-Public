"""
SoundCloud OAuth: loading, refreshing and (only when asked) re-authorizing.

The legacy get_config() opened a browser on any refresh failure, which is why
no script could safely call it - a background job would hang on a consent
screen nobody was watching. Here the browser step is a separate function, and
the ordinary path raises ReauthRequired instead.

Public: Credentials, AuthError, MissingCredentials, ReauthRequired, load,
        save, load_fresh, refresh, authorize_interactively, auth_headers,
        REDIRECT_URI, APPS_PAGE

Imports nothing from src/. This is an I/O boundary, so errors are typed and
raised here rather than leaking requests exceptions upward.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

AUTH_URL = "https://secure.soundcloud.com/authorize"
TOKEN_URL = "https://secure.soundcloud.com/oauth/token"
REDIRECT_URI = "http://localhost:8080/callback"
CALLBACK_PORT = 8080
# Long enough for someone signing in to SoundCloud for the first time.
CALLBACK_TIMEOUT_SECONDS = 300
# Where a user registers the app whose client id and secret this needs.
APPS_PAGE = "https://soundcloud.com/you/apps"
# config.example.json ships these. A copied but unedited example is a first
# launch, not a SoundCloud error.
_PLACEHOLDER_PREFIX = "YOUR_"

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"

# Refresh this long before actual expiry, so a long request cannot straddle it.
_EXPIRY_MARGIN_SECONDS = 60


class AuthError(Exception):
    """Authentication failed in a way the caller cannot fix automatically."""


class MissingCredentials(AuthError):
    """No client id and secret yet - a first launch. Someone must supply them."""


class ReauthRequired(AuthError):
    """The refresh token is dead. A human must approve access again."""


@dataclass(frozen=True)
class Credentials:
    client_id: str
    client_secret: str
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0

    def is_expired(self, *, now: float | None = None) -> bool:
        if not self.expires_at:
            return False
        return (now or time.time()) > self.expires_at - _EXPIRY_MARGIN_SECONDS

    def as_dict(self) -> dict:
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }


def auth_headers(creds: Credentials) -> dict:
    return {"Authorization": f"OAuth {creds.access_token}"}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def load(path: str | Path | None = None) -> Credentials:
    """
    Read config.json. Environment variables win for the client id and secret,
    so a machine can supply them without the file holding them at all.
    """
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    stored: dict = {}
    if target.exists():
        stored = json.loads(target.read_text(encoding="utf-8"))

    client_id = os.environ.get("SOUNDCLOUD_CLIENT_ID") or stored.get("client_id", "")
    client_secret = (
        os.environ.get("SOUNDCLOUD_CLIENT_SECRET") or stored.get("client_secret", "")
    )
    if not _filled_in(client_id) or not _filled_in(client_secret):
        raise MissingCredentials(
            f"No client_id/client_secret. Set SOUNDCLOUD_CLIENT_ID and "
            f"SOUNDCLOUD_CLIENT_SECRET, or fill in {target}. "
            f"See config.example.json."
        )
    return Credentials(
        client_id=client_id,
        client_secret=client_secret,
        access_token=stored.get("access_token", ""),
        refresh_token=stored.get("refresh_token", ""),
        expires_at=float(stored.get("expires_at") or 0),
    )


def _filled_in(value: str) -> bool:
    return bool(value) and not value.startswith(_PLACEHOLDER_PREFIX)


def save(creds: Credentials, path: str | Path | None = None) -> None:
    """Write tokens back. This file is gitignored and holds live secrets."""
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    target.write_text(json.dumps(creds.as_dict(), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

def refresh(creds: Credentials, *, session: requests.Session | None = None
            ) -> Credentials:
    """
    Trade the refresh token for a new access token.

    Raises ReauthRequired when SoundCloud rejects the refresh token - a 400 is
    how it says the grant is dead, and no retry will help.
    """
    if not creds.refresh_token:
        raise ReauthRequired("No refresh token stored.")

    response = _post_token(session or requests, {
        "grant_type": "refresh_token",
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
    })
    if response.status_code == 400:
        raise ReauthRequired(
            "SoundCloud has signed this app out. Close and reopen the app to "
            "sign in again."
        )
    response.raise_for_status()
    return _apply_token_response(creds, response.json())


def _post_token(http, data: dict):
    """POST to the token endpoint. Being offline is an AuthError, not a crash."""
    try:
        return http.post(
            TOKEN_URL,
            headers={
                "Accept": "application/json; charset=utf-8",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=data,
            timeout=15,
        )
    except requests.RequestException as exc:
        raise AuthError(
            "Could not reach SoundCloud. Check your internet connection and "
            "try again."
        ) from exc


def _apply_token_response(creds: Credentials, payload: dict) -> Credentials:
    return replace(
        creds,
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token") or creds.refresh_token,
        expires_at=time.time() + float(payload.get("expires_in", 3600)),
    )


def load_fresh(path: str | Path | None = None, *,
               session: requests.Session | None = None) -> Credentials:
    """
    Usable credentials, refreshed and saved if they had expired.

    NEVER opens a browser. A caller that wants the consent flow must handle
    ReauthRequired and call authorize_interactively() itself, so no background
    job can silently block on a window nobody is looking at.
    """
    creds = load(path)
    if not creds.access_token:
        raise ReauthRequired("No access token stored. Authorize first.")
    if creds.is_expired():
        creds = refresh(creds, session=session)
        save(creds, path)
    return creds


# ---------------------------------------------------------------------------
# Interactive authorization — only ever called explicitly
# ---------------------------------------------------------------------------

def _pkce_pair() -> tuple:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    """Catches the one redirect SoundCloud makes back to localhost."""

    code = None
    error = None

    def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        params = parse_qs(urlparse(self.path).query)
        if "code" in params:
            _CallbackHandler.code = params["code"][0]
            self.send_response(200)
            body = (b"<html><body style='font-family:sans-serif'><h2>Connected."
                    b"</h2><p>You can close this tab and go back to SoundCloud "
                    b"Discovery.</p></body></html>")
        else:
            _CallbackHandler.error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            body = b"<html><body><h2>Authorization failed.</h2></body></html>"
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # the stock handler prints to stderr; we do not want that


def _open_callback_server() -> HTTPServer:
    """Listen BEFORE the browser opens, so a busy port fails up front."""
    try:
        return HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
    except OSError as exc:
        raise AuthError(
            f"Port {CALLBACK_PORT} is already in use by another program, so "
            f"SoundCloud's sign-in page cannot hand back to this app. Close "
            f"that program and try again."
        ) from exc


def _wait_for_callback(server: HTTPServer) -> str | None:
    _CallbackHandler.code = None
    _CallbackHandler.error = None
    worker = threading.Thread(target=server.handle_request, daemon=True)
    worker.start()
    worker.join(timeout=CALLBACK_TIMEOUT_SECONDS)
    server.server_close()
    return _CallbackHandler.code


def _no_code(authorize_url: str) -> AuthError:
    """Why sign-in produced nothing, in words a first-time user can act on."""
    if _CallbackHandler.error:
        return AuthError(
            f"SoundCloud did not approve access ({_CallbackHandler.error}). "
            f"Check that your SoundCloud app's redirect URI is exactly "
            f"{REDIRECT_URI}, then try again."
        )
    return AuthError(
        f"No answer from SoundCloud within {CALLBACK_TIMEOUT_SECONDS // 60} "
        f"minutes. If the sign-in page did not open, visit:\n{authorize_url}"
    )


def _exchange_code(creds: Credentials, code: str, verifier: str) -> dict:
    response = _post_token(requests, {
        "grant_type": "authorization_code",
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
        "code": code,
    })
    if response.status_code in (400, 401):
        raise AuthError(
            "SoundCloud did not accept the client ID and secret. Copy both "
            f"again from your app's page ({APPS_PAGE}) and try again."
        )
    response.raise_for_status()
    return response.json()


def authorize_interactively(creds: Credentials, *, open_browser: bool = True,
                            path: str | Path | None = None) -> Credentials:
    """
    Run the PKCE consent flow and save the resulting tokens.

    Opens a browser and blocks for up to five minutes, so only call this from
    somewhere a human is watching. Returns the saved credentials.
    """
    verifier, challenge = _pkce_pair()
    authorize_url = f"{AUTH_URL}?" + urlencode({
        "client_id": creds.client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": secrets.token_hex(16),
    })

    server = _open_callback_server()
    if open_browser:
        webbrowser.open(authorize_url)

    code = _wait_for_callback(server)
    if not code:
        raise _no_code(authorize_url)

    fresh = _apply_token_response(creds, _exchange_code(creds, code, verifier))
    save(fresh, path)
    return fresh
