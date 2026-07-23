"""
AC-5 (M1) -- log-vs-simple-return CATEGORY error at the quantstats input
boundary. Golden fixture: tests/fixtures/math/ac5_log_return_compounding_boundary.json.

ROOT CAUSE (r2-test finding, ratified in feature-plans/math-r2.md's
plan-approval ADDENDUM): this is NOT a percent/decimal SCALE bug.
composer_backtest_client.py:182 emits LOG returns
(``math.log(curr_val/prev_val)``); the existing ``*100.0`` conversion at the
production call sites (strategy_builder_engine.py:996,
frontrunner_builder.py:1607-1608 -- "log x 100 -> pct") correctly bridges
DECIMAL-scale log returns into the PERCENT-scale contract
analytics.compute_quantstats_metrics's own ``/100.0`` docstring assumes.
The real defect: compute_quantstats_metrics's ``total_return`` (``(1+series)
.prod()-1``) and quantstats' own cagr/calmar internals all assume SIMPLE
(arithmetic) returns. Compounding LOG returns with simple-return math is a
category error -- the mathematically correct compounding of a log-return
series is ``exp(sum(r)) - 1`` (exactly ``final_value/initial_value - 1`` by
telescoping), not ``prod(1+r) - 1``. The error is second-order in the
per-period return magnitude, so it GROWS with volatility -- reproducing the
audit's exact qualitative pattern (larger error at higher vol).

RULED DIRECTION, FINAL (team-lead ADDENDUM 4 @ 148e43ba, superseding an
earlier consumer-side `input_convention` kwarg design that r2-analytics's
completed consumer sweep found entangled with the PBO/CRRA-EU gate path --
see DE-MATH-R2 for the full record): PRODUCER-SIDE fix.
composer_backtest_client._extract_returns emits SIMPLE returns directly
(``daily_returns[date] = (curr_val / prev_val) - 1.0``, replacing today's
``math.log(curr_val/prev_val)``) -- no kwarg, no per-call-site flag. This
fixes compute_quantstats_metrics (this file's original AC-5 target) AND the
PBO/CRRA-EU gate path (the untraced category-half of MA-3) with ONE change,
since both consumers already correctly assume simple/percent-scale input.
analytics.py itself needs ZERO changes ("Group-A zero-drift" -- see the
dedicated test class below). This file's core battery (below) already tests
the OBSERVABLE END-TO-END CONTRACT via the real production call shape
(composer_backtest_client._extract_returns -> the documented *100.0 bridge
-> analytics.compute_quantstats_metrics) and needed NO changes when the
mechanism flipped from consumer-side to producer-side -- it was written
mechanism-agnostic from the start and remains valid. Two additions below
close the gap to the final ruling: a DIRECT structural pin on
_extract_returns's own emission semantic, and the Group-A zero-drift
regression guard for consumers that were already correct.

No hardcoded producer-computed expected values: every "true" reference value
is derived at test time from the SAME real portfolio-value series via
"final_value / initial_value - 1" (a arithmetic identity, not a producer
output) or by feeding the CORRECT simple-return series (also derived from
the same real values) through the SAME compute_quantstats_metrics function --
never a hand-typed literal.
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest

import analytics
from advisors.composer_backtest_client import _extract_returns

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "math" / "ac5_log_return_compounding_boundary.json"
)

# Tolerance for the post-fix total_return/CAGR match against the ground-truth
# compounded value. This is an EXACT mathematical identity for a correctly
# log-aware (or correctly boundary-converted) pipeline
# (exp(sum(log_r)) - 1 == final/initial - 1 by telescoping), so the only
# slack needed is float64 accumulation error over ~90 terms -- 1e-6 absolute
# is generous headroom for that, not a tuned-to-pass fudge factor.
_EXACT_IDENTITY_TOL = 1e-6


def _load_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _real_values() -> list[float]:
    fx = _load_fixture()
    pairs = sorted(
        ((int(k), float(v)) for k, v in fx["dvm_capital_day_values"].items()),
        key=lambda t: t[0],
    )
    return [v for _, v in pairs]


def _build_scaled_value_series(base_values: list[float], scale: float) -> list[float]:
    """Documented transform (see fixture _description): amplify each day's
    REAL log return by `scale`, reconstructing a value series that starts at
    the same initial value but has scale-x the daily log-return magnitude.
    scale=1.0 reproduces the real series exactly (up to float roundoff)."""
    out = [base_values[0]]
    for i in range(1, len(base_values)):
        log_r = math.log(base_values[i] / base_values[i - 1])
        out.append(out[-1] * math.exp(log_r * scale))
    return out


def _dvm_capital_dict(values: list[float], symphony_id: str) -> dict:
    """Wrap a value series as a dvm_capital-shaped dict keyed by day-int
    string, starting at the fixture's real first day_int, so
    _extract_returns's day-int-to-ISO-date machinery runs unmodified."""
    fx = _load_fixture()
    pairs = sorted(
        ((int(k), float(v)) for k, v in fx["dvm_capital_day_values"].items()),
        key=lambda t: t[0],
    )
    first_day_int = pairs[0][0]
    return {symphony_id: {str(first_day_int + i): v for i, v in enumerate(values)}}


