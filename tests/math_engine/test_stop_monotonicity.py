"""
RED tests pinning the canonical trailing-stop monotonicity invariant on the
math_engine.py stop-resolution path (compute_breakeven_update +
compute_active_trailing_stop).

Background (Cycle A2 — task description):

A trailing stop is defined (per Fu & Zhang 2010 and standard risk-engineering
practice) by ONE load-bearing invariant: once tightened, it NEVER loosens
during a position's lifetime. The audit found this invariant is not pinned
anywhere in the math layer's tests, AND the producer math actually permits
the persisted stop level to DECREASE under a specific (and not rare)
scenario:

    1. Position enters; current_return drifts up; breakeven_locked latches
       True after HWM_HOLD_TICKS_THRESHOLD qualifying ticks.
    2. base_stop_level (computed upstream as e.g. current_return -
       active_trailing_stop_distance) reaches 2.0 on tick N.
       compute_breakeven_update returns stop_trigger_level = max(2.0, 0.0)
       = 2.0.
    3. On tick N+1, the upstream pipeline retraces base_stop_level to 1.0
       (smaller HWM, smaller active distance, or both). Lock is still True
       (it never resets). compute_breakeven_update returns stop_trigger_
       level = max(1.0, 0.0) = 1.0.
    4. The PERSISTED stop_trigger_level just LOOSENED from 2.0 to 1.0. That
       is a trailing-stop monotonicity violation.

The math layer alone CAN enforce monotonicity IF the caller (or this layer)
threads the previously-persisted stop_trigger_level into the next call and
the function clamps the new level upward (max-of-previous-and-new). That's
the GREEN behavior we want to pin.

The pure layer is currently stateless across ticks; the GREEN fix can
either:
  (a) take the previously-persisted stop_trigger_level as an input
      parameter and return max(prev, new_resolved_level), or
  (b) keep the per-tick API but document a caller obligation to clamp.

These tests assume (a) — the math layer is the right home for monotonicity
because the layer above is the live-execution engine which already trusts
math_engine for risk semantics. (b) would push semantic load onto the
caller in violation of the project rule "Engine runs 1-minute cadence
during market hours — no blocking I/O on the execution path" (clamp logic
in the hot path is brittle).

Implementer (GREEN phase) — concrete suggestion (NOT a constraint; the test
writer makes the case for (a) but the implementer / risk-engine-specialist
owns the final API choice):
  - Add a new function compute_monotonic_stop_trigger_level(prev_locked_in:
    float | None, candidate: float, is_triggered: bool) -> float that
    returns:
        TRIGGERED_OVERRIDE_LEVEL if is_triggered
        candidate if prev_locked_in is None  (position-open tick)
        max(prev_locked_in, candidate) otherwise
  - Modify compute_breakeven_update OR the caller to thread the previous
    tick's stop_trigger_level through this clamp before persisting.
  - Add a UNIT_STOP_TRIGGER_LEVEL_FLOOR constant if any further structural
    bound is needed (none required by the current invariants).

The MULTI-CALL property tests below stand up the spec for that clamp
without naming the function (the implementer can pick the API surface
during GREEN). What they pin is the sequence-level invariant: across N
consecutive calls representing the lifetime of a single position, the
resolved stop_trigger_level must be monotonically non-decreasing.

Tolerance policy:
  - Exact equality (==) on the monotonicity comparison: a < b is strict
    enough; tolerance would mask off-by-epsilon regressions where the math
    drifted in the WRONG direction.
  - pytest.approx with rel=1e-9, abs=1e-12 on derived expected values that
    pass through IEEE-754 max() chains (no transcendentals; ~1 ulp is the
    realistic noise floor; a wrong impl misses by structural factors).

Provenance: every expected value in the per-test scenarios is DERIVED BY
HAND in the test docstring or inline comment, never captured from a
producer. The math is max() and conditional pass-through; the derivations
are spelled out.
"""

