"""
Tests for prism_scheduler.py — Market Prism Phase 4 nightly scheduler wrapper.

Covers:
  AC-1  Idempotency: today's row exists → no subprocess call, exit 0
  AC-2  No today's row → subprocess invoked with correct args
  AC-3  Bounded retry on subprocess failure → MAX_ATTEMPTS calls, finite backoff, exit non-zero
  AC-4  Retry succeeds on 2nd attempt → subprocess called twice, exit 0
  AC-5  Yesterday's row does NOT trigger idempotency (today still runs)
  AC-6  MAX_ATTEMPTS is a finite named constant (1–5)
  AC-7  Backoff cap enforced (sleep values ≤ BACKOFF_CAP_SECONDS)
  AC-8  API key is NOT echoed to any log or subprocess arg
"""

import importlib
import sys
import types
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today_row(offset_days: int = 0) -> dict:
    """Return a fake MARKET_PRISM summary row whose created_at is today+offset_days UTC."""
    ts = datetime.now(timezone.utc) + timedelta(days=offset_days)
    return {
        "id": 68 - offset_days,
        "verdict": "limited-inputs",
        "created_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "advisor_role": "MARKET_PRISM",
        "raw_response": {"run_id": ts.strftime("%Y-%m-%dT%H:%M:%S+00:00")},
    }


def _import_scheduler():
    """Import (or reimport) prism_scheduler fresh each call."""
    if "prism_scheduler" in sys.modules:
        del sys.modules["prism_scheduler"]
    # The scheduler lives at the project root which is on sys.path in the worktree env;
    # if not, add the worktree root explicitly.
    import os
    worktree = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    if worktree not in sys.path:
        sys.path.insert(0, worktree)
    import prism_scheduler  # noqa: PLC0415
    return prism_scheduler


# ---------------------------------------------------------------------------
# AC-6 — named constant sanity (import-level, no mocking needed)
# ---------------------------------------------------------------------------

class TestConstants:
    def test_max_attempts_is_named_constant(self):
        mod = _import_scheduler()
        assert hasattr(mod, "MAX_ATTEMPTS"), "prism_scheduler must export MAX_ATTEMPTS"
        assert isinstance(mod.MAX_ATTEMPTS, int), "MAX_ATTEMPTS must be an int"
        assert 1 <= mod.MAX_ATTEMPTS <= 5, "MAX_ATTEMPTS must be between 1 and 5"

    def test_backoff_constants_are_named(self):
        mod = _import_scheduler()
        assert hasattr(mod, "BACKOFF_BASE_SECONDS"), "must export BACKOFF_BASE_SECONDS"
        assert hasattr(mod, "BACKOFF_CAP_SECONDS"), "must export BACKOFF_CAP_SECONDS"
        assert mod.BACKOFF_BASE_SECONDS > 0
        assert mod.BACKOFF_CAP_SECONDS >= mod.BACKOFF_BASE_SECONDS

    def test_backoff_cap_is_finite(self):
        mod = _import_scheduler()
        assert mod.BACKOFF_CAP_SECONDS <= 300, "Backoff cap must be finite and reasonable (≤5min)"


# ---------------------------------------------------------------------------
# AC-1 — Idempotency: today's row exists → no subprocess, exit 0
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_today_row_skips_subprocess(self):
        """If a MARKET_PRISM row exists for today UTC, subprocess is never called."""
        mod = _import_scheduler()
        today_row = _today_row(0)

        with (
            patch.object(mod, "_get_summary", return_value=today_row),
            patch("subprocess.run") as mock_run,
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        mock_run.assert_not_called()
        assert exc_info.value.code == 0, "Should exit 0 when today's row already exists"

    def test_today_row_detection_uses_utc(self):
        """created_at comparison must use UTC date, not local time."""
        mod = _import_scheduler()
        # Create a row with an explicit UTC datetime string
        utc_now = datetime.now(timezone.utc)
        row = {
            "id": 99,
            "verdict": "limited-inputs",
            "created_at": utc_now.strftime("%Y-%m-%d %H:%M:%S"),
            "advisor_role": "MARKET_PRISM",
            "raw_response": {},
        }

        with (
            patch.object(mod, "_get_summary", return_value=row),
            patch("subprocess.run") as mock_run,
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        mock_run.assert_not_called()
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# AC-2 — No today's row → subprocess invoked with correct args
# ---------------------------------------------------------------------------

EXPECTED_CLAUDE_ARGS = [
    "claude",
    "-p",
    "--agent",
    "prism-synthesizer",
    "--dangerously-skip-permissions",
    "--model",
    "opus",
]


class TestSubprocessInvocation:
    def test_no_row_invokes_claude_subprocess(self):
        """When no today's row exists, subprocess.run is called with correct claude args."""
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        mock_run.assert_called_once()
        args_used = mock_run.call_args[0][0]  # first positional arg = the cmd list
        for expected in EXPECTED_CLAUDE_ARGS:
            assert expected in args_used, f"Expected '{expected}' in subprocess args: {args_used}"
        assert exc_info.value.code == 0

    def test_subprocess_cwd_is_project_root(self):
        """subprocess.run must use the project root as cwd, not the test cwd."""
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit),
        ):
            mod.main()

        kwargs = mock_run.call_args[1]
        assert "cwd" in kwargs, "subprocess.run must set cwd explicitly"
        import os
        assert os.path.isabs(kwargs["cwd"]), "cwd must be an absolute path"

    def test_subprocess_not_shell_true(self):
        """shell=True would be a security risk — must not be set."""
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit),
        ):
            mod.main()

        kwargs = mock_run.call_args[1]
        assert not kwargs.get("shell", False), "shell=True is not allowed — security risk"

    def test_api_key_not_in_subprocess_args(self):
        """ANTHROPIC_API_KEY must not appear in the subprocess args list."""
        import os
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-TEST-SENTINEL-VALUE"
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        try:
            with (
                patch.object(mod, "_get_summary", return_value=None),
                patch("subprocess.run", return_value=mock_result) as mock_run,
                pytest.raises(SystemExit),
            ):
                mod.main()
        finally:
            del os.environ["ANTHROPIC_API_KEY"]

        args_used = mock_run.call_args[0][0]
        for arg in args_used:
            assert "sk-ant" not in str(arg), "API key must not appear in subprocess args"