def _production_returns_pct(values: list[float], symphony_id: str) -> list[float]:
    """Mirrors the REAL production path: composer_backtest_client._extract_returns
    (whatever it emits post-fix -- log or already-simple, this test does not
    presume which) -> the documented *100.0 pct-scale bridge (identical to
    strategy_builder_engine.py:996 / frontrunner_builder.py:1607-1608)."""
    dvm = _dvm_capital_dict(values, symphony_id)
    _portfolio_values, extracted_returns = _extract_returns(dvm, symphony_id)
    return [r * 100.0 for r in extracted_returns.values()]


def _ground_truth_total_return(values: list[float]) -> float:
    """The one unambiguous, representation-independent truth: what did the
    portfolio actually return over this exact span? Pure arithmetic on the
    fixture's own captured values -- not a producer-computed metric."""
    return values[-1] / values[0] - 1.0


def _ground_truth_metrics_via_correct_simple_returns(values: list[float]) -> dict:
    """An independent second derivation of the correct metrics: feed
    compute_quantstats_metrics the CORRECT simple-return series (also
    derived from the same real values, via the textbook definition of a
    simple return) -- this must agree with total_return's closed-form
    ground truth (self-check), and gives correct annualized_return/calmar
    reference values compute_quantstats_metrics itself computes from
    quantstats internals (not hand-derived, so no independent CAGR/calmar
    formula is invented here)."""
    simple_returns_pct = [(values[i] / values[i - 1] - 1.0) * 100.0 for i in range(1, len(values))]
    return analytics.compute_quantstats_metrics(simple_returns_pct)


class TestFixturePrecondition:
    def test_fixture_exists_and_has_ninety_real_days(self):
        assert _FIXTURE_PATH.exists(), f"Golden fixture missing at {_FIXTURE_PATH}."
        fx = _load_fixture()
        assert fx["_fixture_provenance"].startswith("captured-from-producer"), (
            "AC-5 fixture must be captured-from-producer provenance, not hand-invented."
        )
        assert len(fx["dvm_capital_day_values"]) == 90

    def test_ground_truth_self_check_matches_correct_simple_return_compounding(self):
        """Sanity: the closed-form ground truth (final/initial - 1) must
        match feeding the CORRECT simple-return series through
        compute_quantstats_metrics's own (correct-for-simple-returns)
        total_return formula -- if these disagree, the fixture or the
        correct-series construction has a bug, independent of AC-5's fix."""
        values = _real_values()
        truth = _ground_truth_total_return(values)
        correct_metrics = _ground_truth_metrics_via_correct_simple_returns(values)
        assert correct_metrics["total_return"] == pytest.approx(truth, abs=_EXACT_IDENTITY_TOL), (
            f"compute_quantstats_metrics fed CORRECT simple returns produced "
            f"total_return={correct_metrics['total_return']!r}, expected {truth!r} "
            "(final/initial - 1). This self-check failing means the test's own "
            "ground-truth derivation is broken, not AC-5's fix."
        )


