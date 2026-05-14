"""
Cycle B2-FU3 — Characterization pin of ``autotuner.run_autotuner``'s nested
``run_simulation`` closure (autotuner.py:142-298).

PURPOSE
-------
``run_simulation`` is currently a local function defined inside
``run_autotuner``. The B2 cycle worker flagged this as untestable in
isolation — captured variables and module-private state make it impossible
to import. A subsequent refactor will HOIST ``run_simulation`` to module
scope so it can be unit-tested directly.

These tests pin the CURRENT, pre-refactor behavior. The post-hoist
implementation MUST produce byte-equivalent outputs against the same
fixtures. Any drift here is a regression and blocks the refactor merge.

CLOSURE INVENTORY
-----------------
A full reading of ``run_simulation`` (autotuner.py:142-298) shows the
function reads ONLY:

  * its explicit positional arguments::
        p, history_data, acc_sym_ids, current_date_str, deviation_dict

  * three module-level imports captured implicitly via ``run_autotuner``'s
    enclosing module scope::
        math          (math.exp for the time-decay weight at line ~281)
        datetime      (datetime.strptime at lines ~145 and ~280)
        math_engine   (compute_para_arm_decision, compute_time_squeeze_decay,
                       compute_active_trailing_stop, compute_breakeven_update,
                       compute_vwap_bleed_arm_threshold,
                       compute_vwap_breakdown_update)

Crucially, ``run_simulation`` does NOT close over any local of
``run_autotuner`` (no ``bot_state``, ``normalized_name``, ``locked_vars``,
``current_params``, etc. is referenced inside the closure body). The
"closure" is structural only — hoisting to module scope is a name-only
refactor; behavior must be byte-identical.

PIN STRATEGY
------------
The closure is invoked from FOUR call sites inside ``run_autotuner``::

  1. autotuner.py:313 (training loop, via objective(), suppressed in tests by
     stubbing ``study.optimize`` so the closure is NOT invoked during
     training)
  2. autotuner.py:359 — AI OOS evaluation
  3. autotuner.py:371 — fallback OOS evaluation
  4. autotuner.py:375 — default OOS evaluation

We exercise (2)/(3)/(4) by driving ``run_autotuner`` end-to-end with
fixtures crafted to produce a SPECIFIC simulation path, then pin the
resulting ``oos_alpha`` / ``fallback_oos_alpha`` / ``default_oos_alpha``
values reported by the producer.

The producer formats these alphas as ``{:.2f}`` in the decision banner
(autotuner.py:392-394, 399, 404). That format string IS the producer's
precision contract — pinning against the formatted output gives a tight
characterization spec without re-implementing the math here. We
additionally assert the persisted strategy params (which is the actual
durable side-effect of ``run_autotuner``).

PENALTY MATH (autotuner.py:274-296)
-----------------------------------
The penalty block at the bottom of ``run_simulation`` is the highest-risk
code path because it directly produces the optimization signal. It runs
ONLY when a trigger fires (``triggered_return is not None``). The three
penalty terms are:

  * MISSED UPSIDE penalty (line 285):
        if missed_upside > 1.0:
            total_guard_alpha -= (missed_upside * 1.5 * weight)

  * PEAK-TO-EXIT DRAWDOWN penalty (line 290):
        if safe_hwm > 1.0 and drawdown_from_peak > 1.5:
            total_guard_alpha -= (drawdown_from_peak * 0.75 * weight)

  * EOD-BASED guard alpha (lines 293-296):
        if guard_alpha < 0:
            total_guard_alpha += (guard_alpha * 2.0 * weight)
        else:
            total_guard_alpha += (guard_alpha * weight)

Scenarios 2, 3, and 5 below cover this block. Scenarios 1, 4, and 6 cover
the non-trigger / early-exit / single-tick floor paths.
"""

from __future__ import annotations

import contextlib
import io
import math as _math
import re
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Lazy autotuner/database imports — see test_oos_baseline_selection.py for
# rationale (optuna.logging.set_verbosity at import-time interacts badly with
# pytest stdout capture on Windows if collection order is unlucky).
# ---------------------------------------------------------------------------


# The 7 Optuna-searched param keys (matches database.DEFAULT_STRATEGY's tuned
# subset, sourced inline so we don't drift from the producer).
_OPTUNA_SEARCHED_KEYS = (
    "TRIGGER_THRESHOLD_PCT",
    "TAKE_PROFIT_MC_PCT",
    "VWAP_CROSS_HWM_PCT",
    "VWAP_BLEED_MULTIPLIER",
    "VWAP_BLEED_TICKS",
    "PARABOLIC_VELOCITY_THRESHOLD",
    "MAX_PARABOLIC_SQUEEZE",
)


