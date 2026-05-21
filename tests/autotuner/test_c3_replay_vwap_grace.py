"""
Cluster 3 AC-2 — the autotuner replay must suppress BOTH VWAP signals during
the open-window grace period, the faithful equivalent of production's
`is_in_open_window_grace` gate.

Production (alpha_bot_execution.py:1321-1326) zeroes is_vwap_broken AND
is_vwap_bleed_broken for the first VWAP_OPEN_WINDOW_GRACE_MINUTES of the
session. The replay computes the VWAP flags but never suppresses them, so it
fires phantom VWAP Breakdown / Bleed exits in the early session on days
production would not exit. VWAP Breakdown is the highest-priority trigger, so
any early-session VWAP dip in synthetic data produces a phantom exit.

The replay ticks carry a `tick_idx` but no datetime; the faithful equivalent
of the datetime-based grace gate is `tick_idx < VWAP_OPEN_WINDOW_GRACE_
MINUTES` (minute-bar ticks, tick 0 == session open).

RED EXPECTATION: against pre-fix autotuner.py the replay fires a VWAP exit
inside the grace window — `test_replay_suppresses_vwap_exit_in_grace_window`
sees an exit where production sees none.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

import autotuner


_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "autotuner"
    / "replay_parity"
)
_AUTOTUNER_TREE = ast.parse(
    pathlib.Path(autotuner.__file__).read_text(encoding="utf-8")
)


def _load_fixture(name: str) -> dict:
    with (_FIXTURE_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def _default_params() -> dict:
    return {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 3,
        "PARABOLIC_VELOCITY_THRESHOLD": 99.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


def _replay_seq(ticks: list[dict], params: dict, grace_minutes: int) -> list[dict]:
    if not hasattr(autotuner, "replay_exit_sequence"):
        pytest.fail(
            "autotuner.replay_exit_sequence missing — required for AC-2/AC-6 "
            "exit-decision parity tests."
        )
    return autotuner.replay_exit_sequence(
        ticks, params, grace_minutes=grace_minutes
    )


# ===========================================================================
# AC-2 — behavioural: no VWAP exit inside the grace window.
# ===========================================================================


def test_replay_suppresses_vwap_breakdown_exit_in_grace_window() -> None:
    """AC-2: a VWAP-breakdown dip that occurs entirely within the open-window
    grace period must NOT produce a replay exit.

    Fixture parity_vwap_grace_no_phantom.json supplies an early-session VWAP
    breakdown (a sustained negative vwap_diff with the return below the HWM)
    whose confirming ticks all fall inside grace_minutes. With the grace gate
    the replay produces no exit.

    RED: pre-fix the replay never suppresses VWAP signals — it fires a phantom
    VWAP Breakdown inside the grace window.
    """
    fx = _load_fixture("parity_vwap_grace_no_phantom.json")
    ticks = fx["ticks"]
    params = fx.get("params") or _default_params()
    grace = fx["grace_minutes"]

    seq = _replay_seq(ticks, params, grace)
    exits = [(d["tick_idx"], d["exit_reason"]) for d in seq if d["exit_reason"]]

    early_exits = [(idx, reason) for idx, reason in exits if idx < grace]
    assert not early_exits, (
        f"Replay fired exit(s) {early_exits} inside the open-window grace "
        f"period (tick_idx < {grace}). Production suppresses BOTH VWAP "
        f"signals there; the replay must zero is_vwap_broken and "
        f"is_vwap_bleed_broken for tick_idx < VWAP_OPEN_WINDOW_GRACE_MINUTES."
    )


def test_replay_allows_vwap_exit_after_grace_window() -> None:
    """AC-2 (the gate is a SUPPRESSION, not a disable): a VWAP breakdown whose
    confirming ticks fall AFTER the grace window must still produce a replay
    exit. The grace gate must not permanently disable the VWAP system.

    Reuses the same fixture but with grace_minutes=0 (no grace): the VWAP
    breakdown that was suppressed above must now fire.

    RED-or-GREEN: this test guards against an over-broad fix that suppresses
    VWAP for the whole session. It must pass once the gate is correct.
    """
    fx = _load_fixture("parity_vwap_grace_no_phantom.json")
    ticks = fx["ticks"]
    params = fx.get("params") or _default_params()

    seq = _replay_seq(ticks, params, 0)
    exits = [d for d in seq if d["exit_reason"]]
    assert exits, (
        "With grace_minutes=0 the VWAP breakdown in "
        "parity_vwap_grace_no_phantom.json must produce a replay exit. The "
        "grace gate is a time-bounded suppression — it must not disable the "
        "VWAP exit system outright."
    )
    assert "VWAP" in exits[0]["exit_reason"], (
        f"Expected a VWAP-family exit with no grace; got "
        f"{exits[0]['exit_reason']!r}."
    )


def test_replay_grace_suppresses_both_vwap_signals() -> None:
    """AC-2: the grace gate must zero BOTH is_vwap_broken AND
    is_vwap_bleed_broken — production suppresses both. A fix that only
    suppresses the breakdown signal would still let an early-session bleed
    cut fire a phantom exit.

    Drives a deep, sustained bleed (return far below the bleed-arm threshold)
    starting at tick 0; the bleed confirms within the grace window. With the
    gate, no exit; the bleed-cut must be suppressed alongside the breakdown.

    RED: pre-fix the replay fires a VWAP Bleed Cut inside grace.
    """
    params = _default_params()
    params["VWAP_BLEED_TICKS"] = 3   # bleed confirms after 3 ticks
    grace_minutes = 15

    # Sustained deep bleed from tick 0: return far negative so it is at/below
    # any bleed-arm threshold; vwap_diff negative + weight high so the gate
    # passes; HWM low so System A (breakdown) does not also fire — isolating
    # the bleed signal.
    bleed_tick = {
        "time": "09:30", "return": -8.0, "mc_prob": 50.0, "vol": 0.5,
        "vwap_diff": -3.0, "base_atr_pct": 0.5, "valid_vwap_weight": 1.0,
    }
    ticks = [dict(bleed_tick, time=f"09:{30 + i:02d}") for i in range(10)]

    seq = _replay_seq(ticks, params, grace_minutes)
    early_exits = [
        (d["tick_idx"], d["exit_reason"])
        for d in seq
        if d["exit_reason"] and d["tick_idx"] < grace_minutes
    ]
    assert not early_exits, (
        f"Replay fired {early_exits} inside grace — a VWAP Bleed Cut leaked "
        f"through. The grace gate must suppress is_vwap_bleed_broken as well "
        f"as is_vwap_broken, exactly as production does."
    )


# ===========================================================================
# AC-2 — structural: the grace constant is shared with production, not a
# bare literal.
# ===========================================================================


def test_replay_grace_threshold_is_named_constant_not_literal() -> None:
    """AC-2: the replay's grace-window threshold must be a named constant
    (the shared VWAP_OPEN_WINDOW_GRACE_MINUTES, or an autotuner constant
    that references it) — not a bare `15` inline.

    Asserts autotuner.py references an identifier whose name contains
    'GRACE'. A purely literal `tick_idx < 15` fails — the production grace
    minute count is a tuned dial and must not be silently duplicated.

    RED: pre-fix autotuner.py has no grace logic at all, so no GRACE
    identifier exists.
    """
    grace_names = {
        node.id
        for node in ast.walk(_AUTOTUNER_TREE)
        if isinstance(node, ast.Name) and "GRACE" in node.id.upper()
    }
    grace_attrs = {
        node.attr
        for node in ast.walk(_AUTOTUNER_TREE)
        if isinstance(node, ast.Attribute) and "GRACE" in node.attr.upper()
    }
    assert grace_names or grace_attrs, (
        "autotuner.py references no GRACE-named identifier. The replay's "
        "open-window grace threshold must be a named constant shared with "
        "production's VWAP_OPEN_WINDOW_GRACE_MINUTES — not a bare literal."
    )
