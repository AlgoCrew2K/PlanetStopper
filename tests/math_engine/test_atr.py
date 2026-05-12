"""
Golden-fixture + property tests for the 14-day ATR layer of math_engine.py.

Scope (Gate-1 approved): math_engine.calculate_14d_atr_pct -- the 14-day
Average True Range (as a percent of the most recent close), used by the
parabolic-ratchet layer for trailing-stop tightening.

Reuses the JSON fixture shape + synthetic-history builder pattern from the
volatility-scaling cycle (merge 6cfd4c5). Diverges only where ATR's OHLC
input shape requires it (new builder kinds: ohlc_constant, ohlc_linear,
ohlc_alternating_range, ohlc_constant_with_zero_final_close,
spy_only_daily_ret_alternating).

Provenance rule (HARD): every expected value in tests/fixtures/math_engine/
atr/*.json is mathematically DERIVED from the documented true-range formula
and the function's spec'd fallback behavior (see each fixture's 'derivation'
field), NOT captured from the current math_engine.py output.

True-range formula pinned here (math_engine.py:130):
    TR_i = max(H_i - L_i, |H_i - C_{i-1}|, |L_i - C_{i-1}|)
ATR-pct per ticker:
    atr_pct = mean(TR_1..TR_n) / C_n * 100.0   where n = num_days - 1
Portfolio aggregation (math_engine.py:144):
    portfolio_atr_pct = atr_pct_array.dot(weights)   -- NO normalization

Tolerance policy: pytest.approx(rel=1e-9, abs=1e-12). All fixture inputs are
constructed so the expected ATR is closed-form rational, so the only float
error sources are np.mean's accumulation and a single divide -- well below
1e-9 relative for the magnitudes here.

DERIVATION OMISSIONS (regimes from the gate-2 list not pinned as fixtures):
- NaN/None VALUE policy: math_engine.py guards only against MISSING KEYS
  ('high' not in day_data etc.), NOT against keys present with None values.
  A None value would propagate into max()/abs() and raise TypeError. The
  spec is therefore "missing key -> fallback; None value -> undefined".
  We pin the missing-key path in fixture 06; we do NOT pin the None-value
  path because the current behavior is "raises", which is not a stable
  contract worth pinning before the GREEN-phase implementer decides whether
  to harden it.
"""

from __future__ import annotations

import ast
import json
import pathlib
from typing import Any

import pytest

import math_engine

FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "math_engine" / "atr"

# --- Tolerance --------------------------------------------------------------

# rel=1e-9 catches any algorithmic divergence; abs=1e-12 lets exact-zero
# expected values match the numpy zero. A wrong impl will miss by orders of
# magnitude (e.g., range-only instead of true-range, or off-by-one on the
# lookback), not by 1 ulp.
APPROX_REL = 1e-9
APPROX_ABS = 1e-12


# --- Synthetic-history builder ---------------------------------------------


def _date_key(i: int) -> str:
    """ISO date string for synthetic day index i (lexicographically sortable)."""
    base_day = 1 + i
    month = 1 + (base_day - 1) // 28
    day = 1 + (base_day - 1) % 28
    return f"2024-{month:02d}-{day:02d}"


