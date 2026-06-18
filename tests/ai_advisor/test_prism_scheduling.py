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

Hardening additions (Phase 4 gap closure — RED before GREEN):
  HC-1  Spend cap: --max-budget-usd <MAX_BUDGET_USD> in the claude command (named constant)
  HC-2  Spend logging: --output-format json in cmd; prism_audit_log row with phase='spend_log'
        persisted after a successful run (shape/presence, not value)
  HC-3  Model pin: cmd contains 'claude-opus-4-8' (pinned), NOT bare 'opus' (alias)
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

    def test_get_summary_db_failure_does_not_leak_message_body(self, capsys):
        """D-1: when _get_summary() raises, only type(exc).__name__ appears in output.

        The fallback path (lines 113-117 of prism_scheduler.py) catches the exception
        and prints only type(exc).__name__. A bad implementation could print str(exc),
        leaking internal DB paths/messages. This test locks that contract.

        It also verifies the scheduler treats the DB failure as 'no row' and proceeds
        to attempt the run (exiting 0 on the mocked successful _run_prism call).
        """
        mod = _import_scheduler()

        # A message body that MUST NOT appear anywhere in stdout/stderr
        secret_body = "some/internal/secret/path"

        with (
            patch.object(
                mod,
                "_get_summary",
                side_effect=RuntimeError(secret_body),
            ),
            # _run_prism returns True so main() exits 0 — confirming DB failure is
            # treated as 'no row' and does NOT abort the run
            patch.object(mod, "_run_prism", return_value=True),
            # Suppress _load_env's dotenv import; it swallows exceptions, but patch
            # it here so the test is fully hermetic regardless of the environment
            patch.object(mod, "_load_env"),
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        captured = capsys.readouterr()
        combined_output = captured.out + captured.err

        # D-1 positive assertion: the exception type name IS surfaced (so operators
        # know *something* went wrong without leaking internals)
        assert "RuntimeError" in combined_output, (
            "Expected 'RuntimeError' (the type name) in scheduler output — "
            f"D-1 requires the type name to be logged. Got: {combined_output!r}"
        )

        # D-1 negative assertion: the raw message body MUST NOT appear
        assert secret_body not in combined_output, (
            f"Secret message body {secret_body!r} leaked into output — "
            "D-1 contract violated: only type(exc).__name__ may be surfaced"
        )

        # Behavioral assertion: DB failure is treated as 'no row' (not a crash),
        # so _run_prism is invoked and the scheduler exits 0 on success
        assert exc_info.value.code == 0, (
            "DB failure on idempotency check should be treated as 'no row' "
            "— run proceeds, exits 0 on successful _run_prism"
        )


# ---------------------------------------------------------------------------
# HC-1 — Spend cap: --max-budget-usd with named constant MAX_BUDGET_USD
# ---------------------------------------------------------------------------

class TestSpendCap:
    def test_max_budget_usd_constant_exists_and_is_positive(self):
        """MAX_BUDGET_USD must be a named positive float/int constant.

        A runaway Opus council run with no dollar cap is bounded only by retry
        count — unacceptable for a nightly unattended job.  The constant must
        be named so the PM can audit and adjust without hunting for a magic number.

        RED intent: MAX_BUDGET_USD does not exist in prism_scheduler → AttributeError.
        """
        mod = _import_scheduler()
        assert hasattr(mod, "MAX_BUDGET_USD"), (
            "MAX_BUDGET_USD constant is missing from prism_scheduler.py. "
            "A named spend cap is required — never a magic number."
        )
        val = mod.MAX_BUDGET_USD
        assert isinstance(val, (int, float)), "MAX_BUDGET_USD must be numeric"
        assert val > 0, f"MAX_BUDGET_USD must be positive, got {val}"

    def test_claude_command_includes_max_budget_usd_flag(self):
        """The claude subprocess command must include --max-budget-usd <MAX_BUDGET_USD>.

        Without this flag the nightly Opus run has no dollar ceiling — the only
        bound is retry count (3 × however long the council runs).

        RED intent: _run_prism() command list lacks --max-budget-usd → assertion fails.
        """
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit),
        ):
            mod.main()

        args_used = mock_run.call_args[0][0]
        cmd_str = " ".join(str(a) for a in args_used)

        assert "--max-budget-usd" in args_used, (
            "--max-budget-usd is missing from the claude subprocess command. "
            f"Command was: {cmd_str}"
        )

        # The value after the flag must match the named constant (shape check).
        budget_idx = args_used.index("--max-budget-usd")
        assert budget_idx + 1 < len(args_used), (
            "--max-budget-usd must be followed by a numeric value"
        )
        budget_val = args_used[budget_idx + 1]
        try:
            budget_float = float(budget_val)
        except (ValueError, TypeError):
            pytest.fail(
                f"--max-budget-usd value must be numeric, got {budget_val!r}. "
                f"Command was: {cmd_str}"
            )
        # Value must match the named constant — not an independent magic number.
        assert budget_float == float(mod.MAX_BUDGET_USD), (
            f"--max-budget-usd value {budget_float} does not match "
            f"MAX_BUDGET_USD={mod.MAX_BUDGET_USD}. "
            "The command must use the named constant, not a magic number."
        )


