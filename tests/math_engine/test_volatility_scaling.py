"""
Golden-fixture + property tests for the volatility-scaling layer of math_engine.py.

Scope (Gate-1 approved): math_engine.calculate_20d_vol — the 20-day historical
volatility used by the Guard Alpha scaling layer. The ATR variant
(calculate_14d_atr_pct) is intentionally OUT of scope for this RED cycle; it
will be covered alongside the parabolic-ratchet cycle which is the layer that
actually consumes ATR.

Provenance rule (HARD): every expected value in tests/fixtures/math_engine/
volatility_scaling/*.json is mathematically DERIVED from the documented formula
(see each fixture's 'derivation' field), NOT captured from the current
math_engine.py output. This is the project-wide harness pattern; future cycles
(parabolic ratchet, exit confirmation) will reuse the JSON shape + builder.

Tolerance policy: pytest.approx(rel=1e-9, abs=1e-12). The constructed input
sequences (alternating +/-A) produce closed-form analytic stdevs with no
floating-point rounding in the SAMPLES themselves; the only float error is
inside np.std's accumulation, which is well below 1e-9 relative.

DERIVATION OMISSIONS (regimes from the original list not pinned here):
- None. All 8 regimes are derivable and included. The originally-proposed
  sin()-shaped low/high-vol regimes were swapped for period-2 alternation,
  which gives exact closed-form stdev for any N; the regime intent
  (low-amplitude periodic vs high-amplitude periodic) is preserved.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
from typing import Any

import pytest

import math_engine

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "math_engine" / "volatility_scaling"
)

# --- Tolerance --------------------------------------------------------------

# rel=1e-9 catches any algorithmic divergence; abs=1e-12 lets exact-zero
# expected values match the numpy zero. Tighter than necessary on purpose:
# a wrong impl will miss by ORDERS of magnitude (e.g., dividing by N-1 vs N,
# or returning variance instead of std), not by 1 ulp.
APPROX_REL = 1e-9
APPROX_ABS = 1e-12


# --- Synthetic-history builder ---------------------------------------------


def _date_key(i: int) -> str:
    """ISO date string for synthetic day index i (just needs to sort)."""
    # 2024-01-01 + i days, naive — we only need lexicographic order.
    base_day = 1 + i
    month = 1 + (base_day - 1) // 28
    day = 1 + (base_day - 1) % 28
    return f"2024-{month:02d}-{day:02d}"


def _build_history(spec: dict[str, Any]) -> dict[str, dict[str, dict[str, float]]]:
    """
    Expand a fixture's compact historical_data_spec into the full nested dict
    shape that math_engine consumes:
        { date: { ticker: { "daily_ret": float, ... } } }

    Builder lives in the TEST file (not the fixture) so the fixture only
    declares INTENT (kind + parameters). Each branch's math is asserted via
    the fixture's 'derivation' string — if a builder bug ever drifts from a
    derivation, the test will fail.
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

        if kind == "constant_returns":
            ret = spec["daily_ret"]
            for t in tickers:
                day[t] = {"daily_ret": ret}

        elif kind == "alternating_returns":
            amp = spec["amplitude"]
            ret = amp if i % 2 == 0 else -amp
            for t in tickers:
                day[t] = {"daily_ret": ret}

        elif kind == "regime_shift":
            calm_days = spec["calm_days"]
            if i < calm_days:
                ret = spec["calm_ret"]
            else:
                amp = spec["vol_amplitude"]
                # Alternate starting at the first vol day so the vol half has
                # equal +/- counts when (num_days - calm_days) is even.
                vol_idx = i - calm_days
                ret = amp if vol_idx % 2 == 0 else -amp
            for t in tickers:
                day[t] = {"daily_ret": ret}

        elif kind == "spy_only_alternating":
            amp = spec["spy_amplitude"]
            ret = amp if i % 2 == 0 else -amp
            # ONLY SPY is present; the holding's ticker is intentionally absent.
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
def test_volatility_scaling_matches_derived_expected(
    fixture_name: str, fixture: dict[str, Any]
) -> None:
    """
    Every fixture's expected value is DERIVED in the fixture's 'derivation'
    field. This test asserts the function under test produces that value.
    """
    func_name = fixture["function"]
    assert func_name == "calculate_20d_vol", (
        f"{fixture_name}: only calculate_20d_vol is in scope for this cycle"
    )

    holdings = fixture["inputs"]["holdings"]
    history = _build_history(fixture["inputs"]["historical_data_spec"])
    expected = fixture["expected"]

    actual = math_engine.calculate_20d_vol(holdings, history)

    assert actual == pytest.approx(expected, rel=APPROX_REL, abs=APPROX_ABS), (
        f"Fixture {fixture_name}: expected {expected} (derivation: "
        f"{fixture['derivation']}), got {actual}"
    )