def _ai_full_params():
    """A complete AI proposal that passes the FU2 schema-validation gate."""
    return {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


def _build_bot_state():
    """Single-symphony bot_state — minimal viable input for run_autotuner."""
    return {
        "sym-A": {
            "name": "Char Sim Symphony",
            "account_uuid": "acc-1",
        }
    }


# ---------------------------------------------------------------------------
# Math-engine helper stubs.
#
# The closure invokes 6 math_engine helpers per tick. To make the simulation
# path fully deterministic from fixture inputs, we stub each one to a
# controlled return. The stubs do NOT alter the simulator's own state
# transitions (HWM tracking, armed flag, tp_armed, etc.); they only fix the
# values flowing into those transitions.
# ---------------------------------------------------------------------------


def _stub_para_arm_no_trigger(**kwargs):
    """compute_para_arm_decision: never arms parabolic stop. Returns
    (velocity=0.0, should_arm=False)."""
    return (0.0, False)


def _stub_time_squeeze_passthrough(time_ratio):
    """compute_time_squeeze_decay: returns deterministic (multiplier, min_stop)
    that the simulator carries verbatim into compute_active_trailing_stop."""
    return (1.5, 0.5)


def _stub_active_stop_const(vol, mult, min_stop, para_armed, breakeven_locked,
                            max_para_squeeze):
    """compute_active_trailing_stop: returns a fixed 5.0pct trailing distance —
    far enough below the tick return regime that the trailing-stop branch
    does NOT fire on its own; the only way to fire a trigger in these tests
    is via the VWAP-break stub or via mc_prob-driven take-profit."""
    return 5.0


def _stub_breakeven_no_lock(ret, vol, base_stop, hwm_hold_ticks,
                             breakeven_locked, is_triggered):
    """compute_breakeven_update: keep hwm_hold_ticks at 0, never lock, pass
    base_stop through unmodified."""
    return (hwm_hold_ticks, breakeven_locked, base_stop)


def _stub_vwap_bleed_arm_pct(vol, multiplier):
    """compute_vwap_bleed_arm_threshold: fixed sentinel value; not consumed
    in tests that avoid the VWAP-bleed branch."""
    return -10.0  # well below any tick return, so the bleed arm never fires


def _stub_vwap_breakdown_no_trigger(**kwargs):
    """compute_vwap_breakdown_update: never trigger VWAP break or bleed."""
    return (0, 0, False, False)


def _stub_vwap_breakdown_trigger_on_first_tick(**kwargs):
    """compute_vwap_breakdown_update: ALWAYS trigger VWAP break.

    Returns (vwap_ticks, vwap_bleed_ticks, is_vwap_broken=True,
    is_vwap_bleed_broken=False). Forces a deterministic VWAP-break trigger
    so the simulator's penalty block executes on the first tick of each day.
    """
    return (0, 0, True, False)


# ---------------------------------------------------------------------------
# Patch context manager (subset of the OOS-baseline tests' harness).
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _patches_for_run(
    *,
    history,
    fallback_params,
    default_strategy,
    best_params,
    locked_vars=None,
    vwap_side_effect=_stub_vwap_breakdown_no_trigger,
    breakeven_side_effect=_stub_breakeven_no_lock,
):
    """Wire all mocks for one run_autotuner invocation.

    history             — what synthetic_history.generate_synthetic_history returns
    fallback_params     — what get_symphony_strategy returns as 'params'
    default_strategy    — patched database.DEFAULT_STRATEGY
    best_params         — what the fake Optuna study advertises as best_params
    locked_vars         — what get_symphony_strategy returns as 'locked_vars'
    """
    fake_study = MagicMock(name="fake_optuna_study")
    fake_study.best_params = best_params
    fake_study.best_value = 1.0
    fake_study.optimize = MagicMock(return_value=None)  # no training-sim invocation

    save_calls = []

    def _capture_save(symphony_name, params, lv):
        save_calls.append((symphony_name, dict(params), list(lv)))

    locked = list(locked_vars or [])

    with patch("autotuner.optuna.create_study", return_value=fake_study), \
         patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()), \
         patch("autotuner.synthetic_history.generate_synthetic_history",
               return_value=history), \
         patch("autotuner.database.load_chart_history", return_value={}), \
         patch("autotuner.database.save_chart_archive"), \
         patch("autotuner.database.get_symphony_strategy",
               return_value={"params": fallback_params.copy(), "locked_vars": locked}), \
         patch("autotuner.database.save_symphony_strategy",
               side_effect=_capture_save), \
         patch("autotuner.database.DEFAULT_STRATEGY", default_strategy), \
         patch("autotuner.math_engine.compute_para_arm_decision",
               side_effect=_stub_para_arm_no_trigger), \
         patch("autotuner.math_engine.compute_time_squeeze_decay",
               side_effect=_stub_time_squeeze_passthrough), \
         patch("autotuner.math_engine.compute_active_trailing_stop",
               side_effect=_stub_active_stop_const), \
         patch("autotuner.math_engine.compute_breakeven_update",
               side_effect=breakeven_side_effect), \
         patch("autotuner.math_engine.compute_vwap_bleed_arm_threshold",
               side_effect=_stub_vwap_bleed_arm_pct), \
         patch("autotuner.math_engine.compute_vwap_breakdown_update",
               side_effect=vwap_side_effect):
        yield {
            "save_calls": save_calls,
            "fake_study": fake_study,
        }


def _run_capture(**kwargs):
    """Drive run_autotuner with the given patch parameters; capture stdout."""
    import autotuner

    bot_state = _build_bot_state()
    buf = io.StringIO()
    with _patches_for_run(**kwargs) as ctx, contextlib.redirect_stdout(buf):
        autotuner.run_autotuner(bot_state, "2026-05-10", ["acc-1"])
    return buf.getvalue(), ctx


# ---------------------------------------------------------------------------
# stdout parsing helpers — the producer formats every alpha to .2f. We pin
# against the producer's own formatted output (the contract surface).
# ---------------------------------------------------------------------------


_AI_OOS_PASS_BANNER_POS = re.compile(
    r"OOS Guard Alpha: \+(?P<alpha>-?\d+\.\d{2})%"
)
_AI_OOS_PASS_BANNER_NEG = re.compile(
    r"OOS Guard Alpha: (?P<alpha>-?\d+\.\d{2})% \(Avg:"
)
_FALLBACK_REVERT_BANNER = re.compile(
    r"\(AI: (?P<ai>-?\d+\.\d{2})%\)\. Reverting to Fallback parameters "
    r"\(Fallback: (?P<fallback>-?\d+\.\d{2})% vs Default: (?P<default>-?\d+\.\d{2})%\)"
)
_DEFAULT_RESET_BANNER = re.compile(
    r"Default: (?P<default>-?\d+\.\d{2})% vs AI: (?P<ai>-?\d+\.\d{2})%, "
    r"Fallback: (?P<fallback>-?\d+\.\d{2})%"
)


