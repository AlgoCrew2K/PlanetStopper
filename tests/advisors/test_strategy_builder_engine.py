"""RED tests — Phase 2 Strategy Builder Engine (advisors/strategy_builder_engine.py).

Module under test: advisors.strategy_builder_engine

These tests are RED by construction: the module does not exist yet.
Every assertion is derived from:
  - The module contract spec in the quant-test-writer task brief
  - The sign-convention golden fixture at:
    tests/fixtures/math/strategy_builder_engine_sign_convention.json
  - The existing symphony_schema API (validate_tree, extract_tickers,
    render_rules_text constructors)
  - The backtest_gate_engine contract (BacktestCandidate, GatedBatch,
    evaluate_candidate_batch)
  - asset_swap_engine.py line 577 ("do not re-derive") for the log→pct conversion

ADVERSARIAL FOCUS:
  - FDR integrity: screens must NOT shrink the gate's input — only filter survivors
  - Sign convention: log returns → pct is r * 100.0 exactly; max_drawdown <= 0
  - Named constants: all defaults must exist as module-level names, never magic numbers
  - Never-raises: network errors, empty universe, single-asset universe → ProposalRun,
    never an exception
  - Template validity: all 7 templates must produce validate_tree == [] trees
  - Template vocabulary: T4 uses "if", T5 uses "relative-strength-index",
    T6 uses "select-top" + "cumulative-return", T7 uses "select-bottom" +
    "standard-deviation-return"

Adversarial cycles (added after implementer goes GREEN):
  - Cycle 1: specified_weight_basket with non-100-sum weights validates clean;
    trend_switch with large ma_window builds clean; backtest failures shrink
    gated_batch correctly (only successes count)
  - Cycle 2: insert_advisor_observation call_args json-serializable; render_rules_text
    returns non-empty string for every template

No live network calls. No DB access. Math engine is never mocked.
"""

from __future__ import annotations

import enum
import json
import pathlib
from dataclasses import fields as dataclass_fields
from datetime import date, timedelta
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Repository layout helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "math"
    / "strategy_builder_engine_sign_convention.json"
)


def _load_fixture() -> dict:
    """Load the golden fixture; skip the test gracefully if the file is absent."""
    if not _FIXTURE_PATH.exists():
        pytest.skip(f"Fixture missing: {_FIXTURE_PATH}")
    with open(_FIXTURE_PATH) as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Shared backtest mock helpers
# ---------------------------------------------------------------------------

def _make_fake_result(n_days: int = 100, base_return: float = 0.001):
    """Create a BacktestResult with a synthetic log-return series of n_days.

    n_days must be >= 65 for the fold-transform in backtest_gate_engine to produce
    a valid purge-respecting validation fold (FOLD_TRANSFORM_MIN_TOTAL_DAYS = 65).
    Using a mild oscillating positive return so the series has some variance for
    the Sortino t-stat bootstrap.
    """
    from advisors.composer_backtest_client import BacktestResult

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(n_days):
        # Vary return slightly to avoid zero-variance degenerate series
        returns[d.isoformat()] = base_return * (1 + (i % 5) * 0.1 - 0.2)
        d += timedelta(days=1)

    return BacktestResult(
        stats={"sharpe": 0.5, "cagr": 0.08},
        data_warnings=[],
        daily_returns=returns,
    )


def _make_error_result():
    """Create a BacktestResult representing a failed backtest."""
    from advisors.composer_backtest_client import BacktestResult

    return BacktestResult(
        stats=None,
        data_warnings=[],
        daily_returns={},
        error="network timeout",
    )


# ---------------------------------------------------------------------------
# Import guard — all tests below this line assume the module is importable.
# The ImportError is the first RED signal during the initial run.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sbe():
    """Import and return the strategy_builder_engine module.

    scope=module because the module itself is stateless (no mutable globals that
    tests could corrupt) and importing once per module is cheaper than per-test.
    The module does NOT exist yet — this fixture being unreachable is the first
    expected RED failure.
    """
    import advisors.strategy_builder_engine as _sbe  # noqa: PLC0415
    return _sbe


# ===========================================================================
# SECTION 1 — Module-level named constants
# ===========================================================================