# --- Property tests --------------------------------------------------------


def _make_simple_history(num_days: int, amplitude: float) -> dict[str, dict[str, dict[str, float]]]:
    """Alternating +/-amplitude SPY + AAA, num_days long."""
    h: dict[str, dict[str, dict[str, float]]] = {}
    for i in range(num_days):
        ret = amplitude if i % 2 == 0 else -amplitude
        h[_date_key(i)] = {
            "AAA": {"daily_ret": ret},
            "SPY": {"daily_ret": ret},
        }
    return h


def test_volatility_is_non_negative_for_arbitrary_history() -> None:
    """Invariant: realized volatility is a stdev — it cannot be negative."""
    holdings = [{"ticker": "AAA", "allocation": 1.0}]
    for amp in [0.0, 1e-6, 0.001, 0.01, 0.1, 1.0, 10.0]:
        history = _make_simple_history(20, amp)
        vol = math_engine.calculate_20d_vol(holdings, history)
        assert vol >= 0.0, f"Volatility went negative at amp={amp}: got {vol}"


def test_volatility_is_monotonically_non_decreasing_in_amplitude() -> None:
    """
    Invariant: holding the SHAPE of the return series fixed (period-2
    alternation) and scaling the amplitude up, realized vol must be
    monotonically non-decreasing. A wrong impl that clamped at a hard upper
    bound or that applied a non-monotone transform would fail here.
    """
    holdings = [{"ticker": "AAA", "allocation": 1.0}]
    amps = [0.0, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25]
    vols = [math_engine.calculate_20d_vol(holdings, _make_simple_history(20, a)) for a in amps]
    for i in range(1, len(vols)):
        assert vols[i] >= vols[i - 1], (
            f"Vol decreased as amplitude grew: amp[{i - 1}]={amps[i - 1]} -> "
            f"vol={vols[i - 1]}, amp[{i}]={amps[i]} -> vol={vols[i]}"
        )


def test_volatility_is_zero_when_insufficient_history() -> None:
    """
    Invariant pin: the documented short-circuit at len(valid_dates) < 20.
    Pinned across the full range 0..19 to catch off-by-one (<= 20, <= 19,
    < 19 etc.) bugs and to guarantee no impl 'helpfully' computes a partial
    stdev when the contract says 0.0.
    """
    holdings = [{"ticker": "AAA", "allocation": 1.0}]
    for n in range(0, 20):
        history = _make_simple_history(n, 0.05)
        vol = math_engine.calculate_20d_vol(holdings, history)
        assert vol == 0.0, f"Expected clamp to 0.0 at n={n} days of history, got {vol}"


def test_volatility_uses_only_last_20_days_when_more_provided() -> None:
    """
    Property: with > 20 days, the function uses the LAST 20 (per the
    `sorted(...)[-20:]` slice). Construct a 40-day series whose first 20
    days are calm and last 20 days are alternating +/-0.04; the result
    must equal the alternating-only stdev (4.0), not a diluted average.
    """
    holdings = [{"ticker": "AAA", "allocation": 1.0}]
    history: dict[str, dict[str, dict[str, float]]] = {}
    # 20 calm days
    for i in range(20):
        history[_date_key(i)] = {
            "AAA": {"daily_ret": 0.0},
            "SPY": {"daily_ret": 0.0},
        }
    # 20 alternating +/-0.04 days
    for i in range(20, 40):
        ret = 0.04 if (i - 20) % 2 == 0 else -0.04
        history[_date_key(i)] = {
            "AAA": {"daily_ret": ret},
            "SPY": {"daily_ret": ret},
        }
    vol = math_engine.calculate_20d_vol(holdings, history)
    # If the function correctly slices to the last 20, expected stdev = 4.0.
    # If it uses ALL 40 (wrong), expected stdev = sqrt(40*16/40)/sqrt(2) = ~2.83.
    assert vol == pytest.approx(4.0, rel=APPROX_REL, abs=APPROX_ABS), (
        f"Lookback slice not honored: expected 4.0 from last-20 of 40, got {vol}"
    )


def test_volatility_scales_linearly_with_allocation_for_single_holding() -> None:
    """
    Property: for a SINGLE holding with allocation w on a returns series r,
    daily_returns = r * w * 100. Std scales linearly in w. (Note: allocation
    is NOT required to be in [0,1] for this property to hold — the function
    just dot-products.) A wrong impl that normalized allocations or that
    clamped weights would fail here.
    """
    history = _make_simple_history(20, 0.02)
    weights = [0.25, 0.5, 1.0, 2.0]
    vols = [
        math_engine.calculate_20d_vol([{"ticker": "AAA", "allocation": w}], history)
        for w in weights
    ]
    # Expected: w * 2.0 (since amplitude 0.02 -> base vol 2.0)
    for w, v in zip(weights, vols, strict=True):
        expected = abs(w) * 2.0
        assert v == pytest.approx(expected, rel=APPROX_REL, abs=APPROX_ABS), (
            f"Linearity in allocation broken at w={w}: expected {expected}, got {v}"
        )