@pytest.mark.parametrize("scale", [1.0, 2.0, 3.0])
class TestAC5TotalReturnMatchesGroundTruth:
    """The core RED battery: total_return through the REAL production log-
    return extraction + pct bridge must match the closed-form ground truth
    (final_value/initial_value - 1) at increasing volatility scales,
    reproducing the audit's exact "error grows with vol" pattern as the
    discriminator. RED today (verified live): the pre-fix pipeline computes
    log returns then compounds them as simple percent, diverging from truth
    by an amount that grows with scale -- not by a fixed offset."""

    def test_total_return_matches_ground_truth(self, scale: float):
        base_values = _real_values()
        scaled_values = _build_scaled_value_series(base_values, scale)
        truth = _ground_truth_total_return(scaled_values)

        returns_pct = _production_returns_pct(scaled_values, "sym-ac5-001")
        metrics = analytics.compute_quantstats_metrics(returns_pct)

        assert metrics["total_return"] == pytest.approx(truth, abs=_EXACT_IDENTITY_TOL), (
            f"[scale={scale}] production pipeline total_return="
            f"{metrics['total_return']!r} does not match ground truth {truth!r} "
            f"(final/initial - 1, error={metrics['total_return'] - truth:+.6f}). "
            "AC-5 (M1): the log-return / simple-return compounding boundary is "
            "not yet fixed."
        )

    def test_annualized_return_matches_correct_simple_return_reference(self, scale: float):
        """annualized_return (CAGR) must match what compute_quantstats_metrics
        itself computes when handed the CORRECT simple-return series derived
        from the same values -- the reference is the function's own
        (correct-for-simple-returns) internals, not a hand-derived CAGR
        formula, so this isolates exactly the input-convention defect."""
        base_values = _real_values()
        scaled_values = _build_scaled_value_series(base_values, scale)
        reference = _ground_truth_metrics_via_correct_simple_returns(scaled_values)

        returns_pct = _production_returns_pct(scaled_values, "sym-ac5-001")
        metrics = analytics.compute_quantstats_metrics(returns_pct)

        assert metrics["annualized_return"] == pytest.approx(
            reference["annualized_return"], abs=_EXACT_IDENTITY_TOL
        ), (
            f"[scale={scale}] production pipeline annualized_return="
            f"{metrics['annualized_return']!r} does not match the correct-simple-return "
            f"reference {reference['annualized_return']!r} "
            f"(error={metrics['annualized_return'] - reference['annualized_return']:+.6f}, "
            f"i.e. {(metrics['annualized_return'] - reference['annualized_return']) * 100:+.3f} "
            "percentage points/yr). AC-5 (M1) not yet fixed."
        )

    def test_calmar_matches_correct_simple_return_reference(self, scale: float):
        """Plan explicitly calls out 'Calmar-gate levels wrong' -- calmar
        depends on both CAGR and max_drawdown, so this exercises the
        compounded interaction of both, not just CAGR alone."""
        base_values = _real_values()
        scaled_values = _build_scaled_value_series(base_values, scale)
        reference = _ground_truth_metrics_via_correct_simple_returns(scaled_values)

        returns_pct = _production_returns_pct(scaled_values, "sym-ac5-001")
        metrics = analytics.compute_quantstats_metrics(returns_pct)

        assert metrics["calmar"] == pytest.approx(reference["calmar"], abs=1e-4), (
            f"[scale={scale}] production pipeline calmar={metrics['calmar']!r} does not "
            f"match the correct-simple-return reference {reference['calmar']!r} "
            f"(error={metrics['calmar'] - reference['calmar']:+.6f}). "
            "AC-5 (M1): Calmar-gate levels are still wrong."
        )


