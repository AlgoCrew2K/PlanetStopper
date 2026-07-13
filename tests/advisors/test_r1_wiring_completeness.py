"""RED tests — AC-4/AC-5 wiring-completeness backstop (advisor-remediation-r1).

WHY THIS FILE EXISTS (the "wired 3 of 4 call sites" trap):
  The behavioral production-wiring tests (test_asset_swap_production_wiring.py,
  test_logic_change_production_wiring.py) prove PBO/SPY BITE on the specific
  call paths they drive end-to-end. But a behavioral test can only cheaply
  exercise one or two call sites per run — it cannot cheaply prove EVERY
  reachable evaluate_candidate_batch call site in a file is wired, because most
  sites differ only in which branch of the calling function is taken (the
  zero-candidate defensive branch vs. the real N=1 branch vs. the real batch
  branch), and setting up distinct fixtures to drive all of them behaviorally
  is expensive and still wouldn't catch "the constructor of BacktestCandidate
  itself never sets dated_returns=" if a future edit added a 7th call site that
  happened to also omit it.

  This file is a SOURCE-LEVEL completeness guard via `ast` parsing: it
  enumerates every `BacktestCandidate(` construction and every
  `evaluate_candidate_batch(` call in asset_swap_engine.py and
  logic_change_engine.py and asserts each one passes the wiring keyword. This
  is what catches "wired 3 of 4".

CONTRACT (revised, per team-lead + r1-engine's approved design — supersedes
  the file's original "all 6 call sites" framing, which would have failed a
  CORRECT implementation):
  - AC-4 (dated_returns=): wired ONCE, at the single `BacktestCandidate(...)`
    construction inside each engine's `_evaluate_single_variant` helper — NOT
    per evaluate_candidate_batch call site. Every real gate call (operator N=1
    and weekly batch alike) routes through that one helper, so a single-site
    fix automatically reaches all of them.
  - AC-5 (spy_returns_fn=): wired at the 4 REAL evaluate_candidate_batch call
    sites only — ase.py `propose_operator_swap` (:1075) + `suggest_swaps`
    (:1277), lce.py `propose_operator_logic_change` (:1316) +
    `suggest_logic_changes` (:1466). The 2 EMPTY-BRANCH sites (ase.py :1047,
    lce.py :1289 — the "backtest failed / tweak unparseable, zero candidates
    to gate" defensive early-returns, both calling
    `evaluate_candidate_batch([], ...)` with a literal empty list) are EXEMPT:
    independently verified (not taken on r1-engine's citation alone) at
    `backtest_gate_engine.py:627-633` — `if not candidates: return
    GatedBatch(results=[], survivors=[], n_candidates=0, fdr_q=...)` fires
    before spy_returns_fn (or dated_returns/PBO) is ever read, so there is
    nothing for the empty-list call to wire. The AST filter below identifies
    these sites structurally (first positional arg is an empty-list literal),
    not by hardcoded line number, so it survives incidental line drift.
  - Both engines still get all 6 sites' PBO/SPY-inert-at-N=1 behavior proven
    end-to-end by the behavioral production-wiring tests (which DO exercise
    the N=1 operator-Evaluate path for real).

WHAT THIS DOES NOT PROVE:
  This guard proves the SOURCE passes the right keyword. It does NOT prove the
  keyword's VALUE is correct (e.g. a genuinely-populated dated_returns dict vs.
  an empty one, or a spy_returns_fn that actually fetches SPY). That is what
  the behavioral production-wiring tests are for. The two test families are
  deliberately complementary, not redundant — see the module docstring pattern
  established by test_cull_production_wiring.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ASE_PATH = _REPO_ROOT / "advisors" / "asset_swap_engine.py"
_LCE_PATH = _REPO_ROOT / "advisors" / "logic_change_engine.py"


def _call_sites(source_path: Path, target_names: set[str]) -> list[ast.Call]:
    """Return every ast.Call node in `source_path` whose callee's final
    dotted-name component (e.g. "BacktestCandidate" out of a bare name, or
    "evaluate_candidate_batch" out of a bare name) is in `target_names`.

    Deliberately generic across `Name` and `Attribute` callees so a future
    refactor to a qualified import (e.g. `gate_engine.evaluate_candidate_batch`)
    does not silently escape detection.
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    sites: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in target_names:
            sites.append(node)
    return sites