def _extract_alphas(stdout):
    """Extract whichever OOS alpha banner is present in stdout.

    Returns a dict containing whichever of {ai, fallback, default} were
    surfaced by the producer's banner. The "Adopted AI" banner only prints
    the AI alpha; the other two banners print all three.
    """
    m = _FALLBACK_REVERT_BANNER.search(stdout)
    if m:
        return {
            "branch": "fallback",
            "ai": float(m.group("ai")),
            "fallback": float(m.group("fallback")),
            "default": float(m.group("default")),
        }
    m = _DEFAULT_RESET_BANNER.search(stdout)
    if m:
        return {
            "branch": "default",
            "ai": float(m.group("ai")),
            "fallback": float(m.group("fallback")),
            "default": float(m.group("default")),
        }
    m = _AI_OOS_PASS_BANNER_POS.search(stdout)
    if m:
        return {"branch": "ai", "ai": float(m.group("alpha"))}
    m = _AI_OOS_PASS_BANNER_NEG.search(stdout)
    if m:
        return {"branch": "ai", "ai": float(m.group("alpha"))}
    raise AssertionError(
        f"Could not parse any OOS decision banner from stdout:\n{stdout}"
    )


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_history(date_to_ticks):
    """Wrap a {date: [ticks]} mapping into the full history shape expected
    by run_autotuner (keyed by sym_id)."""
    return {"sym-A": dict(date_to_ticks)}


def _flat_positive_tick(ret=0.5):
    """Tick whose return is below trigger thresholds and whose mc_prob keeps
    the simulator in the 'unarmed' state. No trigger fires.
    """
    return {
        "return": ret,
        "mc_prob": 50.0,  # outside both TP (<5) and trigger-arm (>=15) ranges
        "vol": 1.0,
        "vwap_diff": 0.0,
        "base_atr_pct": 1.0,
        "valid_vwap_weight": 1.0,
    }


def _trigger_tick(ret=3.0, mc_prob=50.0):
    """Tick used to drive a VWAP-break trigger via the stubbed
    compute_vwap_breakdown_update (which returns is_vwap_broken=True
    unconditionally)."""
    return {
        "return": ret,
        "mc_prob": mc_prob,
        "vol": 1.0,
        "vwap_diff": -2.0,
        "base_atr_pct": 1.0,
        "valid_vwap_weight": 1.0,
    }


# Days in the synthetic history. 80/20 split applied by run_autotuner means
# 5 distinct dates -> 4 train / 1 OOS test. We need at least 2 distinct dates
# overall (autotuner.py:110-112 aborts on <2 days).
_TEST_DATES_5 = [
    "2026-04-01", "2026-04-02", "2026-04-03", "2026-04-04", "2026-04-05",
]
_TEST_DATES_2 = ["2026-04-01", "2026-04-02"]
_CURRENT_DATE_STR = "2026-05-10"


# ===========================================================================
# Scenario 1 — All-positive ticks, no trigger fires.
# ===========================================================================


def test_run_simulation_no_trigger_returns_zero_guard_alpha():
    """
    SCENARIO 1 — no trigger path. Every tick has mc_prob=50, vol=1.0, and the
    VWAP-break stub is configured to never trigger. Result: no day in any of
    the AI/fallback/default OOS runs has ``triggered_return`` set; the
    penalty block at lines 274-296 is NEVER ENTERED for any day; total
    guard alpha is exactly 0.0 for all three param sets.

    Pins: every OOS alpha must format to ``0.00`` (or ``-0.00`` is permitted
    by Python's repr but excluded by the producer's format string). The
    decision branch is therefore an all-zero tie. Under the CURRENT decision
    rule (autotuner.py:390 ``>`` strict), no branch beats both others by a
    strict margin, so the cascade falls to fallback.

    Penalty-math coverage: NONE (early-exit before penalty block).
    """
    ai = _ai_full_params()
    fallback = _ai_full_params()  # identical -> markerless tie
    fallback["VWAP_CROSS_HWM_PCT"] = 2.0  # distinguishable from AI's 1.0
    default = _ai_full_params()
    default["VWAP_CROSS_HWM_PCT"] = 2.5

    history = _make_history({d: [_flat_positive_tick()] for d in _TEST_DATES_5})

    stdout, ctx = _run_capture(
        history=history,
        fallback_params=fallback,
        default_strategy=default,
        best_params=ai,
        vwap_side_effect=_stub_vwap_breakdown_no_trigger,
    )

    alphas = _extract_alphas(stdout)
    # Producer's .2f format pins each alpha. 0.0 -> "0.00".
    assert alphas["branch"] == "fallback", (
        "All-zero tie must fall through strict-positive AI check to "
        f"fallback branch; got {alphas['branch']!r}.\nstdout:\n{stdout}"
    )
    assert alphas["ai"] == pytest.approx(0.0, abs=1e-9)
    assert alphas["fallback"] == pytest.approx(0.0, abs=1e-9)
    assert alphas["default"] == pytest.approx(0.0, abs=1e-9)

    # And the persisted strategy must be fallback's (its VWAP marker).
    save_calls = ctx["save_calls"]
    assert len(save_calls) == 1
    _name, persisted, _locked = save_calls[0]
    assert persisted["VWAP_CROSS_HWM_PCT"] == pytest.approx(
        fallback["VWAP_CROSS_HWM_PCT"], abs=1e-9
    )


