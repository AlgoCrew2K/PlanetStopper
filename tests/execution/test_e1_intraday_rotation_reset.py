"""
AC-2 / E-1 / Cluster 6 CASE 2 / INV-COV-5 — intraday-rotation reset.

The 6-cluster remediation closed the cross-DAY re-allocation case via
database.wipe_transient_state at the new-day boundary
(alpha_bot_execution.py:651-661). The post-audit re-review caught the
intraday-rotation gap that Cluster 6 ruled out-of-scope on the basis that
"display-only impact" — that premise is now false. The behavioural fact from
the user: Composer re-buys at ~3:50-3:55 ET in the same session after an
AlphaBot exit, making the exit-state leak path real-money on every triggered
exit.

Decision D11 (team-lead, post-audit-hardening plan) reverses the D9(a)
ruling: engine bookkeeping for intraday rotation IS in scope.

Decision D12: the detection mechanism is per-symphony holdings transition
zero -> positive on every execution cycle. Fires for both the
AlphaBot-exit-then-Composer-rebuy case (E-1) AND the untriggered
Composer-rotation case (Cluster 6 CASE 2) with one code path.

On detection the engine MUST:
  1. Reset bot_state[symphony_id] transient fields to a fresh-position
     baseline. Specifically: triggered=False, breakeven_locked=False,
     prev_return=None, high_water_mark reset to the cycle's current_return,
     hwm_hold_ticks/below_stop_count/above_tp_count/vwap_ticks/
     vwap_bleed_ticks all 0, armed/tp_armed/para_armed all False,
     stop_trigger absent, mc_history emptied, and the trigger-snapshot
     fields (triggered_at_return, triggered_at_hwm, triggered_at_stop,
     triggered_at_time, triggered_reason, triggered_basket_snapshot)
     deleted.
  2. Mint a fresh ``position_epoch`` so shadow_history rows under the new
     position do not bleed into the prior position's epoch.

The reset must NOT fire when:
  - holdings stay positive across cycles (mid-position rebalance);
  - holdings stay zero across cycles (still rotated out);
  - the symphony is carrying a position from yesterday into today
    (already handled by the daily wipe — must not double-reset).

Provenance: bot_state shapes are schema-derived from the live engine's
per-symphony loop. The fixture starts a symphony in the EXACT post-trigger
state the trigger block writes (alpha_bot_execution.py 1340-1420). No
producer-computed values are asserted; assertions pin reset SENTINELS
(False / 0 / None / key-absent / new-epoch-distinct-from-old) — all
structural facts independent of math.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import database


# ---------------------------------------------------------------------------
# Shared state factories — produce schema-correct bot_state shapes the live
# engine actually writes. Keep them parameterless and pure so each test gets
# a fresh dict (mutation across tests is a real risk on bot_state).
# ---------------------------------------------------------------------------


def _post_trigger_state() -> dict:
    """A symphony left FULLY triggered by an earlier-in-the-session AlphaBot
    exit. Mirrors alpha_bot_execution.py's per-symphony trigger block.

    current_value is set NON-zero here because production writes the
    Composer-reported value at the moment of fetch; the rotation transition
    is detected when Composer's next fetch comes back with zero (rotated
    out) and then again with positive (rotated back in). The test sequences
    those values explicitly per scenario below.
    """
    return {
        "name": "RotationSym",
        "account": "acct-rotation-001",
        "high_water_mark": 3.4,
        "shadow_hwm": 3.4,
        "prev_return": 2.8,
        "armed": True,
        "tp_armed": False,
        "para_armed": True,
        "triggered": True,
        "triggered_reason": "Trailing Stop",
        "triggered_at_return": 2.6,
        "triggered_at_hwm": 3.4,
        "triggered_at_stop": 2.5,
        "triggered_at_time": "11:00",
        "breakeven_locked": True,
        "hwm_hold_ticks": 7,
        "below_stop_count": 3,
        "above_tp_count": 0,
        "vwap_ticks": 2,
        "vwap_bleed_ticks": 1,
        "stop_trigger": 2.5,
        "mc_history": [52.0, 47.0, 39.0],
        "current_holdings": [{"ticker": "SPY", "allocation": 1.0}],
        "current_value": 0.0,        # Composer rotated the symphony OUT
        "current_return": 0.0,
        "position_epoch": "old-epoch-pre-rotation",
        "last_holdings_positive": True,   # prior cycle had positive holdings
    }


def _untriggered_rotated_out_state() -> dict:
    """Cluster 6 CASE 2 fixture: a symphony that was NOT triggered by
    AlphaBot but was rotated OUT by Composer (typical when Composer's
    strategy moves the allocation elsewhere). bot_state still carries the
    accumulated exit-guard state from the prior open position.
    """
    return {
        "name": "Case2Sym",
        "account": "acct-case2-001",
        "high_water_mark": 4.1,
        "shadow_hwm": 4.1,
        "prev_return": 3.5,
        "armed": True,
        "tp_armed": False,
        "para_armed": True,
        "triggered": False,                  # never triggered
        "breakeven_locked": True,            # MUST clear on re-entry
        "hwm_hold_ticks": 11,                # MUST clear
        "below_stop_count": 2,
        "above_tp_count": 0,
        "vwap_ticks": 1,
        "vwap_bleed_ticks": 0,
        "stop_trigger": 3.6,                 # MUST clear
        "mc_history": [],
        "current_holdings": [{"ticker": "QQQ", "allocation": 1.0}],
        "current_value": 0.0,                # rotated out by Composer
        "current_return": 0.0,
        "position_epoch": "old-epoch-case2",
        "last_holdings_positive": True,
    }


# Schema-correct: the set of transient fields the engine must wipe on
# re-entry. Derived from the trigger block + wipe_transient_state's
# canonical reset list (database.py:208-256).
_TRANSIENT_INT_ZERO_FIELDS = (
    "hwm_hold_ticks",
    "below_stop_count",
    "above_tp_count",
    "vwap_ticks",
    "vwap_bleed_ticks",
)
_TRANSIENT_BOOL_FALSE_FIELDS = (
    "triggered",
    "armed",
    "tp_armed",
    "para_armed",
    "breakeven_locked",
)
_TRANSIENT_TRIGGER_SNAPSHOT_FIELDS = (
    "triggered_reason",
    "triggered_at_return",
    "triggered_at_hwm",
    "triggered_at_stop",
    "triggered_at_time",
    "trigger_prices",
    "triggered_basket_snapshot",
)


# ---------------------------------------------------------------------------
# 1 — Pure detection helper.
#
# The plan dictates per-symphony zero -> positive holdings transition
# detection. The engine must expose a pure helper so the detection can be
# unit-tested without standing up the full execution loop. The implementer
# may name it ``detect_zero_to_positive_holdings_transition`` or
# ``is_zero_to_positive_transition`` — tests probe both common shapes.
# ---------------------------------------------------------------------------


class TestHoldingsTransitionDetectionHelperExists:
    """A pure helper for the zero -> positive holdings transition must be
    exposed so the detection is testable in isolation."""

    def test_transition_detector_is_exposed(self):
        """One of the two natural names must exist on alpha_bot_execution
        OR database (implementer's choice of module home).

        Without an injectable detector the detection is opaque and intrinsic
        to the execution loop, defeating the AC-2 unit-test strategy.
        """
        import alpha_bot_execution
        import database as _db

        candidate_names = (
            "detect_zero_to_positive_holdings_transition",
            "is_zero_to_positive_transition",
            "detect_intraday_position_reopen",
        )
        found_on = []
        for module in (alpha_bot_execution, _db):
            for name in candidate_names:
                if hasattr(module, name):
                    found_on.append((module.__name__, name))
        assert found_on, (
            "AC-2 / D12: a pure helper for the zero -> positive holdings "
            "transition must be exposed on alpha_bot_execution OR database. "
            f"Searched: {candidate_names}. Without one the detection is "
            "opaque and per-cycle unit tests cannot pin it."
        )


def _resolve_detector():
    """Resolve the transition-detector helper across acceptable name shapes
    and module homes. Skip-style fail if absent so the helper-exists test
    handles that diagnostic separately."""
    import alpha_bot_execution
    import database as _db

    candidate_names = (
        "detect_zero_to_positive_holdings_transition",
        "is_zero_to_positive_transition",
        "detect_intraday_position_reopen",
    )
    for module in (alpha_bot_execution, _db):
        for name in candidate_names:
            if hasattr(module, name):
                return getattr(module, name)
    pytest.fail(
        "Transition detector not exposed; see "
        "TestHoldingsTransitionDetectionHelperExists."
    )


class TestTransitionDetectorContract:
    """The detector takes a prior-state proxy and a current-cycle holdings
    signal and returns True iff the symphony just transitioned from zero to
    positive holdings."""

    def test_transition_fires_zero_to_positive(self):
        """Prior cycle's holdings were zero/absent; current cycle's are
        positive. The detector returns True."""
        detector = _resolve_detector()
        # The detector's signature is implementer's choice — try the two
        # natural shapes. The plan only constrains the SEMANTICS.
        try:
            result = detector(
                previous_state={"last_holdings_positive": False},
                current_holdings_positive=True,
            )
        except TypeError:
            try:
                result = detector(
                    {"last_holdings_positive": False},
                    True,
                )
            except TypeError:
                pytest.fail(
                    "Detector must accept either kwargs (previous_state, "
                    "current_holdings_positive) or two positional args."
                )
        assert result is True, (
            f"Zero -> positive transition must return True; got {result!r}."
        )

    def test_no_transition_when_holdings_stay_positive(self):
        """Mid-position rebalance: holdings positive in both cycles. Must
        NOT fire (this is the BLOCK-1 regression guard from Cluster 1 — a
        rebalance is not a new position)."""
        detector = _resolve_detector()
        try:
            result = detector(
                previous_state={"last_holdings_positive": True},
                current_holdings_positive=True,
            )
        except TypeError:
            result = detector({"last_holdings_positive": True}, True)
        assert result is False, (
            "Holdings positive both cycles (mid-position rebalance): "
            f"detector must return False; got {result!r}. A True here is "
            "the BLOCK-1 mid-position-reset defect coming back."
        )

    def test_no_transition_when_holdings_stay_zero(self):
        """Symphony stays rotated out: zero -> zero. Must NOT fire."""
        detector = _resolve_detector()
        try:
            result = detector(
                previous_state={"last_holdings_positive": False},
                current_holdings_positive=False,
            )
        except TypeError:
            result = detector({"last_holdings_positive": False}, False)
        assert result is False, (
            f"Zero -> zero (still rotated out): must return False; got "
            f"{result!r}."
        )

    def test_detector_handles_missing_last_holdings_marker(self):
        """First-ever observation of a symphony has no
        ``last_holdings_positive`` marker in the prior state dict. The
        detector must NOT crash on this case — implementer chooses the
        semantics (treat-as-transition or treat-as-fresh-init), but the
        engine-level no-double-reset property is pinned by the
        carried-position end-to-end test, not here.
        """
        detector = _resolve_detector()
        try:
            result = detector(
                previous_state={},
                current_holdings_positive=True,
            )
        except TypeError:
            result = detector({}, True)
        assert isinstance(result, bool), (
            "Detector must return a bool on a prior-state without "
            f"last_holdings_positive. Got {result!r} ({type(result).__name__})."
        )


# ---------------------------------------------------------------------------
# 2 — End-to-end scenarios via alpha_bot_execution.main().
#
# Four scenarios per plan:
#   (a) E-1: AlphaBot exit at 11:00 ET + Composer rebuy at 3:55 ET (same
#       session). MUST reset.
#   (b) Cluster 6 CASE 2: untriggered Composer rotation OUT then IN, no
#       AlphaBot exit. MUST reset.
#   (c) Carried position across day. Daily wipe handles it; the intraday
#       transition detection must NOT spuriously fire (no double-reset).
#   (d) Multi-day no-rebuy: symphony stays rotated out; the detection must
#       NOT fire mid-day on stale state.
# ---------------------------------------------------------------------------


def _build_fake_et(hour: int, minute: int, *, day: int = 21) -> datetime:
    """Build an ET datetime at 2026-05-DAY. Used for the time-of-day axis."""
    try:
        from zoneinfo import ZoneInfo
        return datetime(
            2026, 5, day, hour, minute, 0, tzinfo=ZoneInfo("America/New_York")
        )
    except Exception:  # pragma: no cover - timezone library fallback
        return datetime(
            2026, 5, day, hour, minute, 0,
            tzinfo=timezone(timedelta(hours=-4)),
        )


def _run_one_cycle(bot_state, symphonies, *, current_et, **extra_patches):
    """Drive alpha_bot_execution.main() for ONE cycle with mocked Composer +
    Alpaca + Discord. Returns the final saved bot_state.

    Avoids any live I/O. Faithfully exercises the data-phase loop (where
    AC-2's detection must live) by feeding mocked symphony stats and
    Alpaca history.
    """
    import alpha_bot_execution

    s_id = next(
        (k for k in bot_state.keys()
         if k not in ("date", "last_execution_mode", "last_market_close_snapshot",
                      "last_successful_cycle_at", "post_mortem_run")),
        None,
    )
    account = bot_state[s_id]["account"] if s_id else "acct-test"

    chart_history = {"date": bot_state.get("date"), "symphonies": {}}

    with patch.object(alpha_bot_execution, "database") as mock_db, \
         patch.object(alpha_bot_execution, "reporting"), \
         patch.object(alpha_bot_execution, "fetch_symphony_stats") as mock_fetch, \
         patch.object(alpha_bot_execution, "fetch_alpaca_history") as mock_hist, \
         patch.object(alpha_bot_execution, "fetch_intraday_vwaps") as mock_vwap, \
         patch.object(alpha_bot_execution, "get_current_et", return_value=current_et), \
         patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [account]), \
         patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "k"), \
         patch.object(alpha_bot_execution, "ALPACA_KEY", "k"), \
         patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
         patch.object(alpha_bot_execution.time, "sleep"), \
         patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]):

        mock_db.acquire_lock.return_value = True
        mock_db.load_state.return_value = bot_state
        mock_db.load_chart_history.return_value = chart_history
        mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
        mock_db.normalize_name.side_effect = lambda n: (n or "").strip().lower()
        # Pass-through wipe — leave the live database.wipe_transient_state
        # semantics in play but executed against the in-memory dict.
        mock_db.wipe_transient_state.side_effect = database.wipe_transient_state
        mock_db.mint_position_epoch.side_effect = database.mint_position_epoch
        # Pass through the (real or absent) reset/detection database surfaces
        # so AC-2's implementation choice on whether the helper lives in
        # database or alpha_bot_execution does not break the harness.
        for surface in (
            "is_new_position_open",
            "reset_symphony_position_state",
            "detect_zero_to_positive_holdings_transition",
            "is_zero_to_positive_transition",
            "detect_intraday_position_reopen",
        ):
            if hasattr(database, surface):
                getattr(mock_db, surface).side_effect = getattr(database, surface)

        mock_fetch.return_value = symphonies

        # Construct an Alpaca history payload covering every ticker the
        # symphony references; values are placeholders — AC-2's reset
        # detection is independent of return math.
        all_tickers = set()
        for sym in symphonies:
            for h in sym.get("holdings", []):
                t = h.get("ticker", "")
                clean = t.split("::")[-1].split("//")[0].replace("/", ".")
                if clean:
                    all_tickers.add(clean)
        # Also include tickers from the prior bot_state's frozen current_holdings
        # so the post-trigger frozen-tickers branch doesn't KeyError.
        for v in bot_state.values():
            if isinstance(v, dict):
                for h in v.get("current_holdings", []) or []:
                    t = h.get("ticker") or ""
                    if t and "cash" not in t.lower():
                        all_tickers.add(t)

        hist_day = current_et.strftime("%Y-%m-%d")
        mock_hist.return_value = {
            hist_day: {
                t: {"c": 100.0, "daily_ret": 0.001,
                    "high": 101.0, "low": 99.0, "close": 100.0}
                for t in all_tickers
            }
        }
        mock_vwap.return_value = {
            t: {"vwap": 100.0, "last_price": 100.0} for t in all_tickers
        }

        for key, value in extra_patches.items():
            patcher = patch.object(alpha_bot_execution, key, value)
            patcher.start()

        try:
            alpha_bot_execution.main()
        finally:
            patch.stopall()

        save_calls = mock_db.save_state.call_args_list
        assert save_calls, "save_state was never called — cycle aborted."
        return save_calls[-1].args[0]


def _make_composer_payload(
    s_id: str, *, name: str, holdings_positive: bool, current_value: float
):
    """Build a Composer fetch_symphony_stats payload entry."""
    return {
        "id": s_id,
        "name": name,
        "last_percent_change": 0.0,
        "current_value": current_value,
        "value": current_value,
        "holdings": (
            [{"ticker": "SPY", "allocation": 1.0}] if holdings_positive else []
        ),
    }


class TestE1AlphaBotExitThenComposerRebuy:
    """Scenario A — AlphaBot triggered the exit at 11:00 ET; Composer
    re-buys the same symphony at 3:55 ET. Within the same trading day."""

    def test_e1_intraday_rebuy_resets_transient_state(self):
        """The 11:00 ET trigger leaves bot_state full of stale flags
        (triggered=True, breakeven_locked=True, etc.). The 3:55 ET cycle
        sees Composer return positive holdings for the same symphony_id.
        The transition detection must fire and reset the transient fields
        before the action phase runs against the new position.

        Pins every reset field from the canonical post-trigger fixture:
        triggered, breakeven_locked, the int counters, stop_trigger key
        absent, trigger snapshot fields gone, and a fresh position_epoch
        distinct from the prior one.
        """
        s_id = "rotation-sym-01"
        bot_state = {
            "date": "2026-05-21",
            "last_execution_mode": False,
            s_id: _post_trigger_state(),
        }
        old_epoch = bot_state[s_id]["position_epoch"]

        # 13:00 ET cycle — Composer rebuy after AlphaBot's 11:00 ET exit
        # (the plan's 3:50-3:55 example is illustrative; the rebuy can
        # happen any time in the action window. 13:00 ET avoids both the
        # open window and the 15:53 rebalance blackout — those have their
        # own code paths that pre-empt the data phase).
        et = _build_fake_et(13, 0)
        symphonies = [
            _make_composer_payload(
                s_id, name="RotationSym",
                holdings_positive=True, current_value=10_000.0,
            )
        ]

        final_state = _run_one_cycle(bot_state, symphonies, current_et=et)
        sym = final_state[s_id]

        # Triggered must clear.
        assert sym.get("triggered") is False, (
            "AC-2 / E-1 VIOLATED: triggered=True survived the intraday "
            "rebuy. The new position would inherit the prior exit's "
            "freeze."
        )
        # Breakeven latch must clear.
        assert sym.get("breakeven_locked") is False, (
            "AC-2 / E-1 VIOLATED: breakeven_locked latch survived the "
            "intraday rebuy. The new position would treat the breakeven "
            "floor as already earned."
        )
        # Int counters must zero.
        for f in _TRANSIENT_INT_ZERO_FIELDS:
            assert sym.get(f, 0) == 0, (
                f"AC-2 / E-1 VIOLATED: '{f}' was not reset on intraday "
                f"rebuy (got {sym.get(f)!r})."
            )
        # stop_trigger must clear (key absent OR value None — both safe).
        assert sym.get("stop_trigger") in (None, ), (
            f"AC-2 / E-1 VIOLATED: stop_trigger floor survived the "
            f"rebuy (got {sym.get('stop_trigger')!r})."
        )
        # Trigger snapshot fields must clear.
        for f in _TRANSIENT_TRIGGER_SNAPSHOT_FIELDS:
            assert f not in sym or sym.get(f) in (None, ""), (
                f"AC-2 / E-1 VIOLATED: trigger-snapshot field '{f}' "
                f"survived the rebuy (got {sym.get(f)!r})."
            )
        # Fresh epoch.
        new_epoch = sym.get("position_epoch")
        assert new_epoch is not None, (
            "AC-2 / E-1 VIOLATED: position_epoch must be present on the "
            "reset state."
        )
        assert new_epoch != old_epoch, (
            "AC-2 / E-1 VIOLATED: position_epoch was NOT re-minted on "
            "intraday rebuy. shadow_history rows for the new position "
            "would bleed into the prior position's epoch and corrupt "
            "the trajectory query."
        )


class TestCase2UntriggeredComposerRotation:
    """Scenario B — Cluster 6 CASE 2 — symphony was NOT triggered by
    AlphaBot but Composer rotated it OUT then back IN."""

    def test_case2_untriggered_rotation_resets_state(self):
        """The position carried accumulated exit-guard state
        (breakeven_locked=True, hwm_hold_ticks=11). Composer rotated it out
        (zero holdings) and now rotates it back in. The new position must
        start clean even though triggered was never True.

        Closes Cluster 6 CASE 2 — pre-this-AC the closure was display-only;
        Decision D11 makes it real-money.
        """
        s_id = "case2-sym-01"
        bot_state = {
            "date": "2026-05-21",
            "last_execution_mode": False,
            s_id: _untriggered_rotated_out_state(),
        }
        old_epoch = bot_state[s_id]["position_epoch"]

        et = _build_fake_et(14, 30)
        symphonies = [
            _make_composer_payload(
                s_id, name="Case2Sym",
                holdings_positive=True, current_value=10_000.0,
            )
        ]

        final_state = _run_one_cycle(bot_state, symphonies, current_et=et)
        sym = final_state[s_id]

        assert sym.get("breakeven_locked") is False, (
            "AC-2 / CASE 2 VIOLATED: breakeven_locked=True survived an "
            "untriggered Composer-driven rotation. The new position "
            "would inherit the prior latch."
        )
        for f in _TRANSIENT_INT_ZERO_FIELDS:
            assert sym.get(f, 0) == 0, (
                f"AC-2 / CASE 2 VIOLATED: '{f}' was not reset on the "
                f"untriggered rotation (got {sym.get(f)!r})."
            )
        assert sym.get("stop_trigger") in (None, ), (
            f"AC-2 / CASE 2 VIOLATED: stop_trigger floor survived "
            f"untriggered rotation (got {sym.get('stop_trigger')!r})."
        )
        new_epoch = sym.get("position_epoch")
        assert new_epoch and new_epoch != old_epoch, (
            "AC-2 / CASE 2 VIOLATED: position_epoch was not re-minted "
            "on untriggered rotation. shadow_history rows bleed across "
            "epochs."
        )


class TestCarriedPositionAcrossDayNoDoubleReset:
    """Scenario C — a position that carried across a day boundary must
    NOT be double-reset by the intraday transition detection.

    The new-day wipe at alpha_bot_execution.py:651-661 already handles the
    cross-day reset. The intraday detection runs in the data phase AFTER the
    wipe; it must recognise the post-wipe state as 'fresh, not a
    transition' and leave the freshly-stamped epoch alone.
    """

    def test_carried_position_first_cycle_post_wipe_does_not_double_reset(self):
        """First cycle of a new trading day after the daily wipe. Holdings
        are positive (the carried position). The position_epoch stamped by
        wipe_transient_state must SURVIVE — the intraday detection must
        NOT mint yet another epoch on top of it.

        This pins the 'idempotent within a cycle' aspect of D12.
        """
        s_id = "carry-sym-01"
        # Yesterday's bot_state — date is prior day.
        prior_day_state = {
            "name": "CarrySym",
            "account": "acct-carry-001",
            "high_water_mark": -999.0,        # placeholder; wipe will reset
            "shadow_hwm": -999.0,
            "prev_return": None,
            "armed": False,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,
            "breakeven_locked": False,
            "hwm_hold_ticks": 0,
            "below_stop_count": 0,
            "above_tp_count": 0,
            "vwap_ticks": 0,
            "vwap_bleed_ticks": 0,
            "mc_history": [],
            "current_holdings": [{"ticker": "SPY", "allocation": 1.0}],
            "current_value": 10_000.0,
            "current_return": 0.5,
            "position_epoch": "carried-epoch-from-yesterday",
            "last_holdings_positive": True,
        }
        bot_state = {
            "date": "2026-05-20",                 # yesterday
            "last_execution_mode": False,
            s_id: prior_day_state,
        }

        et = _build_fake_et(10, 30, day=21)        # new day
        symphonies = [
            _make_composer_payload(
                s_id, name="CarrySym",
                holdings_positive=True, current_value=10_050.0,
            )
        ]

        final_state = _run_one_cycle(bot_state, symphonies, current_et=et)
        sym = final_state[s_id]

        # The daily wipe will have minted a fresh epoch (covers AC-3 from
        # Cluster 1 — was_triggered OR position_epoch absent). The carried
        # position is open, so wipe_transient_state stamps a fresh epoch
        # (because the prior epoch is present — but the wipe's conditional
        # only re-stamps on triggered=True; the carried position was NOT
        # triggered, so the prior epoch should SURVIVE the daily wipe).
        # Either way, the intraday detection must NOT mint AGAIN on top.
        epoch_after_cycle = sym.get("position_epoch")
        assert epoch_after_cycle is not None, (
            "Carried position must retain a position_epoch."
        )
        # Specifically: the epoch should be the one wipe_transient_state
        # left in place — NOT a new one minted by the intraday detection.
        # The wipe_transient_state conditional preserves the prior epoch
        # when was_triggered is False (database.py:230-237), so the carried
        # position's prior epoch survives.
        assert epoch_after_cycle == "carried-epoch-from-yesterday", (
            "AC-2 carried-position safety VIOLATED: a NEW position_epoch "
            f"({epoch_after_cycle!r}) was minted on top of the carried "
            "position's epoch. The intraday transition detection must "
            "NOT fire when the data-phase loop just received the same "
            "positive-holdings symphony it saw yesterday (an untriggered "
            "carry-across is a continuation, not a re-open)."
        )

    def test_mid_position_holdings_change_does_not_reset(self):
        """Mid-cycle Composer rebalance (BLOCK-1 regression guard): the
        symphony stays in the same live position; its holdings list changes
        from {SPY} to {SPY, QQQ}. Holdings remain positive (zero-to-positive
        never fires). The accumulated exit-guard state must survive."""
        s_id = "mid-rebalance-sym-01"
        live_state = {
            "name": "MidRebalanceSym",
            "account": "acct-mid-rebal-001",
            "high_water_mark": 3.0,
            "shadow_hwm": 3.0,
            "prev_return": 2.4,
            "armed": True,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,
            "breakeven_locked": True,
            "hwm_hold_ticks": 6,
            "below_stop_count": 1,
            "above_tp_count": 0,
            "vwap_ticks": 0,
            "vwap_bleed_ticks": 0,
            "mc_history": [],
            "current_holdings": [{"ticker": "SPY", "allocation": 1.0}],
            "current_value": 10_000.0,
            "current_return": 3.0,
            "position_epoch": "live-position-epoch-stable",
            "last_holdings_positive": True,
        }
        bot_state = {
            "date": "2026-05-21",
            "last_execution_mode": False,
            s_id: live_state,
        }

        # Same day, mid-cycle, holdings list now multi-ticker.
        et = _build_fake_et(13, 0)
        rebalanced = {
            "id": s_id,
            "name": "MidRebalanceSym",
            "last_percent_change": 0.031,
            "current_value": 10_310.0,
            "value": 10_310.0,
            "holdings": [
                {"ticker": "SPY", "allocation": 0.5},
                {"ticker": "QQQ", "allocation": 0.5},
            ],
        }
        final_state = _run_one_cycle(bot_state, [rebalanced], current_et=et)
        sym = final_state[s_id]

        # breakeven_locked / hwm_hold_ticks must survive — same position.
        assert sym.get("breakeven_locked") is True, (
            "AC-2 BLOCK-1 VIOLATED: mid-position rebalance reset "
            "breakeven_locked. Holdings stayed positive (no zero -> positive "
            "transition); the live latch must survive."
        )
        assert sym.get("hwm_hold_ticks", 0) >= 6, (
            "AC-2 BLOCK-1 VIOLATED: mid-position rebalance reset "
            f"hwm_hold_ticks (got {sym.get('hwm_hold_ticks')!r}). "
            "Accumulated breakeven-hold progress must survive a "
            "rebalance."
        )
        # Epoch must be the SAME — the detection didn't fire.
        assert sym.get("position_epoch") == "live-position-epoch-stable", (
            "AC-2 BLOCK-1 VIOLATED: position_epoch was re-minted on a "
            "mid-position rebalance. The intraday detection wrongly "
            "fired on a positive -> positive transition."
        )


class TestNoRebuyMidDayDoesNotResetMidCycle:
    """Scenario D — the symphony stays rotated out across cycles. The
    detection must NOT spuriously fire mid-day on a stale-state cycle."""

    def test_zero_to_zero_does_not_reset(self):
        """First cycle: bot_state shows last_holdings_positive=False (rotated
        out earlier in the day). Composer's current fetch still returns no
        holdings. No transition. Transient state stays as-is; no new
        epoch.
        """
        s_id = "no-rebuy-sym-01"
        rotated_out_state = {
            "name": "NoRebuySym",
            "account": "acct-no-rebuy-001",
            "high_water_mark": 2.0,
            "shadow_hwm": 2.0,
            "prev_return": 1.5,
            "armed": False,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,
            "breakeven_locked": False,
            "hwm_hold_ticks": 0,
            "below_stop_count": 0,
            "above_tp_count": 0,
            "vwap_ticks": 0,
            "vwap_bleed_ticks": 0,
            "mc_history": [],
            "current_holdings": [],
            "current_value": 0.0,
            "current_return": 0.0,
            "position_epoch": "still-rotated-out-epoch",
            "last_holdings_positive": False,
        }
        bot_state = {
            "date": "2026-05-21",
            "last_execution_mode": False,
            s_id: rotated_out_state,
        }
        et = _build_fake_et(13, 15)
        symphonies = [
            _make_composer_payload(
                s_id, name="NoRebuySym",
                holdings_positive=False, current_value=0.0,
            )
        ]
        final_state = _run_one_cycle(bot_state, symphonies, current_et=et)
        sym = final_state[s_id]
        # Epoch unchanged — no transition fired.
        assert sym.get("position_epoch") == "still-rotated-out-epoch", (
            "AC-2 VIOLATED: a stale-state zero -> zero cycle spuriously "
            "minted a new position_epoch. The detection must require an "
            "actual zero -> POSITIVE transition."
        )


# ---------------------------------------------------------------------------
# 3 — last_holdings_positive bookkeeping invariant.
#
# For the detection to fire correctly on the NEXT cycle, the engine must
# update last_holdings_positive on every cycle. Pin that the field is set
# to the current cycle's positive-holdings boolean.
# ---------------------------------------------------------------------------


class TestLastHoldingsPositiveBookkeeping:
    """The engine must persist the current cycle's positive-holdings
    boolean so the NEXT cycle's detector has accurate prior state."""

    def test_last_holdings_positive_updated_to_true_when_holdings_present(self):
        """After a cycle with positive holdings, the persisted state must
        carry last_holdings_positive=True (the canonical D12 field name).

        Start with a symphony state that has NO last_holdings_positive
        marker so a passing assertion proves the ENGINE wrote it — a
        pre-seeded True would pass trivially.
        """
        s_id = "bookkeeping-sym-01"
        seed_state = _post_trigger_state()
        # Strip the pre-seeded marker so the engine's write is the only
        # path that can produce a True.
        seed_state.pop("last_holdings_positive", None)
        bot_state = {
            "date": "2026-05-21",
            "last_execution_mode": False,
            s_id: seed_state,
        }
        et = _build_fake_et(14, 30)
        symphonies = [
            _make_composer_payload(
                s_id, name="RotationSym",
                holdings_positive=True, current_value=10_000.0,
            )
        ]
        final_state = _run_one_cycle(bot_state, symphonies, current_et=et)
        sym = final_state[s_id]
        assert sym.get("last_holdings_positive") is True, (
            "AC-2 / D12 VIOLATED: the engine must persist "
            "last_holdings_positive=True after a cycle with positive "
            f"holdings, so the next cycle's detector has accurate prior "
            f"state. Got {sym.get('last_holdings_positive')!r}."
        )

    def test_last_holdings_positive_updated_to_false_when_holdings_absent(self):
        """After a cycle with zero holdings, last_holdings_positive must
        be False so the NEXT cycle's positive-holdings reading correctly
        triggers a zero -> positive transition."""
        s_id = "bookkeeping-sym-02"
        live_with_holdings = {
            "name": "BookkeepingSym",
            "account": "acct-bookkeeping-002",
            "high_water_mark": 1.0,
            "shadow_hwm": 1.0,
            "prev_return": 0.5,
            "armed": False,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,
            "breakeven_locked": False,
            "hwm_hold_ticks": 0,
            "below_stop_count": 0,
            "above_tp_count": 0,
            "vwap_ticks": 0,
            "vwap_bleed_ticks": 0,
            "mc_history": [],
            "current_holdings": [{"ticker": "SPY", "allocation": 1.0}],
            "current_value": 10_000.0,
            "current_return": 1.0,
            "position_epoch": "pre-rotation-out-epoch",
            "last_holdings_positive": True,
        }
        bot_state = {
            "date": "2026-05-21",
            "last_execution_mode": False,
            s_id: live_with_holdings,
        }
        et = _build_fake_et(15, 0)
        symphonies = [
            _make_composer_payload(
                s_id, name="BookkeepingSym",
                holdings_positive=False, current_value=0.0,
            )
        ]
        final_state = _run_one_cycle(bot_state, symphonies, current_et=et)
        sym = final_state[s_id]
        assert sym.get("last_holdings_positive") is False, (
            "AC-2 / D12 VIOLATED: the engine must persist "
            "last_holdings_positive=False after a cycle with zero "
            "holdings, so the NEXT cycle correctly detects the rebuy "
            f"transition. Got {sym.get('last_holdings_positive')!r}."
        )