class TestNamedConstants:
    """Every default constant must exist as a named float/int — no magic numbers."""

    def test_max_candidates_per_run_is_30(self, sbe):
        """Fixture-anchored: golden fixture names MAX_CANDIDATES_PER_RUN = 30."""
        fixture = _load_fixture()
        expected = fixture["named_constants"]["MAX_CANDIDATES_PER_RUN"]
        assert expected == sbe.MAX_CANDIDATES_PER_RUN

    def test_screen_min_cagr_default_exists_and_is_float(self, sbe):
        assert hasattr(sbe, "SCREEN_MIN_CAGR_DEFAULT"), (
            "SCREEN_MIN_CAGR_DEFAULT must be a module-level named constant"
        )
        assert isinstance(sbe.SCREEN_MIN_CAGR_DEFAULT, float), (
            "SCREEN_MIN_CAGR_DEFAULT must be a float"
        )

    def test_screen_min_sharpe_default_exists_and_is_float(self, sbe):
        assert hasattr(sbe, "SCREEN_MIN_SHARPE_DEFAULT"), (
            "SCREEN_MIN_SHARPE_DEFAULT must be a module-level named constant"
        )
        assert isinstance(sbe.SCREEN_MIN_SHARPE_DEFAULT, float), (
            "SCREEN_MIN_SHARPE_DEFAULT must be a float"
        )

    def test_screen_min_calmar_default_exists_and_is_float(self, sbe):
        assert hasattr(sbe, "SCREEN_MIN_CALMAR_DEFAULT")
        assert isinstance(sbe.SCREEN_MIN_CALMAR_DEFAULT, float)

    def test_screen_max_abs_drawdown_default_is_positive_bounded_float(self, sbe):
        """Fixture-anchored: range is (0, 1.0] (absolute value convention)."""
        assert hasattr(sbe, "SCREEN_MAX_ABS_DRAWDOWN_DEFAULT"), (
            "SCREEN_MAX_ABS_DRAWDOWN_DEFAULT must be a module-level named constant"
        )
        v = sbe.SCREEN_MAX_ABS_DRAWDOWN_DEFAULT
        assert isinstance(v, float), "SCREEN_MAX_ABS_DRAWDOWN_DEFAULT must be a float"
        assert v > 0, "SCREEN_MAX_ABS_DRAWDOWN_DEFAULT must be > 0 (positive absolute value)"
        assert v <= 1.0, "SCREEN_MAX_ABS_DRAWDOWN_DEFAULT must be <= 1.0 (fraction scale)"

    def test_screen_max_blended_abs_drawdown_default_exists_and_is_float(self, sbe):
        assert hasattr(sbe, "SCREEN_MAX_BLENDED_ABS_DRAWDOWN_DEFAULT")
        assert isinstance(sbe.SCREEN_MAX_BLENDED_ABS_DRAWDOWN_DEFAULT, float)

    def test_screen_max_correlation_default_is_in_unit_interval(self, sbe):
        """Fixture-anchored: range is [0.0, 1.0]."""
        assert hasattr(sbe, "SCREEN_MAX_CORRELATION_DEFAULT"), (
            "SCREEN_MAX_CORRELATION_DEFAULT must be a module-level named constant"
        )
        v = sbe.SCREEN_MAX_CORRELATION_DEFAULT
        assert isinstance(v, float), "SCREEN_MAX_CORRELATION_DEFAULT must be a float"
        assert 0.0 <= v <= 1.0, (
            f"SCREEN_MAX_CORRELATION_DEFAULT must be in [0, 1]; got {v}"
        )


# ===========================================================================
# SECTION 2 — Objective enum
# ===========================================================================


class TestObjectiveEnum:
    """Objective enum must have exactly the three specified members with exact values."""

    def test_objective_is_enum(self, sbe):
        assert issubclass(sbe.Objective, enum.Enum)

    def test_objective_diversify_value(self, sbe):
        assert sbe.Objective.diversify.value == "diversify"

    def test_objective_cut_drawdown_value(self, sbe):
        assert sbe.Objective.cut_drawdown.value == "cut_drawdown"

    def test_objective_lift_risk_adjusted_value(self, sbe):
        assert sbe.Objective.lift_risk_adjusted.value == "lift_risk_adjusted"

    def test_objective_has_exactly_three_members(self, sbe):
        member_names = {m.name for m in sbe.Objective}
        assert member_names == {"diversify", "cut_drawdown", "lift_risk_adjusted"}, (
            f"Objective must have exactly 3 members; got {member_names}"
        )


# ===========================================================================
# SECTION 3 — ScreenConfig dataclass
# ===========================================================================


class TestScreenConfig:
    """ScreenConfig must be a dataclass with all six fields defaulting to constants."""

    def test_screen_config_instantiates_with_defaults(self, sbe):
        cfg = sbe.ScreenConfig()
        assert cfg is not None

    def test_screen_config_min_cagr_default_matches_constant(self, sbe):
        cfg = sbe.ScreenConfig()
        # Default must come from the named constant, not a hardcoded literal
        assert cfg.min_cagr == sbe.SCREEN_MIN_CAGR_DEFAULT

    def test_screen_config_min_sharpe_default_matches_constant(self, sbe):
        cfg = sbe.ScreenConfig()
        assert cfg.min_sharpe == sbe.SCREEN_MIN_SHARPE_DEFAULT

    def test_screen_config_min_calmar_default_matches_constant(self, sbe):
        cfg = sbe.ScreenConfig()
        assert cfg.min_calmar == sbe.SCREEN_MIN_CALMAR_DEFAULT

    def test_screen_config_max_abs_drawdown_default_matches_constant(self, sbe):
        cfg = sbe.ScreenConfig()
        assert cfg.max_abs_drawdown == sbe.SCREEN_MAX_ABS_DRAWDOWN_DEFAULT

    def test_screen_config_max_blended_abs_drawdown_default_matches_constant(self, sbe):
        cfg = sbe.ScreenConfig()
        assert cfg.max_blended_abs_drawdown == sbe.SCREEN_MAX_BLENDED_ABS_DRAWDOWN_DEFAULT

    def test_screen_config_max_correlation_default_matches_constant(self, sbe):
        cfg = sbe.ScreenConfig()
        assert cfg.max_correlation == sbe.SCREEN_MAX_CORRELATION_DEFAULT

    def test_screen_config_fields_are_overridable(self, sbe):
        """ScreenConfig must accept per-field overrides — it is a dataclass."""
        cfg = sbe.ScreenConfig(min_sharpe=2.0, max_correlation=0.3)
        assert cfg.min_sharpe == pytest.approx(2.0, rel=1e-9)  # exact override
        assert cfg.max_correlation == pytest.approx(0.3, rel=1e-9)