# --- Constant / magic-number provenance scan --------------------------------


def test_no_unnamed_magic_numbers_in_volatility_scaling_path() -> None:
    """
    Project rule: 'No magic numbers in math_engine.py — every constant named
    + source comment.'

    Scans the AST of calculate_20d_vol for numeric literals. Each literal
    must either be:
      (a) a 'trivially structural' value (0, 1, -1, 0.0, 100.0 — explicitly
          whitelisted with a documented reason below), or
      (b) accompanied by a named-constant assignment or an explanatory
          source comment on the same line.

    Today, calculate_20d_vol uses the magic number 20 (lookback) and 100.0
    (decimal-to-percent scaler) without naming them. This test SHOULD FAIL
    until a follow-up implementer extracts them to module-level named
    constants with source comments.
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    src_lines = source.splitlines()

    # Trivially-structural literals that don't carry domain meaning.
    # Each entry MUST have a comment justifying why it is not a magic number.
    STRUCTURAL = {
        0,  # numpy index zero / array init
        1,  # numpy single-axis / increment
        -1,  # last-element index
        0.0,  # zero default / clamp floor (documented in the function)
    }

    # Find the FunctionDef node for calculate_20d_vol.
    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "calculate_20d_vol":
            target = node
            break
    assert target is not None, "calculate_20d_vol not found in math_engine.py"

    # Collect lines where ANY ast.Name is assigned a Constant — these are
    # candidates for 'named constants', though the project rule actually
    # wants module-level naming, not local. We treat any local assignment
    # of a numeric literal to a Name as 'named' too (e.g., `LOOKBACK = 20`
    # inside the function would still be acceptable).
    named_literal_lines: set[int] = set()
    for sub in ast.walk(target):
        if isinstance(sub, ast.Assign):
            for tgt in sub.targets:
                if isinstance(tgt, ast.Name) and isinstance(sub.value, ast.Constant):
                    named_literal_lines.add(sub.value.lineno)

    # Also accept literals appearing on a line that has an inline `#` comment
    # — the project rule allows "named OR source comment".
    def line_has_comment(lineno: int) -> bool:
        if lineno - 1 >= len(src_lines):
            return False
        line = src_lines[lineno - 1]
        # Strip strings to avoid false positives on `"x #"` literals.
        # Simple heuristic: split on '#' and accept if any non-empty token follows.
        # (math_engine.py has no `#` inside string literals in the target fn.)
        if "#" not in line:
            return False
        # Reject pure-whitespace-then-# (that's the line ABOVE pattern, but
        # we only care about INLINE comments on the literal's line).
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
        "Unnamed magic numbers in calculate_20d_vol (project rule: every "
        "constant in math_engine.py must be named + source-commented). "
        f"Offenders (line, value): {offenders}. "
        "Fix in the GREEN phase by extracting module-level named constants "
        "(e.g., LOOKBACK_DAYS = 20  # 20-day realized vol window) and adding "
        "a brief source comment citing the rationale."
    )


def test_no_unnamed_magic_numbers_appear_to_be_present_today() -> None:
    """
    Sanity guard for the scanner above: confirms the domain constants for
    the volatility-scaling path (LOOKBACK_DAYS and PCT_SCALAR) are present
    as MODULE-LEVEL named constants in math_engine.py, with their canonical
    values (20 and 100.0 respectively). This cross-checks that the magic-
    number scanner is not passing for the wrong reason (e.g., AST-walk
    skipped a node type or the constants were silently removed).

    Originally this test asserted the bare literals "20" and "100.0" existed
    inside the calculate_20d_vol function body. After the GREEN-phase
    extraction (refactor: extract magic numbers in calculate_20d_vol), those
    literals now live as module-level named constants — this test was rewritten
    accordingly (as the original comment anticipated: "both tests should be
    retired or rewritten" once the implementer renames them out).
    """
    assert hasattr(math_engine, "LOOKBACK_DAYS"), (
        "math_engine.LOOKBACK_DAYS not found — module-level constant was removed or renamed."
    )
    assert math_engine.LOOKBACK_DAYS == 20, (
        f"LOOKBACK_DAYS should be 20 (20-day realized-vol window), got {math_engine.LOOKBACK_DAYS}"
    )
    assert hasattr(math_engine, "PCT_SCALAR"), (
        "math_engine.PCT_SCALAR not found — module-level constant was removed or renamed."
    )
    assert math_engine.PCT_SCALAR == 100.0, (
        f"PCT_SCALAR should be 100.0 (decimal return -> percentage points), got {math_engine.PCT_SCALAR}"
    )