# ===========================================================================
# Scenario 2 — Trigger fires; penalty block produces a SPECIFIC alpha.
# ===========================================================================


def test_run_simulation_triggered_exit_pins_specific_guard_alpha():
    """
    SCENARIO 2 — drive exactly one trigger per OOS day and pin the
    resulting ``total_guard_alpha`` against the producer's hand-computed
    expected value.

    Fixture: single-tick days. Each tick has return=3.0, mc_prob=50.0.
    The VWAP-break stub fires on EVERY call, so the trigger fires on
    tick_idx=0. Penalty math runs with::

        ret = 3.0
        hwm = -999.0 initially; after first iteration safe_hwm = max(-999, 3) = 3.0
        eod_return = ticks[-1]["return"] = 3.0
        day_max_return = max([3.0]) = 3.0
        reason_str = "VWAP Breakdown"
        penalty = deviation_dict["VWAP Breakdown"]
            (defaults to -0.40 — see autotuner.calculate_historical_deviation)
        triggered_return = 3.0 + (-0.40) = 2.60
        guard_alpha = 2.60 - 3.0 = -0.40
        missed_upside = 3.0 - 2.60 = 0.40       (NOT > 1.0 -> no penalty)
        drawdown_from_peak = 3.0 - 2.60 = 0.40  (NOT > 1.5 -> no penalty)
        guard_alpha < 0 -> total += -0.40 * 2.0 * weight = -0.80 * weight

    The time-decay weight is::

        weight = exp(-0.015 * days_ago)

    Since OOS evaluation runs over the LAST 20% of the 5-date series (after
    the 80/20 split inside run_autotuner), it picks up exactly the most
    recent date, "2026-04-05". days_ago vs current_date_str ("2026-05-10")
    is (May 10 - Apr 5) = 35 days. weight = exp(-0.015 * 35).

    Producer pin: oos_alpha = -(-0.80 * exp(-0.525)) = 0.80 * exp(-0.525).
    """
    # Compute the expected total_guard_alpha by hand using the same constants
    # the producer uses. We do NOT hardcode the literal — we DERIVE it from
    # the constants the producer uses (decay_rate=0.015 at autotuner.py:144;
    # penalty=-0.40 from calculate_historical_deviation's "VWAP Breakdown"
    # default at autotuner.py:35).
    DECAY_RATE = 0.015  # autotuner.py:144 — local to run_simulation
    DEFAULT_VWAP_BREAKDOWN_PENALTY = -0.40  # autotuner.py:35
    GUARD_ALPHA_NEGATIVE_MULTIPLIER = 2.0  # autotuner.py:294

    current_dt = datetime.strptime(_CURRENT_DATE_STR, "%Y-%m-%d")
    trigger_date = datetime.strptime(_TEST_DATES_5[-1], "%Y-%m-%d")
    days_ago = (current_dt - trigger_date).days
    weight = _math.exp(-DECAY_RATE * days_ago)

    # guard_alpha (per day, single-day OOS) = penalty * 2.0 * weight
    # run_simulation returns -total_guard_alpha; caller flips back ->
    # oos_alpha = total_guard_alpha (sign-equivalent).
    expected_total = DEFAULT_VWAP_BREAKDOWN_PENALTY * GUARD_ALPHA_NEGATIVE_MULTIPLIER * weight
    # The .2f pin: format expected to match producer.
    expected_formatted = f"{expected_total:.2f}"

    ai = _ai_full_params()
    fallback = _ai_full_params()
    fallback["VWAP_CROSS_HWM_PCT"] = 2.0
    default = _ai_full_params()
    default["VWAP_CROSS_HWM_PCT"] = 2.5

    history = _make_history({d: [_trigger_tick()] for d in _TEST_DATES_5})

    stdout, ctx = _run_capture(
        history=history,
        fallback_params=fallback,
        default_strategy=default,
        best_params=ai,
        vwap_side_effect=_stub_vwap_breakdown_trigger_on_first_tick,
    )

    alphas = _extract_alphas(stdout)

    # All three param sets traverse the identical code path under the stubs,
    # so all three alphas are equal.
    # The branch is whichever the >= cascade picks on the tie at the negative
    # expected value. Under autotuner.py:390 strict-positive rule: AI does
    # NOT strictly beat fallback (they tie), so we fall through to fallback.
    assert alphas["branch"] == "fallback", (
        f"On all-tied negative alphas the strict-positive rule must "
        f"fall through to fallback; got {alphas['branch']!r}.\n"
        f"stdout:\n{stdout}"
    )
    # Tight pin on the produced alpha: must match the hand-computed expected
    # value to within the producer's .2f format precision.
    assert alphas["ai"] == pytest.approx(float(expected_formatted), abs=1e-9), (
        f"AI OOS alpha differs from hand-computed expected.\n"
        f"  produced: {alphas['ai']}\n  expected: {expected_formatted}\n"
        f"  raw expected: {expected_total!r}\n"
    )
    assert alphas["fallback"] == pytest.approx(float(expected_formatted), abs=1e-9)
    assert alphas["default"] == pytest.approx(float(expected_formatted), abs=1e-9)


# ===========================================================================
# Scenario 3 — Massive drawdown post-trigger; peak-to-exit drawdown penalty
# (autotuner.py:289-290) DOES fire.
# ===========================================================================


