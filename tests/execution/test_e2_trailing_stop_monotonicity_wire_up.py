"""
Tests for the live-consumer wire-up of the HWM-anchored trailing stop.

REWRITTEN for the H-1 audit fix (math-audit exit-decision-math__2026-05-21.md).

WHAT CHANGED — and why this file was rewritten:

  The PRE-FIX version of this file verified that the live consumer path
  (alpha_bot_execution.py) threads previously_persisted_stop_level= from
  bot_state[symphony_id]['stop_trigger'] into compute_breakeven_update, so
  the resolved stop level would ratchet upward and never decrease.

  The math audit (HIGH finding H-1) showed that frozen-level ratchet is the
  DEFECT — it discards a legitimate widening of the vol-scaled distance on a
  mid-day volatility spike and exits on noise. The H-1 fix REMOVES the
  previously_persisted_stop_level parameter. The stop is HWM-anchored:
  alpha_bot_execution.py already computes
      base_stop_level = safe_hwm - active_trailing_stop
  fresh every tick, and compute_breakeven_update now simply resolves that
  base (with the breakeven floor when locked, the -999.0 sentinel when
  triggered) — no cross-tick clamp.

  So the contract this file verifies FLIPS:
    - PRE-FIX: the live path MUST pass previously_persisted_stop_level=.
    - POST-FIX: the live path must NOT pass it (the parameter is gone).
  The detailed stop-math behavior is covered by
  tests/math_engine/test_hwm_anchored_ratchet.py; this file is the live
  consumer-side structural wire-up guard.

This file ALSO verifies the C-2 position-open reset wire-up (AC-1/AC-2): the
live engine must invoke the per-position reset helper, gate it on a
position-open detector, and stamp position_open_date. The helper's and
detector's own logic is unit-tested in
tests/execution/test_symphony_position_open_reset.py; this file pins that
the engine actually WIRES them — a helper that exists but is never called
leaves the C-2 CRITICAL defect live.

The four pre-fix stop_sequence fixtures
(tests/fixtures/math_engine/stop_sequence/*.json) encoded the frozen-ratchet
expectation and are retired alongside this rewrite.
"""

from __future__ import annotations

import ast
import pathlib

import inspect

import alpha_bot_execution
import math_engine


_PROJECT_ROOT = pathlib.Path(alpha_bot_execution.__file__).parent


def _compute_breakeven_update_calls(filename: str) -> list[ast.Call]:
    path = _PROJECT_ROOT / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "compute_breakeven_update"
            ) or (
                isinstance(func, ast.Name)
                and func.id == "compute_breakeven_update"
            ):
                calls.append(node)
    return calls


# ===========================================================================
# Live consumer no longer threads the removed kwarg.
# ===========================================================================


def test_live_consumer_does_not_pass_previously_persisted_stop_level() -> None:
    """
    H-1: the live consumer path (alpha_bot_execution.py) must call
    compute_breakeven_update WITHOUT previously_persisted_stop_level. The
    parameter is removed by the H-1 fix; passing it raises TypeError on the
    live execution path.

    RED: alpha_bot_execution.py:1219 currently passes
    previously_persisted_stop_level=bot_state[symphony_id].get('stop_trigger').
    """
    calls = _compute_breakeven_update_calls("alpha_bot_execution.py")
    assert calls, (
        "No compute_breakeven_update call found in alpha_bot_execution.py — "
        "the live consumer must still call the function."
    )
    offenders = [
        f"line {c.lineno}"
        for c in calls
        if "previously_persisted_stop_level" in {kw.arg for kw in c.keywords}
    ]
    assert not offenders, (
        f"alpha_bot_execution.py still threads the removed "
        f"previously_persisted_stop_level kwarg at {offenders}. The H-1 fix "
        f"removes the parameter — the live consumer must drop the kwarg "
        f"(and stop reading bot_state[symphony_id]['stop_trigger'] purely "
        f"to feed it)."
    )


def test_live_consumer_still_computes_hwm_anchored_base_stop_level() -> None:
    """
    H-1: the live consumer must STILL recompute the HWM-anchored base each
    tick — base_stop_level = safe_hwm - active_trailing_stop — and pass it
    as base_stop_level=. The H-1 fix removes the clamp, NOT the
    HWM-anchoring; the anchoring is what makes the trailing stop function.

    Structural check: the live source contains the
    `safe_hwm - active_trailing_stop` computation and passes base_stop_level=
    to compute_breakeven_update.
    """
    source = (_PROJECT_ROOT / "alpha_bot_execution.py").read_text(encoding="utf-8")
    assert "safe_hwm - active_trailing_stop" in source, (
        "alpha_bot_execution.py no longer computes "
        "base_stop_level = safe_hwm - active_trailing_stop. The HWM-anchored "
        "stop REQUIRES this per-tick computation — the H-1 fix removes the "
        "clamp, not the anchoring."
    )
    calls = _compute_breakeven_update_calls("alpha_bot_execution.py")
    base_kwarg_present = any(
        "base_stop_level" in {kw.arg for kw in c.keywords} for c in calls
    )
    assert base_kwarg_present, (
        "compute_breakeven_update in the live path must still receive "
        "base_stop_level= (the HWM-anchored base recomputed each tick)."
    )