def _build_history(spec: dict[str, Any]) -> dict[str, dict[str, dict[str, float]]]:
    """
    Expand a fixture's compact historical_data_spec into the full nested dict
    shape that math_engine consumes:
        { date: { ticker: { "high": ..., "low": ..., "close": ..., "daily_ret": ... } } }

    Builder lives in the TEST file (not the fixture) so the fixture only
    declares INTENT (kind + parameters). Each branch's math is asserted via
    the fixture's 'derivation' string.
    """
    kind = spec["kind"]
    if kind == "empty":
        return {}

    num_days = spec["num_days"]
    tickers = spec.get("tickers", [])
    history: dict[str, dict[str, dict[str, float]]] = {}

    for i in range(num_days):
        date = _date_key(i)
        day: dict[str, dict[str, float]] = {}

        if kind == "ohlc_constant":
            high = spec["high"]
            low = spec["low"]
            close = spec["close"]
            for t in tickers:
                day[t] = {"high": high, "low": low, "close": close}

        elif kind == "ohlc_linear":
            # close_i = close_start + close_step * i
            # H_i = close_i + half_range, L_i = close_i - half_range
            close_i = spec["close_start"] + spec["close_step"] * i
            half = spec["half_range"]
            for t in tickers:
                day[t] = {
                    "high": close_i + half,
                    "low": close_i - half,
                    "close": close_i,
                }

        elif kind == "ohlc_alternating_range":
            # All closes constant. Even-index days use even_half_range,
            # odd-index days use odd_half_range.
            close = spec["close"]
            half = spec["even_half_range"] if i % 2 == 0 else spec["odd_half_range"]
            for t in tickers:
                day[t] = {
                    "high": close + half,
                    "low": close - half,
                    "close": close,
                }

        elif kind == "ohlc_constant_with_zero_final_close":
            # All intermediate days: close=spec["close"], high=spec["high"], low=spec["low"].
            # Final day: close=high=low=0.0 to trip the 'recent_close > 0' guard.
            if i == num_days - 1:
                for t in tickers:
                    day[t] = {"high": 0.0, "low": 0.0, "close": 0.0}
            else:
                for t in tickers:
                    day[t] = {
                        "high": spec["high"],
                        "low": spec["low"],
                        "close": spec["close"],
                    }

        elif kind == "spy_only_daily_ret_alternating":
            # ONLY SPY present, with daily_ret only (no OHLC). The holding's
            # ticker is intentionally absent on every day to trip ATR's
            # missing-data fallback path.
            amp = spec["spy_amplitude"]
            ret = amp if i % 2 == 0 else -amp
            day["SPY"] = {"daily_ret": ret}

        else:
            raise ValueError(f"Unknown historical_data_spec kind: {kind!r}")

        history[date] = day

    return history


# --- Fixture discovery ------------------------------------------------------


def _load_fixtures() -> list[tuple[str, dict[str, Any]]]:
    paths = sorted(FIXTURE_DIR.glob("*.json"))
    out: list[tuple[str, dict[str, Any]]] = []
    for p in paths:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        out.append((p.name, data))
    return out


FIXTURES = _load_fixtures()


# --- Golden-fixture parametrized test --------------------------------------


@pytest.mark.parametrize(
    "fixture_name,fixture",
    FIXTURES,
    ids=[name for name, _ in FIXTURES],
)
def test_atr_matches_derived_expected(fixture_name: str, fixture: dict[str, Any]) -> None:
    """
    Every fixture's expected value is DERIVED in the fixture's 'derivation'
    field. This test asserts calculate_14d_atr_pct produces that value.
    """
    func_name = fixture["function"]
    assert func_name == "calculate_14d_atr_pct", (
        f"{fixture_name}: only calculate_14d_atr_pct is in scope for this cycle"
    )

    holdings = fixture["inputs"]["holdings"]
    history = _build_history(fixture["inputs"]["historical_data_spec"])
    expected = fixture["expected"]

    actual = math_engine.calculate_14d_atr_pct(holdings, history)

    assert actual == pytest.approx(expected, rel=APPROX_REL, abs=APPROX_ABS), (
        f"Fixture {fixture_name}: expected {expected} (derivation: "
        f"{fixture['derivation']}), got {actual}"
    )


# --- Property test helpers --------------------------------------------------


def _make_ohlc_constant_range(
    num_days: int, close: float, half_range: float
) -> dict[str, dict[str, dict[str, float]]]:
    """num_days of constant-close, constant-range OHLC for ticker AAA."""
    h: dict[str, dict[str, dict[str, float]]] = {}
    for i in range(num_days):
        h[_date_key(i)] = {
            "AAA": {
                "high": close + half_range,
                "low": close - half_range,
                "close": close,
                "daily_ret": 0.0,
            },
            "SPY": {
                "high": close + half_range,
                "low": close - half_range,
                "close": close,
                "daily_ret": 0.0,
            },
        }
    return h


# --- Property tests --------------------------------------------------------


