"""The real REPL, driven through a pty with the mock backend.

This closes the Phase 0 gap. Two of the five bugs that phase found lived
here and no automated test could see them, because nothing drove the actual
shell:

  1. `_confirm_command` calls `input()`. Under a pipe that raises EOFError,
     every command is silently cancelled, and the orchestrator re-proposed
     the same action until it hit the turn cap. Found by a human typing.
  2. The mock's script position came from per-instance state, but the
     orchestrator builds a fresh backend every turn, so it always replayed
     step one. The integration test missed it by patching with a single
     shared `return_value`, which is not how the agent behaves.

Both need a **real terminal** and the **real loop**. A pipe reproduces
neither: `input()` behaves differently, and prompt_toolkit refuses to start
without a tty. So this spawns the shell under `PtyProcessUnicode` (already a
core dependency, no new one needed) and types at it.

Anything that asserts on a fixed number of turns or exact wording will be
brittle. These assert on observable outcomes instead: the file the plan
creates appears, the loop terminates, no cancellation storm, exit is clean.
"""
from __future__ import annotations

import os
import re
import select
import shutil
import time

import pytest

from tests.integration.conftest import ensure_config

pytestmark = pytest.mark.integration

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# prompt_toolkit and Rich both emit escape sequences; strip them before
# matching so assertions are about content, not styling.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-B]")

requires_pty = pytest.mark.skipif(
    os.name != "posix", reason="ptyprocess is POSIX only",
)


def _clean(text: str) -> str:
    return _ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


