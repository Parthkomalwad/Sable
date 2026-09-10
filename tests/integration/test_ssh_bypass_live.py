"""Live SSH bypass test: real sshd, real ssh/scp clients, real login shell.

This is the test that Phase 0 needed and did not have. `shell/main.py` is the
user's login shell, and sshd runs it as `sable -c "<command>"` for
`ssh host cmd`, for scp and for rsync, WITHOUT setting SSH_ORIGINAL_COMMAND.
A guard that checks only the environment variable therefore misses the common
case, and every one of those transfers lands in the interactive REPL and hangs.

`tests/unit/test_ssh_bypass.py` covers the guard's logic in isolation. This
file covers the thing the unit test cannot: that the guard is wired into the
real login shell that sshd actually invokes.

Requires: an environment with openssh-server, i.e. the playground image.
    docker run --rm -v "$PWD:/app" sable-playground \
        bash -c "cd /app && PYTHONPATH=/app pytest tests/integration/test_ssh_bypass_live.py -q"
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest

pytestmark = pytest.mark.integration

# The banner main.py prints when it reaches the interactive path. If any of
# this shows up in the output of a non-interactive command, the bypass failed
# and we are talking to the REPL instead of to bash.
BANNER_MARKERS = ("Sable", "type naturally", "session resumed")

SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
]


def _have(*binaries: str) -> bool:
    return all(shutil.which(b) for b in binaries)


requires_ssh = pytest.mark.skipif(
    not _have("ssh", "scp", "sshd") or os.name != "posix",
    reason="needs a POSIX host with openssh client and server (the playground image)",
)


def _sshd_is_up(port: int = 22) -> bool:
    """True once something answers on the SSH port with an SSH banner."""
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as sock:
            sock.settimeout(2)
            return sock.recv(4).startswith(b"SSH-")
    except OSError:
        return False


@pytest.fixture(scope="module")
def sshd() -> None:
    """Make sure sshd is listening, starting it if the entrypoint did not.

    The playground entrypoint starts sshd, but pytest is also run directly
    against the image (`playground.ps1 tests`), where the entrypoint's tmux
    path is never reached. Starting it here keeps the test self-contained.
    """
    if not _sshd_is_up():
        os.makedirs("/run/sshd", exist_ok=True)
        subprocess.run(["/usr/sbin/sshd"], check=True)
        for _ in range(50):
            if _sshd_is_up():
                break
            time.sleep(0.2)
    if not _sshd_is_up():
        pytest.skip("sshd would not start in this environment")


def _login_shell_is_sable() -> bool:
    """The bypass only matters when Sable is the login shell. Confirm it is.

    Without this the whole file would pass trivially against /bin/bash and
    tell us nothing.
    """
    try:
        import pwd

        return "sable" in pwd.getpwuid(os.getuid()).pw_shell
    except (KeyError, ImportError):
        return False


@pytest.fixture(scope="module")
def ssh_target(sshd) -> str:
    if not _login_shell_is_sable():
        pytest.skip("login shell is not sable, the bypass is not under test here")
    return "localhost"


def _ssh(target: str, command: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", *SSH_OPTS, target, command],
        capture_output=True, text=True, timeout=timeout,
    )


@requires_ssh
class TestSSHBypassLive:
    def test_remote_command_output_is_exact(self, ssh_target):
        """`ssh host 'echo bypass-ok'` prints exactly that, nothing else.

        This is the assertion that fails against the env-only guard: with the
        old code the REPL starts, prints its banner and waits on stdin, so
        stdout is either the banner or empty.
        """
        result = _ssh(ssh_target, "echo bypass-ok")
        assert result.returncode == 0, f"ssh failed: {result.stderr}"
        assert result.stdout.strip() == "bypass-ok"

    def test_no_sable_banner_leaks_into_remote_output(self, ssh_target):
        """No trace of the interactive shell in a non-interactive session."""
        result = _ssh(ssh_target, "echo bypass-ok")
        combined = result.stdout + result.stderr
        for marker in BANNER_MARKERS:
            assert marker not in combined, (
                f"interactive REPL output {marker!r} leaked into a non-interactive "
                f"SSH command, the bypass did not fire:\n{combined}"
            )

    def test_exit_code_propagates(self, ssh_target):
        """A non-zero remote exit code reaches the client.

        Anything that swallows the status breaks `ssh host make` in CI and
        `git push` over SSH, both of which branch on it.
        """
        result = _ssh(ssh_target, "exit 42")
        assert result.returncode == 42

    def test_stderr_stays_on_stderr(self, ssh_target):
        """Streams are not merged, so `ssh host cmd 2>/dev/null` still works."""
        result = _ssh(ssh_target, "echo out; echo err 1>&2")
        assert result.stdout.strip() == "out"
        assert "err" in result.stderr

    def test_stdin_is_piped_through(self, ssh_target):
        """Piping into a remote command works, which rsync and git rely on."""
        result = subprocess.run(
            ["ssh", *SSH_OPTS, ssh_target, "cat"],
            input="piped-payload\n", capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"ssh failed: {result.stderr}"
        assert result.stdout.strip() == "piped-payload"

    def test_scp_transfers_file_contents(self, ssh_target, tmp_path):
        """scp round-trips a file with its contents intact.

        scp is the canonical victim of a broken bypass: the remote
        `scp -t` never runs, so the client hangs until it times out.
        """
        source = tmp_path / "payload.txt"
        body = "scp-payload-line-1\nscp-payload-line-2\n"
        source.write_text(body)
        remote = f"/tmp/sable-scp-{os.getpid()}.txt"

        push = subprocess.run(
            ["scp", *SSH_OPTS, str(source), f"{ssh_target}:{remote}"],
            capture_output=True, text=True, timeout=60,
        )
        assert push.returncode == 0, f"scp push failed: {push.stderr}"

        try:
            back = tmp_path / "returned.txt"
            pull = subprocess.run(
                ["scp", *SSH_OPTS, f"{ssh_target}:{remote}", str(back)],
                capture_output=True, text=True, timeout=60,
            )
            assert pull.returncode == 0, f"scp pull failed: {pull.stderr}"
            assert back.read_text() == body
        finally:
            _ssh(ssh_target, f"rm -f {remote}")

    def test_sftp_subsystem_still_works(self, ssh_target, tmp_path):
        """`scp -O` forces the legacy path; the default uses the SFTP
        subsystem, which sshd runs itself rather than through the login
        shell. Covering both means a future change to either path is caught.
        """
        source = tmp_path / "sftp.txt"
        source.write_text("sftp-payload\n")
        remote = f"/tmp/sable-sftp-{os.getpid()}.txt"
        push = subprocess.run(
            ["scp", *SSH_OPTS, "-O", str(source), f"{ssh_target}:{remote}"],
            capture_output=True, text=True, timeout=60,
        )
        assert push.returncode == 0, f"scp -O push failed: {push.stderr}"
        try:
            check = _ssh(ssh_target, f"cat {remote}")
            assert check.stdout.strip() == "sftp-payload"
        finally:
            _ssh(ssh_target, f"rm -f {remote}")

    def test_env_var_path_also_bypasses(self, ssh_target):
        """The ForceCommand / authorized_keys `command=` path.

        Here sshd puts the real command in SSH_ORIGINAL_COMMAND. Simulated by
        invoking the login shell directly with no `-c`, which is the shape
        that guard branch has to handle.
        """
        result = subprocess.run(
            ["/usr/local/bin/sable"],
            env={**os.environ, "SSH_ORIGINAL_COMMAND": "echo env-bypass-ok"},
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"login shell failed: {result.stderr}"
        assert result.stdout.strip() == "env-bypass-ok"
