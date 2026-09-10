"""Unit tests for `/route why` and router correction logging (I3)."""
from __future__ import annotations

import io

import pytest

from sable.core.config.schema import ShellConfig
from sable.app.repl import _handle_route_builtin, _record_router_correction
from sable.agents.router import Route, explain


@pytest.fixture
def corrections_file(tmp_path, monkeypatch):
    """Redirect ~/.sable/state/ into tmp_path."""
    import sable.core.paths as paths

    state = tmp_path / ".sable" / "state"
    target = state / "router_corrections.tsv"
    monkeypatch.setattr(paths, "STATE_DIR", state)
    monkeypatch.setattr(paths, "ROUTER_CORRECTIONS", target)
    return target


def _rows(path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    rows = []
    with io.open(path, encoding="utf-8") as handle:
        for line in handle:
            text, _, label = line.rstrip("\n").partition("\t")
            rows.append((text, label))
    return rows


class TestRecordCorrection:
    def test_writes_an_input_and_label_row(self, corrections_file):
        _record_router_correction("make deploy", "bash")

        assert _rows(corrections_file) == [("make deploy", "bash")]

    def test_appends_rather_than_overwriting(self, corrections_file):
        _record_router_correction("ls -la", "bash")
        _record_router_correction("what is using port 80", "agentic")

        assert _rows(corrections_file) == [
            ("ls -la", "bash"),
            ("what is using port 80", "agentic"),
        ]

    def test_creates_the_state_directory(self, corrections_file):
        assert not corrections_file.parent.exists()

        _record_router_correction("ls", "bash")

        assert corrections_file.parent.is_dir()

    def test_blank_input_is_not_recorded(self, corrections_file):
        _record_router_correction("   ", "bash")

        assert _rows(corrections_file) == []

    def test_input_is_stripped(self, corrections_file):
        _record_router_correction("  ls -la  ", "bash")

        assert _rows(corrections_file) == [("ls -la", "bash")]

    def test_rows_are_in_corpus_format(self, corrections_file):
        """Corrections use the same shape as tests/fixtures/router_corpus.tsv,
        so they can be folded into the corpus without conversion."""
        _record_router_correction("git push", "bash")

        raw = io.open(corrections_file, encoding="utf-8").read()
        assert raw == "git push\tbash\n"


class TestRouteWhyBuiltin:
    def _run(self, capsys, argument: str) -> str:
        assert _handle_route_builtin(argument, ShellConfig.defaults()) is True
        return capsys.readouterr().out

    def test_reports_route_and_both_scores(self, capsys):
        """The gate example from docs/roadmap-phases.md Phase 0."""
        out = self._run(capsys, 'why "list big files"')

        assert "agentic" in out
        assert "score" in out
        assert "bash 0" in out
        assert "nl 2" in out

    def test_shows_the_rules_that_fired(self, capsys):
        out = self._run(capsys, 'why "show me the largest files"')

        assert "rules that fired" in out
        assert "instructional phrase" in out

    def test_explains_a_bash_line(self, capsys):
        out = self._run(capsys, 'why "ps aux | grep nginx"')

        assert "shell syntax" in out
        assert "known command" in out

    def test_includes_the_decisive_reason(self, capsys):
        out = self._run(capsys, 'why "ls -la"')

        assert "decision" in out

    def test_accepts_an_unquoted_line(self, capsys):
        out = self._run(capsys, "why ls -la")

        assert "ls -la" in out

    def test_accepts_single_quotes(self, capsys):
        out = self._run(capsys, "why 'df -h'")

        assert "df -h" in out

    def test_missing_line_prints_usage(self, capsys):
        out = self._run(capsys, "why")

        assert "usage:" in out

    def test_unknown_subcommand_prints_usage(self, capsys):
        out = self._run(capsys, "explain something")

        assert "usage:" in out

    def test_agrees_with_the_router(self, capsys):
        line = "what is using port 8080"
        out = self._run(capsys, f'why "{line}"')

        assert explain(line).route is Route.AGENTIC
        assert "agentic" in out
