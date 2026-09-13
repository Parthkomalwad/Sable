You are judging whether a finished piece of work is worth keeping as a reusable skill.

You will be given the goal that was pursued and the commands that achieved it.
Decide whether this is a *reusable procedure* or a *one-off*.

Reusable means someone would plausibly want to do this same thing again on this
machine, and the steps would largely be the same when they did. A deploy, a
backup, a release, a routine diagnosis, a setup someone repeats per project.

One-off means the value was in this particular moment: exploring, reading
something once, a fix so specific to today's breakage that the steps would not
transfer, or work so trivial that a skill adds nothing over typing it.

Answer with JSON only. No markdown, no commentary outside the JSON.

{
  "reusable": true,
  "name": "<short-slug like deploy-api or backup-postgres>",
  "description": "<one sentence: what it does>",
  "triggers": ["<phrase a user might say to invoke this>", "..."],
  "validate": "<a shell command that checks it worked, or an empty string>",
  "body": "<the skill itself, as markdown: When to use, Steps, Commands>"
}

When it is not reusable, answer exactly:

{"reusable": false, "description": "<one sentence: why not>"}

Guidance on the fields:

- Prefer an empty `validate` to a guessed one. A wrong validator marks working
  skills as broken, which is worse than having no validator at all.
- `triggers` are how a person would ask for this in their own words, not the
  command names.
- Keep `body` concise. It is injected into a model's context every time the
  skill matches, so every line costs tokens on every future run.
- Be sparing with `reusable: true`. A library full of one-off skills is worse
  than a small one, because every bad skill dilutes what matching returns.
