"""Ctrl+B sends the next line straight to bash, and never crashes.

It never worked: the key binding set a `global _bypass_next` in the prompt
module while the REPL read its own `_bypass_next`, so Ctrl+B printed
"[bash mode]" and changed nothing. With text already typed it also called
`_record_router_correction`, which that module never imported, and raised
NameError. Found while reviewing Phase 4 Task 5.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from sable.ui.prompt import session


def _press_ctrl_b(typed: str) -> None:
    kb = session._make_key_bindings()
    (binding,) = [b for b in kb.bindings if b.keys and getattr(b.keys[0], "value", b.keys[0]) == "c-b"]
    event = SimpleNamespace(app=SimpleNamespace(current_buffer=SimpleNamespace(text=typed)))
    binding.handler(event)


def test_ctrl_b_with_text_typed_does_not_raise():
    session.take_bypass()               # start clean
    _press_ctrl_b("git statsu")         # raised NameError before the fix
    assert session.take_bypass() is True


def test_the_request_is_taken_once():
    session.take_bypass()
    _press_ctrl_b("")
    assert session.take_bypass() is True
    assert session.take_bypass() is False


def test_the_repl_takes_the_request_and_records_the_correction():
    """The REPL imports ptyprocess, so read its source rather than import it."""
    src = (Path(session.__file__).resolve().parents[2] / "app" / "repl.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "take_bypass" in calls
    assert '_record_router_correction(line, "bash"' in src
