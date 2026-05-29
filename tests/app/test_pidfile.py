"""
Pidfile lifecycle contract pins for app.py daemon singleton.

These tests cover the five observable behaviours required for real-money
correctness:

  1. Pidfile is created at startup containing the running PID.
  2. A second startup attempt while the first daemon is alive REFUSES (exits
     non-zero, prints the expected message, does NOT start Flask/scheduler).
  3. A second startup attempt with a stale pidfile (stored PID not alive) TAKES
     OVER and overwrites the file with the new PID.
  4. On clean shutdown, the pidfile is removed.
  5. Pidfile is NOT removed if it currently contains a different PID (don't
     clobber another daemon's pidfile at teardown).

All psutil calls are mocked — no live daemon is spawned.  Tests stay in-process
and fast.
"""

from __future__ import annotations

import atexit
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

import app as app_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_pid(path: str, pid: int) -> None:
    with open(path, "w", encoding="ascii") as fh:
        fh.write(str(pid))


def _read_pid(path: str) -> int | None:
    try:
        with open(path, "r", encoding="ascii") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Low-level helper unit tests
# ---------------------------------------------------------------------------


class TestIsAlphabotProcessAlive:
    """Unit tests for _is_alphabot_process_alive."""

    def test_returns_true_when_process_has_app_py_in_cmdline(self, monkeypatch):
        fake_proc = MagicMock()
        fake_proc.cmdline.return_value = ["python.exe", "app.py"]
        with patch("app.psutil.Process", return_value=fake_proc):
            assert app_module._is_alphabot_process_alive(12345) is True

    def test_returns_false_when_process_cmdline_lacks_app_py(self, monkeypatch):
        fake_proc = MagicMock()
        fake_proc.cmdline.return_value = ["python.exe", "alpha_bot_execution.py"]
        with patch("app.psutil.Process", return_value=fake_proc):
            assert app_module._is_alphabot_process_alive(12345) is False

    def test_returns_false_on_no_such_process(self):
        import psutil
        with patch("app.psutil.Process", side_effect=psutil.NoSuchProcess(99999)):
            assert app_module._is_alphabot_process_alive(99999) is False

    def test_returns_false_on_access_denied(self):
        import psutil
        with patch("app.psutil.Process", side_effect=psutil.AccessDenied(99999)):
            assert app_module._is_alphabot_process_alive(99999) is False

    def test_returns_false_on_zombie_process(self):
        import psutil
        with patch("app.psutil.Process", side_effect=psutil.ZombieProcess(99999)):
            assert app_module._is_alphabot_process_alive(99999) is False


class TestPidfileHelpers:
    """Unit tests for _write_pidfile, _read_pidfile, _remove_pidfile_if_ours."""

    def test_write_and_read_round_trip(self, tmp_path):
        path = str(tmp_path / "test.pid")
        app_module._write_pidfile(path, 42)
        assert app_module._read_pidfile(path) == 42

    def test_read_returns_none_for_missing_file(self, tmp_path):
        path = str(tmp_path / "nonexistent.pid")
        assert app_module._read_pidfile(path) is None

    def test_read_returns_none_for_non_integer_content(self, tmp_path):
        path = str(tmp_path / "bad.pid")
        with open(path, "w") as fh:
            fh.write("not-a-pid")
        assert app_module._read_pidfile(path) is None

    def test_remove_deletes_file_when_pid_matches(self, tmp_path):
        path = str(tmp_path / "our.pid")
        app_module._write_pidfile(path, 7777)
        app_module._remove_pidfile_if_ours(path, 7777)
        assert not os.path.exists(path)

    def test_remove_leaves_file_when_pid_does_not_match(self, tmp_path):
        """Must NOT remove pidfile that belongs to a different daemon instance."""
        path = str(tmp_path / "other.pid")
        app_module._write_pidfile(path, 8888)
        app_module._remove_pidfile_if_ours(path, 9999)  # different PID
        assert os.path.exists(path)
        assert _read_pid(path) == 8888

    def test_remove_is_idempotent_when_file_already_gone(self, tmp_path):
        """Removing a non-existent pidfile must not raise."""
        path = str(tmp_path / "gone.pid")
        app_module._remove_pidfile_if_ours(path, 1234)  # file does not exist


# ---------------------------------------------------------------------------
# _acquire_daemon_singleton integration tests
# ---------------------------------------------------------------------------