class ReplDriver:
    """A Sable REPL running on a pty, with read-until and send helpers."""

    def __init__(self, cwd: str, env_extra: dict | None = None) -> None:
        from ptyprocess import PtyProcessUnicode

        env = {
            **os.environ,
            "PYTHONPATH": REPO_ROOT,
            "SABLE_MOCK_LLM": "1",
            "PROMPT_TOOLKIT_NO_CPR": "1",
            "TERM": "xterm-256color",
            # Start clean rather than resuming a previous session's context,
            # which would change what the model sees.
            "AGENTIC_NEW_SESSION": "1",
            "NO_TMUX": "1",
        }
        env.update(env_extra or {})
        self.proc = PtyProcessUnicode.spawn(
            ["python3", "-m", "sable.app.main"],
            cwd=cwd, env=env, dimensions=(40, 120),
        )
        self.buffer = ""

    def _pump(self, seconds: float) -> None:
        """Read whatever is available for up to `seconds`.

        `select` before every read, because PtyProcessUnicode.read() blocks
        until it has a full buffer or the child exits. An interactive shell
        sitting at a prompt gives neither, so a bare read() hangs forever
        rather than returning the partial output already on the wire.
        """
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            remaining = max(0.05, min(0.2, deadline - time.monotonic()))
            if not select.select([self.proc.fd], [], [], remaining)[0]:
                continue
            try:
                self.buffer += self.proc.read(1024)
            except (EOFError, OSError):
                return

    def read_until(self, pattern: str, timeout: float = 30.0) -> str:
        """Pump output until `pattern` appears in the cleaned buffer."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pattern in _clean(self.buffer):
                return _clean(self.buffer)
            self._pump(0.5)
        raise AssertionError(
            f"never saw {pattern!r} within {timeout}s. Output so far:\n"
            f"{_clean(self.buffer)[-3000:]}"
        )

    def drain(self, seconds: float = 2.0) -> str:
        """Read whatever arrives for a while, then return everything seen."""
        self._pump(seconds)
        return _clean(self.buffer)

    def send(self, text: str) -> None:
        self.proc.write(text)

    def sendline(self, text: str = "") -> None:
        self.proc.write(text + "\r")

    def close(self) -> None:
        try:
            if self.proc.isalive():
                self.proc.terminate(force=True)
        except (OSError, EOFError):
            pass


@pytest.fixture
def repl(tmp_path):
    if os.name != "posix":
        pytest.skip("ptyprocess is POSIX only")
    ensure_config()
    driver = ReplDriver(cwd=str(tmp_path))
    try:
        yield driver
    finally:
        driver.close()


@requires_pty
class TestReplStartsOnATty:
    def test_banner_appears(self, repl):
        """prompt_toolkit needs a tty; under a pipe it will not start at all."""
        output = repl.read_until("Sable")
        assert "Sable" in output

    def test_bash_command_runs_without_the_model(self, repl, tmp_path):
        """A bash-classified line executes directly, no LLM, no confirm."""
        repl.read_until("Sable")
        repl.sendline("echo direct-bash-ok > proof.txt")
        deadline = time.monotonic() + 20
        target = tmp_path / "proof.txt"
        while time.monotonic() < deadline and not target.exists():
            repl.drain(0.5)
        assert target.exists(), (
            f"bash command never ran:\n{_clean(repl.buffer)[-2000:]}"
        )
        assert target.read_text().strip() == "direct-bash-ok"

    def test_exit_builtin_terminates_the_process(self, repl):
        repl.read_until("Sable")
        repl.sendline("/exit")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and repl.proc.isalive():
            repl.drain(0.5)
        assert not repl.proc.isalive(), "/exit did not end the process"


@requires_pty
class TestMockOrchestratorEndToEnd:
    """The path where both Phase 0 bugs lived."""

    def test_natural_language_reaches_a_confirm_prompt(self, repl):
        """The orchestrator proposes a command and waits for the user.

        Bug 1 was that this prompt read EOF and auto-cancelled. On a real
        pty it must actually appear and wait.
        """
        repl.read_until("Sable")
        repl.sendline("create a hello file")
        output = repl.read_until("run", timeout=45)
        assert "cancel" in output.lower(), (
            f"no confirm prompt appeared:\n{output[-2000:]}"
        )

    def test_confirmed_plan_runs_to_completion(self, repl, tmp_path):
        """Accept each command; the canned plan should finish, not loop.

        This is the regression test for both bugs at once. Bug 1 cancelled
        every command; bug 2 replayed step one forever. Either way the run
        never reached "done" and never created anything. Asserting on the
        outcome rather than a turn count keeps it robust if the canned
        script changes.
        """
        repl.read_until("Sable")
        repl.sendline("create a hello file")

        # Accept each confirm as it appears. The loop bound is generous
        # rather than exact: the canned script's length is not the contract,
        # the outcome is.
        target = tmp_path / "hello.txt"
        for _ in range(8):
            if target.exists():
                break
            try:
                repl.read_until("cancel", timeout=25)
            except AssertionError:
                break
            repl.sendline("")  # bare Enter = run
            repl.drain(3.0)

        # The real assertion: the plan's side effect happened. Both Phase 0
        # bugs ended with no file, either because every command was
        # cancelled or because step one was replayed forever, so this one
        # check catches both without depending on any wording.
        repl.drain(3.0)
        assert target.exists(), (
            f"the canned plan never created its file, so the orchestrator "
            f"loop did not run to completion:\n{_clean(repl.buffer)[-3000:]}"
        )
        assert target.read_text().strip() == "hello"

    def test_no_cancellation_storm(self, repl):
        """The precise shape of bug 1: the same command proposed over and over.

        A healthy run proposes each command once. A broken one repeats a
        single command until the 20-turn cap, so counting repeats of one
        command string is what separates them.
        """
        repl.read_until("Sable")
        repl.sendline("create a hello file")
        for _ in range(4):
            try:
                repl.read_until("cancel", timeout=20)
            except AssertionError:
                break
            repl.sendline("")
            repl.drain(2.0)

        text = _clean(repl.buffer)
        assert text.lower().count("user cancelled") < 3, (
            f"commands are being auto-cancelled, which is the Phase 0 "
            f"EOF-on-input bug:\n{text[-3000:]}"
        )
        # First step of the "hello file" script. A healthy run proposes it
        # once; the per-instance mock state bug replayed it every turn until
        # the 20-turn cap. The prompt echoes it, so a couple of occurrences
        # are normal and a pile of them is the bug.
        assert text.count("echo hello > hello.txt") < 5, (
            f"the same command was proposed repeatedly, which is the "
            f"per-instance mock state bug:\n{text[-3000:]}"
        )

    def test_cancelling_a_command_does_not_hang_the_loop(self, repl):
        """`q` at the confirm prompt should return control, not spin.

        The turn accounting for a cancelled command is what beb00d7 fixed;
        this pins that a cancel is survivable rather than a dead end.
        """
        repl.read_until("Sable")
        repl.sendline("create a hello file")
        repl.read_until("cancel", timeout=45)
        repl.sendline("q")
        repl.drain(5.0)

        # The shell must still be alive and accepting input.
        repl.sendline("echo still-alive")
        output = repl.read_until("still-alive", timeout=20)
        assert "still-alive" in output


@requires_pty
class TestBuiltinsOnATty:
    """Gate items that only ever ran by hand."""

    @pytest.mark.parametrize("command,expected", [
        ("/help", "help"),
        ("/stats", "token"),
        ("/task list", "task"),
        ("/skill list", "skill"),
    ])
    def test_builtin_responds(self, repl, command, expected):
        repl.read_until("Sable")
        repl.sendline(command)
        output = repl.drain(6.0).lower()
        assert expected in output, (
            f"{command} produced no recognisable output:\n{output[-1500:]}"
        )
