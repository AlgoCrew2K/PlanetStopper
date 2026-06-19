"""
RED tests for the DISABLE_DAEMON_LENS_PIPELINE env-flag gate on _run_lens_pipeline().

Scope: app.py:680 _run_lens_pipeline() currently spawns a daemon thread
unconditionally. These tests assert the AFTER-state: when DISABLE_DAEMON_LENS_PIPELINE
is truthy the thread must NOT be started; when absent or empty-string it MUST be.

Test 1 (test_gate_suppresses_thread_when_disable_flag_is_set) is RED on the
pre-gate codebase — _run_lens_pipeline() unconditionally calls threading.Thread(...)
.start() today, so the assert start.call_count == 0 fails immediately.

Tests 2 and 3 are GREEN on the pre-gate codebase (they pass today) and serve as
regression guards once the guard is added — they ensure the guard is inert when the
flag is absent or empty.

Patch target rationale: _run_lens_pipeline() does `import threading` inline at
call time then calls threading.Thread(...).start(). Because the import is inline
(not a module-level binding), the correct patch target is 'threading.Thread' in
the stdlib module itself — that intercepts the Thread constructor regardless of
where it is imported from.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest


class TestLensPipelineGate:
    """Env-flag gate: DISABLE_DAEMON_LENS_PIPELINE controls thread spawn in _run_lens_pipeline()."""

    def test_gate_suppresses_thread_when_disable_flag_is_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Flag set to a truthy value ('1') — thread must NOT be started.

        This is the RED test: current code calls .start() unconditionally, so
        this assertion fails before the guard is implemented.
        """
        import app

        monkeypatch.setenv("DISABLE_DAEMON_LENS_PIPELINE", "1")

        mock_thread_instance = MagicMock()
        mock_thread_cls = MagicMock(return_value=mock_thread_instance)

        with patch("threading.Thread", mock_thread_cls):
            app._run_lens_pipeline()

        assert mock_thread_instance.start.call_count == 0, (
            "_run_lens_pipeline() called thread.start() even though "
            "DISABLE_DAEMON_LENS_PIPELINE='1' is set. "
            "The gate must check os.environ for this flag and return early "
            "without spawning the lens-pipeline thread."
        )

    def test_gate_starts_thread_when_disable_flag_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Flag absent from env — thread MUST be started exactly once (behavior unchanged).

        Regression guard: verifies the guard is inert when the flag is not set,
        so adding the guard does not break the default (flag-off) production path.
        """
        import app

        monkeypatch.delenv("DISABLE_DAEMON_LENS_PIPELINE", raising=False)

        mock_thread_instance = MagicMock()
        mock_thread_cls = MagicMock(return_value=mock_thread_instance)

        with patch("threading.Thread", mock_thread_cls):
            app._run_lens_pipeline()

        assert mock_thread_instance.start.call_count == 1, (
            "_run_lens_pipeline() did not call thread.start() when "
            "DISABLE_DAEMON_LENS_PIPELINE is absent from the environment. "
            "The guard must be inert (no-op) when the flag is not set — "
            "default production behavior is to spawn the thread."
        )

    def test_gate_starts_thread_when_disable_flag_is_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Flag set to empty string ('') — thread MUST be started (falsy == not set).

        Pins the exact semantics: os.environ.get('DISABLE_DAEMON_LENS_PIPELINE')
        returns '' which is falsy in Python; the guard must treat this as NOT set
        and must NOT skip the thread spawn. This prevents a subtle regression
        where `if os.environ.get(FLAG):` (correct) is later changed to
        `if FLAG in os.environ:` (wrong — would skip on empty string).
        """
        import app

        monkeypatch.setenv("DISABLE_DAEMON_LENS_PIPELINE", "")

        mock_thread_instance = MagicMock()
        mock_thread_cls = MagicMock(return_value=mock_thread_instance)

        with patch("threading.Thread", mock_thread_cls):
            app._run_lens_pipeline()

        assert mock_thread_instance.start.call_count == 1, (
            "_run_lens_pipeline() skipped the thread spawn when "
            "DISABLE_DAEMON_LENS_PIPELINE='' (empty string). "
            "Empty string is falsy; the guard must use truthiness "
            "(os.environ.get(FLAG)) not presence (FLAG in os.environ) "
            "so that an empty-string value is treated as not set."
        )

    def test_gate_logs_skip_when_disable_flag_is_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Flag set — _daemon_log.info must be called with a message containing
        'DISABLE_DAEMON_LENS_PIPELINE' so a droplet operator can confirm from
        logs that the 03:00 slot is gated without a thread dump.

        The exact message text is not pinned (implementation-detail), but the
        flag name must appear so grep-ability on the droplet is guaranteed.
        """
        import app

        monkeypatch.setenv("DISABLE_DAEMON_LENS_PIPELINE", "1")

        with patch("threading.Thread"), patch.object(
            app._daemon_log, "info"
        ) as mock_info:
            app._run_lens_pipeline()

        logged_messages = [str(c.args[0]) for c in mock_info.call_args_list]
        assert any(
            "DISABLE_DAEMON_LENS_PIPELINE" in msg for msg in logged_messages
        ), (
            "_run_lens_pipeline() did not log a message containing "
            "'DISABLE_DAEMON_LENS_PIPELINE' when the flag was set. "
            "The skip log line is the only observability signal on the droplet; "
            "it must name the flag so operators can confirm the gate is active."
        )