# ---------------------------------------------------------------------------
# AC-3 — Bounded retry on persistent subprocess failure
# ---------------------------------------------------------------------------

class TestBoundedRetry:
    def test_retries_exactly_max_attempts_times(self):
        """On persistent subprocess failure, retries exactly MAX_ATTEMPTS times."""
        mod = _import_scheduler()
        fail_result = MagicMock()
        fail_result.returncode = 1

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=fail_result) as mock_run,
            patch("time.sleep"),
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        assert mock_run.call_count == mod.MAX_ATTEMPTS, (
            f"Expected exactly {mod.MAX_ATTEMPTS} subprocess calls, got {mock_run.call_count}"
        )
        assert exc_info.value.code != 0, "Should exit non-zero after exhausting retries"

    def test_backoff_sleep_values_are_finite_and_capped(self):
        """Sleep durations must be finite and never exceed BACKOFF_CAP_SECONDS."""
        mod = _import_scheduler()
        fail_result = MagicMock()
        fail_result.returncode = 1
        sleep_calls = []

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=fail_result),
            patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)),
            pytest.raises(SystemExit),
        ):
            mod.main()

        assert len(sleep_calls) > 0, "Expected at least one sleep call on retry"
        for s in sleep_calls:
            assert s <= mod.BACKOFF_CAP_SECONDS, (
                f"Sleep duration {s}s exceeds cap {mod.BACKOFF_CAP_SECONDS}s"
            )
            assert s >= 0, "Sleep duration must be non-negative"

    def test_no_infinite_loop(self):
        """Confirm finite retry — the loop terminates."""
        mod = _import_scheduler()
        call_count = {"n": 0}

        def counting_run(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] > 10:
                raise AssertionError("subprocess.run called more than 10 times — infinite loop?")
            result = MagicMock()
            result.returncode = 1
            return result

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", side_effect=counting_run),
            patch("time.sleep"),
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        assert exc_info.value.code != 0
        assert call_count["n"] <= mod.MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# AC-4 — Retry succeeds on 2nd attempt
# ---------------------------------------------------------------------------

class TestRetrySuccess:
    def test_retry_succeeds_on_second_attempt(self):
        """First call fails, second succeeds → exit 0, called twice."""
        mod = _import_scheduler()
        results = [MagicMock(returncode=1), MagicMock(returncode=0)]

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", side_effect=results) as mock_run,
            patch("time.sleep"),
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        assert mock_run.call_count == 2
        assert exc_info.value.code == 0, "Should exit 0 after successful retry"


# ---------------------------------------------------------------------------
# AC-5 — Yesterday's row does NOT trigger idempotency
# ---------------------------------------------------------------------------

class TestYesterdayRow:
    def test_yesterday_row_triggers_run(self):
        """A row from yesterday UTC is NOT today's row — subprocess must be called."""
        mod = _import_scheduler()
        yesterday_row = _today_row(offset_days=-1)
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=yesterday_row),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        mock_run.assert_called_once()
        assert exc_info.value.code == 0

    def test_none_summary_triggers_run(self):
        """When get_latest_market_prism_summary returns None (no rows), subprocess is called."""
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        mock_run.assert_called_once()
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# AC-7 — Backoff cap: sleep values must not exceed cap even with exponential growth
# ---------------------------------------------------------------------------

class TestBackoffCap:
    def test_exponential_backoff_capped(self):
        """With many retries (if MAX_ATTEMPTS were large), sleep never exceeds cap."""
        mod = _import_scheduler()
        sleep_calls = []
        fail_result = MagicMock(returncode=1)

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=fail_result),
            patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)),
            pytest.raises(SystemExit),
        ):
            mod.main()

        for s in sleep_calls:
            assert s <= mod.BACKOFF_CAP_SECONDS, (
                f"Backoff {s}s exceeds BACKOFF_CAP_SECONDS={mod.BACKOFF_CAP_SECONDS}"
            )


# ---------------------------------------------------------------------------
# AC-8 — D-1 contract: no raw exception text in outputs
# ---------------------------------------------------------------------------

class TestD1Contract:
    def test_exception_in_subprocess_does_not_propagate_raw(self):
        """If subprocess.run raises an exception, it must not propagate unhandled."""
        mod = _import_scheduler()

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", side_effect=OSError("some internal path /secret/path")),
            patch("time.sleep"),
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        # Should exit non-zero — not propagate the raw OSError
        assert exc_info.value.code != 0, "Should exit non-zero on subprocess exception"
