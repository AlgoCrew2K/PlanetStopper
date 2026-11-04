"""
Engine integration tests for the session-scoped regime-label read
(feature-plans/regime-label-producer.md, cycle C1: AC-3, AC-4, S-5.7, S-6.1).

Contract under test
-------------------
The engine reads the cached regime label for a symphony through
``database.get_cached_regime_label`` and applies it to the trailing-stop
confirmation count (trending 2, mean-reverting 5, high-vol 3, anything else the
default 3). A label is applied only when its ``as_of_date`` equals the engine's
ET calendar date for the cycle (AC-4); a label for any other date, a malformed
date, or no row at all leaves the default count in force.

How these tests drive the engine
--------------------------------
Real ``alpha_bot_execution.main()`` cycles, one per tick, through the B1
harness (``tests/execution/test_main_pipeline.py``). Composer, Alpaca, Discord
and the clock are faked there. Unlike the B1 tests, the exit math is NOT
patched: the trailing stop is driven by real ``math_engine`` calls, so the tick
on which it fires is the observable under test.

The harness replaces ``alpha_bot_execution.database`` with a ``MagicMock``. The
fixture below wires its ``get_cached_regime_label`` to the REAL accessor over
the per-test temp state DB (``tests/conftest.py::_isolate_db``), and records
every call and result. Rows are written by the real
``database.save_regime_label``. ``load_state`` / ``save_state`` are wired to an
in-memory holder so state carries from one cycle to the next.

Expected values
---------------
* The tick on which the stop fires is compared with the count the spec states
  per label (trending 2, mean-reverting 5, high-vol 3; default 3). Those four
  numbers are the AC-3 contract, stated literally in ``_SPEC_TICKS`` and
  cross-checked in ``TestSpecTicksMatchTheMathLayer`` against the real
  ``math_engine.apply_regime_exit_adjustment`` so a drift in either place fails.
* Dates are derived from the fixed clock and ``market_calendar``; no row date is
  a literal copied from a run.

Fixture provenance: synthetic, hand-built in this file from the B1 harness
builders (``_make_symphony_payload``, ``_seed_state``). No captured payload is
involved; the only persisted rows are written by ``database.save_regime_label``.
"""

from __future__ import annotations

import copy
import os
import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

import alpha_bot_execution
import database as real_database
import market_calendar
import math_engine
import regime_classifier
from tests.execution.test_main_pipeline import (
    _ACTUAL_SYMPHONY_ID,
    _FIXED_ET,
    _SYMPHONY_ID,
    _SYMPHONY_NAME,
    _TICKER,
    _make_symphony_payload,
    _make_vwap_payload,
    _seed_state,
    patched_environment,  # noqa: F401  (re-exported pytest fixture)
)

# --------------------------------------------------------------------------
# Spec constants and helpers
# --------------------------------------------------------------------------

# AC-3: label -> consecutive qualifying ticks the stop needs. Stated literally
# because they ARE the contract; TestSpecTicksMatchTheMathLayer ties them to
# the math layer so neither side can drift alone.
_SPEC_TICKS = {
    "trending": 2,
    "mean-reverting": 5,
    "high-vol": 3,
    None: 3,
}
_DEFAULT_TICKS = 3

_D = _FIXED_ET.strftime("%Y-%m-%d")  # the engine's ET calendar date for the cycle


def _prev_trading_day(d: date) -> date:
    """The NYSE session before ``d``, derived from the calendar."""
    cur = d - timedelta(days=1)
    while not market_calendar.is_trading_day(cur):
        cur -= timedelta(days=1)
    return cur


def _next_trading_day(d: date) -> date:
    cur = d + timedelta(days=1)
    while not market_calendar.is_trading_day(cur):
        cur += timedelta(days=1)
    return cur


_PREV = _prev_trading_day(_FIXED_ET.date()).isoformat()
_NEXT = _next_trading_day(_FIXED_ET.date()).isoformat()

# Armed symphony seeded far above where the qualifying tick lands, so the
# return is below every stop level the real math produces for the minimal
# history the harness supplies, while the stop count is the only thing that
# separates "tick 2" from "tick 3".
_SEED_HWM = 50.0
_QUALIFYING_PCT = 0.47  # current_return 47.0: below every stop level (HWM 50 less the distance)
_ABOVE_STOP_PCT = 0.499  # current_return 49.9: above the stop level, squeezed or not
_SEED_PREV_RETURN = 47.0  # so the first qualifying tick carries no parabolic velocity