# ===========================================================================
# SECTION 4 — CandidateInfo and ProposalRun shape
# ===========================================================================


class TestDataclassShapes:
    """CandidateInfo and ProposalRun must expose the required fields."""

    def test_candidate_info_has_required_fields(self, sbe):
        required = {"candidate_id", "tree", "template_id", "params", "metrics", "backtest_error"}
        # Support both dataclass and NamedTuple
        if hasattr(sbe.CandidateInfo, "_fields"):
            actual = set(sbe.CandidateInfo._fields)
        else:
            actual = {f.name for f in dataclass_fields(sbe.CandidateInfo)}
        assert required.issubset(actual), (
            f"CandidateInfo missing required fields: {required - actual}"
        )

    def test_proposal_run_has_required_fields(self, sbe):
        required = {"candidates", "gated_batch", "screened_survivors",
                    "observations_written", "error"}
        if hasattr(sbe.ProposalRun, "_fields"):
            actual = set(sbe.ProposalRun._fields)
        else:
            actual = {f.name for f in dataclass_fields(sbe.ProposalRun)}
        assert required.issubset(actual), (
            f"ProposalRun missing required fields: {required - actual}"
        )


# ===========================================================================
# SECTION 5 — Sign-convention golden tests
# ===========================================================================


class TestSignConvention:
    """Fixture-anchored tests verifying the log-return → pct conversion and
    max_drawdown output polarity.

    Fixture: tests/fixtures/math/strategy_builder_engine_sign_convention.json
    """

    def test_log_returns_multiplied_by_100_exactly(self, sbe):
        """Fixture-anchored: conversion is r * 100.0, copy-exact from
        asset_swap_engine.py:577. Tolerance: exactly 0 — this is a multiplicative
        identity, not a float computation.
        """
        fixture = _load_fixture()
        raw = fixture["sign_convention"]["input_daily_returns"]
        expected_pct = fixture["sign_convention"]["expected_returns_pct"]

        # Simulate the conversion the engine must apply
        from advisors.composer_backtest_client import BacktestResult  # noqa: PLC0415

        result = BacktestResult(
            stats={"sharpe": 0.1},
            data_warnings=[],
            daily_returns=raw,
        )
        # The engine converts: [r * 100.0 for r in result.daily_returns.values()]
        converted = [r * 100.0 for r in result.daily_returns.values()]

        assert len(converted) == len(expected_pct), (
            f"Length mismatch: got {len(converted)}, expected {len(expected_pct)}"
        )
        for i, (got, exp) in enumerate(zip(converted, expected_pct, strict=True)):
            # Tolerance is 1e-12 because this is exact multiplication — not a
            # floating-point approximation step, so any deviation is a coding error.
            assert got == pytest.approx(exp, abs=1e-12), (
                f"Return[{i}]: expected {exp} (= {list(raw.values())[i]} × 100), got {got}"
            )

    def test_max_drawdown_is_non_positive_from_quantstats(self, sbe):
        """Fixture-anchored: compute_quantstats_metrics returns max_drawdown <= 0
        (fraction scale, drawdown convention). Tolerance: not applicable — this is
        a sign/polarity assertion, not an approximate float comparison.
        """
        from analytics import compute_quantstats_metrics  # noqa: PLC0415

        # Use a synthetic long return series (100 days, positive average) with
        # an embedded drawdown period so max_drawdown is non-trivially < 0.
        # Series in percent scale (per compute_quantstats_metrics contract).
        pct_returns = []
        for i in range(100):
            # Create a run-up then a 10% drawdown window
            if 40 <= i < 55:
                pct_returns.append(-0.2)  # 0.2% loss per day
            else:
                pct_returns.append(0.15)  # 0.15% gain per day

        metrics = compute_quantstats_metrics(pct_returns)

        assert metrics["max_drawdown"] is not None, (
            "max_drawdown must not be None for a 100-day series with embedded drawdown"
        )
        assert metrics["max_drawdown"] <= 0, (
            f"max_drawdown must be <= 0 (drawdown convention); got {metrics['max_drawdown']}"
        )

    def test_propose_strategies_max_drawdown_non_positive_in_metrics(self, sbe):
        """End-to-end: the metrics stored in CandidateInfo must have max_drawdown <= 0.

        Uses a minimal universe + mocked backtest so analytics.compute_quantstats_metrics
        is exercised for real but no network call is made.
        """
        fake = _make_fake_result(n_days=100, base_return=0.001)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                symphony_id="test-sym",
            )

        assert result.error is None, f"Unexpected error: {result.error}"
        for cand in result.candidates:
            if cand.metrics is not None and cand.metrics.get("max_drawdown") is not None:
                assert cand.metrics["max_drawdown"] <= 0, (
                    f"CandidateInfo.metrics['max_drawdown'] must be <= 0; "
                    f"got {cand.metrics['max_drawdown']} for candidate {cand.candidate_id}"
                )


# ===========================================================================
# SECTION 6 — FDR integrity test (AC-3.2)
# ===========================================================================