def _kwarg_names(call: ast.Call) -> set[str]:
    """Keyword-argument names present on this call (ignores **kwargs spreads,
    which none of the production call sites use for this signature)."""
    return {kw.arg for kw in call.keywords if kw.arg is not None}


def _has_empty_list_literal_first_arg(call: ast.Call) -> bool:
    """True iff this call's first positional argument is a literal `[]` — the
    structural signature of the two defensive zero-candidate branches
    (`evaluate_candidate_batch([], ...)`), distinguished from the real calls
    (which pass `[bt_candidate]` or a `bt_candidates` variable) by CONTENT,
    never by hardcoded line number."""
    if not call.args:
        return False
    first = call.args[0]
    return isinstance(first, ast.List) and len(first.elts) == 0


def _real_evaluate_candidate_batch_sites(source_path: Path) -> list[ast.Call]:
    """The evaluate_candidate_batch call sites that actually gate candidates —
    excludes the empty-list defensive branches (see module docstring CONTRACT)."""
    return [
        c
        for c in _call_sites(source_path, {"evaluate_candidate_batch"})
        if not _has_empty_list_literal_first_arg(c)
    ]


# ---------------------------------------------------------------------------
# Self-guard: confirm the scan actually finds the expected call-site counts.
# A guard that silently finds 0 sites (e.g. because of a parser mismatch or a
# renamed function) would pass vacuously and prove nothing — this pins the
# audit's own call-site count so a future refactor that changes the count is
# forced to touch this test consciously.
# ---------------------------------------------------------------------------


def test_self_guard_finds_three_evaluate_candidate_batch_sites_in_asset_swap_engine():
    sites = _call_sites(_ASE_PATH, {"evaluate_candidate_batch"})
    assert len(sites) == 3, (
        f"Expected 3 evaluate_candidate_batch call sites in asset_swap_engine.py "
        f"(ase.py :1047/:1075/:1277 per the audit) — found {len(sites)}. If this "
        f"is a deliberate refactor, update the completeness guard's expected count."
    )


def test_self_guard_finds_three_evaluate_candidate_batch_sites_in_logic_change_engine():
    sites = _call_sites(_LCE_PATH, {"evaluate_candidate_batch"})
    assert len(sites) == 3, (
        f"Expected 3 evaluate_candidate_batch call sites in logic_change_engine.py "
        f"(lce.py :1289/:1316/:1466 per the audit) — found {len(sites)}. If this "
        f"is a deliberate refactor, update the completeness guard's expected count."
    )


def test_self_guard_finds_exactly_one_empty_list_branch_per_engine():
    """Pins the AST-content filter itself: exactly 1 of the 3 raw sites per
    engine is the defensive `evaluate_candidate_batch([], ...)` branch. If a
    future edit adds or removes an empty-branch call, this forces conscious
    review of the AC-5 exemption rather than silently mis-scoping it."""
    for path, name in [(_ASE_PATH, "asset_swap_engine"), (_LCE_PATH, "logic_change_engine")]:
        all_sites = _call_sites(path, {"evaluate_candidate_batch"})
        empty_branch = [c for c in all_sites if _has_empty_list_literal_first_arg(c)]
        assert len(empty_branch) == 1, (
            f"Expected exactly 1 empty-list-literal evaluate_candidate_batch call "
            f"in {name} — found {len(empty_branch)} of {len(all_sites)} total sites."
        )


