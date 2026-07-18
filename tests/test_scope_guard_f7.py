"""
Scope guard for F7 (AC-5) -- math_engine.py zero diff.

feature-plans/math-f7.md AC-5: "live exit-decision math untouched ... [and]
math_engine.py zero diff." The AC-1 persist guard and the AC-4 tripwire are
both display/diagnostic-layer fixes confined to alpha_bot_execution.py
(the persist sites + the tripwire); the render/tooltip fixes are confined to
templates/table_partial.html and static/index.js. No math_engine.py change
is in scope for this cycle.

Mirrors tests/test_scope_guard.py's git-diff-since-RED-commit idiom exactly
(same mechanics, different anchor file + forbidden path). Scope is checked
from the FIRST F7 RED commit onward only, not the full branch-vs-main diff
-- this branch carries prior-cycle commits (R0/R1/R2) that legitimately
touched math_engine.py for unrelated, already-shipped work.

The "F7 cycle start" is identified by the commit that first introduced
tests/execution/test_f7_ac1_persist_guard.py (the RED anchor file).

AC-5 is an always-pass constraint test:
  - Before the RED commit: SKIP (no RED anchor in history yet).
  - After RED commit, correct GREEN impl: PASS (math_engine.py untouched).
  - After a GREEN impl that touches math_engine.py: FAIL.
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


def _get_f7_cycle_start_sha() -> str | None:
    """Return the commit SHA that first introduced
    tests/execution/test_f7_ac1_persist_guard.py.

    This is the anchor for the F7 cycle start. All scope checks use
    `diff <this SHA> HEAD` so that prior-cycle changes to math_engine.py
    (R0/R1/R2, all already shipped) do not produce false-positive violations.

    Returns None when the file has no git history yet (pre-commit state:
    tests are written but not yet committed), in which case scope guard
    tests skip.
    """
    rc, output = _run_git(
        "log",
        "--follow",
        "--format=%H",
        "--",
        "tests/execution/test_f7_ac1_persist_guard.py",
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


def test_math_engine_not_in_diff() -> None:
    """
    AC-5: math_engine.py must NOT appear in F7 commits. The AC-1 persist
    guard and AC-4 tripwire are alpha_bot_execution.py-only changes; no risk
    -math layer is affected.

    Uses changes since the first F7 RED commit (test_f7_ac1_persist_guard.py
    intro). Skips gracefully when git is unavailable or before the RED
    commit is made.
    """
    f7_sha = _get_f7_cycle_start_sha()
    if f7_sha is None:
        import pytest

        pytest.skip(
            "F7 RED tests not yet committed (pre-commit state); scope guard "
            "runs after the RED commit. Reminder: do NOT touch math_engine.py "
            "for F7 (AC-5)."
        )

    changed = _get_changed_files_since(f7_sha)
    if changed is None:
        import pytest

        pytest.skip(
            f"git diff --name-only {f7_sha} HEAD failed; scope guard requires a working git repository."
        )

    violations = [f for f in changed if "math_engine.py" in f]

    assert not violations, (
        f"AC-5 SCOPE VIOLATION: math_engine.py must not be modified for F7 "
        f"(checked from F7 cycle start {f7_sha[:8]}). Found in diff: "
        f"{violations}. No risk-math constants or layers are affected by "
        f"the honest post-trigger MC display fix + MAPERF-15 tripwire. If "
        f"you need to change math_engine.py, that is a separate cycle."
    )


def test_scope_guard_diff_command_is_runnable() -> None:
    """
    Meta: the git command used by this guard must be runnable in the test
    environment.

    Pre-commit: skip (no F7 anchor in history yet).
    Post-commit: verify the diff command runs and log changed files.
    """
    f7_sha = _get_f7_cycle_start_sha()
    if f7_sha is None:
        import pytest

        pytest.skip("F7 RED tests not yet committed; scope guard meta-test skips pre-commit.")

    changed = _get_changed_files_since(f7_sha)
    if changed is None:
        import pytest

        pytest.skip(f"git diff failed from {f7_sha[:8]} to HEAD.")

    # Log for diagnosis (shown only on failure or -s)
    print(
        f"\n[scope_guard_f7] {len(changed)} file(s) changed since F7 cycle start ({f7_sha[:8]}):",
        file=sys.stderr,
    )
    for f in changed:
        print(f"  {f}", file=sys.stderr)

    assert isinstance(changed, list), (
        f"_get_changed_files_since() must return a list; got {type(changed).__name__!r}."
    )