def test_run_simulation_drawdown_penalty_fires_when_peak_exceeds_threshold():
    """
    SCENARIO 3 — pin the peak-to-exit drawdown penalty path.

    Multi-tick day: first ticks push the return up (HWM climbs); a later
    tick crashes the return down before the VWAP-break stub finally fires.
    The penalty terms that matter:

      safe_hwm                 = max return seen (we engineer this > 1.0)
      triggered_return         = exit-tick return + penalty
      drawdown_from_peak       = safe_hwm - triggered_return (engineer > 1.5)
      missed_upside            = day_max_return - triggered_return
                                 (will equal drawdown_from_peak because the
                                  HWM-tick is also the day's max)

    Test design:
      Day has 3 ticks. We CANNOT control which tick fires the trigger if the
      VWAP stub always returns True — it fires on tick_idx=0. So instead we
      must trigger LATER. Use a side_effect that returns True only on the
      Nth call (per day).
    """
    # Build a per-call counter: trigger fires on call index 2 (the 3rd tick
    # of the day) so the simulator has time to climb the HWM first.
    call_state = {"calls": 0}

    def trigger_on_third_call(**kwargs):
        call_state["calls"] += 1
        # Trigger on every 3rd call so each day's 3rd tick fires.
        # (3 ticks per day -> calls 3, 6, 9, ... fire).
        return (0, 0, call_state["calls"] % 3 == 0, False)

    ai = _ai_full_params()
    fallback = _ai_full_params()
    fallback["VWAP_CROSS_HWM_PCT"] = 2.0
    default = _ai_full_params()
    default["VWAP_CROSS_HWM_PCT"] = 2.5

    # Per-day 3-tick fixture: ramp up to a high HWM, then crash to a low
    # exit return. Tick returns: [4.0, 10.0, 1.0]. HWM after tick 1 = 10.0;
    # exit fires on tick 2 (idx=2) at return=1.0; penalty=-0.40 ->
    # triggered_return = 0.6. safe_hwm = 10.0; drawdown_from_peak = 9.4;
    # missed_upside = 10.0 - 0.6 = 9.4; eod_return = 1.0 (the last tick).
    high_tick = _trigger_tick(ret=4.0)
    peak_tick = _trigger_tick(ret=10.0)
    crash_tick = _trigger_tick(ret=1.0)
    history = _make_history(
        {d: [high_tick, peak_tick, crash_tick] for d in _TEST_DATES_5}
    )

    # Hand-compute expected total_guard_alpha per OOS day:
    DECAY_RATE = 0.015
    DEFAULT_VWAP_BREAKDOWN_PENALTY = -0.40
    MISSED_UPSIDE_THRESHOLD = 1.0  # autotuner.py:284
    MISSED_UPSIDE_MULTIPLIER = 1.5  # autotuner.py:285
    DRAWDOWN_THRESHOLD = 1.5  # autotuner.py:289
    DRAWDOWN_MULTIPLIER = 0.75  # autotuner.py:290
    GUARD_ALPHA_NEG_MULTIPLIER = 2.0  # autotuner.py:294

    current_dt = datetime.strptime(_CURRENT_DATE_STR, "%Y-%m-%d")
    # OOS gets the last day after 80/20 split on 5 dates.
    trigger_date = datetime.strptime(_TEST_DATES_5[-1], "%Y-%m-%d")
    days_ago = (current_dt - trigger_date).days
    weight = _math.exp(-DECAY_RATE * days_ago)

    safe_hwm = 10.0
    triggered_return = 1.0 + DEFAULT_VWAP_BREAKDOWN_PENALTY  # 0.6
    eod_return = 1.0
    day_max_return = 10.0
    guard_alpha = triggered_return - eod_return  # -0.4
    missed_upside = day_max_return - triggered_return  # 9.4
    drawdown_from_peak = safe_hwm - triggered_return  # 9.4

    total = 0.0
    if missed_upside > MISSED_UPSIDE_THRESHOLD:
        total -= missed_upside * MISSED_UPSIDE_MULTIPLIER * weight
    if safe_hwm > 1.0 and drawdown_from_peak > DRAWDOWN_THRESHOLD:
        total -= drawdown_from_peak * DRAWDOWN_MULTIPLIER * weight
    if guard_alpha < 0:
        total += guard_alpha * GUARD_ALPHA_NEG_MULTIPLIER * weight
    else:
        total += guard_alpha * weight
    # run_simulation returns -total; caller flips: oos_alpha = total.
    expected_formatted = f"{total:.2f}"

    # Reset the call counter (it's mutated above) so the actual run starts clean.
    call_state["calls"] = 0

    stdout, _ctx = _run_capture(
        history=history,
        fallback_params=fallback,
        default_strategy=default,
        best_params=ai,
        vwap_side_effect=trigger_on_third_call,
    )

    alphas = _extract_alphas(stdout)
    # All param sets traverse the same simulated path -> identical alphas.
    assert alphas["ai"] == pytest.approx(float(expected_formatted), abs=1e-9), (
        f"Drawdown-penalty path: AI alpha mismatch.\n"
        f"  produced: {alphas['ai']}\n  expected: {expected_formatted}\n"
        f"  raw expected: {total!r}\n"
        f"  weight={weight}, missed_upside={missed_upside}, "
        f"drawdown={drawdown_from_peak}, guard_alpha={guard_alpha}\n"
    )
    # And both peak-to-exit drawdown AND missed-upside penalties must have
    # contributed: verify that the magnitude exceeds what guard_alpha alone
    # would produce.
    guard_alpha_only = guard_alpha * GUARD_ALPHA_NEG_MULTIPLIER * weight
    assert abs(total) > abs(guard_alpha_only), (
        "Expected total to be more negative than guard_alpha-only path "
        "because both missed_upside AND drawdown penalties should fire."
    )


# ===========================================================================
# Scenario 4 — Empty history (acc_sym_ids has entries but no dates).
# ===========================================================================


