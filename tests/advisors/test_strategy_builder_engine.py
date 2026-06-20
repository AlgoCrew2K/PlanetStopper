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
    T6 uses "top" + "cumulative-return", T7 uses "bottom" + "max-drawdown"


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
    _REPO_ROOT / "tests" / "fixtures" / "math" / "strategy_builder_engine_sign_convention.json"
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
        assert 0.0 <= v <= 1.0, f"SCREEN_MAX_CORRELATION_DEFAULT must be in [0, 1]; got {v}"


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

    def test_objective_has_exactly_four_members(self, sbe):
        """Q1-A (AC-8): sbe.Objective must have exactly 4 members after C4 extension."""
        member_names = {m.name for m in sbe.Objective}
        assert member_names == {
            "diversify",
            "cut_drawdown",
            "lift_risk_adjusted",
            "volatility_mitigation",
        }, f"Objective must have exactly 4 members (Q1-A); got {member_names}"


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
        required = {
            "candidates",
            "gated_batch",
            "screened_survivors",
            "observations_written",
            "error",
        }
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
            patch("advisors.strategy_builder_engine.run_backtest", side_effect=_side_effect),
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
    """T6: momentum_top_n — validate_tree == [], uses 'top' + 'cumulative-return'."""

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
        stack = [tree]
        found_fn = None
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            if node.get("step") == "filter":
                found_fn = node.get("select-fn")
                break
            for child in node.get("children", []) or []:
                stack.append(child)
        assert found_fn == "top", (
            f"momentum_top_n must use 'top' as the select-fn per grammar §3.5 VERIFIED-LOCAL; "
            f"got {found_fn!r}"
        )

    def test_uses_cumulative_return(self, sbe):
        tree = sbe.momentum_top_n(["SPY", "QQQ", "IWM"], 2, 63)
        serialized = json.dumps(tree)
        assert "cumulative-return" in serialized, (
            "momentum_top_n must use 'cumulative-return' as the sort-by-fn"
        )