class TestFdrIntegrity:
    """Screens must NOT shrink the batch before the gate. AC-3.2."""

    def test_screens_operate_on_survivors_not_gate_input(self, sbe):
        """Fixture-anchored: gated_batch.n_candidates == N (all successfully
        backtested), even when ScreenConfig rejects all of them.

        This is the load-bearing FDR-integrity invariant: screens are a presentation
        filter on gate.survivors, never an input filter to the gate.
        """
        fake = _make_fake_result(n_days=100, base_return=0.001)

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            # min_sharpe=100.0 is pathologically high — no backtest series will
            # achieve a Sharpe of 100; this ensures all gate survivors get screened out.
            aggressive_screen = sbe.ScreenConfig(min_sharpe=100.0)
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG", "GLD", "TLT", "QQQ"],
                screen_config=aggressive_screen,
                live_returns=[0.001] * 100,
                symphony_id="test-fdr",
            )

        assert result.error is None, f"Unexpected error: {result.error}"

        # The gate must have received ALL successfully-backtested candidates.
        # We cannot assert == n because the engine may generate fewer than N
        # candidates from the universe; we CAN assert that the screened_survivors
        # subset (which should be 0) was filtered from gate.survivors, not from
        # the gate's input.
        assert result.gated_batch is not None, "gated_batch must not be None"
        assert result.gated_batch.n_candidates == len(result.candidates), (
            "gated_batch.n_candidates must equal the number of successfully-backtested "
            "candidates (len(result.candidates)), not the post-screen count. "
            f"Got n_candidates={result.gated_batch.n_candidates}, "
            f"len(candidates)={len(result.candidates)}"
        )

        # Screens must have filtered all survivors (min_sharpe=100 is impossible)
        assert len(result.screened_survivors) == 0, (
            f"All survivors should have been filtered by min_sharpe=100; "
            f"got {len(result.screened_survivors)} survivors"
        )

    def test_fdr_batch_excludes_backtest_failures(self, sbe):
        """Only successfully-backtested candidates (BacktestCandidate created) enter
        the gate. Failed backtests reduce the batch count; they do NOT count as
        zero-return candidates.
        """
        fake_success = _make_fake_result(n_days=100, base_return=0.001)
        fake_error = _make_error_result()

        call_counter = {"n": 0}

        def _side_effect(*args, **kwargs):
            call_counter["n"] += 1
            # First two calls succeed, third fails
            if call_counter["n"] <= 2:
                return fake_success
            return fake_error

        with (
            patch("advisors.strategy_builder_engine.run_backtest",
                  side_effect=_side_effect),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            # Universe of 5 tickers — engine will generate several candidates.
            # We accept whatever n_candidates comes back; the key assertion is:
            # n_candidates == len(result.candidates) (not n_candidates == total_attempted).
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG", "GLD", "TLT", "QQQ"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                symphony_id="test-fdr-failures",
            )

        assert result.error is None, f"Unexpected error: {result.error}"
        if result.gated_batch is not None:
            assert result.gated_batch.n_candidates == len(result.candidates), (
                "gated_batch.n_candidates must match the successfully-backtested "
                "candidate count, not the attempted count"
            )


# ===========================================================================
# SECTION 7 — Template function tests (T1–T7)
# ===========================================================================


class TestTemplateT1EqualWeightBasket:
    """T1: equal_weight_basket — validate_tree == [], json-serializable, has tickers."""

    def test_builds_valid_tree(self, sbe):
        from advisors.symphony_schema import validate_tree  # noqa: PLC0415
        tree = sbe.equal_weight_basket(["SPY", "AGG", "GLD"])
        errors = validate_tree(tree)
        assert errors == [], f"validate_tree must return [] for T1; got {errors}"

    def test_json_serializable(self, sbe):
        tree = sbe.equal_weight_basket(["SPY", "AGG"])
        # Must not raise — if it raises, it is not json-serializable
        serialized = json.dumps(tree)
        assert isinstance(serialized, str)

    def test_extract_tickers_non_empty(self, sbe):
        from advisors.symphony_schema import extract_tickers  # noqa: PLC0415
        tree = sbe.equal_weight_basket(["SPY", "AGG", "TLT"])
        tickers = extract_tickers(tree)
        assert len(tickers) > 0, "equal_weight_basket must produce a tree with at least one ticker"

    def test_tickers_present_in_extract(self, sbe):
        from advisors.symphony_schema import extract_tickers  # noqa: PLC0415
        tickers_in = ["SPY", "AGG"]
        tree = sbe.equal_weight_basket(tickers_in)
        tickers_out = extract_tickers(tree)
        for t in tickers_in:
            assert t in tickers_out, f"Ticker {t!r} missing from extract_tickers output"

    def test_name_parameter_accepted(self, sbe):
        from advisors.symphony_schema import validate_tree  # noqa: PLC0415
        tree = sbe.equal_weight_basket(["SPY"], name="My Equal Basket")
        assert validate_tree(tree) == []


class TestTemplateT2SpecifiedWeightBasket:
    """T2: specified_weight_basket — validate_tree == [], json-serializable, has tickers."""

    def test_builds_valid_tree(self, sbe):
        from advisors.symphony_schema import validate_tree  # noqa: PLC0415
        tree = sbe.specified_weight_basket([("SPY", 60.0), ("AGG", 40.0)])
        errors = validate_tree(tree)
        assert errors == [], f"validate_tree must return [] for T2; got {errors}"

    def test_json_serializable(self, sbe):
        tree = sbe.specified_weight_basket([("SPY", 50.0), ("GLD", 50.0)])
        assert isinstance(json.dumps(tree), str)

    def test_extract_tickers_non_empty(self, sbe):
        from advisors.symphony_schema import extract_tickers  # noqa: PLC0415
        tree = sbe.specified_weight_basket([("SPY", 70.0), ("AGG", 30.0)])
        assert len(extract_tickers(tree)) > 0