from __future__ import annotations

import pytest

import math_engine


# --- Tolerance --------------------------------------------------------------

APPROX_REL = 1e-9
APPROX_ABS = 1e-12


# ===========================================================================
# Helper: a thin "persisted state" wrapper that mirrors what the live engine
# would do across ticks. The wrapper enforces NOTHING on its own; it just
# threads outputs back as inputs. The MATH must do the right thing.
#
# This helper is shared by all sequential-cycle tests. It is deliberately
# TINY and does no clamping of its own — the math under test owns
# monotonicity.
# ===========================================================================


def _simulate_position_lifetime(
    ticks: list[dict],
) -> list[tuple[int, bool, float]]:
    """
    Replay N ticks against compute_breakeven_update, threading
    (hwm_hold_ticks, breakeven_locked, stop_trigger_level) state forward.

    Each tick dict must supply:
        current_return, symphony_vol, base_stop_level, is_triggered

    Returns a list of (new_hold_ticks, new_breakeven_locked,
    stop_trigger_level) tuples — one per tick, in order.

    NO clamping or monotonicity enforcement is applied here. The producer
    math owns the invariant; this helper only persists state.
    """
    state_hold_ticks = 0
    state_locked = False
    out: list[tuple[int, bool, float]] = []
    for tick in ticks:
        new_hold_ticks, new_locked, stop_level = math_engine.compute_breakeven_update(
            current_return=tick["current_return"],
            symphony_vol=tick["symphony_vol"],
            base_stop_level=tick["base_stop_level"],
            current_hold_ticks=state_hold_ticks,
            currently_breakeven_locked=state_locked,
            is_triggered=tick["is_triggered"],
        )
        out.append((new_hold_ticks, new_locked, stop_level))
        # Persist state forward (this is what the live engine does after
        # each call; we mimic it faithfully).
        state_hold_ticks = new_hold_ticks
        state_locked = new_locked
    return out


# ===========================================================================
# TEST 1 — Sequential monotonicity (the canonical trailing-stop invariant).
#
# Once locked, the persisted stop_trigger_level across consecutive ticks
# must be MONOTONICALLY NON-DECREASING. A test that fails on current code:
# we replay a sequence where base_stop_level rises to 2.0 then RETRACES to
# 1.0 post-lock, and assert non-decreasing across the full sequence.
#
# EXPECTED RED: current code returns 1.0 on the retrace tick, which is
# strictly LESS THAN the 2.0 from the prior tick. The assertion fires on
# that tick.
# ===========================================================================


