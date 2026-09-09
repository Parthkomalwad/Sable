# Documentation

| Read this when… | File |
|---|---|
| You want to see how a request flows through every component (v4 target) | [architecture-v4.md](architecture-v4.md) |
| You want the current (v0.3) architecture with diagrams | [architecture.md](architecture.md) |
| You need every component, table and data flow spelled out | [architecture-reference.md](architecture-reference.md) |
| You are planning or building v4 work | [vision.md](vision.md) → [roadmap-phases.md](roadmap-phases.md) → [structure.md](structure.md) |
| You are opening a branch or cutting a release | [branching.md](branching.md) |
| You want the original product requirements | [specs/prd-v1.md](specs/prd-v1.md) (v1 shell) · [specs/prd-v3.md](specs/prd-v3.md) (task engine + skills) |
| You want a feature's design rationale | [specs/](specs/): one design doc per feature, dated |
| You want the step-by-step plan a feature was built from | [plans/](plans/): checkbox plans, one commit per task |
| You are curious what was done in v1–v3 | [history/](history/) |

Diagrams live in [assets/](assets/). Public milestones are in the repo-root [ROADMAP.md](../ROADMAP.md); implementation rules for contributors and AI sessions are in [CLAUDE.md](../CLAUDE.md).

## Conventions

- Design docs: `specs/YYYY-MM-DD-<feature>-design.md`. Plans: `plans/YYYY-MM-DD-<feature>.md`. A plan references its spec in the header.
- v4 feature IDs (`A1` … `I12`) are defined once in `vision.md` §5 and reused in branch names, PR templates, and the roadmap.
- Anything that changes agent autonomy must add a line to `THREAT_MODEL.md` (created in Phase 3).
