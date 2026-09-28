"""Search the official MCP Registry and turn each hit into a `/mcp add` line (D3).

API (checked 2026-09-28): `GET https://registry.modelcontextprotocol.io/v0.1/
servers?search=<q>&limit=<n>&version=latest`. The body is `{"servers": [{"server":
{...}, "_meta": {...}}], "metadata": {...}}`. Without `version=latest` every
published version of a server comes back as its own row.

Everything in a registry entry is written by whoever published it, so it is
untrusted: descriptions have control characters stripped and are capped, and
names are reduced to a safe short name before they go into a command line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

URL = "https://registry.modelcontextprotocol.io/v0.1/servers"
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_IDENT = re.compile(r"[A-Za-z0-9@][A-Za-z0-9._/@:+-]*")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


class RegistryError(Exception):
    """The registry could not be reached or answered badly. Message is one line."""


@dataclass
class Hit:
    name: str
    description: str
    transport: str
    add_line: str
    needs: list[str] = field(default_factory=list)  # env vars / headers to fill in


def clean(text: object, cap: int = 160) -> str:
    text = _CONTROL.sub(" ", str(text or "")).strip()
    return text if len(text) <= cap else text[: cap - 1] + "…"


def _short(name: str) -> str:
    return _UNSAFE.sub("-", name.rsplit("/", 1)[-1]).strip("-").lower() or "server"


def _args(entries: list) -> list[str]:
    """Required positional/named arguments, shown as placeholders to fill in."""
    out = []
    for a in entries or []:
        if not isinstance(a, dict) or not a.get("isRequired"):
            continue
        slot = f"<{_short(a.get('name') or a.get('valueHint') or 'value')}>"
        out.append(slot if a.get("type") != "named" else f"{_short(a.get('name', ''))}={slot}")
    return out


def to_hit(server: dict) -> Hit | None:
    name = clean(server.get("name"), 120)
    short = _short(name)
    desc = clean(server.get("description"))
    for pkg in server.get("packages") or []:
        kind, ident = pkg.get("registryType"), str(pkg.get("identifier", ""))
        prefix = {"npm": ["npx", "-y"], "pypi": ["uvx"], "oci": ["docker", "run", "-i", "--rm"]}.get(kind)
        if not prefix or not _IDENT.fullmatch(ident):
            continue
        cmd = prefix + [ident] + _args(pkg.get("packageArguments"))
        needs = [clean(e.get("name"), 60) + "=<value>" for e in pkg.get("environmentVariables") or []
                 if isinstance(e, dict) and e.get("isRequired")]
        transport = clean((pkg.get("transport") or {}).get("type") or "stdio", 20)
        return Hit(name, desc, transport, f"/mcp add {short} {' '.join(cmd)}", needs)
    for remote in server.get("remotes") or []:
        url = str(remote.get("url", ""))
        if url.startswith(("https://", "http://")) and not _CONTROL.search(url) and " " not in url:
            needs = ["header " + clean(h.get("name"), 60) for h in remote.get("headers") or []
                     if isinstance(h, dict) and h.get("isRequired")]
            return Hit(name, desc, clean(remote.get("type"), 20), f"/mcp add {short} {url}", needs)
    return None  # e.g. mcpb bundles: nothing we can run


def search(q: str, limit: int = 10, client: httpx.Client | None = None) -> list[Hit]:
    params = {"search": q, "limit": limit, "version": "latest"}
    try:
        if client is None:
            with httpx.Client(timeout=httpx.Timeout(30.0)) as c:
                resp = c.get(URL, params=params)
        else:
            resp = client.get(URL, params=params)
    except httpx.TimeoutException:
        raise RegistryError("MCP Registry timed out, try again later") from None
    except httpx.HTTPError:
        raise RegistryError("MCP Registry unreachable (offline?)") from None
    if resp.status_code != 200:
        raise RegistryError(f"MCP Registry answered HTTP {resp.status_code}"
                            + (" (rate limited)" if resp.status_code == 429 else ""))
    try:
        rows = resp.json().get("servers") or []
    except (ValueError, AttributeError):
        raise RegistryError("MCP Registry sent a response that is not JSON") from None
    hits = [to_hit(r.get("server") or {}) for r in rows if isinstance(r, dict)]
    return [h for h in hits if h][:limit]