def test_stop_trigger_level_is_monotonically_non_decreasing_across_lifetime() -> None:
    """
    CANONICAL TRAILING-STOP INVARIANT (Fu & Zhang 2010): once tightened, a
    trailing stop NEVER loosens for the lifetime of the position.

    Scenario (derived by hand):
      Use symphony_vol=1.0 throughout, so dynamic_activation = max(0.4,
      min(3.0, 1.0)) = 1.0. The qualifying threshold is dynamic_activation -
      DEADBAND = 1.0 - 0.2 = 0.8. We need >= HWM_HOLD_TICKS_THRESHOLD (5)
      consecutive qualifying ticks to latch the lock.

      Tick sequence (current_return, base_stop_level):
        t1  ( 1.5, -0.5)  return >= 0.8 -> ticks=1, locked=False, stop=-0.5
        t2  ( 1.6, -0.3)  ticks=2, locked=False, stop=-0.3
        t3  ( 1.7, -0.1)  ticks=3, locked=False, stop=-0.1
        t4  ( 1.8,  0.5)  ticks=4, locked=False, stop=0.5
        t5  ( 1.9,  1.0)  ticks=5 -> LOCK FIRES (5 >= 5), stop=max(1.0,0)=1.0
        t6  ( 2.5,  2.0)  ticks=6, locked=True, stop=max(2.0,0)=2.0
        t7  ( 2.0,  1.0)  ticks=7, locked=True (latched), stop=max(1.0,0)=1.0
                          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                          MONOTONICITY VIOLATION: 1.0 < 2.0 (from t6).

      Sequence of persisted stop levels:
        [-0.5, -0.3, -0.1, 0.5, 1.0, 2.0, 1.0]

      The drop at t7 (2.0 -> 1.0) IS the invariant violation this test
      pins. The fix (proposed): math_engine clamps the new resolved level
      against the previously persisted level (max of the two).

    WHY THIS FAILS TODAY: compute_breakeven_update is per-tick stateless
    w.r.t. the previously-persisted stop_trigger_level. The post-lock floor
    is anchored at 0.0, not at the previously-persisted level. The math
    can return a SMALLER level than last tick whenever base_stop_level
    retraces above zero.

    WHY THIS MATTERS: real-money trading. A loosening stop after a hwm
    drift means giving back unrealized profit the engine had already
    "locked in" semantically. The invariant is a hard contract.
    """
    ticks = [
        {"current_return": 1.5, "symphony_vol": 1.0, "base_stop_level": -0.5, "is_triggered": False},
        {"current_return": 1.6, "symphony_vol": 1.0, "base_stop_level": -0.3, "is_triggered": False},
        {"current_return": 1.7, "symphony_vol": 1.0, "base_stop_level": -0.1, "is_triggered": False},
        {"current_return": 1.8, "symphony_vol": 1.0, "base_stop_level":  0.5, "is_triggered": False},
        {"current_return": 1.9, "symphony_vol": 1.0, "base_stop_level":  1.0, "is_triggered": False},
        {"current_return": 2.5, "symphony_vol": 1.0, "base_stop_level":  2.0, "is_triggered": False},
        {"current_return": 2.0, "symphony_vol": 1.0, "base_stop_level":  1.0, "is_triggered": False},
    ]
    results = _simulate_position_lifetime(ticks)

    # Sanity: lock fires on or before tick 5 (5 qualifying ticks in a row,
    # threshold = HWM_HOLD_TICKS_THRESHOLD = 5). This is documentary; the
    # main assertion is monotonicity.
    assert results[4][1] is True, (
        f"Sanity check: lock should fire by tick 5 (5 consecutive qualifying "
        f"ticks). Got locked={results[4][1]} on tick 5. "
        f"If this fails, the test fixture is mis-specified, not the impl."
    )

    levels = [r[2] for r in results]
    # Monotonicity assertion: each level >= previous. Strict `<` would
    # require strict increases; we use `<` to assert non-decreasing
    # (>=). Exact comparison (no approx): if the new level is BIT-
    # IDENTICAL to the old, that's fine; only a strict decrease violates.
    violations: list[tuple[int, float, float]] = []
    for i in range(1, len(levels)):
        if levels[i] < levels[i - 1]:
            violations.append((i, levels[i - 1], levels[i]))

    assert not violations, (
        f"TRAILING-STOP MONOTONICITY VIOLATED. Sequence of persisted "
        f"stop_trigger_level across ticks: {levels}. "
        f"Tick(s) where level DROPPED (i, prev, new): {violations}. "
        f"Canonical trailing-stop invariant (Fu & Zhang 2010): once "
        f"tightened, NEVER loosens. The producer math allows a drop "
        f"whenever base_stop_level retraces above zero post-lock. The "
        f"GREEN fix must clamp the new resolved level against the "
        f"previously persisted level."
    )


# ===========================================================================
# TEST 2 — Post-breakeven floor invariant: once locked, level cannot drop
# below the breakeven anchor (0.0%). This is the LATERAL floor: the lock
# semantics say "stop can't be worse than zero loss". A naive impl that
# accidentally returns negative base_stop_level after the lock would
# violate this.
#
# Note this is DIFFERENT from the sequential monotonicity test: this one
# only asserts the structural floor at 0.0 once the lock is True, not
# strict non-decreasing across ticks.
#
# Status: this property is currently HONORED by the producer (max(base,
# 0.0) at the lock branch). The test pins it as a regression guard.
# ===========================================================================