_SYMPHONY_B_ID = "sym-pipeline-test-002"
_SYMPHONY_B_NAME = "Pipeline Test Symphony B"


class _Engine:
    """Drives real ``alpha_bot_execution.main()`` cycles with carried state."""

    def __init__(self, env, calls):
        self.env = env
        self.calls = calls  # list of dicts: args, kwargs, result
        self._holder: dict = {"state": None}
        db = env["db"]
        db.load_state.side_effect = lambda *a, **k: copy.deepcopy(self._holder["state"])
        db.save_state.side_effect = lambda s, *a, **k: self._holder.__setitem__(
            "state", copy.deepcopy(s)
        )

    # -- state ---------------------------------------------------------
    def seed(self, symphony_ids=((_SYMPHONY_ID,)), *, date_str=_D, armed=True, hwm=_SEED_HWM):
        state = {"date": date_str, "last_execution_mode": False}
        for sid in symphony_ids:
            state[sid] = _seed_state(armed=armed, hwm=hwm, prev_return=_SEED_PREV_RETURN)[
                _SYMPHONY_ID
            ]
        self._holder["state"] = state

    @property
    def state(self) -> dict:
        return self._holder["state"]

    def sym(self, sid=_SYMPHONY_ID) -> dict:
        return self.state[sid]

    # -- one cycle -----------------------------------------------------
    def tick(self, pct=_QUALIFYING_PCT, *, pcts=None, clock=_FIXED_ET, vwap=None):
        """Run one engine cycle. ``pcts`` maps symphony id -> last_percent_change
        for multi-symphony cycles; otherwise one symphony at ``pct``."""
        if pcts is None:
            pcts = {_SYMPHONY_ID: pct}
        payloads = []
        for sid, p in pcts.items():
            payload = _make_symphony_payload(last_percent_change=p)
            if sid != _SYMPHONY_ID:
                payload["id"] = sid
                payload["symphony_id"] = f"actual-{sid}"
                payload["name"] = _SYMPHONY_B_NAME
            payloads.append(payload)
        self.env["fetch_symphony_stats"].return_value = payloads
        self.env["fetch_intraday_vwaps"].return_value = (
            vwap if vwap is not None else _make_vwap_payload(500.0)
        )
        with patch.object(alpha_bot_execution, "get_current_et", return_value=clock):
            alpha_bot_execution.main()

    def fire_tick(self, sid=_SYMPHONY_ID, *, max_ticks=9, pct=_QUALIFYING_PCT):
        """Qualifying ticks until ``sid`` is triggered; the 1-based tick, or None."""
        for n in range(1, max_ticks + 1):
            self.tick(pct)
            if self.sym(sid).get("triggered"):
                return n
        return None


@pytest.fixture
def engine(patched_environment):  # noqa: F811
    """Harness plus the real session-aware accessor over the temp state DB."""
    env = patched_environment
    calls: list[dict] = []

    def _read(*args, **kwargs):
        result = real_database.get_cached_regime_label(*args, **kwargs)
        calls.append({"args": args, "kwargs": dict(kwargs), "result": result})
        return result

    env["db"].get_cached_regime_label.side_effect = _read
    drv = _Engine(env, calls)
    drv.seed()
    return drv


def _save(label: str, as_of: str, sid: str = _SYMPHONY_ID) -> None:
    real_database.save_regime_label(sid, label, as_of)


def _projection(sym: dict) -> dict:
    """Decision-relevant state of a symphony after a cycle."""
    keys = (
        "armed",
        "triggered",
        "triggered_reason",
        "below_stop_count",
        "above_tp_count",
        "tp_armed",
        "para_armed",
        "vwap_ticks",
        "vwap_bleed_ticks",
        "breakeven_locked",
        "hwm_hold_ticks",
        "prev_return",
        "mc_history",
        "disarm_confirm_count",
    )
    return {k: sym.get(k) for k in keys}


# --------------------------------------------------------------------------
# Harness self-check: the unadjusted stop needs the default count, so every
# per-label assertion below distinguishes 2 / 3 / 5 for real.
# --------------------------------------------------------------------------


