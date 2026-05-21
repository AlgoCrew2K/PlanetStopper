"""
RED-phase tests for the dashboard "tape" chart overlay emission (Changes A & B).

Cycle goal
----------
The intraday tape chart needs two overlay lines — Breakeven and VWAP — but the
engine does not currently emit those values into the per-symphony
``chart_history`` rows it appends in ``alpha_bot_execution.main()``
(``alpha_bot_execution.py`` ~line 1379). Without the keys in the row dict the
frontend overlay datasets have no data to draw.

Change A — Breakeven overlay
    The chart_history row dict gains a key ``"breakeven"`` whose value is the
    named constant ``BREAKEVEN_RETURN_AXIS_VALUE`` (0.0 — breakeven on an
    entry-relative return axis) when the symphony's ``breakeven_locked`` state
    is True for that tick, else ``None``.

Change B — VWAP overlay
    The chart_history row dict gains a key ``"vwap"`` whose value is the
    derived chart quantity ``current_return - (weighted_vwap_diff * 100)``.
    Rationale (validated by quant-risk-researcher): ``current_return`` is
    price-vs-entry on the chart's return axis; ``weighted_vwap_diff`` (a
    fraction) is price-vs-VWAP; subtracting ``weighted_vwap_diff * 100``
    re-bases the VWAP onto the entry-relative return axis.

Test strategy
-------------
These tests drive the REAL ``alpha_bot_execution.main()`` end-to-end with the
math engine UNMOCKED (only network, clock, sleep and the DB namespace are
stubbed) and capture the dict passed to ``database.save_chart_history``. This
is the lowest-fidelity-loss way to pin a value produced deep inside ``main()``
without re-implementing the engine.

The harness (fixtures, payload builders, fixed clock) mirrors
``tests/execution/test_main_pipeline.py`` so the two suites stay consistent.

Fixture-derivation rule (project memory ``feedback_no_hardcoded_test_values``)
-----------------------------------------------------------------------------
The VWAP overlay is a NEW derived chart quantity. Its expected value is
NEVER hardcoded as a literal — every assertion derives the expected number
from the controlled fixture inputs (last_price, vwap, allocation,
last_percent_change) by re-applying the documented formula in the test body.
A test that pinned a precomputed float would pass against a wrong producer.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import alpha_bot_execution

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=_ET)  # Wed 11:30 ET
except Exception:  # pragma: no cover -- tzdata missing
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=timezone(timedelta(hours=-4)))


# Shape-only identifiers — the engine only key-matches these.
_SYMPHONY_ID = "sym-chart-overlay-001"
_ACTUAL_SYMPHONY_ID = "actual-sym-chart-overlay-001"
_ACCOUNT_ID = "acct-chart-overlay-001"
_SYMPHONY_NAME = "Chart Overlay Test Symphony"
_TICKER = "SPY"

# Second symphony for the cross-loop bleed test.
_SYMPHONY_ID_2 = "sym-chart-overlay-002"
_ACTUAL_SYMPHONY_ID_2 = "actual-sym-chart-overlay-002"
_SYMPHONY_NAME_2 = "Chart Overlay Test Symphony 2"

FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "math"

# Tolerance for derived-float comparisons. The VWAP overlay value is a chain
# of one multiply + one subtract on values already carrying IEEE-754 drift
# from the engine's own arithmetic. rel=1e-9 is eight orders of magnitude
# tighter than any plausible algorithm error (a wrong formula misses by
# whole percentage points) yet absorbs that benign drift; abs=1e-12 anchors
# the case where the derived expectation is exactly 0.0.
TOL_REL = 1e-9
TOL_ABS = 1e-12


# ---------------------------------------------------------------------------
# Payload builders (mirror test_main_pipeline.py)
# ---------------------------------------------------------------------------


def _make_symphony_payload(
    last_percent_change: float,
    symphony_id: str = _SYMPHONY_ID,
    actual_symphony_id: str = _ACTUAL_SYMPHONY_ID,
    name: str = _SYMPHONY_NAME,
):
    """Composer-shaped symphony dict for the fan-out loop.

    ``current_return`` inside ``main()`` is ``last_percent_change * 100``.
    """
    return {
        "id": symphony_id,
        "symphony_id": actual_symphony_id,
        "name": name,
        "last_percent_change": last_percent_change,
        "current_value": 10000.0,
        "holdings": [
            {"ticker": _TICKER, "allocation": 1.0},
        ],
    }


def _make_minimal_history(current_date_str: str):
    """Minimal Alpaca-shaped history so MC + 20d-vol don't blow up."""
    return {
        current_date_str: {
            "SPY": {"c": 500.0, "daily_ret": 0.001, "high": 501.0, "low": 499.0, "close": 500.0}
        }
    }


