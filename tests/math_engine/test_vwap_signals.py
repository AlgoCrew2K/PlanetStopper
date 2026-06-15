"""
Golden-fixture + property tests for the VWAP-signals layer of math_engine.py.

Scope (Gate-1 approved): NEW pure function math_engine.compute_vwap_signals,
extracted from alpha_bot_execution.py:472-484 (cycle 8 -- VWAP aggregation
extraction). This is a refactor-extraction cycle, not a new feature; the
inline producer (those 13 source lines on current main, post cycle-7 merge)
is the spec the GREEN phase must preserve.

Inline producer pinned verbatim (alpha_bot_execution.py:472-484 on main after
cycle 7 merge):

    # Pre-calculate True VWAP difference unconditionally so it can be logged
    # in the chart history
    weighted_vwap_diff = 0.0
    valid_vwap_weight = 0.0
    for h in holdings:
        h["ticker"] = h.get("working_ticker", h.get("ticker"))
        t = h["ticker"]
        alloc = h.get("allocation", 0.0)
        if t in live_vwaps:
            p = live_vwaps[t]["last_price"]
            v = live_vwaps[t]["vwap"]
            if v > 0:
                weighted_vwap_diff += alloc * ((p - v) / v)
                valid_vwap_weight += alloc

The `h["ticker"] = h.get("working_ticker", h.get("ticker"))` line is DATA
NORMALIZATION (working_ticker -> ticker fallback), NOT math. It STAYS in the
caller (alpha_bot_execution.py) and is OUT OF SCOPE for this cycle. The pure
function receives already-normalized holdings where each h["ticker"] is the
final ticker to look up in live_vwaps.

PROPOSED PURE INTERFACE (confirmed):

    def compute_vwap_signals(
        holdings: list[dict],         # each dict: {"ticker": str, "allocation": float, ...}
        live_vwaps: dict[str, dict],  # ticker -> {"last_price": float, "vwap": float}
    ) -> tuple[float, float]:
        '''
        Returns (weighted_vwap_diff, valid_vwap_weight).

        weighted_vwap_diff = 0.0
        valid_vwap_weight = 0.0
        for h in holdings:
            t = h["ticker"]
            alloc = h.get("allocation", 0.0)
            if t in live_vwaps:
                p = live_vwaps[t]["last_price"]
                v = live_vwaps[t]["vwap"]
                if v > 0:
                    weighted_vwap_diff += alloc * ((p - v) / v)
                    valid_vwap_weight += alloc
        return weighted_vwap_diff, valid_vwap_weight
        '''

LOAD-BEARING INVARIANTS:
  - GUARD `t in live_vwaps`: holdings whose ticker is not a key in live_vwaps
    contribute NOTHING to either accumulator (NOT a zero-vwap interpretation,
    NOT a "skip diff but count weight" interpretation).
  - GUARD `v > 0`: strict (0.0 fails). holdings with v <= 0 contribute
    NOTHING to either accumulator. The `valid_vwap_weight += alloc` line
    is INSIDE the `v > 0` branch -- a v <= 0 holding does NOT add to weight.
  - NO INPUT MUTATION: the pure function does NOT mutate the holdings list
    or any dict it contains (the inline producer's `h["ticker"] = ...` line
    is data prep that stays in the caller).
  - ALLOCATION DEFAULT: missing 'allocation' key -> 0.0 (h.get fallback).

CONTRACT - caller-normalized inputs (math layer trusts the caller):
  - holdings: list of dicts with at least 'ticker' key. 'allocation' is
    optional (defaults to 0.0 via .get). 'working_ticker' fallback is
    resolved by the caller BEFORE calling.
  - live_vwaps: dict keyed by ticker string, each value is a dict with
    'last_price' and 'vwap' float keys.

Tolerance policy (golden-fixture & property tests):
  - pytest.approx(rel=1e-9, abs=1e-12).
  - rel=1e-9 catches all algorithm errors (a wrong-by-1% impl fails by ~1e-2,
    eight orders of magnitude over the tolerance) while absorbing IEEE-754
    drift from chained mult/div/add. abs=1e-12 anchors the zero-comparison
    case (where rel cannot apply).

Provenance (HARD): every expected value in
tests/fixtures/math_engine/vwap_signals/*.json is DERIVED BY HAND from the
inline producer's formula and pinned in the fixture's 'derivation' field --
NOT captured from a current implementation (compute_vwap_signals does not
exist yet; this is RED).
"""