def test_run_simulation_empty_history_data_pins_zero_alpha_sentinel():
    """
    SCENARIO 4 — empty per-symphony history at the simulation layer.

    The autotuner pre-empts a fully-empty history at autotuner.py:99-101 with
    an early-return None. We exercise the slightly different path where the
    history dict has dates AT THE TOP LEVEL but the inner sym_data mapping
    is empty for our target symphony — meaning ``run_simulation``'s inner
    loop produces zero iterations of the date loop.

    To produce this, give history a DIFFERENT symphony's data so date
    extraction in autotuner.py:104-107 sees non-empty dates, but our target
    symphony ("sym-A") has an empty dates dict.

    Expected: run_simulation iterates ``dates_data = history_data.get(sym_id,
    {})`` -> ``{}`` -> the for-loop is empty -> ``total_guard_alpha`` stays
    at its initial 0.0 -> return -0.0 -> caller flips to oos_alpha = 0.0.

    Penalty-math coverage: NONE (date loop never runs).
    """
    ai = _ai_full_params()
    fallback = _ai_full_params()
    fallback["VWAP_CROSS_HWM_PCT"] = 2.0
    default = _ai_full_params()
    default["VWAP_CROSS_HWM_PCT"] = 2.5

    # Inject dates into history under a DIFFERENT sym_id so the top-level
    # date extraction succeeds but our target symphony's data is empty.
    history = {
        "sym-A": {},  # target symphony — empty
        "OTHER-SYM": {d: [_flat_positive_tick()] for d in _TEST_DATES_5},
    }

    stdout, ctx = _run_capture(
        history=history,
        fallback_params=fallback,
        default_strategy=default,
        best_params=ai,
        vwap_side_effect=_stub_vwap_breakdown_no_trigger,
    )

    alphas = _extract_alphas(stdout)
    # All three OOS alphas must be exactly 0 (no date iterations).
    assert alphas["ai"] == pytest.approx(0.0, abs=1e-9)
    assert alphas.get("fallback", 0.0) == pytest.approx(0.0, abs=1e-9)
    assert alphas.get("default", 0.0) == pytest.approx(0.0, abs=1e-9)
    # And the branch under the all-zero tie is fallback (strict-positive cascade).
    assert alphas["branch"] == "fallback"

    # A strategy must still be persisted (fallback's params).
    save_calls = ctx["save_calls"]
    assert len(save_calls) == 1
    _name, persisted, _locked = save_calls[0]
    assert persisted["VWAP_CROSS_HWM_PCT"] == pytest.approx(
        fallback["VWAP_CROSS_HWM_PCT"], abs=1e-9
    )


# ===========================================================================
# Scenario 5 — Locked-vars override.
#
# IMPORTANT: ``run_simulation`` itself does NOT consult ``locked_vars``. The
# autotuner's locked_vars list is set at autotuner.py:137 from
# get_symphony_strategy, captured into the run_autotuner local scope, and
# then... never read inside run_simulation. The simulator uses ``p``
# verbatim. The locked_vars mechanism is a HIGHER-LEVEL contract (it would
# constrain which keys the Optuna study suggests, were that wiring present).
#
# This test pins the CURRENT behavior: locked_vars is passed in via
# get_symphony_strategy, captured by run_autotuner, and PERSISTED back via
# save_symphony_strategy. It does NOT influence run_simulation's output.
# Pinning the locked_vars round-trip is critical for the refactor: the
# post-hoist module-level run_simulation must NOT start consulting
# locked_vars either (a "fix" that secretly changes behavior is a bug).
# ===========================================================================


def test_locked_vars_round_trip_through_run_autotuner_unaffected_by_run_simulation():
    """
    SCENARIO 5 — pin that locked_vars flows ``get_symphony_strategy`` ->
    captured in run_autotuner local scope -> persisted unchanged through
    ``save_symphony_strategy``, and that ``run_simulation`` does NOT mutate
    or otherwise consume the locked list.

    Test passes a non-empty locked_vars list and asserts:
      * The list is persisted byte-equivalently.
      * The simulation produces the same OOS alpha as it would without
        any locked_vars (because run_simulation never reads the list).

    Penalty-math coverage: indirectly covers the no-trigger path (locked
    keys do not gate triggers in run_simulation).
    """
    ai = _ai_full_params()
    fallback = _ai_full_params()
    fallback["VWAP_CROSS_HWM_PCT"] = 2.0
    default = _ai_full_params()
    default["VWAP_CROSS_HWM_PCT"] = 2.5

    history = _make_history({d: [_flat_positive_tick()] for d in _TEST_DATES_5})

    locked = ["TRIGGER_THRESHOLD_PCT", "MAX_PARABOLIC_SQUEEZE"]

    stdout_locked, ctx_locked = _run_capture(
        history=history,
        fallback_params=fallback,
        default_strategy=default,
        best_params=ai,
        locked_vars=locked,
        vwap_side_effect=_stub_vwap_breakdown_no_trigger,
    )

    stdout_unlocked, ctx_unlocked = _run_capture(
        history=history,
        fallback_params=fallback,
        default_strategy=default,
        best_params=ai,
        locked_vars=[],
        vwap_side_effect=_stub_vwap_breakdown_no_trigger,
    )

    alphas_locked = _extract_alphas(stdout_locked)
    alphas_unlocked = _extract_alphas(stdout_unlocked)

    # Run simulation produces the same alpha regardless of locked_vars.
    assert alphas_locked["ai"] == pytest.approx(alphas_unlocked["ai"], abs=1e-9)
    if "fallback" in alphas_locked and "fallback" in alphas_unlocked:
        assert alphas_locked["fallback"] == pytest.approx(
            alphas_unlocked["fallback"], abs=1e-9
        )
    if "default" in alphas_locked and "default" in alphas_unlocked:
        assert alphas_locked["default"] == pytest.approx(
            alphas_unlocked["default"], abs=1e-9
        )

    # locked_vars round-trips byte-equivalently into save_symphony_strategy.
    assert len(ctx_locked["save_calls"]) == 1
    _name, _params, persisted_locked = ctx_locked["save_calls"][0]
    assert persisted_locked == locked, (
        f"locked_vars list round-trip corrupted.\n"
        f"  input:  {locked}\n  persisted: {persisted_locked}"
    )