def _make_vwap_payload(last_price: float, vwap: float):
    """``fetch_intraday_vwaps`` -> ``{ticker: {"vwap": v, "last_price": p}}``.

    ``last_price`` and ``vwap`` are kept SEPARATE so the test controls the
    sign and magnitude of ``weighted_vwap_diff = (p - v) / v``.
    """
    return {_TICKER: {"vwap": vwap, "last_price": last_price}}


def _seed_state(
    *,
    symphony_id: str = _SYMPHONY_ID,
    armed: bool = False,
    hwm: float = 0.0,
    breakeven_locked: bool = False,
    extra_symphonies: dict | None = None,
    **extras,
):
    """Pre-seeded bot_state dict for the fixture symphony.

    ``breakeven_locked`` is set directly on the seeded symphony state. The
    breakeven-lock LATCHING INVARIANT (``math_engine.compute_breakeven_update``)
    means that once seeded True it stays True for the cycle regardless of the
    activation math — which deterministically pins the chart row's
    ``breakeven`` value without the test having to reverse-engineer the
    volatility-driven activation threshold.
    """
    base = {
        "date": _FIXED_ET.strftime("%Y-%m-%d"),
        "last_execution_mode": False,
        symphony_id: {
            "high_water_mark": hwm,
            "shadow_hwm": hwm,
            "prev_return": 0.0,
            "armed": armed,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,
            "mc_history": [],
            "below_stop_count": 0,
            "above_tp_count": 0,
            "vwap_ticks": 0,
            "vwap_bleed_ticks": 0,
            "breakeven_locked": breakeven_locked,
            "hwm_hold_ticks": 0,
        },
    }
    base[symphony_id].update(extras)
    if extra_symphonies:
        base.update(extra_symphonies)
    return base


@pytest.fixture
def patched_environment():
    """Bundle every patch ``main()`` needs; yield the MagicMocks.

    Mirrors ``test_main_pipeline.py::patched_environment``. The math engine is
    NOT mocked — overlay values must come from the real producer.
    """
    with (
        patch.object(alpha_bot_execution, "database") as mock_db,
        patch.object(alpha_bot_execution, "reporting"),
        patch.object(alpha_bot_execution, "fetch_symphony_stats") as mock_fetch_sym,
        patch.object(alpha_bot_execution, "fetch_alpaca_history") as mock_fetch_hist,
        patch.object(alpha_bot_execution, "fetch_intraday_vwaps") as mock_fetch_vwap,
        patch.object(alpha_bot_execution, "get_current_et", return_value=_FIXED_ET),
        patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]),
        patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-composer-key"),
        patch.object(alpha_bot_execution, "ALPACA_KEY", "test-alpaca-key"),
        patch.object(alpha_bot_execution, "LIVE_EXECUTION", False),
        patch.object(alpha_bot_execution.time, "sleep"),
        patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]),
    ):
        mock_db.acquire_lock.return_value = True
        mock_db.load_state.return_value = {}
        mock_db.load_chart_history.return_value = {
            "date": _FIXED_ET.strftime("%Y-%m-%d"),
            "symphonies": {},
        }
        mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
        mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
        mock_db.wipe_transient_state.side_effect = lambda s: s

        mock_fetch_sym.return_value = []
        mock_fetch_hist.return_value = _make_minimal_history(_FIXED_ET.strftime("%Y-%m-%d"))
        mock_fetch_vwap.return_value = {}

        yield {
            "db": mock_db,
            "fetch_symphony_stats": mock_fetch_sym,
            "fetch_alpaca_history": mock_fetch_hist,
            "fetch_intraday_vwaps": mock_fetch_vwap,
        }


