"""
RED tests for the NaN/Inf rejection policy on list-of-dict math inputs.

==========================================================================
POLICY: REJECT (same policy as A3 scalar cycle — test_nan_policy.py)
==========================================================================

A3 added NaN/Inf rejection to 8 functions with scalar float inputs. A3
reviewer flagged 3 deferred functions and one partial gap where the same
failure mode (NaN silently propagates, disabling a guard comparison) exists
inside nested list-of-dict or dict-of-dict inputs:

  1. compute_vwap_signals(holdings, live_vwaps)
       Float fields in holdings:      allocation
       Float fields in live_vwaps:    last_price, vwap   (per-ticker)

  2. calculate_20d_vol(holdings, historical_data)
       Float fields in holdings:      allocation
       Float fields in historical_data: daily_ret         (per-date, per-ticker)

  3. calculate_14d_atr_pct(holdings, historical_data)
       Float fields in holdings:      allocation
       Float fields in historical_data: high, low, close  (per-date, per-ticker)

  PARTIAL GAP in run_monte_carlo:
       Already validates spy_today_return + per-holding last_percent_change
       and allocation.  Does NOT validate per-date historical_data entries.
       Float fields NOT yet validated: daily_ret           (per-date, per-ticker)

Failure mode (why REJECT, not SAFE-SENTINEL):
  NaN flowing into numpy dot-product or into a comparison expression produces
  a NaN output or silently evaluates False, which either disables an exit gate
  or produces a sentinel probability of NaN that renders the MC gate useless.
  REJECT (ValueError) surfaces the upstream data quality bug at the math
  boundary; app.py's minute scheduler restarts at the next tick, so fail-fast
  does not orphan a position.

Test shape (mirrors A3 scalar cycle):

  For each of the 3 deferred functions:
    (a) RED rejection test — parametrize over (function, field, bad_value).
        Each float field set to NaN/+Inf/-Inf in turn, others valid.
        Assert ValueError with "NaN" in the message.

    (b) Output-finiteness test — for well-formed synthetic inputs, the
        function must return a finite float (or tuple thereof).
        These use IDENTICAL finite inputs to the existing fixture-driven
        tests, so they MUST PASS today and CONTINUE passing after GREEN.

  For run_monte_carlo historical_data gap:
    A RED rejection test parametrized over (ticker_field, bad_value),
    setting historical_data[<date>][<ticker>][field] to NaN/+Inf/-Inf.
    Assert ValueError with "NaN" in the message.

Anti-pattern avoidance (from quant-test-writer AGENT.md):
  * No assertion on producer-computed values; output-finiteness tests
    assert math.isfinite only.
  * No live API calls.
  * No mocking of the math engine.
  * No exact float assertions on outputs — only shape/finiteness.
  * No module-level mutables shared across tests.

Float fields per function (implementer reference):
  compute_vwap_signals:
    holdings[i]["allocation"]          — multiplied into weighted sum
    live_vwaps[ticker]["last_price"]   — numerator of (p-v)/v
    live_vwaps[ticker]["vwap"]         — denominator; also the > 0 guard
  calculate_20d_vol:
    holdings[i]["allocation"]          — weight in dot product
    historical_data[date][ticker]["daily_ret"] — return value summed
  calculate_14d_atr_pct:
    holdings[i]["allocation"]          — weight in dot product
    historical_data[date][ticker]["high"]      — TR computation
    historical_data[date][ticker]["low"]       — TR computation
    historical_data[date][ticker]["close"]     — TR denominator + last_close
  run_monte_carlo (gap):
    historical_data[date][ticker]["daily_ret"] — builds returns_matrix
"""

from __future__ import annotations

import math

import numpy as np
import pytest

import math_engine

# ---------------------------------------------------------------------------
# Shared non-finite sentinels (mirrors test_nan_policy.py)
# ---------------------------------------------------------------------------

NAN = float("nan")
POS_INF = float("inf")
NEG_INF = float("-inf")

NON_FINITE = [
    pytest.param(NAN, id="nan"),
    pytest.param(POS_INF, id="pos_inf"),
    pytest.param(NEG_INF, id="neg_inf"),
]