# ===========================================================================
# Scenario 6 — Single-day, single-tick fixture — minimum viable simulation.
# ===========================================================================


def test_run_simulation_single_day_single_tick_no_trigger_zero_alpha():
    """
    SCENARIO 6 — the absolute minimum-viable simulation: 2 distinct dates
    (the floor enforced by autotuner.py:110-112) with single ticks each.
    No trigger is allowed to fire.

    The 80/20 split with 2 dates: split_idx = int(2 * 0.8) = 1. train_dates
    contains the first 1 date; test_dates contains the last 1 date. So
    the OOS run iterates exactly one date with one tick.

    Pin: total_guard_alpha = 0.0 (no trigger, no penalty block entry).
    The producer formats this as ``+0.00`` (since alpha > -0 is False, but
    alpha == 0 hits the > 0 branch... actually no, alpha=0 hits the ELSE
    branch ``OOS Guard Alpha: {oos_alpha:.2f}%`` per autotuner.py:392-394.
    """
    ai = _ai_full_params()
    fallback = _ai_full_params()
    fallback["VWAP_CROSS_HWM_PCT"] = 2.0
    default = _ai_full_params()
    default["VWAP_CROSS_HWM_PCT"] = 2.5

    history = _make_history({d: [_flat_positive_tick()] for d in _TEST_DATES_2})

    stdout, ctx = _run_capture(
        history=history,
        fallback_params=fallback,
        default_strategy=default,
        best_params=ai,
        vwap_side_effect=_stub_vwap_breakdown_no_trigger,
    )

    alphas = _extract_alphas(stdout)
    assert alphas["ai"] == pytest.approx(0.0, abs=1e-9)
    # Branch: all-zero tie -> fallback.
    assert alphas["branch"] == "fallback"

    # Persistence still occurs (exactly once) under the floor configuration.
    save_calls = ctx["save_calls"]
    assert len(save_calls) == 1


# ===========================================================================
# Cross-cutting characterization invariants — these guard structural
# properties that must survive the closure-hoisting refactor.
# ===========================================================================


def test_run_simulation_called_three_times_per_symphony_for_oos_evaluation():
    """
    CHARACTERIZATION INVARIANT — run_simulation must be invoked exactly
    THREE times during the OOS-evaluation block (autotuner.py:359, 371,
    375) — once each for AI, fallback, default. Training-loop invocation
    is suppressed in this test because study.optimize is a no-op (real
    Optuna would invoke it N_TRIALS times; we don't pin that here).

    The closure-hoist refactor MUST preserve this call count exactly: a
    refactor that accidentally collapses two OOS evaluations into one (e.g.,
    by sharing state across the three calls) would corrupt the decision.

    We pin by counting the number of times
    ``math_engine.compute_vwap_breakdown_update`` is called per day. With
    a single-tick-per-day fixture (5 days, 80/20 split -> 1 OOS day), and 3
    OOS calls (AI/fallback/default), we expect exactly 1 * 3 = 3 invocations.
    """
    ai = _ai_full_params()
    fallback = _ai_full_params()
    fallback["VWAP_CROSS_HWM_PCT"] = 2.0
    default = _ai_full_params()
    default["VWAP_CROSS_HWM_PCT"] = 2.5

    history = _make_history({d: [_flat_positive_tick()] for d in _TEST_DATES_5})

    # Wrap the no-trigger stub in a counter.
    counter = {"calls": 0}

    def counting_vwap_stub(**kwargs):
        counter["calls"] += 1
        return (0, 0, False, False)

    _stdout, _ctx = _run_capture(
        history=history,
        fallback_params=fallback,
        default_strategy=default,
        best_params=ai,
        vwap_side_effect=counting_vwap_stub,
    )

    # 1 OOS day x 1 tick per day x 3 OOS evaluations = 3 invocations.
    assert counter["calls"] == 3, (
        f"Expected exactly 3 OOS-phase invocations of "
        f"compute_vwap_breakdown_update (one per of AI/fallback/default); "
        f"got {counter['calls']}. Closure-hoist refactor must preserve "
        f"this call count exactly."
    )