@pytest.mark.parametrize(
    "base_stop_level",
    [
        -100.0,    # deeply negative base
        -5.0,
        -1.0,
        -0.5,
        -0.001,
        -1e-12,    # epsilon-negative
        0.0,       # exact zero (boundary)
        0.001,     # epsilon-positive (no clamp needed)
        1.0,
        100.0,
    ],
)
def test_post_breakeven_stop_level_never_below_zero(base_stop_level: float) -> None:
    """
    POST-BREAKEVEN FLOOR INVARIANT: when breakeven_locked has latched
    (currently_breakeven_locked=True), the returned stop_trigger_level
    MUST be >= 0.0 — regardless of how negative base_stop_level is.

    Derivation: max(base_stop_level, 0.0). For base in [-100, 0): returns
    0.0. For base >= 0: returns base. The function-level floor is 0.0.

    Catches an impl that:
      - omitted the max(..., 0.0) clamp at the locked branch
      - applied the override sentinel (-999.0) without first checking
        is_triggered
      - sign-flipped the comparison and returned min instead of max
    """
    _, _, stop_level = math_engine.compute_breakeven_update(
        current_return=0.0,
        symphony_vol=1.0,
        base_stop_level=base_stop_level,
        current_hold_ticks=0,
        currently_breakeven_locked=True,
        is_triggered=False,
    )
    assert stop_level >= 0.0, (
        f"POST-BREAKEVEN FLOOR VIOLATED: base_stop_level={base_stop_level}, "
        f"breakeven_locked=True, but returned stop_trigger_level="
        f"{stop_level} which is BELOW 0.0. The lock semantics anchor the "
        f"stop at no-worse-than-zero-loss; the producer must apply "
        f"max(base, 0.0) at the locked branch."
    )


# ===========================================================================
# TEST 3 — Stop-level lower bound from the active-trailing-stop layer.
#
# compute_active_trailing_stop returns a STOP DISTANCE (in percentage
# points). Per the producer math:
#   safe_vol = symphony_vol if symphony_vol > 0 else VOL_FALLBACK
#   active = max(safe_vol * dynamic_multiplier, dynamic_min_stop)
#   if (para_armed or breakeven_locked): active *= squeeze_multiplier
#
# Without the squeeze, the lower bound is dynamic_min_stop (caller-
# supplied). With the squeeze, the squeeze multiplier can produce an
# output below dynamic_min_stop. The test pins THE PRESENT BOUND ONLY for
# the no-squeeze case (which is also the safest claim).
# ===========================================================================