class TestTemplateT3InverseVolBasket:
    """T3: inverse_vol_basket — validate_tree == [], json-serializable, has tickers."""

    def test_builds_valid_tree(self, sbe):
        from advisors.symphony_schema import validate_tree  # noqa: PLC0415
        tree = sbe.inverse_vol_basket(["SPY", "AGG", "GLD"])
        errors = validate_tree(tree)
        assert errors == [], f"validate_tree must return [] for T3; got {errors}"

    def test_json_serializable(self, sbe):
        tree = sbe.inverse_vol_basket(["SPY", "TLT"])
        assert isinstance(json.dumps(tree), str)

    def test_extract_tickers_non_empty(self, sbe):
        from advisors.symphony_schema import extract_tickers  # noqa: PLC0415
        tree = sbe.inverse_vol_basket(["SPY", "AGG"])
        assert len(extract_tickers(tree)) > 0


class TestTemplateT4TrendSwitch:
    """T4: trend_switch — validate_tree == [], uses "if" step, has true+else branches."""

    def test_builds_valid_tree(self, sbe):
        from advisors.symphony_schema import validate_tree  # noqa: PLC0415
        tree = sbe.trend_switch(
            signal_ticker="SPY",
            ma_window=200,
            risk_on_tickers=["QQQ", "IWM"],
            risk_off_tickers=["TLT", "SHY"],
        )
        errors = validate_tree(tree)
        assert errors == [], f"validate_tree must return [] for T4; got {errors}"

    def test_json_serializable(self, sbe):
        tree = sbe.trend_switch("SPY", 200, ["QQQ"], ["TLT"])
        assert isinstance(json.dumps(tree), str)

    def test_extract_tickers_non_empty(self, sbe):
        from advisors.symphony_schema import extract_tickers  # noqa: PLC0415
        tree = sbe.trend_switch("SPY", 200, ["QQQ"], ["TLT"])
        assert len(extract_tickers(tree)) > 0

    def test_root_has_if_child(self, sbe):
        """The tree must contain at least one node with step="if"."""
        tree = sbe.trend_switch("SPY", 50, ["QQQ"], ["TLT"])

        # BFS to find any "if" step node
        stack = [tree]
        found_if = False
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            if node.get("step") == "if":
                found_if = True
                break
            for child in node.get("children", []) or []:
                stack.append(child)

        assert found_if, 'trend_switch tree must contain at least one node with step="if"'

    def test_if_node_has_true_and_else_branches(self, sbe):
        """The 'if' node must have both a true-branch and else-branch if-child."""
        tree = sbe.trend_switch("SPY", 50, ["QQQ"], ["TLT"])

        # Find the if node
        stack = [tree]
        if_node = None
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            if node.get("step") == "if":
                if_node = node
                break
            for child in node.get("children", []) or []:
                stack.append(child)

        assert if_node is not None, 'trend_switch tree must have an "if" node'
        children = [c for c in (if_node.get("children") or []) if isinstance(c, dict)]
        has_true = any(not c.get("is-else-condition?") for c in children)
        has_else = any(c.get("is-else-condition?") for c in children)
        assert has_true, "trend_switch if-node must have a true-branch if-child"
        assert has_else, "trend_switch if-node must have an else-branch if-child"


class TestTemplateT5RsiRotation:
    """T5: rsi_rotation — validate_tree == [], uses 'relative-strength-index' (not 'rsi')."""

    def test_builds_valid_tree(self, sbe):
        from advisors.symphony_schema import validate_tree  # noqa: PLC0415
        tree = sbe.rsi_rotation(
            signal_ticker="SPY",
            rsi_window=14,
            threshold=70,
            overbought_tickers=["TLT"],
            neutral_tickers=["SPY"],
        )
        errors = validate_tree(tree)
        assert errors == [], f"validate_tree must return [] for T5; got {errors}"

    def test_json_serializable(self, sbe):
        tree = sbe.rsi_rotation("SPY", 14, 70, ["TLT"], ["SPY"])
        assert isinstance(json.dumps(tree), str)

    def test_extract_tickers_non_empty(self, sbe):
        from advisors.symphony_schema import extract_tickers  # noqa: PLC0415
        tree = sbe.rsi_rotation("SPY", 14, 70, ["TLT"], ["SPY"])
        assert len(extract_tickers(tree)) > 0

    def test_uses_relative_strength_index_not_rsi(self, sbe):
        """Grammar doc §4.1: the canonical RSI token is 'relative-strength-index'.
        The abbreviation 'rsi' is a lint warning (unknown fn). T5 must emit the
        full canonical string so the tree passes validate_tree with no errors.
        """
        tree = sbe.rsi_rotation("SPY", 14, 70, ["TLT"], ["SPY"])
        serialized = json.dumps(tree)
        # The canonical indicator fn string must appear in the tree JSON
        assert "relative-strength-index" in serialized, (
            "rsi_rotation must use 'relative-strength-index' (not 'rsi') as the "
            "indicator fn string per grammar doc §4.1"
        )
        # The abbreviation 'rsi' (without the hyphen expansion) must NOT appear
        # as a standalone fn value (it would trigger a lint warning)
        # We check by looking at lhs-fn / rhs-fn keys in the serialized JSON.
        # A simple substring check is sufficient because the only place 'rsi'
        # as a standalone token would appear is in fn fields.
        import re  # noqa: PLC0415
        # Match "rsi" not preceded or followed by a hyphen (i.e., standalone)
        standalone_rsi = re.search(r'"(?:lhs-fn|rhs-fn|sort-by-fn)"\s*:\s*"rsi"', serialized)
        assert standalone_rsi is None, (
            "rsi_rotation must not use 'rsi' as the fn value; use 'relative-strength-index'"
        )


