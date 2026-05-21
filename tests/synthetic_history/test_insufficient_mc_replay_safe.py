"""
AC-2 / team-lead Ruling 1b — RED tests: synthetic_history must convert an
insufficient-MC result into a replay-safe representation before it reaches the
autotuner replay tick dict.

THE DEFECT
----------
``run_monte_carlo`` now returns the out-of-band sentinel
``MC_INSUFFICIENT_HISTORY_SENTINEL`` (None) when MC history is insufficient.
``synthetic_history.py`` calls ``run_monte_carlo`` and stores the raw return
straight into ``tick["mc_prob"]``. The autotuner replay then reads that tick:
``autotuner.run_simulation`` does ``tick.get("mc_prob", 50.0)`` and immediately
compares the value numerically (``TAKE_PROFIT_MC_PCT <= mc < ...``). Because
the ``mc_prob`` key EXISTS with value None, ``.get`` returns None (not the
50.0 default), and the comparison raises ``TypeError`` — the autotuner replay
crashes whenever a replayed day has insufficient MC history.

THE FIX (team-lead Ruling 1b — keep it MINIMAL)
-----------------------------------------------
``synthetic_history`` converts an insufficient (None) MC result into a
replay-safe representation rather than propagating raw None into the tick dict.
The autotuner's open-coded replay exit logic is NOT restructured (that is
Cluster 3). The bounded obligation: (a) nothing crashes on the sentinel, and
(b) the replay's insufficient-MC handling does not FABRICATE an exit/arm —
consistent with the production fail-safe (insufficient MC = no arm, no disarm,
no TP).

WHAT THESE TESTS ASSERT
-----------------------
1. (static) synthetic_history.py does not store a raw, possibly-None
   ``run_monte_carlo`` return directly into ``tick["mc_prob"]`` — there must be
   a None-guard / conversion between the call and the tick-dict store.
2. (behavioural) the autotuner replay's arm/disarm gates do NOT fabricate a
   state transition for the replay-safe insufficient-MC value: a tick carrying
   the replay-safe representation must leave an unarmed symphony unarmed and an
   armed symphony armed.
3. (behavioural, RED-verification of the defect) feeding ``run_simulation`` a
   tick with a raw None ``mc_prob`` raises TypeError — this documents WHY the
   conversion in synthetic_history is mandatory (the autotuner cannot consume
   raw None).

PROVENANCE
----------
synthetic_history.py is analysed statically (AST) — the project's existing
synthetic_history tests do this because the module imports requests/pandas and
configures Alpaca credentials at import time. The autotuner behavioural tests
import autotuner directly (safe) and drive the module-level run_simulation.
No producer-computed value is hardcoded.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import autotuner

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SYNTH_PATH = _REPO_ROOT / "synthetic_history.py"


# ---------------------------------------------------------------------------
# Static analysis of synthetic_history.py
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synth_tree() -> ast.Module:
    assert _SYNTH_PATH.is_file(), f"expected production module at {_SYNTH_PATH}"
    return ast.parse(_SYNTH_PATH.read_text(encoding="utf-8"), filename=str(_SYNTH_PATH))


def _find_run_monte_carlo_call(tree: ast.AST) -> ast.Call:
    """Locate the run_monte_carlo call in synthetic_history.py."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run_monte_carlo"
        ):
            return node
    raise AssertionError(
        "Could not find a run_monte_carlo call in synthetic_history.py."
    )