def _assert_nan_value_error(exc_info: pytest.ExceptionInfo) -> None:
    """Assert that ValueError message contains the 'NaN' token (case-sensitive).

    Consistent with the A3 scalar rejection contract: the message token 'NaN'
    lets operators grep production logs for the boundary-rejection event without
    ambiguity about casing or synonyms.
    """
    assert "NaN" in str(exc_info.value), (
        f"Expected ValueError message to contain the substring 'NaN' "
        f"(matches A3 scalar rejection contract); got: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# Date-key helper (shared across all synthetic history builders)
# ---------------------------------------------------------------------------


def _date_key(i: int) -> str:
    """ISO date string for synthetic day index i (lexicographically sortable)."""
    base_day = 1 + i
    month = 1 + (base_day - 1) // 28
    day = 1 + (base_day - 1) % 28
    return f"2024-{month:02d}-{day:02d}"


# ===========================================================================
# Section 1: compute_vwap_signals
#
# Float fields:
#   holdings[i]["allocation"]
#   live_vwaps[ticker]["last_price"]
#   live_vwaps[ticker]["vwap"]
# ===========================================================================

# ---------------------------------------------------------------------------
# Canonical valid inputs for compute_vwap_signals
# Using a positive vwap so the > 0 guard passes and ALL fields participate
# in arithmetic — ensures any non-finite value actually reaches a computation.
# ---------------------------------------------------------------------------

_VWAP_TICKER = "AAPL"

_VWAP_VALID_HOLDINGS = [
    {"ticker": _VWAP_TICKER, "allocation": 0.5},
]

_VWAP_VALID_LIVE_VWAPS = {
    _VWAP_TICKER: {"last_price": 110.0, "vwap": 100.0},
}


@pytest.mark.parametrize("bad", NON_FINITE)
def test_compute_vwap_signals_rejects_non_finite_holding_allocation(
    bad: float,
) -> None:
    """
    NaN/Inf in holdings[i]["allocation"] must raise ValueError naming 'NaN'.

    Risk: allocation multiplies into the weighted sum. A NaN allocation
    produces NaN for weighted_vwap_diff which then flows into the VWAP
    breakdown gate comparison and silently disables it.
    """
    holdings = [{"ticker": _VWAP_TICKER, "allocation": bad}]
    with pytest.raises(ValueError) as exc_info:
        math_engine.compute_vwap_signals(holdings, _VWAP_VALID_LIVE_VWAPS)
    _assert_nan_value_error(exc_info)


@pytest.mark.parametrize("bad", NON_FINITE)
def test_compute_vwap_signals_rejects_non_finite_live_vwap_last_price(
    bad: float,
) -> None:
    """
    NaN/Inf in live_vwaps[ticker]["last_price"] must raise ValueError naming 'NaN'.

    Risk: last_price is the numerator of (p - v) / v. NaN last_price produces
    NaN weighted_vwap_diff regardless of the valid vwap denominator.
    """
    live_vwaps = {_VWAP_TICKER: {"last_price": bad, "vwap": 100.0}}
    with pytest.raises(ValueError) as exc_info:
        math_engine.compute_vwap_signals(_VWAP_VALID_HOLDINGS, live_vwaps)
    _assert_nan_value_error(exc_info)


@pytest.mark.parametrize("bad", NON_FINITE)
def test_compute_vwap_signals_rejects_non_finite_live_vwap_vwap(
    bad: float,
) -> None:
    """
    NaN/Inf in live_vwaps[ticker]["vwap"] must raise ValueError naming 'NaN'.

    Risk: vwap is both the > 0 guard AND the denominator of (p - v) / v.
    NaN vwap evaluates `NaN > 0` as False (silently skips the holding, hiding
    bad data) OR if the guard were absent, divides by NaN.
    Both outcomes violate the contract: bad data must be loud, not silent.
    """
    live_vwaps = {_VWAP_TICKER: {"last_price": 110.0, "vwap": bad}}
    with pytest.raises(ValueError) as exc_info:
        math_engine.compute_vwap_signals(_VWAP_VALID_HOLDINGS, live_vwaps)
    _assert_nan_value_error(exc_info)


def test_compute_vwap_signals_output_is_finite_for_valid_inputs() -> None:
    """For well-formed inputs the return (weighted_vwap_diff, valid_vwap_weight)
    must be a 2-tuple of finite floats.

    This test uses the same input shape as the fixture-driven tests in
    test_vwap_signals.py and MUST PASS both before and after GREEN.
    """
    holdings = [
        {"ticker": "AAPL", "allocation": 0.5},
        {"ticker": "SPY", "allocation": 0.5},
    ]
    live_vwaps = {
        "AAPL": {"last_price": 110.0, "vwap": 100.0},
        "SPY": {"last_price": 200.0, "vwap": 200.0},
    }
    result = math_engine.compute_vwap_signals(holdings, live_vwaps)
    assert isinstance(result, tuple) and len(result) == 2, (
        f"Expected 2-tuple from compute_vwap_signals, got {result!r}"
    )
    for val in result:
        assert math.isfinite(val), (
            f"compute_vwap_signals returned non-finite value {val!r} "
            f"for valid inputs: holdings={holdings}, live_vwaps={live_vwaps}"
        )


# ===========================================================================
# Section 2: calculate_20d_vol
#
# Float fields:
#   holdings[i]["allocation"]
#   historical_data[date][ticker]["daily_ret"]
# ===========================================================================

# ---------------------------------------------------------------------------
# Synthetic history builder for calculate_20d_vol
# 20 days minimum required (LOOKBACK_DAYS) to avoid the short-circuit 0.0 return.
# We need all fields to participate in arithmetic so a bad value reaches numpy.
# ---------------------------------------------------------------------------

_VOL_TICKER = "AAA"
_VOL_NUM_DAYS = 20  # exactly at the LOOKBACK_DAYS threshold so all days are used


def _build_vol_history(num_days: int = _VOL_NUM_DAYS, amplitude: float = 0.02) -> dict:
    """Alternating daily_ret history for ticker AAA + SPY fallback."""
    history: dict = {}
    for i in range(num_days):
        ret = amplitude if i % 2 == 0 else -amplitude
        history[_date_key(i)] = {
            _VOL_TICKER: {"daily_ret": ret},
            "SPY": {"daily_ret": ret},
        }
    return history


_VOL_VALID_HOLDINGS = [{"ticker": _VOL_TICKER, "allocation": 1.0}]


@pytest.mark.parametrize("bad", NON_FINITE)
def test_calculate_20d_vol_rejects_non_finite_holding_allocation(
    bad: float,
) -> None:
    """
    NaN/Inf in holdings[i]["allocation"] must raise ValueError naming 'NaN'.

    Risk: allocation is the weight in returns_matrix.dot(weights). NaN weight
    produces NaN daily_returns, and np.std(NaN-containing array) returns NaN
    — the reported vol is silently NaN, corrupting every downstream stop calc.
    """
    holdings = [{"ticker": _VOL_TICKER, "allocation": bad}]
    with pytest.raises(ValueError) as exc_info:
        math_engine.calculate_20d_vol(holdings, _build_vol_history())
    _assert_nan_value_error(exc_info)


@pytest.mark.parametrize("bad", NON_FINITE)
def test_calculate_20d_vol_rejects_non_finite_historical_daily_ret(
    bad: float,
) -> None:
    """
    NaN/Inf in any historical_data[date][ticker]["daily_ret"] must raise
    ValueError naming 'NaN'.

    Risk: a single NaN entry in returns_matrix propagates through the dot
    product to NaN daily_returns, then NaN vol, silently disabling vol scaling.
    Inject the bad value on the FIRST date so it is within the [-20:] window.
    """
    history = _build_vol_history()
    first_date = sorted(history.keys())[0]
    history[first_date][_VOL_TICKER]["daily_ret"] = bad
    with pytest.raises(ValueError) as exc_info:
        math_engine.calculate_20d_vol(_VOL_VALID_HOLDINGS, history)
    _assert_nan_value_error(exc_info)


def test_calculate_20d_vol_output_is_finite_for_valid_inputs() -> None:
    """For well-formed inputs the return must be a finite non-negative float.

    Uses the same alternating-return pattern as the fixture-driven tests in
    test_volatility_scaling.py. MUST PASS both before and after GREEN.
    """
    result = math_engine.calculate_20d_vol(_VOL_VALID_HOLDINGS, _build_vol_history())
    assert isinstance(result, float), (
        f"Expected float from calculate_20d_vol, got {type(result).__name__}"
    )
    assert math.isfinite(result), (
        f"calculate_20d_vol returned non-finite {result!r} for valid inputs"
    )
    assert result >= 0.0, (
        f"calculate_20d_vol returned negative value {result!r} — volatility "
        f"is a standard deviation and cannot be negative"
    )


# ===========================================================================
# Section 3: calculate_14d_atr_pct
#
# Float fields:
#   holdings[i]["allocation"]
#   historical_data[date][ticker]["high"]
#   historical_data[date][ticker]["low"]
#   historical_data[date][ticker]["close"]
# ===========================================================================

# ---------------------------------------------------------------------------
# Synthetic OHLC history for calculate_14d_atr_pct.
# ATR_LOOKBACK_DAYS = 15 days minimum; use exactly 15.
# Each bar: close=100, high=102, low=98 (constant range, positive close).
# ---------------------------------------------------------------------------

_ATR_TICKER = "AAA"
_ATR_NUM_DAYS = 15  # exactly at the ATR_LOOKBACK_DAYS threshold


def _build_atr_history(num_days: int = _ATR_NUM_DAYS) -> dict:
    """15 days of constant-OHLC history for ticker AAA.
    close=100, high=102, low=98 — all fields positive, all TRs = 4.
    """
    history: dict = {}
    for i in range(num_days):
        history[_date_key(i)] = {
            _ATR_TICKER: {
                "high": 102.0,
                "low": 98.0,
                "close": 100.0,
            },
            "SPY": {
                "high": 102.0,
                "low": 98.0,
                "close": 100.0,
                "daily_ret": 0.0,
            },
        }
    return history


_ATR_VALID_HOLDINGS = [{"ticker": _ATR_TICKER, "allocation": 1.0}]

# Float fields present in each OHLC bar that feed into True Range computation.
_ATR_OHLC_FIELDS = ["high", "low", "close"]


@pytest.mark.parametrize("bad", NON_FINITE)
def test_calculate_14d_atr_pct_rejects_non_finite_holding_allocation(
    bad: float,
) -> None:
    """
    NaN/Inf in holdings[i]["allocation"] must raise ValueError naming 'NaN'.

    Risk: allocation is the weight in atr_pct_array.dot(weights). NaN weight
    produces NaN portfolio_atr_pct which corrupts every trailing-stop width
    calc downstream.
    """
    holdings = [{"ticker": _ATR_TICKER, "allocation": bad}]
    with pytest.raises(ValueError) as exc_info:
        math_engine.calculate_14d_atr_pct(holdings, _build_atr_history())
    _assert_nan_value_error(exc_info)


@pytest.mark.parametrize("ohlc_field", _ATR_OHLC_FIELDS)
@pytest.mark.parametrize("bad", NON_FINITE)
def test_calculate_14d_atr_pct_rejects_non_finite_ohlc_field(
    bad: float,
    ohlc_field: str,
) -> None:
    """
    NaN/Inf in any historical_data[date][ticker][field] (high, low, close)
    must raise ValueError naming 'NaN'.

    Risk matrix:
      high:  participates in max(H-L, |H-Cprev|, |L-Cprev|). NaN high produces
             NaN TR; mean([NaN, ...]) = NaN; NaN / close = NaN vol.
      low:   same TR computation. NaN low = NaN TR chain.
      close: used as both Cprev in the next TR and as the denominator
             (recent_close) of avg_tr / recent_close. NaN close corrupts
             all subsequent TRs AND the final division.

    Injects the bad value on the FIRST date (index 0) within the 15-day
    window so it is guaranteed to be included in the slice used for computation.
    """
    history = _build_atr_history()
    first_date = sorted(history.keys())[0]
    history[first_date][_ATR_TICKER][ohlc_field] = bad
    with pytest.raises(ValueError) as exc_info:
        math_engine.calculate_14d_atr_pct(_ATR_VALID_HOLDINGS, history)
    _assert_nan_value_error(exc_info)


def test_calculate_14d_atr_pct_output_is_finite_for_valid_inputs() -> None:
    """For well-formed inputs the return must be a finite non-negative float.

    Uses the same constant-OHLC pattern as the fixture-driven tests in
    test_atr.py (ohlc_constant kind). MUST PASS both before and after GREEN.
    """
    result = math_engine.calculate_14d_atr_pct(_ATR_VALID_HOLDINGS, _build_atr_history())
    assert isinstance(result, float), (
        f"Expected float from calculate_14d_atr_pct, got {type(result).__name__}"
    )
    assert math.isfinite(result), (
        f"calculate_14d_atr_pct returned non-finite {result!r} for valid inputs"
    )
    assert result >= 0.0, (
        f"calculate_14d_atr_pct returned negative value {result!r} — ATR is "
        f"a mean of non-negative TR terms and cannot be negative"
    )


# ===========================================================================
# Section 4: run_monte_carlo — historical_data gap
#
# A3 already validates spy_today_return + per-holding last_percent_change
# and allocation. This section covers the per-date historical_data entries
# which are NOT yet validated:
#   historical_data[date][ticker]["daily_ret"]
#
# The gap: daily_ret values feed directly into returns_matrix and then into
# the numpy dot product.  NaN in any cell propagates to NaN sim results and
# ultimately NaN probability, which renders the MC exit gate useless (NaN
# fails all comparisons, either silently allowing or blocking every exit).
# ===========================================================================

_MC_TICKER = "AAA"
# Eligible-sufficient history length (team-lead Ruling 2, AC-4): run_monte_carlo's
# sufficiency guard counts days remaining after the early-window exclusion, so a
# history must have >= MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1) raw days
# (+ a margin) to run the real MC path.
_MC_NUM_DAYS = math_engine.MC_MIN_HISTORY_DAYS + (math_engine.MC_VOL_WINDOW_DAYS - 1) + 10


def _build_mc_history(
    num_days: int = _MC_NUM_DAYS, spy_amp: float = 0.02, ticker_amp: float = 0.03
) -> dict:
    """Alternating daily_ret history with SPY + AAA for run_monte_carlo."""
    history: dict = {}
    for i in range(num_days):
        sign = 1 if i % 2 == 0 else -1
        history[_date_key(i)] = {
            "SPY": {"daily_ret": sign * spy_amp},
            _MC_TICKER: {"daily_ret": sign * ticker_amp},
        }
    return history


_MC_VALID_HOLDINGS = [{"ticker": _MC_TICKER, "allocation": 1.0, "last_percent_change": 0.01}]


@pytest.mark.parametrize("bad", NON_FINITE)
def test_run_monte_carlo_rejects_non_finite_historical_data_ticker_daily_ret(
    bad: float,
) -> None:
    """
    NaN/Inf in historical_data[date][ticker]["daily_ret"] must raise
    ValueError naming 'NaN'.

    Injects the bad value on the FIRST date in the history (within the
    kNN candidate pool) for the holding's ticker. This is a partial gap
    in the A3 validation: per-holding scalars are already guarded; the
    per-date per-ticker daily_ret inside historical_data is not.

    Risk: returns_matrix[i, j] is assigned from historical_data[date]
    [ticker]["daily_ret"]. A NaN cell propagates through dot(weights) to
    NaN nearest_day_returns, and np.random.choice then builds a NaN
    sim_results array. np.searchsorted on a NaN-containing sorted array
    has undefined behavior and can return wrong ranks — the MC probability
    becomes NaN, disabling the gate entirely.
    """
    history = _build_mc_history()
    first_date = sorted(history.keys())[0]
    history[first_date][_MC_TICKER]["daily_ret"] = bad
    holdings = [{"ticker": _MC_TICKER, "allocation": 1.0, "last_percent_change": 0.01}]
    with pytest.raises(ValueError) as exc_info:
        np.random.seed(42)  # deterministic in case of accidental MC entry
        math_engine.run_monte_carlo(
            holdings=holdings,
            historical_data=history,
            spy_today_return=2.0,
            simulation_paths=200,  # small for test speed
            neighbor_k=10,
        )
    _assert_nan_value_error(exc_info)


@pytest.mark.parametrize("bad", NON_FINITE)
def test_run_monte_carlo_rejects_non_finite_historical_data_spy_daily_ret(
    bad: float,
) -> None:
    """
    NaN/Inf in historical_data[date]["SPY"]["daily_ret"] must raise
    ValueError naming 'NaN'.

    SPY daily_ret is used to build the spy_returns array for kNN distance
    computation AND for the spy_vols rolling-std window.  NaN in spy_returns
    propagates through np.std and the Euclidean distance formula, producing
    NaN distances and corrupting the entire kNN neighbor selection.
    """
    history = _build_mc_history()
    first_date = sorted(history.keys())[0]
    history[first_date]["SPY"]["daily_ret"] = bad
    holdings = [{"ticker": _MC_TICKER, "allocation": 1.0, "last_percent_change": 0.01}]
    with pytest.raises(ValueError) as exc_info:
        np.random.seed(42)
        math_engine.run_monte_carlo(
            holdings=holdings,
            historical_data=history,
            spy_today_return=2.0,
            simulation_paths=200,
            neighbor_k=10,
        )
    _assert_nan_value_error(exc_info)


def test_run_monte_carlo_output_is_finite_after_historical_data_guard_for_valid_inputs() -> None:
    """For well-formed historical_data the MC probability must be a finite float.

    Uses the same alternating-history shape as test_nan_policy.py's
    _valid_mc_inputs helper. MUST PASS both before and after GREEN.
    """
    holdings = _MC_VALID_HOLDINGS
    history = _build_mc_history()
    np.random.seed(42)
    result = math_engine.run_monte_carlo(
        holdings=holdings,
        historical_data=history,
        spy_today_return=2.0,
        simulation_paths=200,
        neighbor_k=10,
    )
    assert math.isfinite(float(result)), (
        f"run_monte_carlo returned non-finite {result!r} for valid inputs"
    )