def test_compute_breakeven_update_signature_is_six_params() -> None:
    """
    H-1: after the fix compute_breakeven_update accepts exactly the six
    HWM-anchored parameters — current_return, symphony_vol, base_stop_level,
    current_hold_ticks, currently_breakeven_locked, is_triggered — and NOT
    previously_persisted_stop_level.

    This is the live consumer's contract: it must call the 6-parameter form.
    """
    sig = inspect.signature(math_engine.compute_breakeven_update)
    params = set(sig.parameters)
    assert "previously_persisted_stop_level" not in params, (
        "compute_breakeven_update still declares "
        "previously_persisted_stop_level. The H-1 fix removes it."
    )
    expected = {
        "current_return",
        "symphony_vol",
        "base_stop_level",
        "current_hold_ticks",
        "currently_breakeven_locked",
        "is_triggered",
    }
    assert params == expected, (
        f"compute_breakeven_update parameters {sorted(params)} != the "
        f"expected HWM-anchored six {sorted(expected)}."
    )


# ===========================================================================
# C-2 wire-up — the live engine must invoke the per-position reset on a
# position-open boundary (team-lead steer: this file keeps the live-consumer
# WIRE-UP verification; under the H-1 contract that wire-up now includes the
# C-2 position-open reset).
#
# The per-position reset HELPER and DETECTOR contracts are unit-tested in
# tests/execution/test_symphony_position_open_reset.py. This section pins the
# orthogonal concern: the helper is actually CALLED from the live engine.
# ===========================================================================


def _live_source() -> str:
    return (_PROJECT_ROOT / "alpha_bot_execution.py").read_text(encoding="utf-8")


def test_live_engine_invokes_the_per_position_reset_helper() -> None:
    """
    C-2 (AC-1/AC-2) wire-up: the per-position reset must not just exist as a
    pure helper — the live engine (alpha_bot_execution.py per-symphony loop)
    must actually CALL it. The audit's CRITICAL finding is that the engine
    never resets `triggered`; a helper that exists but is never invoked
    leaves the defect live.

    Structural check: alpha_bot_execution.py references one of the accepted
    reset-helper names. The helper itself is unit-tested in
    test_symphony_position_open_reset.py; this pins that the engine wires it.

    RED: no per-position reset helper is called anywhere in
    alpha_bot_execution.py today.
    """
    source = _live_source()
    accepted = (
        "reset_symphony_position_state",
        "reset_position_state_on_open",
        "wipe_symphony_position_state",
    )
    assert any(name in source for name in accepted), (
        "alpha_bot_execution.py does not invoke any per-position reset "
        "helper. AC-1/AC-2 (C-2 CRITICAL) require the engine to call the "
        "reset on a position-open boundary so a reused symphony_id's new "
        "position starts clean. A helper that exists but is never called "
        "does not fix the defect. Accepted names: "
        f"{', '.join(accepted)}."
    )


def test_live_engine_invokes_the_position_open_detector() -> None:
    """
    C-2 (AC-2) wire-up: the engine must gate the per-position reset on a
    position-open detector so the reset fires exactly once per new position,
    never mid-position. This pins that the engine references the detector —
    the detector's own logic is unit-tested in
    test_symphony_position_open_reset.py.

    RED: alpha_bot_execution.py has no position-open detection today.
    """
    source = _live_source()
    accepted = (
        "is_new_position_open",
        "detect_position_open",
        "position_opened",
    )
    assert any(name in source for name in accepted), (
        "alpha_bot_execution.py does not invoke a position-open detector. "
        "AC-2 requires the per-position reset to be gated on a reliable "
        "position-open event (keyed on position_open_date / composition "
        "identity) so it fires once per new position and never "
        f"mid-position. Accepted names: {', '.join(accepted)}."
    )


def test_live_engine_stamps_position_open_date() -> None:
    """
    C-2 (AC-2) wire-up: position-open detection is keyed on
    position_open_date / composition identity. position_open_date is
    currently only READ (alpha_bot_execution.py:~1357,
    bot_state[...].get('position_open_date', '2000-01-01')) and never
    WRITTEN — so the detector would have no real signal.

    The engine must WRITE position_open_date into bot_state on a position
    open. Structural check: an assignment to a 'position_open_date' key
    exists in alpha_bot_execution.py.

    RED: position_open_date is read-only in the current source.
    """
    source = _live_source()
    tree = ast.parse(source)
    writes_position_open_date = False
    for node in ast.walk(tree):
        # Match bot_state[...]["position_open_date"] = ...  (Subscript target)
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (
                    isinstance(tgt, ast.Subscript)
                    and isinstance(tgt.slice, ast.Constant)
                    and tgt.slice.value == "position_open_date"
                ):
                    writes_position_open_date = True
    # Also accept a dict-literal key (e.g. the bot_state init dict) — a
    # Constant 'position_open_date' used as a dict key in an ast.Dict.
    if not writes_position_open_date:
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "position_open_date"
                    ):
                        writes_position_open_date = True
    assert writes_position_open_date, (
        "alpha_bot_execution.py never WRITES position_open_date into "
        "bot_state — it is only read with a '2000-01-01' default. AC-2's "
        "position-open detection is keyed on position_open_date; the engine "
        "must stamp it on a position open or the detector has no signal."
    )