from __future__ import annotations

import ast
import copy
import json
import pathlib
from typing import Any

import pytest

import math_engine

FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "math_engine" / "vwap_signals"

# Tolerance: see module docstring. rel=1e-9 catches algorithm errors by
# eight orders of magnitude; abs=1e-12 anchors zero comparisons.
TOL_REL = 1e-9
TOL_ABS = 1e-12


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
def test_vwap_signals_matches_derived_expected(fixture_name: str, fixture: dict[str, Any]) -> None:
    """
    Every fixture's expected (weighted_vwap_diff, valid_vwap_weight) tuple
    is DERIVED BY HAND in the fixture's 'derivation' field. This test
    asserts compute_vwap_signals produces those values. The function does
    not exist yet -- this is RED.

    Assertions:
      - weighted_vwap_diff: pytest.approx with rel=1e-9, abs=1e-12.
      - valid_vwap_weight:  pytest.approx with rel=1e-9, abs=1e-12.
    """
    func_name = fixture["function"]
    assert func_name == "compute_vwap_signals", (
        f"{fixture_name}: only compute_vwap_signals is in scope for this cycle"
    )

    inputs = fixture["inputs"]
    expected = fixture["expected"]

    result = math_engine.compute_vwap_signals(
        holdings=inputs["holdings"],
        live_vwaps=inputs["live_vwaps"],
    )

    # Contract: 2-tuple. Defensive unpack with clear failure message if the
    # impl returns a different shape.
    assert isinstance(result, tuple), (
        f"Fixture {fixture_name}: expected a tuple, got {type(result).__name__}"
    )
    assert len(result) == 2, (
        f"Fixture {fixture_name}: expected a 2-tuple "
        f"(weighted_vwap_diff, valid_vwap_weight), got length {len(result)}"
    )
    weighted_vwap_diff, valid_vwap_weight = result

    assert weighted_vwap_diff == pytest.approx(
        expected["weighted_vwap_diff"], rel=TOL_REL, abs=TOL_ABS
    ), (
        f"Fixture {fixture_name}: weighted_vwap_diff mismatch. "
        f"Expected {expected['weighted_vwap_diff']} "
        f"(derivation: {fixture['derivation']}), got {weighted_vwap_diff}"
    )
    assert valid_vwap_weight == pytest.approx(
        expected["valid_vwap_weight"], rel=TOL_REL, abs=TOL_ABS
    ), (
        f"Fixture {fixture_name}: valid_vwap_weight mismatch. "
        f"Expected {expected['valid_vwap_weight']} "
        f"(derivation: {fixture['derivation']}), got {valid_vwap_weight}"
    )


# --- Property: empty holdings is exactly (0.0, 0.0) -------------------------


def test_empty_holdings_returns_exact_zero_zero() -> None:
    """
    Invariant: holdings=[] returns (0.0, 0.0) exactly. No float drift because
    no arithmetic occurs -- the loop never executes; both accumulators stay
    at their initial 0.0/0.0 literal.
    """
    diff, weight = math_engine.compute_vwap_signals(
        holdings=[],
        live_vwaps={"AAPL": {"last_price": 100.0, "vwap": 99.0}},
    )
    assert diff == 0.0, (
        f"Empty holdings: weighted_vwap_diff must be exactly 0.0 "
        f"(no arithmetic occurred), got {diff!r}"
    )
    assert weight == 0.0, (
        f"Empty holdings: valid_vwap_weight must be exactly 0.0 "
        f"(no arithmetic occurred), got {weight!r}"
    )


# --- Property: no qualifying holdings is exactly (0.0, 0.0) -----------------


