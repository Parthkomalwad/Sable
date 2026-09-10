"""Unit tests for /tour and the wizard's confirm-tier explanation (I10)."""
from __future__ import annotations

import re

import pytest

from sable.app.tour import _STEPS, run_tour

_ANSI = re.compile(chr(27) + r"\[[0-9;]*m")


@pytest.fixture(autouse=True)
def wide_console(monkeypatch):
    """Render at a fixed wide width with no colour.

    Rich wraps to the terminal, and pytest's captured terminal is narrow
    enough to truncate panel titles, so assertions on content would fail for
    reasons that have nothing to do with the tour.
    """
    import sable.core.config.wizard as wizard
    import sable.app.tour as tour
    from rich.console import Console

    for module in (tour, wizard):
        monkeypatch.setattr(module, "console", Console(width=200, no_color=True, legacy_windows=False))


def _text(capsys) -> str:
    return _ANSI.sub("", capsys.readouterr().out)


class TestTourContent:
    def test_covers_every_promised_topic(self, capsys):
        """Phase 0 asks the tour to cover routing, confirm, YES, /task,
        /skill, /bash and /exit."""
        run_tour(interactive=False)
        out = _text(capsys).lower()

        for topic in ["rout", "confirm", "yes", "/task", "/skill", "/bash", "/exit"]:
            assert topic in out, f"tour never mentions {topic}"

    def test_has_seven_steps(self):
        assert len(_STEPS) == 7

    def test_every_step_is_numbered_in_its_title(self):
        for index, step in enumerate(_STEPS, start=1):
            assert step.title.startswith(f"{index} of 7")

    def test_mentions_route_why_for_routing_questions(self, capsys):
        run_tour(interactive=False)
        assert "/route why" in _text(capsys)

    def test_explains_how_to_leave(self, capsys):
        run_tour(interactive=False)
        out = _text(capsys)
        assert "sable off" in out
        assert "Ctrl+B" in out


class TestTourPaging:
    def test_non_interactive_prints_everything_without_prompting(self, capsys):
        def _fail(_):
            raise AssertionError("should not prompt when interactive=False")

        run_tour(interactive=False, prompt=_fail)
        assert "7 of 7" in _text(capsys)

    def test_interactive_advances_on_enter(self, capsys):
        answers = iter([""] * 10)
        run_tour(interactive=True, prompt=lambda _: next(answers))
        assert "7 of 7" in _text(capsys)

    def test_q_stops_early(self, capsys):
        run_tour(interactive=True, prompt=lambda _: "q")
        out = _text(capsys)
        assert "1 of 7" in out
        assert "7 of 7" not in out
        assert "tour ended" in out

    def test_ctrl_c_stops_cleanly(self, capsys):
        def _interrupt(_):
            raise KeyboardInterrupt

        run_tour(interactive=True, prompt=_interrupt)
        assert "tour ended" in _text(capsys)

    def test_eof_stops_cleanly(self, capsys):
        def _eof(_):
            raise EOFError

        run_tour(interactive=True, prompt=_eof)
        assert "tour ended" in _text(capsys)

    def test_last_step_does_not_wait_for_input(self, capsys):
        calls = []

        def _count(_):
            calls.append(1)
            return ""

        run_tour(interactive=True, prompt=_count)
        assert len(calls) == len(_STEPS) - 1


class TestTourRunsWithoutABackend:
    def test_needs_no_llm_or_api_key(self, capsys, monkeypatch):
        """The tour explains, it never calls a model, so it works with
        SABLE_MOCK_LLM=1 or with no backend configured at all."""
        monkeypatch.setenv("SABLE_MOCK_LLM", "1")

        def _explode(*args, **kwargs):
            raise AssertionError("the tour must not build a backend")

        import sable.app.repl

        monkeypatch.setattr(sable.app.repl, "_build_backend", _explode)
        run_tour(interactive=False)

        assert "7 of 7" in _text(capsys)


class TestWizardConfirmTiers:
    def test_explains_all_three_tiers(self, capsys):
        from sable.core.config.wizard import _explain_confirm_tiers

        _explain_confirm_tiers()
        out = _text(capsys)

        assert "read-only" in out
        assert "destructive" in out
        assert "YES" in out

    def test_says_nothing_runs_unseen(self, capsys):
        from sable.core.config.wizard import _explain_confirm_tiers

        _explain_confirm_tiers()

        assert "never runs a command you have not seen" in _text(capsys)

    def test_points_at_the_tour(self, capsys):
        from sable.core.config.wizard import _explain_confirm_tiers

        _explain_confirm_tiers()

        assert "/tour" in _text(capsys)
