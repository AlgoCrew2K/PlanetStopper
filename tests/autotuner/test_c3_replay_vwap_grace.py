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


_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "autotuner" / "replay_parity"
_AUTOTUNER_TREE = ast.parse(pathlib.Path(autotuner.__file__).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _pin_execution_start_time_to_session_open(monkeypatch):
    """Pin alpha_bot_execution.EXECUTION_START_TIME = "09:30" for every
    test in this file.

    Cluster 7 / AC-5 fixed the replay's grace gate to read
    EXECUTION_START_TIME from env (the bug it corrected: the replay
    hardcoded a "09:30" anchor while production correctly anchored at
    EXECUTION_START_TIME). With that fix landed, this file's fixtures
    — which were authored under the legacy semantic "tick_idx 0 ==
    09:30 ET session open" — would otherwise see the replay's grace
    window shift to tick_idx 60..74 when the operator's env carries
    EXECUTION_START_TIME="10:31" (the default-changed-by-operator case).

    Pinning EXECUTION_START_TIME to "09:30" for the test's duration
    keeps the fixture's baked-in tick-anchoring honest AND tests the
    replay's grace semantic at the env-default anchor. The AC-5 RED
    suite (tests/autotuner/test_n3_replay_grace_execution_start_time.py)
    independently exercises the NON-default-EXECUTION_START_TIME case.
    """
    monkeypatch.setattr("alpha_bot_execution.EXECUTION_START_TIME", "09:30", raising=False)


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
    return autotuner.replay_exit_sequence(ticks, params, grace_minutes=grace_minutes)


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
        f"Expected a VWAP-family exit with no grace; got {exits[0]['exit_reason']!r}."
    )