class TestDriverFiresAtDefaultCountWithoutLabel:
    def test_no_row_fires_on_default_tick(self, engine):
        assert engine.fire_tick() == _DEFAULT_TICKS

    def test_stop_stays_unfired_before_the_default_tick(self, engine):
        for _ in range(_DEFAULT_TICKS - 1):
            engine.tick()
            assert engine.sym()["triggered"] is False


class TestSpecTicksMatchTheMathLayer:
    @pytest.mark.parametrize("label", ["trending", "mean-reverting", "high-vol", None])
    def test_spec_ticks_equal_the_real_adjustment(self, label):
        assert (
            math_engine.apply_regime_exit_adjustment(label, math_engine.EXIT_CONFIRM_TICKS)
            == _SPEC_TICKS[label]
        )


# --------------------------------------------------------------------------
# AC-3 -- the session's label sets the count
# --------------------------------------------------------------------------


class TestS3_1to3_3LabelSetsCountOnItsSession:
    @pytest.mark.parametrize("label", ["trending", "mean-reverting", "high-vol"])
    def test_stop_fires_on_the_labels_tick_count_when_row_is_dated_today(self, engine, label):
        _save(label, _D)
        assert engine.fire_tick() == _SPEC_TICKS[label]

    @pytest.mark.parametrize("label", ["trending", "mean-reverting"])
    def test_stop_does_not_fire_before_the_labels_tick_count(self, engine, label):
        _save(label, _D)
        for n in range(1, _SPEC_TICKS[label]):
            engine.tick()
            assert engine.sym()["triggered"] is False, f"fired early on tick {n} with {label}"
            assert engine.sym()["below_stop_count"] == n


class TestS3_4NoRowUsesDefault:
    def test_no_row_for_the_symphony_fires_on_the_default_tick(self, engine):
        # A row for ANOTHER symphony must not leak onto this one.
        _save("trending", _D, sid="some-other-symphony")
        assert engine.fire_tick() == _DEFAULT_TICKS


class TestS3_5UnrecognisedLabelUsesDefault:
    @pytest.mark.parametrize("label", ["Trending", "", "sideways"])
    def test_label_outside_the_three_fires_on_the_default_tick(self, engine, label):
        _save(label, _D)
        assert engine.fire_tick() == _DEFAULT_TICKS


class TestS3_6CountResetsOnAboveStopTick:
    def test_mean_reverting_count_restarts_after_an_above_stop_tick(self, engine):
        _save("mean-reverting", _D)
        need = _SPEC_TICKS["mean-reverting"]
        for _ in range(need - 1):  # 4 qualifying ticks
            engine.tick()
            assert engine.sym()["triggered"] is False
        engine.tick(_ABOVE_STOP_PCT)
        assert engine.sym()["triggered"] is False
        assert engine.sym()["below_stop_count"] == 0, "above-stop tick must reset the count"
        for _ in range(need - 1):  # 4 new consecutive qualifying ticks: still no fire
            engine.tick()
            assert engine.sym()["triggered"] is False
        engine.tick()  # the 5th consecutive one fires
        assert engine.sym()["triggered"] is True


class TestS3_7ReadFailureFallsBackToDefault:
    def test_missing_table_fires_on_default_tick_and_no_exception_escapes(self, engine):
        conn = sqlite3.connect(os.environ["DB_PATH"])
        try:
            conn.execute("DROP TABLE regime_label_cache")
            conn.commit()
        finally:
            conn.close()
        assert engine.fire_tick() == _DEFAULT_TICKS

    def test_locked_database_fires_on_default_tick_and_no_exception_escapes(self, engine):
        _save("trending", _D)  # would give 2 if the read succeeded

        def _locked():
            raise sqlite3.OperationalError("database is locked")

        with patch.object(real_database, "get_ro_connection", side_effect=_locked):
            assert engine.fire_tick() == _DEFAULT_TICKS