class TestAcquireDaemonSingleton:
    """
    Contract tests for _acquire_daemon_singleton.

    All tests patch psutil.Process and os.getpid() so they are deterministic
    and do not spawn or interact with live OS processes.
    """

    # --- 1. Pidfile created at startup ---

    def test_creates_pidfile_with_our_pid_when_no_pidfile_exists(self, tmp_path, monkeypatch):
        """Startup with no pre-existing pidfile writes our PID to the file."""
        pidfile = str(tmp_path / "alphabot.pid")
        monkeypatch.setattr(app_module.os, "getpid", lambda: 1001)

        # Suppress atexit registration so we don't leave dangling handlers.
        with patch("app.atexit.register"):
            app_module._acquire_daemon_singleton(pidfile)

        assert os.path.exists(pidfile)
        assert _read_pid(pidfile) == 1001

    # --- 2. Refuses to start when another daemon is alive ---

    def test_refuses_and_exits_nonzero_when_alive_daemon_found(self, tmp_path, monkeypatch, capsys):
        """
        If a live Planet Stopper daemon holds the pidfile, _acquire_daemon_singleton
        must print an error to stderr and exit with non-zero status.
        Flask and the scheduler must never be started.
        """
        pidfile = str(tmp_path / "alphabot.pid")
        _write_pid(pidfile, 5050)  # simulate a running daemon

        # Make _is_alphabot_process_alive return True for PID 5050.
        with patch("app._is_alphabot_process_alive", return_value=True):
            with pytest.raises(SystemExit) as exc_info:
                app_module._acquire_daemon_singleton(pidfile)

        assert exc_info.value.code != 0, "must exit non-zero when alive daemon present"

    def test_error_message_contains_blocking_pid_and_pidfile_path(self, tmp_path, capsys):
        """The refusal message must name the blocking PID and the pidfile path."""
        pidfile = str(tmp_path / "alphabot.pid")
        _write_pid(pidfile, 5050)

        with patch("app._is_alphabot_process_alive", return_value=True):
            with pytest.raises(SystemExit):
                app_module._acquire_daemon_singleton(pidfile)

        captured = capsys.readouterr()
        output = captured.err + captured.out
        assert "5050" in output
        assert pidfile in output

    # --- 3. Stale pidfile takeover ---

    def test_takes_over_stale_pidfile_and_overwrites_with_our_pid(
        self, tmp_path, monkeypatch
    ):
        """
        When the pidfile contains a dead PID, startup must overwrite it with
        our own PID and continue (no SystemExit).
        """
        pidfile = str(tmp_path / "alphabot.pid")
        _write_pid(pidfile, 4040)  # dead PID

        monkeypatch.setattr(app_module.os, "getpid", lambda: 2002)

        with patch("app._is_alphabot_process_alive", return_value=False):
            with patch("app.atexit.register"):
                app_module._acquire_daemon_singleton(pidfile)  # must not raise

        assert _read_pid(pidfile) == 2002

    def test_stale_takeover_prints_notice(self, tmp_path, monkeypatch, capsys):
        """Stale-pidfile takeover must log a visible notice (not silent)."""
        pidfile = str(tmp_path / "alphabot.pid")
        _write_pid(pidfile, 4040)

        monkeypatch.setattr(app_module.os, "getpid", lambda: 2002)

        with patch("app._is_alphabot_process_alive", return_value=False):
            with patch("app.atexit.register"):
                app_module._acquire_daemon_singleton(pidfile)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "4040" in output  # the stale PID must appear in the notice

    # --- 4. Pidfile removed on clean shutdown ---

    def test_atexit_handler_registered_removes_pidfile_on_exit(self, tmp_path, monkeypatch):
        """
        _acquire_daemon_singleton registers an atexit handler.  When that
        handler fires for our own PID the pidfile is removed.
        """
        pidfile = str(tmp_path / "alphabot.pid")
        monkeypatch.setattr(app_module.os, "getpid", lambda: 3003)

        registered_handlers = []

        def fake_atexit_register(fn, *args, **kwargs):
            registered_handlers.append((fn, args, kwargs))

        with patch("app.atexit.register", side_effect=fake_atexit_register):
            app_module._acquire_daemon_singleton(pidfile)

        # The pidfile now contains 3003.
        assert _read_pid(pidfile) == 3003

        # Simulate clean exit by calling all registered handlers.
        for fn, args, kwargs in registered_handlers:
            fn(*args, **kwargs)

        assert not os.path.exists(pidfile), "pidfile must be removed after clean shutdown"

    # --- 5. Pidfile NOT removed if it belongs to a different daemon ---

    def test_atexit_handler_does_not_remove_pidfile_owned_by_another_pid(
        self, tmp_path, monkeypatch
    ):
        """
        If a new daemon took ownership between our startup and our shutdown
        (i.e., our PID is no longer in the file), we must NOT remove their
        pidfile.
        """
        pidfile = str(tmp_path / "alphabot.pid")
        monkeypatch.setattr(app_module.os, "getpid", lambda: 3003)

        registered_handlers = []

        def fake_atexit_register(fn, *args, **kwargs):
            registered_handlers.append((fn, args, kwargs))

        with patch("app.atexit.register", side_effect=fake_atexit_register):
            app_module._acquire_daemon_singleton(pidfile)

        # Simulate another daemon taking ownership while we were running.
        _write_pid(pidfile, 9999)

        # Our atexit handler fires — it must NOT remove the file.
        for fn, args, kwargs in registered_handlers:
            fn(*args, **kwargs)

        assert os.path.exists(pidfile), (
            "must not remove pidfile that belongs to a different daemon"
        )
        assert _read_pid(pidfile) == 9999

    # --- Edge: no pidfile initially, normal flow ---

    def test_no_exception_when_pidfile_does_not_exist(self, tmp_path, monkeypatch):
        """Clean startup path (no prior pidfile) completes without exception."""
        pidfile = str(tmp_path / "alphabot.pid")
        monkeypatch.setattr(app_module.os, "getpid", lambda: 1234)

        with patch("app.atexit.register"):
            app_module._acquire_daemon_singleton(pidfile)  # must not raise

        assert _read_pid(pidfile) == 1234
