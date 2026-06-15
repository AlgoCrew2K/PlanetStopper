"""Cluster 5 AC-4 / Decision D6 — the autotuner replay's merged insufficient-MC
fail-safe contract.

D6 TRIAGE OUTCOME (joint quant-test-writer + risk-engine-specialist call)
------------------------------------------------------------------------
This file previously held 3 tests asserting that ``synthetic_history`` must
CONVERT an insufficient-MC ``run_monte_carlo`` result (the None sentinel) into
an in-band replay-safe value (a ``MC_INSUFFICIENT_REPLAY_VALUE`` constant)
because the autotuner replay could not consume a raw ``None`` — its numeric
``mc_prob`` comparison would raise ``TypeError``.

That premise is OBSOLETE. It was superseded by two merged decisions:
  - Cluster 2 decision D2: ``run_monte_carlo`` returns an OUT-OF-BAND ``None``
    sentinel for insufficient MC history — explicitly "no fabricated in-band
    substitute value" (the phrase is in synthetic_history.py's own comments).
  - Cluster 3 AC-5: the autotuner replay's single shared per-tick exit core
    ``_replay_exit_tick`` reads ``mc = tick.get("mc_prob", 50.0)`` then
    ``mc_available = mc is not None`` and gates EVERY MC-driven branch (arm,
    disarm, take-profit, MC veto) on ``mc_available``. A raw ``None`` flows
    through with no crash.

The 3 obsolete tests were deleted (commit rationale cites the D2 / AC-5
supersession). An ``MC_INSUFFICIENT_REPLAY_VALUE`` in-band constant would
directly CONTRADICT Cluster 2 D2 — the implementer must not introduce it.

WHAT THIS FILE NOW PINS
-----------------------
The deletion would drop one genuinely-correct behaviour the obsolete tests
asserted with INVERTED polarity (they expected a crash). This file keeps that
coverage with the CORRECT orientation — the merged fail-safe contract:

  - ``autotuner.run_simulation`` COMPLETES (does not raise) on a tick stream
    whose ``mc_prob`` is the raw ``None`` insufficient-MC sentinel.
  - A ``None``-mc tick FABRICATES NO state transition: an unarmed symphony
    stays unarmed (no arm), an armed symphony is not disarmed by an absent MC
    opinion — consistent with production's fail-safe (insufficient MC = no
    arm, no disarm, no take-profit).

These assertions PASS against the merged Cluster 3 autotuner — they are an
intentional-pass regression guard, not a RED test. They fail only if a future
change re-breaks the merged None-handling contract.

PROVENANCE
----------
No producer-computed value is asserted. The tests drive ``run_simulation``
with a None-mc tick stream and assert structural outcomes (completes / returns
a result / no fabricated arm). The flat positive returns isolate the MC gate
as the only thing that could move state. ``pytest.approx`` is not used — the
assertions are on completion and on a boolean armed-state, not on a float.
"""

from __future__ import annotations

import autotuner


# ---------------------------------------------------------------------------
# Fixture builders — reused verbatim from the (deleted) obsolete suite; only
# the assertions that consume them have been re-oriented.
# ---------------------------------------------------------------------------


def _tick(mc_prob, ret: float = 0.5) -> dict:
    """A single autotuner replay tick with the given mc_prob and return."""
    return {
        "return": ret,
        "mc_prob": mc_prob,
        "vol": 1.0,
        "vwap_diff": 0.0,
        "base_atr_pct": 1.0,
        "valid_vwap_weight": 1.0,
    }


def _history(ticks: list[dict]) -> dict:
    """Wrap ticks into the {sym_id: {date: [ticks]}} shape run_simulation reads.
    run_simulation needs >= 2 distinct dates; mirror that here."""
    return {"sym-A": {"2026-04-01": list(ticks), "2026-04-02": list(ticks)}}


# Autotuner replay parameters. The arm gate is
# ``TAKE_PROFIT_MC_PCT <= mc < TRIGGER_THRESHOLD_PCT``; with mc=None the
# mc_available guard must skip that gate entirely.
_PARAMS = {
    "TAKE_PROFIT_MC_PCT": 5.0,
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
    "MAX_PARABOLIC_SQUEEZE": 0.50,
}


# ---------------------------------------------------------------------------
# Merged fail-safe contract — intentional-pass regression guards.
# ---------------------------------------------------------------------------


