"""
Scope guard for F7 (AC-5) -- math_engine.py zero diff.

feature-plans/math-f7.md AC-5: "live exit-decision math untouched ... [and]
math_engine.py zero diff." The AC-1 persist guard and the AC-4 tripwire are
both display/diagnostic-layer fixes confined to alpha_bot_execution.py
(the persist sites + the tripwire); the render/tooltip fixes are confined to
templates/table_partial.html and static/index.js. No math_engine.py change
was in scope for F7.

REWRITTEN 2026-07-24 (exit-friction-realized-savings cycle -- ga2-tw RED
review + PM-directed fork-point/shallow-clone trace, requested after the
diagnosis "stale, pre-existing" needed the specific evidence, not just the
verdict). Two defects found in the original <dynamic-anchor>..HEAD design:

1. SHALLOW-CLONE FALSE PASS (silent, in CI): the original
   `_get_f7_cycle_start_sha()` used `git log --follow` to discover the F7 RED
   anchor commit dynamically. `.github/workflows/tests.yml` uses
   `actions/checkout@v4` with no `fetch-depth` override, i.e. the CI default
   shallow clone (depth 1). In a shallow clone, `git log --follow` can only
   see the checkout's own tip commit -- it silently resolved the "anchor" to
   HEAD itself instead of the true F7 origin, so the diff-since-anchor
   collapsed to `git diff <sha> <same sha>`, which is always empty. CI has
   therefore been passing this test VACUOUSLY -- the assertion never
   actually ran against real history. Reproduced directly: a real depth=1
   fetch of PR #112's CI-green commit (62532ea7) resolved the anchor to
   62532ea7 itself rather than the true F7 origin (7752bb00).

2. UNBOUNDED-RANGE FALSE FAIL (in any full clone, forever after): diffing
   <anchor>..HEAD means ANY future, UNRELATED cycle that legitimately
   touches math_engine.py breaks this test permanently, on every branch
   downstream, regardless of whether F7 itself stayed in scope. This
   happened for real one cycle later: R3-b's MA-4 disarm-band fix
   (43a458f8) legitimately touched math_engine.py. Confirmed independently
   by running the original test at feat/exit-friction-realized-savings' own
   fork point (ccda9abe) -- it already failed there, before this cycle's
   first commit, proving the failure predates and is unrelated to this
   cycle.

FIX: F7 is a COMPLETED, SHIPPED cycle (PR #99, merged at bd2c8d5d). Its
scope claim -- "math_engine.py was never touched during F7" -- is a FIXED
historical fact about a closed range, not a live invariant that should keep
tracking HEAD forever. The check now diffs F7's own two fixed, hardcoded
endpoints (RED anchor 7752bb00 .. merge commit bd2c8d5d) instead of
<anchor>..HEAD:
  - Permanently correct: the historical fact does not change.
  - Shallow-clone-safe: a shallow clone lacking these specific historical
    objects makes the git diff command itself fail loudly (`fatal: bad
    object`, rc=128) rather than silently resolving to a wrong anchor --
    verified directly against the same depth=1 shallow clone used to
    reproduce defect 1 above. That failure correctly routes to the
    pre-existing "diff command failed -> skip" path, so CI now either
    genuinely checks the claim (full clone) or honestly skips (shallow
    clone) -- never silently lies green.
  - Verified: `git diff --name-only 7752bb00 bd2c8d5d -- math_engine.py` is
    empty (F7 kept its promise); the full file list in that range matches
    this docstring's stated scope exactly (alpha_bot_execution.py,
    static/index.js, templates/table_partial.html, and test/doc files).

BACKLOG: the sibling tests/test_scope_guard.py (DE-EOD-BASIS-001) has the
IDENTICAL unbounded <anchor>..HEAD design and is very likely subject to the
same eventual false-failure class once any later cycle touches its
forbidden files -- flagged to ga2-doc for tracking. Not fixed here: it
belongs to a different AC/historical cycle, out of this cycle's scope.

AC-5 is now a fixed historical-range constraint test:
  - Both F7 endpoints resolvable (normal full-clone case): PASS (verified
    empty diff) or FAIL (would mean F7's own shipped history was rewritten,
    which should never happen on a merged, deployed PR).
  - Either endpoint unresolvable (shallow clone, git unavailable): SKIP.
"""

from __future__ import annotations

import subprocess
import sys

# F7 cycle's own fixed historical boundaries (PR #99, math-f7). Hardcoded
# because F7 is a shipped, closed cycle -- these are permanent facts, not
# re-derived from a live git-log walk (see rewrite rationale above: dynamic
# anchor discovery via `git log --follow` is what broke under a shallow CI
# checkout).
_F7_RED_ANCHOR_SHA = "7752bb00f81b3d7dd3aa5ead4de15d32684ddcfe"
_F7_MERGE_SHA = "bd2c8d5d148e528bf1a12509656eebd0deb7f5ae"  # Merge pull request #99


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


def _get_f7_cycle_diff() -> list[str] | None:
    """Return files changed within F7's own shipped cycle (fixed RED-anchor
    to fixed merge-commit range -- see module docstring).

    Returns None when the two historical commits aren't resolvable in the
    current git environment (shallow clone missing that history, or git
    unavailable) -- callers must skip, never treat None as "no violations".
    """
    rc, output = _run_git("diff", "--name-only", _F7_RED_ANCHOR_SHA, _F7_MERGE_SHA)
    if rc != 0:
        return None
    return [ln.strip() for ln in output.splitlines() if ln.strip()]


def test_math_engine_not_in_diff() -> None:
    """
    AC-5: math_engine.py must not appear in F7's own shipped diff (the fixed
    historical range 7752bb00..bd2c8d5d -- PR #99). See module docstring for
    why this replaced the original <dynamic-anchor>..HEAD design.
    """
    changed = _get_f7_cycle_diff()
    if changed is None:
        import pytest

        pytest.skip(
            f"git diff --name-only {_F7_RED_ANCHOR_SHA[:8]} {_F7_MERGE_SHA[:8]} failed "
            "-- likely a shallow clone missing F7's historical commits, or git "
            "unavailable. This scope guard can only be evaluated with full git history."
        )

    violations = [f for f in changed if "math_engine.py" in f]

    assert not violations, (
        f"AC-5 SCOPE VIOLATION: math_engine.py appears in F7's own shipped diff "
        f"({_F7_RED_ANCHOR_SHA[:8]}..{_F7_MERGE_SHA[:8]}, PR #99). Found: "
        f"{violations}. This is a fixed historical range -- if this assertion "
        f"ever fails, F7's own shipped history has been rewritten, which should "
        f"never happen on a merged, deployed PR."
    )


def test_scope_guard_diff_command_is_runnable() -> None:
    """
    Meta: the git command used by this guard must be runnable in the test
    environment (requires a clone with F7's two historical commits present).
    """
    changed = _get_f7_cycle_diff()
    if changed is None:
        import pytest

        pytest.skip(
            f"git diff failed for the fixed F7 range "
            f"{_F7_RED_ANCHOR_SHA[:8]}..{_F7_MERGE_SHA[:8]} -- shallow clone or git unavailable."
        )

    print(
        f"\n[scope_guard_f7] {len(changed)} file(s) changed within F7's own "
        f"shipped cycle ({_F7_RED_ANCHOR_SHA[:8]}..{_F7_MERGE_SHA[:8]}):",
        file=sys.stderr,
    )
    for f in changed:
        print(f"  {f}", file=sys.stderr)

    assert isinstance(changed, list), (
        f"_get_f7_cycle_diff() must return a list; got {type(changed).__name__!r}."
    )