def test_error_magnitude_is_bounded_near_zero_regardless_of_volatility_scale():
    """Regression pin against reintroducing ANY residual scale-dependent
    bias: for a CORRECT implementation, the total_return error vs ground
    truth must stay near zero (float-precision only) across a spread of
    volatility scales -- it must NOT grow with scale the way the pre-fix
    log-as-simple compounding bug does (verified live: pre-fix error grows
    from ~-0.3pp at scale=1 to ~-3.3pp at scale=3 on this exact fixture --
    over 10x worse, not a fixed offset)."""
    base_values = _real_values()
    errors = []
    for scale in (1.0, 2.0, 3.0):
        scaled_values = _build_scaled_value_series(base_values, scale)
        truth = _ground_truth_total_return(scaled_values)
        returns_pct = _production_returns_pct(scaled_values, "sym-ac5-001")
        metrics = analytics.compute_quantstats_metrics(returns_pct)
        errors.append(abs(metrics["total_return"] - truth))

    assert all(e <= _EXACT_IDENTITY_TOL for e in errors), (
        f"total_return error vs ground truth at scales [1.0, 2.0, 3.0] = {errors!r} -- "
        f"a correct implementation must keep every error <= {_EXACT_IDENTITY_TOL} "
        "(float precision only), not growing with volatility scale (AC-5/M1 signature)."
    )


# ===========================================================================
# Direct structural pin: _extract_returns's own emission semantic.
# RED today (verified live): the current implementation emits
# math.log(curr_val/prev_val), NOT (curr_val/prev_val - 1.0).
# ===========================================================================


class TestExtractReturnsEmitsSimpleReturnsNotLog:
    """team-lead ADDENDUM 4: the fix lands in _extract_returns itself --
    pin the emission formula directly, independent of any downstream
    consumer, so a future regression back to log returns is caught at the
    producer, not just inferred from a downstream metric drifting."""

    def test_extract_returns_matches_simple_ratio_formula(self):
        """A small, hand-traceable 3-day value series (100.0 -> 102.0 ->
        99.0) with EXACTLY computable simple returns:
        day2: 102/100 - 1 = 0.02
        day3: 99/102 - 1  = -0.029411764705882353
        Both values are plain arithmetic on the fixture's own inputs, not
        producer output -- not a hardcoded literal in the Rule-4 sense."""
        values = [100.0, 102.0, 99.0]
        symphony_id = "sym-ac5-emission-001"
        dvm = {symphony_id: {str(19000 + i): v for i, v in enumerate(values)}}

        _portfolio_values, extracted = _extract_returns(dvm, symphony_id)

        assert len(extracted) == 2, f"Expected 2 returns for 3 values, got {extracted!r}."
        returns_by_order = [v for _k, v in sorted(extracted.items())]

        expected_simple = [
            values[1] / values[0] - 1.0,
            values[2] / values[1] - 1.0,
        ]
        for i, (actual, expected) in enumerate(zip(returns_by_order, expected_simple, strict=True)):
            assert actual == pytest.approx(expected, abs=1e-12), (
                f"_extract_returns day {i + 1}: got {actual!r}, expected simple return "
                f"{expected!r} (curr/prev - 1). AC-5: emission must be SIMPLE, not log."
            )

    def test_extract_returns_output_does_not_match_log_return_formula(self):
        """Adversarial converse of the above: a correct (simple-return)
        implementation must NOT reproduce the log-return values for a
        series where log and simple diverge measurably (a large single-day
        move, where the log/simple gap is not swamped by float tolerance)."""
        values = [100.0, 130.0]  # +30% day -- log/simple gap is material here
        symphony_id = "sym-ac5-emission-002"
        dvm = {symphony_id: {str(19000): values[0], str(19001): values[1]}}

        _portfolio_values, extracted = _extract_returns(dvm, symphony_id)
        assert len(extracted) == 1
        actual = next(iter(extracted.values()))

        simple_expected = values[1] / values[0] - 1.0  # 0.30 exactly
        log_value = math.log(values[1] / values[0])  # ~0.262364 -- measurably different

        assert abs(simple_expected - log_value) > 0.03, (
            "Fixture self-check: this scenario's log/simple gap is too small to "
            "discriminate the two formulas -- pick a larger single-day move."
        )
        assert actual == pytest.approx(simple_expected, abs=1e-12), (
            f"_extract_returns returned {actual!r}, matching neither the correct simple "
            f"return {simple_expected!r} nor clearly excluding the log return "
            f"{log_value!r}. AC-5: emission must be curr/prev - 1, not math.log(curr/prev)."
        )
        assert actual != pytest.approx(log_value, abs=1e-6), (
            f"_extract_returns returned {actual!r}, which matches the LOG return "
            f"{log_value!r} -- AC-5 not yet fixed (still emitting log, not simple)."
        )