class TestS3_8OnlyTheTrailingStopCountMoves:
    def test_non_qualifying_ticks_leave_every_decision_field_identical_with_a_row(self, engine):
        """No stop-qualifying tick, VWAP breakdown and a rising then falling
        return: every decision field must equal the no-row run tick by tick."""
        weak_vwap = {_TICKER: {"vwap": 520.0, "last_price": 500.0}}
        pcts = [0.499, 0.4995, 0.5, 0.4985, 0.499, 0.5]

        def run(with_row: bool):
            engine.seed()
            if with_row:
                _save("trending", _D)
            traj = []
            for p in pcts:
                engine.tick(p, vwap=weak_vwap)
                traj.append(_projection(engine.sym()))
            return traj

        without_row = run(False)
        # Reset the cache between the two runs.
        conn = sqlite3.connect(os.environ["DB_PATH"])
        try:
            conn.execute("DELETE FROM regime_label_cache")
            conn.commit()
        finally:
            conn.close()
        with_row = run(True)
        assert with_row == without_row

    def test_qualifying_ticks_differ_only_in_the_stop_count_and_trigger_fields(self, engine):
        engine.seed()
        engine.tick()
        baseline_t1 = _projection(engine.sym())
        engine.tick()
        baseline_t2 = _projection(engine.sym())

        conn = sqlite3.connect(os.environ["DB_PATH"])
        try:
            conn.execute("DELETE FROM regime_label_cache")
            conn.commit()
        finally:
            conn.close()

        engine.seed()
        _save("trending", _D)
        engine.tick()
        assert _projection(engine.sym()) == baseline_t1
        engine.tick()
        trending_t2 = _projection(engine.sym())
        assert trending_t2["triggered"] is True and baseline_t2["triggered"] is False
        outcome_fields = {"triggered", "triggered_reason", "armed", "below_stop_count"}
        for key in baseline_t2:
            if key in outcome_fields:
                continue
            assert trending_t2[key] == baseline_t2[key], f"{key} moved with the label"
        assert engine.sym()["triggered_reason"] == "Trailing Stop"


class TestS3_8TakeProfitConfirmationIsUntouchedByTheLabel:
    """The label moves the trailing-stop count only. The Monte Carlo result is seamed so the
    take-profit machine really runs (the harness's history is too short for a real MC)."""

    _TP_ARM_PROB = 1.0  # below TAKE_PROFIT_MC_PCT: arms the take-profit
    # At or above TAKE_PROFIT_MC_PCT and below every label's tick count scaled threshold, so a
    # threshold that varied with the label would read this as "still below" and count differently.
    _TP_ABOVE_PROB = 7.0

    @pytest.mark.parametrize("label", ["trending", "mean-reverting"])
    def test_take_profit_state_and_trigger_match_the_no_row_run_tick_by_tick(self, engine, label):
        assert alpha_bot_execution.TAKE_PROFIT_MC_PCT < self._TP_ABOVE_PROB
        probs = [self._TP_ARM_PROB, self._TP_ARM_PROB] + [self._TP_ABOVE_PROB] * 6

        def run(with_row: bool):
            conn = sqlite3.connect(os.environ["DB_PATH"])
            try:
                conn.execute("DELETE FROM regime_label_cache")
                conn.commit()
            finally:
                conn.close()
            engine.seed()
            if with_row:
                _save(label, _D)
            feed = iter(probs)
            traj = []
            with patch.object(
                math_engine, "run_monte_carlo", side_effect=lambda *a, **k: next(feed)
            ):
                for _ in probs:
                    engine.tick(_ABOVE_STOP_PCT)
                    sym = engine.sym()
                    traj.append(
                        {
                            "tp_armed": sym.get("tp_armed"),
                            "above_tp_count": sym.get("above_tp_count"),
                            "triggered": sym.get("triggered"),
                            "triggered_reason": sym.get("triggered_reason"),
                        }
                    )
            return traj

        without_row = run(False)
        with_row = run(True)

        assert with_row == without_row
        # Non-vacuous: the take-profit armed, counted and fired in the reference run.
        assert any(step["tp_armed"] for step in without_row)
        assert any(step["above_tp_count"] for step in without_row)
        assert any(step["triggered_reason"] for step in without_row)


class TestS3_9NoRowsMeansRefBehaviour:
    def test_decisions_equal_a_run_where_the_only_row_is_dated_another_session(self, engine):
        q, a = _QUALIFYING_PCT, _ABOVE_STOP_PCT
        pcts = [q, q, a, q, q, q, q]

        def run():
            engine.seed()
            traj = []
            for p in pcts:
                engine.tick(p)
                traj.append(_projection(engine.sym()))
            return traj

        with_empty_table = run()
        # A real row for this symphony that is NOT for today's session: the reference behaviour.
        _save("trending", _PREV)
        with_other_date_row = run()
        assert with_empty_table == with_other_date_row
        # Non-vacuous: the sequence reaches a trigger in both runs.
        assert any(step["triggered"] for step in with_other_date_row)


