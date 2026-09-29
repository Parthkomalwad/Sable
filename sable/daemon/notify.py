"""Push notifications over ntfy (Phase 5, E5).

`send()` publishes one message with the ntfy JSON API. Server, topic and
reply topic live in config.json under `notify`; the access token lives only
in the keyring. Bodies pass the secret redactor. A failure is logged and
returns False; it never raises into the daemon loop.
"""
from __future__ import annotations

import json
import logging

import httpx

from sable.policy.engine import redact_text

DEFAULT_SERVER = "https://ntfy.sh"
KEYRING_SERVICE = "ntfy"
TIMEOUT = httpx.Timeout(30.0)
log = logging.getLogger("sabled.notify")


def settings() -> dict:
    """{"server", "topic", "reply_topic"} from config.json; server defaulted."""
    from sable.core.config.wizard import CONFIG_PATH
    try:
        data = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    except (OSError, ValueError):
        data = {}
    s = dict(data.get("notify") or {})
    s["server"] = (s.get("server") or DEFAULT_SERVER).rstrip("/")
    return s


def _token() -> str | None:
    from sable.core.config import keyring
    try:
        return keyring.lookup(KEYRING_SERVICE)
    except (keyring.KeyringUnavailable, RuntimeError):
        return None


def _client():
    """httpx itself; tests swap in a Client on a MockTransport."""
    return httpx


def auth_headers() -> dict:
    token = _token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def send(title: str, body: str, actions=(), topic: str | None = None) -> bool:
    """Publish to `topic`, or the configured one; False when neither is set."""
    s = settings()
    topic = topic or s.get("topic")
    if not topic:
        return False
    payload = {"topic": topic, "title": redact_text(title),
               "message": redact_text(body), "actions": list(actions)}
    try:
        r = _client().post(s["server"], json=payload, headers=auth_headers(), timeout=TIMEOUT)
        r.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        # The exception names the URL and status, never the request headers.
        log.warning("ntfy publish failed: %s", exc)
        return False
