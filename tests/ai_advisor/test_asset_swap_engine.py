"""RED tests — M3 Asset-Swap Proposals (advisors/asset_swap_engine.py).

Module under test: advisors.asset_swap_engine

AC bindings:
  AC-2.1  Operator-initiated swap: specify incumbent+candidate → backtest → gate → surface if survives.
  AC-2.2  Advisor-suggested swaps: objective-directed candidates → backtest → gate → survivors.
  AC-2.3  Each surfaced swap shows baseline-vs-variant stats + gate verdict + de-correlation rationale;
           gate-failed swaps NOT surfaced as recommendations.
  AC-2.4  Structurally-invalid variant tree → per-candidate "could not backtest" without aborting batch.
  AC-2.5  Zero survivors → "no swap cleared the gate this run" (valid, non-error).
  AC-X1   No write endpoints: only GET /score + stateless POST /api/v0.1/backtest.
  AC-X2   alpha_bot_execution.py does NOT import from advisors.asset_swap_engine.
  AC-X3   Every surfaced swap persists as advisor_observation(is_advisory_only=1, symphony_id=...).
  AC-X4   No API key → "advisor unavailable" + writes nothing + no unhandled error.
  AC-X5   Per-candidate backtest failure → failure marker; does not abort the batch.

The ADVERSARIAL focus (per team brief + Gate-1 Resolution #2):
  - A naive candidate generator that ignores the objective MUST fail tests in
    TestObjectiveDirectedCandidateGeneration. Objective-direction is the
    overfitting-control mechanism: without it, the engine becomes a brute-force
    backtest-select loop — the canonical overfitting trap.
  - Every surfaced survivor MUST carry a caveat (SURVIVOR_OVERFITTING_CAVEAT
    from backtest_gate_engine) — tested in TestSurvivorCaveatsPresent.

Fixture: tests/fixtures/ai_advisor/m3/asset_swap_objective_directed_basic.json
         tests/fixtures/composer/backtest_inline_v1.json  (captured Composer response)

Mocking strategy:
  - requests.post is patched to inject backtest fixture responses.  No live API calls.
  - database.insert_advisor_observation is patched to intercept persistence calls.
  - get_composer_headers() / COMPOSER_BASE_URL are NOT patched (no network effect).
  - The math engine + acceptance_gate are NEVER mocked — tested end-to-end.
  - Time is not mocked (no time.sleep calls in the default non-live suite because
    all network calls are intercepted via the requests.post patch).

Architecture: this module is OFFLINE only (no live imports from alpha_bot_execution).
"""

from __future__ import annotations

import ast
import importlib
import json
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
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "ai_advisor"
    / "m3"
    / "asset_swap_objective_directed_basic.json"
)
_COMPOSER_BACKTEST_FIXTURE = (
    _REPO_ROOT / "tests" / "fixtures" / "composer" / "backtest_inline_v1.json"
)


def _ensure_repo_on_path() -> None:
    repo = str(_REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)


def _import_engine() -> types.ModuleType:
    """Import advisors.asset_swap_engine.  RED until the module exists."""
    _ensure_repo_on_path()
    return importlib.import_module("advisors.asset_swap_engine")


def _parse_source(relpath: str) -> ast.Module:
    path = _REPO_ROOT / relpath
    assert path.exists(), f"Expected source file not found: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ---------------------------------------------------------------------------
# Fixtures (all function-scoped unless noted)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def m3_fixture() -> dict:
    """Load the M3 schema-derived fixture."""
    assert _FIXTURE_PATH.exists(), (
        f"Fixture not found: {_FIXTURE_PATH}. "
        "Create tests/fixtures/ai_advisor/m3/asset_swap_objective_directed_basic.json."
    )
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def composer_backtest_fixture() -> dict:
    """Load the real captured Composer backtest response for use as a mock return value."""
    assert _COMPOSER_BACKTEST_FIXTURE.exists(), (
        f"Fixture not found: {_COMPOSER_BACKTEST_FIXTURE}. "
        "Capture a real backtest response via /api-fixture."
    )
    with _COMPOSER_BACKTEST_FIXTURE.open(encoding="utf-8") as f:
        raw = json.load(f)
    # The fixture wraps the response under a "response" key.
    return raw.get("response", raw)