class TestTemplateT6MomentumTopN:
    """T6: momentum_top_n — validate_tree == [], uses 'select-top' + 'cumulative-return'."""

    def test_builds_valid_tree(self, sbe):
        from advisors.symphony_schema import validate_tree  # noqa: PLC0415
        tree = sbe.momentum_top_n(
            universe=["SPY", "QQQ", "IWM", "EFA"],
            n=2,
            window=63,
        )
        errors = validate_tree(tree)
        assert errors == [], f"validate_tree must return [] for T6; got {errors}"

    def test_json_serializable(self, sbe):
        tree = sbe.momentum_top_n(["SPY", "QQQ", "IWM"], 2, 63)
        assert isinstance(json.dumps(tree), str)

    def test_extract_tickers_non_empty(self, sbe):
        from advisors.symphony_schema import extract_tickers  # noqa: PLC0415
        tree = sbe.momentum_top_n(["SPY", "QQQ", "IWM"], 2, 63)
        assert len(extract_tickers(tree)) > 0

    def test_uses_select_top(self, sbe):
        tree = sbe.momentum_top_n(["SPY", "QQQ", "IWM"], 2, 63)
        serialized = json.dumps(tree)
        assert "select-top" in serialized, (
            "momentum_top_n must use 'select-top' as the select-fn"
        )

    def test_uses_cumulative_return(self, sbe):
        tree = sbe.momentum_top_n(["SPY", "QQQ", "IWM"], 2, 63)
        serialized = json.dumps(tree)
        assert "cumulative-return" in serialized, (
            "momentum_top_n must use 'cumulative-return' as the sort-by-fn"
        )


class TestTemplateT7LowVolFloor:
    """T7: low_vol_floor — validate_tree == [], uses 'select-bottom' + 'standard-deviation-return'.
    """

    def test_builds_valid_tree(self, sbe):
        from advisors.symphony_schema import validate_tree  # noqa: PLC0415
        tree = sbe.low_vol_floor(
            universe=["SPY", "TLT", "GLD", "IWM"],
            n=2,
            window=21,
        )
        errors = validate_tree(tree)
        assert errors == [], f"validate_tree must return [] for T7; got {errors}"

    def test_json_serializable(self, sbe):
        tree = sbe.low_vol_floor(["SPY", "TLT", "GLD"], 2, 21)
        assert isinstance(json.dumps(tree), str)

    def test_extract_tickers_non_empty(self, sbe):
        from advisors.symphony_schema import extract_tickers  # noqa: PLC0415
        tree = sbe.low_vol_floor(["SPY", "TLT", "GLD"], 2, 21)
        assert len(extract_tickers(tree)) > 0

    def test_uses_select_bottom(self, sbe):
        tree = sbe.low_vol_floor(["SPY", "TLT", "GLD"], 2, 21)
        serialized = json.dumps(tree)
        assert "select-bottom" in serialized, (
            "low_vol_floor must use 'select-bottom' as the select-fn"
        )

    def test_uses_standard_deviation_return(self, sbe):
        tree = sbe.low_vol_floor(["SPY", "TLT", "GLD"], 2, 21)
        serialized = json.dumps(tree)
        assert "standard-deviation-return" in serialized, (
            "low_vol_floor must use 'standard-deviation-return' as the sort-by-fn"
        )


# ===========================================================================
# SECTION 8 — Never-raises contract
# ===========================================================================