class TestS3_10DryRunStatesApplyTheLabel:
    @pytest.mark.parametrize("mode", ["live_execution_false", "live_mode_false"])
    def test_simulated_trigger_fires_on_trending_tick_and_sends_no_order(self, engine, mode):
        _save("trending", _D)
        post = MagicMock()
        strat = {"params": {}, "locked_vars": {}}
        live_execution = mode != "live_execution_false"
        strat["live_mode"] = mode != "live_mode_false"
        engine.env["db"].get_symphony_strategy.return_value = strat
        with (
            patch.object(alpha_bot_execution, "LIVE_EXECUTION", live_execution),
            patch.object(alpha_bot_execution.requests, "post", post),
        ):
            assert engine.fire_tick() == _SPEC_TICKS["trending"]
        post.assert_not_called()
        assert engine.sym()["triggered_reason"] == "Trailing Stop"


class TestS3_11LiveTrendingSendsSellAfterTickTwo:
    def test_go_to_cash_post_follows_the_second_tick_and_not_the_first(self, engine):
        _save("trending", _D)
        resp = MagicMock(status_code=200)
        post = MagicMock(return_value=resp)
        with (
            patch.object(alpha_bot_execution, "LIVE_EXECUTION", True),
            patch.object(alpha_bot_execution.requests, "post", post),
        ):
            engine.tick()
            assert post.call_count == 0, "sell sent before the label's count"
            engine.tick()
        assert post.call_count == 1
        url = post.call_args.args[0]
        assert url.endswith(f"/symphonies/{_ACTUAL_SYMPHONY_ID}/go-to-cash")
        assert engine.sym()["triggered"] is True


# --------------------------------------------------------------------------
# AC-4 -- a label not computed for the current session is never applied
# --------------------------------------------------------------------------


class TestS4_1StaleLabelIgnored:
    # high-vol maps to the default count, so a stale high-vol row could not be
    # told from an ignored one; only the two labels that move the count are used.
    @pytest.mark.parametrize("label", ["trending", "mean-reverting"])
    def test_label_dated_the_previous_trading_day_fires_on_the_default_tick(self, engine, label):
        _save(label, _PREV)
        assert engine.fire_tick() == _DEFAULT_TICKS

    def test_the_accessor_never_hands_the_engine_a_label_for_a_stale_row(self, engine):
        _save("mean-reverting", _PREV)
        engine.fire_tick()
        assert engine.calls, "engine must read the cache"
        assert all(c["result"] is None for c in engine.calls)


class TestS4_2TwoSymphoniesBothIgnoreStaleRows:
    def test_each_symphony_needs_the_default_count(self, engine):
        engine.seed((_SYMPHONY_ID, _SYMPHONY_B_ID))
        _save("trending", _PREV, sid=_SYMPHONY_ID)
        _save("mean-reverting", _PREV, sid=_SYMPHONY_B_ID)
        fired = {}
        for n in range(1, 8):
            engine.tick(pcts={_SYMPHONY_ID: _QUALIFYING_PCT, _SYMPHONY_B_ID: _QUALIFYING_PCT})
            for sid in (_SYMPHONY_ID, _SYMPHONY_B_ID):
                if sid not in fired and engine.sym(sid)["triggered"]:
                    fired[sid] = n
        assert fired == {_SYMPHONY_ID: _DEFAULT_TICKS, _SYMPHONY_B_ID: _DEFAULT_TICKS}


class TestS4_3NoRowForTodayAtAnyClock:
    def test_before_the_open_nothing_fires_and_after_it_the_default_count_applies(self, engine):
        _save("mean-reverting", _PREV)
        pre_open = _FIXED_ET.replace(hour=9, minute=20)
        engine.tick(clock=pre_open)
        assert engine.sym()["triggered"] is False
        assert engine.sym()["below_stop_count"] == 0
        assert engine.fire_tick() == _DEFAULT_TICKS

    def test_a_trending_row_from_the_previous_session_is_not_borrowed_at_midday(self, engine):
        _save("trending", _PREV)
        late = _FIXED_ET.replace(hour=15, minute=30)
        for _ in range(_DEFAULT_TICKS - 1):
            engine.tick(clock=late)
            assert engine.sym()["triggered"] is False
        engine.tick(clock=late)
        assert engine.sym()["triggered"] is True


