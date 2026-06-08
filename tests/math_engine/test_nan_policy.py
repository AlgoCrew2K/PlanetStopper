"""
RED tests for the NaN/Inf rejection policy across math_engine.py.

==========================================================================
POLICY (Gate-1, this cycle): REJECT
==========================================================================

Math-layer functions taking floats as inputs MUST raise ``ValueError``
with a message containing the substring ``"NaN"`` (case-sensitive) when
ANY input is ``float('nan')``, ``float('inf')``, or ``float('-inf')``.

Rationale (drives the policy choice):
  The invariant audit at
  ``docs/research/math_engine/invariant-audit__2026-05-13.md`` documents
  that ``compute_active_trailing_stop``, ``compute_exit_confirmation``,
  and ``run_monte_carlo`` currently propagate NaN inputs to NaN outputs.
  Downstream of the math layer, ``alpha_bot_execution.py`` evaluates
  expressions of the form ``current_return <= stop_trigger_level - X``
  --- which, when either side is NaN, evaluates to False under IEEE-754
  semantics. The trailing stop is THEN SILENTLY DISABLED on a position
  the engine believes is being protected. This is a money-loss path on a
  real-money trading engine.

  Two policies were considered:
    * REJECT --- raise ValueError. Loud failure surfaces the upstream
      data quality bug at the math boundary, where it can be diagnosed
      and patched (e.g., normalize ``None -> 0.0`` in the caller). The
      engine is restarted by ``app.py``'s minute scheduler at the next
      tick, so a fail-fast policy does not orphan a position.
    * SAFE-SENTINEL --- substitute a documented default (e.g., 0.0 or
      ``VOL_FALLBACK``) and continue. Hides the upstream defect; risks
      compounding the bug across cycles.

  REJECT is selected as the project default. ``quant-code-reviewer`` may
  override this in favor of SAFE-SENTINEL for specific functions if the
  implementer presents a documented reason; if so, this test file must
  be updated to pin the chosen sentinel under the same input regimes
  rather than ``ValueError``.

Functions in scope for this cycle (audit's named functions plus any other
function in math_engine taking floats --- the audit identifies these three
as the highest-risk; we add the remaining float-taking functions because
the same NaN-silently-propagates failure mode applies to all of them):

  HIGH RISK (audit-named):
    * ``compute_active_trailing_stop``
    * ``compute_exit_confirmation``
    * ``run_monte_carlo``

  Also float-taking (same failure class, included for parity):
    * ``compute_para_arm_decision``
    * ``compute_time_squeeze_decay``
    * ``compute_breakeven_update``
    * ``compute_vwap_bleed_arm_threshold``
    * ``compute_vwap_breakdown_update``

  Not in scope --- list-of-dict inputs (``compute_vwap_signals``,
  ``calculate_20d_vol``, ``calculate_14d_atr_pct``): NaN propagation
  through these functions is real but the input shape is a nested
  container, not a scalar; the rejection contract is more nuanced
  (per-element validation) and is deferred to a follow-on cycle. The
  primary risk surface (the three audit-named functions plus the five
  scalar layers above) is fully covered here.

Test shape (three per function):

  1. NaN-input rejection: parametrize over EACH float input position;
     set that one position to ``float('nan')``; assert ``ValueError``
     with ``"NaN"`` in the message.

  2. Inf-input rejection: same shape, ``float('inf')`` and
     ``float('-inf')``; same assertion.

  3. Output finiteness for valid inputs: a small fixture set of
     well-formed inputs; assert the function returns only finite floats
     (no NaN, no Inf). For tuple-returning functions, every float in the
     tuple must be finite; ints/bools are skipped.

Tests are written WITHOUT regard to whether the production code can pass
today. The audit confirms it cannot --- these tests are RED and the
GREEN implementer adds the input guard at function entry.

Anti-pattern avoidance (from quant-test-writer AGENT.md):
  * No assertion on producer-computed values (rates, dollars, percents);
    output-finiteness tests assert SHAPE/IS-FINITE only.
  * No live API calls; all fixtures are synthetic and local.
  * ``pytest.approx`` is not used --- finiteness is exact (``math.isfinite``).
  * No mocking of the math engine.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

import math_engine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NAN = float("nan")
POS_INF = float("inf")
NEG_INF = float("-inf")
NON_FINITE_VALUES = [
    pytest.param(NAN, id="nan"),
    pytest.param(POS_INF, id="pos_inf"),
    pytest.param(NEG_INF, id="neg_inf"),
]


def _assert_nan_value_error(exc_info: pytest.ExceptionInfo) -> None:
    """
    Common assertion shape for the rejection contract: the function MUST
    raise ``ValueError`` AND the exception message MUST contain the
    substring ``"NaN"`` (case-sensitive --- the audit names the failure
    mode by that exact token, and the consistent string lets reviewer
    grep production logs for the rejection event).
    """
    assert "NaN" in str(exc_info.value), (
        f"Expected the ValueError message to contain the substring 'NaN' "
        f"to identify the failure mode; got: {exc_info.value!r}"
    )


def _iter_floats(value: Any):
    """
    Yield every float in a (possibly tuple) return value. ints and bools
    are skipped; ``compute_breakeven_update`` returns ``(int, bool, float)``
    so this filter is necessary for the output-finiteness assertion.
    """
    if isinstance(value, tuple):
        for v in value:
            # bool is a subclass of int; both are skipped before float check
            if isinstance(v, bool):
                continue
            if isinstance(v, int):
                continue
            if isinstance(v, float):
                yield v
    elif isinstance(value, float):
        yield value
    # else: not a float-bearing return; nothing to check


def _date_key(i: int) -> str:
    """Date string for synthetic MC history (sortable; matches the
    pattern in tests/math_engine/test_mc_gating_magic_numbers.py)."""
    base_day = 1 + i
    month = 1 + (base_day - 1) // 28
    day = 1 + (base_day - 1) % 28
    return f"2024-{month:02d}-{day:02d}"


def _build_alternating_history(
    num_days: int, spy_amp: float, ticker_amp: float, ticker: str = "AAA"
) -> dict:
    """Synthetic MC history of the shape run_monte_carlo expects."""
    history: dict = {}
    for i in range(num_days):
        sign = 1 if i % 2 == 0 else -1
        history[_date_key(i)] = {
            "SPY": {"daily_ret": sign * spy_amp},
            ticker: {"daily_ret": sign * ticker_amp},
        }
    return history


# ---------------------------------------------------------------------------
# compute_active_trailing_stop --- 4 float inputs
# ---------------------------------------------------------------------------
#
# Signature:
#   compute_active_trailing_stop(
#       symphony_vol, dynamic_multiplier, dynamic_min_stop,
#       para_armed, breakeven_locked, parabolic_squeeze_multiplier,
#   ) -> float
#
# Float positions: symphony_vol, dynamic_multiplier, dynamic_min_stop,
# parabolic_squeeze_multiplier. Bools (para_armed, breakeven_locked) are
# not float-input positions and are excluded from this sweep.

_ATS_VALID = dict(
    symphony_vol=1.5,
    dynamic_multiplier=1.0,
    dynamic_min_stop=0.2,
    para_armed=False,
    breakeven_locked=False,
    parabolic_squeeze_multiplier=0.5,
)
_ATS_FLOAT_POSITIONS = [
    "symphony_vol",
    "dynamic_multiplier",
    "dynamic_min_stop",
    "parabolic_squeeze_multiplier",
]


@pytest.mark.parametrize("bad", NON_FINITE_VALUES)
@pytest.mark.parametrize("position", _ATS_FLOAT_POSITIONS)
def test_compute_active_trailing_stop_rejects_non_finite_input(
    position: str, bad: float
) -> None:
    """
    REJECT policy: for each float position, when that position is
    NaN/Inf/-Inf the function must raise ValueError naming 'NaN'.
    """
    kwargs = dict(_ATS_VALID)
    kwargs[position] = bad
    with pytest.raises(ValueError) as exc_info:
        math_engine.compute_active_trailing_stop(**kwargs)
    _assert_nan_value_error(exc_info)


def test_compute_active_trailing_stop_output_is_finite_for_valid_inputs() -> None:
    """For a sweep of well-formed inputs, the returned float must be
    finite (no NaN, no Inf). This is a shape assertion --- no producer
    value is hardcoded; the test only checks isfinite()."""
    sweeps = [
        dict(symphony_vol=0.5, dynamic_multiplier=1.5, dynamic_min_stop=0.3,
             para_armed=False, breakeven_locked=False, parabolic_squeeze_multiplier=0.5),
        dict(symphony_vol=2.0, dynamic_multiplier=0.8, dynamic_min_stop=0.15,
             para_armed=True, breakeven_locked=False, parabolic_squeeze_multiplier=0.5),
        dict(symphony_vol=-0.1, dynamic_multiplier=1.0, dynamic_min_stop=0.2,  # vol<=0 fallback
             para_armed=False, breakeven_locked=True, parabolic_squeeze_multiplier=0.5),
        dict(symphony_vol=1.0, dynamic_multiplier=1.0, dynamic_min_stop=0.2,
             para_armed=True, breakeven_locked=True, parabolic_squeeze_multiplier=0.5),
    ]
    for kwargs in sweeps:
        result = math_engine.compute_active_trailing_stop(**kwargs)
        for f in _iter_floats(result):
            assert math.isfinite(f), (
                f"compute_active_trailing_stop returned non-finite {f!r} "
                f"for valid inputs {kwargs!r}"
            )


# ---------------------------------------------------------------------------
# compute_exit_confirmation --- 3 float inputs
# ---------------------------------------------------------------------------
#
# Signature:
#   compute_exit_confirmation(
#       armed, is_triggered, current_return, stop_trigger_level,
#       prob_underperforming, current_below_stop_count,
#   ) -> (int, bool)
#
# Float positions: current_return, stop_trigger_level, prob_underperforming.
# Bools (armed, is_triggered) and int (current_below_stop_count) are
# not float-input positions.
#
# Note: the function early-returns when (not armed) or is_triggered
# WITHOUT touching the floats; that branch is the "guard absoluteness"
# behavior pinned elsewhere. The REJECT policy must STILL fire on
# NaN/Inf inputs even on the early-return branch, because (a) the
# contract should be uniform (no "smuggled" NaN through the guard),
# and (b) a future caller bug that flips armed/is_triggered would
# otherwise see NaN reach the live-eval branch on the very next tick.
# The implementer enforces by validating ALL floats at function entry,
# BEFORE the guard check.

_EXC_VALID = dict(
    armed=True,
    is_triggered=False,
    current_return=-0.5,
    stop_trigger_level=-0.4,
    prob_underperforming=50.0,
    current_below_stop_count=0,
)
_EXC_FLOAT_POSITIONS = [
    "current_return",
    "stop_trigger_level",
    "prob_underperforming",
]


@pytest.mark.parametrize("bad", NON_FINITE_VALUES)
@pytest.mark.parametrize("position", _EXC_FLOAT_POSITIONS)
def test_compute_exit_confirmation_rejects_non_finite_input(
    position: str, bad: float
) -> None:
    kwargs = dict(_EXC_VALID)
    kwargs[position] = bad
    with pytest.raises(ValueError) as exc_info:
        math_engine.compute_exit_confirmation(**kwargs)
    _assert_nan_value_error(exc_info)


def test_compute_exit_confirmation_output_is_finite_for_valid_inputs() -> None:
    """The (int, bool) return contains no float --- so by the shape of
    _iter_floats this test is vacuous on the float-finiteness side, but
    we still assert the call runs without raising on valid input. The
    explicit isfinite loop is preserved so a future contract change
    that adds a float to the return value is still covered."""
    sweeps = [
        dict(armed=True, is_triggered=False, current_return=-1.0,
             stop_trigger_level=-0.5, prob_underperforming=30.0, current_below_stop_count=0),
        dict(armed=True, is_triggered=False, current_return=-0.4,
             stop_trigger_level=-0.5, prob_underperforming=80.0, current_below_stop_count=2),
        dict(armed=False, is_triggered=False, current_return=0.0,
             stop_trigger_level=0.0, prob_underperforming=50.0, current_below_stop_count=5),
        dict(armed=True, is_triggered=True, current_return=0.0,
             stop_trigger_level=0.0, prob_underperforming=50.0, current_below_stop_count=5),
    ]
    for kwargs in sweeps:
        result = math_engine.compute_exit_confirmation(**kwargs)
        # Return contract is (int, bool); confirm structurally:
        assert isinstance(result, tuple) and len(result) == 2
        for f in _iter_floats(result):
            assert math.isfinite(f), (
                f"compute_exit_confirmation returned non-finite {f!r} "
                f"for valid inputs {kwargs!r}"
            )


# ---------------------------------------------------------------------------
# run_monte_carlo --- 1 explicit scalar float input plus nested floats
# ---------------------------------------------------------------------------
#
# Signature:
#   run_monte_carlo(
#       holdings, historical_data, spy_today_return,
#       simulation_paths=..., neighbor_k=...,
#   ) -> float (probability in [0, 100])
#
# Float positions:
#   * ``spy_today_return`` --- the only top-level scalar float.
#   * Two nested float fields whose NaN/Inf the audit highlights:
#       holdings[i]["last_percent_change"]  ("Composer may emit null
#           -> upstream coerces to NaN")
#       holdings[i]["allocation"]            ("autotuner trial bug")
#     The contract: when ANY top-level scalar OR ANY in-scope nested
#     float is NaN/Inf, run_monte_carlo must raise ValueError naming 'NaN'.
#
# Out of scope (deferred): per-date entries inside ``historical_data``
# (NaN in a single day's daily_ret). The audit names this as a real risk
# but flagging it requires validating an arbitrarily-deep dict; the
# follow-on cycle for nested-container validation will cover it. This
# cycle focuses on the call-site scalars + the top-level holdings dicts.


def _valid_mc_inputs() -> dict:
    """Reusable well-formed MC input set (matches the existing
    fixture shape under tests/fixtures/math_engine/mc_gating/)."""
    return dict(
        holdings=[
            {"ticker": "AAA", "allocation": 1.0, "last_percent_change": 0.01}
        ],
        historical_data=_build_alternating_history(
            num_days=50, spy_amp=0.02, ticker_amp=0.03, ticker="AAA"
        ),
        spy_today_return=2.0,
        simulation_paths=500,  # smaller than default for test speed
        neighbor_k=150,
    )


@pytest.mark.parametrize("bad", NON_FINITE_VALUES)
def test_run_monte_carlo_rejects_non_finite_spy_today_return(bad: float) -> None:
    """The top-level scalar float input MUST be rejected when NaN/Inf."""
    inputs = _valid_mc_inputs()
    inputs["spy_today_return"] = bad
    with pytest.raises(ValueError) as exc_info:
        np.random.seed(42)  # deterministic in case of accidental MC entry
        math_engine.run_monte_carlo(**inputs)
    _assert_nan_value_error(exc_info)


@pytest.mark.parametrize("bad", NON_FINITE_VALUES)
def test_run_monte_carlo_rejects_non_finite_holding_last_percent_change(
    bad: float,
) -> None:
    """
    A single holding's ``last_percent_change`` set to NaN/Inf must be
    rejected. This is the audit's named failure mode:
    ``current_symphony_return = sum(...)`` becomes NaN/Inf and
    ``np.searchsorted`` then returns the wrong rank.
    """
    inputs = _valid_mc_inputs()
    inputs["holdings"][0]["last_percent_change"] = bad
    with pytest.raises(ValueError) as exc_info:
        np.random.seed(42)
        math_engine.run_monte_carlo(**inputs)
    _assert_nan_value_error(exc_info)


@pytest.mark.parametrize("bad", NON_FINITE_VALUES)
def test_run_monte_carlo_rejects_non_finite_holding_allocation(bad: float) -> None:
    """A single holding's ``allocation`` set to NaN/Inf must be rejected.

    Audit: a buggy autotuner trial that yields a NaN weight propagates
    through ``returns_matrix.dot(weights)`` to a NaN MC probability,
    which silently disables (or randomly mis-fires) the MC exit gate.
    """
    inputs = _valid_mc_inputs()
    inputs["holdings"][0]["allocation"] = bad
    with pytest.raises(ValueError) as exc_info:
        np.random.seed(42)
        math_engine.run_monte_carlo(**inputs)
    _assert_nan_value_error(exc_info)


def test_run_monte_carlo_output_is_finite_for_valid_inputs() -> None:
    """For well-formed inputs the probability output must be a finite real
    number; for the insufficient-history short-circuit it must be the
    out-of-band sentinel.

    Cluster 2 AC-2 update: run_monte_carlo no longer returns an in-band
    probability when history is below MC_MIN_HISTORY_DAYS — it returns the
    distinct out-of-band sentinel MC_INSUFFICIENT_HISTORY_SENTINEL (None). The
    sufficient-history regime still returns a finite float; the short-circuit
    regime returns the sentinel (which is intentionally NOT a finite number)."""
    np.random.seed(42)
    inputs = _valid_mc_inputs()
    result = math_engine.run_monte_carlo(**inputs)
    assert result is not None, (
        "run_monte_carlo returned the insufficient sentinel for a "
        "sufficient-history input; expected a real probability."
    )
    assert math.isfinite(float(result)), (
        f"run_monte_carlo returned non-finite {result!r} for valid inputs"
    )

    # Short-circuit regime (history < MC_MIN_HISTORY_DAYS) --- returns the
    # out-of-band insufficient sentinel, never an in-band (possibly non-finite)
    # value.
    short_inputs = _valid_mc_inputs()
    short_inputs["historical_data"] = _build_alternating_history(
        num_days=5, spy_amp=0.01, ticker_amp=0.01, ticker="AAA"
    )
    np.random.seed(42)
    result_short = math_engine.run_monte_carlo(**short_inputs)
    assert result_short is math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
        f"run_monte_carlo short-circuit returned {result_short!r}; expected the "
        f"out-of-band MC_INSUFFICIENT_HISTORY_SENTINEL "
        f"({math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL!r})."
    )


# ---------------------------------------------------------------------------
# compute_para_arm_decision --- 3 float inputs
# ---------------------------------------------------------------------------
#
# Signature:
#   compute_para_arm_decision(
#       current_return, prev_return, para_threshold, currently_armed,
#   ) -> (velocity, should_arm)
#
# Audit F1 explicitly names this function's NaN regime: ``velocity = NaN``
# silently suppresses arming. Included in the REJECT sweep.

_PARA_VALID = dict(
    current_return=1.0,
    prev_return=0.5,
    para_threshold=0.3,
    currently_armed=False,
)
_PARA_FLOAT_POSITIONS = ["current_return", "prev_return", "para_threshold"]


@pytest.mark.parametrize("bad", NON_FINITE_VALUES)
@pytest.mark.parametrize("position", _PARA_FLOAT_POSITIONS)
def test_compute_para_arm_decision_rejects_non_finite_input(
    position: str, bad: float
) -> None:
    kwargs = dict(_PARA_VALID)
    kwargs[position] = bad
    with pytest.raises(ValueError) as exc_info:
        math_engine.compute_para_arm_decision(**kwargs)
    _assert_nan_value_error(exc_info)


def test_compute_para_arm_decision_output_is_finite_for_valid_inputs() -> None:
    sweeps = [
        dict(current_return=1.0, prev_return=0.5, para_threshold=0.3, currently_armed=False),
        dict(current_return=-0.5, prev_return=0.0, para_threshold=0.3, currently_armed=True),
        dict(current_return=0.0, prev_return=0.0, para_threshold=0.0, currently_armed=False),
    ]
    for kwargs in sweeps:
        result = math_engine.compute_para_arm_decision(**kwargs)
        for f in _iter_floats(result):
            assert math.isfinite(f), (
                f"compute_para_arm_decision returned non-finite {f!r} "
                f"for valid inputs {kwargs!r}"
            )


# ---------------------------------------------------------------------------
# compute_time_squeeze_decay --- 1 float input
# ---------------------------------------------------------------------------
#
# Signature:
#   compute_time_squeeze_decay(time_ratio) -> (dynamic_multiplier, dynamic_min_stop)
#
# Audit F2 flags out-of-domain inputs (negative time_ratio) but the
# NaN/Inf case is a separate sub-regime: log10(1 + 9*NaN) = NaN
# silently corrupts both the multiplier and the floor.

@pytest.mark.parametrize("bad", NON_FINITE_VALUES)
def test_compute_time_squeeze_decay_rejects_non_finite_input(bad: float) -> None:
    with pytest.raises(ValueError) as exc_info:
        math_engine.compute_time_squeeze_decay(bad)
    _assert_nan_value_error(exc_info)


def test_compute_time_squeeze_decay_output_is_finite_for_valid_inputs() -> None:
    """time_ratio sweep over the documented [0.0, 1.0] domain."""
    for t in [0.0, 0.25, 0.5, 0.75, 1.0]:
        result = math_engine.compute_time_squeeze_decay(t)
        for f in _iter_floats(result):
            assert math.isfinite(f), (
                f"compute_time_squeeze_decay returned non-finite {f!r} "
                f"for time_ratio={t!r}"
            )


# ---------------------------------------------------------------------------
# compute_breakeven_update --- 3 float inputs
# ---------------------------------------------------------------------------
#
# Signature:
#   compute_breakeven_update(
#       current_return, symphony_vol, base_stop_level,
#       current_hold_ticks, currently_breakeven_locked, is_triggered,
#   ) -> (int, bool, float)
#
# Float positions: current_return, symphony_vol, base_stop_level. The
# returned ``stop_trigger_level`` is a float; NaN in any of the three
# inputs propagates to the float in the output and (via downstream
# comparisons) silently disables the breakeven floor.

_BE_VALID = dict(
    current_return=1.0,
    symphony_vol=1.0,
    base_stop_level=-0.5,
    current_hold_ticks=0,
    currently_breakeven_locked=False,
    is_triggered=False,
)
_BE_FLOAT_POSITIONS = ["current_return", "symphony_vol", "base_stop_level"]


@pytest.mark.parametrize("bad", NON_FINITE_VALUES)
@pytest.mark.parametrize("position", _BE_FLOAT_POSITIONS)
def test_compute_breakeven_update_rejects_non_finite_input(
    position: str, bad: float
) -> None:
    kwargs = dict(_BE_VALID)
    kwargs[position] = bad
    with pytest.raises(ValueError) as exc_info:
        math_engine.compute_breakeven_update(**kwargs)
    _assert_nan_value_error(exc_info)


def test_compute_breakeven_update_output_is_finite_for_valid_inputs() -> None:
    sweeps = [
        dict(current_return=1.0, symphony_vol=1.0, base_stop_level=-0.5,
             current_hold_ticks=0, currently_breakeven_locked=False, is_triggered=False),
        dict(current_return=3.0, symphony_vol=2.0, base_stop_level=0.3,
             current_hold_ticks=4, currently_breakeven_locked=False, is_triggered=False),
        dict(current_return=0.0, symphony_vol=0.4, base_stop_level=-0.1,
             current_hold_ticks=5, currently_breakeven_locked=True, is_triggered=False),
        dict(current_return=2.0, symphony_vol=1.5, base_stop_level=0.5,
             current_hold_ticks=10, currently_breakeven_locked=True, is_triggered=True),
    ]
    for kwargs in sweeps:
        result = math_engine.compute_breakeven_update(**kwargs)
        for f in _iter_floats(result):
            assert math.isfinite(f), (
                f"compute_breakeven_update returned non-finite {f!r} "
                f"for valid inputs {kwargs!r}"
            )


# ---------------------------------------------------------------------------
# compute_vwap_bleed_arm_threshold --- 2 float inputs
# ---------------------------------------------------------------------------
#
# Signature:
#   compute_vwap_bleed_arm_threshold(symphony_vol, bleed_multiplier) -> float

_BLEED_VALID = dict(symphony_vol=1.0, bleed_multiplier=1.0)
_BLEED_FLOAT_POSITIONS = ["symphony_vol", "bleed_multiplier"]


@pytest.mark.parametrize("bad", NON_FINITE_VALUES)
@pytest.mark.parametrize("position", _BLEED_FLOAT_POSITIONS)
def test_compute_vwap_bleed_arm_threshold_rejects_non_finite_input(
    position: str, bad: float
) -> None:
    kwargs = dict(_BLEED_VALID)
    kwargs[position] = bad
    with pytest.raises(ValueError) as exc_info:
        math_engine.compute_vwap_bleed_arm_threshold(**kwargs)
    _assert_nan_value_error(exc_info)


def test_compute_vwap_bleed_arm_threshold_output_is_finite_for_valid_inputs() -> None:
    sweeps = [
        dict(symphony_vol=0.5, bleed_multiplier=1.0),
        dict(symphony_vol=2.0, bleed_multiplier=2.0),
        dict(symphony_vol=0.0, bleed_multiplier=1.0),
        dict(symphony_vol=1.0, bleed_multiplier=0.5),
    ]
    for kwargs in sweeps:
        result = math_engine.compute_vwap_bleed_arm_threshold(**kwargs)
        for f in _iter_floats(result):
            assert math.isfinite(f), (
                f"compute_vwap_bleed_arm_threshold returned non-finite {f!r} "
                f"for valid inputs {kwargs!r}"
            )


# ---------------------------------------------------------------------------
# compute_vwap_breakdown_update --- 5 float inputs
# ---------------------------------------------------------------------------
#
# Signature:
#   compute_vwap_breakdown_update(
#       is_triggered, valid_vwap_weight, weighted_vwap_diff, safe_hwm,
#       current_return, vwap_cross_hwm_pct, vwap_bleed_arm_pct,
#       vwap_bleed_ticks_threshold, current_vwap_ticks,
#       current_vwap_bleed_ticks,
#   ) -> (int, int, bool, bool)
#
# Float positions: valid_vwap_weight, weighted_vwap_diff, safe_hwm,
# current_return, vwap_cross_hwm_pct, vwap_bleed_arm_pct.
# (vwap_bleed_ticks_threshold, current_vwap_ticks, current_vwap_bleed_ticks
# are ints; is_triggered is bool.)

_VBD_VALID = dict(
    is_triggered=False,
    valid_vwap_weight=0.8,
    weighted_vwap_diff=-0.2,
    safe_hwm=2.0,
    current_return=1.0,
    vwap_cross_hwm_pct=1.5,
    vwap_bleed_arm_pct=-1.0,
    vwap_bleed_ticks_threshold=3,
    current_vwap_ticks=0,
    current_vwap_bleed_ticks=0,
)
_VBD_FLOAT_POSITIONS = [
    "valid_vwap_weight",
    "weighted_vwap_diff",
    "safe_hwm",
    "current_return",
    "vwap_cross_hwm_pct",
    "vwap_bleed_arm_pct",
]


@pytest.mark.parametrize("bad", NON_FINITE_VALUES)
@pytest.mark.parametrize("position", _VBD_FLOAT_POSITIONS)
def test_compute_vwap_breakdown_update_rejects_non_finite_input(
    position: str, bad: float
) -> None:
    kwargs = dict(_VBD_VALID)
    kwargs[position] = bad
    with pytest.raises(ValueError) as exc_info:
        math_engine.compute_vwap_breakdown_update(**kwargs)
    _assert_nan_value_error(exc_info)


def test_compute_vwap_breakdown_update_output_is_finite_for_valid_inputs() -> None:
    sweeps = [
        dict(is_triggered=False, valid_vwap_weight=0.8, weighted_vwap_diff=-0.2,
             safe_hwm=2.0, current_return=1.0, vwap_cross_hwm_pct=1.5,
             vwap_bleed_arm_pct=-1.0, vwap_bleed_ticks_threshold=3,
             current_vwap_ticks=0, current_vwap_bleed_ticks=0),
        dict(is_triggered=True, valid_vwap_weight=0.8, weighted_vwap_diff=-0.2,
             safe_hwm=2.0, current_return=1.0, vwap_cross_hwm_pct=1.5,
             vwap_bleed_arm_pct=-1.0, vwap_bleed_ticks_threshold=3,
             current_vwap_ticks=2, current_vwap_bleed_ticks=1),
        dict(is_triggered=False, valid_vwap_weight=0.3, weighted_vwap_diff=-0.2,
             safe_hwm=2.0, current_return=1.0, vwap_cross_hwm_pct=1.5,
             vwap_bleed_arm_pct=-1.0, vwap_bleed_ticks_threshold=3,
             current_vwap_ticks=2, current_vwap_bleed_ticks=1),
    ]
    for kwargs in sweeps:
        result = math_engine.compute_vwap_breakdown_update(**kwargs)
        for f in _iter_floats(result):
            assert math.isfinite(f), (
                f"compute_vwap_breakdown_update returned non-finite {f!r} "
                f"for valid inputs {kwargs!r}"
            )