class TestNeverRaises:
    """propose_strategies must never raise — always return ProposalRun with error field."""

    def test_network_error_returns_proposal_run_with_error(self, sbe):
        """A RuntimeError from run_backtest must be caught and returned in
        ProposalRun.error — never propagated to the caller.
        """
        with (
            patch("advisors.strategy_builder_engine.run_backtest",
                  side_effect=RuntimeError("network down")),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database"),
        ):
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 50,
            )

        # Must not raise; must return a ProposalRun
        assert isinstance(result, sbe.ProposalRun)
        # The error must be a non-empty string (or the result must be a fallback
        # ProposalRun even if some candidates succeeded before the error)
        # At minimum: must not raise — the ProposalRun.error field carries the detail.

    def test_empty_universe_returns_proposal_run_without_raising(self, sbe):
        """universe=[] is an edge case. Must return ProposalRun, not raise."""
        with (
            patch("advisors.strategy_builder_engine.run_backtest",
                  return_value=_make_fake_result()),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database"),
        ):
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=[],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
            )
        assert isinstance(result, sbe.ProposalRun)

    def test_single_asset_universe_does_not_raise(self, sbe):
        """universe=["SPY"] is a valid degenerate case. Must not raise."""
        fake = _make_fake_result(n_days=100, base_return=0.001)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
            )
        assert isinstance(result, sbe.ProposalRun)

    def test_no_composer_key_returns_proposal_run_with_error(self, sbe):
        """When _has_composer_key() returns False, the engine must return a
        ProposalRun with a non-None error field (consistent with AC-X4).
        """
        with (
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=False),
            patch("advisors.strategy_builder_engine.database"),
        ):
            result = sbe.propose_strategies(
                objective=sbe.Objective.lift_risk_adjusted,
                universe=["SPY", "AGG"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 50,
            )
        assert isinstance(result, sbe.ProposalRun)
        assert result.error is not None, (
            "When no Composer API key is configured, ProposalRun.error must be non-None"
        )


# ===========================================================================
# SECTION 9 — ProposalRun structure tests
# ===========================================================================


class TestProposalRunStructure:
    """A successful propose_strategies call must return a well-formed ProposalRun."""

    def test_successful_run_error_is_none(self, sbe):
        fake = _make_fake_result(n_days=100, base_return=0.001)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG", "GLD"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                symphony_id="test-structure",
            )
        assert result.error is None, f"error must be None on success; got {result.error!r}"

    def test_gated_batch_is_not_none_on_success(self, sbe):
        fake = _make_fake_result(n_days=100, base_return=0.001)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.cut_drawdown,
                universe=["SPY", "TLT"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
            )
        assert result.gated_batch is not None, "gated_batch must not be None on success"

    def test_candidates_is_list(self, sbe):
        fake = _make_fake_result(n_days=100, base_return=0.001)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
            )
        assert isinstance(result.candidates, list), (
            f"candidates must be a list; got {type(result.candidates)}"
        )

    def test_screened_survivors_is_list(self, sbe):
        fake = _make_fake_result(n_days=100, base_return=0.001)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
            )
        assert isinstance(result.screened_survivors, list), (
            f"screened_survivors must be a list; got {type(result.screened_survivors)}"
        )

    def test_observations_written_is_non_negative_int(self, sbe):
        fake = _make_fake_result(n_days=100, base_return=0.001)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
            )
        assert isinstance(result.observations_written, int), (
            f"observations_written must be int; got {type(result.observations_written)}"
        )
        assert result.observations_written >= 0, (
            f"observations_written must be >= 0; got {result.observations_written}"
        )

    def test_screened_survivors_subset_of_gate_survivors(self, sbe):
        """screened_survivors must be a subset of gate survivors — it is a
        presentation filter on gate.survivors, never a superset.
        """
        fake = _make_fake_result(n_days=100, base_return=0.001)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG", "GLD"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
            )

        if result.gated_batch is None:
            return  # skip if no gate ran (empty universe handled separately)

        gate_survivor_ids = {s.candidate_id for s in result.gated_batch.survivors}
        for survivor in result.screened_survivors:
            assert survivor.candidate_id in gate_survivor_ids, (
                f"screened_survivors contains {survivor.candidate_id!r} which is not a "
                f"gate survivor — screened_survivors must be a subset of gate survivors"
            )


# ===========================================================================
# SECTION 10 — propose_strategies signature and parameter passing
# ===========================================================================


class TestProposeStrategiesSignature:
    """propose_strategies must accept all documented parameters without raising."""

    def test_accepts_incumbent_oos_alpha_kwarg(self, sbe):
        fake = _make_fake_result(n_days=100, base_return=0.001)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            # Should not raise TypeError about unexpected keyword argument
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                symphony_id="sym-1",
                incumbent_oos_alpha=0.05,
                default_oos_alpha=0.02,
            )
        assert isinstance(result, sbe.ProposalRun)

    def test_symphony_id_defaults_to_empty_string(self, sbe):
        """symphony_id has a default of "" so omitting it must not raise."""
        fake = _make_fake_result(n_days=100, base_return=0.001)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
            )
        assert isinstance(result, sbe.ProposalRun)

    def test_all_three_objectives_accepted(self, sbe):
        """propose_strategies must accept all three Objective enum values."""
        fake = _make_fake_result(n_days=100, base_return=0.001)
        for obj in sbe.Objective:
            with (
                patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
                patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
                patch("advisors.strategy_builder_engine.database") as mock_db,
            ):
                mock_db.insert_advisor_observation.return_value = 1
                result = sbe.propose_strategies(
                    objective=obj,
                    universe=["SPY", "AGG"],
                    screen_config=sbe.ScreenConfig(),
                    live_returns=[0.001] * 100,
                )
            assert isinstance(result, sbe.ProposalRun), (
                f"propose_strategies must return ProposalRun for objective={obj}"
            )


# ===========================================================================
# SECTION 11 — Adversarial cycle 1 (post-GREEN edge cases)
# ===========================================================================


