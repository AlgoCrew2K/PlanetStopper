"""
Tests for the per-symphony cross-position exit-state reset (the CRITICAL
audit finding C-2).

REWRITTEN after the risk-engine-specialist's C-2 ruling.

THE C-2 STORY, RESOLVED:

  The audit's CRITICAL C-2 finding was that `triggered` is never reset in
  the alpha_bot_execution.py engine path — so a symphony_id re-allocated a
  new position could re-enter carrying a stale triggered=True + stale
  stop_trigger, leaving the new position mis-protected.

  Cluster 1's first GREEN attempt added a per-symphony "position-open
  detection" — is_new_position_open + reset_symphony_position_state, keyed
  on a hash of the symphony's CURRENT holding tickers. quant-code-reviewer's
  BLOCK-1 and the risk-engine-specialist's domain ruling established that
  this mechanism is WRONG:

    - A Composer symphony is a rebalancing strategy; its holding-ticker set
      changes WITHIN a single position by design. The engine even maintains
      a separately-frozen `current_holdings` snapshot (alpha_bot_execution.py
      :752/825/1352) precisely because live holdings drift — that snapshot
      would be pointless if holdings were immutable per position.
    - A composition hash of LIVE holdings is therefore a REBALANCE detector,
      not a position identity. Keying the reset on it fires
      reset_symphony_position_state MID-POSITION on a normal rebalance —
      wiping triggered / breakeven_locked / the tick counters on a live
      in-flight position. That is a real-money mis-protection and a direct
      AC-2 violation ("reset fires exactly once per new position, never
      mid-position").
    - There is no genuine intraday position-open signal to key on instead:
      a triggered symphony is FROZEN intraday (the TRUE SHADOW RETURN
      OVERRIDE) and never re-opens as a live position the same day; the
      Composer API exposes no verified position-open field.

  RESOLUTION: the per-symphony composition-change detection is REMOVED
  entirely. The audit's C-2 CRITICAL is correctly remediated by the
  pre-existing database.wipe_transient_state, which IS the engine-path
  reset: it resets triggered=False, deletes stop_trigger, and zeroes the
  six per-position counters. wipe_transient_state runs at the new-day
  boundary and on an exec-mode toggle (alpha_bot_execution.py:643/655) —
  and every genuine re-allocation of a symphony_id crosses one of those
  boundaries (intraday a triggered symphony stays frozen). So C-2 is
  closed, and the cluster's C-2 work is TEST COVERAGE of that existing
  mechanism — not new detection code.

This module therefore pins:
  1. wipe_transient_state genuinely resets the audit's exact C-2 field set.
  2. A same-basket cross-day re-open starts clean (via wipe_transient_state).
  3. A mid-position holdings rebalance does NOT wipe live exit-guard state
     (the BLOCK-1 regression guard — passes because the composition-change
     detection is gone).
  4. The removed composition-hash detection does not creep back.

Provenance: bot_state dicts are schema-derived from the shape the engine
writes (alpha_bot_execution.py per-symphony loop + the trigger block). No
producer-computed values are asserted — assertions pin reset SENTINELS
(False / 0 / key-absent), which are structural.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime
from unittest.mock import patch

import pytest

import database


# The six per-position transient fields the audit's C-2 finding names.
_TRANSIENT_INT_FIELDS = (
    "hwm_hold_ticks",
    "below_stop_count",
    "vwap_ticks",
    "vwap_bleed_ticks",
)
_TRANSIENT_BOOL_FIELDS = (
    "triggered",
    "breakeven_locked",
)


def _dirty_symphony_state() -> dict:
    """A bot_state symphony sub-dict left in a fully-triggered, dirty state —
    the exact shape the live engine persists after a trigger fires
    (alpha_bot_execution.py trigger block).
    """
    return {
        "name": "Rotated Symphony",
        "account": "acct-rotate-001",
        "high_water_mark": -999.0,          # post-trigger sentinel
        "shadow_hwm": 3.2,
        "prev_return": 2.1,
        "armed": False,
        "tp_armed": False,
        "para_armed": True,
        "triggered": True,                  # STALE — must clear
        "triggered_reason": "Trailing Stop",
        "breakeven_locked": True,           # STALE — must clear
        "hwm_hold_ticks": 8,                # STALE — must clear
        "below_stop_count": 2,              # STALE — must clear
        "vwap_ticks": 3,                    # STALE — must clear
        "vwap_bleed_ticks": 1,              # STALE — must clear
        "stop_trigger": 2.5,                # STALE — must be DELETED
        "mc_history": [55.0, 61.0, 48.0],
        "current_holdings": [{"ticker": "SPY", "allocation": 1.0}],
    }


# ===========================================================================
# 1 — wipe_transient_state resets the audit's exact C-2 field set.
#
# This is the real C-2 regression guard: the audit's CRITICAL was "triggered
# is never reset in the engine path." wipe_transient_state IS that reset
# path. Pin that it clears every field the audit named so the C-2 closure
# cannot silently regress.
# ===========================================================================


class TestWipeTransientStateClosesC2:
    """C-2: wipe_transient_state is the engine-path reset that closes the
    audit's CRITICAL — pin the exact field set it must clear."""

    def test_wipe_transient_state_resets_all_six_exit_guard_fields_and_deletes_stop_trigger(
        self,
    ):
        """
        C-2 (the core regression guard): wipe_transient_state must reset the
        SIX per-position transient exit-guard fields the audit named —
        triggered, breakeven_locked, hwm_hold_ticks, below_stop_count,
        vwap_ticks, vwap_bleed_ticks — AND delete the stop_trigger key.

        The audit's CRITICAL C-2 finding was that a re-allocated symphony_id
        could carry stale triggered=True + stop_trigger into a new position.
        wipe_transient_state is the engine-path reset (called at the new-day
        boundary and on an exec-mode toggle) that prevents that. This test
        pins its contract directly.

        A symphony left in a fully-triggered dirty state must come out of
        wipe_transient_state fully reset.
        """
        bot_state = {
            "date": "2026-05-20",
            "rotated_sym": _dirty_symphony_state(),
        }
        dirty = bot_state["rotated_sym"]
        # Sanity: the fixture starts genuinely dirty.
        assert dirty["triggered"] is True
        assert dirty["breakeven_locked"] is True
        assert "stop_trigger" in dirty
        for f in _TRANSIENT_INT_FIELDS:
            assert dirty[f] != 0

        database.wipe_transient_state(bot_state)

        cleaned = bot_state["rotated_sym"]
        assert cleaned["triggered"] is False, (
            "C-2 VIOLATED: wipe_transient_state did not reset triggered. "
            "This is the engine-path reset the audit's CRITICAL requires."
        )
        assert cleaned["breakeven_locked"] is False, (
            "C-2 VIOLATED: wipe_transient_state did not reset "
            "breakeven_locked — a new position would inherit the latch."
        )
        for field in _TRANSIENT_INT_FIELDS:
            assert cleaned[field] == 0, (
                f"C-2 VIOLATED: wipe_transient_state left a stale "
                f"'{field}'={cleaned[field]!r} — a re-allocated position "
                f"would inherit it and could trip a confirmation gate it "
                f"never earned."
            )
        assert cleaned.get("stop_trigger") is None, (
            "C-2 VIOLATED: wipe_transient_state did not delete stop_trigger. "
            "A new position must not inherit the prior position's stop floor "
            "(audit C-2; the compute_breakeven_update HWM-anchored stop reads "
            "no prior floor, but a stale persisted value would still mislead "
            "telemetry / any future consumer)."
        )

    def test_wipe_transient_state_resets_triggered_reason(self):
        """
        C-2: wipe_transient_state must also drop triggered_reason — a stale
        exit-reason string from a prior position is meaningless for the
        re-allocated one.
        """
        bot_state = {"date": "2026-05-20", "s": _dirty_symphony_state()}
        assert bot_state["s"]["triggered_reason"] == "Trailing Stop"
        database.wipe_transient_state(bot_state)
        assert bot_state["s"].get("triggered_reason") in (None, ""), (
            "wipe_transient_state must clear the stale triggered_reason."
        )

    def test_wipe_transient_state_preserves_identity_fields(self):
        """
        C-2 scope guard: wipe_transient_state clears ONLY transient fields —
        identity / accounting fields (name, account) survive. Catches an
        over-broad wipe.
        """
        bot_state = {"date": "2026-05-20", "s": _dirty_symphony_state()}
        name_before = bot_state["s"]["name"]
        account_before = bot_state["s"]["account"]
        database.wipe_transient_state(bot_state)
        assert bot_state["s"]["name"] == name_before
        assert bot_state["s"]["account"] == account_before


