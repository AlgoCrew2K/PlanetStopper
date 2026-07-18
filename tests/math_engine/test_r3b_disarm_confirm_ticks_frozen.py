"""
R3-b MA-4 AC-2 — DISARM_CONFIRM_TICKS is a named, FROZEN theory-set constant.

The recovery-disarm hysteresis ladder is gated by a new named constant
``math_engine.DISARM_CONFIRM_TICKS`` (project rule: no magic numbers). It must be
FROZEN — sized once via R3-a's mc_band_edge_stability probe (r3b-engine's
[PM-ASSUMED] call) and NEVER added to the Optuna search space (widening the
search mid-remediation is out of scope; mirrors the existing frozen tick-confirm
constants EXIT_CONFIRM_TICKS / TP_CONFIRM_TICKS).

This test deliberately does NOT pin the VALUE (that is r3b-engine's probe-sized
judgment call). It pins the CONTRACT: the constant exists, is a sane positive int,
and is frozen OUTSIDE ``autotuner.OPTUNA_SEARCH_SPACE_KEYS``.

RED on base: ``math_engine.DISARM_CONFIRM_TICKS`` does not exist yet (AttributeError).
"""

from __future__ import annotations

import autotuner
import math_engine


def test_disarm_confirm_ticks_constant_exists_and_is_positive_int() -> None:
    """The named hysteresis constant exists and is a sane positive int (>= 1).
    A zero/negative ladder would disarm on the first recovery tick — the very
    chatter the ladder exists to prevent. Value NOT pinned (probe-sized)."""
    ticks = math_engine.DISARM_CONFIRM_TICKS
    assert isinstance(ticks, int) and not isinstance(ticks, bool), (
        f"DISARM_CONFIRM_TICKS must be a plain int, got {type(ticks)}"
    )
    assert ticks >= 1, (
        f"DISARM_CONFIRM_TICKS must be >= 1 (a confirmation ladder cannot be "
        f"disabled); got {ticks!r}"
    )


def test_disarm_confirm_ticks_is_frozen_not_in_optuna_search_space() -> None:
    """AC-2 freeze discipline: DISARM_CONFIRM_TICKS must NOT be an Optuna-tuned
    key. It is a frozen theory-set dial, not a search-space parameter — do not
    widen the search mid-remediation (mirrors EXIT_CONFIRM_TICKS / TP_CONFIRM_TICKS,
    neither of which is in the search space)."""
    assert "DISARM_CONFIRM_TICKS" not in autotuner.OPTUNA_SEARCH_SPACE_KEYS, (
        "DISARM_CONFIRM_TICKS leaked into autotuner.OPTUNA_SEARCH_SPACE_KEYS — the "
        "recovery-disarm hysteresis constant is FROZEN, never Optuna-tuned."
    )


def test_disarm_confirm_ticks_is_the_seam_default() -> None:
    """The seam's ``disarm_confirm_ticks`` parameter defaults to the module-level
    DISARM_CONFIRM_TICKS, so a caller that does not pass it explicitly (production
    main() will not) gets the frozen theory-set value — one source of truth."""
    import inspect

    sig = inspect.signature(math_engine.compute_arm_disarm_decision)
    default = sig.parameters["disarm_confirm_ticks"].default
    assert default == math_engine.DISARM_CONFIRM_TICKS, (
        "compute_arm_disarm_decision.disarm_confirm_ticks must default to the "
        f"module constant DISARM_CONFIRM_TICKS ({math_engine.DISARM_CONFIRM_TICKS}); "
        f"got default {default!r}."
    )