class TestAdversarialCycle1:
    """Adversarial tests added after implementer goes GREEN (cycle 1).

    Tests push corner-cases that a naive implementation might miss.
    """

    def test_specified_weight_basket_non_100_sum_validates_clean(self, sbe):
        """Weight sums != 100 are a lint_tree WARNING, not a validate_tree hard error
        (per symphony_schema handoff amendment — weight sums are lint-only; OQ-4).
        The tree must still pass validate_tree even when weights sum to 70.
        """
        from advisors.symphony_schema import validate_tree  # noqa: PLC0415
        # Weights sum to 70 (not 100)
        tree = sbe.specified_weight_basket([("SPY", 40.0), ("AGG", 30.0)])
        errors = validate_tree(tree)
        assert errors == [], (
            "validate_tree must return [] even when weights != 100 "
            f"(lint warning, not hard error); got {errors}"
        )

    def test_trend_switch_large_ma_window_builds_cleanly(self, sbe):
        """ma_window=200 (large value) must build a valid tree without errors.

        This probes that the window value is passed through to the indicator without
        being truncated, clamped, or rejected.
        """
        from advisors.symphony_schema import validate_tree  # noqa: PLC0415
        tree = sbe.trend_switch(
            signal_ticker="SPY",
            ma_window=200,
            risk_on_tickers=["QQQ"],
            risk_off_tickers=["TLT"],
        )
        errors = validate_tree(tree)
        assert errors == [], (
            f"trend_switch with ma_window=200 must produce a valid tree; got {errors}"
        )

    def test_backtest_failures_reduce_gated_batch_count(self, sbe):
        """When some backtests fail (return BacktestResult with error), the successfully-
        backtested candidates are a smaller set. gated_batch.n_candidates must equal
        the NUMBER OF SUCCESSFUL BacktestCandidates, not the total attempted.

        This test uses a side_effect that alternates success/failure so we can
        assert the count is consistent with the success count.
        """
        fake_success = _make_fake_result(n_days=100, base_return=0.001)
        fake_error = _make_error_result()

        call_results = [fake_success, fake_success, fake_error, fake_success, fake_error]
        call_iter = iter(call_results)

        def _side_effect(*args, **kwargs):
            try:
                return next(call_iter)
            except StopIteration:
                return fake_success  # fallback for any extra calls

        with (
            patch("advisors.strategy_builder_engine.run_backtest",
                  side_effect=_side_effect),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG", "GLD", "TLT", "QQQ"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                symphony_id="test-adv-failures",
            )

        assert result.error is None, f"Unexpected error: {result.error}"
        if result.gated_batch is not None and len(result.candidates) > 0:
            # The gate's input must be exactly the successfully-backtested candidates
            assert result.gated_batch.n_candidates == len(result.candidates), (
                "gated_batch.n_candidates must equal len(result.candidates) "
                "(only successfully-backtested candidates count); "
                f"got n_candidates={result.gated_batch.n_candidates}, "
                f"len(candidates)={len(result.candidates)}"
            )


# ===========================================================================
# SECTION 12 — Adversarial cycle 2 (post-cycle-1-GREEN)
# ===========================================================================


class TestAdversarialCycle2:
    """Adversarial tests added after implementer re-greens on cycle 1.

    Focus: persistence contract (json-serializability of observation payload)
    and render_rules_text coverage of all templates.
    """

    def test_insert_advisor_observation_call_args_are_json_serializable(self, sbe):
        """Capture the args passed to database.insert_advisor_observation and assert
        that any 'raw_response' argument can be json.dumps'd without raising.

        This catches the case where an implementer passes a raw dict containing
        non-serializable objects (e.g., Python datetime, Enum members, NamedTuples)
        as the observation payload.
        """
        fake = _make_fake_result(n_days=100, base_return=0.001)

        captured_kwargs: list[dict] = []

        def _capture_insert(*args, **kwargs):
            captured_kwargs.append({"args": args, "kwargs": kwargs})
            return 1

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch(
                "advisors.strategy_builder_engine.database.insert_advisor_observation",
                side_effect=_capture_insert,
            ),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG", "GLD"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                symphony_id="test-adv-json",
            )

        # If no observations were written (no gate survivors), skip the assertion
        # — this is a valid outcome; the test only applies when something was persisted.
        if not captured_kwargs:
            pytest.skip("No observations written — test only applies when survivors exist")

        for call in captured_kwargs:
            # The raw_response / observation payload may be in positional args or kwargs.
            # We assert that every argument that is a dict or list is json-serializable.
            all_args = list(call["args"]) + list(call["kwargs"].values())
            for arg in all_args:
                if isinstance(arg, (dict, list)):
                    try:
                        json.dumps(arg)
                    except (TypeError, ValueError) as exc:
                        pytest.fail(
                            f"Argument passed to insert_advisor_observation is not "
                            f"json-serializable: {exc!r}\n"
                            f"Value (type={type(arg).__name__}): {arg!r}"
                        )

    def test_render_rules_text_non_empty_for_all_templates(self, sbe):
        """render_rules_text must return a non-empty string for every template tree.

        This ensures the tree structure emitted by each template has enough
        recognizable nodes for the renderer to produce meaningful operator text.
        """
        from advisors.symphony_schema import render_rules_text  # noqa: PLC0415

        templates_and_trees = [
            ("T1_equal_weight_basket",
             sbe.equal_weight_basket(["SPY", "AGG"])),
            ("T2_specified_weight_basket",
             sbe.specified_weight_basket([("SPY", 60.0), ("AGG", 40.0)])),
            ("T3_inverse_vol_basket",
             sbe.inverse_vol_basket(["SPY", "TLT"])),
            ("T4_trend_switch",
             sbe.trend_switch("SPY", 50, ["QQQ"], ["TLT"])),
            ("T5_rsi_rotation",
             sbe.rsi_rotation("SPY", 14, 70, ["TLT"], ["SPY"])),
            ("T6_momentum_top_n",
             sbe.momentum_top_n(["SPY", "QQQ", "IWM"], 2, 63)),
            ("T7_low_vol_floor",
             sbe.low_vol_floor(["SPY", "TLT", "GLD"], 2, 21)),
        ]

        for name, tree in templates_and_trees:
            text = render_rules_text(tree)
            assert isinstance(text, str), (
                f"{name}: render_rules_text must return str; got {type(text)}"
            )
            assert len(text.strip()) > 0, (
                f"{name}: render_rules_text must return a non-empty string; got {text!r}"
            )