# ===========================================================================
# 2 — A same-basket cross-day re-open starts clean.
# ===========================================================================


class TestSameBasketCrossDayReopen:
    """C-2: a symphony that triggers one day and re-opens the same basket
    after a day boundary starts the new position clean — via the new-day
    wipe_transient_state."""

    def test_same_basket_cross_day_reopen_starts_clean(self):
        """
        C-2: pin WHY a same-basket cross-day re-open is safe.

        A symphony triggers (exits) one day; the SAME symphony_id is
        re-allocated a new position with the SAME basket after a new-day
        boundary. The new day fires wipe_transient_state (the engine calls
        it at alpha_bot_execution.py:655 when bot_state['date'] changes),
        which resets triggered=False + deletes stop_trigger + zeroes the
        counters. The re-opened position therefore starts clean.

        This documents the mechanism that closes C-2 for the most common
        rotation case — a symphony re-entering its own holdings — so a
        future maintainer who removes the day-boundary wipe sees this test
        break and knows why it matters.
        """
        # A symphony that ended the prior day triggered, with stale fields.
        bot_state = {
            "date": "2026-05-20",
            "some_symphony_id": _dirty_symphony_state(),
        }
        dirty = bot_state["some_symphony_id"]
        assert dirty["triggered"] is True, "fixture: prior day ended triggered"
        assert "stop_trigger" in dirty, "fixture: stale stop_trigger present"

        # New-day boundary fires wipe_transient_state.
        database.wipe_transient_state(bot_state)

        reopened = bot_state["some_symphony_id"]
        assert reopened["triggered"] is False, (
            "C-2 cross-day safety VIOLATED: wipe_transient_state did not "
            "reset triggered on the new-day boundary."
        )
        assert reopened.get("stop_trigger") is None, (
            "C-2 cross-day safety VIOLATED: stale stop_trigger floor "
            "survived the new-day boundary."
        )
        for field in _TRANSIENT_INT_FIELDS:
            assert reopened[field] == 0, (
                f"C-2 cross-day safety: wipe_transient_state left a stale "
                f"'{field}' — the re-opened position would inherit it."
            )


