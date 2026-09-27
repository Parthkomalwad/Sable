"""Agent tools (Phase 3.5, J1): typed actions a model calls by name.

One layer above `policy` and below `agents`: a tool call goes through
`policy.engine.gate()` like a command, and agents call tools through
`registry.call()`.
"""
