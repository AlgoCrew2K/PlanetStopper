"""
R3-b MA-4 — input-guard regression locks for math_engine.compute_arm_disarm_decision.

SUFFICIENCY-REVIEW additions (test-writer): the GREEN seam added two defensive
guards BEYOND the AC-1..8 behavioral surface, and a worse implementation that
DROPPED either would still pass the entire behavioral battery (which never feeds
0/NaN). These lock them adversarially:

- ``disarm_confirm_ticks <= 0`` would disable the hysteresis ladder entirely
  (disarm on the FIRST recovery tick) — the exact chatter AC-2 exists to prevent.
  Reject-don't-coerce, mirroring compute_exit_confirmation's identical guard.
- A NaN / +-Inf ``prob_underperforming`` or arm-band threshold would silently
  corrupt the arm/disarm decision (a NaN comparison short-circuits to False),
  a fail-dangerous path on a live-money exit surface — mirrors the module-wide
  ``_reject_non_finite`` policy applied to compute_exit_confirmation /
  compute_tp_confirmation.

These pass against the current (correct) seam and FAIL against a regression that
removes the guards — the adversarial point of a sufficiency review. Validation
happens at function entry (before the is_triggered / arm / disarm logic), so the
armed/prob context below is arbitrary-but-valid.
"""

from __future__ import annotations

import pytest

import math_engine


def _call(**overrides):
    """Invoke the seam with valid defaults, overridden per-test."""
    kwargs = dict(
        prob_underperforming=10.0,
        is_triggered=False,
        armed=True,
        disarm_confirm_count=0,
        take_profit_mc_pct=5.0,
        trigger_threshold_pct=15.0,
    )
    kwargs.update(overrides)
    return math_engine.compute_arm_disarm_decision(**kwargs)


@pytest.mark.parametrize("bad_ticks", [0, -1, -3])
def test_non_positive_disarm_confirm_ticks_rejected(bad_ticks: int) -> None:
    """A zero/negative confirmation ladder would disarm on the first recovery tick
    — no hysteresis at all (the chatter AC-2 prevents). The seam must reject it,
    not coerce it. RED against a regression that drops the guard."""
    with pytest.raises(ValueError):
        _call(disarm_confirm_ticks=bad_ticks)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_prob_underperforming_rejected(bad: float) -> None:
    """A non-finite prob_underperforming must be rejected — a NaN comparison
    short-circuits to False and could silently mis-arm/disarm the protective stop
    on a live-money path."""
    with pytest.raises(ValueError):
        _call(prob_underperforming=bad)


@pytest.mark.parametrize("field", ["take_profit_mc_pct", "trigger_threshold_pct"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_arm_band_threshold_rejected(field: str, bad: float) -> None:
    """A non-finite arm-band threshold must be rejected (same fail-safe as prob)."""
    with pytest.raises(ValueError):
        _call(**{field: bad})


def test_valid_finite_inputs_do_not_raise() -> None:
    """Control: the guards do NOT fire on ordinary finite inputs — the rejection
    is scoped to non-finite / non-positive-ladder, not a blanket raise."""
    new_armed, new_count = _call(prob_underperforming=3.0, disarm_confirm_ticks=3)
    assert isinstance(new_armed, bool) and isinstance(new_count, int)
