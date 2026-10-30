"""RED tests: the daemon's stdout reaches the journal line by line.

At the ref the ``__main__`` block rewraps ``sys.stdout`` in an ``io.TextIOWrapper`` with no
``line_buffering``, so under systemd (stdout is a pipe) the daemon's prints sit in an 8 KB block
and reach the journal late. Contract:

  - ``timing_log.make_daemon_stdout(buffer)`` returns a UTF-8 wrapper with replacement for
    anything unencodable, that hands each completed line to the buffer and flushes it.
  - ``timing_log.configure_daemon_output()`` is the first statement of the daemon's ``__main__``
    block (before the singleton is taken), so no rewrap of ``sys.stdout`` remains in ``app.py``.
  - A child process that calls it delivers each line while the child is still running, and a
    line printed more than a moment before a SIGKILL is in the pipe.
  - The engine's own output keeps going to the daemon log file through the same handle for
    stdout and stderr, as at the ref.

The in-process tests drive the real ``trigger_alpha_bot`` over a recording raw buffer, and
observe what was delivered at the moment the engine is spawned or the cycle is published, so a
wrapper that holds the line back fails them. The child-process tests are the real-entrypoint
check: they run the same two calls the ``__main__`` block runs. The engine process is replaced
at ``subprocess.run`` (its process boundary); no producer data is involved.
"""

from __future__ import annotations

import ast
import io
import os
import pathlib
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app as app_module
import timing_log

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_APP_SOURCE = REPO_ROOT / "app.py"
_TRIGGER_LINE = re.compile(r"^\[\d\d:\d\d:\d\d\] Triggering Alpha Bot\.\.\.$")
# Longest the parent waits for a child's line.  The assertion is on order (the line arrives while
# the child is still alive), never on a tight bound, so a loaded runner cannot flake it.
_CHILD_WAIT_S = 10.0


class RecordingBuffer(io.BufferedIOBase):
    """The raw side of a text wrapper. ``delivered`` is what a flush has handed onward."""

    def __init__(self) -> None:
        super().__init__()
        self._written = bytearray()
        self._delivered = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        self._written += bytes(data)
        return len(data)

    def flush(self) -> None:
        self._delivered = bytearray(self._written)

    def delivered_bytes(self) -> bytes:
        return bytes(self._delivered)

    def delivered_lines(self) -> list[str]:
        return self.delivered_bytes().decode("utf-8", "replace").splitlines()


@pytest.fixture()
def daemon_stdout(monkeypatch):
    """sys.stdout replaced by the daemon's wrapper over a recording buffer."""
    buffer = RecordingBuffer()
    wrapper = timing_log.make_daemon_stdout(buffer)
    monkeypatch.setattr(sys, "stdout", wrapper)
    return buffer


@pytest.fixture()
def engine_spawns(monkeypatch, tmp_path, daemon_stdout):
    """trigger_alpha_bot with the engine replaced; each spawn records what was delivered."""
    spawns: list[dict] = []
    exit_codes: list[int] = []

    def fake_run(cmd, **kwargs):
        spawns.append({"delivered": daemon_stdout.delivered_lines(), "kwargs": kwargs})
        code = exit_codes.pop(0) if exit_codes else 0
        if code:
            raise subprocess.CalledProcessError(code, cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(app_module, "LOG_FILE", str(tmp_path / "engine.log"))
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module, "_publish_cycle", lambda record: None)
    return SimpleNamespace(calls=spawns, exit_codes=exit_codes)