class TestS4_4RowFromTheFridayOnAMonday:
    def test_friday_row_is_ignored_on_monday_and_a_monday_row_is_applied(self, engine):
        monday = _FIXED_ET.replace(year=2025, month=5, day=19)
        friday = _prev_trading_day(monday.date())
        assert friday.weekday() == 4 and monday.weekday() == 0
        monday_str = monday.strftime("%Y-%m-%d")

        engine.seed(date_str=monday_str)
        _save("trending", friday.isoformat())
        for n in range(1, _DEFAULT_TICKS + 1):
            engine.tick(clock=monday)
            assert engine.sym()["triggered"] is (n == _DEFAULT_TICKS)

        # Control: the same Monday with a row dated Monday applies it (the clock
        # and the seeded state are valid, so the ignore above is the rule).
        engine.seed(date_str=monday_str)
        _save("trending", monday_str)
        for n in range(1, _SPEC_TICKS["trending"] + 1):
            engine.tick(clock=monday)
        assert engine.sym()["triggered"] is True
        assert engine.sym()["below_stop_count"] >= _SPEC_TICKS["trending"]


class TestS4_5FutureDatedRowIgnored:
    @pytest.mark.parametrize("as_of", [_NEXT, "2099-01-01"])
    def test_row_dated_after_today_fires_on_the_default_tick(self, engine, as_of):
        _save("trending", as_of)
        assert engine.fire_tick() == _DEFAULT_TICKS


class TestS4_6MalformedDateIgnored:
    @pytest.mark.parametrize("as_of", ["", "2025/05/14", "not-a-date"])
    def test_row_with_unusable_date_fires_on_default_tick_without_exception(self, engine, as_of):
        conn = sqlite3.connect(os.environ["DB_PATH"])
        try:
            conn.execute(
                "INSERT INTO regime_label_cache (symphony_id, label, as_of_date) VALUES (?, ?, ?)",
                (_SYMPHONY_ID, "trending", as_of),
            )
            conn.commit()
        finally:
            conn.close()
        assert engine.fire_tick() == _DEFAULT_TICKS


class TestS4_7SymphonyAddedAfterTheRun:
    def test_symphony_without_a_row_needs_default_while_its_neighbour_uses_its_label(self, engine):
        engine.seed((_SYMPHONY_ID, _SYMPHONY_B_ID))
        _save("trending", _D, sid=_SYMPHONY_ID)  # B has no row
        fired = {}
        for n in range(1, 8):
            engine.tick(pcts={_SYMPHONY_ID: _QUALIFYING_PCT, _SYMPHONY_B_ID: _QUALIFYING_PCT})
            for sid in (_SYMPHONY_ID, _SYMPHONY_B_ID):
                if sid not in fired and engine.sym(sid)["triggered"]:
                    fired[sid] = n
        assert fired == {
            _SYMPHONY_ID: _SPEC_TICKS["trending"],
            _SYMPHONY_B_ID: _DEFAULT_TICKS,
        }


class TestS4_8RemovedSymphonyRowHasNoEffect:
    def test_removed_symphony_is_not_read_and_does_not_affect_the_others(self, engine):
        removed = "sym-removed-001"
        engine.seed((_SYMPHONY_ID, removed))
        _save("trending", _D, sid=removed)  # R has a row for D but is not reported
        removed_before = copy.deepcopy(engine.sym(removed))
        assert engine.fire_tick() == _DEFAULT_TICKS  # payload carries only _SYMPHONY_ID
        assert all(c["args"][0] != removed for c in engine.calls), (
            "the cache was read for a symphony Composer no longer reports"
        )
        assert engine.sym(removed) == removed_before


class TestS4_10EngineReadsReadOnlyAndWritesNothing:
    def test_read_uses_the_read_only_path_and_never_writes_the_cache(self, engine):
        _save("trending", _D)
        row_before = _cache_rows()
        ro = MagicMock(wraps=real_database.get_ro_connection)
        save = MagicMock()
        with (
            patch.object(real_database, "get_ro_connection", ro),
            patch.object(real_database, "save_regime_label", save),
        ):
            engine.fire_tick()
        assert ro.call_count >= 1
        save.assert_not_called()
        engine.env["db"].save_regime_label.assert_not_called()
        assert _cache_rows() == row_before


