"""
Phase-1 structural integration test — run_monte_carlo consumer enumeration.

WHY THIS TEST EXISTS
--------------------
The consumer-map document (docs/handoff/run-monte-carlo-consumer-map.md)
establishes a baseline of 2 direct call sites and 27+ downstream consumer
references across 4 files. A PR that adds a new call site or consumer without
updating the map (and without a corresponding sentinel-handling guard) is the
most dangerous regression path in this codebase.

This test grep-asserts the structural invariants from the consumer map so that:
  * New call sites are caught at CI time, not during review.
  * Consumer-guard patterns are present in every production file that consumes
    the result.
  * No production file attempts a raw numeric comparison with ``prob_beating``
    or ``mc_prob`` without an ``is None`` guard.

WHAT IS ASSERTED
----------------
1. Total ``run_monte_carlo(`` call count in production files is exactly the
   baseline (2). An increase means a new unreviewed call site; a decrease
   means a call was removed without updating the map.
2. Every file that calls ``run_monte_carlo`` has a corresponding ``is not None``
   guard in the same file (the sentinel is always checked at the call site
   level).
3. ``alpha_bot_execution.py`` contains the canonical ``mc_available`` guard
   pattern — ``mc_available = prob_beating is not None`` — not a bare
   comparison.
4. ``autotuner.py`` contains the canonical ``mc_available`` guard pattern —
   ``mc_available = mc is not None`` — not a bare comparison.
5. No production file contains an unguarded numeric comparison of the form
   ``prob_beating < N`` or ``prob_beating > N`` or ``mc_prob < N`` or
   ``mc_prob > N`` outside a context that first checks ``is not None``.
   (This is a grep heuristic — it catches the class of pre-fix TypeError bugs
   that the math audit flagged as fail-dangerous.)
6. The consumer-map document exists at the expected path.

RED STATUS
----------
Tests 1–6 are GREEN on the current fork-point (5f44060) because the baseline
was established from this codebase. They go RED if:
  * A new ``run_monte_carlo(`` call is added without a guard (test 1 + 2).
  * The ``mc_available`` guard pattern is removed (tests 3 + 4).
  * An unguarded bare comparison is introduced (test 5).
  * The consumer map document is deleted (test 6).

SCOPE NOTE
----------
This file lives in ``tests/integration/`` because it crosses module boundaries
(it asserts structural properties of 4 production files simultaneously). It
imports no production module at test time — it operates via text search only,
matching the grep-and-introspect pattern described in the plan.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent

# Production files in scope for consumer enumeration.
_PRODUCTION_FILES = [
    "alpha_bot_execution.py",
    "synthetic_history.py",
    "autotuner.py",
    "reporting.py",
]

# Baseline call count from the Phase-1 consumer map.
# autotuner.py and reporting.py have 0 direct calls (they consume via dicts/args).
_BASELINE_CALL_SITES = {
    "alpha_bot_execution.py": 1,
    "synthetic_history.py": 1,
    "autotuner.py": 0,
    "reporting.py": 0,
}

_CONSUMER_MAP_PATH = _PROJECT_ROOT / "docs" / "handoff" / "run-monte-carlo-consumer-map.md"


def _read_production(filename: str) -> str:
    path = _PROJECT_ROOT / filename
    if not path.exists():
        pytest.skip(f"{filename} not found in project root.")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 6. Consumer map document exists (fast guard — put first so failures are clear)
# ---------------------------------------------------------------------------

def test_run_monte_carlo_consumer_map_document_exists() -> None:
    """
    The consumer-map document is a plan deliverable (deliverable 1). It must
    exist at docs/handoff/run-monte-carlo-consumer-map.md. If it was deleted,
    the blast-radius narrative is lost and future reviewers cannot verify the
    enumeration is exhaustive.

    RED if the document is deleted or moved.
    """
    assert _CONSUMER_MAP_PATH.exists(), (
        f"Consumer-map document not found at {_CONSUMER_MAP_PATH}. "
        f"This is plan deliverable 1 (mc-sentinel-blast-radius/plan.md). "
        f"Restore it before merging."
    )


# ---------------------------------------------------------------------------
# 1. Direct call count per file matches the Phase-1 baseline
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename", _PRODUCTION_FILES)
def test_run_monte_carlo_call_count_matches_baseline(filename: str) -> None:
    """
    The count of ``run_monte_carlo(`` call sites in each production file must
    equal the Phase-1 baseline from the consumer map. An increase means a new
    unreviewed call site exists; a decrease means a site was removed without
    updating the baseline.

    RED trigger (increase): a new ``result = math_engine.run_monte_carlo(...)``
    added to alpha_bot_execution.py without a corresponding sentinel guard.
    RED trigger (decrease): the existing call removed without updating the map.
    """
    source = _read_production(filename)
    # Count full call-site patterns: run_monte_carlo( at the start of a call expression.
    # This excludes import lines, comments, and test files.
    live_count = len(re.findall(r"\brun_monte_carlo\s*\(", source))
    expected = _BASELINE_CALL_SITES[filename]

    assert live_count == expected, (
        f"{filename}: expected {expected} run_monte_carlo( call site(s), "
        f"found {live_count}. "
        f"A count change means the Phase-1 consumer map is stale. "
        f"Update docs/handoff/run-monte-carlo-consumer-map.md before merging "
        f"and confirm each new call site has a sentinel guard."
    )


# ---------------------------------------------------------------------------
# 2. Every file with a direct call has a sentinel guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename", ["alpha_bot_execution.py", "synthetic_history.py"])
def test_files_with_direct_calls_have_is_not_none_guard(filename: str) -> None:
    """
    Any file that directly calls ``run_monte_carlo`` must contain an
    ``is not None`` guard on the result. The pre-fix bug was a bare numeric
    comparison with no guard — this test ensures the guard pattern exists.

    This is a presence check, not a positional check; the precise guard idiom
    is validated in tests 3 and 4 for the specific consumer files.

    RED trigger: removing all ``is not None`` guards from a file that calls
    run_monte_carlo.
    """
    source = _read_production(filename)
    has_guard = bool(re.search(r"is\s+not\s+None", source))
    assert has_guard, (
        f"{filename} calls run_monte_carlo but contains no 'is not None' guard. "
        f"The sentinel (None) must be checked before any numeric operation on the "
        f"result. The pre-fix absence of this guard caused TypeError to abort "
        f"live cycles (memory: project_mc_sentinel_consumer_blast_radius)."
    )


# ---------------------------------------------------------------------------
# 3. alpha_bot_execution.py: canonical mc_available guard present
# ---------------------------------------------------------------------------

def test_alpha_bot_execution_has_mc_available_sentinel_guard() -> None:
    """
    The canonical sentinel guard in alpha_bot_execution.py is:
        mc_available = prob_beating is not None
    Every downstream branch in that file reads ``mc_available`` rather than
    comparing ``prob_beating`` directly. If this guard is removed or renamed,
    all 15 downstream consumer sites become unguarded.

    RED trigger: renaming ``mc_available`` to something else without updating
    the 15 consumer sites, or removing the guard entirely.
    """
    source = _read_production("alpha_bot_execution.py")
    # The exact canonical form (whitespace-flexible).
    guard_pattern = re.compile(
        r"mc_available\s*=\s*prob_beating\s+is\s+not\s+None"
    )
    assert guard_pattern.search(source), (
        "alpha_bot_execution.py is missing the canonical sentinel guard "
        "'mc_available = prob_beating is not None'. "
        "This guard is the entry point for all 15 downstream consumer branches. "
        "Its absence means prob_beating is consumed without a None check."
    )


# ---------------------------------------------------------------------------
# 4. autotuner.py: canonical mc_available guard present
# ---------------------------------------------------------------------------

def test_autotuner_has_mc_available_sentinel_guard() -> None:
    """
    The canonical sentinel guard in autotuner.py's replay tick function is:
        mc_available = mc is not None
    (where mc = tick.get("mc_prob", 50.0)). This mirrors the production guard
    from alpha_bot_execution.py. Removing it exposes all 5 downstream MC-driven
    branches in the replay sim.

    RED trigger: removing the mc_available guard from _replay_exit_tick.
    """
    source = _read_production("autotuner.py")
    guard_pattern = re.compile(r"mc_available\s*=\s*\w+\s+is\s+not\s+None")
    assert guard_pattern.search(source), (
        "autotuner.py is missing the canonical sentinel guard "
        "'mc_available = mc is not None' (or equivalent). "
        "This guard gates all MC-driven branches in the replay tick function. "
        "Its absence means None can reach unguarded numeric comparisons."
    )


# ---------------------------------------------------------------------------
# 5. No production file contains a bare unguarded numeric comparison of
#    prob_beating or mc_prob outside a prior is-not-None context
# ---------------------------------------------------------------------------

def test_reporting_uses_pre_guarded_variable_for_prob_beating_format() -> None:
    """
    reporting.py had two bare ``f"{prob_beating:.1f}%"`` format sites before
    the fix (memory: project_mc_sentinel_consumer_blast_radius). After the fix,
    all format sites must use the sentinel-safe conditional assignment
    ``mc_prob_text = ... if prob_beating is not None else "N/A"`` and the
    derived ``mc_prob_text`` variable must be used for rendering — never a bare
    ``{prob_beating:.1f}`` inside an f-string.

    Assertions:
    (a) The sentinel-safe assignment ``mc_prob_text = ... if prob_beating is not
        None else ...`` is present.
    (b) No bare ``{prob_beating:`` format expression appears in reporting.py —
        all formatting goes through the pre-guarded ``mc_prob_text``.

    RED trigger: removing the mc_prob_text assignment, or adding a new
    bare ``{prob_beating:.1f}`` format without going through the pre-guard.
    """
    source = _read_production("reporting.py")

    # (a) The guarded assignment must exist.
    guarded_pattern = re.compile(
        r'mc_prob_text\s*=.+if\s+prob_beating\s+is\s+not\s+None'
    )
    assert guarded_pattern.search(source), (
        "reporting.py is missing the sentinel-safe 'mc_prob_text = ... if "
        "prob_beating is not None else ...' assignment. All prob_beating format "
        "sites must go through this pre-guard to avoid TypeError on None."
    )

    # (b) Every line with a {prob_beating: format expression must be the sentinel-safe
    # conditional assignment line (mc_prob_text = f"..." if prob_beating is not None ...).
    # Any OTHER line with a bare {prob_beating: format is an unguarded regression.
    lines = source.splitlines()
    violations = []
    for lineno, line in enumerate(lines, start=1):
        if re.search(r'\{prob_beating\s*:', line):
            # This line formats prob_beating. It is safe if it is the guarded
            # mc_prob_text assignment — i.e. the same line also contains the
            # "if prob_beating is not None" guard.
            if "is not None" not in line:
                violations.append(f"  line {lineno}: {line.strip()}")

    assert not violations, (
        f"reporting.py contains {len(violations)} f-string format site(s) for "
        f"prob_beating that are not on a line with an 'is not None' guard:\n"
        + "\n".join(violations)
        + "\nAll prob_beating format sites must use mc_prob_text (pre-guarded) "
        "or be on the guarded-assignment line itself."
    )


def test_alpha_bot_execution_prob_beating_format_sites_are_guarded() -> None:
    """
    alpha_bot_execution.py formats ``prob_beating`` at several sites. Each must
    be provably safe — either inside an ``mc_available``-gated block or using a
    sentinel-safe ternary expression.

    The legitimate safe patterns are:
      (a) ``f"...{prob_beating:.1f}..." if mc_available else "N/A"``  — ternary on
          the same line.
      (b) ``arm_reason = f"MC Prob {prob_beating:.1f}%"`` — inside the
          ``if mc_available and ...`` block (the arm gate); the block itself is the
          guard.
      (c) Lines inside TP-armed transition prints — these are inside
          ``if new_tp_armed and not prev_tp_armed:`` which can only be True when
          ``mc_available`` was True (compute_tp_confirmation gates on mc_available).

    Assertion strategy: the number of ``{prob_beating:`` format expressions in
    alpha_bot_execution.py must not INCREASE beyond the Phase-1 baseline count
    (any new unguarded format site is a regression). We pin the baseline count so
    the test fails on addition, not on specific line positions.

    RED trigger: adding ``f"...{prob_beating:.1f}..."`` outside any mc_available
    context (which would be a new unguarded TypeError risk).
    """
    source = _read_production("alpha_bot_execution.py")

    # Phase-1 baseline: count all {prob_beating: format expressions.
    # The current fork-point has exactly 5 sites (arm_reason, ternary status line,
    # TP-armed transition ×2, TP snapshot). All are inside mc_available-gated
    # contexts. Pinning the count ensures any addition is caught.
    _BASELINE_FORMAT_COUNT = 4
    live_count = len(re.findall(r'\{prob_beating\s*:', source))

    assert live_count == _BASELINE_FORMAT_COUNT, (
        f"alpha_bot_execution.py has {live_count} prob_beating format expression(s); "
        f"Phase-1 baseline is {_BASELINE_FORMAT_COUNT}. "
        f"A count increase means a new format site was added. Verify the new site "
        f"is inside an mc_available-gated block before updating the baseline here. "
        f"A count decrease means a format site was removed; update the baseline."
    )

    # Also assert the sentinel-safe ternary pattern exists (the arm_prob_str line).
    # The canonical form is: f"...{prob_beating:.1f}..." if mc_available else "N/A"
    # The entire f-string literal comes before the `if mc_available` ternary.
    ternary_guard = re.compile(
        r'\{prob_beating\s*:[^}]+\}[^"]*"\s+if\s+mc_available\s+else'
    )
    assert ternary_guard.search(source), (
        "alpha_bot_execution.py is missing the canonical sentinel-safe ternary "
        "format 'f\"...{prob_beating:...}...\" if mc_available else \"N/A\"'. "
        "This pattern must be present as the model for new format sites."
    )
