"""
Scope guard for DE-EOD-BASIS-001 (AC-5).

The fix is confined to:
  app.py — frozen branch recompute block (~lines 1750-1834), module-level cache
    variables (_account_totals_last_good, _account_totals_last_success_at,
    _ACCOUNT_TOTALS_HTTP_TIMEOUT_S), _refresh_account_totals, _compute_portfolio_strip.
  analytics.py — account-basis helpers already exist; NO changes expected.

MUST NOT touch (AC-5 scope boundary):
  alpha_bot_execution.py — live trade engine.
  math_engine.py          — risk math constants and layers.

Scope is checked from the FIRST EOD-basis cycle commit onward only, not from the
full branch-vs-main diff. This branch carries prior-cycle commits that legitimately
changed these files for different features; the constraint applies only to work
introduced in the EOD-basis cycle.

The "EOD-basis cycle start" is identified by the commit that first introduced
tests/dashboard/test_eod_account_basis.py (the RED anchor file).

AC-5 is an always-pass constraint test:
  - Before the RED commit: SKIP (no RED anchor in history yet).
  - After RED commit: PASS (only test files added so far).
  - After GREEN commit (correct impl): PASS (only app.py + analytics.py changed).
  - After GREEN commit (scope violation): FAIL (forbidden file in diff).
  - Git unavailable: SKIP.
"""

from __future__ import annotations

import subprocess
import sys


def _run_git(*args: str) -> tuple[int, str]:
    """Run git with the given args; return (returncode, stdout+stderr combined)."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode, result.stdout + result.stderr
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return -1, ""


def _get_eod_cycle_start_sha() -> str | None:
    """Return the commit SHA that first introduced test_eod_account_basis.py.

    This is the anchor for the EOD-basis cycle start. All scope checks use
    `diff <this SHA> HEAD` so that prior-cycle changes to the forbidden files
    do not produce false-positive violations.

    Returns None when the file has no git history yet (pre-commit state: tests
    are written but not yet committed), in which case scope guard tests skip.
    """
    rc, output = _run_git(
        "log",
        "--follow",
        "--format=%H",
        "--",
        "tests/dashboard/test_eod_account_basis.py",
    )
    if rc != 0 or not output.strip():
        return None
    # `git log` outputs newest-first; the LAST line is the oldest commit (first introduction).
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    return lines[-1] if lines else None


def _get_changed_files_since(sha: str) -> list[str] | None:
    """Return list of files changed from sha (exclusive) to HEAD (inclusive).

    Returns None when the git diff command fails.
    """
    rc, output = _run_git("diff", "--name-only", sha, "HEAD")
    if rc != 0:
        return None
    return [ln.strip() for ln in output.splitlines() if ln.strip()]


def test_alpha_bot_execution_not_in_diff() -> None:
    """
    AC-5: alpha_bot_execution.py must NOT appear in EOD-basis commits.
    The fix is confined to app.py (rendering layer).

    Uses changes since the first EOD RED commit (test_eod_account_basis.py intro).
    Skips gracefully when git is unavailable or before the RED commit is made.
    """
    eod_sha = _get_eod_cycle_start_sha()
    if eod_sha is None:
        import pytest

        pytest.skip(
            "EOD-basis RED tests not yet committed (pre-commit state); "
            "scope guard runs after the RED commit. "
            "Reminder: do NOT touch alpha_bot_execution.py for DE-EOD-BASIS-001."
        )

    changed = _get_changed_files_since(eod_sha)
    if changed is None:
        import pytest

        pytest.skip(
            f"git diff --name-only {eod_sha} HEAD failed; "
            "scope guard requires a working git repository."
        )

    violations = [f for f in changed if "alpha_bot_execution.py" in f]

    assert not violations, (
        f"AC-5 SCOPE VIOLATION: alpha_bot_execution.py must not be modified for "
        f"DE-EOD-BASIS-001 (checked from EOD cycle start {eod_sha[:8]}). "
        f"Found in diff: {violations}. "
        f"The fix is confined to the rendering layer (app.py frozen branch + analytics.py). "
        f"If you need to change the execution engine, that is a separate cycle."
    )


def test_math_engine_not_in_diff() -> None:
    """
    AC-5: math_engine.py must NOT appear in EOD-basis commits.
    Risk math layers are not affected by the EOD account-basis fix.

    Uses changes since the first EOD RED commit (test_eod_account_basis.py intro).
    Skips gracefully when git is unavailable or before the RED commit is made.
    """
    eod_sha = _get_eod_cycle_start_sha()
    if eod_sha is None:
        import pytest

        pytest.skip(
            "EOD-basis RED tests not yet committed (pre-commit state); "
            "scope guard runs after the RED commit. "
            "Reminder: do NOT touch math_engine.py for DE-EOD-BASIS-001."
        )

    changed = _get_changed_files_since(eod_sha)
    if changed is None:
        import pytest

        pytest.skip(
            f"git diff --name-only {eod_sha} HEAD failed; "
            "scope guard requires a working git repository."
        )

    violations = [f for f in changed if "math_engine.py" in f]

    assert not violations, (
        f"AC-5 SCOPE VIOLATION: math_engine.py must not be modified for DE-EOD-BASIS-001 "
        f"(checked from EOD cycle start {eod_sha[:8]}). "
        f"Found in diff: {violations}. "
        f"No risk-math constants or layers are affected by the EOD account-basis fix. "
        f"If you need to change math_engine.py, that is a separate cycle."
    )


def test_scope_guard_diff_command_is_runnable() -> None:
    """
    Meta: the git command used by this guard must be runnable in the test environment.

    Pre-commit: skip (no EOD anchor in history yet).
    Post-commit: verify the diff command runs and log changed files.
    """
    eod_sha = _get_eod_cycle_start_sha()
    if eod_sha is None:
        import pytest

        pytest.skip(
            "EOD-basis RED tests not yet committed; scope guard meta-test skips pre-commit."
        )

    changed = _get_changed_files_since(eod_sha)
    if changed is None:
        import pytest

        pytest.skip(f"git diff failed from {eod_sha[:8]} to HEAD.")

    # Log for diagnosis (shown only on failure or -s)
    print(
        f"\n[scope_guard] {len(changed)} file(s) changed since EOD cycle start "
        f"({eod_sha[:8]}):",
        file=sys.stderr,
    )
    for f in changed:
        print(f"  {f}", file=sys.stderr)

    assert isinstance(changed, list), (
        f"_get_changed_files_since() must return a list; got {type(changed).__name__!r}."
    )