# ===========================================================================
# Group-A zero-drift regression guard: consumers that ALREADY receive
# simple-percent-scale data (never touched composer_backtest_client) must
# show IDENTICAL compute_quantstats_metrics output before and after AC-5,
# because analytics.py itself carries zero diff under the ruled producer-
# side fix.
# ===========================================================================


class TestGroupAZeroDriftForAlreadyCorrectConsumers:
    """Representative of app.py:3350-3351 (Performance tab, shadow_history-
    sourced) and strategy_builder_engine.py:574 (live-baseline) -- inputs
    that were NEVER log returns and never touched composer_backtest_client.
    Since the ruled fix touches ONLY composer_backtest_client.py (analytics.py
    gets zero diff), these consumers' output must be governed exclusively by
    the EXISTING, unchanged /100.0-then-simple-compounding contract -- this
    test is a structural regression guard, not a RED gap in the current
    implementation (it should already pass and must keep passing after AC-5
    lands elsewhere)."""

    def test_compute_quantstats_metrics_signature_has_no_input_convention_parameter(self):
        """The DEAD consumer-side kwarg design (superseded by ADDENDUM 4)
        must never be reintroduced -- the fix lives in the producer, not here."""
        import inspect

        sig = inspect.signature(analytics.compute_quantstats_metrics)
        assert "input_convention" not in sig.parameters, (
            "analytics.compute_quantstats_metrics gained an 'input_convention' "
            "parameter -- that design was RULED DEAD (team-lead ADDENDUM 4): the "
            "fix belongs in composer_backtest_client._extract_returns, not here."
        )

    def test_already_simple_percent_input_total_return_matches_plain_compounding(self):
        """A plain simple-percent-scale series (representative of a
        shadow_history/live-baseline consumer, never touched by
        composer_backtest_client) must compute total_return via the
        EXISTING, unchanged formula -- this must be true both before and
        after AC-5 lands, since analytics.py is untouched."""
        # Real-shaped but arbitrary simple-percent daily returns (not
        # composer-sourced at all -- this class of input was never log).
        simple_returns_pct = [0.5, -0.3, 1.2, -0.8, 0.4, 0.1, -0.2]
        metrics = analytics.compute_quantstats_metrics(simple_returns_pct)

        fractions = [r / 100.0 for r in simple_returns_pct]
        expected_total_return = 1.0
        for f in fractions:
            expected_total_return *= 1.0 + f
        expected_total_return -= 1.0

        assert metrics["total_return"] == pytest.approx(expected_total_return, abs=1e-9), (
            f"Group-A consumer (already simple-percent) total_return="
            f"{metrics['total_return']!r} does not match the existing, unchanged "
            f"plain-compounding formula {expected_total_return!r}. analytics.py must "
            "carry ZERO diff for this input class under the AC-5 producer-side fix."
        )