@pytest.mark.parametrize(
    "symphony_vol,dynamic_multiplier,dynamic_min_stop",
    [
        # case: vol-scale wins (output = safe_vol * mult > min_stop)
        (2.0, 1.5, 0.3),
        # case: min-stop floor wins (output = min_stop > safe_vol * mult)
        (0.1, 0.5, 0.3),
        # case: zero vol uses VOL_FALLBACK; output > 0
        (0.0, 1.0, 0.2),
        # case: negative vol uses VOL_FALLBACK; output > 0
        (-0.5, 1.0, 0.2),
        # case: tiny positive vol -> tiny product, floor wins
        (1e-9, 1.0, 0.15),
    ],
)
def test_active_trailing_stop_lower_bound_is_dynamic_min_stop_when_unsqueezed(
    symphony_vol: float,
    dynamic_multiplier: float,
    dynamic_min_stop: float,
) -> None:
    """
    Lower-bound invariant for compute_active_trailing_stop (UNSQUEEZED
    regime, i.e. para_armed=False AND breakeven_locked=False):

        output >= dynamic_min_stop

    Derivation: max(safe_vol * mult, dynamic_min_stop) >= dynamic_min_stop
    by definition of max. The squeeze branch does NOT fire here, so no
    additional multiplicative reduction can push the output below the
    floor.

    Catches an impl that:
      - dropped the max() and returned safe_vol * mult unconditionally
      - applied the squeeze unconditionally (would reduce below floor)
      - clamped against dynamic_min_stop using min() (sign flip)

    Note: in the SQUEEZED regime (either flag True with squeeze < 1.0),
    the output CAN be below dynamic_min_stop — that is the documented
    behavior (parabolic squeeze tightens beyond the normal floor) and is
    NOT tested by this property. See active_trailing_stop fixtures 11/12
    for squeeze-regime pinning.
    """
    output = math_engine.compute_active_trailing_stop(
        symphony_vol=symphony_vol,
        dynamic_multiplier=dynamic_multiplier,
        dynamic_min_stop=dynamic_min_stop,
        para_armed=False,
        breakeven_locked=False,
        parabolic_squeeze_multiplier=0.5,   # set but should NOT fire
    )
    assert output >= dynamic_min_stop, (
        f"Lower-bound violated: output {output} < dynamic_min_stop "
        f"{dynamic_min_stop}. Unsqueezed regime guarantees "
        f"output = max(safe_vol*mult, dynamic_min_stop) >= dynamic_min_stop."
    )


# ===========================================================================
# TEST 4 — Stop-trigger-level upper-vs-base relationship.
#
# In compute_breakeven_update, the returned stop_trigger_level can only
# move in ONE direction relative to base_stop_level:
#   - is_triggered=True: sentinel override (handled separately).
#   - locked=True: stop = max(base, 0.0) >= base when base <= 0; == base
#     when base >= 0. Always >= base.
#   - locked=False: stop = base. Equal.
#
# So stop_trigger_level >= base_stop_level in the non-triggered path. The
# modification can only TIGHTEN (raise the stop level closer to or above
# current_return), never LOOSEN it (lower it below base).
#
# Note: this is a SINGLE-CALL invariant, the natural reading of "modifi-
# cations can only TIGHTEN, never LOOSEN" for the producer's resolution
# step. It is complementary to TEST 1 (which is the multi-call lifetime
# property).
# ===========================================================================


@pytest.mark.parametrize(
    "base_stop_level,locked",
    [
        (-5.0, False), (-5.0, True),
        (-1.0, False), (-1.0, True),
        (-0.001, False), (-0.001, True),
        (0.0, False), (0.0, True),
        (0.5, False), (0.5, True),
        (2.0, False), (2.0, True),
        (100.0, False), (100.0, True),
    ],
)
def test_stop_level_modifications_only_tighten_never_loosen_relative_to_base(
    base_stop_level: float, locked: bool,
) -> None:
    """
    SINGLE-CALL TIGHTEN-ONLY INVARIANT for compute_breakeven_update
    (is_triggered=False path):

        stop_trigger_level >= base_stop_level

    Derivation:
      - locked=False: stop = base; equality holds.
      - locked=True: stop = max(base, 0.0). For base <= 0 -> stop = 0.0 >=
        base. For base >= 0 -> stop = base; equality holds.

    Catches an impl that:
      - applied the max() in the wrong direction (would return min(base,
        0.0), i.e. clamp DOWN at 0.0 — pushes stop further from current_
        return = LOOSER)
      - returned a different floor (e.g. some negative constant)
      - applied the lock floor when not locked (would lose strict
        equality)

    Excludes is_triggered=True (sentinel override -999.0 deliberately
    violates this relationship; the override is its own contract tested
    elsewhere — see test_triggered_override_is_absolute in
    test_breakeven_update.py).
    """
    _, _, stop_level = math_engine.compute_breakeven_update(
        current_return=0.0,
        symphony_vol=1.0,
        base_stop_level=base_stop_level,
        current_hold_ticks=0,
        currently_breakeven_locked=locked,
        is_triggered=False,
    )
    assert stop_level >= base_stop_level, (
        f"TIGHTEN-ONLY VIOLATED: base_stop_level={base_stop_level}, "
        f"locked={locked}, returned stop_trigger_level={stop_level}. "
        f"Modifications must only RAISE the level (tighten), never LOWER "
        f"it (loosen). Got a DROP of {base_stop_level - stop_level} pct "
        f"points relative to base."
    )