def test_self_guard_finds_one_backtest_candidate_construction_per_engine():
    # Both engines route every entry point through a single _evaluate_single_variant
    # helper, so there is exactly ONE BacktestCandidate(...) construction site per
    # file today. If a future edit adds a second construction path, this guard
    # forces conscious update rather than silently exempting the new site.
    ase_sites = _call_sites(_ASE_PATH, {"BacktestCandidate"})
    lce_sites = _call_sites(_LCE_PATH, {"BacktestCandidate"})
    assert len(ase_sites) == 1, f"Expected 1 BacktestCandidate(...) site in asset_swap_engine.py, found {len(ase_sites)}"
    assert len(lce_sites) == 1, f"Expected 1 BacktestCandidate(...) site in logic_change_engine.py, found {len(lce_sites)}"


# ---------------------------------------------------------------------------
# AC-4 — every BacktestCandidate construction threads dated_returns=.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "engine_path,engine_name",
    [(_ASE_PATH, "asset_swap_engine"), (_LCE_PATH, "logic_change_engine")],
)
def test_ac4_every_backtest_candidate_construction_passes_dated_returns_kwarg(
    engine_path, engine_name
):
    """WIRING COMPLETENESS (AC-4): every BacktestCandidate(...) construction in
    this engine file must pass dated_returns=. Today both engines omit it
    entirely (defaults to {} per the BacktestCandidate NamedTuple field default),
    which is exactly why _batch_pbo stays None and the PBO veto can never fire —
    MUST FAIL pre-wiring."""
    sites = _call_sites(engine_path, {"BacktestCandidate"})
    assert sites, f"No BacktestCandidate(...) construction found in {engine_path.name} — self-guard should have caught this"
    missing = [s.lineno for s in sites if "dated_returns" not in _kwarg_names(s)]
    assert not missing, (
        f"AC-4 WIRING GAP in {engine_name}: BacktestCandidate(...) at line(s) "
        f"{missing} does not pass dated_returns= — the batch PBO veto is "
        f"structurally incapable of firing for candidates built here (audit F2)."
    )


# ---------------------------------------------------------------------------
# AC-5 — the 4 REAL evaluate_candidate_batch call sites thread spy_returns_fn=.
# Per the revised contract (module docstring): the 2 empty-list defensive
# branches are exempt — nothing to wire when there are zero candidates to gate.
# The N=1 operator-Evaluate site IS one of the 4 real sites and IS required —
# a below-SPY single candidate must visibly WITHHOLD (see the N=1 behavioral
# tests in the production-wiring files).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "engine_path,engine_name",
    [(_ASE_PATH, "asset_swap_engine"), (_LCE_PATH, "logic_change_engine")],
)
def test_ac5_real_evaluate_candidate_batch_calls_pass_spy_returns_fn_kwarg(
    engine_path, engine_name
):
    """WIRING COMPLETENESS (AC-5): every REAL (non-empty-list) evaluate_candidate_batch(...)
    call site in this engine file must pass spy_returns_fn=. Today both real
    sites in both engines omit it, so _effective_default_oos_alpha stays at
    the always-0.0 default_oos_alpha (beats-a-flat-0.0%-return) instead of the
    SPY-relative baseline — MUST FAIL pre-wiring."""
    sites = _real_evaluate_candidate_batch_sites(engine_path)
    assert len(sites) == 2, (
        f"Expected exactly 2 REAL evaluate_candidate_batch call sites in "
        f"{engine_path.name} (the N=1 operator-Evaluate + weekly-batch paths) "
        f"— found {len(sites)}. Self-guard mismatch, review before trusting "
        f"this test's result."
    )
    missing = [s.lineno for s in sites if "spy_returns_fn" not in _kwarg_names(s)]
    assert not missing, (
        f"AC-5 WIRING GAP in {engine_name}: evaluate_candidate_batch(...) at "
        f"line(s) {missing} does not pass spy_returns_fn= — the SPY-relative OOS "
        f"baseline never bites at these call sites; they silently keep the "
        f"beats-a-flat-0.0%-return default (audit F2, the exact permissiveness "
        f"AC-25/edge-14 eliminated for Strategy Builder only)."
    )
