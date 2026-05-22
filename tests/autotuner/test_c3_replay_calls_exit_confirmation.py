"""
Cluster 3 AC-1 — the autotuner replay must call
`math_engine.compute_exit_confirmation` for the trailing-stop exit; the
open-coded exit block and its duplicated literals must be gone.

The replay currently re-implements the trailing-stop exit inline:

    is_trailing_hit = False
    if armed:
        if ret <= (stop_level - 0.10) and mc < 60.0:
            below_stop_count += 1
            if below_stop_count >= 3: is_trailing_hit = True
        else: below_stop_count = 0

This open-codes three values that math_engine already names —
MAGNITUDE_FLOOR_PCT (0.10), MC_SANITY_THRESHOLD (60.0) and
EXIT_CONFIRM_TICKS (3). If a production constant is re-tuned the replay
silently keeps the stale copy and optimizes the wrong exit rule.

This file is split into:
  * STRUCTURAL tests — AST/source assertions that the open-coded block is
    gone and compute_exit_confirmation is called in BOTH replay functions.
  * BEHAVIOURAL tests — the replay must delegate to the real primitive so
    re-tuning a constant flows through automatically.

RED EXPECTATION: against pre-fix autotuner.py every test here fails — the
inline block is present in `_collect_sim_returns` (~line 368) and
`run_simulation` (~line 509), and neither calls compute_exit_confirmation.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import autotuner
import math_engine


_AUTOTUNER_PATH = pathlib.Path(autotuner.__file__)
_AUTOTUNER_SRC = _AUTOTUNER_PATH.read_text(encoding="utf-8")
_AUTOTUNER_TREE = ast.parse(_AUTOTUNER_SRC)


def _function_node(name: str) -> ast.FunctionDef:
    for node in ast.walk(_AUTOTUNER_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found in autotuner.py")


def _calls_in(node: ast.AST, attr: str) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Attribute) and f.attr == attr) or (
                isinstance(f, ast.Name) and f.id == attr
            ):
                calls.append(n)
    return calls


def _numeric_constants_in(node: ast.AST) -> list[float]:
    """All numeric literal constants appearing in an AST subtree."""
    out: list[float] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) \
                and not isinstance(n.value, bool):
            out.append(float(n.value))
    return out


# Replay machinery: the two top-level replay functions plus any shared
# per-tick exit core they delegate to. A shared-core refactor (run_simulation
# / _collect_sim_returns / replay_exit_sequence all calling one
# _replay_exit_tick) is the PREFERRED architecture — these AC-1 tests must
# accommodate it, not forbid it. They therefore walk the whole autotuner
# module / the union of replay-machinery functions, not a single FunctionDef.
_REPLAY_MACHINERY_NAMES = (
    "_collect_sim_returns",
    "run_simulation",
    "replay_exit_sequence",
    "_replay_exit_tick",
    "_replay_tick",
    "_simulate_exit_tick",
)


def _replay_machinery_nodes() -> list[ast.FunctionDef]:
    """The replay-machinery FunctionDefs that exist in autotuner.py."""
    found: list[ast.FunctionDef] = []
    for node in ast.walk(_AUTOTUNER_TREE):
        if isinstance(node, ast.FunctionDef) and node.name in _REPLAY_MACHINERY_NAMES:
            found.append(node)
    return found


# ===========================================================================
# STRUCTURAL — compute_exit_confirmation is the replay's trailing-stop exit.
#
# These tests are architecture-agnostic: the replay may keep one per-tick
# exit core shared by run_simulation / _collect_sim_returns / replay_exit_
# sequence (the PREFERRED single-source-of-truth design) or inline it — but
# either way compute_exit_confirmation must own the trailing-stop decision
# and no open-coded exit literal may survive anywhere in autotuner.py.
# ===========================================================================


def test_autotuner_replay_calls_compute_exit_confirmation() -> None:
    """AC-1: the autotuner replay must call
    math_engine.compute_exit_confirmation for the trailing-stop decision.

    Walks the whole autotuner module: the call must appear in the replay
    machinery (the two replay functions or a shared per-tick exit core they
    delegate to). This accommodates the preferred shared-core refactor — the
    call lives ONCE, not duplicated per function.

    RED: pre-fix autotuner.py never calls compute_exit_confirmation — the
    exit is open-coded in both replay functions.
    """
    calls = _calls_in(_AUTOTUNER_TREE, "compute_exit_confirmation")
    assert calls, (
        "autotuner.py never calls math_engine.compute_exit_confirmation. "
        "The replay's trailing-stop exit must delegate to the canonical "
        "math_engine primitive — not be open-coded."
    )


def test_both_replay_functions_reach_compute_exit_confirmation() -> None:
    """AC-1: BOTH run_simulation and _collect_sim_returns must reach
    compute_exit_confirmation — directly, or via a shared per-tick exit core
    they call. Neither replay function may keep its own open-coded exit.

    For each of the two replay functions: either it calls
    compute_exit_confirmation directly, OR it calls a shared replay-machinery
    helper (e.g. _replay_exit_tick) that itself calls compute_exit_
    confirmation. This is the call-graph check the shared-core architecture
    requires — a lexical-containment check would wrongly forbid the refactor.

    RED: pre-fix neither function calls it and there is no shared exit core.
    """
    shared_cores = {
        f.name
        for f in _replay_machinery_nodes()
        if f.name not in ("_collect_sim_returns", "run_simulation")
        and _calls_in(f, "compute_exit_confirmation")
    }
    for func_name in ("_collect_sim_returns", "run_simulation"):
        func = _function_node(func_name)
        calls_directly = bool(_calls_in(func, "compute_exit_confirmation"))
        calls_shared_core = any(
            _calls_in(func, core_name) for core_name in shared_cores
        )
        assert calls_directly or calls_shared_core, (
            f"autotuner.{func_name} neither calls compute_exit_confirmation "
            f"directly nor delegates to a shared per-tick exit core that "
            f"does. The replay's trailing-stop exit must route through "
            f"compute_exit_confirmation for this function."
        )


def test_replay_machinery_has_no_open_coded_exit_literals() -> None:
    """AC-1: the open-coded exit block duplicated MAGNITUDE_FLOOR_PCT (0.10),
    MC_SANITY_THRESHOLD (60.0) and EXIT_CONFIRM_TICKS (3). After the fix the
    replay calls compute_exit_confirmation, so NONE of those three exit-rule
    literals may appear as bare numbers anywhere in the replay machinery
    (the two replay functions or any shared per-tick exit core).

    The literals are sourced from math_engine so the test fails if the
    producer re-tunes them — it asserts 'these specific values are not
    duplicated', not '0.10 / 60.0 / 3 are forbidden forever'.

    RED: the inline block contains `0.10`, `60.0` and `3` verbatim.
    """
    forbidden = {
        "MAGNITUDE_FLOOR_PCT": math_engine.MAGNITUDE_FLOOR_PCT,
        "MC_SANITY_THRESHOLD": math_engine.MC_SANITY_THRESHOLD,
        "EXIT_CONFIRM_TICKS": float(math_engine.EXIT_CONFIRM_TICKS),
    }
    leaked: dict[str, dict[str, float]] = {}
    for func in _replay_machinery_nodes():
        consts = _numeric_constants_in(func)
        hits = {
            name: value for name, value in forbidden.items() if value in consts
        }
        if hits:
            leaked[func.name] = hits
    assert not leaked, (
        f"Replay machinery still contains exit-rule literals {leaked} as "
        f"bare numbers. These are named in math_engine (MAGNITUDE_FLOOR_PCT "
        f"/ MC_SANITY_THRESHOLD / EXIT_CONFIRM_TICKS); the replay must call "
        f"compute_exit_confirmation, which owns them, so the values are "
        f"never duplicated in autotuner.py."
    )


def test_no_open_coded_below_stop_count_increment() -> None:
    """AC-1: the open-coded confirm-counter (`below_stop_count += 1` followed
    by `>= 3`) must not survive. compute_exit_confirmation owns the counter;
    the replay assigns its returned new_below_stop_count instead of mutating
    a local with `+= 1`.

    Detects an AugAssign that increments a name containing 'below_stop'
    ANYWHERE in the replay machinery (the two replay functions or any shared
    per-tick exit core). Architecture-agnostic — a shared-core refactor must
    not relocate the open-coded increment, it must remove it.

    RED: the replay functions do `below_stop_count += 1` inside the inline
    exit block.
    """
    offenders: list[str] = []
    for func in _replay_machinery_nodes():
        for node in ast.walk(func):
            if (
                isinstance(node, ast.AugAssign)
                and isinstance(node.op, ast.Add)
                and isinstance(node.target, ast.Name)
                and "below_stop" in node.target.id
            ):
                offenders.append(f"{func.name}:line {node.lineno}")
    assert not offenders, (
        f"Open-coded below_stop_count increment still present at {offenders}. "
        f"compute_exit_confirmation returns the updated count; the replay "
        f"must assign that return value, never `below_stop_count += 1`."
    )


# ===========================================================================
# BEHAVIOURAL — the replay tracks compute_exit_confirmation when a constant
# is re-tuned. This is the real anti-drift guarantee.
# ===========================================================================


def test_replay_trailing_exit_tracks_retuned_mc_sanity_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1 (anti-drift): if MC_SANITY_THRESHOLD is re-tuned in math_engine,
    the replay's trailing-stop decision must change accordingly — proving the
    replay reads the constant through compute_exit_confirmation rather than a
    stale hardcoded `60.0`.

    Construct a fixture where every tick sits below the stop with mc just
    above the DEFAULT MC_SANITY_THRESHOLD (so the default veto blocks every
    exit) but below a RAISED threshold (so a raised threshold lets the exit
    confirm). Run the replay under both thresholds; the exit decision must
    flip.

    RED: the replay hardcodes 60.0, so re-tuning math_engine.MC_SANITY_
    THRESHOLD does NOT change the replay's decision — both runs agree and
    the flip assertion fails.
    """
    if not hasattr(autotuner, "replay_exit_sequence"):
        pytest.fail(
            "autotuner.replay_exit_sequence missing — AC-6 helper required."
        )

    default_threshold = math_engine.MC_SANITY_THRESHOLD
    raised_threshold = default_threshold + 20.0  # 80.0 with the stock 60.0

    # mc between the two thresholds: default veto blocks; raised veto passes.
    mc_between = (default_threshold + raised_threshold) / 2.0  # 70.0

    params = {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 99.0,   # so far above any return VWAP never arms
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 999,      # bleed never confirms
        "PARABOLIC_VELOCITY_THRESHOLD": 99.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }

    # Ticks: armed (mc in [5,15) on tick 0 to arm), then a deep, sustained
    # drop below the stop for well over EXIT_CONFIRM_TICKS ticks, mc held at
    # mc_between. valid_vwap_weight 0 so VWAP gate never even evaluates.
    arm_tick = {
        "time": "09:30", "return": 5.0, "mc_prob": 10.0, "vol": 0.5,
        "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0,
    }
    drop_tick = {
        "time": "09:31", "return": -50.0, "mc_prob": mc_between, "vol": 0.5,
        "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0,
    }
    ticks = [arm_tick] + [dict(drop_tick) for _ in range(8)]

    def _run() -> list[tuple[int, str | None]]:
        seq = autotuner.replay_exit_sequence(ticks, params, grace_minutes=0)
        return [(d["tick_idx"], d["exit_reason"]) for d in seq]

    # Default threshold (60.0): mc 70 >= 60 -> veto -> NO trailing exit.
    monkeypatch.setattr(math_engine, "MC_SANITY_THRESHOLD", default_threshold)
    default_seq = _run()

    # Raised threshold (80.0): mc 70 < 80 -> veto passes -> trailing exit
    # confirms after EXIT_CONFIRM_TICKS qualifying ticks.
    monkeypatch.setattr(math_engine, "MC_SANITY_THRESHOLD", raised_threshold)
    raised_seq = _run()

    default_exited = any(reason for _, reason in default_seq)
    raised_exited = any(reason for _, reason in raised_seq)

    assert not default_exited, (
        "With the stock MC_SANITY_THRESHOLD the MC veto (mc 70 >= 60) must "
        f"block every trailing-stop exit; got exit sequence {default_seq}."
    )
    assert raised_exited, (
        "With MC_SANITY_THRESHOLD raised to 80 the veto (mc 70 < 80) must "
        "PASS and the trailing stop must confirm. The replay's decision did "
        "not change when the threshold was re-tuned — it is reading a "
        "hardcoded 60.0 instead of calling compute_exit_confirmation."
    )