@pytest.mark.parametrize(
    "holdings,live_vwaps",
    [
        # All tickers absent from live_vwaps
        (
            [
                {"ticker": "TSLA", "allocation": 0.5},
                {"ticker": "NVDA", "allocation": 0.5},
            ],
            {"AAPL": {"last_price": 100.0, "vwap": 99.0}},
        ),
        # Tickers present but all v == 0
        (
            [
                {"ticker": "AAPL", "allocation": 0.5},
                {"ticker": "SPY", "allocation": 0.5},
            ],
            {
                "AAPL": {"last_price": 100.0, "vwap": 0.0},
                "SPY": {"last_price": 200.0, "vwap": 0.0},
            },
        ),
        # Tickers present but all v < 0
        (
            [
                {"ticker": "AAPL", "allocation": 0.5},
            ],
            {"AAPL": {"last_price": 100.0, "vwap": -1.0}},
        ),
        # Mix of absent + v <= 0
        (
            [
                {"ticker": "TSLA", "allocation": 0.4},
                {"ticker": "AAPL", "allocation": 0.6},
            ],
            {"AAPL": {"last_price": 100.0, "vwap": 0.0}},
        ),
        # Empty live_vwaps with non-empty holdings
        (
            [
                {"ticker": "AAPL", "allocation": 1.0},
            ],
            {},
        ),
    ],
)
def test_no_qualifying_holdings_returns_exact_zero_zero(
    holdings: list[dict[str, Any]],
    live_vwaps: dict[str, dict[str, float]],
) -> None:
    """
    Invariant: if every holding is skipped (either ticker not in live_vwaps
    OR v <= 0), result is (0.0, 0.0) exactly.

    The `valid_vwap_weight += alloc` line is INSIDE the `v > 0` branch, so
    a non-qualifying holding contributes NOTHING to weight either. Catches
    an impl that incorrectly accumulates weight outside the guard.
    """
    diff, weight = math_engine.compute_vwap_signals(holdings=holdings, live_vwaps=live_vwaps)
    assert diff == 0.0, (
        f"No qualifying holdings: weighted_vwap_diff must be exactly 0.0, "
        f"got {diff!r}. Inputs: holdings={holdings}, live_vwaps={live_vwaps}"
    )
    assert weight == 0.0, (
        f"No qualifying holdings: valid_vwap_weight must be exactly 0.0 "
        f"(the `valid_vwap_weight += alloc` is INSIDE the `v > 0` branch -- "
        f"an impl that hoisted it outside the guard would fail here), "
        f"got {weight!r}. Inputs: holdings={holdings}, live_vwaps={live_vwaps}"
    )


# --- Property: weight equals sum of qualifying allocations ------------------


@pytest.mark.parametrize(
    "holdings,live_vwaps,expected_weight",
    [
        # All qualifying
        (
            [
                {"ticker": "A", "allocation": 0.1},
                {"ticker": "B", "allocation": 0.2},
                {"ticker": "C", "allocation": 0.3},
            ],
            {
                "A": {"last_price": 10.0, "vwap": 10.0},
                "B": {"last_price": 20.0, "vwap": 20.0},
                "C": {"last_price": 30.0, "vwap": 30.0},
            },
            0.6,  # 0.1 + 0.2 + 0.3
        ),
        # Some absent
        (
            [
                {"ticker": "A", "allocation": 0.1},
                {"ticker": "X", "allocation": 0.5},
                {"ticker": "B", "allocation": 0.2},
            ],
            {
                "A": {"last_price": 10.0, "vwap": 10.0},
                "B": {"last_price": 20.0, "vwap": 20.0},
            },
            0.3,  # 0.1 + 0.2 (X skipped)
        ),
        # Some with v == 0
        (
            [
                {"ticker": "A", "allocation": 0.1},
                {"ticker": "B", "allocation": 0.4},
            ],
            {
                "A": {"last_price": 10.0, "vwap": 10.0},
                "B": {"last_price": 20.0, "vwap": 0.0},
            },
            0.1,  # B skipped via v > 0
        ),
        # Some with v < 0
        (
            [
                {"ticker": "A", "allocation": 0.7},
                {"ticker": "B", "allocation": 0.1},
            ],
            {
                "A": {"last_price": 10.0, "vwap": -5.0},
                "B": {"last_price": 20.0, "vwap": 20.0},
            },
            0.1,  # A skipped via v > 0
        ),
        # Allocations that include zero
        (
            [
                {"ticker": "A", "allocation": 0.0},
                {"ticker": "B", "allocation": 0.5},
                {"ticker": "C", "allocation": 0.0},
            ],
            {
                "A": {"last_price": 10.0, "vwap": 10.0},
                "B": {"last_price": 20.0, "vwap": 20.0},
                "C": {"last_price": 30.0, "vwap": 30.0},
            },
            0.5,  # 0.0 + 0.5 + 0.0
        ),
    ],
)
def test_valid_weight_equals_sum_of_qualifying_allocations(
    holdings: list[dict[str, Any]],
    live_vwaps: dict[str, dict[str, float]],
    expected_weight: float,
) -> None:
    """
    Invariant: valid_vwap_weight equals the arithmetic sum of allocations of
    QUALIFYING holdings (ticker in live_vwaps AND v > 0). Computed
    independently in the test as a sanity reference.

    Catches an impl that:
      - sums ALL allocations (ignores the guards)
      - sums only the diff-contributing weight (would skip alloc=0)
      - mishandles missing-ticker vs missing-vwap in different ways
    """
    _, weight = math_engine.compute_vwap_signals(holdings=holdings, live_vwaps=live_vwaps)
    assert weight == pytest.approx(expected_weight, rel=TOL_REL, abs=TOL_ABS), (
        f"valid_vwap_weight mismatch. Expected {expected_weight} (sum of "
        f"qualifying allocations), got {weight}. Inputs: holdings={holdings}, "
        f"live_vwaps={live_vwaps}"
    )