def test_replay_grace_suppresses_flags_not_counters() -> None:
    """AC-2 (counter-vs-flag — the subtle correctness trap): the grace gate
    must suppress the OUTPUT FLAGS (is_vwap_broken / is_vwap_bleed_broken),
    NOT the VWAP tick COUNTERS.

    Production zeroes the flags AFTER compute_vwap_breakdown_update has run
    and its returned new_vwap_ticks counter has been assigned
    (alpha_bot_execution.py:1318-1326) — so the counters keep advancing
    through the grace window. A wrong fix that resets the counters during
    grace diverges from production on the first post-grace tick.

    Construction: grace_minutes = 3, VWAP_BREAK_CONFIRM_TICKS = 3. A VWAP
    breakdown holds on EVERY tick from tick 0:
      tick 0 (grace): vwap_ticks -> 1, flag suppressed
      tick 1 (grace): vwap_ticks -> 2, flag suppressed
      tick 2 (grace): vwap_ticks -> 3, is_vwap_broken True but SUPPRESSED
      tick 3 (post):  vwap_ticks -> 4, is_vwap_broken True -> EXIT on tick 3
    Correct (flag-suppression) GREEN exits on EXACTLY tick 3 — the first
    post-grace tick — because the counter advanced through grace.
    A wrong (counter-reset) GREEN keeps the counter at 0 through ticks 0-2,
    so tick 3 only reaches count 1 and the exit slips to tick 5.

    RED: pre-fix the replay has no grace gate at all — it exits on tick 2
    (inside grace), failing the 'exit on exactly tick 3' assertion.
    """
    import math_engine

    grace_minutes = 3
    confirm_ticks = math_engine.VWAP_BREAK_CONFIRM_TICKS  # 3
    params = _default_params()
    params["VWAP_BLEED_TICKS"] = 999  # bleed never confirms — isolate System A

    # Every tick triggers System A: safe_hwm >= VWAP_CROSS_HWM_PCT (1.0) and
    # current_return < safe_hwm, with negative vwap_diff + full weight so the
    # VWAP gate passes. The HWM is set on tick 0 and the return stays below it.
    ticks = [
        {
            "time": "09:30",
            "return": 5.0,
            "mc_prob": 50.0,
            "vol": 0.5,
            "vwap_diff": -2.0,
            "base_atr_pct": 0.5,
            "valid_vwap_weight": 1.0,
        },
    ]
    ticks += [
        {
            "time": f"09:{31 + i:02d}",
            "return": 2.0,
            "mc_prob": 50.0,
            "vol": 0.5,
            "vwap_diff": -2.0,
            "base_atr_pct": 0.5,
            "valid_vwap_weight": 1.0,
        }
        for i in range(9)
    ]

    seq = _replay_seq(ticks, params, grace_minutes)
    exits = [(d["tick_idx"], d["exit_reason"]) for d in seq if d["exit_reason"]]

    assert exits, (
        "A VWAP breakdown holding every tick must eventually exit once grace "
        "ends — the grace gate suppresses, it does not disable."
    )
    exit_idx, exit_reason = exits[0]
    assert exit_idx == grace_minutes, (
        f"VWAP exit fired on tick {exit_idx}; expected exactly tick "
        f"{grace_minutes} (the first post-grace tick). The grace gate must "
        f"suppress the OUTPUT FLAG and let the vwap_ticks COUNTER advance "
        f"through grace — so the break, already confirmed (counter >= "
        f"{confirm_ticks}) by the end of grace, fires on the very next tick. "
        f"An exit later than tick {grace_minutes} means the fix wrongly "
        f"reset the counter during grace instead of just the flag; an exit "
        f"earlier means the grace suppression is missing."
    )
    assert "VWAP" in exit_reason, f"Expected a VWAP-family exit; got {exit_reason!r}."


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
    params["VWAP_BLEED_TICKS"] = 3  # bleed confirms after 3 ticks
    grace_minutes = 15

    # Sustained deep bleed from tick 0: return far negative so it is at/below
    # any bleed-arm threshold; vwap_diff negative + weight high so the gate
    # passes; HWM low so System A (breakdown) does not also fire — isolating
    # the bleed signal.
    bleed_tick = {
        "time": "09:30",
        "return": -8.0,
        "mc_prob": 50.0,
        "vol": 0.5,
        "vwap_diff": -3.0,
        "base_atr_pct": 0.5,
        "valid_vwap_weight": 1.0,
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


def _rhs_references_production_grace(value: ast.AST) -> bool:
    """True iff an assignment RHS obtains the grace value by REFERENCING
    alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_MINUTES (the shared source of
    truth) rather than re-deriving it.

    Accepted: an Attribute access whose attr is VWAP_OPEN_WINDOW_GRACE_MINUTES
    on an `alpha_bot_execution` value (alpha_bot_execution.VWAP_... — possibly
    through an alias), or a bare Name of that constant imported `from
    alpha_bot_execution import VWAP_OPEN_WINDOW_GRACE_MINUTES`.
    Rejected: a Constant, an os.getenv(...) Call, arithmetic — any RHS that
    re-derives the dial instead of referencing production's.
    """
    if isinstance(value, ast.Attribute):
        return value.attr == "VWAP_OPEN_WINDOW_GRACE_MINUTES"
    if isinstance(value, ast.Name):
        return value.id == "VWAP_OPEN_WINDOW_GRACE_MINUTES"
    return False


def test_replay_grace_constant_is_shared_with_production_not_redefined() -> None:
    """AC-2 / quant-code-reviewer BLOCK (Gate 6): the replay must SHARE
    production's VWAP_OPEN_WINDOW_GRACE_MINUTES — it must NOT re-derive its
    own copy. A second derivation (a literal, OR an independent
    os.getenv(..., "15") parse with its own default) is a duplicated tuned
    dial: production and the replay agree today only by coincidence of the
    same default, and drift the moment one is re-tuned.

    autotuner.py must obtain the grace value by REFERENCING
    alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_MINUTES — via a function-local
    / deferred import (top-level import is out: alpha_bot_execution imports
    autotuner, a circular import — verified) or a lazy accessor. Never a
    fresh `VWAP_OPEN_WINDOW_GRACE_MINUTES = int(os.getenv(...,"15"))` of its
    own.

    Asserts: (a) every autotuner.py assignment binding a VWAP_OPEN_WINDOW_
    GRACE_MINUTES name has an RHS that REFERENCES production's constant — a
    Constant RHS, an os.getenv Call RHS, or any other re-derivation fails;
    and (b) the runtime grace value the replay uses equals
    alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_MINUTES.

    RED: autotuner.py:19 currently does
    `VWAP_OPEN_WINDOW_GRACE_MINUTES = int(os.getenv("VWAP_OPEN_WINDOW_GRACE_MINUTES", "15"))`
    — an independent os.getenv re-derivation duplicating production's dial.
    """
    import alpha_bot_execution

    production_grace = alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_MINUTES

    # (a) Every autotuner-local binding of the grace name must REFERENCE
    # production's constant — not re-derive it (literal / os.getenv / arith).
    redefinitions: list[str] = []
    for node in ast.walk(_AUTOTUNER_TREE):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and "VWAP_OPEN_WINDOW_GRACE" in tgt.id:
                    if not _rhs_references_production_grace(node.value):
                        kind = type(node.value).__name__
                        redefinitions.append(
                            f"line {node.lineno}: {tgt.id} = <{kind}> "
                            f"(re-derives the dial; must reference "
                            f"alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_MINUTES)"
                        )
    assert not redefinitions, (
        f"autotuner.py re-derives the grace constant at {redefinitions}. It "
        f"must SHARE production's alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_"
        f"MINUTES by referencing it (function-local import or lazy accessor "
        f"— top-level import is circular), NOT re-parse the env var or "
        f"hardcode a literal. An independent derivation is a duplicated "
        f"tuned dial that drifts on the next re-tune."
    )

    # (b) The replay's runtime grace value must equal production's. autotuner
    # may not have a module-level constant at all under the shared-reference
    # fix — it may use a function-local import or lazy accessor. So part (b)
    # only requires production's constant to exist and be a non-negative int;
    # the BINDING (the replay actually tracks it) is proven behaviourally by
    # test_replay_grace_gate_tracks_retuned_production_constant below, which
    # monkeypatches production's value and asserts the replay's gate shifts.
    assert isinstance(production_grace, int) and production_grace >= 0, (
        f"alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_MINUTES must be a "
        f"non-negative int; got {production_grace!r}."
    )


def test_replay_grace_gate_tracks_retuned_production_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-2 / quant-code-reviewer BLOCK (Gate 6 — the anti-drift behavioural
    proof): when production's VWAP_OPEN_WINDOW_GRACE_MINUTES is re-tuned, the
    autotuner replay's grace gate MUST shift with it — proving the replay
    references production's constant rather than carrying an independent
    duplicate.

    Construct a VWAP breakdown that confirms early in the session. Run the
    replay twice:
      * production grace = 0  -> the breakdown is NOT suppressed -> exit.
      * production grace re-tuned high (past the breakdown's confirm tick)
        -> the breakdown IS suppressed -> no early exit.
    The replay's exit decision must flip between the two. A duplicated
    autotuner-local dial would NOT shift when only production's value is
    monkeypatched — that GREEN goes RED here.

    This drives the replay's internal grace path (run_simulation /
    _collect_sim_returns, which read production's constant), NOT
    replay_exit_sequence (which takes grace as an explicit parameter and so
    cannot detect the duplication).
    """
    import alpha_bot_execution

    params = _default_params()
    params["VWAP_BLEED_TICKS"] = 999  # isolate System A (breakdown)

    # A VWAP breakdown holding every tick from tick 0: tick 0 banks the HWM,
    # subsequent ticks sit below it with negative vwap_diff + full weight.
    ticks = [
        {
            "time": "09:30",
            "return": 5.0,
            "mc_prob": 50.0,
            "vol": 0.5,
            "vwap_diff": -2.0,
            "base_atr_pct": 0.5,
            "valid_vwap_weight": 1.0,
        },
    ]
    ticks += [
        {
            "time": f"09:{31 + i:02d}",
            "return": 2.0,
            "mc_prob": 50.0,
            "vol": 0.5,
            "vwap_diff": -2.0,
            "base_atr_pct": 0.5,
            "valid_vwap_weight": 1.0,
        }
        for i in range(20)
    ]
    history = {"sym-A": {"2026-04-06": ticks}}

    def _run_sim_exits() -> bool:
        """True iff run_simulation's replay fires an exit on this day."""
        # run_simulation returns -total_guard_alpha; a day that triggers an
        # exit contributes a non-zero term, a no-exit day contributes 0.0.
        return autotuner.run_simulation(params, history, ["sym-A"], "2026-05-10", {}) != 0.0

    # Production grace = 0: the early VWAP breakdown is NOT suppressed.
    monkeypatch.setattr(alpha_bot_execution, "VWAP_OPEN_WINDOW_GRACE_MINUTES", 0)
    exits_grace_off = _run_sim_exits()

    # Production grace re-tuned to 999: the entire session is inside grace,
    # the VWAP breakdown is suppressed for every tick.
    monkeypatch.setattr(alpha_bot_execution, "VWAP_OPEN_WINDOW_GRACE_MINUTES", 999)
    exits_grace_max = _run_sim_exits()

    assert exits_grace_off, (
        "With production VWAP_OPEN_WINDOW_GRACE_MINUTES = 0 the early VWAP "
        "breakdown must NOT be suppressed — run_simulation's replay must "
        "fire an exit."
    )
    assert not exits_grace_max, (
        "With production VWAP_OPEN_WINDOW_GRACE_MINUTES re-tuned to 999 the "
        "whole session is inside grace — the replay must suppress the VWAP "
        "exit. The replay's grace gate did NOT shift when production's "
        "constant was re-tuned: autotuner is reading a DUPLICATED grace dial "
        "instead of referencing alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_"
        "MINUTES."
    )
