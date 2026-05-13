"""
RED tests — Cycle B of task #24.

These tests assert that autotuner.py's inline VWAP breakdown / bleed state
machine has been replaced by a call to the canonical helper
``math_engine.compute_vwap_breakdown_update``. They are static AST-only checks
— they do NOT execute autotuner.run_autotuner, synthetic_history, or any
network/IO code. They are designed to remain robust to whitespace / small
refactorings and to fail loudly if a future implementer drops a required
kwarg.

Pre-cycle-B (RED): autotuner.py contains an inline state machine that mutates
``vwap_ticks`` / ``vwap_bleed_ticks`` directly and never calls
``math_engine.compute_vwap_breakdown_update``. All four tests below FAIL.

Post-cycle-B (GREEN, written by the implementer in a later commit): the
inline mutations are gone, the canonical helper is invoked with all required
kwargs including ``valid_vwap_weight`` derived from each tick, and these
tests pass.
"""

from __future__ import annotations

import ast
import pathlib


# Required kwargs for the canonical helper (per cycle 10 signature):
#   is_triggered, valid_vwap_weight, weighted_vwap_diff, safe_hwm,
#   current_return, vwap_cross_hwm_pct, vwap_bleed_arm_pct,
#   vwap_bleed_ticks_threshold, current_vwap_ticks, current_vwap_bleed_ticks
REQUIRED_KWARGS = frozenset(
    {
        "is_triggered",
        "valid_vwap_weight",
        "weighted_vwap_diff",
        "safe_hwm",
        "current_return",
        "vwap_cross_hwm_pct",
        "vwap_bleed_arm_pct",
        "vwap_bleed_ticks_threshold",
        "current_vwap_ticks",
        "current_vwap_bleed_ticks",
    }
)