# ---------------------------------------------------------------------------
# Helper: extract the latest chart_history row appended for a symphony
# ---------------------------------------------------------------------------


def _latest_chart_row(env, symphony_id: str = _SYMPHONY_ID) -> dict:
    """Return the last chart_history row dict appended for ``symphony_id``.

    ``main()`` calls ``database.save_chart_history(chart_history)`` once the
    fan-out loop completes. We read the dict from that call's args and pull
    the per-symphony row list. Fails loudly if the call never happened or the
    symphony has no rows — those are pipeline-abort signals, not silent zeros.
    """
    save_calls = env["db"].save_chart_history.call_args_list
    assert save_calls, (
        "save_chart_history was never called — the pipeline aborted before "
        "the chart-history write-back. No overlay row to assert against."
    )
    chart_history = save_calls[-1].args[0]
    assert isinstance(chart_history, dict) and "symphonies" in chart_history, (
        f"save_chart_history received an unexpected shape: {chart_history!r}"
    )
    rows = chart_history["symphonies"].get(symphony_id)
    assert rows, (
        f"chart_history has no rows for symphony {symphony_id!r}. "
        f"Available keys: {list(chart_history['symphonies'].keys())}"
    )
    return rows[-1]


# ===========================================================================
# Change A — Breakeven overlay emission
# ===========================================================================
class TestBreakevenOverlayEmission:
    """The chart_history row must carry a ``breakeven`` key:
      * value == BREAKEVEN_RETURN_AXIS_VALUE (0.0) when breakeven_locked is True
      * value is None when breakeven_locked is False
    """

    def test_breakeven_constant_is_defined_and_zero(self):
        """The overlay value must be a NAMED constant, not an inline 0.0.

        Project rule: no magic numbers in engine math. The breakeven axis
        value (0% return on an entry-relative axis) must be exposed as
        ``alpha_bot_execution.BREAKEVEN_RETURN_AXIS_VALUE`` and equal 0.0.
        """
        assert hasattr(alpha_bot_execution, "BREAKEVEN_RETURN_AXIS_VALUE"), (
            "alpha_bot_execution must define a named constant "
            "BREAKEVEN_RETURN_AXIS_VALUE for the breakeven overlay value "
            "(no inline magic 0.0)."
        )
        const = alpha_bot_execution.BREAKEVEN_RETURN_AXIS_VALUE
        assert const == 0.0, (
            "BREAKEVEN_RETURN_AXIS_VALUE must be 0.0 — breakeven is 0% return "
            f"on the chart's entry-relative return axis. Got {const!r}."
        )
        assert type(const) is float, (
            "BREAKEVEN_RETURN_AXIS_VALUE must be a plain float (serialized "
            f"into chart JSON). Got {type(const).__name__}."
        )

    def test_breakeven_key_present_in_chart_row(self, patched_environment):
        """The chart_history row dict MUST contain the key ``breakeven``.

        A missing key (vs. a present key holding None) is the failure mode
        that leaves the frontend overlay dataset undefined.
        """
        env = patched_environment
        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=0.05)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(
            last_price=500.0, vwap=500.0
        )
        env["db"].load_state.return_value = _seed_state(breakeven_locked=False)

        alpha_bot_execution.main()
        row = _latest_chart_row(env)

        assert "breakeven" in row, (
            "chart_history row is missing the 'breakeven' key. The frontend "
            "Breakeven overlay dataset reads this key directly; without it "
            f"the overlay cannot draw. Row keys: {sorted(row.keys())}"
        )

    def test_breakeven_value_is_zero_when_breakeven_locked(self, patched_environment):
        """When breakeven_locked is True for the tick, the row's ``breakeven``
        value equals BREAKEVEN_RETURN_AXIS_VALUE (0.0).

        breakeven_locked is seeded True; the latching invariant of
        ``compute_breakeven_update`` keeps it True for the cycle, so the
        chart-row value is deterministic regardless of the activation math.
        """
        env = patched_environment
        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=0.05)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(
            last_price=500.0, vwap=500.0
        )
        env["db"].load_state.return_value = _seed_state(breakeven_locked=True)

        alpha_bot_execution.main()
        row = _latest_chart_row(env)

        assert "breakeven" in row, "row missing 'breakeven' key"
        assert row["breakeven"] == alpha_bot_execution.BREAKEVEN_RETURN_AXIS_VALUE, (
            "When breakeven_locked is True the chart row's 'breakeven' value "
            "must equal BREAKEVEN_RETURN_AXIS_VALUE (0.0 on the entry-relative "
            f"return axis). Got {row['breakeven']!r}."
        )
        # Pin the axis semantics explicitly: NOT a price, NOT the stop level.
        assert row["breakeven"] == 0.0, (
            "breakeven overlay must sit at 0.0 (0% return) — it must NOT be a "
            f"price or stop_trigger_level. Got {row['breakeven']!r}."
        )

    def test_breakeven_value_is_none_when_not_breakeven_locked(self, patched_environment):
        """When breakeven_locked is False the row's ``breakeven`` value is None.

        A False lock state must yield an explicit None (a gap in the overlay
        line), NOT 0.0 — drawing a breakeven line for a symphony that has not
        locked breakeven would mislead the operator.
        """
        env = patched_environment
        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=0.0)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(
            last_price=500.0, vwap=500.0
        )
        # 0% return + no hold ticks => activation math cannot flip the lock on;
        # seeded False stays False.
        env["db"].load_state.return_value = _seed_state(
            breakeven_locked=False, hwm=0.0
        )

        alpha_bot_execution.main()
        row = _latest_chart_row(env)

        assert "breakeven" in row, "row missing 'breakeven' key"
        assert row["breakeven"] is None, (
            "When breakeven is NOT locked the chart row's 'breakeven' value "
            "must be None (overlay gap), never 0.0. Drawing a breakeven line "
            f"for an unlocked symphony misleads the operator. Got {row['breakeven']!r}."
        )

    def test_breakeven_does_not_bleed_across_symphonies_in_loop(self, patched_environment):
        """Two symphonies in one cycle: a locked one and an unlocked one.

        The locked symphony's row must carry 0.0; the unlocked symphony's row
        must carry None. Catches an implementation that binds the breakeven
        value from a loop-scoped variable that survives across iterations
        (state bleed) instead of re-deriving it per symphony.
        """
        env = patched_environment
        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(
                last_percent_change=0.05,
                symphony_id=_SYMPHONY_ID,
                actual_symphony_id=_ACTUAL_SYMPHONY_ID,
                name=_SYMPHONY_NAME,
            ),
            _make_symphony_payload(
                last_percent_change=0.0,
                symphony_id=_SYMPHONY_ID_2,
                actual_symphony_id=_ACTUAL_SYMPHONY_ID_2,
                name=_SYMPHONY_NAME_2,
            ),
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(
            last_price=500.0, vwap=500.0
        )
        # Symphony 1: breakeven_locked True. Symphony 2: breakeven_locked False.
        seed = _seed_state(
            symphony_id=_SYMPHONY_ID, breakeven_locked=True, hwm=10.0
        )
        seed_2 = _seed_state(
            symphony_id=_SYMPHONY_ID_2, breakeven_locked=False, hwm=0.0
        )
        seed[_SYMPHONY_ID_2] = seed_2[_SYMPHONY_ID_2]
        env["db"].load_state.return_value = seed

        alpha_bot_execution.main()

        row_locked = _latest_chart_row(env, symphony_id=_SYMPHONY_ID)
        row_unlocked = _latest_chart_row(env, symphony_id=_SYMPHONY_ID_2)

        assert row_locked["breakeven"] == 0.0, (
            "Locked symphony's chart row must carry breakeven=0.0. "
            f"Got {row_locked['breakeven']!r}."
        )
        assert row_unlocked["breakeven"] is None, (
            "Unlocked symphony's chart row must carry breakeven=None. A 0.0 "
            "here would mean the locked symphony's value bled across the "
            f"fan-out loop. Got {row_unlocked['breakeven']!r}."
        )


