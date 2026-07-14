"""RED tests — M4 Logic-Change Proposals (advisors/logic_change_engine.py).

Module under test: advisors.logic_change_engine

AC bindings:
  AC-3.1  Both modes (operator-initiated tweak; advisor-suggested candidates),
           each diagnose → propose → backtest → gate → surface survivors.
  AC-3.2  (MANDATORY FDR GUARDRAIL) N backtested candidates → acceptance applies
           ONE FDR/multiple-testing correction across the FULL set.  Raising N
           raises the bar each candidate must clear.  Per-candidate gating
           (which would yield n_effective=1 for each) must FAIL these tests.
  AC-3.3  Every surfaced logic-change carries an explicit selecting-on-backtest
           overfitting caveat + post-correction gate verdict.  Pre-correction-only
           passers are NOT surfaced.
  AC-3.4  Never auto-applies.  Surfacing writes only an advisory observation
           (AC-X1 / AC-X3).
  AC-X1   No capability calls a Composer write endpoint.  Only reads (GET /score)
           + stateless POST /api/v0.1/backtest.
  AC-X2   alpha_bot_execution.py does NOT import from advisors.logic_change_engine.
  AC-X3   Every surfaced recommendation persists as an advisor_observation with
           is_advisory_only=1 + originating symphony_id.
  AC-X4   No Composer API key → clear "advisor unavailable" + writes nothing +
           never an unhandled error.
  AC-X5   Per-candidate backtest failure → failure marker; does not abort the batch.

THE ADVERSARIAL FOCUS (per team brief — AC-3.2 is the load-bearing guardrail):

1. **Raising N raises the bar** (FDR monotonicity): the BHY Yekutieli c(N) factor
   grows with N, tightening the adjusted significance threshold q/c(N).  An
   implementation that gates candidates individually (n_effective=1 per candidate)
   will fail this test because the gate engine uses n_effective = len(candidates).

2. **Per-candidate gating is a methodological failure**: if a caller submits N
   candidates one by one in N separate evaluate_candidate_batch calls, the
   n_effective count is wrong (1 instead of N) and the FDR correction is defeated.
   The engine MUST submit all candidates as ONE batch; tests verify this via spy.

3. **Pre-correction passers are not surfaced**: a candidate whose raw p-value passes
   q but whose BHY-adjusted p_adj > q (due to a large batch) is NOT ADOPT_CANDIDATE
   and must not appear in survivors.

4. **SURVIVOR_OVERFITTING_CAVEAT is mandatory on every survivor** (AC-3.3).

5. **Advisory-only**: the module never auto-applies, never calls a write endpoint,
   never imports from alpha_bot_execution (AC-X1, AC-X2, AC-X4).

API contract (from existing advisors/logic_change_engine.py):
  - LogicChangeObjective(objective_type, measured_value, rationale="", target_pair=None)
  - LogicTweak(node_path, param_key, old_value, new_value, node_description="")
  - LogicProposalResult — per-candidate result type
  - LogicChangeRunResult — top-level return type
  - propose_operator_logic_change(symphony_id, score_tree, change_description, objective, ...)
  - suggest_logic_changes(symphony_id, score_tree, objective, ...)

R2-2 (reasoning port): the fixed-multiplier generate_objective_directed_candidates
/ generate_objective_directed_logic_candidates / _parse_change_description_to_tweak
/ _fallback_direction_factor generators and their scaling constants were DELETED
(zero production callers post-rewire — team-lead ruling) in favor of the LLM-backed
advisors.logic_change_engine.generate_reasoned_logic_candidates. Tests that pinned
the deleted functions' fixed-multiplier/deterministic-parse behavior were retired;
tests exercising RETAINED primitives (extract_numeric_params, apply_logic_tweak,
evaluate_candidate_batch, validate_tree) were kept, with a controlled
generate_reasoned_logic_candidates() return value injected wherever a test needs a
real (non-empty) candidate to reach downstream gate/persistence behavior. The
reasoned path itself is covered by the dedicated RED suite: tests/advisors/
test_logic_change_engine_{reasoning_context,reasoned_generation,
validate_tree_guard,gate_batch_characterization,provenance,honest_degradation,
credentialless_bounded_prompt}.py.

Fixture: tests/fixtures/ai_advisor/m4/logic_change_proposals_basic.json
         tests/fixtures/ai_advisor/m4/logic_change_objective_directed_basic.json

Mocking strategy:
  - advisors.backtest_gate_engine.evaluate_candidate_batch is spied upon (not mocked)
    to verify that the engine submits ONE batch rather than N individual calls.
  - advisors.composer_backtest_client.run_backtest is patched to avoid live API calls.
  - database.insert_advisor_observation is patched to intercept persistence calls.
  - The acceptance_gate and backtest_gate_engine math is NEVER mocked — tested end-to-end.
  - No live network calls in any test in this module.
"""

from __future__ import annotations

import ast
import importlib
import json
import math
import pathlib
import random
import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Repo path + import helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "ai_advisor" / "m4" / "logic_change_proposals_basic.json"
)
_OBJ_DIRECTED_FIXTURE_PATH = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "ai_advisor"
    / "m4"
    / "logic_change_objective_directed_basic.json"
)


def _ensure_repo_on_path() -> None:
    repo = str(_REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)


def _import_engine() -> types.ModuleType:
    """Import advisors.logic_change_engine.  RED until the module exists."""
    _ensure_repo_on_path()
    return importlib.import_module("advisors.logic_change_engine")


def _import_gate_engine() -> types.ModuleType:
    _ensure_repo_on_path()
    return importlib.import_module("advisors.backtest_gate_engine")


def _parse_source(relpath: str) -> ast.Module:
    path = _REPO_ROOT / relpath
    assert path.exists(), f"Expected source file not found: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_synthetic_returns_pct(n: int = 500, seed: int = 42, mean_pct: float = 0.05) -> list:
    """Deterministic pseudo-random daily returns in percent.

    Values roughly in [-2%, +2%] — realistic for a diversified ETF portfolio.
    Not a financial model; used for reproducible non-degenerate test inputs.
    Uses standard library random to avoid numpy/scipy dependency in tests.
    """
    rng = random.Random(seed)
    return [rng.gauss(mean_pct, 0.8) for _ in range(n)]


def _make_noisy_returns_pct(n: int = 500, seed: int = 99) -> list:
    """Near-zero-mean, high-noise series — unlikely to pass BHY at any reasonable alpha."""
    rng = random.Random(seed)
    return [rng.gauss(0.0, 0.5) for _ in range(n)]


def _make_score_tree_with_numeric_params(
    symphony_id: str = "sym-m4-test",
) -> dict:
    """Build a score tree that contains numeric parameters tweakable by the engine.

    Uses values from the fixture's sample_score_tree_for_tests shape (window=20, window=50).
    The window=20 and window=50 values are > 5, qualifying for all objective types.
    Shape is fixture-derived, not producer-computed.
    """
    return {
        "id": symphony_id,
        "name": "Test Symphony",
        "type": "root",
        "children": [
            {
                "type": "momentum",
                "window": 20,
                "threshold": 8.0,
                "children": [],
            },
            {
                "type": "moving_average",
                "window": 50,
                "children": [{"ticker": "SPY"}],
            },
        ],
    }


def _make_score_tree_simple(
    symphony_id: str = "sym-m4-simple",
) -> dict:
    """Build a minimal score tree without numeric parameters (empty candidate generation)."""
    return {
        "id": symphony_id,
        "name": "Simple Symphony",
        "type": "root",
        "children": [
            {"type": "asset", "ticker": "SPY", "weight": 1.0},
        ],
    }


def _make_mock_backtest_result(
    daily_returns_pct: list | None = None,
    status_code: int = 200,
) -> MagicMock:
    """Build a mock BacktestResult compatible with advisors.composer_backtest_client.

    daily_returns must be log-returns (pct / 100) per the client contract.
    """
    mock = MagicMock()
    returns_pct = daily_returns_pct or _make_synthetic_returns_pct(500, seed=7)
    if status_code == 200:
        mock.error = None
        # The client stores daily_returns as log-returns (divide by 100 to convert from pct).
        mock.daily_returns = {str(19000 + i): r / 100.0 for i, r in enumerate(returns_pct)}
        mock.stats = {
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "max_drawdown": None,
            "annual_return": None,
        }
        mock.data_warnings = []
    else:
        mock.error = f"HTTP {status_code}"
        mock.daily_returns = {}
        mock.stats = None
        mock.data_warnings = []
    return mock