def test_atr_is_non_negative_for_arbitrary_history() -> None:
    """
    Invariant: ATR is a mean of non-negative max() terms divided by a
    positive close -- it cannot be negative. Sweep amplitudes to catch a
    sign-flip bug.
    """
    holdings = [{"ticker": "AAA", "allocation": 1.0}]
    for half_range in [0.0, 1e-6, 0.001, 0.1, 1.0, 10.0, 100.0]:
        history = _make_ohlc_constant_range(15, close=100.0, half_range=half_range)
        atr = math_engine.calculate_14d_atr_pct(holdings, history)
        assert atr >= 0.0, f"ATR went negative at half_range={half_range}: got {atr}"


def test_atr_is_monotonically_non_decreasing_in_range_amplitude() -> None:
    """
    Invariant: holding the close fixed (100) and the bar SHAPE fixed
    (symmetric around close), scaling the half-range up must yield a
    monotonically non-decreasing ATR. A wrong impl that clamped at an
    upper bound or applied a non-monotone transform would fail here.
    """
    holdings = [{"ticker": "AAA", "allocation": 1.0}]
    half_ranges = [0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0]
    atrs = [
        math_engine.calculate_14d_atr_pct(
            holdings, _make_ohlc_constant_range(15, close=100.0, half_range=hr)
        )
        for hr in half_ranges
    ]
    for i in range(1, len(atrs)):
        assert atrs[i] >= atrs[i - 1], (
            f"ATR decreased as half_range grew: hr[{i - 1}]={half_ranges[i - 1]} -> "
            f"atr={atrs[i - 1]}, hr[{i}]={half_ranges[i]} -> atr={atrs[i]}"
        )


def test_atr_clamps_via_vol_fallback_when_history_below_15_days() -> None:
    """
    Invariant pin: the documented short-circuit at len(valid_dates) < 15
    falls back to calculate_20d_vol. With 0..14 days of OHLC AND no
    daily_ret on those days, the vol fallback ALSO short-circuits (< 20)
    and returns 0.0. Pinned across the full range 0..14 to catch off-by-one
    bugs at the ATR boundary.
    """
    holdings = [{"ticker": "AAA", "allocation": 1.0}]
    for n in range(0, 15):
        history = _make_ohlc_constant_range(n, close=100.0, half_range=2.0)
        atr = math_engine.calculate_14d_atr_pct(holdings, history)
        assert atr == 0.0, (
            f"Expected clamp to 0.0 (via vol fallback) at n={n} days of history, got {atr}"
        )


def test_atr_uses_only_last_15_days_when_more_provided() -> None:
    """
    Property: with > 15 days, the function uses the LAST 15 (per the
    sorted(...)[-15:] slice). Construct a 30-day series whose first 15
    days are calm (range=0) and last 15 days have range=4. The result
    must equal the volatile-only ATR (4/100*100 = 4.0), not a diluted
    average. A wrong impl that used ALL 30 days would yield half that.
    """
    holdings = [{"ticker": "AAA", "allocation": 1.0}]
    history: dict[str, dict[str, dict[str, float]]] = {}
    # 15 calm days: close=100, no intraday range
    for i in range(15):
        history[_date_key(i)] = {
            "AAA": {"high": 100.0, "low": 100.0, "close": 100.0, "daily_ret": 0.0},
            "SPY": {"high": 100.0, "low": 100.0, "close": 100.0, "daily_ret": 0.0},
        }
    # 15 volatile days: close=100, high=102, low=98 -> range 4
    for i in range(15, 30):
        history[_date_key(i)] = {
            "AAA": {"high": 102.0, "low": 98.0, "close": 100.0, "daily_ret": 0.0},
            "SPY": {"high": 102.0, "low": 98.0, "close": 100.0, "daily_ret": 0.0},
        }
    atr = math_engine.calculate_14d_atr_pct(holdings, history)
    # Correct impl ([-15:] slice): TR_i = max(4, |102-100|=2, |98-100|=2) = 4
    # for each of 14 TR-eligible days (days 16..29 within the slice, since
    # day 15 within the slice is the first day with no prev close). avg_tr=4,
    # ATR% = 4/100*100 = 4.0.
    # Wrong impl (uses ALL 30 days): produces 29 TRs -- 14 of value 0 from
    # the calm prefix (days 1..14) plus 15 of value 4 from the volatile tail
    # and boundary. avg_tr = 60/29 ~= 2.069, ATR% ~= 2.069. The fixtures
    # distinguish: correct = 4.0, wrong = ~2.069.
    assert atr == pytest.approx(4.0, rel=APPROX_REL, abs=APPROX_ABS), (
        f"ATR on last-15-volatile of 30-day series: expected 4.0, got {atr}"
    )


