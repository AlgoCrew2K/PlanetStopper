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

This file ALSO verifies the C-2 resolution at the live-engine level: the
composition-hash per-symphony position-open detection that an earlier GREEN
attempt added has been REMOVED (it keyed on a hash of live holdings, which
is a rebalance detector — not a position identity — and wiped live
exit-guard state on a normal Composer rebalance; see the risk-engine-
specialist domain ruling and quant-code-reviewer's BLOCK-1). C-2 is closed
by the pre-existing database.wipe_transient_state. This file pins that the
removed detection does not creep back into the live engine. The full C-2
coverage lives in tests/execution/test_symphony_position_open_reset.py.

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
            if (isinstance(func, ast.Attribute) and func.attr == "compute_breakeven_update") or (
                isinstance(func, ast.Name) and func.id == "compute_breakeven_update"
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
    base_kwarg_present = any("base_stop_level" in {kw.arg for kw in c.keywords} for c in calls)
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
# C-2 resolution — the composition-hash position-open detection is REMOVED.
#
# An earlier GREEN attempt added a per-symphony position-open detection
# (is_new_position_open + a position_identity keyed on a hash of the
# symphony's live holding tickers). quant-code-reviewer's BLOCK-1 and the
# risk-engine-specialist's domain ruling established that a live-holdings
# composition hash is a REBALANCE detector, not a position identity: a
# Composer symphony rebalances its constituents within a position by design,
# so the hash flips mid-position and the reset wiped live exit-guard state.
#
# The detection was removed entirely. C-2 is closed by the pre-existing
# database.wipe_transient_state (the engine-path reset, run at the new-day
# boundary and on an exec-mode toggle). These tests pin that the removed
# detection does not creep back into the live engine.
# ===========================================================================


def _live_source() -> str:
    return (_PROJECT_ROOT / "alpha_bot_execution.py").read_text(encoding="utf-8")


def test_live_engine_has_no_composition_hash_position_detection() -> None:
    """
    C-2 resolution guard: alpha_bot_execution.py must NOT contain the removed
    composition-hash position-open detection. The forbidden references are
    `is_new_position_open` (the removed detector) and `position_identity`
    (the removed identity key).

    A live-holdings composition hash flips on a normal Composer rebalance and
    a per-position reset keyed on it wipes a live in-flight position's
    exit-guard state (BLOCK-1). The mechanism was removed; C-2 is closed by
    wipe_transient_state.

    RED while the composition-hash detection exists; GREEN once removed.
    """
    tree = ast.parse(_live_source())
    forbidden = {"is_new_position_open", "position_identity"}
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden:
            offenders.append(f"attribute '{node.attr}' at line {node.lineno}")
        if isinstance(node, ast.Name) and node.id in forbidden:
            offenders.append(f"name '{node.id}' at line {node.lineno}")
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in forbidden
        ):
            offenders.append(f"string '{node.value}' at line {node.lineno}")
    assert not offenders, (
        "The composition-hash position-open detection has crept back into "
        f"alpha_bot_execution.py: {offenders}. It must stay removed — a "
        "live-holdings hash is a rebalance detector, not a position "
        "identity. C-2 is closed by wipe_transient_state."
    )


def test_live_engine_does_not_invoke_a_per_position_reset_helper() -> None:
    """
    C-2 resolution guard: the per-symphony reset helper
    (reset_symphony_position_state) had exactly one caller — the removed
    composition-hash detection block. With the detection gone there is no
    safe intraday position-open boundary to call it from, so the engine must
    not invoke it. C-2 is closed by wipe_transient_state, which the engine
    already calls at the new-day boundary.

    RED while the engine still calls a per-position reset helper; GREEN once
    the call is removed.
    """
    source = _live_source()
    forbidden_calls = (
        "reset_symphony_position_state",
        "reset_position_state_on_open",
        "wipe_symphony_position_state",
    )
    present = [name for name in forbidden_calls if name in source]
    assert not present, (
        f"alpha_bot_execution.py still references a per-position reset helper "
        f"{present}. Its only caller — the composition-hash position-open "
        "detection — was removed (BLOCK-1). The engine must rely on "
        "wipe_transient_state for the C-2 reset; an intraday per-position "
        "reset has no safe position-open boundary to fire on."
    )


def test_live_engine_still_calls_wipe_transient_state() -> None:
    """
    C-2 positive guard: with the composition-hash detection removed, C-2 is
    closed entirely by wipe_transient_state. The live engine MUST still call
    it — removing the detection must not also remove the genuine reset path.

    Structural check: alpha_bot_execution.py invokes wipe_transient_state
    (it does so at the new-day boundary and on an exec-mode toggle).
    """
    source = _live_source()
    assert "wipe_transient_state" in source, (
        "alpha_bot_execution.py no longer calls wipe_transient_state — that "
        "is the engine-path reset that closes the audit's C-2 CRITICAL. "
        "Removing the composition-hash detection must NOT remove the genuine "
        "reset path."
    )