# ===========================================================================
# Change B — VWAP overlay emission (NEW derived chart quantity)
# ===========================================================================
class TestVwapOverlayEmission:
    """The chart_history row must carry a ``vwap`` key whose value is the
    derived quantity ``current_return - (weighted_vwap_diff * 100)``.
    """

    def test_vwap_key_present_in_chart_row(self, patched_environment):
        """The chart_history row dict MUST contain the key ``vwap``."""
        env = patched_environment
        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=0.05)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(
            last_price=505.0, vwap=500.0
        )
        env["db"].load_state.return_value = _seed_state()

        alpha_bot_execution.main()
        row = _latest_chart_row(env)

        assert "vwap" in row, (
            "chart_history row is missing the 'vwap' key. The frontend VWAP "
            "overlay dataset reads this key directly; without it the overlay "
            f"cannot draw. Row keys: {sorted(row.keys())}"
        )

    def test_vwap_value_equals_derived_formula_price_above_vwap(self, patched_environment):
        """VWAP overlay == current_return - weighted_vwap_diff*100.

        Fixture: last_price=505.0, vwap=500.0, single holding allocation 1.0.
        The expected value is DERIVED in-test from the fixture inputs by
        re-applying the documented formulae — never a precomputed literal:

          weighted_vwap_diff = sum(alloc * (p - v) / v)
                             = 1.0 * (505.0 - 500.0) / 500.0
          current_return     = last_percent_change * 100
          expected_vwap      = current_return - weighted_vwap_diff * 100

        Because last_price > vwap, weighted_vwap_diff > 0, so the VWAP overlay
        sits BELOW current_return — a directional check the formula sign must
        satisfy.
        """
        env = patched_environment
        last_pct_change = 0.05
        last_price = 505.0
        vwap = 500.0
        allocation = 1.0

        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=last_pct_change)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(
            last_price=last_price, vwap=vwap
        )
        env["db"].load_state.return_value = _seed_state()

        alpha_bot_execution.main()
        row = _latest_chart_row(env)

        # Derive expected from fixture inputs (NOT a hardcoded producer value).
        weighted_vwap_diff = allocation * (last_price - vwap) / vwap
        current_return = last_pct_change * 100.0
        expected_vwap = current_return - (weighted_vwap_diff * 100.0)

        assert "vwap" in row, "row missing 'vwap' key"
        assert row["vwap"] == pytest.approx(expected_vwap, rel=TOL_REL, abs=TOL_ABS), (
            "VWAP overlay value mismatch. Expected "
            f"current_return({current_return}) - weighted_vwap_diff*100"
            f"({weighted_vwap_diff * 100.0}) = {expected_vwap}, got {row['vwap']!r}."
        )
        # Directional: price above VWAP => overlay sits below current_return.
        assert row["vwap"] < current_return, (
            "last_price > vwap means weighted_vwap_diff > 0, so the VWAP "
            "overlay must sit BELOW current_return. A formula that ADDED "
            f"instead of subtracted would fail this. current_return={current_return}, "
            f"vwap_overlay={row['vwap']!r}."
        )

    def test_vwap_value_equals_derived_formula_price_below_vwap(self, patched_environment):
        """Sign-flip case: last_price < vwap => overlay ABOVE current_return.

        Fixture: last_price=495.0, vwap=500.0. weighted_vwap_diff < 0, so
        ``- weighted_vwap_diff*100`` is positive and the VWAP overlay sits
        ABOVE current_return. Catches a sign error the price-above case
        cannot.
        """
        env = patched_environment
        last_pct_change = 0.03
        last_price = 495.0
        vwap = 500.0
        allocation = 1.0

        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=last_pct_change)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(
            last_price=last_price, vwap=vwap
        )
        env["db"].load_state.return_value = _seed_state()

        alpha_bot_execution.main()
        row = _latest_chart_row(env)

        weighted_vwap_diff = allocation * (last_price - vwap) / vwap
        current_return = last_pct_change * 100.0
        expected_vwap = current_return - (weighted_vwap_diff * 100.0)

        assert row["vwap"] == pytest.approx(expected_vwap, rel=TOL_REL, abs=TOL_ABS), (
            f"VWAP overlay mismatch (price-below-vwap). Expected {expected_vwap}, "
            f"got {row['vwap']!r}."
        )
        assert row["vwap"] > current_return, (
            "last_price < vwap means weighted_vwap_diff < 0, so the VWAP "
            "overlay must sit ABOVE current_return. "
            f"current_return={current_return}, vwap_overlay={row['vwap']!r}."
        )

    def test_vwap_value_equals_current_return_when_price_equals_vwap(
        self, patched_environment
    ):
        """When last_price == vwap, weighted_vwap_diff is 0 and the VWAP
        overlay coincides exactly with current_return (the overlay and the
        return line touch). Anchors the zero-diff case.
        """
        env = patched_environment
        last_pct_change = 0.04
        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=last_pct_change)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(
            last_price=500.0, vwap=500.0
        )
        env["db"].load_state.return_value = _seed_state()

        alpha_bot_execution.main()
        row = _latest_chart_row(env)

        current_return = last_pct_change * 100.0
        # weighted_vwap_diff == 0 => expected_vwap == current_return.
        assert row["vwap"] == pytest.approx(current_return, rel=TOL_REL, abs=TOL_ABS), (
            "When last_price == vwap, weighted_vwap_diff is 0 and the VWAP "
            f"overlay must equal current_return ({current_return}). "
            f"Got {row['vwap']!r}."
        )

    def test_vwap_overlay_distinct_from_raw_vwap_diff_key(self, patched_environment):
        """The new ``vwap`` key must be the entry-relative DERIVED overlay,
        NOT a duplicate of the existing raw ``vwap_diff`` key.

        The row already carries ``vwap_diff`` (the raw weighted_vwap_diff
        fraction). The new ``vwap`` key is a different quantity on a different
        axis. An implementation that aliased ``vwap`` to ``vwap_diff`` would
        feed the frontend the wrong scale.
        """
        env = patched_environment
        last_price = 510.0
        vwap = 500.0
        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=0.06)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(
            last_price=last_price, vwap=vwap
        )
        env["db"].load_state.return_value = _seed_state()

        alpha_bot_execution.main()
        row = _latest_chart_row(env)

        assert "vwap" in row and "vwap_diff" in row, (
            "row must carry BOTH the new 'vwap' overlay key and the existing "
            f"raw 'vwap_diff' key. Row keys: {sorted(row.keys())}"
        )
        # With last_price != vwap the two are numerically distinct: vwap_diff
        # is a small fraction (~0.02), the overlay is on the percent axis.
        assert row["vwap"] != row["vwap_diff"], (
            "The 'vwap' overlay key must NOT alias the raw 'vwap_diff' key — "
            "they are different quantities on different axes. "
            f"vwap={row['vwap']!r}, vwap_diff={row['vwap_diff']!r}."
        )