def test_atr_lookback_slice_excludes_earlier_volatile_days() -> None:
    """
    Stronger lookback-slice property: construct a 30-day series where the
    first 15 days are HIGHLY volatile (range=20) and the last 15 are CALM
    (range=0). If the slice [-15:] is honored, ATR = 0 on the calm tail.
    A wrong impl that used ALL 30 days would yield a non-zero ATR.
    """
    holdings = [{"ticker": "AAA", "allocation": 1.0}]
    history: dict[str, dict[str, dict[str, float]]] = {}
    # 15 volatile days: close=100, high=110, low=90 -> range 20
    for i in range(15):
        history[_date_key(i)] = {
            "AAA": {"high": 110.0, "low": 90.0, "close": 100.0, "daily_ret": 0.0},
            "SPY": {"high": 110.0, "low": 90.0, "close": 100.0, "daily_ret": 0.0},
        }
    # 15 calm days: close=100, high=100, low=100 -> range 0
    for i in range(15, 30):
        history[_date_key(i)] = {
            "AAA": {"high": 100.0, "low": 100.0, "close": 100.0, "daily_ret": 0.0},
            "SPY": {"high": 100.0, "low": 100.0, "close": 100.0, "daily_ret": 0.0},
        }
    atr = math_engine.calculate_14d_atr_pct(holdings, history)
    # Last 15 days are all (H=100, L=100, C=100). All TRs = 0. avg_tr = 0.
    # ATR% = 0/100*100 = 0.
    # If the slice were ignored and all 30 were used, the first 14 TRs
    # would average ~20 and the result would be ~10.
    assert atr == pytest.approx(0.0, rel=APPROX_REL, abs=APPROX_ABS), (
        f"Lookback slice not honored: expected 0.0 from last-15-calm of 30, got {atr}"
    )


def test_atr_scales_linearly_with_allocation_for_single_holding() -> None:
    """
    Property: portfolio_atr_pct = atr_pct_array.dot(weights). For a SINGLE
    holding with allocation w, doubling w doubles the result -- there is
    NO normalization. A wrong impl that normalized allocations or that
    clamped weights would fail here.
    """
    history = _make_ohlc_constant_range(15, close=100.0, half_range=2.0)
    # Per-ticker ATR%: TR = max(4, |H-Cprev|=2, |L-Cprev|=2) = 4 for each of
    # 14 TR-eligible days. avg_tr = 4. ATR% = 4/100*100 = 4.0.
    base_atr = 4.0
    weights = [0.25, 0.5, 1.0, 2.0]
    atrs = [
        math_engine.calculate_14d_atr_pct([{"ticker": "AAA", "allocation": w}], history)
        for w in weights
    ]
    for w, a in zip(weights, atrs, strict=True):
        expected = w * base_atr
        assert a == pytest.approx(expected, rel=APPROX_REL, abs=APPROX_ABS), (
            f"Linearity in allocation broken at w={w}: expected {expected}, got {a}"
        )


def test_atr_aggregates_linearly_across_multiple_holdings() -> None:
    """
    Property: with two holdings AAA + BBB carrying allocations w_a, w_b on
    independent OHLC streams that each yield per-ticker ATR%=4.0, the
    portfolio ATR = w_a*4 + w_b*4. This catches an impl that normalized
    weights to sum to 1, or that averaged instead of dot-producting.
    """
    history: dict[str, dict[str, dict[str, float]]] = {}
    for i in range(15):
        history[_date_key(i)] = {
            "AAA": {"high": 102.0, "low": 98.0, "close": 100.0, "daily_ret": 0.0},
            "BBB": {"high": 102.0, "low": 98.0, "close": 100.0, "daily_ret": 0.0},
            "SPY": {"high": 102.0, "low": 98.0, "close": 100.0, "daily_ret": 0.0},
        }
    cases = [
        (0.5, 0.5, 4.0),  # canonical normalized -> 4.0
        (1.0, 1.0, 8.0),  # un-normalized -> 8.0 (catches normalize-to-1 bug)
        (0.3, 0.7, 4.0),  # normalized -> 4.0
        (2.0, 3.0, 20.0),  # over-allocated -> 20.0
    ]
    for w_a, w_b, expected in cases:
        holdings = [
            {"ticker": "AAA", "allocation": w_a},
            {"ticker": "BBB", "allocation": w_b},
        ]
        atr = math_engine.calculate_14d_atr_pct(holdings, history)
        assert atr == pytest.approx(expected, rel=APPROX_REL, abs=APPROX_ABS), (
            f"Aggregation broken at (w_a={w_a}, w_b={w_b}): expected {expected}, got {atr}"
        )