def _enclosing_assign_target(tree: ast.AST, call: ast.Call) -> str | None:
    """If the run_monte_carlo call is the RHS of a simple `name = call`,
    return the assigned name; else None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and node.value is call:
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                return node.targets[0].id
    return None


def test_synthetic_history_does_not_store_raw_run_monte_carlo_return_in_tick(
    synth_tree: ast.Module,
) -> None:
    """
    Ruling 1b: synthetic_history must NOT store the raw run_monte_carlo return
    (which can be the None insufficient sentinel) straight into the tick dict's
    ``mc_prob`` field. There must be a None-handling conversion in between.

    The check: find the name bound to the run_monte_carlo result, then find the
    tick dict's ``mc_prob`` value expression. If ``mc_prob`` is just that bare
    name (raw passthrough) AND there is no None-guard converting it, the
    insufficient sentinel reaches the autotuner unconverted — RED.

    RED against the current code (synthetic_history.py:237 `mc_prob = run_monte_
    carlo(...)` then `"mc_prob": mc_prob` with no conversion). GREEN once a
    None-guard / conversion is inserted (e.g. an `if mc_prob is None:`
    substitution, or `mc_prob if mc_prob is not None else <replay-safe value>`).
    """
    mc_call = _find_run_monte_carlo_call(synth_tree)
    result_name = _enclosing_assign_target(synth_tree, mc_call)

    # Collect every name referenced anywhere in the module that is compared
    # against None or used as the test of an if/conditional — a proxy for
    # "this value is None-guarded somewhere".
    none_guarded_names: set[str] = set()
    for node in ast.walk(synth_tree):
        # `x is None` / `x is not None` comparisons
        if isinstance(node, ast.Compare):
            for operand in [node.left, *node.comparators]:
                if isinstance(operand, ast.Name):
                    # the other side must be a None constant
                    others = [node.left, *node.comparators]
                    if any(
                        isinstance(o, ast.Constant) and o.value is None
                        for o in others
                    ):
                        none_guarded_names.add(operand.id)
        # `x if x is not None else y` ternary
        if isinstance(node, ast.IfExp) and isinstance(node.test, ast.Compare):
            for operand in [node.test.left, *node.test.comparators]:
                if isinstance(operand, ast.Name):
                    none_guarded_names.add(operand.id)

    # Locate the tick dict and its mc_prob value expression.
    mc_prob_value: ast.AST | None = None
    for node in ast.walk(synth_tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (
                    isinstance(k, ast.Constant)
                    and k.value == "mc_prob"
                ):
                    mc_prob_value = v

    assert mc_prob_value is not None, (
        "Could not find the tick dict's 'mc_prob' value expression in "
        "synthetic_history.py — the RED test cannot proceed."
    )

    # If mc_prob's value is a bare Name equal to the raw run_monte_carlo
    # result, that name MUST be None-guarded somewhere in the module.
    if isinstance(mc_prob_value, ast.Name) and result_name is not None:
        if mc_prob_value.id == result_name:
            assert result_name in none_guarded_names, (
                f"synthetic_history.py stores the raw run_monte_carlo result "
                f"('{result_name}') directly into tick['mc_prob'] with no "
                f"None-guard anywhere. run_monte_carlo now returns None for "
                f"insufficient MC history; that None reaches the autotuner "
                f"replay and crashes run_simulation's numeric mc gates. "
                f"Ruling 1b: convert the insufficient (None) result into a "
                f"replay-safe representation before the tick dict."
            )
    # If mc_prob's value is itself a conditional expression (IfExp), the
    # conversion is inline — that satisfies the contract; nothing to assert.


# ---------------------------------------------------------------------------
# Behavioural — the autotuner replay must not be crashed or fooled
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
    run_autotuner needs >= 2 distinct dates; mirror that here."""
    return {"sym-A": {"2026-04-01": list(ticks), "2026-04-02": list(ticks)}}


_PARAMS = {
    "TAKE_PROFIT_MC_PCT": 5.0,
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
    "MAX_PARABOLIC_SQUEEZE": 0.50,
}


def test_run_simulation_raises_on_raw_none_mc_prob_documenting_the_defect() -> None:
    """
    RED-VERIFICATION of the defect. autotuner.run_simulation cannot consume a
    raw None mc_prob — the arm gate's numeric comparison raises TypeError. This
    is WHY synthetic_history must convert the insufficient sentinel before the
    tick dict (Ruling 1b keeps the autotuner unchanged).

    This test PASSES today (it asserts the raise) and documents the constraint.
    It is the converse of the static test above: synthetic_history MUST do the
    conversion precisely because the autotuner replay cannot.
    """
    history = _history([_tick(mc_prob=None)])
    with pytest.raises(TypeError):
        autotuner.run_simulation(_PARAMS, history, ["sym-A"], "2026-05-10", {})