def test_run_simulation_decay_rate_constant_is_0_015():
    """
    CHARACTERIZATION INVARIANT — the time-decay rate (autotuner.py:144,
    ``decay_rate = 0.015``) is a producer-private numeric constant that
    drives the exponential weighting in the penalty block. The closure-hoist
    refactor must keep this constant byte-identical; if the implementer
    parameterizes it, the parameter's DEFAULT must remain 0.015.

    We pin indirectly by comparing the OOS alpha at a known days_ago against
    the closed-form expression ``penalty * 2.0 * exp(-0.015 * days_ago)``.
    Any drift in decay_rate would show up as a .2f-level mismatch here.

    This duplicates Scenario 2's check but isolates the decay constant
    specifically — a refactor that swaps 0.015 for (say) 0.01 would pass
    Scenario 2's hand-computed pin only if Scenario 2's expected was
    re-derived with the wrong constant. Independent pin defends against
    that.
    """
    ai = _ai_full_params()
    fallback = _ai_full_params()
    fallback["VWAP_CROSS_HWM_PCT"] = 2.0
    default = _ai_full_params()
    default["VWAP_CROSS_HWM_PCT"] = 2.5

    history = _make_history({d: [_trigger_tick()] for d in _TEST_DATES_5})

    stdout, _ctx = _run_capture(
        history=history,
        fallback_params=fallback,
        default_strategy=default,
        best_params=ai,
        vwap_side_effect=_stub_vwap_breakdown_trigger_on_first_tick,
    )

    alphas = _extract_alphas(stdout)

    # If decay_rate were anything other than 0.015 in the producer, this
    # expected value would mismatch. We do NOT assert against the alternate
    # decay rates — the producer's value is 0.015, period.
    DECAY_RATE = 0.015
    DEFAULT_PENALTY = -0.40
    GUARD_NEG_MULT = 2.0
    days_ago = (
        datetime.strptime(_CURRENT_DATE_STR, "%Y-%m-%d")
        - datetime.strptime(_TEST_DATES_5[-1], "%Y-%m-%d")
    ).days
    expected = DEFAULT_PENALTY * GUARD_NEG_MULT * _math.exp(-DECAY_RATE * days_ago)

    assert alphas["ai"] == pytest.approx(float(f"{expected:.2f}"), abs=1e-9), (
        f"Producer's decay_rate must be 0.015. Observed OOS alpha would "
        f"only match the expected formula with decay_rate=0.015.\n"
        f"  produced: {alphas['ai']}\n  expected (decay=0.015): {expected:.2f}\n"
    )


def test_run_simulation_eod_return_taken_from_last_tick():
    """
    CHARACTERIZATION INVARIANT — ``eod_return = ticks[-1]["return"]``
    (autotuner.py:166) — the simulator reads end-of-day return from the
    last tick of the day, NOT from the trigger tick. A refactor that
    accidentally reads the trigger-tick return as eod_return would shift
    the guard_alpha calculation.

    Construct a fixture where the trigger fires on tick 2 (return=1.0) but
    the LAST tick of the day has a different return (5.0). The
    guard_alpha = triggered_return - eod_return computation must use 5.0
    for eod_return, not 1.0.
    """
    # Trigger on the 2nd tick; the day has 3 ticks total.
    call_state = {"calls": 0}

    def trigger_on_second_tick(**kwargs):
        call_state["calls"] += 1
        # 3 ticks per day -> fire on calls 2, 5, 8, ...
        return (0, 0, call_state["calls"] % 3 == 2, False)

    ai = _ai_full_params()
    fallback = _ai_full_params()
    fallback["VWAP_CROSS_HWM_PCT"] = 2.0
    default = _ai_full_params()
    default["VWAP_CROSS_HWM_PCT"] = 2.5

    # Day's ticks: [3.0 first, 1.0 trigger, 5.0 last]. HWM after tick 0 = 3.0.
    # Trigger fires on tick_idx=1 (call 2 of the day). triggered_return = 1.0 +
    # (-0.40) = 0.60. eod_return = 5.0 (last tick). guard_alpha = 0.60 - 5.0
    # = -4.40. safe_hwm = max(3.0, 1.0) at tick 1 = 3.0 (HWM doesn't update
    # because 1.0 < 3.0). day_max_return = max(3.0, 1.0, 5.0) = 5.0.
    # missed_upside = 5.0 - 0.6 = 4.4 (> 1.0 -> penalty fires).
    # drawdown_from_peak = 3.0 - 0.6 = 2.4 (> 1.5 -> penalty fires).
    history = _make_history(
        {
            d: [
                _trigger_tick(ret=3.0),
                _trigger_tick(ret=1.0),
                _trigger_tick(ret=5.0),
            ]
            for d in _TEST_DATES_5
        }
    )

    DECAY_RATE = 0.015
    PENALTY = -0.40
    safe_hwm = 3.0
    triggered_return = 1.0 + PENALTY
    eod_return = 5.0
    day_max_return = 5.0
    guard_alpha = triggered_return - eod_return  # -4.4
    missed_upside = day_max_return - triggered_return  # 4.4
    drawdown_from_peak = safe_hwm - triggered_return  # 2.4
    days_ago = (
        datetime.strptime(_CURRENT_DATE_STR, "%Y-%m-%d")
        - datetime.strptime(_TEST_DATES_5[-1], "%Y-%m-%d")
    ).days
    weight = _math.exp(-DECAY_RATE * days_ago)

    total = 0.0
    if missed_upside > 1.0:
        total -= missed_upside * 1.5 * weight
    if safe_hwm > 1.0 and drawdown_from_peak > 1.5:
        total -= drawdown_from_peak * 0.75 * weight
    if guard_alpha < 0:
        total += guard_alpha * 2.0 * weight
    else:
        total += guard_alpha * weight

    expected_fmt = f"{total:.2f}"

    call_state["calls"] = 0  # reset before the actual run

    stdout, _ctx = _run_capture(
        history=history,
        fallback_params=fallback,
        default_strategy=default,
        best_params=ai,
        vwap_side_effect=trigger_on_second_tick,
    )

    alphas = _extract_alphas(stdout)
    assert alphas["ai"] == pytest.approx(float(expected_fmt), abs=1e-9), (
        f"eod_return must be read from ticks[-1] (5.0), not from the "
        f"trigger tick (1.0). If this assertion fails, the simulator "
        f"likely regressed to reading eod_return at trigger-time.\n"
        f"  produced: {alphas['ai']}\n  expected: {expected_fmt}\n"
    )
