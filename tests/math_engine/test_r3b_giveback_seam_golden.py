"""
R3-b MA-4 AC-4 — the GIVEBACK GOLDEN at the pure-seam DECISION level.

The audit's crux scenario (+3% -> -1.5% giveback): a position arms at a peak
(prob in [5, 15)), then prob climbs through 2x-trigger (30) into breakdown
(>= 60) as the position gives back through 0 into a loss. The extracted seam
``math_engine.compute_arm_disarm_decision`` MUST keep ``armed=True`` the whole way
so that ``compute_exit_confirmation`` downstream can fire the Trailing Stop.

Under the OLD inverted disarm the prob=35 tick (prob > 2x trigger AND return > 0)
stripped ``armed`` -> the position could never exit (re-arm needs prob back in
[5, 15), which the giveback never revisits). This test pins the DECISION half of
the fix; the end-to-end behavioral proof (that the OLD code never actually exits,
demonstrably RED on base) lives in
``tests/autotuner/test_r3b_giveback_exits_via_replay.py``.

RED on base: the seam does not exist yet (AttributeError).

TOLERANCE: armed state is a discrete categorical outcome — exact equality.
"""

from __future__ import annotations

import json
import pathlib

import math_engine

_FIXTURE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "fixtures"
    / "math_engine"
    / "arm_disarm_decision"
    / "giveback_trajectory_stays_armed.json"
)


def test_seam_keeps_armed_through_the_whole_giveback_trajectory() -> None:
    """AC-4 (decision level): across the arm -> deteriorate -> giveback-into-loss
    prob trajectory, the seam keeps armed=True every step after the initial arm.
    The OLD inverted disarm would have flipped armed=False on the prob=35 step."""
    with _FIXTURE.open("r", encoding="utf-8") as fh:
        fx = json.load(fh)
    c = fx["constants"]
    armed = fx["initial"]["armed"]
    count = fx["initial"]["disarm_confirm_count"]

    armed_trace = []
    for step in fx["sequence"]:
        armed, count = math_engine.compute_arm_disarm_decision(
            prob_underperforming=step["prob_underperforming"],
            is_triggered=step["is_triggered"],
            armed=armed,
            disarm_confirm_count=count,
            take_profit_mc_pct=c["take_profit_mc_pct"],
            trigger_threshold_pct=c["trigger_threshold_pct"],
            disarm_confirm_ticks=c["disarm_confirm_ticks"],
        )
        armed_trace.append(armed)
        assert armed == step["expect_armed"], (
            f"giveback step ({step['note']}): expected armed={step['expect_armed']}, "
            f"got {armed}. A disarm anywhere in the giveback re-creates the MA-4 bug "
            f"(the exit gate can never fire on an unarmed stop)."
        )

    # Once armed at the peak, the stop must remain armed for the ENTIRE remaining
    # deterioration -> the exit gate stays reachable. Not one False after the arm.
    assert armed_trace[0] is True, "must arm at the peak (prob in [5,15))"
    assert all(a is True for a in armed_trace[1:]), (
        f"the stop disarmed mid-giveback: armed trace = {armed_trace}. MA-4: the "
        f"protective stop must survive the whole giveback so the Trailing Stop can fire."
    )