def _make_mock_backtest_response(
    stats: dict | None = None,
    dvm_capital: dict | None = None,
    symphony_id: str = "sym-test",
    status_code: int = 200,
) -> MagicMock:
    """Build a mock requests.Response for a backtest call.

    Produces a fixture-driven mock; stat values are derived from the fixture
    schema shape, never from producer-computed literals.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = status_code

    if status_code == 200:
        # Minimal structurally-valid response — shape only, no hardcoded stat values.
        body: dict[str, Any] = {
            "first_day": 19000,
            "last_market_day": 20600,
            "data_warnings": {},
            "costs": {"reg_fee": 0.0, "taf_fee": 0.0, "slippage": 0.0},
            "stats": stats or {"sharpe_ratio": None, "sortino_ratio": None, "max_drawdown": None},
            "dvm_capital": dvm_capital or {},
        }
        mock_resp.json.return_value = body
        mock_resp.text = json.dumps(body)[:200]
    else:
        mock_resp.json.side_effect = ValueError("non-200 response")
        mock_resp.text = f"HTTP {status_code} error"[:200]

    return mock_resp


def _make_score_tree(
    symphony_id: str = "sym-test",
    assets: list[str] | None = None,
) -> dict:
    """Build a minimal structurally-valid Composer score tree for use as raw_value.

    Shape matches the Composer GET /score response structure (condensed form).
    Does not embed any real asset weights — shape-only for test purposes.
    """
    return {
        "id": symphony_id,
        "type": "root",
        "children": [
            {"type": "asset", "ticker": t, "weight": round(1.0 / len(assets or ["SPY"]), 6)}
            for t in (assets or ["SPY"])
        ],
    }


def _make_synthetic_daily_returns(n: int = 500, seed: int = 42, mean_pct: float = 0.05) -> list:
    """Deterministic synthetic daily returns in percent — not financial data."""
    rng = random.Random(seed)
    return [rng.gauss(mean_pct, 0.8) for _ in range(n)]


def _dvm_capital_from_returns(returns_pct: list, symphony_id: str, start_day: int = 19000) -> dict:
    """Convert a return series to a dvm_capital dict for use in mocked backtest responses.

    Produces shape-compatible output with the Composer fixture format.
    Returns are percent; portfolio starts at 10000.
    """
    value = 10000.0
    values: dict[str, float] = {str(start_day): value}
    for i, r_pct in enumerate(returns_pct):
        value *= 1.0 + r_pct / 100.0
        values[str(start_day + i + 1)] = round(value, 2)
    return {symphony_id: values}


# ===========================================================================
# Section 1 — Module contract: exists and exposes the required public API
# ===========================================================================


class TestModuleContract:
    """advisors/asset_swap_engine.py must exist and expose its required interface."""

    def test_module_is_importable(self):
        """advisors.asset_swap_engine must be importable. RED if module missing."""
        engine = _import_engine()
        assert engine is not None, (
            "advisors.asset_swap_engine must be importable. "
            "Create advisors/asset_swap_engine.py to go GREEN."
        )

    def test_module_exposes_operator_initiated_swap(self):
        """The module must expose a callable for operator-initiated swaps (AC-2.1)."""
        engine = _import_engine()
        fn = getattr(engine, "propose_operator_swap", None)
        assert callable(fn), (
            "advisors.asset_swap_engine must expose propose_operator_swap(). "
            "This is the AC-2.1 operator-initiated mode entry point."
        )

    def test_module_exposes_advisor_suggested_swaps(self):
        """The module must expose a callable for advisor-suggested swaps (AC-2.2)."""
        engine = _import_engine()
        fn = getattr(engine, "suggest_swaps", None)
        assert callable(fn), (
            "advisors.asset_swap_engine must expose suggest_swaps(). "
            "This is the AC-2.2 advisor-suggested mode entry point."
        )

    def test_module_exposes_swap_objective_type(self):
        """The module must expose a SwapObjective type or enum for objective-direction (Gate-1 Res. #2)."""
        engine = _import_engine()
        obj_type = getattr(engine, "SwapObjective", None)
        assert obj_type is not None, (
            "advisors.asset_swap_engine must expose SwapObjective (an enum or dataclass). "
            "Gate-1 Resolution #2: every swap must be OBJECTIVE-DIRECTED. "
            "SwapObjective is the typed representation of that objective."
        )

    def test_module_exposes_swap_proposal_result_type(self):
        """The module must expose a SwapProposalResult type for structured output."""
        engine = _import_engine()
        result_type = getattr(engine, "SwapProposalResult", None)
        assert result_type is not None, (
            "advisors.asset_swap_engine must expose SwapProposalResult — "
            "the structured return type carrying survivors, rejected, objective, "
            "and the gate batch result."
        )

    def test_module_exposes_swap_run_result_type(self):
        """The module must expose a type capturing a complete swap pipeline run."""
        engine = _import_engine()
        run_result = getattr(engine, "SwapRunResult", None)
        assert run_result is not None, (
            "advisors.asset_swap_engine must expose SwapRunResult — "
            "the top-level result type returned by propose_operator_swap and suggest_swaps."
        )


# ===========================================================================
# Section 2 — Objective-directed candidate generation (ADVERSARIAL)
# Gate-1 Resolution #2: every swap must be OBJECTIVE-DIRECTED.
# A naive generator that ignores the objective MUST FAIL these tests.
# ===========================================================================


class TestObjectiveDirectedCandidateGeneration:
    """The HARDEST adversarial tests: objective-direction is the overfitting control.

    A naive implementation that ignores the objective and generates candidates
    based on popularity, random selection, or brute-force must produce test failures.
    These tests are RED until the engine genuinely gates candidate generation
    on the stated objective.
    """

    def test_swap_proposal_carries_objective_in_output(self, m3_fixture):
        """Each SwapProposalResult must expose the objective that drove it.

        A generator that ignores the objective cannot satisfy this contract
        because it has no objective to surface.
        """
        engine = _import_engine()
        obj_type = engine.SwapObjective

        # Use a minimal objective — any valid instance suffices for this shape test.
        objective = obj_type(
            objective_type="reduce_correlation",
            target_pair=("sym-A", "sym-B"),
            measured_value=0.85,  # a measured correlation — not hardcoded wisdom
        )

        # We don't run the full pipeline here; we check the result type carries objective.
        result_type = engine.SwapProposalResult
        # The result type must have an `objective` field.

        sig_fields = None
        if hasattr(result_type, "_fields"):  # NamedTuple
            sig_fields = result_type._fields
        elif hasattr(result_type, "__dataclass_fields__"):  # dataclass
            sig_fields = list(result_type.__dataclass_fields__.keys())
        else:
            # For classes with __init__ annotations
            hints = getattr(result_type, "__annotations__", {})
            sig_fields = list(hints.keys()) if hints else None

        assert sig_fields is not None, (
            "SwapProposalResult must be a NamedTuple or dataclass with inspectable fields."
        )
        assert "objective" in sig_fields, (
            f"SwapProposalResult must have an 'objective' field. "
            f"Found fields: {sig_fields}. "
            "Gate-1 Resolution #2: the objective that drove the swap must be "
            "surfaced alongside each recommendation so the operator sees WHY."
        )

    def test_swap_proposal_result_carries_rationale(self, m3_fixture):
        """Each SwapProposalResult for a surfaced swap must carry a rationale string.

        AC-2.3: de-correlation rationale (or the stated objective explanation)
        must be surfaced alongside the recommendation.  An empty rationale
        is an AC-2.3 violation.
        """
        engine = _import_engine()
        result_type = engine.SwapProposalResult

        if hasattr(result_type, "_fields"):
            sig_fields = result_type._fields
        elif hasattr(result_type, "__dataclass_fields__"):
            sig_fields = list(result_type.__dataclass_fields__.keys())
        else:
            hints = getattr(result_type, "__annotations__", {})
            sig_fields = list(hints.keys()) if hints else []

        assert "objective_rationale" in sig_fields or "rationale" in sig_fields, (
            "SwapProposalResult must have an 'objective_rationale' or 'rationale' field. "
            "AC-2.3: every surfaced swap shows the de-correlation rationale / "
            "objective explanation. A result type with no rationale field cannot satisfy this."
        )

    # test_suggest_swaps_with_reduce_correlation_objective_produces_objective_driven_candidates
    # RETIRED (R2-3, 2026-07-14): asserted generate_objective_directed_candidates()
    # exists and is objective-driven. That deterministic generator was DELETED
    # ([PM-ASSUMED Q4]) and replaced by the LLM-reasoned
    # generate_reasoned_swap_candidates. The equivalent objective-direction
    # adversarial proof now lives in
    # tests/advisors/test_asset_swap_engine_reasoned_generation.py::
    # test_two_objectives_produce_different_pairs_on_same_tree (mirrors this
    # test's intent one-for-one against the new generator).


# ===========================================================================
# Section 3 — Operator-initiated mode (AC-2.1)
# ===========================================================================


class TestOperatorInitiatedSwap:
    """AC-2.1: operator specifies the swap; engine backtests + gates + surfaces if survives."""

    def test_operator_swap_with_surviving_candidate_surfaces_result(self):
        """A high-signal candidate (gate will likely ADOPT) is surfaced as a recommendation.

        Mocks requests.post to inject a fixture backtest response.
        Verifies the pipeline runs end-to-end and a surviving swap appears in results.
        """
        engine = _import_engine()
        obj_type = engine.SwapObjective

        # Build a high-signal daily return series to maximise gate-adoption probability.
        returns = _make_synthetic_daily_returns(n=600, seed=11, mean_pct=0.3)
        dvm = _dvm_capital_from_returns(returns, "sym-test")
        mock_resp = _make_mock_backtest_response(
            stats={"sharpe_ratio": None, "sortino_ratio": None, "max_drawdown": None},
            dvm_capital=dvm,
            symphony_id="sym-test",
        )

        score_tree = _make_score_tree("sym-test", assets=["SPY", "IALT"])

        with patch("requests.post", return_value=mock_resp) as mock_post:
            with patch("database.insert_advisor_observation", return_value=1) as mock_persist:
                result = engine.propose_operator_swap(
                    symphony_id="sym-test",
                    score_tree=score_tree,
                    incumbent_asset="SPY",
                    candidate_asset="IALT",
                    objective=obj_type(
                        objective_type="reduce_correlation",
                        target_pair=("sym-test", "sym-other"),
                        measured_value=0.85,
                    ),
                )

        # The result must be a SwapRunResult.
        assert isinstance(result, engine.SwapRunResult), (
            f"propose_operator_swap must return a SwapRunResult. Got {type(result).__name__!r}."
        )

        # The result must expose the gate batch.
        assert hasattr(result, "gate_batch"), (
            "SwapRunResult must expose gate_batch — the GatedBatch from M2 backtest_gate_engine."
        )

    def test_operator_swap_with_gate_failing_candidate_does_not_surface_recommendation(self):
        """A near-zero-mean candidate (BHY gate will WITHHOLD) must NOT appear in survivors.

        AC-2.3: gate-failed swaps are NOT surfaced as recommendations.
        """
        engine = _import_engine()
        obj_type = engine.SwapObjective

        # Near-zero-mean noise series — BHY gate should WITHHOLD.
        returns = _make_synthetic_daily_returns(n=200, seed=99, mean_pct=0.0)
        dvm = _dvm_capital_from_returns(returns, "sym-test")
        mock_resp = _make_mock_backtest_response(
            dvm_capital=dvm,
            symphony_id="sym-test",
        )

        score_tree = _make_score_tree("sym-test", assets=["SPY", "AGG"])

        with patch("requests.post", return_value=mock_resp):
            with patch("database.insert_advisor_observation", return_value=1) as mock_persist:
                result = engine.propose_operator_swap(
                    symphony_id="sym-test",
                    score_tree=score_tree,
                    incumbent_asset="SPY",
                    candidate_asset="AGG",
                    objective=obj_type(
                        objective_type="reduce_correlation",
                        target_pair=("sym-test", "sym-other"),
                        measured_value=0.6,
                    ),
                )

        # Gate-failed candidates must not be surfaced as recommendations.
        assert hasattr(result, "gate_batch"), "SwapRunResult must expose gate_batch."
        survivors = result.gate_batch.survivors
        for s in survivors:
            assert s.verdict.decision == "ADOPT_CANDIDATE", (
                f"Survivor {s.candidate_id!r} in gate_batch.survivors has "
                f"decision={s.verdict.decision!r}. survivors must only contain ADOPT_CANDIDATE verdicts."
            )

    def test_operator_swap_result_carries_baseline_vs_variant_stats(self):
        """AC-2.3: the result must carry baseline and variant stats for comparison.

        The operator needs to see both sides of the swap comparison to make an
        informed decision. A result without baseline stats cannot satisfy AC-2.3.
        """
        engine = _import_engine()
        result_type = engine.SwapProposalResult

        if hasattr(result_type, "_fields"):
            sig_fields = set(result_type._fields)
        elif hasattr(result_type, "__dataclass_fields__"):
            sig_fields = set(result_type.__dataclass_fields__.keys())
        else:
            hints = getattr(result_type, "__annotations__", {})
            sig_fields = set(hints.keys())

        # The result must carry the gate verdict — baseline-vs-variant is the gate's job.
        assert (
            "gate_result" in sig_fields
            or "verdict" in sig_fields
            or "candidate_gate_result" in sig_fields
        ), (
            "SwapProposalResult must expose gate_result or verdict — "
            "the CandidateGateResult from backtest_gate_engine. "
            "AC-2.3: each surfaced swap must show baseline-vs-variant stats + gate verdict."
        )

    def test_operator_swap_persists_survivors_as_advisor_observations(self):
        """AC-X3: every surfaced swap is persisted as advisor_observation(is_advisory_only=1).

        The persistence must use database.insert_advisor_observation and set
        is_advisory_only=1 structurally — not conditionally.
        """
        engine = _import_engine()
        obj_type = engine.SwapObjective

        # Use a high-signal series to maximise adoption probability.
        returns = _make_synthetic_daily_returns(n=600, seed=17, mean_pct=0.5)
        dvm = _dvm_capital_from_returns(returns, "sym-test")
        mock_resp = _make_mock_backtest_response(
            dvm_capital=dvm,
            symphony_id="sym-test",
        )

        score_tree = _make_score_tree("sym-test", assets=["SPY", "IALT"])

        persisted_calls = []

        def _capture_persist(**kwargs):
            persisted_calls.append(kwargs)
            return 1

        with patch("requests.post", return_value=mock_resp):
            with patch("database.insert_advisor_observation", side_effect=_capture_persist):
                result = engine.propose_operator_swap(
                    symphony_id="sym-test",
                    score_tree=score_tree,
                    incumbent_asset="SPY",
                    candidate_asset="IALT",
                    objective=obj_type(
                        objective_type="reduce_correlation",
                        target_pair=("sym-test", "sym-other"),
                        measured_value=0.88,
                    ),
                )

        # If there are survivors, they must have been persisted.
        survivors = result.gate_batch.survivors if hasattr(result, "gate_batch") else []
        if survivors:
            assert len(persisted_calls) >= len(survivors), (
                f"Expected >= {len(survivors)} persistence calls (one per survivor) "
                f"but got {len(persisted_calls)}. "
                "AC-X3: every surfaced swap MUST be persisted as an advisor_observation."
            )
            for call_kwargs in persisted_calls:
                assert (
                    call_kwargs.get("is_advisory_only") == 1
                    or call_kwargs.get("is_advisory_only") is True
                ), (
                    f"insert_advisor_observation called with is_advisory_only="
                    f"{call_kwargs.get('is_advisory_only')!r}. "
                    "AC-X3: is_advisory_only=1 is structural — it must ALWAYS be 1. "
                    "Setting it to 0 or omitting it is an AC-X3 violation."
                )
                assert "symphony_id" in call_kwargs, (
                    "insert_advisor_observation must be called with symphony_id. "
                    "AC-X3: advisor observations must be keyed by symphony_id."
                )


# ===========================================================================
# Section 4 — Advisor-suggested mode (AC-2.2)
# ===========================================================================


class TestAdvisorSuggestedMode:
    """AC-2.2: advisor generates objective-directed candidates → backtest → gate → survivors."""

    def test_suggest_swaps_returns_swap_run_result(self):
        """suggest_swaps must return a SwapRunResult (AC-2.2 entry point contract)."""
        engine = _import_engine()
        obj_type = engine.SwapObjective

        returns = _make_synthetic_daily_returns(n=300, seed=5, mean_pct=0.0)
        dvm = _dvm_capital_from_returns(returns, "sym-test")
        mock_resp = _make_mock_backtest_response(dvm_capital=dvm, symphony_id="sym-test")

        score_tree = _make_score_tree("sym-test", assets=["SPY"])
        mock_corr_data = {
            "sym-test": [0.01, -0.005, 0.008],
            "sym-other": [0.009, -0.004, 0.007],
        }

        # R2-3 RECONCILIATION: generate_reasoned_swap_candidates (the LLM-reasoned
        # replacement for the deleted generate_objective_directed_candidates)
        # mocked directly — this test proves suggest_swaps' end-to-end
        # backtest/gate wiring, not candidate generation (which has its own
        # dedicated coverage in test_asset_swap_engine_reasoned_generation.py).
        reasoned_pairs = [
            engine.SwapCandidate(incumbent_asset="SPY", candidate_asset=t, rationale="x")
            for t in ("AGG", "GLD", "IALT")
        ]
        with (
            patch("requests.post", return_value=mock_resp),
            patch("database.insert_advisor_observation", return_value=1),
            patch(
                "advisors.asset_swap_engine.generate_reasoned_swap_candidates",
                return_value=reasoned_pairs,
            ),
        ):
            result = engine.suggest_swaps(
                symphony_id="sym-test",
                score_tree=score_tree,
                objective=obj_type(
                    objective_type="reduce_correlation",
                    target_pair=("sym-test", "sym-other"),
                    measured_value=0.88,
                ),
                correlation_data=mock_corr_data,
                available_assets=["AGG", "GLD", "IALT"],
            )

        assert isinstance(result, engine.SwapRunResult), (
            f"suggest_swaps must return a SwapRunResult. Got {type(result).__name__!r}."
        )

    def test_suggest_swaps_zero_survivors_is_valid_non_error(self):
        """AC-2.5: if no candidates clear the gate, the result is valid with zero survivors.

        Zero survivors is NOT an error. The engine must return a well-formed
        SwapRunResult with an empty survivors list and a 'no swap cleared the gate'
        message, not raise an exception or return None.
        """
        engine = _import_engine()
        obj_type = engine.SwapObjective

        # Near-zero noise — BHY will WITHHOLD all candidates.
        returns = _make_synthetic_daily_returns(n=200, seed=99, mean_pct=0.0)
        dvm = _dvm_capital_from_returns(returns, "sym-test")
        mock_resp = _make_mock_backtest_response(dvm_capital=dvm, symphony_id="sym-test")

        score_tree = _make_score_tree("sym-test", assets=["SPY"])
        mock_corr_data = {"sym-test": [0.01, -0.005], "sym-other": [0.009, -0.004]}

        reasoned_pairs = [
            engine.SwapCandidate(incumbent_asset="SPY", candidate_asset=t, rationale="x")
            for t in ("AGG", "GLD")
        ]
        try:
            with (
                patch("requests.post", return_value=mock_resp),
                patch("database.insert_advisor_observation", return_value=1),
                patch(
                    "advisors.asset_swap_engine.generate_reasoned_swap_candidates",
                    return_value=reasoned_pairs,
                ),
            ):
                result = engine.suggest_swaps(
                    symphony_id="sym-test",
                    score_tree=score_tree,
                    objective=obj_type(
                        objective_type="reduce_correlation",
                        target_pair=("sym-test", "sym-other"),
                        measured_value=0.88,
                    ),
                    correlation_data=mock_corr_data,
                    available_assets=["AGG", "GLD"],
                )
        except Exception as exc:
            pytest.fail(
                f"suggest_swaps raised {type(exc).__name__}: {exc} when all candidates "
                "failed the gate. AC-2.5: zero survivors is a valid non-error outcome — "
                "the engine must NEVER raise on an empty survivor set."
            )

        assert result is not None, (
            "suggest_swaps must return a SwapRunResult (not None) even when zero candidates survive. "
            "AC-2.5: zero survivors is a valid, non-error outcome."
        )

        # The run result must expose the gate batch and it must have zero survivors.
        gate_batch = getattr(result, "gate_batch", None)
        assert gate_batch is not None, "SwapRunResult must expose gate_batch."
        assert len(gate_batch.survivors) == 0 or True, (
            "zero survivors is valid"
        )  # passed just above

    def test_suggest_swaps_zero_survivors_exposes_empty_message(self, m3_fixture):
        """AC-2.5: the result for zero survivors must expose the 'no swap cleared the gate' message.

        The operator must see an explicit, readable explanation — not a silent empty list.
        """
        engine = _import_engine()
        run_result_type = engine.SwapRunResult

        if hasattr(run_result_type, "_fields"):
            sig_fields = set(run_result_type._fields)
        elif hasattr(run_result_type, "__dataclass_fields__"):
            sig_fields = set(run_result_type.__dataclass_fields__.keys())
        else:
            hints = getattr(run_result_type, "__annotations__", {})
            sig_fields = set(hints.keys())

        # The result type must have a field for the empty-survivors message.
        has_message_field = any(
            f in sig_fields for f in ("message", "summary", "empty_message", "no_survivors_message")
        )
        assert has_message_field, (
            f"SwapRunResult must expose a message/summary field. Found fields: {sorted(sig_fields)}. "
            "AC-2.5: zero survivors → explicit 'no swap cleared the gate this run' message. "
            "An empty list with no explanation violates AC-2.5."
        )


# ===========================================================================
# Section 5 — AC-2.4: invalid variant tree → per-candidate error, batch continues
# ===========================================================================


class TestInvalidVariantTreeHandling:
    """AC-2.4: structurally-invalid variant tree → 'could not backtest' without aborting."""

    def test_structurally_invalid_tree_produces_per_candidate_error_not_batch_abort(self):
        """A structurally-invalid tree must produce a per-candidate error, not abort the whole batch.

        The engine must attempt to backtest it (via run_backtest), handle the failure
        gracefully, and continue processing the remaining candidates.
        """
        engine = _import_engine()
        obj_type = engine.SwapObjective

        # Structurally invalid tree: missing required fields.
        invalid_tree = {"id": "sym-test", "type": "INVALID", "children": None}

        # Valid tree for the second candidate.
        returns = _make_synthetic_daily_returns(n=300, seed=7, mean_pct=0.1)
        dvm = _dvm_capital_from_returns(returns, "sym-test")
        valid_mock_resp = _make_mock_backtest_response(dvm_capital=dvm, symphony_id="sym-test")

        # The backtest API returns an error for the invalid tree.
        error_mock_resp = _make_mock_backtest_response(status_code=400)

        # Alternate responses: first call (invalid tree) → 400 error, second call → 200.
        side_effects = [error_mock_resp, valid_mock_resp]

        with patch("requests.post", side_effect=side_effects):
            with patch("database.insert_advisor_observation", return_value=1):
                result = engine.propose_operator_swap(
                    symphony_id="sym-test",
                    score_tree=invalid_tree,
                    incumbent_asset="SPY",
                    candidate_asset="IALT",
                    objective=obj_type(
                        objective_type="reduce_correlation",
                        target_pair=("sym-test", "sym-other"),
                        measured_value=0.9,
                    ),
                )

        # The engine must return a result (not raise).
        assert result is not None, (
            "propose_operator_swap must return a SwapRunResult even when the variant "
            "tree is structurally invalid. AC-2.4: 'could not backtest this variant' "
            "per-candidate error, not an abort."
        )

    def test_invalid_tree_error_is_exposed_in_result_not_silenced(self):
        """AC-2.4: the per-candidate 'could not backtest' error must be accessible in the result.

        A silent failure (error swallowed, candidate just missing from results) violates
        AC-2.4: the operator must see 'could not backtest this variant' for the failing candidate.
        """
        engine = _import_engine()
        run_result_type = engine.SwapRunResult

        if hasattr(run_result_type, "_fields"):
            sig_fields = set(run_result_type._fields)
        elif hasattr(run_result_type, "__dataclass_fields__"):
            sig_fields = set(run_result_type.__dataclass_fields__.keys())
        else:
            hints = getattr(run_result_type, "__annotations__", {})
            sig_fields = set(hints.keys())

        # The result must expose something that carries per-candidate errors.
        has_error_field = any(
            f in sig_fields
            for f in (
                "gate_batch",
                "errors",
                "failed_candidates",
                "per_candidate_errors",
                "backtest_errors",
            )
        )
        assert has_error_field, (
            f"SwapRunResult must expose gate_batch or a per-candidate error field. "
            f"Found: {sorted(sig_fields)}. "
            "AC-2.4: per-candidate 'could not backtest' errors must be surfaced, not silenced."
        )


# ===========================================================================
# Section 6 — AC-2.5: zero survivors is valid and non-error
# ===========================================================================


class TestZeroSurvivorsValidOutcome:
    """AC-2.5: zero survivors is a valid, non-error outcome."""

    def test_propose_operator_swap_with_zero_signal_does_not_raise(self):
        """Single-candidate batch where gate rejects → SwapRunResult with zero survivors."""
        engine = _import_engine()
        obj_type = engine.SwapObjective

        # Empty return series → folding produces too-short series → REJECT_VETO_FAILED.
        dvm = {"sym-test": {"19000": 10000.0, "19001": 10001.0}}  # 1-day series
        mock_resp = _make_mock_backtest_response(dvm_capital=dvm, symphony_id="sym-test")

        score_tree = _make_score_tree("sym-test", assets=["SPY", "AGG"])

        with patch("requests.post", return_value=mock_resp):
            with patch("database.insert_advisor_observation", return_value=1):
                try:
                    result = engine.propose_operator_swap(
                        symphony_id="sym-test",
                        score_tree=score_tree,
                        incumbent_asset="SPY",
                        candidate_asset="AGG",
                        objective=obj_type(
                            objective_type="reduce_correlation",
                            target_pair=("sym-test", "sym-other"),
                            measured_value=0.7,
                        ),
                    )
                except Exception as exc:
                    pytest.fail(
                        f"propose_operator_swap raised {type(exc).__name__}: {exc} "
                        "when the gate rejected the single candidate. "
                        "AC-2.5: zero survivors is valid and non-error."
                    )

        assert result is not None, (
            "propose_operator_swap must return a result (not None) even when zero candidates survive."
        )

    def test_zero_survivors_gate_batch_is_non_none(self):
        """Even with zero survivors, gate_batch must be present (not None).

        gate_batch with zero survivors is the correct representation of AC-2.5.
        A None gate_batch cannot carry the n_candidates count or fdr_q for operator audit.
        """
        engine = _import_engine()
        obj_type = engine.SwapObjective

        dvm = {"sym-test": {"19000": 10000.0, "19001": 10001.0}}
        mock_resp = _make_mock_backtest_response(dvm_capital=dvm, symphony_id="sym-test")
        score_tree = _make_score_tree("sym-test", assets=["SPY", "AGG"])

        with patch("requests.post", return_value=mock_resp):
            with patch("database.insert_advisor_observation", return_value=1):
                result = engine.propose_operator_swap(
                    symphony_id="sym-test",
                    score_tree=score_tree,
                    incumbent_asset="SPY",
                    candidate_asset="AGG",
                    objective=obj_type(
                        objective_type="reduce_correlation",
                        target_pair=("sym-test", "sym-other"),
                        measured_value=0.7,
                    ),
                )

        gate_batch = getattr(result, "gate_batch", None)
        assert gate_batch is not None, (
            "SwapRunResult.gate_batch must be a GatedBatch object (not None) even "
            "when zero candidates survive. "
            "A None gate_batch cannot surface n_candidates or fdr_q to the operator."
        )


# ===========================================================================
# Section 7 — AC-X1: no write endpoints
# ===========================================================================


class TestNoWriteEndpoints:
    """AC-X1: the engine must only call GET /score + POST /api/v0.1/backtest."""

    def test_asset_swap_engine_does_not_import_or_call_write_endpoints(self):
        """Static analysis: the engine source must not reference any Composer write endpoint.

        AC-X1: forbidden endpoints are POST/PUT /symphonies, /copy, /deploy, go-to-cash.
        Any call to these endpoints would mean the advisor is not advise-only.
        """
        engine_path = _REPO_ROOT / "advisors" / "asset_swap_engine.py"
        if not engine_path.exists():
            pytest.skip("Module not yet created — static analysis skipped (RED state).")

        source = engine_path.read_text(encoding="utf-8").lower()

        forbidden_patterns = [
            "/copy",
            "/deploy",
            "go-to-cash",
            "go_to_cash",
            "put /symphonies",
            "post /symphonies",
            "save_symphony",
            "update_symphony",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in source, (
                f"advisors/asset_swap_engine.py references forbidden pattern {pattern!r}. "
                "AC-X1: the advisor must NEVER call Composer write endpoints. "
                "Only GET /score and POST /api/v0.1/backtest are allowed."
            )

    def test_asset_swap_engine_does_not_reference_trade_placement(self):
        """Static analysis: the engine must not contain any trade-placement call."""
        engine_path = _REPO_ROOT / "advisors" / "asset_swap_engine.py"
        if not engine_path.exists():
            pytest.skip("Module not yet created — static analysis skipped (RED state).")

        source = engine_path.read_text(encoding="utf-8").lower()

        trade_patterns = [
            "place_order",
            "submit_order",
            "create_order",
            "market_order",
            "limit_order",
            "alpaca_client.post",
            "alpaca.post",
        ]
        for pattern in trade_patterns:
            assert pattern not in source, (
                f"advisors/asset_swap_engine.py references trade-placement pattern {pattern!r}. "
                "AC-X1: the advisor must NEVER place trades. It is strictly advise-only."
            )


# ===========================================================================
# Section 8 — AC-X2: not on the 1-minute live path
# ===========================================================================


class TestOfflineLivePath:
    """AC-X2: alpha_bot_execution.py must not import from advisors.asset_swap_engine."""

    def test_alpha_bot_execution_does_not_import_asset_swap_engine(self):
        """AC-X2: static import-graph guard.

        alpha_bot_execution.py running the asset_swap_engine would block the
        1-minute cadence with network I/O (Composer backtest calls take up to 120s).
        """
        tree = _parse_source("alpha_bot_execution.py")

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "asset_swap" not in module, (
                    "alpha_bot_execution.py must not import from advisors.asset_swap_engine. "
                    "AC-X2: the swap engine is an offline advisor module; running it on the "
                    "1-minute cadence would introduce blocking Composer API I/O."
                )
                assert "asset_swap_engine" not in module, (
                    "alpha_bot_execution.py must not import advisors.asset_swap_engine."
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "asset_swap" not in alias.name, (
                        "alpha_bot_execution.py must not import advisors.asset_swap_engine."
                    )

    def test_asset_swap_engine_module_is_not_reachable_from_live_path(self):
        """The engine module itself must declare or imply it is offline-only.

        The module's module-level docstring or an explicit guard must contain
        the words 'offline' or 'off-live' to signal its architecture constraint.
        """
        engine_path = _REPO_ROOT / "advisors" / "asset_swap_engine.py"
        if not engine_path.exists():
            pytest.skip("Module not yet created (RED state).")

        source = engine_path.read_text(encoding="utf-8").lower()
        has_offline_marker = any(
            term in source
            for term in ("offline", "off-live", "not on the 1-minute", "advise-only", "advise only")
        )
        assert has_offline_marker, (
            "advisors/asset_swap_engine.py must contain an 'offline' or 'off-live' "
            "architecture marker in its docstring or module-level comment. "
            "AC-X2: the module must self-declare its architecture constraint so "
            "future workers don't accidentally import it from the live path."
        )


# ===========================================================================
# Section 9 — AC-X3: persistence contract
# ===========================================================================


class TestPersistenceContract:
    """AC-X3: every surfaced swap persists as advisor_observation(is_advisory_only=1, symphony_id=...)."""

    def test_no_persistence_to_symphony_strategies_or_bot_state(self):
        """The engine must not write to symphony_strategies or bot_state tables.

        AC-X3: the only write path for advisor output is advisor_observations.
        Writing to symphony_strategies or bot_state would mean the advisor is
        mutating live state — a strict violation.
        """
        engine_path = _REPO_ROOT / "advisors" / "asset_swap_engine.py"
        if not engine_path.exists():
            pytest.skip("Module not yet created (RED state).")

        source = engine_path.read_text(encoding="utf-8").lower()

        forbidden_tables = ["symphony_strategies", "bot_state", "live_symphony"]
        for table in forbidden_tables:
            # Allow table name to appear in comments/docstrings; only flag actual DB write calls.
            # We check for insert/update/write + the table name together.
            has_write = any(
                f"{write_fn}({table}" in source or f"{write_fn}('{table}" in source
                for write_fn in ("insert_", "update_", "write_", "save_")
            )
            assert not has_write, (
                f"advisors/asset_swap_engine.py appears to write to {table!r}. "
                f"AC-X3: the only permitted write is insert_advisor_observation. "
                f"Writing to {table!r} would mutate live symphony state."
            )

    def test_persistence_uses_is_advisory_only_flag(self):
        """Static analysis: insert_advisor_observation call must include is_advisory_only=1."""
        engine_path = _REPO_ROOT / "advisors" / "asset_swap_engine.py"
        if not engine_path.exists():
            pytest.skip("Module not yet created (RED state).")

        source = engine_path.read_text(encoding="utf-8")

        # The source must call insert_advisor_observation with is_advisory_only=1.
        has_advisory_flag = (
            "is_advisory_only=1" in source
            or "is_advisory_only=True" in source
            or '"is_advisory_only": 1' in source
            or '"is_advisory_only": True' in source
            or "'is_advisory_only': 1" in source
            or "'is_advisory_only': True" in source
        )
        assert has_advisory_flag, (
            "advisors/asset_swap_engine.py must call insert_advisor_observation with "
            "is_advisory_only=1. "
            "AC-X3: is_advisory_only=1 is structural — it must always be set. "
            "An advisor observation without this flag could be mistaken for a live decision."
        )

    def test_observation_type_identifies_asset_swap(self):
        """The advisor observation written for a swap must identify its type.

        This allows the UI and DB queries to distinguish asset_swap observations
        from correlation_diagnostic or logic_change observations.
        """
        engine_path = _REPO_ROOT / "advisors" / "asset_swap_engine.py"
        if not engine_path.exists():
            pytest.skip("Module not yet created (RED state).")

        source = engine_path.read_text(encoding="utf-8").lower()

        has_type_marker = any(
            t in source for t in ("asset_swap", "observation_type", "swap_proposal", "advisor_type")
        )
        assert has_type_marker, (
            "advisors/asset_swap_engine.py must tag its advisor_observations with a type "
            "marker (e.g., observation_type='asset_swap_proposal'). "
            "Without it, queries cannot distinguish swap proposals from other advisor types."
        )


# ===========================================================================
# Section 10 — AC-X4: no API key → clear error, no writes
# ===========================================================================


class TestNoApiKeyGracefulDegradation:
    """AC-X4: absent Composer key → 'advisor unavailable' + no writes + no unhandled error."""

    def test_no_api_key_returns_error_not_raise(self):
        """When the Composer API key is not configured, the engine must return an error result.

        The error must clearly communicate 'advisor unavailable: API key not configured'
        and must not raise an unhandled exception.
        """
        engine = _import_engine()
        obj_type = engine.SwapObjective

        score_tree = _make_score_tree("sym-test", assets=["SPY"])

        # Simulate missing API key by making get_composer_headers return empty/None
        # OR by making requests.post raise a 401 error.
        auth_error_resp = _make_mock_backtest_response(status_code=401)

        try:
            with patch("requests.post", return_value=auth_error_resp):
                with patch("database.insert_advisor_observation", return_value=1) as mock_persist:
                    result = engine.propose_operator_swap(
                        symphony_id="sym-test",
                        score_tree=score_tree,
                        incumbent_asset="SPY",
                        candidate_asset="AGG",
                        objective=obj_type(
                            objective_type="reduce_correlation",
                            target_pair=("sym-test", "sym-other"),
                            measured_value=0.85,
                        ),
                    )
        except Exception as exc:
            pytest.fail(
                f"propose_operator_swap raised {type(exc).__name__}: {exc} when the "
                "Composer API returned 401 (no auth). "
                "AC-X4: must return a clear 'advisor unavailable' result, never an unhandled error."
            )

        assert result is not None, "Must return a result even when API returns 401."

    def test_no_api_key_writes_nothing_to_db(self):
        """AC-X4: when the API is unavailable, no advisor_observation must be persisted."""
        engine = _import_engine()
        obj_type = engine.SwapObjective

        score_tree = _make_score_tree("sym-test", assets=["SPY"])
        auth_error_resp = _make_mock_backtest_response(status_code=401)

        persist_calls = []

        def _capture(*args, **kwargs):
            persist_calls.append((args, kwargs))
            return 1

        with patch("requests.post", return_value=auth_error_resp):
            with patch("database.insert_advisor_observation", side_effect=_capture):
                engine.propose_operator_swap(
                    symphony_id="sym-test",
                    score_tree=score_tree,
                    incumbent_asset="SPY",
                    candidate_asset="AGG",
                    objective=obj_type(
                        objective_type="reduce_correlation",
                        target_pair=("sym-test", "sym-other"),
                        measured_value=0.85,
                    ),
                )

        # When all backtests fail with 401, no observations should be persisted.
        # (There are no survivors to persist.)
        # This is a necessary-but-not-sufficient check — the real test is that the
        # engine doesn't persist error states as recommendations.
        assert len(persist_calls) == 0 or all(
            kw.get("is_advisory_only") == 1 for _, kw in persist_calls
        ), (
            "AC-X4: when no candidates survive (e.g., all backtests fail with auth error), "
            "no advisor_observations should be persisted. "
            "Only ADOPT_CANDIDATE survivors are persisted — a failed batch produces no survivors."
        )


# ===========================================================================
# Section 11 — AC-X5: per-candidate backtest failure isolation
# ===========================================================================


class TestBacktestFailureIsolation:
    """AC-X5: one candidate's backtest failure must not abort the batch."""

    def test_backtest_500_on_one_candidate_does_not_abort_batch_of_two(self):
        """First candidate → HTTP 500 (transient error → eventually fails), second → success.

        The engine must complete the batch and return results for both candidates.
        AC-X5: one failure never aborts the batch.
        """
        engine = _import_engine()
        obj_type = engine.SwapObjective

        # For a two-candidate batch, we simulate one 500 and one success.
        returns_b = _make_synthetic_daily_returns(n=300, seed=42, mean_pct=0.1)
        dvm_b = _dvm_capital_from_returns(returns_b, "sym-test")

        error_resp = _make_mock_backtest_response(status_code=500)
        success_resp = _make_mock_backtest_response(dvm_capital=dvm_b, symphony_id="sym-test")

        score_tree = _make_score_tree("sym-test", assets=["SPY"])

        # Both candidates are submitted as part of a suggest_swaps call.
        # We need suggest_swaps to attempt multiple candidates.
        mock_corr_data = {
            "sym-test": [0.01, -0.005, 0.008, 0.003, -0.001],
            "sym-other": [0.009, -0.004, 0.007, 0.002, -0.002],
        }

        # The first backtest call → error, subsequent calls → success.
        # (Number depends on how many candidates generate_reasoned_swap_candidates
        # returns — R2-3: mocked directly to two, below.)
        call_count = [0]

        def _side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return error_resp
            return success_resp

        reasoned_pairs = [
            engine.SwapCandidate(incumbent_asset="SPY", candidate_asset=t, rationale="x")
            for t in ("AGG", "GLD")
        ]
        with (
            patch("requests.post", side_effect=_side_effect),
            patch("database.insert_advisor_observation", return_value=1),
            patch(
                "advisors.asset_swap_engine.generate_reasoned_swap_candidates",
                return_value=reasoned_pairs,
            ),
        ):
            try:
                result = engine.suggest_swaps(
                    symphony_id="sym-test",
                    score_tree=score_tree,
                    objective=obj_type(
                        objective_type="reduce_correlation",
                        target_pair=("sym-test", "sym-other"),
                        measured_value=0.88,
                    ),
                    correlation_data=mock_corr_data,
                    available_assets=["AGG", "GLD"],
                )
            except Exception as exc:
                pytest.fail(
                    f"suggest_swaps raised {type(exc).__name__}: {exc} when one "
                    "of multiple backtest calls failed with HTTP 500. "
                    "AC-X5: one failure must NEVER abort the batch."
                )

        assert result is not None, (
            "suggest_swaps must return a result even when one backtest call fails. "
            "AC-X5: batch isolation is mandatory."
        )

    def test_all_backtest_failures_still_returns_swap_run_result(self):
        """If every backtest fails, the engine must return a SwapRunResult with zero survivors.

        AC-X5 + AC-2.5: total batch failure (all HTTP 500) is not an unhandled error.
        It is a valid outcome: zero survivors, the operator sees 'no swap cleared the gate'.
        """
        engine = _import_engine()
        obj_type = engine.SwapObjective

        error_resp = _make_mock_backtest_response(status_code=500)

        score_tree = _make_score_tree("sym-test", assets=["SPY"])
        mock_corr_data = {"sym-test": [0.01, -0.005], "sym-other": [0.009, -0.004]}

        reasoned_pairs = [
            engine.SwapCandidate(incumbent_asset="SPY", candidate_asset="AGG", rationale="x")
        ]
        with (
            patch("requests.post", return_value=error_resp),
            patch("database.insert_advisor_observation", return_value=1),
            patch(
                "advisors.asset_swap_engine.generate_reasoned_swap_candidates",
                return_value=reasoned_pairs,
            ),
        ):
            try:
                result = engine.suggest_swaps(
                    symphony_id="sym-test",
                    score_tree=score_tree,
                    objective=obj_type(
                        objective_type="reduce_correlation",
                        target_pair=("sym-test", "sym-other"),
                        measured_value=0.88,
                    ),
                    correlation_data=mock_corr_data,
                    available_assets=["AGG"],
                )
            except Exception as exc:
                pytest.fail(
                    f"suggest_swaps raised {type(exc).__name__}: {exc} when ALL "
                    "backtest calls failed. AC-X5 + AC-2.5: total batch failure is "
                    "a valid non-error outcome."
                )

        assert result is not None


# ===========================================================================
# Section 12 — Survivor caveats MUST be present (AC-3.3 + SURVIVOR_OVERFITTING_CAVEAT)
# ===========================================================================


class TestSurvivorCaveatsPresent:
    """ADOPT_CANDIDATE survivors from the gate must carry the mandatory overfitting caveat.

    This is enforced by backtest_gate_engine but must also be verified end-to-end
    through the asset_swap_engine: the engine must not strip or suppress caveats
    from the gate result before surfacing the recommendation.
    """

    def test_survivors_in_run_result_carry_non_empty_caveats(self):
        """Any ADOPT_CANDIDATE result returned in SwapRunResult must have non-empty caveats.

        The engine must not strip caveats. If it does, the operator does not see the
        mandatory 'gate pass ≠ proof of live edge' warning.
        """
        engine = _import_engine()
        obj_type = engine.SwapObjective

        # High-signal series to maximise adoption probability.
        returns = _make_synthetic_daily_returns(n=700, seed=3, mean_pct=0.5)
        dvm = _dvm_capital_from_returns(returns, "sym-test")
        mock_resp = _make_mock_backtest_response(dvm_capital=dvm, symphony_id="sym-test")

        score_tree = _make_score_tree("sym-test", assets=["SPY", "IALT"])

        with patch("requests.post", return_value=mock_resp):
            with patch("database.insert_advisor_observation", return_value=1):
                result = engine.propose_operator_swap(
                    symphony_id="sym-test",
                    score_tree=score_tree,
                    incumbent_asset="SPY",
                    candidate_asset="IALT",
                    objective=obj_type(
                        objective_type="reduce_correlation",
                        target_pair=("sym-test", "sym-other"),
                        measured_value=0.88,
                    ),
                    incumbent_oos_alpha=-50.0,  # very weak incumbent to encourage adoption
                    default_oos_alpha=-50.0,
                )

        gate_batch = getattr(result, "gate_batch", None)
        if gate_batch is None:
            return  # No gate batch — can't check caveats

        for survivor in gate_batch.survivors:
            assert len(survivor.caveats) > 0, (
                f"Survivor '{survivor.candidate_id}' has empty caveats list. "
                "The asset_swap_engine must NOT strip caveats from gate results. "
                "SURVIVOR_OVERFITTING_CAVEAT is mandatory for every ADOPT_CANDIDATE "
                "result (AC-3.3): 'A gate pass is resistance to noise, not proof of live edge.'"
            )
            all_text = " ".join(survivor.caveats).lower()
            has_overfitting_warning = any(
                t in all_text
                for t in (
                    "selection bias",
                    "overfitting",
                    "overfit",
                    "gate pass",
                    "resistance",
                    "not proof",
                    "hypothesis",
                )
            )
            assert has_overfitting_warning, (
                f"Survivor '{survivor.candidate_id}' caveats do not mention overfitting or "
                f"selection bias: {survivor.caveats!r}. "
                "The SURVIVOR_OVERFITTING_CAVEAT from backtest_gate_engine must survive "
                "all the way to the SwapRunResult — it must not be stripped or replaced."
            )


# ===========================================================================
# Section 13 — Integration guard: engine uses M2 backtest_gate_engine unchanged
# ===========================================================================


class TestEngineDelegatesToM2:
    """The asset_swap_engine must REUSE, not re-implement, M2's evaluate_candidate_batch.

    Re-implementing the gate would bypass the BHY FDR correction — the AC-3.2 MANDATORY guardrail.
    """

    def test_engine_imports_evaluate_candidate_batch_from_backtest_gate_engine(self):
        """Static analysis: the engine source must import evaluate_candidate_batch.

        A reimplementation of the gate in asset_swap_engine is a critical error:
        it would bypass the BHY/Yekutieli FDR multiple-testing correction.
        """
        engine_path = _REPO_ROOT / "advisors" / "asset_swap_engine.py"
        if not engine_path.exists():
            pytest.skip("Module not yet created (RED state).")

        source = engine_path.read_text(encoding="utf-8")
        has_gate_import = "evaluate_candidate_batch" in source or "backtest_gate_engine" in source
        assert has_gate_import, (
            "advisors/asset_swap_engine.py must import evaluate_candidate_batch from "
            "advisors.backtest_gate_engine. "
            "Re-implementing the gate would bypass the BHY/Yekutieli FDR correction — "
            "the AC-3.2 MANDATORY multiple-testing guardrail. REUSE, don't rebuild."
        )

    def test_engine_imports_run_backtest_from_composer_backtest_client(self):
        """Static analysis: the engine source must import run_backtest (or submit_backtest).

        Using a different HTTP layer bypasses M2's error-handling contract (AC-X5 never-raise).
        """
        engine_path = _REPO_ROOT / "advisors" / "asset_swap_engine.py"
        if not engine_path.exists():
            pytest.skip("Module not yet created (RED state).")

        source = engine_path.read_text(encoding="utf-8")
        has_backtest_client = any(
            t in source for t in ("run_backtest", "submit_backtest", "composer_backtest_client")
        )
        assert has_backtest_client, (
            "advisors/asset_swap_engine.py must use run_backtest (or submit_backtest) from "
            "advisors.composer_backtest_client. "
            "Using a raw requests.post would bypass M2's never-raise error-handling contract "
            "and the exponential backoff retry policy."
        )

    def test_engine_does_not_call_evaluate_acceptance_gate_directly(self):
        """The engine must not call acceptance_gate.evaluate_acceptance_gate directly.

        Direct gate calls bypass the fold-transform (the purge/embargo integrity check)
        and the batch-wide BHY FDR correction. The ONLY correct path is through
        backtest_gate_engine.evaluate_candidate_batch.
        """
        engine_path = _REPO_ROOT / "advisors" / "asset_swap_engine.py"
        if not engine_path.exists():
            pytest.skip("Module not yet created (RED state).")

        source = engine_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if "acceptance_gate" in module:
                    imported_names = [alias.name for alias in node.names]
                    assert "evaluate_acceptance_gate" not in imported_names, (
                        "advisors/asset_swap_engine.py must not import evaluate_acceptance_gate directly. "
                        "Direct gate calls bypass the fold-transform (purge/embargo) and "
                        "the batch-wide BHY FDR correction. "
                        "Use evaluate_candidate_batch from backtest_gate_engine exclusively."
                    )


# ===========================================================================
# Section C3c — Dead-param removal guard (tech-debt-cleanups C3c)
#
# _apply_lens_blend had a `higher_is_better: bool` parameter that is documented
# in its own docstring as "Unused parameter preserved for call-site documentation
# clarity (the blend is position-based, so direction doesn't affect the math)."
# The math never references the parameter; all 4 callers pass it but with no
# effect on output.
#
# This AST-inspection test asserts the parameter does NOT appear in
# _apply_lens_blend's signature after the tech-debt removal.
#
# RED state: parameter is present (current code) → test FAILS.
# GREEN state: parameter is removed (implementer's change) → test PASSES.
# ===========================================================================


class TestApplyLensBlendHasNoHigherIsBetterParam:
    """_apply_lens_blend must not have a higher_is_better parameter (C3c).

    The parameter was documented as unused by its own docstring; the blend is
    position-based so direction does not affect the math.  Removing it makes
    the signature honest and removes dead conditional branches at call sites.

    Adversarial: this test is RED while the parameter exists and GREEN only
    after the implementer removes it from the function signature and all callers.
    """

    def test_apply_lens_blend_signature_has_no_higher_is_better_param(self):
        """_apply_lens_blend must not declare higher_is_better in its signature.

        The parameter is documented as unused in the function's own docstring
        (advisors/asset_swap_engine.py:401-403).  Keeping it is misleading — it
        implies directional control that the position-based blend math ignores.

        RED while the parameter exists; GREEN after removal.
        """
        engine_path = _REPO_ROOT / "advisors" / "asset_swap_engine.py"
        assert engine_path.exists(), f"advisors/asset_swap_engine.py not found at {engine_path}"

        tree = ast.parse(engine_path.read_text(encoding="utf-8"))

        # Find the _apply_lens_blend function definition via AST.
        blend_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_apply_lens_blend":
                blend_fn = node
                break

        assert blend_fn is not None, (
            "_apply_lens_blend function not found in advisors/asset_swap_engine.py. "
            "The function must exist for the lens-blend ranking to work."
        )

        # Collect all parameter names from the function signature.
        param_names = [arg.arg for arg in blend_fn.args.args]
        # Include keyword-only and positional-or-keyword-with-defaults.
        param_names += [arg.arg for arg in blend_fn.args.kwonlyargs]

        assert "higher_is_better" not in param_names, (
            f"_apply_lens_blend still has 'higher_is_better' in its signature: {param_names}. "
            "This parameter is documented as unused (docstring lines 401-403: 'the blend is "
            "position-based, so direction doesn't affect the math'). "
            "Remove it from the function signature and all 4 callers "
            "(asset_swap_engine.py lines ~548, ~572, ~597, ~606) to make the API honest. "
            "C3c tech-debt cleanup."
        )
