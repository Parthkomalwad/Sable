# Security Policy

AgenticOS runs as a login shell and can execute commands proposed by a language model. Treat every report seriously.

## Reporting a vulnerability

Email **parthkomalwad99@gmail.com** with the subject `AgenticOS security`. Include reproduction steps, affected version/commit, and impact. You will get an acknowledgement within 72 hours. Please do not open a public issue.

## Scope: what we consider a vulnerability

- Any way for content the model reads (command output, files, web pages, MCP results) to cause a command to execute without the user's confirmation tier being honoured (prompt/output injection).
- Sandbox escape from a task workspace (bwrap or bash-wrapper mode).
- Policy bypass: a `deny`-tier command executing, or a `confirm`-tier command executing without confirmation.
- Secret leakage: API keys or `$SECRET:` values appearing in model requests, audit logs, telemetry, or sub-agent hand-offs.
- SSH bypass failures that let `SSH_ORIGINAL_COMMAND` reach the agentic path.
- Privilege escalation between Linux users via shared state (`/etc/agentic`, `/var/log/agentic-shell`).

## Out of scope

- Behaviour of third-party LLM providers or MCP servers themselves.
- Issues requiring an attacker who already has the user's shell.

## Threat model

`docs/THREAT_MODEL.md` (Phase 3 deliverable) documents assets, trust boundaries, and mitigations. Until it lands, the operative rules are: model output is never executed without the policy engine; command output returned to the model is untrusted data; agents never run as root.

## Supported versions

Only `main` receives fixes until 1.0.