# ===========================================================================
# TEST 5 — Breakeven_locked one-way latch ACROSS sequential calls.
#
# This crosses function boundaries (the value flows out of one call as
# new_breakeven_locked, and back into the next call as currently_break
# even_locked). The latch must hold for the lifetime of the position
# regardless of intermediate inputs.
#
# Status: the latching invariant is already pinned at single-call scope
# in test_breakeven_update.py (test_latching_invariant_locked_stays_
# locked). This test extends it to the MULTI-CALL lifecycle scope — a
# stress sequence with inputs that would otherwise reset ticks /
# unset locks under a wrong impl.
# ===========================================================================


def test_breakeven_locked_is_one_way_latch_across_sequential_calls() -> None:
    """
    ONE-WAY LATCH (multi-call lifecycle): once a call returns
    new_breakeven_locked=True, every subsequent call within the same
    position must also return True — regardless of:
      - current_return dropping below the qualifying threshold (would
        reset hold_ticks to 0)
      - is_triggered toggling on/off
      - base_stop_level oscillating
      - symphony_vol shifting across the clamp range
      - the position re-meeting and re-failing the qualifying threshold
        (many times)

    The single-call latching invariant is already pinned; this test
    extends it to the LIFECYCLE scope by replaying a 10-tick sequence
    designed to STRESS the latch in every way the lifecycle permits.

    Derivation of the sequence (current_return, symphony_vol,
    base_stop_level, is_triggered):
      t1  ( 1.5, 1.0, -0.5, False)  q-tick 1
      t2  ( 1.5, 1.0, -0.5, False)  q-tick 2
      t3  ( 1.5, 1.0, -0.5, False)  q-tick 3
      t4  ( 1.5, 1.0, -0.5, False)  q-tick 4
      t5  ( 1.5, 1.0, -0.5, False)  q-tick 5 -> LOCK FIRES
      t6  (-5.0, 1.0, -2.0, False)  return crashes; resets hold_ticks;
                                    BUT lock must HOLD
      t7  (-5.0, 5.0, -2.0, False)  vol shifts; still nothing resets lock
      t8  (-5.0, 0.1, -2.0, True)   is_triggered fires
      t9  ( 0.5, 1.0,  0.0, False)  is_triggered un-fires (engine restarts?)
      t10 (-5.0, 1.0, -1.0, False)  crash again
    The lock must be True on every tick from t5 onward.

    NOTE on is_triggered transitions: the producer math does NOT use
    is_triggered to reset breakeven_locked. is_triggered only affects
    stop_trigger_level (sentinel override). The latch lives in the lock
    field exclusively.
    """
    ticks = [
        {"current_return":  1.5, "symphony_vol": 1.0, "base_stop_level": -0.5, "is_triggered": False},
        {"current_return":  1.5, "symphony_vol": 1.0, "base_stop_level": -0.5, "is_triggered": False},
        {"current_return":  1.5, "symphony_vol": 1.0, "base_stop_level": -0.5, "is_triggered": False},
        {"current_return":  1.5, "symphony_vol": 1.0, "base_stop_level": -0.5, "is_triggered": False},
        {"current_return":  1.5, "symphony_vol": 1.0, "base_stop_level": -0.5, "is_triggered": False},
        {"current_return": -5.0, "symphony_vol": 1.0, "base_stop_level": -2.0, "is_triggered": False},
        {"current_return": -5.0, "symphony_vol": 5.0, "base_stop_level": -2.0, "is_triggered": False},
        {"current_return": -5.0, "symphony_vol": 0.1, "base_stop_level": -2.0, "is_triggered": True},
        {"current_return":  0.5, "symphony_vol": 1.0, "base_stop_level":  0.0, "is_triggered": False},
        {"current_return": -5.0, "symphony_vol": 1.0, "base_stop_level": -1.0, "is_triggered": False},
    ]
    results = _simulate_position_lifetime(ticks)

    # Documentary sanity: lock fires on tick 5 (1-indexed) = index 4.
    assert results[4][1] is True, (
        f"Sanity: lock should fire on tick 5 (5 qualifying ticks in a row). "
        f"Got locked={results[4][1]} on tick 5. Fixture mis-specified if "
        f"this fires."
    )

    # Latch assertion: from tick 5 onward, every tick's new_locked must be
    # True. Exact bool equality (not truthiness) — see breakeven type
    # contract.
    for i in range(4, len(results)):
        new_locked = results[i][1]
        assert new_locked is True, (
            f"ONE-WAY LATCH VIOLATED at tick {i + 1} (0-indexed {i}): "
            f"new_breakeven_locked={new_locked!r}, expected True. The "
            f"lock latched at tick 5 and must hold for the lifetime of "
            f"the position. Sequence of locked values: "
            f"{[r[1] for r in results]}."
        )