# ===========================================================================
# Change B (golden fixture) — VWAP overlay derivation pinned to a JSON fixture
# ===========================================================================
class TestVwapOverlayGoldenFixture:
    """Golden-fixture test for the NEW derived VWAP-overlay quantity.

    The fixture under tests/fixtures/math/ pins controlled inputs and a
    hand-derived expected overlay value (in its ``derivation`` field). The
    test re-derives the expected number from the fixture's own inputs rather
    than trusting the fixture's stored ``expected`` blindly — so neither a
    wrong producer NOR a wrong fixture can pass silently.
    """

    def test_vwap_overlay_golden_fixture(self, patched_environment):
        fixture_path = FIXTURE_DIR / "vwap_overlay_basic.json"
        assert fixture_path.exists(), (
            f"Missing golden fixture {fixture_path}. The VWAP overlay is a "
            "new derived chart quantity and REQUIRES a persisted golden "
            "fixture under tests/fixtures/math/."
        )
        with fixture_path.open("r", encoding="utf-8") as fh:
            fixture = json.load(fh)

        inputs = fixture["inputs"]
        last_pct_change = inputs["last_percent_change"]
        last_price = inputs["last_price"]
        vwap = inputs["vwap"]
        allocation = inputs["allocation"]

        env = patched_environment
        env["fetch_symphony_stats"].return_value = [
            {
                "id": _SYMPHONY_ID,
                "symphony_id": _ACTUAL_SYMPHONY_ID,
                "name": _SYMPHONY_NAME,
                "last_percent_change": last_pct_change,
                "current_value": 10000.0,
                "holdings": [{"ticker": _TICKER, "allocation": allocation}],
            }
        ]
        env["fetch_intraday_vwaps"].return_value = {
            _TICKER: {"vwap": vwap, "last_price": last_price}
        }
        env["db"].load_state.return_value = _seed_state()

        alpha_bot_execution.main()
        row = _latest_chart_row(env)

        # Independently re-derive from the fixture's inputs.
        weighted_vwap_diff = allocation * (last_price - vwap) / vwap
        current_return = last_pct_change * 100.0
        rederived_expected = current_return - (weighted_vwap_diff * 100.0)

        # Cross-check the fixture's stored expectation against the re-derivation
        # so a typo in the fixture cannot mask a producer bug.
        assert fixture["expected"]["vwap_overlay"] == pytest.approx(
            rederived_expected, rel=TOL_REL, abs=TOL_ABS
        ), (
            "Golden fixture's stored expected.vwap_overlay disagrees with the "
            "value re-derived from its own inputs — the fixture is internally "
            f"inconsistent. stored={fixture['expected']['vwap_overlay']}, "
            f"re-derived={rederived_expected}."
        )

        assert row["vwap"] == pytest.approx(
            rederived_expected, rel=TOL_REL, abs=TOL_ABS
        ), (
            f"VWAP overlay does not match golden fixture {fixture_path.name}. "
            f"Expected {rederived_expected} "
            f"(derivation: {fixture.get('derivation')}), got {row['vwap']!r}."
        )


