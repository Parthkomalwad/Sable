"""`web.search` and `web.fetch` (Phase 3.5, J2). Both taint.

URL safety for `web.fetch`, the choice and why:

- Only `http` and `https`. `file://`, `ftp://` and the rest are refused.
- The host is resolved with `getaddrinfo` and **every** address it returns
  must be public (`ipaddress.is_global`) and not a cloud metadata address.
  Private, loopback, link-local (`169.254.0.0/16`, so `169.254.169.254`),
  shared (`100.64.0.0/10`, so Alibaba's `100.100.100.200`) and unique-local
  IPv6 (so AWS's `fd00:ec2::254`) are denied with no request made.
  Vision J2 said `confirm` for localhost and raw IPs; a tool's tier is fixed
  per tool, so a private address is denied outright (stricter), and a
  literal public IP is fetched like a name.
- The connection is **pinned to the checked IP**: the request goes to the IP
  with the original `Host` header and TLS SNI, so a DNS answer that changes
  between check and connect (rebinding) cannot move it.
- Redirects are followed by hand, at most 5, and each hop is checked again
  the same way, so a public page cannot redirect into a private range.
- Plain `http` to a public host is allowed and the output says so.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, urljoin, urlsplit

import httpx

from sable.policy.tiers import Tier
from sable.tools.base import Tool, ToolContext, ToolError, ToolResult
from sable.tools.html_text import html_to_text
from sable.tools.registry import register

PROVIDERS = ("duckduckgo", "searxng", "brave", "tavily")
MAX_REDIRECTS = 5
DEFAULT_MAX_BYTES = 200_000
#: What the model reads of a page: about 4k tokens at 4 characters a token.
OUTPUT_CHARS = 16_000
CACHE_TTL_S = 900
_METADATA = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254"),
             ipaddress.ip_address("100.100.100.200")}
_UA = "Mozilla/5.0 (compatible; sable-shell)"

#: Tests swap in an `httpx.MockTransport`; None is the real network.
_transport: httpx.BaseTransport | None = None
#: url -> (fetched at, output). Per process. ponytail: unbounded dict, an LRU if a session fetches thousands.
_cache: dict[str, tuple[float, str]] = {}


def _client() -> httpx.Client:
    return httpx.Client(transport=_transport, timeout=httpx.Timeout(30.0),
                        follow_redirects=False, headers={"User-Agent": _UA})


def _web_config() -> dict:
    """`tools.web` from the config file; {} when absent or unreadable."""
    from sable.core.config.schema import ShellConfig
    from sable.core.config.wizard import CONFIG_PATH
    try:
        return ShellConfig.from_dict(json.loads(CONFIG_PATH.read_text())).tools.get("web", {})
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# URL safety
# ---------------------------------------------------------------------------

def check_url(url: str) -> str:
    """The IP to connect to for `url`, or ToolError. Makes no request."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ToolError(f"refused: only http and https URLs, not {parts.scheme or 'none'!r}")
    host = parts.hostname
    if not host:
        raise ToolError("refused: URL has no host")
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise ToolError(f"could not resolve {host}: {exc}") from exc
    ips = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if ip in _METADATA or not ip.is_global:
            raise ToolError(f"refused: {host} resolves to {ip}, a private, local or metadata address")
        ips.append(ip)
    if not ips:
        raise ToolError(f"could not resolve {host}")
    return str(ips[0])


def _pinned(url: str, ip: str) -> tuple[str, dict, dict]:
    """`url` rewritten to connect to `ip`, with the Host header and SNI kept."""
    parts = urlsplit(url)
    netloc = f"[{ip}]" if ":" in ip else ip
    if parts.port:
        netloc += f":{parts.port}"
    host_header = parts.hostname + (f":{parts.port}" if parts.port else "")
    return (parts._replace(netloc=netloc).geturl(), {"Host": host_header},
            {"sni_hostname": parts.hostname})


