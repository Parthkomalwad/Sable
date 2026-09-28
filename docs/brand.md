# Sable logo

**Positioning.** Sable is your server's AI operator: talk to it in plain
English, it runs the work, watches the box while you sleep, and asks you
(in the shell or on your phone) before anything risky. Lead with what it
does; the safety model is the proof, not the pitch.

**The idea.** `>.`: a prompt chevron and a status light. The prompt is where
you talk to the machine; the green dot is the operator on duty, still on after
you log off. It departs from the stock `>_` terminal icon on purpose.

| File | Use |
|---|---|
| `docs/assets/sable-logo-dark.svg`, `-light.svg` | App icon: the symbol on its tile, 256 x 256 |
| `docs/assets/sable-mark-dark.svg`, `-light.svg` | Lockup: tile, `sable` wordmark, tagline (README header) |

Both are rendered from `scripts/assets/*.template.svg` by
`python scripts/build-assets.py`, one file per theme; edit the template,
never the output.

**Construction.** On a 256 grid: one 38-unit round-capped stroke through
(62, 60), (130, 128), (62, 196), arms at exactly 45 degrees; a dot of radius
26 at (184, 178), sitting on the chevron's baseline side like a cursor.

**Colour.** Chevron: violet `#C4B8FF` to `#8B7CF6` on dark, `#9B84F5` to
`#6D4AEA` on light. Dot: status green `#5FCB7A` on dark, `#1F8F45` on light.
Two colours with a reason (voice and status); in one colour it works in solid
black or white.

**Do not** stretch or rotate it, add shadows, recolour the dot red or amber
(it would read as an alert), or swap the dot for an underscore. Keep clear
space of at least the dot's diameter around the tile. Tagline: "Your server's
AI operator."