# Names whose inline mutation we are trying to eliminate. Post-swap, the only
# allowed Assign / AugAssign of these names is the tuple-unpack of the helper's
# return value.
INLINE_VWAP_STATE_NAMES = frozenset({"vwap_ticks", "vwap_bleed_ticks"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _autotuner_path() -> pathlib.Path:
    """Resolve the autotuner.py path from the repo root."""
    # tests/autotuner/<this_file>  ->  repo_root / autotuner.py
    return pathlib.Path(__file__).resolve().parents[2] / "autotuner.py"


def _parse_autotuner() -> ast.Module:
    src = _autotuner_path().read_text(encoding="utf-8")
    return ast.parse(src, filename=str(_autotuner_path()))


def _is_compute_vwap_breakdown_call(node: ast.AST) -> bool:
    """Return True iff ``node`` is a Call to math_engine.compute_vwap_breakdown_update."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    # Match the dotted form: math_engine.compute_vwap_breakdown_update(...)
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "compute_vwap_breakdown_update"
        and isinstance(func.value, ast.Name)
        and func.value.id == "math_engine"
    ):
        return True
    # Also match a bare from-import form: compute_vwap_breakdown_update(...)
    if isinstance(func, ast.Name) and func.id == "compute_vwap_breakdown_update":
        return True
    return False


def _find_canonical_calls(tree: ast.Module) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if _is_compute_vwap_breakdown_call(n)]


def _find_run_simulation(tree: ast.Module) -> ast.FunctionDef | None:
    """Locate the inner ``run_simulation`` function definition within run_autotuner."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_simulation":
            return node
    return None


def _value_reads_valid_vwap_weight_from_tick(value: ast.AST) -> bool:
    """
    Return True if the AST expression ``value`` looks like it's pulling
    ``valid_vwap_weight`` from a tick — accepting either of:
        tick.get("valid_vwap_weight", <default>)
        tick["valid_vwap_weight"]
    The variable name is treated leniently — we accept any Name node on the
    receiver side (in case the implementer renamed ``tick`` locally) but the
    string literal must be exactly "valid_vwap_weight".
    """
    # tick.get("valid_vwap_weight", ...)
    if isinstance(value, ast.Call):
        func = value.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and value.args
            and isinstance(value.args[0], ast.Constant)
            and value.args[0].value == "valid_vwap_weight"
        ):
            return True
    # tick["valid_vwap_weight"]
    if isinstance(value, ast.Subscript):
        slc = value.slice
        # Python 3.9+: slice is the expression itself
        if isinstance(slc, ast.Constant) and slc.value == "valid_vwap_weight":
            return True
    return False


# ---------------------------------------------------------------------------
# Test 1 — math_engine.compute_vwap_breakdown_update is called in autotuner.py
# ---------------------------------------------------------------------------


def test_autotuner_invokes_canonical_vwap_breakdown_helper() -> None:
    """autotuner.py must contain at least one call to math_engine.compute_vwap_breakdown_update."""
    tree = _parse_autotuner()
    calls = _find_canonical_calls(tree)
    assert len(calls) >= 1, (
        "Expected autotuner.py to call math_engine.compute_vwap_breakdown_update "
        "(canonical VWAP state machine), but found zero such calls. The inline "
        "state machine at lines ~222-237 must be replaced with a call to the "
        "canonical helper."
    )


# ---------------------------------------------------------------------------
# Test 2 — the canonical call passes valid_vwap_weight pulled from a tick
# ---------------------------------------------------------------------------


def test_canonical_call_passes_valid_vwap_weight_from_tick() -> None:
    """
    The canonical call must include a ``valid_vwap_weight`` kwarg whose value
    is read from the tick (either ``tick.get("valid_vwap_weight", ...)`` or
    ``tick["valid_vwap_weight"]``). This connects Cycle A's tick schema
    (synthetic_history emits the field) to the canonical state machine.

    To remain robust against the implementer assigning the tick read to a
    local intermediate (e.g. ``vvw = tick.get("valid_vwap_weight", 1.0)``)
    before passing it as the kwarg, we also accept a kwarg whose value is a
    Name whose binding origin in the enclosing function is a tick read with
    the literal "valid_vwap_weight".
    """
    tree = _parse_autotuner()
    calls = _find_canonical_calls(tree)
    assert calls, (
        "Cannot verify valid_vwap_weight kwarg: no call to "
        "math_engine.compute_vwap_breakdown_update found in autotuner.py."
    )

    # Build a set of local Names whose binding origin reads valid_vwap_weight
    # from a tick. We search all Assign nodes in the module — broad but safe
    # since "valid_vwap_weight" is a distinctive literal.
    tick_read_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _value_reads_valid_vwap_weight_from_tick(node.value):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    tick_read_aliases.add(tgt.id)

    found = False
    for call in calls:
        for kw in call.keywords:
            if kw.arg != "valid_vwap_weight":
                continue
            # Accept direct tick read at the call site, OR a Name that we have
            # confirmed was assigned from a tick read of "valid_vwap_weight".
            if _value_reads_valid_vwap_weight_from_tick(kw.value):
                found = True
                break
            if isinstance(kw.value, ast.Name) and kw.value.id in tick_read_aliases:
                found = True
                break
        if found:
            break

    assert found, (
        "Expected a call to math_engine.compute_vwap_breakdown_update with a "
        "valid_vwap_weight kwarg whose value is derived from the tick "
        '(e.g. tick.get("valid_vwap_weight", 1.0) or tick["valid_vwap_weight"]). '
        "No such kwarg was found at the call site."
    )


# ---------------------------------------------------------------------------
# Test 3 — inline VWAP state mutations are removed
# ---------------------------------------------------------------------------


def test_inline_vwap_state_mutations_are_removed() -> None:
    """
    Post-swap, ``vwap_ticks`` and ``vwap_bleed_ticks`` must NOT be mutated by
    inline state-machine code. The only acceptable Assign for these names is
    a tuple-unpack of the canonical helper's return value. AugAssigns
    (``vwap_ticks += 1``) and standalone reassignments (``vwap_ticks = 0``)
    are inline-state-machine signatures and are forbidden.

    Initialization assignments (``vwap_ticks = 0`` at the top of the per-day
    block) are allowed — they bind the starting state that the helper then
    advances.
    """
    tree = _parse_autotuner()
    run_sim = _find_run_simulation(tree)
    assert run_sim is not None, "Could not locate run_simulation in autotuner.py"

    canonical_calls_in_run_sim = [
        n for n in ast.walk(run_sim) if _is_compute_vwap_breakdown_call(n)
    ]

    # Collect every Assign / AugAssign of vwap_ticks or vwap_bleed_ticks
    # inside run_simulation.
    offending: list[tuple[str, int]] = []
    init_lines: set[int] = set()  # lines we treat as initialization (allowed)

    # Find the line range of the innermost for-tick loop (tick_idx, tick).
    # Mutations inside that loop body that are NOT tuple-unpacks of the
    # canonical call are offending.
    tick_loop: ast.For | None = None
    for node in ast.walk(run_sim):
        if isinstance(node, ast.For):
            tgt = node.target
            # for tick_idx, tick in enumerate(ticks):
            if (
                isinstance(tgt, ast.Tuple)
                and len(tgt.elts) == 2
                and isinstance(tgt.elts[1], ast.Name)
                and tgt.elts[1].id == "tick"
            ):
                tick_loop = node
                break

    assert tick_loop is not None, (
        "Could not find the per-tick for-loop in run_simulation — autotuner "
        "may have been refactored in a way these tests don't understand."
    )

    tick_loop_start = tick_loop.lineno
    tick_loop_end = getattr(tick_loop, "end_lineno", tick_loop_start)

    def _is_tuple_unpack_of_canonical_call(value: ast.AST) -> bool:
        return isinstance(value, ast.Call) and _is_compute_vwap_breakdown_call(value)

    # Initialization sites: any Assign of these names OUTSIDE the tick loop
    # (i.e. in the per-day setup) is permitted. Pre-swap, the offending
    # mutations all live INSIDE the tick loop, so this is sufficient.
    for node in ast.walk(run_sim):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                # Plain `vwap_ticks = 0` or `vwap_bleed_ticks = 0`
                if isinstance(tgt, ast.Name) and tgt.id in INLINE_VWAP_STATE_NAMES:
                    if not (tick_loop_start <= node.lineno <= tick_loop_end):
                        init_lines.add(node.lineno)
                        continue
                    # Inside tick loop — must be a tuple-unpack of the canonical call
                    if _is_tuple_unpack_of_canonical_call(node.value):
                        continue
                    offending.append((tgt.id, node.lineno))
                # Tuple-target: (vwap_ticks, vwap_bleed_ticks, ...) = canonical_call(...)
                if isinstance(tgt, ast.Tuple):
                    names_in_target = {e.id for e in tgt.elts if isinstance(e, ast.Name)}
                    hit = names_in_target & INLINE_VWAP_STATE_NAMES
                    if hit and not _is_tuple_unpack_of_canonical_call(node.value):
                        for n in hit:
                            offending.append((n, node.lineno))
        if isinstance(node, ast.AugAssign):
            tgt = node.target
            if isinstance(tgt, ast.Name) and tgt.id in INLINE_VWAP_STATE_NAMES:
                offending.append((tgt.id, node.lineno))

    assert not offending, (
        "Inline VWAP state mutations still present in run_simulation. The "
        "following Assign/AugAssign nodes mutate vwap_ticks / vwap_bleed_ticks "
        "without unpacking from math_engine.compute_vwap_breakdown_update:\n"
        + "\n".join(f"  - {name} mutated at line {lineno}" for name, lineno in offending)
        + "\nThese must be removed and replaced by a single tuple-unpack of "
        "the canonical helper's return value."
    )

    # Sanity: there should also actually BE a canonical call inside the tick
    # loop — otherwise the absence of mutations is vacuous.
    assert canonical_calls_in_run_sim, (
        "No call to math_engine.compute_vwap_breakdown_update found inside "
        "run_simulation. The inline state machine was removed but the "
        "canonical helper was not wired in."
    )


# ---------------------------------------------------------------------------
# Test 4 — all 9 required kwargs are present in the canonical call
# ---------------------------------------------------------------------------


def test_canonical_call_includes_all_required_kwargs() -> None:
    """
    Anti-drift: if a future implementer wires in the canonical helper but
    forgets a kwarg, the math layer silently degrades. This test pins every
    kwarg of the cycle-10 signature by name in a single assertion so failure
    output enumerates every missing kwarg at once.
    """
    tree = _parse_autotuner()
    calls = _find_canonical_calls(tree)
    assert calls, (
        "Cannot verify kwargs: no call to "
        "math_engine.compute_vwap_breakdown_update found in autotuner.py."
    )

    # Any call to the canonical helper must include every required kwarg.
    missing_by_call: list[tuple[int, set[str]]] = []
    for call in calls:
        kw_names = {kw.arg for kw in call.keywords if kw.arg is not None}
        missing = REQUIRED_KWARGS - kw_names
        if missing:
            missing_by_call.append((call.lineno, missing))

    assert not missing_by_call, (
        "Call(s) to math_engine.compute_vwap_breakdown_update are missing "
        "required kwargs from the cycle-10 signature. Every kwarg must be "
        "passed by name:\n"
        + "\n".join(
            f"  - line {lineno}: missing {sorted(missing)}"
            for lineno, missing in missing_by_call
        )
        + f"\nFull required kwarg set: {sorted(REQUIRED_KWARGS)}."
    )