# ---------------------------------------------------------------------------
# web.fetch
# ---------------------------------------------------------------------------

def fetch(url: str, max_bytes: int = DEFAULT_MAX_BYTES) -> str:
    cached = _cache.get(url)
    if cached and time.monotonic() - cached[0] < CACHE_TTL_S:
        return cached[1]
    current = url
    try:
        with _client() as client:
            for _ in range(MAX_REDIRECTS + 1):
                ip = check_url(current)
                target, headers, ext = _pinned(current, ip)
                with client.stream("GET", target, headers=headers, extensions=ext) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            raise ToolError(f"HTTP {resp.status_code} redirect with no location")
                        current = urljoin(current, location)
                        continue
                    body = bytearray()
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) >= max_bytes:
                            break
                    status, ctype = resp.status_code, resp.headers.get("content-type", "")
                    charset = resp.charset_encoding or "utf-8"
                break
            else:
                raise ToolError(f"more than {MAX_REDIRECTS} redirects")
    except httpx.HTTPError as exc:
        raise ToolError(f"request failed: {exc}") from exc

    if status >= 400:
        raise ToolError(f"HTTP {status}")
    mime = ctype.split(";")[0].strip().lower()
    text = bytes(body[:max_bytes]).decode(charset, errors="replace")
    if mime in ("text/html", "application/xhtml+xml"):
        text = html_to_text(text)
    elif not (mime.startswith("text/") or mime == "application/json"):
        raise ToolError(f"refused: content type {mime or 'unknown'!r} is not text")
    notes = []
    if len(body) >= max_bytes:
        notes.append(f"read the first {max_bytes} bytes only")
    if len(text) > OUTPUT_CHARS:
        text = text[:OUTPUT_CHARS]
        notes.append(f"truncated to {OUTPUT_CHARS} characters")
    if current.startswith("http:"):
        notes.append("plain http: not encrypted, could be altered in transit")
    head = f"url: {current}" + "".join(f"\n[{n}]" for n in notes)
    out = f"{head}\n\n{text}"
    _cache[url] = (time.monotonic(), out)
    return out


# ---------------------------------------------------------------------------
# web.search
# ---------------------------------------------------------------------------