# ===========================================================================
# TEST 6 — Property test variant (hypothesis-style without the hypothesis
# dependency): randomized sequential cycles with non-decreasing assertion.
#
# We do NOT import hypothesis (project's test deps don't include it as far
# as the existing test files show — none of test_*.py imports hypothesis).
# Instead, we parametrize over a hand-crafted set of adversarial sequences
# that each represent a different shape of the lifetime monotonicity
# property.
# ===========================================================================


# Each scenario is (id, list_of_base_stop_levels_post_lock, expected_to_
# violate_under_current_code). All scenarios use symphony_vol=1.0 and
# current_return=2.0 throughout (well above the q-threshold) so that
# hold_ticks accumulates monotonically and the lock latches on tick 5.
# The interesting variation is base_stop_level post-lock.
_POSTLOCK_SEQUENCES: list[tuple[str, list[float]]] = [
    # All scenarios start with 5 pre-lock ticks (base levels chosen to be
    # below current_return; we just need lock to fire on tick 5). The
    # values listed below are JUST the post-lock segment (ticks 6+).
    ("strictly_increasing",          [0.5, 1.0, 1.5, 2.0, 2.5]),   # no violation
    ("flat_then_increasing",         [1.0, 1.0, 1.0, 2.0, 2.0]),   # no violation
    ("retrace_above_zero",           [2.0, 1.5, 1.0, 0.5, 0.0]),   # VIOLATES (strictly decreasing post-lock)
    ("retrace_dip_recover",          [1.0, 2.0, 1.5, 2.5, 3.0]),   # VIOLATES (2.0 -> 1.5 dip)
    ("retrace_below_zero",           [2.0, 1.0, -1.0, -2.0, -3.0]),# VIOLATES (2.0 -> 1.0; also -1 -> -2 but floored to 0)
    ("oscillating_above_zero",       [1.0, 2.0, 1.0, 2.0, 1.0]),   # VIOLATES (2.0 -> 1.0 twice)
    ("always_negative_floored_zero", [-1.0, -2.0, -3.0, -4.0, -5.0]),# no violation (all floor to 0; flat 0 is non-decreasing)
]