# ---------------------------------------------------------------------------
# HC-2 — Spend logging: --output-format json + prism_audit_log persistence
# ---------------------------------------------------------------------------

class TestSpendLogging:
    def test_claude_command_includes_output_format_json(self):
        """The claude command must include --output-format json so cost can be parsed.

        Without structured JSON output from the subprocess, there is no reliable
        way to parse the Opus spend (AC-4 contract).

        RED intent: _run_prism() cmd lacks --output-format json → assertion fails.
        """
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit),
        ):
            mod.main()

        args_used = mock_run.call_args[0][0]
        cmd_str = " ".join(str(a) for a in args_used)

        assert "--output-format" in args_used, (
            "--output-format is missing from the claude command. "
            f"Command was: {cmd_str}"
        )
        fmt_idx = args_used.index("--output-format")
        assert fmt_idx + 1 < len(args_used), "--output-format must be followed by a value"
        assert args_used[fmt_idx + 1] == "json", (
            f"--output-format value must be 'json', got {args_used[fmt_idx + 1]!r}. "
            f"Command was: {cmd_str}"
        )

    def test_successful_run_persists_spend_log_audit_entry(self):
        """After a successful claude run, a prism_audit_log entry with phase='spend_log'
        must be persisted via database.insert_prism_audit_entry.

        Shape assertion only — we verify the entry exists with the correct phase and
        a positive float cost value.  We do NOT assert the exact dollar amount
        (that is a producer-computed value; see global feedback rule
        feedback_no_hardcoded_test_values).

        RED intent: _run_prism() does not call insert_prism_audit_entry → no row → fails.
        """
        import json as _json
        import sqlite3
        import sys
        import os

        # Ensure the project root (where database.py lives) is importable.
        mod = _import_scheduler()
        worktree = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        if worktree not in sys.path:
            sys.path.insert(0, worktree)
        import database as _db

        # Simulate subprocess returning a JSON payload with a cost field.
        # The cost value is arbitrary — we assert shape (positive float), not the literal.
        simulated_cost = 1.23
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _json.dumps({"cost_usd": simulated_cost})

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result),
            pytest.raises(SystemExit) as exc_info,
        ):
            mod.main()

        assert exc_info.value.code == 0, "Expected exit 0 on successful run"

        # Verify the spend log entry was written to prism_audit_log.
        conn = sqlite3.connect(_db._db_file())
        cursor = conn.cursor()
        cursor.execute(
            "SELECT content FROM prism_audit_log WHERE phase = 'spend_log' ORDER BY id DESC LIMIT 5"
        )
        rows = cursor.fetchall()
        conn.close()

        assert rows, (
            "No prism_audit_log row with phase='spend_log' was written after a successful run. "
            "AC-4 requires per-run Opus spend to be persisted for PM observability."
        )

        # Parse the most recent spend log entry and verify it has a positive float cost.
        latest_content = rows[0][0]
        try:
            parsed = _json.loads(latest_content)
        except (_json.JSONDecodeError, TypeError):
            pytest.fail(
                f"spend_log content is not valid JSON: {latest_content!r}. "
                "The spend log must be a JSON object with a cost field."
            )

        # Accept any of these canonical cost key names.
        cost_val = parsed.get("cost_usd") or parsed.get("cost") or parsed.get("spend_usd")
        assert cost_val is not None, (
            f"spend_log JSON has no cost_usd/cost/spend_usd key. Got: {parsed!r}"
        )
        assert isinstance(cost_val, (int, float)) and cost_val > 0, (
            f"spend_log cost value must be a positive float. Got: {cost_val!r}"
        )


# ---------------------------------------------------------------------------
# HC-3 — Model pin: 'claude-opus-4-8' (pinned), not bare 'opus' (alias)
# ---------------------------------------------------------------------------

class TestModelPin:
    def test_claude_command_uses_pinned_model_not_alias(self):
        """The claude command must use the pinned model ID 'claude-opus-4-8',
        not the bare alias 'opus'.

        Aliases are unstable — 'opus' can silently advance to a new model version,
        changing cost and behaviour without any code change.  Pinning to
        'claude-opus-4-8' makes the model choice explicit and reviewable.

        RED intent: _run_prism() uses '--model opus' → 'opus' in args but
        'claude-opus-4-8' not in args → assertion fails.
        """
        mod = _import_scheduler()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=mock_result) as mock_run,
            pytest.raises(SystemExit),
        ):
            mod.main()

        args_used = mock_run.call_args[0][0]
        cmd_str = " ".join(str(a) for a in args_used)

        # Pinned model ID must be present.
        assert "claude-opus-4-8" in args_used, (
            "Pinned model 'claude-opus-4-8' is missing from the claude command. "
            "Use the pinned model ID, not the bare alias 'opus'. "
            f"Command was: {cmd_str}"
        )

        # Bare alias must NOT appear as the --model value.
        # Find the value after '--model' and verify it is not just 'opus'.
        if "--model" in args_used:
            model_idx = args_used.index("--model")
            if model_idx + 1 < len(args_used):
                model_val = args_used[model_idx + 1]
                assert model_val != "opus", (
                    f"--model is set to bare alias 'opus' — use pinned ID 'claude-opus-4-8'. "
                    f"Command was: {cmd_str}"
                )