# --- Property: diff equals reference formula sweep --------------------------


@pytest.mark.parametrize(
    "holdings,live_vwaps,expected_diff",
    [
        # Single qualifying
        (
            [{"ticker": "A", "allocation": 1.0}],
            {"A": {"last_price": 110.0, "vwap": 100.0}},
            0.1,  # 1.0 * (110-100)/100
        ),
        # Sign flip
        (
            [{"ticker": "A", "allocation": 1.0}],
            {"A": {"last_price": 90.0, "vwap": 100.0}},
            -0.1,
        ),
        # Two qualifying same sign
        (
            [
                {"ticker": "A", "allocation": 0.5},
                {"ticker": "B", "allocation": 0.5},
            ],
            {
                "A": {"last_price": 110.0, "vwap": 100.0},  # 0.5*0.10 = 0.05
                "B": {"last_price": 105.0, "vwap": 100.0},  # 0.5*0.05 = 0.025
            },
            0.075,
        ),
        # Skipped contributes nothing to diff
        (
            [
                {"ticker": "A", "allocation": 0.5},
                {"ticker": "X", "allocation": 100.0},  # absent, would dominate
            ],
            {"A": {"last_price": 110.0, "vwap": 100.0}},
            0.05,  # 0.5 * 0.10
        ),
        # Diff is 0 when all p == v
        (
            [
                {"ticker": "A", "allocation": 0.3},
                {"ticker": "B", "allocation": 0.7},
            ],
            {
                "A": {"last_price": 50.0, "vwap": 50.0},
                "B": {"last_price": 200.0, "vwap": 200.0},
            },
            0.0,
        ),
    ],
)
def test_weighted_vwap_diff_equals_reference_formula(
    holdings: list[dict[str, Any]],
    live_vwaps: dict[str, dict[str, float]],
    expected_diff: float,
) -> None:
    """
    Invariant: weighted_vwap_diff = sum over qualifying holdings of
        alloc * (p - v) / v
    where qualifying means (ticker in live_vwaps AND v > 0).

    The expected_diff column is the reference value, computed by hand for
    each row. This is a SWEEP across single/multi/sign/skipped/zero regimes
    -- a per-row golden, complementary to the JSON fixture suite.
    """
    diff, _ = math_engine.compute_vwap_signals(holdings=holdings, live_vwaps=live_vwaps)
    assert diff == pytest.approx(expected_diff, rel=TOL_REL, abs=TOL_ABS), (
        f"weighted_vwap_diff mismatch. Expected {expected_diff}, got {diff}. "
        f"Inputs: holdings={holdings}, live_vwaps={live_vwaps}"
    )


# --- Property: input mutation invariance ------------------------------------