class TestPrintedLinesAreDelivered:
    def test_the_trigger_line_is_delivered_before_the_engine_is_spawned(self, engine_spawns):
        app_module.trigger_alpha_bot()

        (spawn,) = engine_spawns.calls
        assert any(_TRIGGER_LINE.match(line) for line in spawn["delivered"]), (
            "the trigger line must already be in the journal pipe when the engine starts, not "
            f"held in an 8 KB block; delivered at spawn: {spawn['delivered']!r}"
        )

    def test_the_failure_line_is_delivered_before_the_next_minutes_trigger_line(
        self, engine_spawns
    ):
        engine_spawns.exit_codes.append(1)
        app_module.trigger_alpha_bot()
        app_module.trigger_alpha_bot()

        _first, second = engine_spawns.calls
        kinds = [
            "trigger"
            if _TRIGGER_LINE.match(line)
            else "failed"
            if "Execution failed" in line
            else "other"
            for line in second["delivered"]
        ]
        assert [k for k in kinds if k != "other"] == ["trigger", "failed", "trigger"], (
            "the failed run's line must reach the journal before the next trigger line; "
            f"delivered at the second spawn: {second['delivered']!r}"
        )

    def test_the_engine_output_goes_to_one_log_file_handle_for_stdout_and_stderr(
        self, engine_spawns
    ):
        app_module.trigger_alpha_bot()

        (spawn,) = engine_spawns.calls
        out, err = spawn["kwargs"]["stdout"], spawn["kwargs"]["stderr"]
        assert out is err, "the engine's stdout and stderr must share one handle, as at the ref"
        assert out.name == app_module.LOG_FILE, "the engine writes to the daemon log, as at the ref"


class TestEngineOutputKeepsItsOwnDestination:
    def test_the_engine_rewrap_still_reaches_the_log_file_when_the_run_ends(
        self, monkeypatch, tmp_path
    ):
        """The engine rewraps its own stdout (alpha_bot_execution.py), so its lines arrive when
        each run ends; no daemon-side setting changes that, and nothing here may break it."""
        log_file = tmp_path / "daemon.log"
        real_run = subprocess.run
        engine_stand_in = (
            "import io, sys\n"
            "sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')\n"
            "print('Alpha Bot Waking Up...')\n"
        )

        def run_stand_in(cmd, **kwargs):
            return real_run([sys.executable, "-c", engine_stand_in], **kwargs)

        monkeypatch.setattr(app_module, "LOG_FILE", str(log_file))
        monkeypatch.setattr(app_module.subprocess, "run", run_stand_in)
        monkeypatch.setattr(app_module, "_publish_cycle", lambda record: None)

        app_module.trigger_alpha_bot()

        assert "Alpha Bot Waking Up..." in log_file.read_text(encoding="utf-8")


class TestDaemonStdoutEncoding:
    def test_it_is_a_utf8_wrapper_that_replaces_what_it_cannot_encode(self):
        buffer = RecordingBuffer()
        wrapper = timing_log.make_daemon_stdout(buffer)

        assert wrapper.encoding.lower().replace("_", "-") == "utf-8"
        assert wrapper.errors == "replace"
        assert wrapper.line_buffering is True

    def test_a_non_latin1_character_and_a_lone_surrogate_are_written_as_the_daemon_always_did(
        self,
    ):
        buffer = RecordingBuffer()
        wrapper = timing_log.make_daemon_stdout(buffer)
        text = "arrow \u2192 and a lone surrogate \ud800 end\n"

        wrapper.write(text)

        assert buffer.delivered_bytes() == text.encode("utf-8", "replace")


class TestDaemonEntryPoint:
    @staticmethod
    def _main_block() -> list[ast.stmt]:
        tree = ast.parse(_APP_SOURCE.read_text(encoding="utf-8"))
        for node in tree.body:
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"
            ):
                return node.body
        raise AssertionError("app.py has no __main__ block")

    @staticmethod
    def _called_name(stmt: ast.stmt) -> str | None:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            func = stmt.value.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                return f"{func.value.id}.{func.attr}"
            if isinstance(func, ast.Name):
                return func.id
        return None

    def test_configuring_the_output_is_the_first_statement_of_the_main_block(self):
        body = self._main_block()

        assert self._called_name(body[0]) == "timing_log.configure_daemon_output", (
            "the __main__ block must configure daemon output before anything prints, "
            "including the singleton check"
        )
        assert [self._called_name(s) for s in body].index("_acquire_daemon_singleton") > 0

    def test_app_imports_the_timing_module_at_module_level(self):
        tree = ast.parse(_APP_SOURCE.read_text(encoding="utf-8"))
        imported = {
            alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names
        }
        assert "timing_log" in imported

    def test_no_text_wrapper_is_built_in_app_py(self):
        tree = ast.parse(_APP_SOURCE.read_text(encoding="utf-8"))
        wrappers = [
            node.lineno
            for node in ast.walk(tree)
            if (isinstance(node, ast.Attribute) and node.attr == "TextIOWrapper")
            or (isinstance(node, ast.Name) and node.id == "TextIOWrapper")
        ]
        assert wrappers == [], (
            "the one stdout rewrap lives in timing_log; app.py builds none "
            f"(found at lines {wrappers})"
        )