def _cache_rows() -> list[tuple]:
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        return conn.execute(
            "SELECT symphony_id, label, as_of_date, updated_at FROM regime_label_cache "
            "ORDER BY symphony_id"
        ).fetchall()
    finally:
        conn.close()


class TestS4_11StaleLabelIgnoredInBothDryRunStates:
    @pytest.mark.parametrize("mode", ["live_execution_false", "live_mode_false"])
    def test_simulated_trigger_fires_on_the_default_tick(self, engine, mode):
        _save("trending", _PREV)
        post = MagicMock()
        strat = {"params": {}, "locked_vars": {}, "live_mode": mode != "live_mode_false"}
        engine.env["db"].get_symphony_strategy.return_value = strat
        with (
            patch.object(alpha_bot_execution, "LIVE_EXECUTION", mode != "live_execution_false"),
            patch.object(alpha_bot_execution.requests, "post", post),
        ):
            assert engine.fire_tick() == _DEFAULT_TICKS
        post.assert_not_called()


class TestS4_12LiveStaleLabelSendsSellAfterTickThree:
    def test_go_to_cash_post_follows_the_third_tick_not_the_second(self, engine):
        _save("trending", _PREV)  # would sell after tick 2 if applied
        post = MagicMock(return_value=MagicMock(status_code=200))
        with (
            patch.object(alpha_bot_execution, "LIVE_EXECUTION", True),
            patch.object(alpha_bot_execution.requests, "post", post),
        ):
            for _ in range(_DEFAULT_TICKS - 1):
                engine.tick()
                assert post.call_count == 0, "sell sent on the strength of a stale label"
            engine.tick()
        assert post.call_count == 1
        assert post.call_args.args[0].endswith(f"/symphonies/{_ACTUAL_SYMPHONY_ID}/go-to-cash")


# --------------------------------------------------------------------------
# AC-5 (engine half)
# --------------------------------------------------------------------------


class TestS5_7ProducerWroteNothingForTodayEngineUsesDefault:
    def test_previous_session_label_is_never_read_as_todays(self, engine):
        _save("mean-reverting", _PREV)
        assert engine.fire_tick(max_ticks=_SPEC_TICKS["mean-reverting"]) == _DEFAULT_TICKS
        assert [c["result"] for c in engine.calls] and all(
            c["result"] is None for c in engine.calls
        )


# --------------------------------------------------------------------------
# AC-6 -- one read, no other I/O
# --------------------------------------------------------------------------


class TestS6_1OneReadPerSymphonyPerCycleAndNoOtherIO:
    _CYCLES = 3

    def _run(self, engine, with_row: bool):
        engine.seed()
        engine.calls.clear()
        if with_row:
            _save("trending", _D)
        env = engine.env
        for key in ("fetch_symphony_stats", "fetch_alpaca_history", "fetch_intraday_vwaps"):
            env[key].reset_mock()
        get = MagicMock()
        post = MagicMock()
        with (
            patch.object(alpha_bot_execution.requests, "get", get),
            patch.object(alpha_bot_execution.requests, "post", post),
            patch.object(regime_classifier, "classify_regime", side_effect=AssertionError),
        ):
            for _ in range(self._CYCLES):
                engine.tick(_ABOVE_STOP_PCT)  # non-qualifying: the symphony stays untriggered
        assert get.call_count == 0 and post.call_count == 0
        return {
            "stats": env["fetch_symphony_stats"].call_args_list,
            "history": env["fetch_alpaca_history"].call_args_list,
            "vwaps": env["fetch_intraday_vwaps"].call_args_list,
            "calls": list(engine.calls),
        }

    def test_exactly_one_keyed_read_per_cycle_with_the_engine_session_date(self, engine):
        run = self._run(engine, with_row=True)
        assert len(run["calls"]) == self._CYCLES
        for call in run["calls"]:
            assert call["args"] == (_SYMPHONY_ID,)
            assert call["kwargs"].get("session_date") == _D

    def test_other_io_is_identical_with_and_without_a_row(self, engine):
        with_row = self._run(engine, with_row=True)
        conn = sqlite3.connect(os.environ["DB_PATH"])
        try:
            conn.execute("DELETE FROM regime_label_cache")
            conn.commit()
        finally:
            conn.close()
        without_row = self._run(engine, with_row=False)
        for key in ("stats", "history", "vwaps"):
            assert with_row[key] == without_row[key], key
        assert len(with_row["stats"]) == len(without_row["stats"]) == self._CYCLES