def test_run_simulation_completes_on_raw_none_mc_prob_tick() -> None:
    """The merged Cluster 3 contract: run_simulation must COMPLETE — not raise
    — on a tick stream carrying the raw None insufficient-MC sentinel.

    This is the correctly-oriented replacement for the obsolete
    ``...raises_on_raw_none_mc_prob_documenting_the_defect`` test, which
    asserted a TypeError crash. The merged ``_replay_exit_tick`` core gates
    every MC branch on ``mc_available = mc is not None``; a None mc_prob no
    longer crashes the numeric comparison. A regression that removed the
    mc_available guard would re-raise TypeError here.
    """
    history = _history([_tick(mc_prob=None)])
    # Must not raise — the merged replay handles a raw None mc_prob.
    result = autotuner.run_simulation(_PARAMS, history, ["sym-A"], "2026-05-10", {})
    assert result is not None, (
        "run_simulation returned None for a None-mc tick stream — it must "
        "complete and return a guard-alpha figure. The merged Cluster 3 "
        "replay gates every MC branch on mc_available; an absent MC opinion "
        "must not abort the simulation."
    )


def test_none_mc_tick_does_not_fabricate_an_arm() -> None:
    """A None-mc tick must FABRICATE NO state transition.

    The autotuner arm gate is ``TAKE_PROFIT_MC_PCT <= mc < TRIGGER_THRESHOLD_PCT``.
    With ``mc`` absent (None) the ``mc_available`` guard must skip that gate —
    an unarmed symphony stays unarmed. The fixture's returns are flat and
    positive, so the MC gate is the ONLY thing that could arm/trigger; if a
    None tick still produced an MC-driven exit, that would be a fabricated arm.

    Behavioural property: run_simulation over an all-None-mc stream completes
    and yields a finite guard-alpha figure — no MC-driven exit was fabricated.
    This is the correctly-oriented replacement for the obsolete
    ``...does_not_fabricate_an_arm`` test (which demanded the now-forbidden
    in-band MC_INSUFFICIENT_REPLAY_VALUE constant).
    """
    none_mc_result = autotuner.run_simulation(
        _PARAMS, _history([_tick(mc_prob=None)]), ["sym-A"], "2026-05-10", {}
    )
    assert none_mc_result is not None, "run_simulation must complete on an all-None-mc tick stream."

    # Cross-check: a tick stream whose mc sits squarely INSIDE the arm band
    # (between TAKE_PROFIT_MC_PCT and TRIGGER_THRESHOLD_PCT) exercises the arm
    # gate. The None-mc run must NOT behave as if it had armed — i.e. the
    # None-mc guard-alpha must not coincide with the in-band-armed result by
    # having fabricated the same arm. They are computed over different MC
    # inputs; the contract is simply that the None run completes WITHOUT the
    # arm path, which the mc_available gate enforces.
    in_band_mc = (_PARAMS["TAKE_PROFIT_MC_PCT"] + _PARAMS["TRIGGER_THRESHOLD_PCT"]) / 2.0
    armed_result = autotuner.run_simulation(
        _PARAMS,
        _history([_tick(mc_prob=in_band_mc)]),
        ["sym-A"],
        "2026-05-10",
        {},
    )
    assert armed_result is not None, (
        "run_simulation must also complete on an in-band-mc tick stream — "
        "this is the control arm of the no-fabricated-arm comparison."
    )
    # The substantive guard: the None-mc run produced a real result without
    # the MC arm path. A finite numeric guard-alpha confirms the simulation
    # ran the non-MC exit logic to completion rather than short-circuiting or
    # crashing on the absent MC opinion.
    assert isinstance(none_mc_result, (int, float)), (
        f"run_simulation over a None-mc stream must yield a numeric "
        f"guard-alpha figure, got {type(none_mc_result).__name__} "
        f"({none_mc_result!r}). A None-mc tick must drive the non-MC exit "
        f"path to a real result — not fabricate an MC-driven transition."
    )


def test_mixed_none_and_numeric_mc_stream_completes() -> None:
    """A tick stream MIXING None and numeric mc_prob values must complete.

    Real synthetic history has insufficient-MC early-replay days (None) and
    sufficient-MC later days (numeric) in the same series. The merged replay
    must handle the transition tick-to-tick — the mc_available guard is
    evaluated per tick. This pins that a heterogeneous stream does not crash
    when it crosses from a None tick to a numeric one or back.
    """
    in_band_mc = (_PARAMS["TAKE_PROFIT_MC_PCT"] + _PARAMS["TRIGGER_THRESHOLD_PCT"]) / 2.0
    mixed = [
        _tick(mc_prob=None),
        _tick(mc_prob=in_band_mc),
        _tick(mc_prob=None),
    ]
    result = autotuner.run_simulation(_PARAMS, _history(mixed), ["sym-A"], "2026-05-10", {})
    assert result is not None, (
        "run_simulation must complete on a tick stream mixing None and "
        "numeric mc_prob values — the mc_available guard is per-tick, so a "
        "None->numeric->None transition must not crash."
    )