# ---------------------------------------------------------------------------
# Real-entrypoint check: a child process running the __main__ block's two calls.
# ---------------------------------------------------------------------------

_CHILD_SOURCE = """\
import sys, time
import timing_log

timing_log.configure_daemon_output()
print("[10:44:00] Triggering Alpha Bot...")
print("second line")
sys.stdin.readline()
timing_log.emit("probe", "ok", n=1)
time.sleep(120)
"""


class _Child:
    """A child process whose stdout is a pipe read by a thread into a queue."""

    def __init__(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _CHILD_SOURCE],
            cwd=REPO_ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.lines: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        for raw in iter(self.proc.stdout.readline, b""):
            self.lines.put(raw.decode("utf-8", "replace").rstrip("\r\n"))

    def next_line(self, timeout: float = _CHILD_WAIT_S) -> str | None:
        try:
            return self.lines.get(timeout=timeout)
        except queue.Empty:
            return None

    def release(self) -> None:
        self.proc.stdin.write(b"\n")
        self.proc.stdin.flush()

    def kill_and_drain(self) -> list[str]:
        self.proc.kill()
        self._reader.join(timeout=_CHILD_WAIT_S)
        rest: list[str] = []
        while not self.lines.empty():
            rest.append(self.lines.get_nowait())
        return rest


@pytest.fixture()
def child():
    c = _Child()
    try:
        yield c
    finally:
        if c.proc.poll() is None:
            c.proc.kill()
        c.proc.wait(timeout=_CHILD_WAIT_S)
        c.proc.stdout.close()
        c.proc.stdin.close()


class TestChildProcessDeliversLineByLine:
    def test_each_printed_line_arrives_while_the_daemon_is_still_running(self, child):
        first = child.next_line()

        assert first is not None and _TRIGGER_LINE.match(first), (
            f"the trigger line must reach the pipe while the child runs; got {first!r}"
        )
        assert child.next_line() == "second line"
        assert child.proc.poll() is None, "the lines must arrive before the child exits"

    def test_the_stream_handler_writes_each_timing_line_with_its_own_utc_time(self, child):
        assert child.next_line() is not None, "the first printed line must arrive"
        assert child.next_line() is not None, "the second printed line must arrive"
        child.release()

        line = child.next_line()

        assert line is not None, "the timing line must arrive while the child runs"
        match = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3})Z\s+probe ok n=1$", line)
        assert match, f"expected '<UTC time>Z probe ok n=1', got {line!r}"
        stamped = datetime.fromisoformat(match.group(1)).replace(tzinfo=UTC)
        assert abs((datetime.now(UTC) - stamped).total_seconds()) < 120, (
            "the line must carry the moment it was written, in UTC"
        )

    def test_every_line_printed_before_a_sigkill_is_in_the_pipe(self, child):
        seen = [child.next_line()]
        assert seen[0] is not None, "the first printed line must arrive while the child runs"
        seen.append(child.next_line())
        child.release()
        seen.append(child.next_line())
        assert None not in seen, f"the child must have delivered three lines, got {seen!r}"
        time.sleep(0.1)  # the kill lands strictly after the lines above were delivered

        after_kill = child.kill_and_drain()

        everything = seen + after_kill
        assert sum(1 for line in everything if _TRIGGER_LINE.match(line)) == 1
        assert "second line" in everything
        assert any(line.endswith(" probe ok n=1") for line in everything)
