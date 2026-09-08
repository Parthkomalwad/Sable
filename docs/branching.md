# Branching & release model

Trunk-based. `main` is always installable and CI-green; everything else is short-lived.

## Branches

| Branch | Purpose | Lifetime |
|---|---|---|
| `main` | Releasable trunk. Protected: PR + CI required, no force-push. | permanent |
| `feat/<id>-<slug>` | One roadmap feature, e.g. `feat/A1-event-bus`, `feat/B2-folder-skills` | days–2 weeks |
| `fix/<slug>` | Bug fix | days |
| `refactor/<slug>` | No behaviour change, e.g. `refactor/phase-0.5-agentic-package` | ≤ 1 week |
| `docs/<slug>` | Documentation only | days |
| `release/v0.x` | Only if a release needs stabilising while `main` moves on | until tagged |

Feature IDs (`A1`, `I3` …) come from [vision.md](vision.md); putting them in the branch name links the PR to the roadmap automatically.

## Flow

```
git switch -c feat/A1-event-bus main
# small commits, tests green at each
git push -u origin feat/A1-event-bus
gh pr create --fill          # template asks for feature ID, gate, changelog line
# squash-merge after CI + review
```

- Rebase on `main` before opening the PR; squash-merge so `main` history is one commit per feature.
- A PR that changes what an agent may do autonomously needs a policy-tier default, an audit entry, and a `THREAT_MODEL.md` line (see CONTRIBUTING).
- Delete the branch after merge (GitHub setting: auto-delete head branches).

## Releases

- Version in `pyproject.toml`; SemVer once `1.0`, `0.x` before.
- Tag on `main`: `git tag -a v0.4.0 -m "v0.4.0 foundations" && git push origin v0.4.0`. CI builds the playground image and attaches nothing else for now; release notes come from `CHANGELOG.md`.
- A roadmap milestone closes when every gate in `docs/roadmap-phases.md` for that phase passes in the playground.

## Protection settings to enable on GitHub (one-time)

*Settings → Branches → Add rule for `main`:* require a pull request, require status checks `Conventions`, `Unit tests`, `Integration`, require branches up to date, block force pushes, block deletions. *Settings → General:* automatically delete head branches.