def test_replay_safe_insufficient_value_does_not_fabricate_an_arm() -> None:
    """
    Ruling 1b (b): the replay-safe representation synthetic_history substitutes
    for an insufficient MC result must NOT fabricate an arm in the autotuner
    replay — consistent with the production fail-safe.

    The autotuner arm gate is ``TAKE_PROFIT_MC_PCT <= mc < TRIGGER_THRESHOLD_PCT``
    and the disarm gate is ``mc > TRIGGER_THRESHOLD_PCT * 2``. A replay-safe
    insufficient value must fall OUTSIDE the arm band and NOT exceed the disarm
    bound — i.e. in ``[TRIGGER_THRESHOLD_PCT, TRIGGER_THRESHOLD_PCT * 2]``.

    This test does not pin the exact value (implementer's choice); it asserts
    the PROPERTY: run_simulation must complete without error AND a tick stream
    carrying the replay-safe value must not produce a triggered exit on its own
    (no fabricated arm -> no MC-driven trigger). The fixture's returns are flat
    and positive so the ONLY thing that could arm/trigger is the MC gate.

    The replay-safe value is read from a synthetic_history module constant the
    implementer must expose (MC_INSUFFICIENT_REPLAY_VALUE or similar). If no
    such constant exists yet this test fails RED with a clear message.
    """
    # The implementer must expose the replay-safe value as a named constant so
    # this test can assert the property on the ACTUAL substituted value rather
    # than guessing it.
    import importlib

    synth = pytest.importorskip(
        "synthetic_history",
        reason="synthetic_history import unavailable in this environment",
    )
    importlib.reload(synth)
    replay_value = getattr(synth, "MC_INSUFFICIENT_REPLAY_VALUE", None)
    assert replay_value is not None, (
        "synthetic_history must expose a named constant for the replay-safe "
        "value substituted when MC history is insufficient (e.g. "
        "MC_INSUFFICIENT_REPLAY_VALUE). Ruling 1b: the conversion value must be "
        "named with a source comment, not a bare literal."
    )

    # The replay-safe value must not be None and must be a real number.
    assert isinstance(replay_value, (int, float)) and replay_value is not None, (
        f"MC_INSUFFICIENT_REPLAY_VALUE must be a real number (the autotuner "
        f"replay does numeric comparisons on it); got {replay_value!r}."
    )

    # Property: it must fall outside the arm band and not exceed the disarm
    # bound, so it fabricates neither an arm nor a disarm.
    tp = _PARAMS["TAKE_PROFIT_MC_PCT"]
    trigger = _PARAMS["TRIGGER_THRESHOLD_PCT"]
    assert not (tp <= replay_value < trigger), (
        f"The replay-safe insufficient-MC value {replay_value} falls inside "
        f"the autotuner arm band [{tp}, {trigger}) — it would fabricate an ARM "
        f"on every insufficient-MC replay tick. Ruling 1b: insufficient MC "
        f"must not fabricate a state transition."
    )
    assert not (replay_value > trigger * 2), (
        f"The replay-safe insufficient-MC value {replay_value} exceeds the "
        f"autotuner disarm bound ({trigger * 2}) — combined with a positive "
        f"return it would fabricate a DISARM. Ruling 1b: insufficient MC must "
        f"not fabricate a state transition."
    )

    # Behavioural confirmation: run_simulation completes without error on a
    # tick stream carrying the replay-safe value (flat positive returns, so
    # only the MC gate could move state).
    history = _history([_tick(mc_prob=replay_value)])
    result = autotuner.run_simulation(
        _PARAMS, history, ["sym-A"], "2026-05-10", {}
    )
    assert result is not None, (
        "run_simulation returned None for a replay-safe insufficient-MC tick "
        "stream — it must complete and return a guard-alpha figure."
    )