def test_function_does_not_mutate_holdings_list_or_dicts() -> None:
    """
    HARD invariant: compute_vwap_signals is pure. It MUST NOT mutate the
    input holdings list (no append/pop/sort) NOR any holding dict inside it
    (no key write, no .pop, no .update).

    The inline producer's `h["ticker"] = h.get("working_ticker", h.get("ticker"))`
    is DATA NORMALIZATION that STAYS IN THE CALLER. The pure function must
    NOT carry that mutation across the extraction boundary -- the caller has
    already normalized 'ticker' before calling. An impl that copy-pasted the
    inline producer literally would mutate the input and fail this test.
    """
    holdings_before = [
        {"ticker": "AAPL", "allocation": 0.5, "working_ticker": "AAPL"},
        {"ticker": "SPY", "allocation": 0.3, "extra_field": "untouched"},
        {"ticker": "TSLA", "allocation": 0.2},  # not in live_vwaps -> skipped
    ]
    # Deep snapshot for comparison; deepcopy guarantees no shared references.
    holdings_snapshot = copy.deepcopy(holdings_before)

    live_vwaps = {
        "AAPL": {"last_price": 110.0, "vwap": 100.0},
        "SPY": {"last_price": 200.0, "vwap": 200.0},
    }
    live_vwaps_snapshot = copy.deepcopy(live_vwaps)

    math_engine.compute_vwap_signals(holdings=holdings_before, live_vwaps=live_vwaps)

    # holdings list identity preserved (no reassignment) -- but more
    # importantly, contents identical to pre-call snapshot.
    assert holdings_before == holdings_snapshot, (
        "compute_vwap_signals MUTATED the input holdings list or one of its "
        "dicts. The function must be pure: the caller is responsible for "
        "normalizing 'ticker' BEFORE calling. Likely cause: GREEN impl "
        "copy-pasted the inline producer's `h['ticker'] = h.get(...)` line. "
        f"Before: {holdings_snapshot!r}\nAfter: {holdings_before!r}"
    )
    assert live_vwaps == live_vwaps_snapshot, (
        "compute_vwap_signals MUTATED the input live_vwaps dict. The function "
        "must be pure -- it should only READ from live_vwaps. "
        f"Before: {live_vwaps_snapshot!r}\nAfter: {live_vwaps!r}"
    )


def test_function_does_not_inject_ticker_key_when_only_working_ticker_present() -> None:
    """
    Sharper guard against the inline producer's mutation creeping in: a
    holding dict that has 'working_ticker' but NO 'ticker' key was
    historically normalized by the producer with
    `h["ticker"] = h.get("working_ticker", h.get("ticker"))`. The PURE
    function MUST NOT do that -- the caller pre-normalizes, so the contract
    says holdings already have 'ticker'. If a caller passes a malformed
    holding (only working_ticker), the function should NOT silently inject
    'ticker'.

    We pass holdings with 'ticker' present (per contract) but ALSO with
    'working_ticker' present, and assert that the post-call dict has not
    been re-written. Combined with the broader mutation test above, this
    pins the data-normalization boundary.
    """
    h = {"ticker": "AAPL", "working_ticker": "AAPL_WT", "allocation": 0.5}
    holdings = [h]
    live_vwaps = {"AAPL": {"last_price": 110.0, "vwap": 100.0}}

    math_engine.compute_vwap_signals(holdings=holdings, live_vwaps=live_vwaps)

    # The 'ticker' field MUST remain 'AAPL' (per caller-provided value),
    # NOT be overwritten with 'AAPL_WT' from working_ticker.
    assert h["ticker"] == "AAPL", (
        f"compute_vwap_signals overwrote h['ticker'] with the working_ticker "
        f"fallback. That's the CALLER's responsibility (data normalization), "
        f"not the math layer's. h['ticker'] should remain 'AAPL', got "
        f"{h['ticker']!r}."
    )
    assert h.get("working_ticker") == "AAPL_WT", (
        f"compute_vwap_signals mutated h['working_ticker']. Got "
        f"{h.get('working_ticker')!r}, expected 'AAPL_WT'."
    )


# --- Property: determinism --------------------------------------------------


def test_function_is_pure_repeat_call_returns_identical_result() -> None:
    """
    Sanity: a pure function returns identical results when called twice with
    the same arguments. Catches an impl that stashes state in a module-level
    variable, uses an unseeded RNG, or has a hidden time-dependence.
    """
    holdings = [
        {"ticker": "AAPL", "allocation": 0.5},
        {"ticker": "SPY", "allocation": 0.3},
        {"ticker": "QQQ", "allocation": 0.2},
    ]
    live_vwaps = {
        "AAPL": {"last_price": 110.0, "vwap": 100.0},
        "SPY": {"last_price": 200.0, "vwap": 200.0},
        "QQQ": {"last_price": 90.0, "vwap": 100.0},
    }
    # deepcopy each call so prior-call mutations (if any -- another test
    # asserts no mutation, but be defensive) cannot influence the second.
    a = math_engine.compute_vwap_signals(
        holdings=copy.deepcopy(holdings), live_vwaps=copy.deepcopy(live_vwaps)
    )
    b = math_engine.compute_vwap_signals(
        holdings=copy.deepcopy(holdings), live_vwaps=copy.deepcopy(live_vwaps)
    )
    assert a == b, f"Non-deterministic output: {a} vs {b}"


# --- Property: return type contract (float, float) --------------------------