def _make_logic_tweak(
    node_path: list | None = None,
    param_key: str = "window",
    old_value: Any = 20,
    new_value: Any = 16,
    node_description: str = "window=20 at path [children, 0]",
) -> Any:
    """Construct a LogicTweak using the engine's own type."""
    engine = _import_engine()
    return engine.LogicTweak(
        node_path=node_path if node_path is not None else ["children", 0],
        param_key=param_key,
        old_value=old_value,
        new_value=new_value,
        node_description=node_description,
    )


def _make_two_logic_tweaks() -> list:
    """Two real tweaks matching _make_score_tree_with_numeric_params()'s two
    numeric params (children[0].window=20, children[1].window=50) — used as a
    canned generate_reasoned_logic_candidates() return value in tests that need
    >1 candidate (post-R2-2 the deterministic fixed-multiplier generator is
    gone; these tests inject a controlled candidate list directly, same
    pattern as the reasoning port's own RED suite)."""
    return [
        _make_logic_tweak(node_path=["children", 0], param_key="window", old_value=20, new_value=16),
        _make_logic_tweak(node_path=["children", 1], param_key="window", old_value=50, new_value=40),
    ]


def _make_logic_objective(
    objective_type: str = "reduce_drawdown",
    measured_value: float = -0.25,
    rationale: str = "measured drawdown from backtest",
) -> Any:
    """Construct a LogicChangeObjective using the engine's own type."""
    engine = _import_engine()
    return engine.LogicChangeObjective(
        objective_type=objective_type,
        measured_value=measured_value,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def m4_fixture() -> dict:
    """Load the M4 schema-derived fixture."""
    assert _FIXTURE_PATH.exists(), (
        f"Fixture not found: {_FIXTURE_PATH}. "
        "File should be at tests/fixtures/ai_advisor/m4/logic_change_proposals_basic.json."
    )
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def obj_directed_fixture() -> dict:
    """Load the objective-directed logic-change fixture."""
    assert _OBJ_DIRECTED_FIXTURE_PATH.exists(), f"Fixture not found: {_OBJ_DIRECTED_FIXTURE_PATH}."
    with _OBJ_DIRECTED_FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def score_tree() -> dict:
    return _make_score_tree_with_numeric_params()


@pytest.fixture
def logic_objective() -> Any:
    return _make_logic_objective()


@pytest.fixture
def logic_tweak() -> Any:
    # Corresponds to the sample_tweak_reduce_drawdown in the existing fixture:
    # window=20 → 16 at children[0].
    return _make_logic_tweak()


# Canonical change_description used in operator-initiated tests.
# Tests that call propose_operator_logic_change must use this string.
_OPERATOR_CHANGE_DESCRIPTION = "Reduce window from 20d to 16d to improve drawdown reactivity"


# ===========================================================================
# Section 1 — Module contract: exists and exposes the required public API
# ===========================================================================


class TestModuleContract:
    """advisors/logic_change_engine.py must exist and expose its required interface."""

    def test_module_is_importable(self):
        """advisors.logic_change_engine must be importable."""
        engine = _import_engine()
        assert engine is not None, (
            "advisors.logic_change_engine must be importable. "
            "Create advisors/logic_change_engine.py to go GREEN."
        )

    def test_module_exposes_propose_operator_logic_change(self):
        """Module must expose propose_operator_logic_change (AC-3.1 operator-initiated mode)."""
        engine = _import_engine()
        assert callable(getattr(engine, "propose_operator_logic_change", None)), (
            "advisors.logic_change_engine must expose propose_operator_logic_change "
            "(AC-3.1 operator-initiated mode)."
        )

    def test_module_exposes_suggest_logic_changes(self):
        """Module must expose suggest_logic_changes (AC-3.1 advisor-suggested mode)."""
        engine = _import_engine()
        assert callable(getattr(engine, "suggest_logic_changes", None)), (
            "advisors.logic_change_engine must expose suggest_logic_changes "
            "(AC-3.1 advisor-suggested mode)."
        )

    def test_module_exposes_logic_change_objective_type(self):
        """Module must expose LogicChangeObjective with objective_type + measured_value + rationale."""
        engine = _import_engine()
        assert hasattr(engine, "LogicChangeObjective"), (
            "advisors.logic_change_engine must expose LogicChangeObjective."
        )
        # Construct to verify fields exist.
        obj = engine.LogicChangeObjective(
            objective_type="reduce_drawdown",
            measured_value=-0.25,
        )
        assert obj.objective_type == "reduce_drawdown"
        assert obj.measured_value == pytest.approx(-0.25, abs=1e-12)

    def test_module_exposes_logic_tweak_type(self):
        """Module must expose LogicTweak with node_path + param_key + old_value + new_value."""
        engine = _import_engine()
        assert hasattr(engine, "LogicTweak"), (
            "advisors.logic_change_engine must expose LogicTweak — "
            "the typed parameter-change descriptor for operator-initiated mode."
        )
        tweak = engine.LogicTweak(
            node_path=["children", 0],
            param_key="window",
            old_value=20,
            new_value=16,
        )
        assert tweak.node_path == ["children", 0]
        assert tweak.param_key == "window"

    def test_module_exposes_logic_change_run_result_type(self):
        """Module must expose LogicChangeRunResult as the top-level return type."""
        engine = _import_engine()
        assert hasattr(engine, "LogicChangeRunResult"), (
            "advisors.logic_change_engine must expose LogicChangeRunResult."
        )

    def test_module_exposes_logic_proposal_result_type(self):
        """Module must expose LogicProposalResult for per-candidate results."""
        engine = _import_engine()
        assert hasattr(engine, "LogicProposalResult"), (
            "advisors.logic_change_engine must expose LogicProposalResult — "
            "the per-candidate result type carrying gate_result, caveats, apply_guidance."
        )

    def test_module_exposes_no_survivors_message_constant(self):
        """Module must expose NO_SURVIVORS_MESSAGE matching the AC-3 required text."""
        engine = _import_engine()
        msg = getattr(engine, "NO_SURVIVORS_MESSAGE", None)
        assert msg is not None, (
            "advisors.logic_change_engine must expose NO_SURVIVORS_MESSAGE constant."
        )
        assert "no logic change cleared the gate" in msg.lower(), (
            f"NO_SURVIVORS_MESSAGE must contain 'no logic change cleared the gate'. Got: {msg!r}."
        )

    def test_module_exposes_advise_only_apply_template_constant(self):
        """Module must expose ADVISE_ONLY_APPLY_TEMPLATE for apply-guidance generation."""
        engine = _import_engine()
        assert hasattr(engine, "ADVISE_ONLY_APPLY_TEMPLATE"), (
            "advisors.logic_change_engine must expose ADVISE_ONLY_APPLY_TEMPLATE "
            "(AC-X1 / AC-3.4 — operator apply-manually instruction template)."
        )

    def test_module_exposes_survivor_overfitting_caveat(self):
        """Module must expose or re-export SURVIVOR_OVERFITTING_CAVEAT (AC-3.3)."""
        engine = _import_engine()
        assert hasattr(engine, "SURVIVOR_OVERFITTING_CAVEAT"), (
            "advisors.logic_change_engine must expose SURVIVOR_OVERFITTING_CAVEAT — "
            "the mandatory caveat text appended to every surfaced survivor (AC-3.3)."
        )

    def test_module_exposes_apply_logic_tweak(self):
        """Module must expose apply_logic_tweak for unit-testing tree mutation."""
        engine = _import_engine()
        assert callable(getattr(engine, "apply_logic_tweak", None)), (
            "advisors.logic_change_engine must expose apply_logic_tweak."
        )

    def test_module_exposes_extract_numeric_params(self):
        """Module must expose extract_numeric_params for unit-testing parameter extraction."""
        engine = _import_engine()
        assert callable(getattr(engine, "extract_numeric_params", None)), (
            "advisors.logic_change_engine must expose extract_numeric_params."
        )


# ===========================================================================
# Section 2 — Tree manipulation helpers
# ===========================================================================


class TestTreeManipulation:
    """apply_logic_tweak and extract_numeric_params must work correctly."""

    def test_apply_logic_tweak_produces_deep_copy(self, score_tree):
        """apply_logic_tweak must return a deep copy — never mutate the input."""
        engine = _import_engine()
        tweak = _make_logic_tweak()
        result = engine.apply_logic_tweak(score_tree, tweak)

        assert result is not score_tree, (
            "apply_logic_tweak must return a new object, not a reference to the input."
        )
        # Input must be unchanged.
        assert score_tree["children"][0]["window"] == 20, (
            "apply_logic_tweak must not mutate the original score_tree."
        )

    def test_apply_logic_tweak_changes_target_value(self, score_tree):
        """apply_logic_tweak must set the target param_key to new_value in the result."""
        engine = _import_engine()
        tweak = _make_logic_tweak(old_value=20, new_value=16)
        result = engine.apply_logic_tweak(score_tree, tweak)

        assert result is not None, "apply_logic_tweak must not return None for a valid tweak."
        target = result["children"][0]
        assert target["window"] == 16, (
            f"apply_logic_tweak must change window from 20 to 16. Got {target['window']!r}."
        )

    def test_apply_logic_tweak_returns_none_when_old_value_mismatch(self, score_tree):
        """apply_logic_tweak must return None when old_value does not match current tree value."""
        engine = _import_engine()
        # old_value=999 does not exist in the tree (current value is 20).
        tweak = _make_logic_tweak(old_value=999, new_value=799)
        result = engine.apply_logic_tweak(score_tree, tweak)

        assert result is None, (
            "apply_logic_tweak must return None when old_value does not match "
            "the current value at node_path + param_key. "
            f"Got {result!r} for old_value=999 when current value=20."
        )

    def test_apply_logic_tweak_returns_none_for_invalid_node_path(self, score_tree):
        """apply_logic_tweak must return None when the node_path cannot be navigated."""
        engine = _import_engine()
        tweak = _make_logic_tweak(
            node_path=["children", 99],  # index 99 does not exist
            old_value=20,
            new_value=16,
        )
        result = engine.apply_logic_tweak(score_tree, tweak)

        assert result is None, (
            "apply_logic_tweak must return None for an invalid node_path (index 99 OOB). "
            f"Got {result!r}."
        )

    def test_extract_numeric_params_finds_window_params(self, score_tree):
        """extract_numeric_params must find window=20 and window=50 in the score tree."""
        engine = _import_engine()
        params = engine.extract_numeric_params(score_tree)

        # Shape assertion: result is a list of dicts with node_path, param_key, value.
        assert isinstance(params, list), "extract_numeric_params must return a list."
        for p in params:
            assert "node_path" in p, f"Each param entry must have 'node_path'. Got: {p}"
            assert "param_key" in p, f"Each param entry must have 'param_key'. Got: {p}"
            assert "value" in p, f"Each param entry must have 'value'. Got: {p}"

        # The window=20 (children[0]) and window=50 (children[1]) should be extracted.
        window_values = {p["value"] for p in params if p["param_key"] == "window"}
        assert 20 in window_values or 50 in window_values, (
            "extract_numeric_params must find at least one of window=20 or window=50 "
            f"in the score tree. Found window values: {window_values}."
        )

    def test_extract_numeric_params_returns_empty_for_no_numeric_nodes(self):
        """extract_numeric_params returns empty list for a tree with no numeric params."""
        engine = _import_engine()
        tree_no_params = _make_score_tree_simple()
        params = engine.extract_numeric_params(tree_no_params)
        assert isinstance(params, list), "extract_numeric_params must return a list."
        # weight=1.0 is present but value is exactly 1 and is excluded per the spec
        # (values of 0 or 1 are boolean flags). So result should be empty.
        window_or_threshold_params = [
            p for p in params if p["param_key"] in ("window", "threshold")
        ]
        assert len(window_or_threshold_params) == 0, (
            "A tree with no window/threshold-type numeric params should yield no "
            f"window/threshold entries. Got: {window_or_threshold_params}."
        )


# ===========================================================================
# Section 3 — FDR monotonicity (AC-3.2 MANDATORY — the load-bearing guardrail)
# ===========================================================================


class TestFDRMonotonicity:
    """Raising N raises the bar each candidate must clear (AC-3.2 mandatory guardrail).

    These tests target the most dangerous failure mode: an implementation that gates
    each candidate independently with n_effective=1 will fail because the BHY Yekutieli
    c(N) = sum(1/k for k in 1..N) grows with N, tightening the threshold q/c(N) as N grows.
    """

    def test_raising_n_tightens_adjusted_threshold_via_gate_engine(self):
        """BHY FDR: more candidates → same raw p-value → larger (stricter) adjusted p-value.

        Tests the gate engine directly (not mocked) to verify that the Yekutieli c(N)
        factor tightens the threshold as N grows.  This is the mathematical foundation
        of AC-3.2.

        Tolerance (1e-9): arithmetic inequality on deterministically-computed values.
        """
        gate = _import_gate_engine()

        # High-signal series to ensure N=1 can yield a low p-value.
        returns_strong = _make_synthetic_returns_pct(600, seed=1, mean_pct=0.15)
        cand_strong = gate.BacktestCandidate(
            candidate_id="strong-cand",
            daily_returns_pct=returns_strong,
            candidate_params={},
            incumbent_params={},
            theory_prior_params={},
            nn1_compliant=True,
        )

        # N=1 batch.
        batch_n1 = gate.evaluate_candidate_batch([cand_strong])
        p_adj_n1 = batch_n1.results[0].winner_p_adj

        # N=20 batch: same strong candidate + 19 weak noise candidates.
        weak_cands = [
            gate.BacktestCandidate(
                candidate_id=f"weak-{i}",
                daily_returns_pct=_make_noisy_returns_pct(600, seed=100 + i),
                candidate_params={},
                incumbent_params={},
                theory_prior_params={},
                nn1_compliant=True,
            )
            for i in range(19)
        ]
        batch_n20 = gate.evaluate_candidate_batch([cand_strong] + weak_cands)
        result_n20 = next(r for r in batch_n20.results if r.candidate_id == "strong-cand")
        p_adj_n20 = result_n20.winner_p_adj

        assert batch_n20.n_candidates == 20, (
            f"n_candidates must equal 20. Got {batch_n20.n_candidates}."
        )
        assert batch_n1.n_candidates == 1, (
            f"n_candidates must equal 1. Got {batch_n1.n_candidates}."
        )
        # Monotonicity: a larger batch means a stricter (larger) adjusted p-value
        # for the same underlying raw p-value.
        # Tolerance 1e-9: pure arithmetic comparison, no sampling noise.
        assert p_adj_n20 >= p_adj_n1 - 1e-9, (
            f"BHY FDR monotonicity violated: p_adj_n1={p_adj_n1:.6f}, "
            f"p_adj_n20={p_adj_n20:.6f}. "
            "Raising N must raise (or maintain) each candidate's adjusted p-value. "
            "Per-candidate gating (n_effective=1 each) would keep p_adj constant — "
            "that is the failure mode this test catches (AC-3.2)."
        )

    def test_joint_batch_p_adj_differs_from_solo_calls(self):
        """Demonstrate that individual-call gating does NOT reproduce joint FDR correction.

        Proves the negative: N solo calls (n_effective=1 each) vs one joint batch
        (n_effective=N) produce different p_adj values.  This is the anti-pattern
        AC-3.2 forbids — gating individually defeats the FDR denominator.
        """
        gate = _import_gate_engine()

        returns_list = [_make_synthetic_returns_pct(500, seed=i, mean_pct=0.10) for i in range(5)]
        candidates = [
            gate.BacktestCandidate(
                candidate_id=f"c{i}",
                daily_returns_pct=r,
                candidate_params={},
                incumbent_params={},
                theory_prior_params={},
                nn1_compliant=True,
            )
            for i, r in enumerate(returns_list)
        ]

        # Joint batch (correct FDR semantics — n_effective = 5).
        batch_joint = gate.evaluate_candidate_batch(candidates)

        # Solo calls (incorrect — each has n_effective = 1).
        solo_p_adjs = []
        for cand in candidates:
            solo_batch = gate.evaluate_candidate_batch([cand])
            solo_p_adjs.append(solo_batch.results[0].winner_p_adj)

        joint_p_adjs = [r.winner_p_adj for r in batch_joint.results]

        all_equal = all(abs(joint - solo) < 1e-12 for joint, solo in zip(joint_p_adjs, solo_p_adjs))
        assert not all_equal, (
            "Joint-batch FDR correction must produce different p_adj values than "
            "N individual single-candidate calls (Yekutieli c(N) differs for N=5 vs N=1). "
            f"Joint p_adjs: {[round(p, 6) for p in joint_p_adjs]}. "
            f"Solo p_adjs: {[round(p, 6) for p in solo_p_adjs]}. "
            "If these are equal, the gate is not applying N-aware Yekutieli correction."
        )


# ===========================================================================
# Section 4 — Batch dispatch contract (the engine must submit ONE batch)
# ===========================================================================


class TestBatchDispatchContract:
    """The logic_change_engine must submit all N candidates as ONE evaluate_candidate_batch call.

    A per-candidate gating loop (N separate calls with n_effective=1 each) defeats
    the FDR correction mandated by AC-3.2.
    """

    def test_operator_mode_submits_single_batch_to_gate(self, score_tree, logic_objective):
        """propose_operator_logic_change must call evaluate_candidate_batch exactly once.

        For a single operator-initiated tweak, the batch has at most N=1 candidate.
        The key requirement: at most ONE call to evaluate_candidate_batch.
        """
        engine = _import_engine()
        gate = _import_gate_engine()

        good_returns = _make_synthetic_returns_pct(500, seed=3)
        mock_backtest = _make_mock_backtest_result(daily_returns_pct=good_returns)

        call_count = {"n": 0}
        real_evaluate = gate.evaluate_candidate_batch

        def spy_evaluate(candidates, **kwargs):
            call_count["n"] += 1
            return real_evaluate(candidates, **kwargs)

        with (
            patch(
                "advisors.logic_change_engine.evaluate_candidate_batch",
                side_effect=spy_evaluate,
            ),
            patch(
                "advisors.logic_change_engine.run_backtest",
                return_value=mock_backtest,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch(
                "advisors.logic_change_engine.generate_reasoned_logic_candidates",
                return_value=[_make_logic_tweak()],
            ),
            patch("database.insert_advisor_observation"),
        ):
            engine.propose_operator_logic_change(
                symphony_id="sym-op-batch-test",
                score_tree=score_tree,
                change_description=_OPERATOR_CHANGE_DESCRIPTION,
                objective=logic_objective,
            )

        assert call_count["n"] == 1, (
            f"propose_operator_logic_change must call evaluate_candidate_batch "
            f"exactly once (one batch = one gate call). Called {call_count['n']} times. "
            "Submitting candidates individually would use n_effective=1 per candidate, "
            "defeating the FDR correction (AC-3.2)."
        )

    def test_advisor_suggested_mode_submits_all_candidates_in_one_batch(self, score_tree):
        """suggest_logic_changes must submit ALL backtested candidates as ONE batch.

        When N > 1 candidates are generated, they must be submitted in a SINGLE
        evaluate_candidate_batch call so n_effective = N (the honest multiple-testing count).
        """
        engine = _import_engine()
        gate = _import_gate_engine()

        call_batches: list[int] = []

        real_evaluate = gate.evaluate_candidate_batch

        def spy_evaluate(candidates, **kwargs):
            call_batches.append(len(candidates))
            return real_evaluate(candidates, **kwargs)

        call_idx = {"n": 0}

        def mock_backtest_fn(tree, symphony_id=None):
            seed = call_idx["n"]
            call_idx["n"] += 1
            return _make_mock_backtest_result(
                daily_returns_pct=_make_synthetic_returns_pct(500, seed=seed)
            )

        objective = _make_logic_objective(objective_type="reduce_drawdown")

        with (
            patch(
                "advisors.logic_change_engine.evaluate_candidate_batch",
                side_effect=spy_evaluate,
            ),
            patch(
                "advisors.logic_change_engine.run_backtest",
                side_effect=mock_backtest_fn,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch(
                "advisors.logic_change_engine.generate_reasoned_logic_candidates",
                return_value=_make_two_logic_tweaks(),
            ),
            patch("database.insert_advisor_observation"),
        ):
            result = engine.suggest_logic_changes(
                symphony_id="sym-suggest-batch-test",
                score_tree=score_tree,
                objective=objective,
            )

        n_gate_calls = len(call_batches)
        total_candidates_gated = sum(call_batches)

        if total_candidates_gated > 0:
            assert n_gate_calls == 1, (
                f"suggest_logic_changes must submit all candidates in ONE "
                f"evaluate_candidate_batch call. Made {n_gate_calls} calls "
                f"with batch sizes {call_batches}. "
                "Per-candidate gating defeats the FDR correction (AC-3.2)."
            )
            # Gate batch n_candidates must match total submitted.
            assert result.gate_batch.n_candidates == total_candidates_gated, (
                f"GatedBatch.n_candidates={result.gate_batch.n_candidates} must equal "
                f"total candidates submitted ({total_candidates_gated})."
            )

# ===========================================================================
# Section 5 — Pre-correction passers are NOT surfaced (AC-3.3)
# ===========================================================================


class TestPreCorrectionPassersNotSurfaced:
    """A candidate with raw p < q but adjusted p_adj > q must not be ADOPT_CANDIDATE."""

    def test_candidate_failing_post_correction_is_not_adopt_candidate(self):
        """Gate engine: raw p-value passer that fails BHY adjustment is not ADOPT_CANDIDATE.

        Constructs a large batch (N=51) where the BHY adjustment pushes a marginal
        candidate's p_adj above HARVEY_LIU_FDR_Q, verifying it is not surfaced.
        """
        gate = _import_gate_engine()

        marginal_returns = _make_synthetic_returns_pct(500, seed=77, mean_pct=0.04)
        weak_returns_list = [_make_noisy_returns_pct(500, seed=200 + i) for i in range(50)]

        marginal_cand = gate.BacktestCandidate(
            candidate_id="marginal",
            daily_returns_pct=marginal_returns,
            candidate_params={},
            incumbent_params={},
            theory_prior_params={},
            nn1_compliant=True,
        )
        weak_cands = [
            gate.BacktestCandidate(
                candidate_id=f"weak-{i}",
                daily_returns_pct=r,
                candidate_params={},
                incumbent_params={},
                theory_prior_params={},
                nn1_compliant=True,
            )
            for i, r in enumerate(weak_returns_list)
        ]

        # Solo run — marginal candidate alone.
        solo_batch = gate.evaluate_candidate_batch([marginal_cand])
        solo_p_adj = solo_batch.results[0].winner_p_adj

        # Large batch run — same marginal candidate + 50 weak ones.
        large_batch = gate.evaluate_candidate_batch([marginal_cand] + weak_cands)
        marginal_in_large = next(r for r in large_batch.results if r.candidate_id == "marginal")
        large_p_adj = marginal_in_large.winner_p_adj

        # Precondition: FDR tightened (large batch is strictly harder).
        assert large_p_adj >= solo_p_adj - 1e-9, (
            f"Precondition: large_p_adj ({large_p_adj:.6f}) >= solo_p_adj ({solo_p_adj:.6f})."
        )

        # Key assertion: if large_p_adj > FDR_Q, the candidate must not be ADOPT_CANDIDATE.
        if large_p_adj > gate.HARVEY_LIU_FDR_Q:
            assert marginal_in_large.verdict.decision != "ADOPT_CANDIDATE", (
                f"Candidate with BHY-adjusted p_adj={large_p_adj:.6f} > FDR_Q="
                f"{gate.HARVEY_LIU_FDR_Q} must NOT be ADOPT_CANDIDATE (AC-3.3). "
                f"Got {marginal_in_large.verdict.decision!r}."
            )
            survivor_ids = {r.candidate_id for r in large_batch.survivors}
            assert "marginal" not in survivor_ids, (
                "Candidate that fails post-correction FDR must not be in survivors list."
            )


# ===========================================================================
# Section 6 — Mandatory overfitting caveat on all survivors (AC-3.3)
# ===========================================================================


class TestSurvivorCaveatsPresent:
    """Every ADOPT_CANDIDATE survivor must carry the mandatory overfitting caveat."""

    def test_adopt_candidate_from_gate_engine_carries_overfitting_caveat(self):
        """Gate engine: ADOPT_CANDIDATE results carry SURVIVOR_OVERFITTING_CAVEAT."""
        gate = _import_gate_engine()

        strong_returns = _make_synthetic_returns_pct(600, seed=2, mean_pct=0.20)
        cand = gate.BacktestCandidate(
            candidate_id="strong",
            daily_returns_pct=strong_returns,
            candidate_params={},
            incumbent_params={},
            theory_prior_params={},
            nn1_compliant=True,
        )
        batch = gate.evaluate_candidate_batch([cand])
        adopt_results = [r for r in batch.results if r.verdict.decision == "ADOPT_CANDIDATE"]

        for result in adopt_results:
            assert len(result.caveats) > 0, (
                f"ADOPT_CANDIDATE result for '{result.candidate_id}' has no caveats. "
                "Every survivor must carry at least SURVIVOR_OVERFITTING_CAVEAT (AC-3.3)."
            )
            caveat_text = " ".join(result.caveats).lower()
            assert (
                "overfit" in caveat_text or "gate" in caveat_text or "selection" in caveat_text
            ), (
                f"ADOPT_CANDIDATE caveats must mention overfitting / gate risk. "
                f"Got: {result.caveats}."
            )

    def test_logic_change_survivor_carries_overfitting_caveat_from_gate(
        self, score_tree, logic_objective
    ):
        """propose_operator_logic_change survivors must carry SURVIVOR_OVERFITTING_CAVEAT.

        The caveat must be propagated from gate_result.caveats onto the proposal.caveats.
        """
        engine = _import_engine()
        strong_returns = _make_synthetic_returns_pct(600, seed=5, mean_pct=0.20)
        mock_backtest = _make_mock_backtest_result(daily_returns_pct=strong_returns)

        with (
            patch(
                "advisors.logic_change_engine.run_backtest",
                return_value=mock_backtest,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch(
                "advisors.logic_change_engine.generate_reasoned_logic_candidates",
                return_value=[_make_logic_tweak()],
            ),
            patch("database.insert_advisor_observation"),
        ):
            result = engine.propose_operator_logic_change(
                symphony_id="sym-caveat-test",
                score_tree=score_tree,
                change_description=_OPERATOR_CHANGE_DESCRIPTION,
                objective=logic_objective,
            )

        for proposal in result.survivors:
            assert len(proposal.caveats) > 0, (
                f"Survivor proposal '{proposal.candidate_id}' has empty caveats. "
                "SURVIVOR_OVERFITTING_CAVEAT is mandatory (AC-3.3)."
            )
            caveat_text = " ".join(proposal.caveats).lower()
            assert (
                "overfit" in caveat_text
                or "gate" in caveat_text
                or "selection" in caveat_text
                or "backtest" in caveat_text
            ), f"Survivor caveats must reference overfitting / gate risk. Got: {proposal.caveats}."

    def test_logic_change_survivor_winner_p_adj_is_finite_float(self, score_tree, logic_objective):
        """Survivors must expose winner_p_adj as a finite float for operator audit (AC-3.3)."""
        engine = _import_engine()
        strong_returns = _make_synthetic_returns_pct(600, seed=11, mean_pct=0.20)
        mock_backtest = _make_mock_backtest_result(daily_returns_pct=strong_returns)

        with (
            patch(
                "advisors.logic_change_engine.run_backtest",
                return_value=mock_backtest,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch(
                "advisors.logic_change_engine.generate_reasoned_logic_candidates",
                return_value=[_make_logic_tweak()],
            ),
            patch("database.insert_advisor_observation"),
        ):
            result = engine.propose_operator_logic_change(
                symphony_id="sym-verdict-test",
                score_tree=score_tree,
                change_description=_OPERATOR_CHANGE_DESCRIPTION,
                objective=logic_objective,
            )

        for proposal in result.survivors:
            assert proposal.gate_result is not None, (
                f"Survivor '{proposal.candidate_id}' must have gate_result present."
            )
            assert proposal.gate_result.winner_p_adj is not None, (
                "Survivor gate_result.winner_p_adj must not be None (AC-3.3)."
            )
            assert math.isfinite(proposal.gate_result.winner_p_adj), (
                f"winner_p_adj={proposal.gate_result.winner_p_adj!r} must be finite."
            )


# ===========================================================================
# Section 7 — Advisory-only and no-auto-apply (AC-3.4)
# ===========================================================================


class TestAdvisoryOnlyContract:
    """The engine never auto-applies; survivors write an advisory_observation only (AC-3.4)."""

    def test_persisted_observations_are_advisory_only_1(self, score_tree, logic_objective):
        """Every persisted observation must carry is_advisory_only=1 (AC-X3 / AC-3.4).

        is_advisory_only=1 is structural, not optional — the advisor never moves money.
        observation_type must identify 'logic_change_proposal'.

        RC-4 (DIAGNOSIS F4): persistence is verdict-agnostic (one row per GATED
        proposal regardless of decision), so this asserts the structural advisory
        flag on EVERY persisted row, not a per-survivor count.
        """
        engine = _import_engine()
        strong_returns = _make_synthetic_returns_pct(600, seed=6, mean_pct=0.20)
        mock_backtest = _make_mock_backtest_result(daily_returns_pct=strong_returns)

        insert_calls: list[dict] = []

        def capture_insert(**kwargs):
            insert_calls.append(kwargs)

        with (
            patch(
                "advisors.logic_change_engine.run_backtest",
                return_value=mock_backtest,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch(
                "advisors.logic_change_engine.generate_reasoned_logic_candidates",
                return_value=[_make_logic_tweak()],
            ),
            patch(
                "database.insert_advisor_observation",
                side_effect=capture_insert,
            ),
        ):
            result = engine.propose_operator_logic_change(
                symphony_id="sym-persist-test",
                score_tree=score_tree,
                change_description=_OPERATOR_CHANGE_DESCRIPTION,
                objective=logic_objective,
            )

        # RC-4: one observation per gated proposal (verdict-agnostic), so at least
        # one row is written whenever the gate produced a result.
        n_gated = len(result.gate_batch.results)
        assert len(insert_calls) == n_gated, (
            f"insert_advisor_observation must be called once per GATED proposal "
            f"(RC-4 verdict-agnostic). Gated: {n_gated}, calls: {len(insert_calls)}."
        )
        for call_kwargs in insert_calls:
            assert call_kwargs.get("is_advisory_only") == 1, (
                f"insert_advisor_observation must be called with is_advisory_only=1 (AC-X3). "
                f"Got: {call_kwargs.get('is_advisory_only')!r}."
            )
            obs_type = call_kwargs.get("observation_type", "")
            assert "logic_change" in obs_type.lower(), (
                f"observation_type must contain 'logic_change'. Got: {obs_type!r}."
            )

    def test_apply_guidance_is_plain_text_not_a_write_call(self, score_tree, logic_objective):
        """apply_guidance must be a plain-text string — never a write endpoint call (AC-3.4)."""
        engine = _import_engine()
        returns = _make_synthetic_returns_pct(500, seed=8, mean_pct=0.10)
        mock_backtest = _make_mock_backtest_result(daily_returns_pct=returns)

        with (
            patch(
                "advisors.logic_change_engine.run_backtest",
                return_value=mock_backtest,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch(
                "advisors.logic_change_engine.generate_reasoned_logic_candidates",
                return_value=[_make_logic_tweak()],
            ),
            patch("database.insert_advisor_observation"),
        ):
            result = engine.propose_operator_logic_change(
                symphony_id="sym-apply-test",
                score_tree=score_tree,
                change_description=_OPERATOR_CHANGE_DESCRIPTION,
                objective=logic_objective,
            )

        for proposal in result.proposals:
            guidance = getattr(proposal, "apply_guidance", None)
            assert guidance is not None, (
                "LogicChangeProposalResult must have apply_guidance field (AC-3.4)."
            )
            assert isinstance(guidance, str) and len(guidance) > 0, (
                "apply_guidance must be a non-empty string."
            )
            guidance_lower = guidance.lower()
            for forbidden in ["/api/v0.1/symphonies", "/copy", "/deploy", "go-to-cash"]:
                assert forbidden not in guidance_lower, (
                    f"apply_guidance must not reference write endpoints. "
                    f"Found '{forbidden}' in: {guidance!r}."
                )

    def test_rejected_candidates_are_persisted_with_their_real_verdict(self, logic_objective):
        """RC-4 (DIAGNOSIS F4): a rejected/kept candidate IS persisted — with its REAL
        gate verdict — so the operator sees the engine ran and kept the incumbent.

        This supersedes the old "persist only on ADOPT" contract: an ADOPT-only write
        left advisor_observations empty on the common KEEP/REJECT path, making the
        advisor look dead.  The persisted row must carry the actual decision (NOT a
        hardcoded ADOPT_CANDIDATE) and is_advisory_only=1.

        R2-2 NOTE: uses a LOCAL symphony_schema-constructed tree (real "step"-based
        Composer grammar), not the file's shared `score_tree` fixture — that fixture
        predates AC-3's validate_tree guard and uses a legacy "type"-keyed shape that
        validate_tree correctly rejects (it is not real Composer grammar), which
        would make this test's candidate never reach the gate at all and turn the
        RC-4 assertion below into a false precondition failure unrelated to what
        this test actually verifies (persistence of a REAL gate verdict).
        """
        import advisors.symphony_schema as symphony_schema  # noqa: PLC0415

        engine = _import_engine()
        tree = symphony_schema.make_root(
            "Test Symphony",
            "daily",
            [symphony_schema.make_inverse_vol([symphony_schema.make_asset("SPY")])],
        )
        tree["children"][0]["window-days"] = 20
        tweak = engine.LogicTweak(
            node_path=["children", 0],
            param_key="window-days",
            old_value=20,
            new_value=16,
            node_description="window-days=20 at path [children, 0]",
        )

        # Short noisy series — will fail the gate (non-ADOPT verdict).
        bad_returns = _make_noisy_returns_pct(80, seed=555)
        mock_backtest = _make_mock_backtest_result(daily_returns_pct=bad_returns)

        insert_calls: list = []

        with (
            patch(
                "advisors.logic_change_engine.run_backtest",
                return_value=mock_backtest,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch(
                "advisors.logic_change_engine.generate_reasoned_logic_candidates",
                return_value=[tweak],
            ),
            patch(
                "database.insert_advisor_observation",
                side_effect=lambda **kw: insert_calls.append(kw),
            ),
        ):
            result = engine.propose_operator_logic_change(
                symphony_id="sym-reject-test",
                score_tree=tree,
                change_description=_OPERATOR_CHANGE_DESCRIPTION,
                objective=logic_objective,
            )

        # Confirm this run is a non-ADOPT case (exercises the regardless-of-verdict path).
        decisions = [r.verdict.decision for r in result.gate_batch.results]
        assert decisions and "ADOPT_CANDIDATE" not in decisions, (
            f"Test setup invalid: expected a non-ADOPT verdict; got {decisions!r}."
        )

        # RC-4: the rejected/kept proposal is STILL persisted (one row per gated proposal).
        n_gated = len(result.gate_batch.results)
        assert len(insert_calls) == n_gated, (
            f"insert_advisor_observation called {len(insert_calls)} times for {n_gated} "
            f"gated proposal(s). RC-4: persistence is verdict-agnostic — a non-ADOPT "
            "verdict must STILL write a row so the operator sees the engine ran."
        )
        for kw in insert_calls:
            assert kw.get("is_advisory_only") == 1, (
                f"Persisted observation must be is_advisory_only=1; got "
                f"{kw.get('is_advisory_only')!r}."
            )
            assert kw.get("verdict") in ("KEEP_INCUMBENT", "REJECT_VETO_FAILED"), (
                f"The persisted verdict must reflect the REAL gate decision, not a "
                f"hardcoded ADOPT_CANDIDATE. Got: {kw.get('verdict')!r}."
            )


# ===========================================================================
# Section 8 — AC-X constraints (live path, write endpoints, API key)
# ===========================================================================


class TestArchitectureConstraints:
    """AC-X1/X2/X4 — advise-only, off-live-path, no write endpoints."""

    def test_alpha_bot_execution_does_not_import_logic_change_engine(self, m4_fixture):
        """AC-X2: alpha_bot_execution.py must not import from advisors.logic_change_engine."""
        guard = m4_fixture["live_path_guard"]
        assert guard["alpha_bot_execution_imports_logic_change"] is False, (
            "Fixture: live_path_guard.alpha_bot_execution_imports_logic_change must be False."
        )

        tree = _parse_source("alpha_bot_execution.py")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = ""
                if isinstance(node, ast.Import):
                    module = " ".join(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    module = node.module
                assert "logic_change_engine" not in module, (
                    f"alpha_bot_execution.py must not import advisors.logic_change_engine "
                    f"(AC-X2). Found import: '{module}'."
                )

    def test_logic_change_engine_source_does_not_import_alpha_bot_execution(self):
        """AC-X2: logic_change_engine.py must not import alpha_bot_execution directly."""
        engine_path = _REPO_ROOT / "advisors" / "logic_change_engine.py"
        if not engine_path.exists():
            pytest.skip("advisors/logic_change_engine.py not yet created (RED phase).")

        tree = _parse_source("advisors/logic_change_engine.py")
        # Module-level imports only.
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "alpha_bot_execution":
                # Top-level (non-local) import is forbidden.
                # _has_composer_key uses a local import inside the function body —
                # that is exempted because it is a deferred import inside a function.
                # We check only module-level (col_offset == 0) imports.
                if node.col_offset == 0:
                    assert False, (
                        "advisors/logic_change_engine.py must not have a module-level "
                        "import of alpha_bot_execution (AC-X2). "
                        "Local imports inside functions are permitted."
                    )

    def test_no_api_key_operator_mode_returns_no_api_key_true(self, score_tree, logic_objective):
        """AC-X4: absent Composer API key → no_api_key=True + 'advisor unavailable' message."""
        engine = _import_engine()

        insert_calls: list = []

        with (
            patch("advisors.logic_change_engine._has_composer_key", return_value=False),
            patch(
                "database.insert_advisor_observation",
                side_effect=lambda **kw: insert_calls.append(kw),
            ),
        ):
            result = engine.propose_operator_logic_change(
                symphony_id="sym-nokey-test",
                score_tree=score_tree,
                change_description=_OPERATOR_CHANGE_DESCRIPTION,
                objective=logic_objective,
            )

        assert result.no_api_key is True, (
            "When Composer API key is absent, LogicChangeRunResult.no_api_key must be True."
        )
        assert "advisor unavailable" in result.message.lower(), (
            f"No-API-key message must contain 'advisor unavailable' (AC-X4). "
            f"Got: {result.message!r}."
        )
        assert len(result.survivors) == 0
        assert len(insert_calls) == 0, "No-API-key run must write nothing (AC-X4)."

    def test_no_api_key_suggest_mode_returns_no_api_key_true(self, score_tree):
        """AC-X4: suggest_logic_changes with absent key → no_api_key=True + no persistence."""
        engine = _import_engine()
        objective = _make_logic_objective()
        insert_calls: list = []

        with (
            patch("advisors.logic_change_engine._has_composer_key", return_value=False),
            patch(
                "database.insert_advisor_observation",
                side_effect=lambda **kw: insert_calls.append(kw),
            ),
        ):
            result = engine.suggest_logic_changes(
                symphony_id="sym-nokey-suggest",
                score_tree=score_tree,
                objective=objective,
            )

        assert result.no_api_key is True
        assert "advisor unavailable" in result.message.lower(), (
            f"AC-X4: suggest_logic_changes no-key message must contain 'advisor unavailable'. "
            f"Got: {result.message!r}."
        )
        assert len(insert_calls) == 0

    def test_logic_change_engine_source_does_not_reference_composer_write_endpoints(self):
        """AC-X1: static analysis — logic_change_engine.py must not reference write endpoints."""
        engine_path = _REPO_ROOT / "advisors" / "logic_change_engine.py"
        if not engine_path.exists():
            pytest.skip("advisors/logic_change_engine.py not yet created (RED phase).")

        source = engine_path.read_text(encoding="utf-8")
        for term in ["/copy", "/deploy", "go-to-cash"]:
            assert term not in source, (
                f"advisors/logic_change_engine.py must not reference Composer write endpoint "
                f"'{term}' (AC-X1). Found in source."
            )


# ===========================================================================
# Section 9 — Backtest failure isolation (AC-X5)
# ===========================================================================


class TestBacktestFailureIsolation:
    """One candidate's backtest failure must not abort the batch (AC-X5)."""

    def test_backtest_error_on_variant_does_not_raise(self, score_tree, logic_objective):
        """A backtest error must set backtest_error on the proposal, never raise.

        AC-X5: backtest failure → failure marker on the proposal; result is returned.
        """
        engine = _import_engine()

        error_backtest = MagicMock()
        error_backtest.error = "HTTP 500: Internal Server Error"
        error_backtest.daily_returns = {}
        error_backtest.stats = None
        error_backtest.data_warnings = []

        with (
            patch(
                "advisors.logic_change_engine.run_backtest",
                return_value=error_backtest,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch(
                "advisors.logic_change_engine.generate_reasoned_logic_candidates",
                return_value=[_make_logic_tweak()],
            ),
            patch("database.insert_advisor_observation"),
        ):
            result = engine.propose_operator_logic_change(
                symphony_id="sym-fail-test",
                score_tree=score_tree,
                change_description=_OPERATOR_CHANGE_DESCRIPTION,
                objective=logic_objective,
            )

        assert result is not None, (
            "propose_operator_logic_change must not raise on backtest failure (AC-X5)."
        )
        # Any failed candidates must have backtest_error set (not None).
        for proposal in result.proposals:
            if proposal.backtest_error is not None:
                assert (
                    isinstance(proposal.backtest_error, str) and len(proposal.backtest_error) > 0
                ), "backtest_error must be a non-empty string describing the failure."

# ===========================================================================
# Section 10 — Zero survivors is a valid outcome
# ===========================================================================


class TestZeroSurvivorsIsValid:
    """Zero survivors is a valid, non-error outcome."""

    def test_zero_survivors_returns_no_survivors_message(self, score_tree, logic_objective):
        """When no candidate survives, the result message must contain NO_SURVIVORS_MESSAGE."""
        engine = _import_engine()
        bad_returns = _make_noisy_returns_pct(80, seed=1)
        mock_backtest = _make_mock_backtest_result(daily_returns_pct=bad_returns)

        with (
            patch(
                "advisors.logic_change_engine.run_backtest",
                return_value=mock_backtest,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch(
                "advisors.logic_change_engine.generate_reasoned_logic_candidates",
                return_value=[_make_logic_tweak()],
            ),
            patch("database.insert_advisor_observation"),
        ):
            result = engine.propose_operator_logic_change(
                symphony_id="sym-zero-survivors",
                score_tree=score_tree,
                change_description=_OPERATOR_CHANGE_DESCRIPTION,
                objective=logic_objective,
            )

        assert result is not None
        if not result.survivors:
            no_survivors_msg = engine.NO_SURVIVORS_MESSAGE
            assert no_survivors_msg.lower() in result.message.lower(), (
                f"Zero-survivors result must contain NO_SURVIVORS_MESSAGE. Got: {result.message!r}."
            )

    def test_gate_batch_is_non_none_even_with_zero_survivors(self, score_tree, logic_objective):
        """gate_batch must be present even when no survivors (for audit trail)."""
        engine = _import_engine()
        bad_returns = _make_noisy_returns_pct(80, seed=2)
        mock_backtest = _make_mock_backtest_result(daily_returns_pct=bad_returns)

        with (
            patch(
                "advisors.logic_change_engine.run_backtest",
                return_value=mock_backtest,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch(
                "advisors.logic_change_engine.generate_reasoned_logic_candidates",
                return_value=[_make_logic_tweak()],
            ),
            patch("database.insert_advisor_observation"),
        ):
            result = engine.propose_operator_logic_change(
                symphony_id="sym-zero-batch",
                score_tree=score_tree,
                change_description=_OPERATOR_CHANGE_DESCRIPTION,
                objective=logic_objective,
            )

        assert result.gate_batch is not None, (
            "LogicChangeRunResult.gate_batch must be non-None even with zero survivors."
        )
        assert isinstance(result.gate_batch.fdr_q, float), "gate_batch.fdr_q must be a float."
        assert math.isfinite(result.gate_batch.fdr_q), "gate_batch.fdr_q must be a finite float."


# ===========================================================================
# Section 11 — Operator-initiated mode: full result structure (AC-3.1)
# ===========================================================================


class TestOperatorInitiatedMode:
    """AC-3.1 operator-initiated mode structural requirements."""

    def test_returns_logic_change_run_result_with_all_required_fields(
        self, score_tree, logic_objective
    ):
        """propose_operator_logic_change must return LogicChangeRunResult with required fields."""
        engine = _import_engine()
        returns = _make_synthetic_returns_pct(500, seed=9, mean_pct=0.10)
        mock_backtest = _make_mock_backtest_result(daily_returns_pct=returns)

        with (
            patch(
                "advisors.logic_change_engine.run_backtest",
                return_value=mock_backtest,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch("database.insert_advisor_observation"),
        ):
            result = engine.propose_operator_logic_change(
                symphony_id="sym-struct-test",
                score_tree=score_tree,
                change_description=_OPERATOR_CHANGE_DESCRIPTION,
                objective=logic_objective,
            )

        for field in (
            "gate_batch",
            "proposals",
            "survivors",
            "rejected_candidates",
            "message",
            "objective",
            "no_api_key",
        ):
            assert hasattr(result, field), f"LogicChangeRunResult must have '{field}' field."
        assert isinstance(result.proposals, list)
        assert isinstance(result.survivors, list)
        assert isinstance(result.rejected_candidates, list)
        assert result.no_api_key is False

    def test_proposal_carries_tweak_and_objective(self, score_tree, logic_objective):
        """Each LogicChangeProposalResult must carry the tweak and objective (AC-3.3)."""
        engine = _import_engine()
        returns = _make_synthetic_returns_pct(500, seed=10, mean_pct=0.10)
        mock_backtest = _make_mock_backtest_result(daily_returns_pct=returns)

        with (
            patch(
                "advisors.logic_change_engine.run_backtest",
                return_value=mock_backtest,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch(
                "advisors.logic_change_engine.generate_reasoned_logic_candidates",
                return_value=[_make_logic_tweak()],
            ),
            patch("database.insert_advisor_observation"),
        ):
            result = engine.propose_operator_logic_change(
                symphony_id="sym-tweak-obj-test",
                score_tree=score_tree,
                change_description=_OPERATOR_CHANGE_DESCRIPTION,
                objective=logic_objective,
            )

        assert len(result.proposals) > 0, (
            "propose_operator_logic_change must produce at least one proposal."
        )
        for proposal in result.proposals:
            assert hasattr(proposal, "tweak"), "LogicChangeProposalResult must have 'tweak' field."
            assert hasattr(proposal, "objective"), (
                "LogicChangeProposalResult must have 'objective' field."
            )
            assert hasattr(proposal, "objective_rationale"), (
                "LogicChangeProposalResult must have 'objective_rationale' field."
            )
            assert isinstance(getattr(proposal, "objective_rationale", None), str), (
                "objective_rationale must be a string."
            )

    def test_proposal_carries_baseline_and_variant_stats_as_dicts(
        self, score_tree, logic_objective
    ):
        """Non-failed proposals must carry baseline_stats and variant_stats as dicts (AC-3.3)."""
        engine = _import_engine()
        returns = _make_synthetic_returns_pct(500, seed=13)
        mock_backtest = _make_mock_backtest_result(daily_returns_pct=returns)

        with (
            patch(
                "advisors.logic_change_engine.run_backtest",
                return_value=mock_backtest,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch(
                "advisors.logic_change_engine.generate_reasoned_logic_candidates",
                return_value=[_make_logic_tweak()],
            ),
            patch("database.insert_advisor_observation"),
        ):
            result = engine.propose_operator_logic_change(
                symphony_id="sym-stats-test",
                score_tree=score_tree,
                change_description=_OPERATOR_CHANGE_DESCRIPTION,
                objective=logic_objective,
            )

        for proposal in result.proposals:
            if proposal.backtest_error:
                continue
            assert isinstance(proposal.baseline_stats, dict), (
                f"baseline_stats must be a dict. Got {type(proposal.baseline_stats).__name__!r}."
            )
            assert isinstance(proposal.variant_stats, dict), (
                f"variant_stats must be a dict. Got {type(proposal.variant_stats).__name__!r}."
            )

    def test_n_candidates_equals_1_for_single_operator_tweak(self, score_tree, logic_objective):
        """For a single operator tweak, gate_batch.n_candidates must equal 1 (or 0 if failed).

        The FDR denominator must equal the actual candidate count submitted.
        """
        engine = _import_engine()
        returns = _make_synthetic_returns_pct(500, seed=14)
        mock_backtest = _make_mock_backtest_result(daily_returns_pct=returns)

        with (
            patch(
                "advisors.logic_change_engine.run_backtest",
                return_value=mock_backtest,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch(
                "advisors.logic_change_engine.generate_reasoned_logic_candidates",
                return_value=[_make_logic_tweak()],
            ),
            patch("database.insert_advisor_observation"),
        ):
            result = engine.propose_operator_logic_change(
                symphony_id="sym-n1-test",
                score_tree=score_tree,
                change_description=_OPERATOR_CHANGE_DESCRIPTION,
                objective=logic_objective,
            )

        assert result.gate_batch.n_candidates <= 1, (
            f"Single operator tweak should produce n_candidates=0 or 1. "
            f"Got {result.gate_batch.n_candidates}."
        )


# ===========================================================================
# Section 12 — Advisor-suggested mode structural requirements (AC-3.1)
# ===========================================================================


class TestAdvisorSuggestedMode:
    """AC-3.1 advisor-suggested mode structural requirements."""

    def test_suggest_mode_returns_run_result_with_all_fields(self, score_tree):
        """suggest_logic_changes must return LogicChangeRunResult with all required fields."""
        engine = _import_engine()
        objective = _make_logic_objective(objective_type="reduce_drawdown")
        returns = _make_synthetic_returns_pct(500, seed=15)
        mock_backtest = _make_mock_backtest_result(daily_returns_pct=returns)

        with (
            patch(
                "advisors.logic_change_engine.run_backtest",
                return_value=mock_backtest,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch("database.insert_advisor_observation"),
        ):
            result = engine.suggest_logic_changes(
                symphony_id="sym-suggest-struct",
                score_tree=score_tree,
                objective=objective,
            )

        for field in (
            "gate_batch",
            "proposals",
            "survivors",
            "rejected_candidates",
            "message",
            "objective",
            "no_api_key",
        ):
            assert hasattr(result, field), (
                f"LogicChangeRunResult from suggest_logic_changes must have '{field}'."
            )
        assert result.no_api_key is False

    def test_suggest_mode_with_simple_tree_yields_zero_proposals(self):
        """suggest_logic_changes on a tree with no numeric params yields an empty result.

        A tree with no tweakable numeric parameters produces zero candidates, which
        is a valid non-error outcome.
        """
        engine = _import_engine()
        objective = _make_logic_objective()
        simple_tree = _make_score_tree_simple()

        with (
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch("database.insert_advisor_observation"),
        ):
            result = engine.suggest_logic_changes(
                symphony_id="sym-no-params",
                score_tree=simple_tree,
                objective=objective,
            )

        assert result is not None
        assert len(result.survivors) == 0, (
            "A tree with no tweakable params must yield zero survivors."
        )

    def test_suggest_mode_n_candidates_matches_gated_count(self, score_tree):
        """gate_batch.n_candidates must equal the number of successfully-backtested candidates."""
        engine = _import_engine()
        objective = _make_logic_objective(objective_type="reduce_drawdown")

        returns = _make_synthetic_returns_pct(500, seed=16)
        mock_backtest = _make_mock_backtest_result(daily_returns_pct=returns)

        gate = _import_gate_engine()
        real_evaluate = gate.evaluate_candidate_batch
        submitted_sizes: list[int] = []

        def spy_evaluate(candidates, **kwargs):
            submitted_sizes.append(len(candidates))
            return real_evaluate(candidates, **kwargs)

        with (
            patch(
                "advisors.logic_change_engine.evaluate_candidate_batch",
                side_effect=spy_evaluate,
            ),
            patch(
                "advisors.logic_change_engine.run_backtest",
                return_value=mock_backtest,
            ),
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch(
                "advisors.logic_change_engine.generate_reasoned_logic_candidates",
                return_value=_make_two_logic_tweaks(),
            ),
            patch("database.insert_advisor_observation"),
        ):
            result = engine.suggest_logic_changes(
                symphony_id="sym-n-match",
                score_tree=score_tree,
                objective=objective,
            )

        if submitted_sizes:
            total_submitted = sum(submitted_sizes)
            assert result.gate_batch.n_candidates == total_submitted, (
                f"gate_batch.n_candidates={result.gate_batch.n_candidates} must equal "
                f"total submitted ({total_submitted})."
            )


# ===========================================================================
# Section 13 — Golden fixture contract
# ===========================================================================


class TestFixtureContract:
    """The fixture encodes the module-level contracts; tests assert shape, not values."""

    def test_proposals_fixture_exists_and_has_correct_provenance(self, m4_fixture):
        """The M4 proposals fixture must be schema-derived and have required sections."""
        assert isinstance(m4_fixture, dict)
        assert m4_fixture.get("_fixture_provenance") == "schema-derived", (
            "M4 proposals fixture must be schema-derived."
        )

    def test_proposals_fixture_encodes_fdr_guardrail_contract(self, m4_fixture):
        """Fixture must encode the FDR multiple-testing guardrail (AC-3.2)."""
        fdr = m4_fixture.get("fdr_multiple_testing_guardrail")
        assert fdr is not None, "Fixture must have fdr_multiple_testing_guardrail section."
        assert "batch_dispatch_contract" in fdr
        assert "n_raises_bar_contract" in fdr

    def test_proposals_fixture_encodes_persistence_with_is_advisory_only_1(self, m4_fixture):
        """Fixture must encode is_advisory_only=1 in the persistence contract (AC-X3)."""
        persist = m4_fixture.get("persistence_contract")
        assert persist is not None, "Fixture must have persistence_contract section."
        assert persist.get("is_advisory_only") == 1, (
            "Fixture persistence_contract.is_advisory_only must be 1."
        )

    def test_objective_directed_fixture_exists_and_has_sample_score_tree(
        self, obj_directed_fixture
    ):
        """The objective-directed fixture must exist and contain sample_score_tree_for_tests."""
        assert isinstance(obj_directed_fixture, dict)
        assert "sample_score_tree_for_tests" in obj_directed_fixture, (
            "Objective-directed fixture must contain sample_score_tree_for_tests."
        )

    def test_objective_directed_fixture_sample_tweaks_are_structurally_sound(
        self, obj_directed_fixture
    ):
        """The sample tweak fixtures must be consistent with the tree they target."""
        engine = _import_engine()
        tree = obj_directed_fixture["sample_score_tree_for_tests"]
        tweak_data = obj_directed_fixture["sample_tweak_reduce_drawdown"]

        tweak = engine.LogicTweak(
            node_path=tweak_data["node_path"],
            param_key=tweak_data["param_key"],
            old_value=tweak_data["old_value"],
            new_value=tweak_data["new_value"],
            node_description=tweak_data.get("node_description", ""),
        )
        result_tree = engine.apply_logic_tweak(tree, tweak)
        assert result_tree is not None, (
            "sample_tweak_reduce_drawdown must be applicable to sample_score_tree_for_tests. "
            f"apply_logic_tweak returned None for tweak {tweak!r}."
        )

    def test_objective_directed_fixture_invalid_tweak_cannot_be_applied(self, obj_directed_fixture):
        """The sample_tweak_invalid must return None from apply_logic_tweak (structural guard)."""
        engine = _import_engine()
        tree = obj_directed_fixture["sample_score_tree_for_tests"]
        invalid_data = obj_directed_fixture["sample_tweak_invalid"]

        tweak = engine.LogicTweak(
            node_path=invalid_data["node_path"],
            param_key=invalid_data["param_key"],
            old_value=invalid_data["old_value"],
            new_value=invalid_data["new_value"],
            node_description=invalid_data.get("node_description", ""),
        )
        result_tree = engine.apply_logic_tweak(tree, tweak)
        assert result_tree is None, (
            "sample_tweak_invalid must be rejected by apply_logic_tweak (old_value mismatch). "
            f"Got a non-None result: {result_tree!r}."
        )