# ===========================================================================
# 3 — BLOCK-1 regression guard: a mid-position holdings rebalance must NOT
# wipe live exit-guard state.
#
# Once the composition-change detection is removed, no engine path resets a
# live position's exit-guard state on a holdings change. This test pins that
# — it was RED while the composition-hash detector existed (a rebalance
# flipped the hash and fired the reset); it PASSES once the detector is gone.
# ===========================================================================


class TestRebalanceDoesNotResetLivePosition:
    """BLOCK-1: a mid-position holdings rebalance must not wipe a live
    position's accumulated exit-guard state."""

    def test_mid_position_holdings_change_does_not_reset_exit_guard_state(self):
        """
        BLOCK-1 (quant-code-reviewer) / AC-2: drive the engine main() for
        ONE symphony_id that stays in the SAME live position, but whose
        holdings differ from the prior cycle (the strategy rebalanced — a
        legitimate mid-position event by Composer's design, NOT a re-open).

        The persisted bot_state seeds a LIVE (not triggered) position that
        has already latched breakeven (breakeven_locked=True) and
        accumulated hold ticks (hwm_hold_ticks=6). That per-position
        exit-guard state MUST survive a rebalance.

        While the composition-hash position-open detection existed, a
        holdings change flipped the identity hash, is_new_position_open
        returned True, and reset_symphony_position_state wiped
        breakeven_locked / hwm_hold_ticks on the live position — a
        real-money mis-protection. After the detection is removed, no engine
        path resets a live position on a holdings change, so the state
        survives.

        RED while the composition-change detection exists; GREEN once it is
        removed. AC-2: "the reset fires exactly once per new position, never
        mid-position" — and a rebalance is not a new position.
        """
        import alpha_bot_execution

        symphony_id = "sym-mid-position-rebalance-001"
        account_id = "acct-mid-position-rebalance-001"

        mid_position_state = {
            "name": "Mid-Position Rebalance Symphony",
            "account": account_id,
            "high_water_mark": 3.0,
            "shadow_hwm": 3.0,
            "prev_return": 2.4,
            "armed": True,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,             # LIVE position, not exited
            "breakeven_locked": True,       # latched mid-position — MUST survive
            "hwm_hold_ticks": 6,            # accumulated — MUST survive
            "below_stop_count": 1,
            "vwap_ticks": 0,
            "vwap_bleed_ticks": 0,
            "mc_history": [],
            "current_holdings": [{"ticker": "SPY", "allocation": 1.0}],
        }
        # Seed the prior-cycle position_identity as the composition hash of
        # the prior {SPY} holding set. While the composition-hash detection
        # exists, the engine compares this cycle's {SPY,QQQ} hash against it,
        # finds a mismatch, and (defectively) fires the reset mid-position —
        # the BLOCK-1 defect. After the detection is removed the engine never
        # reads position_identity, so this seed is harmless. Without it the
        # symphony looks like a first observation and the test has no teeth.
        # Use database.compute_composition_hash (database.py:1851) — the
        # canonical composition-hash for the port_state table. The previous
        # port_selector.composition_hash was a byte-for-byte duplicate algorithm
        # (sha256 of comma-joined sorted ids, hex[:16]) that lived inside a
        # now-deleted orphan module (S3-AUDIT-005). The fallback sentinel is
        # retained in case the helper is unavailable for any reason — it still
        # differs from any {SPY,QQQ} hash so the test keeps its teeth.
        try:
            from database import compute_composition_hash as _ch
            mid_position_state["position_identity"] = _ch(["SPY"])
        except Exception:  # pragma: no cover
            mid_position_state["position_identity"] = "prior-spy-only-identity"

        bot_state = {
            "date": "2026-05-21",
            "last_execution_mode": False,
            symphony_id: mid_position_state,
        }

        # The SAME symphony, SAME position — strategy rebalanced to {SPY,QQQ}.
        rebalanced_payload = {
            "id": symphony_id,
            "name": "Mid-Position Rebalance Symphony",
            "last_percent_change": 0.031,   # +3.1% — position continuing
            "current_value": 10000.0,
            "holdings": [
                {"ticker": "SPY", "allocation": 0.5},
                {"ticker": "QQQ", "allocation": 0.5},
            ],
        }

        try:
            from zoneinfo import ZoneInfo
            _et = datetime(
                2026, 5, 21, 12, 0, 0, tzinfo=ZoneInfo("America/New_York")
            )
        except Exception:  # pragma: no cover
            from datetime import timedelta, timezone
            _et = datetime(
                2026, 5, 21, 12, 0, 0, tzinfo=timezone(timedelta(hours=-4))
            )

        with patch.object(alpha_bot_execution, "database") as mock_db, \
             patch.object(alpha_bot_execution, "reporting"), \
             patch.object(alpha_bot_execution, "fetch_symphony_stats") as mock_fetch, \
             patch.object(alpha_bot_execution, "fetch_alpaca_history") as mock_hist, \
             patch.object(alpha_bot_execution, "fetch_intraday_vwaps") as mock_vwap, \
             patch.object(alpha_bot_execution, "get_current_et", return_value=_et), \
             patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [account_id]), \
             patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "k"), \
             patch.object(alpha_bot_execution, "ALPACA_KEY", "k"), \
             patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
             patch.object(alpha_bot_execution.time, "sleep"), \
             patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]):

            mock_db.acquire_lock.return_value = True
            mock_db.load_state.return_value = bot_state
            mock_db.load_chart_history.return_value = {
                "date": "2026-05-21", "symphonies": {}
            }
            mock_db.get_symphony_strategy.return_value = {
                "params": {}, "locked_vars": {}
            }
            mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
            mock_db.wipe_transient_state.side_effect = lambda s: s
            # Wire the REAL C-2 functions through the mocked database so the
            # engine genuinely exercises any composition-change detection it
            # still has. If the detection has been removed, these functions
            # no longer exist on `database` and the engine never calls them —
            # the getattr guard keeps the test valid in both states (before
            # and after the BLOCK-1 removal). Without this wiring the engine
            # would call bare MagicMocks and the test would pass for the
            # WRONG reason (the real reset never running).
            if hasattr(database, "is_new_position_open"):
                mock_db.is_new_position_open.side_effect = (
                    database.is_new_position_open
                )
            if hasattr(database, "reset_symphony_position_state"):
                mock_db.reset_symphony_position_state.side_effect = (
                    database.reset_symphony_position_state
                )

            mock_fetch.return_value = [rebalanced_payload]
            mock_hist.return_value = {
                "2026-05-21": {
                    "SPY": {"c": 500.0, "daily_ret": 0.001,
                            "high": 501.0, "low": 499.0, "close": 500.0},
                    "QQQ": {"c": 400.0, "daily_ret": 0.001,
                            "high": 401.0, "low": 399.0, "close": 400.0},
                }
            }
            mock_vwap.return_value = {
                "SPY": {"vwap": 500.0, "last_price": 500.0},
                "QQQ": {"vwap": 400.0, "last_price": 400.0},
            }

            alpha_bot_execution.main()

            save_calls = mock_db.save_state.call_args_list
            assert save_calls, "save_state was never called — cycle aborted."
            final_state = save_calls[-1].args[0][symphony_id]

        assert final_state.get("breakeven_locked") is True, (
            "AC-2 BLOCK-1 VIOLATED: a mid-position holdings change reset the "
            "live position's breakeven_locked latch (now "
            f"{final_state.get('breakeven_locked')!r}). The symphony stayed "
            "in the SAME position — its strategy merely rebalanced its "
            "holdings. No engine path may reset a live position's exit-guard "
            "state on a holdings change."
        )
        assert final_state.get("hwm_hold_ticks", 0) >= 6, (
            "AC-2 BLOCK-1 VIOLATED: a mid-position holdings change reset "
            f"hwm_hold_ticks (now {final_state.get('hwm_hold_ticks')!r}, was "
            "6+ going in). Accumulated breakeven-hold progress must survive a "
            "mid-position rebalance."
        )
        assert final_state.get("triggered") is False, (
            "Sanity: the position was never triggered; it must still be False."
        )