# ===========================================================================
# Decision-path parity — the overlay keys are WRITE-ONLY observational data
# ===========================================================================
class TestOverlayKeysDoNotPerturbDecisionPath:
    """The new ``breakeven`` and ``vwap`` chart-row keys are observational —
    emitted into ``chart_history`` only. They must NOT feed any exit / risk
    decision.

    The engine resolves exits via ``resolve_trigger_priority`` on the four
    boolean signals (is_vwap_broken / is_tp_hit / is_vwap_bleed_broken /
    is_trailing_stop_hit) — none of which is the overlay data. The chart
    row's pre-existing decision-relevant fields (``stop``, ``event``,
    ``return``, ``mc_prob``) and the persisted exit state must keep the
    values they would have had WITHOUT the overlay additions.

    Strategy: drive a controlled cycle that fires a trailing-stop trigger
    (``compute_exit_confirmation`` patched to force the hit, exactly as
    test_main_pipeline.py scenario 1 does). Then assert BOTH:
      (a) the overlay keys are present (the additions happened), AND
      (b) every decision-relevant field holds its independently-derived
          value — the additions did not perturb the exit path.

    An implementation that wired ``breakeven``/``vwap`` into the trigger
    logic, or that overwrote ``stop`` / ``event`` while adding the keys,
    fails this test.
    """

    def test_overlay_additions_do_not_change_trigger_outcome_or_stop(
        self, patched_environment
    ):
        env = patched_environment

        fixture_pct_change = 0.12  # current_return == 12.0
        seed_hwm = 20.0  # above current_return -> rising-HWM branch skipped
        live_price = 505.0
        live_vwap = 500.0

        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=fixture_pct_change)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(
            last_price=live_price, vwap=live_vwap
        )
        # breakeven_locked True so the breakeven overlay key is populated AND
        # the chart row's `stop` (tracked_stop) is the breakeven-clamped level.
        env["db"].load_state.return_value = _seed_state(
            armed=True, hwm=seed_hwm, breakeven_locked=True
        )

        with (
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(3, True),  # force trailing-stop hit
            ),
            patch.object(alpha_bot_execution, "execute_sell_to_cash"),
        ):
            alpha_bot_execution.main()

        # ----- The overlay additions happened -----
        row = _latest_chart_row(env)
        assert "breakeven" in row and "vwap" in row, (
            "overlay keys must be present — the additions are the point of "
            f"the cycle. Row keys: {sorted(row.keys())}"
        )

        # ----- Decision path UNPERTURBED -----
        # 1. The trailing-stop trigger still fired and was persisted.
        save_calls = env["db"].save_state.call_args_list
        assert save_calls, "save_state never called — pipeline aborted"
        sym_state = save_calls[-1].args[0][_SYMPHONY_ID]
        assert sym_state["triggered"] is True, (
            "Forced trailing-stop hit must still resolve to triggered=True. "
            "Adding the overlay keys must not suppress the exit."
        )
        assert sym_state["triggered_reason"] == "Trailing Stop", (
            "Exit reason must still be 'Trailing Stop' — overlay keys must "
            f"not reroute resolve_trigger_priority. Got {sym_state['triggered_reason']!r}."
        )

        # 2. The chart row's `event` reflects the trigger, not an overlay
        #    value. On a fired trigger the engine overwrites the row's `event`
        #    with the resolved trigger reason (alpha_bot_execution.py:1729);
        #    for a trailing-stop hit that reason is "Trailing Stop". The
        #    overlay additions must leave that overwrite intact.
        assert row["event"] == "Trailing Stop", (
            "chart row 'event' must be the resolved trigger reason "
            "'Trailing Stop' after the exit (engine overwrites it post-"
            f"trigger) — the overlay additions must not clobber it. "
            f"Got {row['event']!r}."
        )

        # 3. The chart row's `return` is the independently-derived current
        #    return, untouched by the overlay arithmetic.
        expected_current_return = fixture_pct_change * 100.0
        assert row["return"] == pytest.approx(
            expected_current_return, rel=TOL_REL, abs=TOL_ABS
        ), (
            "chart row 'return' must equal fixture-derived current_return "
            f"({expected_current_return}); the VWAP-overlay arithmetic "
            f"(which also reads current_return) must not mutate it. "
            f"Got {row['return']!r}."
        )

        # 4. The breakeven overlay value must NOT have leaked into the row's
        #    `stop` field. `stop` is tracked_stop (a stop_trigger_level), a
        #    distinct decision quantity from the breakeven overlay constant.
        #    With breakeven_locked True the stop is breakeven-clamped >= 0.0,
        #    but it is the engine's stop level, not BREAKEVEN_RETURN_AXIS_VALUE
        #    copied across.
        assert row["stop"] is not None, (
            "with an armed/triggered symphony, chart row 'stop' must carry "
            "the tracked stop level, not None."
        )
        assert "breakeven" in row and row["breakeven"] == 0.0, (
            "breakeven overlay should be 0.0 here (locked) — sanity anchor "
            "for the next assertion."
        )
        # The two are independent keys; assert `stop` is sourced from the
        # engine's stop math by checking it equals the persisted stop_trigger.
        assert row["stop"] == pytest.approx(
            sym_state["stop_trigger"], rel=TOL_REL, abs=TOL_ABS
        ), (
            "chart row 'stop' must equal the engine-computed stop_trigger "
            "persisted to bot_state — proving 'stop' is still sourced from "
            "the decision math and was not overwritten by the breakeven "
            f"overlay constant. row['stop']={row['stop']!r}, "
            f"stop_trigger={sym_state['stop_trigger']!r}."
        )