class _DDG(HTMLParser):
    """Results from DuckDuckGo's HTML endpoint: `a.result__a` then `.result__snippet`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._field: str | None = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if self._field:
            self._depth += 1
            return
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self.results.append({"title": "", "url": _ddg_target(a.get("href") or ""), "snippet": ""})
            self._field, self._depth = "title", 1
        elif "result__snippet" in classes and self.results:
            self._field, self._depth = "snippet", 1

    def handle_endtag(self, tag):
        if self._field:
            self._depth -= 1
            if self._depth == 0:
                self._field = None

    def handle_data(self, data):
        if self._field:
            self.results[-1][self._field] += data


def _ddg_target(href: str) -> str:
    """DuckDuckGo links go through `/l/?uddg=<real url>`; unwrap them."""
    if href.startswith("//"):
        href = "https:" + href
    uddg = parse_qs(urlsplit(href).query).get("uddg")
    return uddg[0] if uddg else href


def parse_duckduckgo(html: str) -> list[dict]:
    parser = _DDG()
    parser.feed(html)
    return [{k: " ".join(v.split()) for k, v in r.items()} for r in parser.results]


def _key(service: str) -> str:
    from sable.core.config.keyring import KeyringUnavailable, lookup
    try:
        key = lookup(service)
    except KeyringUnavailable as exc:
        raise ToolError(f"{service} search needs an API key but the keyring is unavailable: {exc}") from exc
    if not key:
        raise ToolError(f"{service} search needs an API key: store it with /secret add {service}")
    return key


def search(query: str, k: int = 5) -> list[dict]:
    cfg = _web_config()
    provider = cfg.get("search_provider", "duckduckgo")
    k = max(1, min(k, 20))
    try:
        with _client() as client:
            if provider == "duckduckgo":
                r = client.get(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")
                r.raise_for_status()
                results = parse_duckduckgo(r.text)
            elif provider == "searxng":
                base = cfg.get("searxng_url")
                if not base:
                    raise ToolError("searxng needs tools.web.searxng_url in the config")
                r = client.get(base.rstrip("/") + "/search", params={"q": query, "format": "json"})
                r.raise_for_status()
                results = [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
                           for x in r.json().get("results", [])]
            elif provider == "brave":
                r = client.get("https://api.search.brave.com/res/v1/web/search",
                               params={"q": query, "count": k},
                               headers={"X-Subscription-Token": _key("brave"), "Accept": "application/json"})
                r.raise_for_status()
                results = [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("description", "")}
                           for x in r.json().get("web", {}).get("results", [])]
            elif provider == "tavily":
                r = client.post("https://api.tavily.com/search",
                                json={"api_key": _key("tavily"), "query": query, "max_results": k})
                r.raise_for_status()
                results = [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
                           for x in r.json().get("results", [])]
            else:
                raise ToolError(f"unknown search provider {provider!r}; one of {', '.join(PROVIDERS)}")
    except httpx.HTTPError as exc:
        raise ToolError(f"{provider} search failed: {exc}") from exc
    except ValueError as exc:   # a JSON body that is not JSON
        raise ToolError(f"{provider} returned an unreadable answer: {exc}") from exc
    return results[:k]


def _format(results: list[dict]) -> str:
    if not results:
        return "no results"
    return "\n\n".join(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}"
                       for i, r in enumerate(results, 1))


# ---------------------------------------------------------------------------
# Safe research
# ---------------------------------------------------------------------------
#
# Taint makes every call one tier stricter, so after the first search each
# fetch asked for YES. Two calls are safe from a tainted agent, and only these:
#
# - `web.search`: the query goes to the search provider, which an attacker
#   cannot read, so it is no channel out.
# - `web.fetch` of a URL this goal's search returned, exactly: the search
#   engine chose that URL, so a hostile page cannot make the agent fetch an
#   address with data smuggled into it. A URL the model built or copied from
#   a page is not in the set and still needs YES.
#
# Everything else a tainted agent does (commands, writes, other fetches) is
# still bumped.


def _url_key(url: str) -> str:
    return url.split("#", 1)[0].strip()


def _search_exempt(args: dict, ctx: ToolContext) -> bool:
    return True


def _fetch_exempt(args: dict, ctx: ToolContext) -> bool:
    return ctx.seen_urls is not None and _url_key(str(args.get("url", ""))) in ctx.seen_urls


def _run_search(args: dict, ctx: ToolContext) -> ToolResult:
    results = search(args["query"], args.get("k", 5))
    if ctx.seen_urls is not None:
        ctx.seen_urls.update(_url_key(r["url"]) for r in results if r.get("url"))
    return ToolResult(ok=True, output=_format(results), taints=True)


def _run_fetch(args: dict, ctx: ToolContext) -> ToolResult:
    max_bytes = max(1, min(args.get("max_bytes", DEFAULT_MAX_BYTES), DEFAULT_MAX_BYTES * 10))
    return ToolResult(ok=True, output=fetch(args["url"], max_bytes), taints=True)


register(Tool(
    name="web.search",
    description="searches the web; returns numbered title, url and snippet",
    schema={"query": "string", "k?": "integer"},
    tier=Tier.ALLOW,
    run=_run_search,
    taint_exempt=_search_exempt,
))
register(Tool(
    name="web.fetch",
    description="reads a public http(s) page as text; private and local addresses are refused",
    schema={"url": "string", "max_bytes?": "integer"},
    tier=Tier.ALLOW,
    run=_run_fetch,
    taint_exempt=_fetch_exempt,
))