@pytest.mark.parametrize(
    "scenario_id,postlock_bases",
    _POSTLOCK_SEQUENCES,
    ids=[s[0] for s in _POSTLOCK_SEQUENCES],
)
def test_lifetime_monotonicity_across_postlock_base_shapes(
    scenario_id: str, postlock_bases: list[float],
) -> None:
    """
    Property: across a 10-tick lifetime (5 pre-lock + 5 post-lock), the
    persisted stop_trigger_level sequence is monotonically non-decreasing.

    The 5 pre-lock ticks use a flat positive base (-1.0) to accumulate
    hold_ticks; the lock fires on tick 5 (5 qualifying ticks in a row,
    threshold = HWM_HOLD_TICKS_THRESHOLD = 5). The post-lock 5 ticks vary
    base_stop_level per scenario.

    Expected RED scenarios (current impl violates):
      - retrace_above_zero: 2.0 -> 1.5 (drop)
      - retrace_dip_recover: 2.0 -> 1.5 (drop)
      - retrace_below_zero: 2.0 -> 1.0 (drop)
      - oscillating_above_zero: 2.0 -> 1.0 (drop)

    Expected GREEN scenarios (current impl satisfies):
      - strictly_increasing, flat_then_increasing: monotone-by-construction
      - always_negative_floored_zero: all floor to 0.0 post-lock; the
        sequence is flat at 0.0 post-lock, which is non-decreasing.
    """
    # Pre-lock priming: 5 ticks with current_return=1.5, base=-1.0, vol=1.0.
    # Threshold = 1.0 - 0.2 = 0.8; 1.5 > 0.8 so each tick is qualifying.
    pre_lock = [
        {"current_return": 1.5, "symphony_vol": 1.0, "base_stop_level": -1.0, "is_triggered": False}
        for _ in range(5)
    ]
    post_lock = [
        {"current_return": 2.0, "symphony_vol": 1.0, "base_stop_level": b, "is_triggered": False}
        for b in postlock_bases
    ]
    ticks = pre_lock + post_lock
    results = _simulate_position_lifetime(ticks)
    levels = [r[2] for r in results]

    # Documentary sanity: lock fires on tick 5.
    assert results[4][1] is True, (
        f"[{scenario_id}] Sanity: lock should fire on tick 5. Got "
        f"locked={results[4][1]}. Fixture mis-specified."
    )

    violations: list[tuple[int, float, float]] = []
    for i in range(1, len(levels)):
        if levels[i] < levels[i - 1]:
            violations.append((i, levels[i - 1], levels[i]))

    assert not violations, (
        f"[{scenario_id}] MONOTONICITY VIOLATED. Lifetime levels: "
        f"{levels}. Drops at (tick_idx, prev, new): {violations}. The "
        f"canonical trailing-stop invariant requires non-decreasing "
        f"persisted stop_trigger_level across the position lifetime; "
        f"the producer math fails to clamp the new resolved level "
        f"against the previously persisted level."
    )


# ===========================================================================
# TEST 7 — Determinism check for the multi-call simulation.
#
# Independent of monotonicity: replaying the same tick sequence twice
# produces bit-identical results. This is a sanity test that the helper
# itself is deterministic and the math functions are pure (already pinned
# at the single-call level in test_breakeven_update.py and
# test_active_trailing_stop.py, but extending to the multi-call composition
# is cheap and pins the helper.)
# ===========================================================================


def test_simulate_position_lifetime_is_deterministic() -> None:
    """
    Sanity: the multi-call helper is deterministic. Same inputs -> same
    outputs. Catches an accidental state leak in either compute_breakeven_
    update or the helper itself.
    """
    ticks = [
        {"current_return": 1.5, "symphony_vol": 1.0, "base_stop_level": -0.5, "is_triggered": False},
        {"current_return": 2.0, "symphony_vol": 1.0, "base_stop_level":  1.0, "is_triggered": False},
        {"current_return": 1.5, "symphony_vol": 1.0, "base_stop_level":  0.5, "is_triggered": False},
    ]
    a = _simulate_position_lifetime(ticks)
    b = _simulate_position_lifetime(ticks)
    assert a == b, (
        f"Non-deterministic multi-call replay: {a} vs {b}. The pure math "
        f"layer must produce bit-identical results for identical inputs."
    )
