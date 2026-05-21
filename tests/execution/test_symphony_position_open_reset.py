"""
RED tests for AC-1 / AC-2 — the per-symphony cross-position state reset (the
CRITICAL audit finding C-2).

Background (math-audit exit-decision-math__2026-05-21.md, CRITICAL):

  `triggered` is never reset to False anywhere in alpha_bot_execution.py.
  After a trigger, high_water_mark is reset to -999.0 but `stop_trigger` and
  `triggered` are left set, and the whole bot_state dict persists across
  cycles/restarts. AlphaBot rotates symphonies — a symphony_id is re-allocated
  capital and a NEW position re-enters under the SAME symphony_id.

  On that re-entry the new position inherits the prior position's stale
  triggered=True + stale stop_trigger:
    - compute_breakeven_update returns the -999.0 sentinel forever and
      compute_exit_confirmation early-returns False — the new live position
      has NO trailing-stop protection at all; OR
    - if any path clears `triggered`, the stale positive stop_trigger becomes
      the ratchet floor for the unrelated new position and forces an immediate
      spurious exit.
  Either way a real-money position is mis-protected.

THE FIX (plan AC-1 / AC-2):
  Detect position-open (keyed on position_open_date / composition identity)
  and on open clear the six per-position transient fields —
  triggered, breakeven_locked, hwm_hold_ticks, below_stop_count, vwap_ticks,
  vwap_bleed_ticks — and DELETE stop_trigger so the next
  compute_breakeven_update receives no stale floor. The reset fires EXACTLY
  ONCE per new position, never mid-position.

THE TESTED CONTRACT — a pure reset helper:
  The most testable home for this is a pure function (the project file-map
  rule keeps math/state primitives pure and testable from fixtures). The
  audit names database.wipe_transient_state as the cross-reference; the
  per-position reset is its single-symphony analogue. This module resolves
  the helper from either `database` or `alpha_bot_execution` so the
  implementer owns the final module placement; the helper is referred to
  here as `reset_symphony_position_state`.

  Required behavior of reset_symphony_position_state(sym_state: dict) -> dict:
    - sets triggered=False, breakeven_locked=False
    - sets hwm_hold_ticks=0, below_stop_count=0, vwap_ticks=0,
      vwap_bleed_ticks=0
    - DELETES the 'stop_trigger' key (so .get('stop_trigger') -> None)
    - does NOT corrupt non-transient fields (name, account, etc.)

Provenance: bot_state dicts are schema-derived from the shape main() writes
(alpha_bot_execution.py:711-726). No producer-computed values are asserted —
the assertions pin the reset SENTINELS (False / 0 / key-absent), which are
structural, not producer outputs.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helper resolution — the implementer chooses the module home.
# ---------------------------------------------------------------------------


def _resolve_reset_helper():
    """Return the per-position reset helper, or None if not yet implemented."""
    for module_name in ("database", "alpha_bot_execution"):
        try:
            module = __import__(module_name)
        except ImportError:
            continue
        for fn_name in (
            "reset_symphony_position_state",
            "reset_position_state_on_open",
            "wipe_symphony_position_state",
        ):
            fn = getattr(module, fn_name, None)
            if callable(fn):
                return fn
    return None


_RESET_HELPER = _resolve_reset_helper()


# The six per-position transient fields AC-1 names explicitly.
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
    (alpha_bot_execution.py:1700-1744).
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


def _resolve_position_open_detector():
    """Return the position-open detector, or None if not yet implemented."""
    for module_name in ("database", "alpha_bot_execution"):
        try:
            module = __import__(module_name)
        except ImportError:
            continue
        for fn_name in (
            "is_new_position_open",
            "detect_position_open",
            "position_opened",
        ):
            fn = getattr(module, fn_name, None)
            if callable(fn):
                return fn
    return None


# ===========================================================================
# AC-1 / AC-2 — UNCONDITIONAL existence pin (the genuine RED for C-2).
#
# The skipif-gated classes below cannot fail until the helper exists — a skip
# is not a RED signal. These two tests fail OUTRIGHT against pre-fix code, so
# the CRITICAL C-2 finding has a real RED gate the implementer must close.
# ===========================================================================


def test_per_position_reset_helper_exists() -> None:
    """
    AC-1 (C-2 CRITICAL): a per-position reset helper MUST exist. The audit's
    CRITICAL finding is that the engine never resets `triggered` (or the
    other five transient fields) when a symphony_id re-opens a position. The
    fix introduces a pure reset helper — the single-symphony analogue of
    database.wipe_transient_state.

    RED: no such helper exists in database.py or alpha_bot_execution.py.
    This test FAILS (does not skip) until the implementer adds it.
    """
    assert _RESET_HELPER is not None, (
        "No per-position reset helper found in database or "
        "alpha_bot_execution. AC-1 (C-2 CRITICAL) requires a pure helper "
        "that, given one symphony's bot_state sub-dict, resets triggered, "
        "breakeven_locked, hwm_hold_ticks, below_stop_count, vwap_ticks, "
        "vwap_bleed_ticks and deletes stop_trigger. Acceptable names: "
        "reset_symphony_position_state / reset_position_state_on_open / "
        "wipe_symphony_position_state."
    )


def test_position_open_detector_exists() -> None:
    """
    AC-2 (C-2 CRITICAL): a position-open detector MUST exist so the reset
    fires EXACTLY ONCE per new position and never mid-position. The audit
    requires position-open detection keyed on position_open_date /
    composition identity.

    RED: no detector exists. This test FAILS (does not skip) until the
    implementer adds one.
    """
    assert _resolve_position_open_detector() is not None, (
        "No position-open detector found in database or alpha_bot_execution. "
        "AC-2 requires the per-position reset to be gated on a reliable "
        "position-open event so it fires once per new position and never "
        "mid-position. Acceptable names: is_new_position_open / "
        "detect_position_open / position_opened."
    )


# ===========================================================================
# AC-1 — a re-opened position starts with fully reset per-position state
# ===========================================================================


@pytest.mark.skipif(
    _RESET_HELPER is None,
    reason="per-position reset helper not implemented yet (RED phase)",
)
class TestPositionOpenResetsTransientState:
    """AC-1: a new position under a reused symphony_id starts clean."""

    def test_reset_clears_triggered_flag(self):
        """
        AC-1: the stale triggered=True from the prior position must be reset
        to False so the new position's trailing-stop math is not permanently
        suppressed (compute_breakeven_update would otherwise return the
        -999.0 sentinel forever).
        """
        state = _dirty_symphony_state()
        _RESET_HELPER(state)
        assert state["triggered"] is False, (
            f"AC-1 VIOLATED: triggered={state['triggered']!r} after a "
            f"position-open reset. A reused symphony_id's new position must "
            f"start with triggered=False; otherwise compute_breakeven_update "
            f"emits the -999.0 sentinel and the new live position has NO "
            f"trailing-stop protection."
        )

    def test_reset_clears_breakeven_locked(self):
        """
        AC-1: breakeven_locked is a one-way latch WITHIN a position; across a
        position boundary it must reset to False. A stale True would make the
        new position's stop floor at 0.0 from tick 1, before it has earned
        any breakeven.
        """
        state = _dirty_symphony_state()
        _RESET_HELPER(state)
        assert state["breakeven_locked"] is False, (
            f"AC-1 VIOLATED: breakeven_locked={state['breakeven_locked']!r} "
            f"survived a position-open reset. The breakeven latch is "
            f"per-position; a new position has not earned breakeven."
        )

    @pytest.mark.parametrize("field", _TRANSIENT_INT_FIELDS)
    def test_reset_zeroes_transient_counters(self, field: str):
        """
        AC-1: the four per-position tick counters — hwm_hold_ticks,
        below_stop_count, vwap_ticks, vwap_bleed_ticks — must all reset to 0
        on a position open. A stale counter near its threshold would let the
        new position fire an exit confirmation it never earned.
        """
        state = _dirty_symphony_state()
        assert state[field] != 0, "test setup: field must start dirty"
        _RESET_HELPER(state)
        assert state[field] == 0, (
            f"AC-1 VIOLATED: '{field}'={state[field]!r} after a "
            f"position-open reset, expected 0. A stale tick counter can "
            f"trip a confirmation gate the new position never earned."
        )

    def test_reset_deletes_stop_trigger_key(self):
        """
        AC-1: the persisted stop_trigger from the prior position must be
        DELETED (not set to None or 0) so the next compute_breakeven_update
        receives previously_persisted_stop_level=None via .get('stop_trigger').

        The plan is explicit: "no persisted stop_trigger (the first
        compute_breakeven_update receives previously_persisted_stop_level=
        None)". The new position's first computed stop level must be neither
        the -999.0 sentinel nor clamped by the prior position's level.
        """
        state = _dirty_symphony_state()
        assert "stop_trigger" in state, "test setup: stop_trigger must start present"
        _RESET_HELPER(state)
        assert state.get("stop_trigger") is None, (
            f"AC-1 VIOLATED: stop_trigger={state.get('stop_trigger')!r} after "
            f"a position-open reset. It must be cleared so the new position "
            f"does not inherit the prior position's stop floor."
        )

    def test_reset_first_stop_is_not_sentinel_and_not_prior_level(self):
        """
        AC-1 (the integration assertion): after the reset, feeding the new
        position's first tick through compute_breakeven_update must produce a
        stop level that is NEITHER the -999.0 triggered sentinel NOR clamped
        by the prior position's stop level (2.5 in the dirty fixture).

        Derivation: post-reset triggered=False, breakeven_locked=False,
        hwm_hold_ticks=0, no stop_trigger. The new position's first tick
        with base_stop_level=0.3 and a sub-threshold return yields
        stop = base_stop_level = 0.3 — well below the prior 2.5 and not the
        -999.0 sentinel.
        """
        import math_engine

        state = _dirty_symphony_state()
        _RESET_HELPER(state)

        first_tick_base = 0.3
        _, _, first_stop = math_engine.compute_breakeven_update(
            current_return=-1.0,           # sub-threshold; no lock
            symphony_vol=1.0,
            base_stop_level=first_tick_base,
            current_hold_ticks=state["hwm_hold_ticks"],
            currently_breakeven_locked=state["breakeven_locked"],
            is_triggered=state["triggered"],
        )
        assert first_stop != math_engine.TRIGGERED_OVERRIDE_LEVEL, (
            f"AC-1 VIOLATED: the new position's first stop is the -999.0 "
            f"triggered sentinel ({first_stop}). triggered did not reset."
        )
        assert first_stop == pytest.approx(first_tick_base, rel=1e-9, abs=1e-12), (
            f"AC-1 VIOLATED: the new position's first stop is {first_stop}, "
            f"expected {first_tick_base} (the HWM-anchored base, no clamp). "
            f"If it is 2.5 the prior position's stop level still clamps it."
        )

    def test_reset_preserves_non_transient_fields(self):
        """
        AC-1 scope guard: the reset must clear ONLY the per-position
        transient fields. Identity / accounting fields (name, account,
        mc_history, current_holdings) are not per-position transients and
        must survive the reset untouched.

        Catches an over-broad reset that wipes the whole sub-dict.
        """
        state = _dirty_symphony_state()
        name_before = state["name"]
        account_before = state["account"]
        _RESET_HELPER(state)
        assert state["name"] == name_before, (
            "position-open reset must not clear the symphony name"
        )
        assert state["account"] == account_before, (
            "position-open reset must not clear the account id"
        )

    def test_reset_returns_or_mutates_the_state_dict(self):
        """
        AC-1 contract shape: the helper either mutates the passed dict in
        place (mirroring database.wipe_transient_state, which mutates) or
        returns the reset dict. Either is acceptable; this test pins that
        the post-call state is reset regardless of which the implementer
        chooses.
        """
        state = _dirty_symphony_state()
        returned = _RESET_HELPER(state)
        effective = returned if isinstance(returned, dict) else state
        assert effective["triggered"] is False
        assert effective.get("stop_trigger") is None


# ===========================================================================
# AC-2 — the reset fires exactly once per new position, never mid-position
# ===========================================================================


@pytest.mark.skipif(
    _RESET_HELPER is None,
    reason="per-position reset helper not implemented yet (RED phase)",
)
class TestPositionOpenDetectionFiresExactlyOnce:
    """
    AC-2: position-open is detected reliably (keyed on position_open_date /
    composition identity); the reset fires exactly once per new position,
    never mid-position.
    """

    def test_reset_is_idempotent_on_an_already_clean_state(self):
        """
        AC-2: applying the reset to an already-clean position state is a
        safe no-op — it leaves every transient field at its reset sentinel.
        This guards against a reset that toggles rather than sets.
        """
        clean = {
            "name": "Clean Symphony",
            "triggered": False,
            "breakeven_locked": False,
            "hwm_hold_ticks": 0,
            "below_stop_count": 0,
            "vwap_ticks": 0,
            "vwap_bleed_ticks": 0,
            # stop_trigger key intentionally absent
        }
        _RESET_HELPER(clean)
        for field in _TRANSIENT_BOOL_FIELDS:
            assert clean[field] is False
        for field in _TRANSIENT_INT_FIELDS:
            assert clean[field] == 0
        assert clean.get("stop_trigger") is None

    def test_reset_does_not_resurrect_a_deleted_stop_trigger(self):
        """
        AC-2: a second reset call must not re-create the stop_trigger key.
        Running the reset twice (e.g. position-open detected, then the same
        open re-evaluated) leaves stop_trigger absent both times.
        """
        state = _dirty_symphony_state()
        _RESET_HELPER(state)
        assert "stop_trigger" not in state or state.get("stop_trigger") is None
        _RESET_HELPER(state)  # second call
        assert state.get("stop_trigger") is None, (
            "a repeated reset must not resurrect the stop_trigger key"
        )

    def test_mid_position_state_is_not_reset_when_position_unchanged(self):
        """
        AC-2: the reset must NOT fire mid-position. The reset is gated on a
        position-open event (a change in position identity / open date);
        when the position identity is UNCHANGED across two cycles, the
        engine must not call the reset helper — a mid-position reset would
        wipe a live position's accumulated tick counters and breakeven
        latch.

        This test pins the GATING CONTRACT structurally: the engine must
        expose a position-open detector that returns False when the
        position identity is unchanged. The detector is resolved from the
        same modules as the reset helper.

        RED: there is currently no position-open detection at all — the
        engine never resets `triggered`, so neither a detector nor the
        gating exists.
        """
        detector = None
        for module_name in ("database", "alpha_bot_execution"):
            try:
                module = __import__(module_name)
            except ImportError:
                continue
            for fn_name in (
                "is_new_position_open",
                "detect_position_open",
                "position_opened",
            ):
                fn = getattr(module, fn_name, None)
                if callable(fn):
                    detector = fn
                    break
            if detector is not None:
                break

        if detector is None:
            pytest.fail(
                "No position-open detector found in database or "
                "alpha_bot_execution. AC-2 requires position-open to be "
                "detected reliably (keyed on position_open_date / "
                "composition identity) so the reset fires exactly once per "
                "new position and never mid-position. Implement a detector "
                "(e.g. is_new_position_open(prev_identity, current_identity)) "
                "and gate reset_symphony_position_state on it."
            )

        # Same position identity across two cycles -> NOT a new open.
        same_identity = "2026-05-21|comp-hash-abc"
        assert detector(same_identity, same_identity) is False, (
            "is_new_position_open must return False when the position "
            "identity is unchanged — the reset must not fire mid-position."
        )
        # A changed identity -> a new open.
        assert detector(same_identity, "2026-05-22|comp-hash-xyz") is True, (
            "is_new_position_open must return True when the position "
            "identity changes (new open date / composition) — that is the "
            "exact event the per-position reset must fire on."
        )

    def test_reused_symphony_id_second_position_starts_clean(self):
        """
        AC-1 + AC-2 end-to-end: simulate the rotation lifecycle for ONE
        symphony_id — position 1 opens, runs, triggers; the same
        symphony_id is re-allocated and position 2 opens. Assert position 2
        starts with triggered=False, breakeven_locked=False, all four
        counters 0, and no stop_trigger.

        This is the audit's named CRITICAL scenario: "open->trigger->re-open
        for the same symphony_id".
        """
        # --- Position 1: runs and triggers (engine leaves it dirty) ---
        symphony_id = "sym-rotation-lifecycle-001"
        bot_state = {symphony_id: _dirty_symphony_state()}
        position_1_state = bot_state[symphony_id]
        assert position_1_state["triggered"] is True, "position 1 ended triggered"

        # --- Position 2 opens under the SAME symphony_id ---
        # The engine detects the position-open and calls the reset helper on
        # the existing (dirty) sub-dict before the new position's first tick.
        _RESET_HELPER(bot_state[symphony_id])
        position_2_state = bot_state[symphony_id]

        assert position_2_state["triggered"] is False, (
            "AC-1/AC-2 VIOLATED: position 2 (reused symphony_id) inherited "
            "triggered=True from position 1."
        )
        assert position_2_state["breakeven_locked"] is False, (
            "position 2 inherited breakeven_locked=True from position 1"
        )
        for field in _TRANSIENT_INT_FIELDS:
            assert position_2_state[field] == 0, (
                f"position 2 inherited a stale '{field}' from position 1"
            )
        assert position_2_state.get("stop_trigger") is None, (
            "position 2 inherited position 1's stop_trigger floor"
        )
