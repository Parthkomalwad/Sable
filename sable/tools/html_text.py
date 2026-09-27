"""HTML to readable text with the stdlib parser only (Phase 3.5, J2).

This path reads hostile pages, so it adds no dependency. It drops what a
reader never sees (scripts, styles, hidden elements), keeps link text, and
collapses whitespace. It is not a renderer: CSS classes that hide things
are not seen, only the `hidden` attribute, `aria-hidden="true"` and an
inline `display:none`.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

_SKIP = {"script", "style", "noscript", "template", "svg", "head"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}
_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
          "section", "article", "header", "footer", "pre", "blockquote", "table",
          "ul", "ol", "dt", "dd", "hr", "title"}
_DISPLAY_NONE = re.compile(r"display\s*:\s*none", re.IGNORECASE)


def _hidden(tag: str, attrs: list[tuple[str, str | None]]) -> bool:
    if tag in _SKIP:
        return True
    a = {k.lower(): (v or "") for k, v in attrs}
    return ("hidden" in a or a.get("aria-hidden", "").lower() == "true"
            or bool(_DISPLAY_NONE.search(a.get("style", ""))))


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0   # depth inside a dropped element

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            if not self.skip and tag in _BLOCK:
                self.parts.append("\n")
            return
        if self.skip:
            self.skip += 1
        elif _hidden(tag, attrs):
            self.skip = 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if not self.skip and tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        if self.skip:
            self.skip -= 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    parser.close()
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip()
             for line in "".join(parser.parts).split("\n"))
    return "\n".join(line for line in lines if line)
