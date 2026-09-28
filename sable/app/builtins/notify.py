"""`/notify setup|test|status` (Phase 5, E5).

Server, topic and reply topic go to config.json under `notify`; the ntfy
access token goes to the keyring only and is never printed.
"""
from __future__ import annotations

import getpass
import json
import os
import stat

from sable.core.config import keyring
from sable.daemon import notify
from sable.ui.console import out as _out

_USAGE = "usage: /notify setup | /notify test | /notify status"
_WARN = ("without an access token, the topic names are the only access control on a "
         "public server: pick long random ones, anyone who knows them can read pushes")


def _save(settings: dict, path=None) -> None:
    from sable.core.config.wizard import CONFIG_PATH
    path = path or CONFIG_PATH
    data = json.loads(path.read_text()) if path.exists() else {}
    data["notify"] = settings
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _setup(ask, secret) -> None:
    cur = notify.settings()
    s = {}
    for key, label in (("server", "server"), ("topic", "topic"), ("reply_topic", "reply topic")):
        s[key] = ask(f"{label} [{cur.get(key, '')}]: ").strip() or cur.get(key, "")
    if not s["topic"]:
        _out("a topic is required, nothing changed")
        return
    if s["reply_topic"] == s["topic"]:
        _out("the reply topic must differ from the topic, nothing changed")
        return
    try:
        _save(s)
    except (OSError, ValueError) as exc:
        _out(f"could not write config.json: {exc}")
        return
    token = secret("access token (not echoed, empty to keep/skip): ")
    if token:
        try:
            keyring.store_api_key(notify.KEYRING_SERVICE, token)
            _out("access token stored in the keyring")
        except RuntimeError as exc:
            _out(f"keyring unavailable, token not stored: {exc}")
    _out(f"saved: {s['server']} topic {s['topic']}"
         + (f", replies on {s['reply_topic']}" if s["reply_topic"] else ", no phone approvals"))
    if not notify.auth_headers():
        _out("warning: " + _WARN)


def _handle_notify_builtin(argument: str, ask=input, secret=getpass.getpass) -> bool:
    sub = argument.strip()
    try:
        if sub == "setup":
            _setup(ask, secret)
        elif sub == "test":
            ok = notify.send("Sable test", "notifications from sabled work")
            _out("sent" if ok else "not sent: run /notify setup, or see the daemon log")
        elif sub in ("", "status"):
            s = notify.settings()
            token = "set" if notify.auth_headers() else "not set"
            _out(f"server {s['server']}  topic {s.get('topic') or '(none)'}  "
                 f"reply topic {s.get('reply_topic') or '(none)'}  access token {token}")
            if token == "not set" and s.get("topic"):
                _out("warning: " + _WARN)
        else:
            _out(_USAGE)
    except (EOFError, KeyboardInterrupt):
        _out("cancelled")
    return True
