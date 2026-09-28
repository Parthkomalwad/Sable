# Sable logo

**The idea:** a shell prompt that asks. The hook of a question mark is the
prompt chevron `>`, and its dot is the terminal cursor. One glyph says what
Sable does: it proposes a command and asks before it runs.

| File | Use |
|---|---|
| `docs/assets/sable-logo-dark.svg`, `-light.svg` | The app icon: the symbol on its tile, 256 x 256 |
| `docs/assets/sable-mark-dark.svg`, `-light.svg` | The lockup: tile, `sable` wordmark and tagline (README header) |

Both are rendered from `scripts/assets/*.template.svg` by
`python scripts/build-assets.py`, one file per theme; edit the template,
never the output.

**Construction.** On a 256 grid: one 36.8-unit round-capped stroke through
(78, 42), (178, 100), (131, 127), (131, 157). The two arms of the chevron sit
at 30 degrees. Under it, a 74 x 35 cursor block with an 8-unit corner radius.
The symbol is centred slightly above the geometric centre.

**Colour.** Violet `#8B7CF6` to `#C4B8FF` on the dark tile, `#6D4AEA` to
`#9B84F5` on the light one. In one colour it works in solid black or white
with no gradient.

**Do not** stretch it, rotate it, add a shadow, or set the chevron at another
angle. Keep clear space of at least the cursor's height around the tile.
Below 16 px, use the tile, which still reads as `?`.