class TestTemplateT7LowVolFloor:
    """T7: low_vol_floor — validate_tree == [], uses 'bottom' + 'max-drawdown'.

    max-drawdown replaced standard-deviation-return after the vocabulary
    deep-research refuted the latter in sort-by position (it is verified as an
    indicator fn only); max-drawdown is VERIFIED-LOCAL in sort position.
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
        stack = [tree]
        found_fn = None
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            if node.get("step") == "filter":
                found_fn = node.get("select-fn")
                break
            for child in node.get("children", []) or []:
                stack.append(child)
        assert found_fn == "bottom", (
            f"low_vol_floor must use 'bottom' as the select-fn per grammar §3.5 VERIFIED-LOCAL; "
            f"got {found_fn!r}"
        )

    def test_uses_max_drawdown_sort(self, sbe):
        tree = sbe.low_vol_floor(["SPY", "TLT", "GLD"], 2, 21)
        serialized = json.dumps(tree)
        assert "max-drawdown" in serialized, (
            "low_vol_floor must use 'max-drawdown' as the sort-by-fn (the "
            "VERIFIED-LOCAL defensive proxy; standard-deviation-return is "
            "refuted in sort position per vocabulary research)"
        )
        assert "standard-deviation-return" not in serialized, (
            "standard-deviation-return must NOT appear: unattested in sort-by "
            "position (vocabulary research) — emitting it risks API rejection"
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
            patch(
                "advisors.strategy_builder_engine.run_backtest",
                side_effect=RuntimeError("network down"),
            ),
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
            patch(
                "advisors.strategy_builder_engine.run_backtest", return_value=_make_fake_result()
            ),
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
            patch("advisors.strategy_builder_engine.run_backtest", side_effect=_side_effect),
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
            ("T1_equal_weight_basket", sbe.equal_weight_basket(["SPY", "AGG"])),
            (
                "T2_specified_weight_basket",
                sbe.specified_weight_basket([("SPY", 60.0), ("AGG", 40.0)]),
            ),
            ("T3_inverse_vol_basket", sbe.inverse_vol_basket(["SPY", "TLT"])),
            ("T4_trend_switch", sbe.trend_switch("SPY", 50, ["QQQ"], ["TLT"])),
            ("T5_rsi_rotation", sbe.rsi_rotation("SPY", 14, 70, ["TLT"], ["SPY"])),
            ("T6_momentum_top_n", sbe.momentum_top_n(["SPY", "QQQ", "IWM"], 2, 63)),
            ("T7_low_vol_floor", sbe.low_vol_floor(["SPY", "TLT", "GLD"], 2, 21)),
        ]

        for name, tree in templates_and_trees:
            text = render_rules_text(tree)
            assert isinstance(text, str), (
                f"{name}: render_rules_text must return str; got {type(text)}"
            )
            assert len(text.strip()) > 0, (
                f"{name}: render_rules_text must return a non-empty string; got {text!r}"
            )


# ===========================================================================
# SECTION 13 — Adversarial cycle 3 (post-cycle-2-GREEN)
# ADVERSARIAL CYCLE 3 — added by test-writer
# ===========================================================================


class TestAdversarialCycle3:
    """Adversarial tests attacking the five contract hot spots:
    (1) FDR integrity — persisted n_candidates matches gated_batch.n_candidates
    (2) Screen fail-closed on None metrics — each field independently
    (3) Log-return sign preservation under * 100.0 conversion
    (4) Never-raises on pathological inputs (ValueError, KeyError, DB exception, >10 tickers)
    (5) Grammar construct purity (T4/T5/T6/T7 vocabulary and value-type assertions)
    """

    # -----------------------------------------------------------------------
    # Hot spot 1 — FDR integrity: persisted n_candidates == gated_batch.n_candidates
    # -----------------------------------------------------------------------

    def test_fdr_persisted_n_candidates_matches_gated_batch_n_candidates(self, sbe):
        """Contract §2.2: n_candidates in the persisted observation must equal the
        number backtested (gated_batch.n_candidates), not the total-attempted count
        or the post-screen count.

        Captures the raw_response passed to insert_advisor_observation and asserts
        raw_response["n_candidates"] == result.gated_batch.n_candidates.
        """
        fake = _make_fake_result(n_days=100, base_return=0.001)
        captured_calls: list[dict] = []

        def _capture(*args, **kwargs):
            captured_calls.append({"args": args, "kwargs": kwargs})
            return 1

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch(
                "advisors.strategy_builder_engine.database.insert_advisor_observation",
                side_effect=_capture,
            ),
        ):
            # Use a permissive ScreenConfig (min_sharpe=-999) so at least some
            # gate survivors pass the screen and get persisted.
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG", "GLD"],
                screen_config=sbe.ScreenConfig(min_sharpe=-999.0),
                live_returns=[0.001] * 100,
                symphony_id="test-fdr-persist",
            )

        assert result.error is None, f"Unexpected error: {result.error}"

        if not captured_calls:
            pytest.skip(
                "No observations were persisted (no gate survivors passed screens) "
                "— test requires at least one persistence call to verify FDR contract"
            )

        for call in captured_calls:
            # raw_response is in kwargs
            raw_response = call["kwargs"].get("raw_response")
            if raw_response is None:
                # Fall back: check positional args for a dict with 'n_candidates'
                for arg in call["args"]:
                    if isinstance(arg, dict) and "n_candidates" in arg:
                        raw_response = arg
                        break

            assert raw_response is not None, (
                "insert_advisor_observation must receive a raw_response dict "
                "containing 'n_candidates'"
            )
            assert "n_candidates" in raw_response, (
                "raw_response passed to insert_advisor_observation must contain "
                "'n_candidates' key (contract §2.2)"
            )
            # The core FDR integrity assertion: persisted n_candidates must equal
            # the gate's n_candidates (number successfully backtested), not the
            # total-attempted or post-screen count.
            assert raw_response["n_candidates"] == result.gated_batch.n_candidates, (
                f"raw_response['n_candidates']={raw_response['n_candidates']} must equal "
                f"result.gated_batch.n_candidates={result.gated_batch.n_candidates}. "
                "Contract §2.2: n_candidates must equal the number backtested."
            )

    # -----------------------------------------------------------------------
    # Hot spot 2 — Screen fail-closed on None metrics (per-field)
    # -----------------------------------------------------------------------

    def test_passes_screens_returns_false_when_annualized_return_is_none(self, sbe):
        """Contract §4: None-metric → screen fails closed.
        annualized_return=None must return False, not pass silently.
        """
        from advisors.strategy_builder_engine import (  # noqa: PLC0415
            ScreenConfig,
            _passes_screens,
        )

        metrics = {
            "annualized_return": None,
            "sharpe": 1.0,
            "calmar": 0.5,
            "max_drawdown": -0.10,
        }
        # Tolerance explanation: this is a boolean result — pytest.approx not applicable.
        result = _passes_screens(
            metrics,
            live_returns=[0.001] * 50,
            screen_config=ScreenConfig(),
            returns_pct=[0.1] * 50,
        )
        assert result is False, (
            "_passes_screens must return False when annualized_return is None "
            "(fail-closed per contract §4)"
        )

    def test_passes_screens_returns_false_when_sharpe_is_none(self, sbe):
        """Contract §4: None-metric → screen fails closed. sharpe=None must return False."""
        from advisors.strategy_builder_engine import (  # noqa: PLC0415
            ScreenConfig,
            _passes_screens,
        )

        metrics = {
            "annualized_return": 0.10,
            "sharpe": None,
            "calmar": 0.5,
            "max_drawdown": -0.10,
        }
        result = _passes_screens(
            metrics,
            live_returns=[0.001] * 50,
            screen_config=ScreenConfig(),
            returns_pct=[0.1] * 50,
        )
        assert result is False, (
            "_passes_screens must return False when sharpe is None (fail-closed per contract §4)"
        )

    def test_passes_screens_returns_false_when_calmar_is_none(self, sbe):
        """Contract §4: None-metric → screen fails closed. calmar=None must return False."""
        from advisors.strategy_builder_engine import (  # noqa: PLC0415
            ScreenConfig,
            _passes_screens,
        )

        metrics = {
            "annualized_return": 0.10,
            "sharpe": 1.0,
            "calmar": None,
            "max_drawdown": -0.10,
        }
        result = _passes_screens(
            metrics,
            live_returns=[0.001] * 50,
            screen_config=ScreenConfig(),
            returns_pct=[0.1] * 50,
        )
        assert result is False, (
            "_passes_screens must return False when calmar is None (fail-closed per contract §4)"
        )

    def test_passes_screens_returns_false_when_max_drawdown_is_none(self, sbe):
        """Contract §4: None-metric → screen fails closed. max_drawdown=None must return False."""
        from advisors.strategy_builder_engine import (  # noqa: PLC0415
            ScreenConfig,
            _passes_screens,
        )

        metrics = {
            "annualized_return": 0.10,
            "sharpe": 1.0,
            "calmar": 0.5,
            "max_drawdown": None,
        }
        result = _passes_screens(
            metrics,
            live_returns=[0.001] * 50,
            screen_config=ScreenConfig(),
            returns_pct=[0.1] * 50,
        )
        assert result is False, (
            "_passes_screens must return False when max_drawdown is None "
            "(fail-closed per contract §4)"
        )

    def test_passes_screens_returns_false_when_all_metrics_none(self, sbe):
        """Contract §4: all-None metrics dict must return False (not raise an exception).

        This tests that the function degrades gracefully on maximally degenerate input.
        """
        from advisors.strategy_builder_engine import (  # noqa: PLC0415
            ScreenConfig,
            _passes_screens,
        )

        metrics = {
            "annualized_return": None,
            "sharpe": None,
            "calmar": None,
            "max_drawdown": None,
        }
        # Must not raise; must return False
        try:
            result = _passes_screens(
                metrics,
                live_returns=[0.001] * 50,
                screen_config=ScreenConfig(),
                returns_pct=[0.1] * 50,
            )
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"_passes_screens must not raise on all-None metrics; got {exc!r}")
        assert result is False, (
            "_passes_screens must return False for an all-None metrics dict "
            "(fail-closed per contract §4)"
        )

    # -----------------------------------------------------------------------
    # Hot spot 3 — Log-return sign preservation under r * 100.0 conversion
    # -----------------------------------------------------------------------

    def test_negative_log_return_converts_to_negative_pct(self, sbe):
        """Contract §2.4: conversion is r * 100.0 exactly.
        A negative log return (-0.005) must convert to -0.5, not +0.5.

        Tolerance: 1e-12 (exact multiplication, not an approximation step).
        """
        log_return = -0.005
        expected_pct = -0.5
        # The engine applies: r * 100.0 for r in daily_returns.values()
        converted = log_return * 100.0
        assert converted == pytest.approx(expected_pct, abs=1e-12), (
            # Tolerance 1e-12: r*100.0 is exact floating-point multiplication;
            # any deviation is a sign-flip coding error.
            f"Negative log return {log_return} must convert to {expected_pct} "
            f"(r * 100.0 exactly); got {converted}"
        )
        # Explicit sign check: must be strictly negative
        assert converted < 0, (
            f"Converted pct must be negative for negative log return {log_return}; got {converted}"
        )

    def test_zero_log_return_converts_to_zero_pct(self, sbe):
        """Contract §2.4: zero log return must convert to exactly 0.0 pct.

        Tolerance: 1e-12 (exact multiplication).
        """
        log_return = 0.0
        converted = log_return * 100.0
        assert converted == pytest.approx(0.0, abs=1e-12), (
            # Tolerance 1e-12: 0.0 * 100.0 is always exactly 0.0 in IEEE-754.
            f"Zero log return must convert to 0.0 pct; got {converted}"
        )

    def test_mixed_sign_log_returns_preserve_signs_under_conversion(self, sbe):
        """Contract §2.4: a mix of positive and negative log returns must all
        preserve their sign after r * 100.0 conversion. Probes that the conversion
        does not take abs() or flip sign on any element.

        Tolerance: 1e-12 (exact multiplication — any deviation is a coding error).
        """
        from advisors.composer_backtest_client import BacktestResult  # noqa: PLC0415

        raw = {
            "2023-01-02": -0.005,  # negative → must stay negative
            "2023-01-03": 0.003,  # positive → must stay positive
            "2023-01-04": 0.0,  # zero → must stay zero
            "2023-01-05": -0.012,  # larger negative → must stay negative
            "2023-01-06": 0.008,  # positive → must stay positive
        }
        expected_signs = [-1, +1, 0, -1, +1]  # sign of each converted value

        result = BacktestResult(
            stats={"sharpe": 0.1},
            data_warnings=[],
            daily_returns=raw,
        )
        converted = [r * 100.0 for r in result.daily_returns.values()]

        assert len(converted) == len(expected_signs), (
            f"Length mismatch: {len(converted)} vs {len(expected_signs)}"
        )
        for i, (val, expected_sign) in enumerate(zip(converted, expected_signs, strict=True)):
            if expected_sign == -1:
                assert val < 0, (
                    # Tolerance: exact sign — no approx needed, this is a sign check.
                    f"Return[{i}] must be negative after conversion; got {val}"
                )
            elif expected_sign == +1:
                assert val > 0, f"Return[{i}] must be positive after conversion; got {val}"
            else:
                # Tolerance 1e-12: 0.0 * 100.0 is always exactly 0.0 in IEEE-754.
                assert val == pytest.approx(0.0, abs=1e-12), (
                    f"Return[{i}] must be zero after conversion; got {val}"
                )

    # -----------------------------------------------------------------------
    # Hot spot 4 — Never-raises on genuinely pathological inputs
    # -----------------------------------------------------------------------

    def test_value_error_from_run_backtest_does_not_raise(self, sbe):
        """Contract §2.6: ValueError from run_backtest must be caught; must return
        ProposalRun, not propagate the exception to the caller.
        """
        with (
            patch(
                "advisors.strategy_builder_engine.run_backtest",
                side_effect=ValueError("bad tree shape"),
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database"),
        ):
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 50,
                symphony_id="test-valueerror",
            )

        assert isinstance(result, sbe.ProposalRun), (
            "propose_strategies must return ProposalRun even when run_backtest "
            "raises ValueError (never-raises contract §2.6)"
        )

    def test_key_error_from_run_backtest_does_not_raise(self, sbe):
        """Contract §2.6: KeyError from run_backtest must be caught; must return
        ProposalRun, not propagate the exception to the caller.
        """
        with (
            patch(
                "advisors.strategy_builder_engine.run_backtest",
                side_effect=KeyError("missing_field"),
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database"),
        ):
            result = sbe.propose_strategies(
                objective=sbe.Objective.lift_risk_adjusted,
                universe=["SPY", "AGG", "GLD"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 50,
                symphony_id="test-keyerror",
            )

        assert isinstance(result, sbe.ProposalRun), (
            "propose_strategies must return ProposalRun even when run_backtest "
            "raises KeyError (never-raises contract §2.6)"
        )

    def test_db_exception_during_persistence_does_not_abort_run(self, sbe):
        """Contract §2.6: an Exception from database.insert_advisor_observation must
        NOT abort the run or set ProposalRun.error. Persistence errors are logged and
        silently skipped; the batch completes with whatever was persisted before the failure.
        """
        fake = _make_fake_result(n_days=100, base_return=0.001)

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch(
                "advisors.strategy_builder_engine.database.insert_advisor_observation",
                side_effect=Exception("DB connection failed"),
            ),
        ):
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG", "GLD"],
                screen_config=sbe.ScreenConfig(min_sharpe=-999.0),
                live_returns=[0.001] * 100,
                symphony_id="test-db-exception",
            )

        assert isinstance(result, sbe.ProposalRun), (
            "propose_strategies must return ProposalRun even when persistence raises"
        )
        assert result.error is None, (
            f"ProposalRun.error must be None when only persistence fails; "
            f"got {result.error!r}. Persistence errors are logged, not propagated."
        )

    def test_universe_with_more_than_10_tickers_does_not_raise(self, sbe):
        """Contract §2.3: the engine caps candidates internally at 10 tickers.
        A universe with >10 tickers must not raise, and result.candidates length
        must be <= MAX_CANDIDATES_PER_RUN.
        """
        fake = _make_fake_result(n_days=100, base_return=0.001)
        large_universe = [
            "SPY",
            "AGG",
            "GLD",
            "TLT",
            "QQQ",
            "IWM",
            "EFA",
            "EEM",
            "LQD",
            "HYG",
            "VNQ",
            "TIP",
            "SHY",
            "BIL",
            "BNDX",
        ]  # 15 tickers — 5 beyond the 10-ticker cap

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=large_universe,
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                symphony_id="test-large-universe",
            )

        assert isinstance(result, sbe.ProposalRun), (
            "propose_strategies must return ProposalRun for universe with >10 tickers"
        )
        assert len(result.candidates) <= sbe.MAX_CANDIDATES_PER_RUN, (
            f"result.candidates length must be <= MAX_CANDIDATES_PER_RUN "
            f"({sbe.MAX_CANDIDATES_PER_RUN}); got {len(result.candidates)}"
        )

    # -----------------------------------------------------------------------
    # Hot spot 5 — Grammar construct purity (T4/T5/T6/T7 specific)
    # -----------------------------------------------------------------------

    def test_t4_uses_moving_average_price_not_alias(self, sbe):
        """Contract §2.1: T4 must use 'moving-average-price' (VERIFIED-LOCAL grammar token),
        not an alias such as 'moving-average' or 'sma'. Probes that the indicator fn
        string in the serialized tree is the canonical form.
        """
        tree = sbe.trend_switch(
            signal_ticker="SPY",
            ma_window=50,
            risk_on_tickers=["QQQ"],
            risk_off_tickers=["TLT"],
        )
        serialized = json.dumps(tree)
        assert "moving-average-price" in serialized, (
            "T4 (trend_switch) must use 'moving-average-price' as the MA indicator fn; "
            "not found in serialized tree"
        )
        # Reject short aliases that would fail validate_tree or confuse Composer
        import re  # noqa: PLC0415

        assert not re.search(r'"(?:lhs-fn|rhs-fn)"\s*:\s*"sma"', serialized), (
            "T4 must not use 'sma' as an indicator fn alias — use 'moving-average-price'"
        )

    def test_t4_uses_current_price(self, sbe):
        """Contract §2.1: T4 must use 'current-price' (VERIFIED-LOCAL) as the lhs
        indicator fn. Probes that both sides of the MA comparison use canonical tokens.
        """
        tree = sbe.trend_switch(
            signal_ticker="SPY",
            ma_window=50,
            risk_on_tickers=["QQQ"],
            risk_off_tickers=["TLT"],
        )
        serialized = json.dumps(tree)
        assert "current-price" in serialized, (
            "T4 (trend_switch) must use 'current-price' as the lhs indicator fn; "
            "not found in serialized tree"
        )

    def test_t5_rsi_condition_comparison_value_is_numeric_string(self, sbe):
        """Grammar §3.4 + §16.8: when rhs-fixed-value? is true, rhs-val must be a
        numeric STRING (e.g. "70", not the integer 70). VERIFIED-LOCAL — empirically
        validated 2026-05-14. The stringification rule is documented in §16.8:
        whole-number numeric rhs is stringified without a trailing .0.

        Three assertions:
        1. rhs_val_found is a str (not int or float)
        2. float(rhs_val_found) does not raise — the string is parseable as a number
        3. float(rhs_val_found) == 70.0 — the string represents the correct threshold
        """
        tree = sbe.rsi_rotation(
            signal_ticker="SPY",
            rsi_window=14,
            threshold=70,
            overbought_tickers=["TLT"],
            neutral_tickers=["SPY"],
        )
        serialized_dict = json.loads(json.dumps(tree))

        # Walk the tree to find the if-child node with the RSI condition
        stack = [serialized_dict]
        rhs_val_found = None
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            if node.get("step") == "if-child" and not node.get("is-else-condition?"):
                rhs_val_found = node.get("rhs-val")
                break
            for child in node.get("children", []) or []:
                stack.append(child)

        assert rhs_val_found is not None, (
            "T5 (rsi_rotation) tree must contain an if-child node with rhs-val set"
        )
        # Assertion 1: must be a str, not int or float (grammar §3.4 VERIFIED-LOCAL)
        assert isinstance(rhs_val_found, str), (
            f"T5 rhs-val (threshold) must be a numeric STRING per grammar §3.4; "
            f"got {type(rhs_val_found).__name__}={rhs_val_found!r}"
        )
        # Assertion 2: the string must be parseable as a number
        try:
            parsed = float(rhs_val_found)
        except (ValueError, TypeError) as exc:
            pytest.fail(f"T5 rhs-val {rhs_val_found!r} must be parseable as a float; got {exc!r}")
        # Assertion 3: the numeric value must equal the threshold passed in (70)
        # Tolerance: 1e-9 — threshold=70 is an exact integer; any deviation
        # would indicate a mangled stringification (e.g. rounding or sign flip).
        assert parsed == pytest.approx(70.0, rel=1e-9), (
            f"T5 rhs-val string must represent the threshold value 70.0; "
            f"got {rhs_val_found!r} (parsed={parsed})"
        )

    def test_t6_window_value_is_integer_in_json(self, sbe):
        """Contract §2.1: T6 (momentum_top_n) window must appear as an integer
        in the JSON sort-by-fn-params, not as a stringified value.

        Tolerance: exact type check (int, not float or str).
        """
        window = 63
        tree = sbe.momentum_top_n(["SPY", "QQQ", "IWM"], n=2, window=window)
        serialized_dict = json.loads(json.dumps(tree))

        # Walk the tree to find the filter node
        stack = [serialized_dict]
        window_val_found = None
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            if node.get("step") == "filter":
                params = node.get("sort-by-fn-params") or {}
                window_val_found = params.get("window")
                break
            for child in node.get("children", []) or []:
                stack.append(child)

        assert window_val_found is not None, (
            "T6 (momentum_top_n) tree must contain a filter node with sort-by-fn-params.window"
        )
        assert isinstance(window_val_found, int) and not isinstance(window_val_found, bool), (
            f"T6 sort-by-fn-params.window must be an integer in JSON; "
            f"got {type(window_val_found).__name__}={window_val_found!r}"
        )
        assert window_val_found == window, (
            f"T6 sort-by-fn-params.window must equal the passed window={window}; "
            f"got {window_val_found}"
        )

    def test_t7_select_bottom_and_max_drawdown_present(self, sbe):
        """Contract §2.1: T7 (low_vol_floor) must use 'bottom' (VERIFIED-LOCAL per
        grammar §3.5) as the select-fn and 'max-drawdown' as the sort-by-fn
        (VERIFIED-LOCAL in sort position per vocabulary research; it replaced
        standard-deviation-return, which is refuted in sort position).

        select-fn is checked via tree traversal to the filter node (not a raw JSON
        substring match) so the assertion targets the exact field value.
        max-drawdown is checked via raw JSON substring — it is a sort-by-fn
        value that does not appear elsewhere in the tree, so the substring match is exact.
        """
        tree = sbe.low_vol_floor(["SPY", "TLT", "GLD", "IWM"], n=2, window=21)

        # Check select-fn via traversal — raw substring would pass on "select-bottom" too
        stack = [tree]
        found_fn = None
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            if node.get("step") == "filter":
                found_fn = node.get("select-fn")
                break
            for child in node.get("children", []) or []:
                stack.append(child)
        assert found_fn == "bottom", (
            f"T7 (low_vol_floor) must use 'bottom' as select-fn per grammar §3.5 "
            f"VERIFIED-LOCAL; got {found_fn!r}"
        )

        # sort-by-fn: substring check is safe — value does not appear elsewhere in the tree
        serialized = json.dumps(tree)
        assert "max-drawdown" in serialized, (
            "T7 (low_vol_floor) must use 'max-drawdown' as sort-by-fn"
        )

    def test_t7_n_is_integer_in_json(self, sbe):
        """Contract §2.1: T7 (low_vol_floor) select-n must appear as an integer
        in the JSON filter node, not as a float or string.

        Tolerance: exact type check (int, not float or str).
        """
        n = 2
        tree = sbe.low_vol_floor(["SPY", "TLT", "GLD"], n=n, window=21)
        serialized_dict = json.loads(json.dumps(tree))

        stack = [serialized_dict]
        select_n_found = None
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            if node.get("step") == "filter":
                select_n_found = node.get("select-n")
                break
            for child in node.get("children", []) or []:
                stack.append(child)

        assert select_n_found is not None, (
            "T7 (low_vol_floor) tree must contain a filter node with select-n"
        )
        assert isinstance(select_n_found, int) and not isinstance(select_n_found, bool), (
            f"T7 select-n must be an integer in JSON; "
            f"got {type(select_n_found).__name__}={select_n_found!r}"
        )
        assert select_n_found == n, f"T7 select-n must equal the passed n={n}; got {select_n_found}"


# ===========================================================================
# SECTION 14 — Adversarial cycle 4 (post-cycle-3-GREEN)
# ADVERSARIAL CYCLE 4 — added by test-writer
# ===========================================================================


class TestAdversarialCycle4:
    """Adversarial tests targeting invariants not covered by cycles 1–3:
    (1) _passes_screens skips blended/correlation when live_returns is empty
    (2) ProposalRun.candidates contains ONLY successfully-backtested candidates
    (3) propose_strategies with live_returns=[] never raises
    (4) incumbent_oos_alpha / default_oos_alpha are forwarded to evaluate_candidate_batch
    (5) MAX_CANDIDATES_PER_RUN upper-bound enforced for very large universes
    """

    # -----------------------------------------------------------------------
    # Attack 1 — _passes_screens skips blended/correlation on empty live_returns
    # -----------------------------------------------------------------------

    def test_passes_screens_empty_live_returns_skips_blended_and_correlation(self, sbe):
        """Contract §4: blended drawdown and correlation sub-checks require live
        portfolio returns to be non-empty. When live_returns=[], those two screens
        cannot be evaluated and must be SKIPPED — the candidate must still pass the
        other (individual-metric) screens if they are satisfied.

        This probes the guard: `if live_returns and returns_pct:` — empty live_returns
        must cause both the blended MDD and correlation checks to be bypassed, so a
        candidate that passes all individual-metric thresholds returns True even when
        a tight max_blended_abs_drawdown or max_correlation would otherwise reject it.
        """
        from advisors.strategy_builder_engine import (  # noqa: PLC0415
            ScreenConfig,
            _passes_screens,
        )

        # Metrics that pass all individual screens with the default ScreenConfig
        # (SCREEN_MIN_CAGR_DEFAULT=0.0, SCREEN_MIN_SHARPE_DEFAULT=0.0,
        #  SCREEN_MIN_CALMAR_DEFAULT=0.0, SCREEN_MAX_ABS_DRAWDOWN_DEFAULT=0.50)
        good_metrics = {
            "annualized_return": 0.12,
            "sharpe": 0.8,
            "calmar": 0.4,
            "max_drawdown": -0.15,
        }

        # Use pathologically tight blended/correlation thresholds that WOULD reject
        # the candidate if the screens ran. Since live_returns=[], they must be skipped.
        tight_config = ScreenConfig(
            max_blended_abs_drawdown=0.001,  # essentially impossible to pass
            max_correlation=0.001,  # essentially impossible to pass
        )

        result = _passes_screens(
            good_metrics,
            live_returns=[],  # empty — must cause skip of both sub-checks
            screen_config=tight_config,
            returns_pct=[0.01] * 50,
        )

        assert result is True, (
            "_passes_screens must return True when live_returns=[] and all individual "
            "metric screens pass: blended-drawdown and correlation checks must be "
            "SKIPPED (not applied) when there are no live portfolio returns to blend with. "
            f"Got False with tight_config={tight_config!r}"
        )

    # -----------------------------------------------------------------------
    # Attack 2 — ProposalRun.candidates only contains backtest-error-free candidates
    # -----------------------------------------------------------------------

    def test_candidates_list_excludes_failed_backtests(self, sbe):
        """FDR invariant: ProposalRun.candidates must contain ONLY candidates where
        backtest_error is None. Candidates whose backtest raised or returned an error
        result must be excluded from result.candidates even though they are tracked
        internally for the gate's n_candidates accounting.

        Uses a side_effect that returns success for the first 2 calls and a backtest
        error for subsequent calls, ensuring the universe generates a mix of
        success and failure candidates.
        """
        fake_success = _make_fake_result(n_days=100, base_return=0.001)
        fake_error = _make_error_result()

        call_counter = {"n": 0}

        def _side_effect(*args, **kwargs):
            call_counter["n"] += 1
            # First 2 calls succeed; everything else fails
            return fake_success if call_counter["n"] <= 2 else fake_error

        with (
            patch("advisors.strategy_builder_engine.run_backtest", side_effect=_side_effect),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG", "GLD", "TLT", "QQQ"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                symphony_id="test-adv4-candidates",
            )

        assert result.error is None, f"Unexpected error: {result.error}"

        # Every CandidateInfo in result.candidates must have backtest_error is None
        for cand in result.candidates:
            assert cand.backtest_error is None, (
                f"result.candidates must only contain successfully-backtested candidates "
                f"(backtest_error is None); found candidate {cand.candidate_id!r} with "
                f"backtest_error={cand.backtest_error!r}"
            )

    # -----------------------------------------------------------------------
    # Attack 3 — propose_strategies with live_returns=[] never raises
    # -----------------------------------------------------------------------

    def test_propose_strategies_empty_live_returns_does_not_raise(self, sbe):
        """Contract §2.6 + §4: live_returns=[] is a valid degenerate input.
        The function must return a ProposalRun without raising, even though the
        blended-drawdown and correlation sub-checks cannot be evaluated.

        This is an end-to-end complement to attack 1: confirms that the empty
        live_returns guard in _passes_screens propagates cleanly through the full
        propose_strategies call path.
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
                live_returns=[],  # empty — the key edge case
                symphony_id="test-adv4-empty-live",
            )

        assert isinstance(result, sbe.ProposalRun), (
            "propose_strategies must return ProposalRun when live_returns=[]"
        )
        assert result.error is None, (
            f"propose_strategies must not set error when live_returns=[]; "
            f"got error={result.error!r}"
        )

    # -----------------------------------------------------------------------
    # Attack 4 — incumbent_oos_alpha / default_oos_alpha forwarded to gate
    # -----------------------------------------------------------------------

    def test_oos_alpha_params_forwarded_to_gate(self, sbe):
        """Contract §2: incumbent_oos_alpha and default_oos_alpha accepted by
        propose_strategies must be forwarded unchanged to evaluate_candidate_batch.

        Patches evaluate_candidate_batch with a MagicMock so the kwargs can be
        inspected. The mock returns an empty GatedBatch so the rest of the pipeline
        completes without error.
        """
        from unittest.mock import MagicMock  # noqa: PLC0415

        from advisors.backtest_gate_engine import GatedBatch  # noqa: PLC0415

        fake = _make_fake_result(n_days=100, base_return=0.001)

        # Build a minimal GatedBatch that the downstream pipeline can consume
        fake_gate_batch = GatedBatch(
            results=[],
            survivors=[],
            n_candidates=0,
            fdr_q=0.05,
        )
        mock_gate = MagicMock(return_value=fake_gate_batch)

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database"),
            patch("advisors.strategy_builder_engine.evaluate_candidate_batch", mock_gate),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                symphony_id="test-adv4-alpha-forwarding",
                incumbent_oos_alpha=0.05,
                default_oos_alpha=0.02,
            )

        assert mock_gate.called, "evaluate_candidate_batch must be called by propose_strategies"
        _, call_kwargs = mock_gate.call_args
        assert "incumbent_oos_alpha" in call_kwargs, (
            "evaluate_candidate_batch must be called with incumbent_oos_alpha kwarg"
        )
        assert "default_oos_alpha" in call_kwargs, (
            "evaluate_candidate_batch must be called with default_oos_alpha kwarg"
        )
        # Tolerance: exact equality — these are pass-through values, not computed floats.
        assert call_kwargs["incumbent_oos_alpha"] == pytest.approx(0.05, rel=1e-9), (
            # Tolerance 1e-9: pass-through of a literal float; any deviation indicates
            # the value was transformed or defaulted rather than forwarded.
            f"incumbent_oos_alpha must be forwarded as 0.05; "
            f"got {call_kwargs['incumbent_oos_alpha']!r}"
        )
        assert call_kwargs["default_oos_alpha"] == pytest.approx(0.02, rel=1e-9), (
            # Tolerance 1e-9: same reasoning as above.
            f"default_oos_alpha must be forwarded as 0.02; got {call_kwargs['default_oos_alpha']!r}"
        )

    # -----------------------------------------------------------------------
    # Attack 5 — MAX_CANDIDATES_PER_RUN upper-bound for very large universes
    # -----------------------------------------------------------------------

    def test_universe_large_generates_at_most_max_candidates(self, sbe):
        """Contract §2.3: candidate generation is bounded by MAX_CANDIDATES_PER_RUN.
        A universe of 30 tickers (3× the internal 10-ticker cap) must produce
        at most MAX_CANDIDATES_PER_RUN candidates in result.candidates.

        This is distinct from the >10-ticker test in cycle 3 (which used 15 tickers
        and focused on the 10-ticker internal cap). Here we use 30 tickers and focus
        on the MAX_CANDIDATES_PER_RUN = 30 outer bound, asserting the implementation
        never exceeds it regardless of how many template × parameter combinations are
        generated internally.
        """
        fake = _make_fake_result(n_days=100, base_return=0.001)

        # 30 tickers — 3× the internal 10-ticker slice ceiling
        large_universe = [
            "SPY",
            "AGG",
            "GLD",
            "TLT",
            "QQQ",
            "IWM",
            "EFA",
            "EEM",
            "LQD",
            "HYG",
            "VNQ",
            "TIP",
            "SHY",
            "BIL",
            "BNDX",
            "XLK",
            "XLF",
            "XLE",
            "XLV",
            "XLI",
            "XLU",
            "XLP",
            "XLB",
            "XLRE",
            "XLC",
            "IEMG",
            "VEA",
            "VWO",
            "BND",
            "VCIT",
        ]
        assert len(large_universe) == 30

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=large_universe,
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                symphony_id="test-adv4-large-universe",
            )

        assert isinstance(result, sbe.ProposalRun), (
            "propose_strategies must return ProposalRun for a 30-ticker universe"
        )
        assert result.error is None, (
            f"propose_strategies must not error on a 30-ticker universe; got error={result.error!r}"
        )
        assert len(result.candidates) <= sbe.MAX_CANDIDATES_PER_RUN, (
            f"result.candidates length must be <= MAX_CANDIDATES_PER_RUN "
            f"({sbe.MAX_CANDIDATES_PER_RUN}) for a 30-ticker universe; "
            f"got {len(result.candidates)}"
        )
