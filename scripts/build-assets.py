#!/usr/bin/env python3
"""Render the README's animated SVGs from templates, once per theme.

An SVG loaded through <img> cannot see the page's colour scheme: the
prefers-color-scheme media query inside it is always evaluated against the
image's own context, which browsers force to light. So a themed asset has to
be two files, swapped by <picture> in the README. Both come from one template
here, so the pair cannot drift.

    python scripts/build-assets.py [--check]

--check re-renders into memory and fails if docs/assets/ is out of date, which
is what CI runs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "scripts" / "assets"
OUT = ROOT / "docs" / "assets"

THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "BG0": "#0B0F14", "BG1": "#131C27",
        "CHROME": "#080B10", "DOT": "#2C3541",
        "CARD0": "#161F2B", "CARD1": "#111823",
        "TILE0": "#151C25", "TILE1": "#0B0F14",
        "RULE": "#1F2730", "RULE2": "#2E3947",
        "INK": "#D8DEE6", "HI": "#FFFFFF", "DIM": "#7C8794",
        "ACC": "#8B7CF6", "ACC2": "#C4B8FF",
        "OK": "#5FCB7A", "WARN": "#E7B24B", "WARNBG": "#2A2113",
    },
    "light": {
        "BG0": "#FFFFFF", "BG1": "#F2F4F8",
        "CHROME": "#E8ECF2", "DOT": "#C3CBD6",
        "CARD0": "#FFFFFF", "CARD1": "#F4F6FA",
        "TILE0": "#F4F2FF", "TILE1": "#E6E2FB",
        "RULE": "#DCE1E9", "RULE2": "#BFC8D4",
        "INK": "#1C2430", "HI": "#0A0E14", "DIM": "#5C6775",
        "ACC": "#6D4AEA", "ACC2": "#9B84F5",
        "OK": "#1F8F45", "WARN": "#9A6608", "WARNBG": "#FCF3DE",
    },
}


def render(template: str, theme: dict[str, str]) -> str:
    out = template
    for key, value in theme.items():
        out = out.replace(f"__{key}__", value)
    if "__" in out:
        leftover = {
            frag.split("__")[0]
            for frag in out.split("__")[1::2]
        }
        raise SystemExit(f"unsubstituted placeholders: {sorted(leftover)}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="fail if docs/assets is out of date")
    args = parser.parse_args()

    templates = sorted(TEMPLATES.glob("*.template.svg"))
    if not templates:
        raise SystemExit(f"no templates in {TEMPLATES}")

    OUT.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []

    # Newlines are handled explicitly throughout. The rendered bytes are
    # compared against what is committed, and Python's text mode would write
    # CRLF on Windows and LF on the Linux CI runner from the same template,
    # so --check would fail on one platform or the other. .gitattributes
    # pins *.svg to LF to match.
    for path in templates:
        name = path.name.removesuffix(".template.svg")
        source = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        for theme_name, theme in THEMES.items():
            target = OUT / f"sable-{name}-{theme_name}.svg"
            rendered = render(source, theme)
            current = target.read_text(encoding="utf-8", newline="") if target.exists() else None
            if current == rendered:
                continue
            if args.check:
                stale.append(target.relative_to(ROOT).as_posix())
            else:
                target.write_text(rendered, encoding="utf-8", newline="\n")
                size = len(rendered.encode("utf-8"))
                print(f"  {target.relative_to(ROOT).as_posix()}  {size:,} bytes")

    if stale:
        print("out of date, run python scripts/build-assets.py:", file=sys.stderr)
        for item in stale:
            print(f"  {item}", file=sys.stderr)
        return 1

    print("assets up to date" if args.check else "done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
