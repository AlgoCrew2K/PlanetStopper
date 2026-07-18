"""
R3-c / MA-11 AC-5 — THE BEHAVIORAL NON-VACUITY GOLDEN (end-to-end via the real
autotuner replay exit core).

This is the crux RED-on-old proof. It drives the REAL
``autotuner._replay_exit_tick`` (production-faithful exit core -- no mocking of
the math engine) over a squeezed noise-dip scenario and asserts the position
SURVIVES a noise dip once the floor is wired.

WHY IT FAILS BEHAVIORALLY (not via TypeError) AT HEAD
-----------------------------------------------------
``MAX_SQUEEZE_FLOOR`` is fed through the ``params`` dict, which the OLD
``_replay_exit_tick`` accepts (it simply never reads the key). So at HEAD the
call RUNS and the squeezed stop collapses to 0.075 pp -> the -0.05 noise dip
breaches it -> a premature 'Trailing Stop' exit fires -> the ``expect_exit ==
False`` assertion FAILS with a real exit signature. No TypeError artifact.

Once the tuner wires ``_replay_exit_tick`` to pass
``squeeze_floor=p.get("MAX_SQUEEZE_FLOOR", _replay_squeeze_floor_default())``,
the clamped stop distance is 0.15 pp, the -0.05 dip no longer breaches it, and
the position survives -> GREEN.

Fixture provenance: input tick sequences + params are hand-derived (schema =
synthetic_history tick schema); the test asserts the RELATIONAL exit outcome
(exit / no-exit + reason string), never a producer-computed stop value.

Tolerance: exit decision is a discrete categorical outcome -- exact.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import autotuner

_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "autotuner" / "squeeze_floor"


def _load(name: str) -> dict:
    with (_FIXTURE_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def _drive(fx: dict) -> tuple[int, str] | None:
    """Run the fixture's tick sequence through the real replay exit core.

    Returns (tick_idx, exit_reason) on the tick an exit fires, else None.
    """
    state = autotuner._fresh_replay_state()
    ticks = fx["ticks"]
    n = len(ticks)
    confirm = fx["exit_confirm_ticks"]
    params = fx["params"]
    for idx, tk in enumerate(ticks):
        reason = autotuner._replay_exit_tick(
            state, tk, idx, n, params, grace_minutes=0, exit_confirm_ticks=confirm
        )
        if reason:
            return (idx, reason)
    return None


def test_squeezed_noise_dip_survives_under_floored_stop() -> None:
    """AC-5: the noise dip must NOT exit once the squeeze floor is wired.

    RED-on-old at HEAD: the replay ignores the floor, the squeezed stop
    collapses to 0.075 pp, and the -0.05 dip fires a premature 'Trailing Stop'
    exit -> this assertion FAILS with that exit signature.
    """
    fx = _load("squeeze_floor_noise_dip_survives.json")
    assert fx["expect_exit"] is False  # fixture intent guard
    fired = _drive(fx)
    assert fired is None, (
        "PREMATURE EXIT: a squeezed noise dip that breaches only the UNCLAMPED "
        f"stop fired {fired[1]!r} at tick {fired[0]} -- the squeeze floor "
        "(MAX_SQUEEZE_FLOOR=0.20 in params) was NOT applied, so the collapsed "
        "0.075 pp stop sat on the high-water mark. With the floor wired the "
        "clamped 0.15 pp stop is not breached and the position must survive."
    )


def test_squeezed_breakdown_dip_still_exits() -> None:
    """AC-5 converse: a deeper dip that breaches even the CLAMPED floor still
    exits. The floor guards against noise, not a genuine breakdown. GREEN at
    HEAD and post-impl.
    """
    fx = _load("squeeze_floor_breakdown_dip_exits.json")
    assert fx["expect_exit"] is True  # fixture intent guard
    fired = _drive(fx)
    assert fired is not None, (
        "the deep breakdown dip must still fire an exit -- the floor raises the "
        "stop but must never suppress a genuine breakdown."
    )
    assert fired[1] == fx["expected_exit_reason"], (
        f"expected exit reason {fx['expected_exit_reason']!r}, got {fired[1]!r} at tick {fired[0]}."
    )