def test_return_type_contract_float_float() -> None:
    """
    Contract: returns (float, float). Strict `type() is` checks because:
      - weighted_vwap_diff and valid_vwap_weight are downstream serialized
        to SQLite (chart history) and JSON (Discord embed); numpy floats
        serialize inconsistently across SQLite/json modules.
      - bool is a subclass of int (which can be assigned to a float
        annotation); strict type() blocks accidental int/bool return.

    Same defensive pattern as cycles 3-7.
    """
    result = math_engine.compute_vwap_signals(
        holdings=[{"ticker": "AAPL", "allocation": 0.5}],
        live_vwaps={"AAPL": {"last_price": 110.0, "vwap": 100.0}},
    )
    assert isinstance(result, tuple) and len(result) == 2, f"Expected 2-tuple, got {result!r}"
    diff, weight = result

    assert type(diff) is float, (
        f"weighted_vwap_diff must be EXACTLY float, got "
        f"{type(diff).__name__} (numpy floats / ints forbidden; downstream "
        f"SQLite + JSON serialization relies on plain float)."
    )
    assert type(weight) is float, (
        f"valid_vwap_weight must be EXACTLY float, got "
        f"{type(weight).__name__} (numpy floats / ints forbidden)."
    )


# --- Property: type contract on empty-holdings path -------------------------


def test_return_type_contract_float_float_on_empty_holdings() -> None:
    """
    Edge: when the loop never executes (empty holdings), accumulators are
    returned at their initial 0.0 literals. Confirm those are plain float --
    catches an impl that initialized with `0` (int) instead of `0.0` (float),
    which would change the return type on the no-arithmetic path.
    """
    result = math_engine.compute_vwap_signals(holdings=[], live_vwaps={})
    assert isinstance(result, tuple) and len(result) == 2
    diff, weight = result
    assert type(diff) is float, (
        f"On empty-holdings path, weighted_vwap_diff must still be float "
        f"(initialize accumulators with 0.0, not 0). Got "
        f"{type(diff).__name__}={diff!r}."
    )
    assert type(weight) is float, (
        f"On empty-holdings path, valid_vwap_weight must still be float. "
        f"Got {type(weight).__name__}={weight!r}."
    )


# --- Constant-provenance scanner -------------------------------------------


def test_no_unnamed_magic_numbers_in_vwap_signals_path() -> None:
    """
    Project rule: 'No magic numbers in math_engine.py -- every constant
    named + source comment.'

    For compute_vwap_signals, the only numeric literals expected are:
      - 0.0 (accumulator initial values)
      - 0   (the `v > 0` strict-positive guard threshold)
    Both are STRUCTURAL anchors (universal zero / origin point), explicitly
    whitelisted. Any OTHER unnamed numeric literal indicates a magic number
    leaked in from the inline producer or a naive GREEN impl.

    Scans the AST of compute_vwap_signals for numeric literals. Each literal
    must either be:
      (a) a 'trivially structural' value: 0, 1, -1, 0.0, 1.0
      (b) accompanied by a named-constant assignment or an explanatory source
          comment on the same line.

    The function does not exist yet (RED); this scanner asserts the
    function exists in the module and then verifies its body. RED-phase
    behaviour: the `target is not None` assertion will fail, marking this
    test red until GREEN creates the function.
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    src_lines = source.splitlines()

    # Structural literals -- universal anchors that carry no domain meaning.
    # Python hashes int 0 and float 0.0 as the same dict/set key.
    STRUCTURAL = {
        0,  # universal zero / accumulator init / `> 0` threshold
        1,  # universal unit
        -1,  # unlikely but harmless
    }

    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "compute_vwap_signals":
            target = node
            break
    assert target is not None, (
        "compute_vwap_signals not found in math_engine.py "
        "(expected in RED; the function will be created in GREEN)"
    )

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
            if isinstance(val, bool):  # bool is a subclass of int
                continue
            if val in STRUCTURAL:
                continue
            if sub.lineno in named_literal_lines:
                continue
            if line_has_comment(sub.lineno):
                continue
            offenders.append((sub.lineno, val))

    assert not offenders, (
        "Unnamed magic numbers in compute_vwap_signals (project rule: every "
        "constant in math_engine.py must be named + source-commented). "
        f"Offenders (line, value): {offenders}. This function should only "
        "use 0.0 accumulator literals and the `v > 0` structural guard -- "
        "no domain constants exist for this extraction."
    )
