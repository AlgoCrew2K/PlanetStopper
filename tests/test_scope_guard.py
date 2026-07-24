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

REWRITTEN 2026-07-24 (exit-friction-realized-savings cycle -- same defect
class independently found on tests/test_scope_guard_f7.py, PM-directed
fork-point trace, ga2-doc's recon on this sibling, ga2-tw fix). Both tests
were failing (2/2) on the current tree via the same two defects fixed on
the F7 sibling at bb731525:

1. SHALLOW-CLONE FALSE PASS (silent, in CI): the original
   `_get_eod_cycle_start_sha()` used `git log --follow` to discover the
   DE-EOD-BASIS-001 RED anchor dynamically. `.github/workflows/tests.yml`
   uses `actions/checkout@v4` with no `fetch-depth` override -- CI's default
   shallow clone (depth 1). In a shallow clone, `git log --follow` can only
   see the checkout's own tip commit -- it silently resolves the "anchor" to
   HEAD itself instead of the true origin, so the diff-since-anchor
   collapses to `git diff <sha> <same sha>`, always empty. CI has therefore
   been passing this test vacuously -- the assertion never actually ran
   against real history there.

2. UNBOUNDED-RANGE FALSE FAIL (in any full clone, forever after): diffing
   <anchor>..HEAD means ANY future, unrelated cycle that legitimately
   touches alpha_bot_execution.py or math_engine.py breaks this test
   permanently, on every branch downstream, regardless of whether
   DE-EOD-BASIS-001 itself stayed in scope. Confirmed independently: this
   test also fails on feat/exit-friction-realized-savings' own fork point
   (ccda9abe), before this cycle's first commit -- pre-existing, unrelated.

FIX: DE-EOD-BASIS-001 (PR #89) was squash-merged as ONE commit -- RED and
GREEN landed together at 848acf94, with no separate earlier RED commit to
anchor from. Its scope claim -- "alpha_bot_execution.py / math_engine.py
untouched by this cycle" -- is therefore a fixed historical fact about that
ONE commit's own diff against its parent, not a live invariant tracking
HEAD forever. The check now diffs the squash commit's two fixed, hardcoded
endpoints (its parent .. itself) instead of <dynamic-anchor>..HEAD:
  - Permanently correct: the historical fact does not change.
  - Shallow-clone-safe: a shallow clone lacking these specific historical
    objects makes the git diff command itself fail loudly (`fatal: bad
    object`, verified via the same depth=1 reproduction technique used on
    the F7 sibling) rather than silently resolving to a wrong anchor --
    routes to the pre-existing "diff command failed -> skip" path.
  - Verified: `git diff --name-only 30b89c01 848acf94 -- alpha_bot_execution.py
    math_engine.py` is empty (DE-EOD-BASIS-001 kept its promise); the full
    file list in that single-commit diff matches this docstring's stated
    scope exactly (app.py, analytics/test fixtures, docs, and this guard
    file's own addition -- no forbidden file).

AC-5 is now a fixed historical-range constraint test:
  - Both endpoints resolvable (normal full-clone case): PASS (verified
    empty diff) or FAIL (would mean DE-EOD-BASIS-001's own shipped history
    was rewritten, which should never happen on a merged, deployed PR).
  - Either endpoint unresolvable (shallow clone, git unavailable): SKIP.
"""

from __future__ import annotations

import subprocess
import sys

# DE-EOD-BASIS-001's own fixed historical boundaries (PR #89, squash-merged
# as a single commit -- RED and GREEN together, so the "cycle range" is that
# one commit's own diff against its parent). Hardcoded because this is a
# shipped, closed cycle -- these are permanent facts, not re-derived from a
# live git-log walk (see rewrite rationale above: dynamic anchor discovery
# via `git log --follow` is what broke under a shallow CI checkout).
_EOD_PARENT_SHA = "30b89c01d54c8494851e8ecb846d9ed51348fe9c"
_EOD_SQUASH_SHA = "848acf94396dc674d01b098ac5ab5e4bf39ae681"  # PR #89


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


def _get_eod_cycle_diff() -> list[str] | None:
    """Return files changed within DE-EOD-BASIS-001's own shipped, squashed
    commit (fixed parent-to-commit range -- see module docstring).

    Returns None when the two historical commits aren't resolvable in the
    current git environment (shallow clone missing that history, or git
    unavailable) -- callers must skip, never treat None as "no violations".
    """
    rc, output = _run_git("diff", "--name-only", _EOD_PARENT_SHA, _EOD_SQUASH_SHA)
    if rc != 0:
        return None
    return [ln.strip() for ln in output.splitlines() if ln.strip()]


def test_alpha_bot_execution_not_in_diff() -> None:
    """
    AC-5: alpha_bot_execution.py must not appear in DE-EOD-BASIS-001's own
    shipped diff (the fixed range 30b89c01..848acf94 -- PR #89). A
    historical fact-check, not a live-forever constraint; see module
    docstring for why this replaced the original <anchor>..HEAD design.
    """
    changed = _get_eod_cycle_diff()
    if changed is None:
        import pytest

        pytest.skip(
            f"git diff --name-only {_EOD_PARENT_SHA[:8]} {_EOD_SQUASH_SHA[:8]} failed "
            "-- likely a shallow clone missing DE-EOD-BASIS-001's historical commits, "
            "or git unavailable. This scope guard can only be evaluated with full "
            "git history."
        )

    violations = [f for f in changed if "alpha_bot_execution.py" in f]

    assert not violations, (
        f"AC-5 SCOPE VIOLATION: alpha_bot_execution.py appears in "
        f"DE-EOD-BASIS-001's own shipped diff "
        f"({_EOD_PARENT_SHA[:8]}..{_EOD_SQUASH_SHA[:8]}, PR #89). Found: "
        f"{violations}. This is a fixed historical range -- if this assertion "
        f"ever fails, DE-EOD-BASIS-001's own shipped history has been "
        f"rewritten, which should never happen on a merged, deployed PR."
    )


def test_math_engine_not_in_diff() -> None:
    """
    AC-5: math_engine.py must not appear in DE-EOD-BASIS-001's own shipped
    diff (the fixed range 30b89c01..848acf94 -- PR #89). See module
    docstring for why this replaced the original <anchor>..HEAD design.
    """
    changed = _get_eod_cycle_diff()
    if changed is None:
        import pytest

        pytest.skip(
            f"git diff --name-only {_EOD_PARENT_SHA[:8]} {_EOD_SQUASH_SHA[:8]} failed "
            "-- likely a shallow clone missing DE-EOD-BASIS-001's historical commits, "
            "or git unavailable. This scope guard can only be evaluated with full "
            "git history."
        )

    violations = [f for f in changed if "math_engine.py" in f]

    assert not violations, (
        f"AC-5 SCOPE VIOLATION: math_engine.py appears in DE-EOD-BASIS-001's "
        f"own shipped diff ({_EOD_PARENT_SHA[:8]}..{_EOD_SQUASH_SHA[:8]}, "
        f"PR #89). Found: {violations}. This is a fixed historical range -- "
        f"if this assertion ever fails, DE-EOD-BASIS-001's own shipped "
        f"history has been rewritten, which should never happen on a merged, "
        f"deployed PR."
    )


def test_scope_guard_diff_command_is_runnable() -> None:
    """
    Meta: the git command used by this guard must be runnable in the test
    environment (requires a clone with DE-EOD-BASIS-001's two historical
    commits present).
    """
    changed = _get_eod_cycle_diff()
    if changed is None:
        import pytest

        pytest.skip(
            f"git diff failed for the fixed DE-EOD-BASIS-001 range "
            f"{_EOD_PARENT_SHA[:8]}..{_EOD_SQUASH_SHA[:8]} -- shallow clone or "
            "git unavailable."
        )

    print(
        f"\n[scope_guard] {len(changed)} file(s) changed within DE-EOD-BASIS-001's "
        f"own shipped commit ({_EOD_PARENT_SHA[:8]}..{_EOD_SQUASH_SHA[:8]}):",
        file=sys.stderr,
    )
    for f in changed:
        print(f"  {f}", file=sys.stderr)

    assert isinstance(changed, list), (
        f"_get_eod_cycle_diff() must return a list; got {type(changed).__name__!r}."
    )