# --- Constant / magic-number provenance scan --------------------------------


def test_no_unnamed_magic_numbers_in_atr_path() -> None:
    """
    Project rule: 'No magic numbers in math_engine.py -- every constant named
    + source comment.'

    Scans the AST of calculate_14d_atr_pct for numeric literals. Each literal
    must either be:
      (a) a trivially structural value (0, 1, -1, 0.0 -- explicitly
          whitelisted with a documented reason below), or
      (b) accompanied by a named-constant assignment or an inline source
          comment on the same line.

    Today, calculate_14d_atr_pct uses unnamed literals:
      - 15 (the slice depth, line 112)
      - 15 (the short-circuit threshold, line 113)
      - 100.0 (decimal-to-percent scaler, line 147)
    This test SHOULD FAIL until the GREEN-phase implementer extracts them
    to module-level named constants (e.g., ATR_LOOKBACK_DAYS = 15, and
    reuses PCT_SCALAR which already exists at module level from the
    vol-scaling cycle). PCT_SCALAR (=100.0) was introduced in commit 1310a3d.

    Whitelist note: the structural set INTENTIONALLY excludes 15 and 100.0
    so this scanner catches them today.
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    src_lines = source.splitlines()

    # Trivially-structural literals that don't carry domain meaning.
    # Each entry MUST have a comment justifying why it is not a magic number.
    # Note: 0 and 0.0 hash-collide in a Python set; one entry covers both.
    STRUCTURAL = {
        0,  # numpy index zero / array init / count zero / clamp floor (covers 0.0)
        1,  # numpy single-axis / increment
        -1,  # last-element index
    }

    # Find the FunctionDef node for calculate_14d_atr_pct.
    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "calculate_14d_atr_pct":
            target = node
            break
    assert target is not None, "calculate_14d_atr_pct not found in math_engine.py"

    # Local-named-constant lines: any Assign where the value is a Constant
    # numeric -- accept those literals as "named".
    named_literal_lines: set[int] = set()
    for sub in ast.walk(target):
        if isinstance(sub, ast.Assign):
            for tgt in sub.targets:
                if isinstance(tgt, ast.Name) and isinstance(sub.value, ast.Constant):
                    named_literal_lines.add(sub.value.lineno)

    def line_has_comment(lineno: int) -> bool:
        if lineno - 1 >= len(src_lines):
            return False
        line = src_lines[lineno - 1]
        if "#" not in line:
            return False
        before, _, after = line.partition("#")
        return before.strip() != "" and after.strip() != ""

    offenders: list[tuple[int, Any]] = []
    for sub in ast.walk(target):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, int | float):
            val = sub.value
            if isinstance(val, bool):  # bool is subclass of int
                continue
            if val in STRUCTURAL:
                continue
            if sub.lineno in named_literal_lines:
                continue
            if line_has_comment(sub.lineno):
                continue
            offenders.append((sub.lineno, val))

    assert not offenders, (
        "Unnamed magic numbers in calculate_14d_atr_pct (project rule: every "
        "constant in math_engine.py must be named + source-commented). "
        f"Offenders (line, value): {offenders}. "
        "Fix in the GREEN phase by extracting module-level named constants "
        "(e.g., ATR_LOOKBACK_DAYS = 15  # 14 TRs + 1 prior close) and "
        "reusing the existing PCT_SCALAR (=100.0) introduced in the "
        "vol-scaling cycle (commit 1310a3d)."
    )