# ===========================================================================
# 4 — The removed composition-hash detection must not creep back.
# ===========================================================================


class TestNoCompositionHashPositionDetection:
    """BLOCK-1 / C-2: the composition-hash position-open detection was
    removed because a live-holdings hash is a rebalance detector, not a
    position identity. Pin the removal so it cannot silently return."""

    def test_no_composition_hash_position_detection_remains(self):
        """
        BLOCK-1 resolution guard: the per-symphony composition-change
        position-open detection — is_new_position_open and the
        position_identity construction — must NOT be present in
        alpha_bot_execution.py.

        Keying a per-position reset on a hash of LIVE holdings wipes a live
        position's exit-guard state on a normal Composer rebalance (BLOCK-1,
        confirmed by the risk-engine-specialist domain ruling). The
        mechanism was removed; C-2 is closed by wipe_transient_state. This
        test AST-scans alpha_bot_execution.py to assert the removed names do
        not reappear.

        RED while the composition-hash detection exists; GREEN once removed.
        """
        import alpha_bot_execution as _abe

        source = pathlib.Path(_abe.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        forbidden = {"is_new_position_open", "position_identity"}
        offenders: list[str] = []
        for node in ast.walk(tree):
            # Attribute access: database.is_new_position_open(...)
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                offenders.append(f"attribute '{node.attr}' at line {node.lineno}")
            # Bare name / subscript-key string literals.
            if isinstance(node, ast.Name) and node.id in forbidden:
                offenders.append(f"name '{node.id}' at line {node.lineno}")
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in forbidden
            ):
                offenders.append(
                    f"string '{node.value}' at line {node.lineno}"
                )

        assert not offenders, (
            "The composition-hash position-open detection has crept back "
            "into alpha_bot_execution.py: "
            f"{offenders}. A live-holdings composition hash is a REBALANCE "
            "detector, not a position identity — keying a per-position reset "
            "on it wipes a live position's exit-guard state on a normal "
            "rebalance (BLOCK-1). C-2 is closed by wipe_transient_state; the "
            "composition-change detection must stay removed."
        )

    def test_is_new_position_open_is_removed_from_database(self):
        """
        BLOCK-1 resolution guard: database.is_new_position_open had exactly
        one caller (the removed alpha_bot_execution.py detection block). With
        no caller it is dead code; per the project 'no unused code' standard
        it must be removed from database.py.

        RED while is_new_position_open still exists; GREEN once removed.
        """
        assert not hasattr(database, "is_new_position_open"), (
            "database.is_new_position_open still exists. Its only caller — "
            "the composition-hash position-open detection in "
            "alpha_bot_execution.py — was removed (BLOCK-1). An unused "
            "detector is dead code; remove it. C-2 is closed by "
            "wipe_transient_state."
        )
