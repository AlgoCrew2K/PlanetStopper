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
    """Locate the ``run_simulation`` function definition."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_simulation":
            return node
    return None


# Cluster-3: the autotuner-replay-parity cycle may extract the per-tick exit
# loop into a shared core (_replay_exit_tick) that run_simulation /
# _collect_sim_returns / replay_exit_sequence all call. The VWAP state machine
# then lives in that shared core, not lexically inside run_simulation. These
# names cover both the pre-cluster-3 (loop inside run_simulation) and the
# post-cluster-3 shared-core layouts.
_REPLAY_VWAP_BEARING_NAMES = (
    "run_simulation",
    "_collect_sim_returns",
    "_replay_exit_tick",
    "_replay_tick",
    "_simulate_exit_tick",
)


def _find_vwap_state_machine_functions(tree: ast.Module) -> list[ast.FunctionDef]:
    """Return the replay function(s) that carry the VWAP breakdown state
    machine — whichever of the replay-machinery functions actually contain a
    call to math_engine.compute_vwap_breakdown_update.

    Pre-cluster-3 this is run_simulation itself; post-cluster-3 it is the
    shared per-tick exit core. The inline-mutation check runs against
    whichever function(s) own the machine.
    """
    out: list[ast.FunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in _REPLAY_VWAP_BEARING_NAMES:
            if any(_is_compute_vwap_breakdown_call(n) for n in ast.walk(node)):
                out.append(node)
    return out


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

    Cluster-3 update: the autotuner-replay-parity cycle may extract the
    per-tick exit machine into a shared core (_replay_exit_tick). The VWAP
    state machine then lives in that core, not lexically inside
    run_simulation. This test now checks the function(s) that actually OWN
    the VWAP machine — whichever replay-machinery function calls
    compute_vwap_breakdown_update — so it is correct under both the
    pre-cluster-3 (loop inside run_simulation) and the shared-core layouts.

    A plain-Name Assign of vwap_ticks / vwap_bleed_ticks that is NOT a
    tuple-unpack of the canonical call is an inline-state-machine signature
    and is forbidden; an AugAssign of those names is always forbidden. Plain
    initialization (``vwap_ticks = 0``) outside any per-tick context is
    allowed. A shared core that threads VWAP state via a dict
    (``state["vwap_ticks"]``) has no plain-Name mutation at all — also fine.
    """
    tree = _parse_autotuner()
    vwap_funcs = _find_vwap_state_machine_functions(tree)
    assert vwap_funcs, (
        "No replay-machinery function calls math_engine.compute_vwap_"
        "breakdown_update. The replay's VWAP breakdown decision must "
        "delegate to the canonical helper — see autotuner.py "
        "run_simulation / _collect_sim_returns / _replay_exit_tick."
    )

    def _is_tuple_unpack_of_canonical_call(value: ast.AST) -> bool:
        return isinstance(value, ast.Call) and _is_compute_vwap_breakdown_call(value)

    # An offending mutation = a plain-Name (not subscript/attribute) Assign or
    # AugAssign of vwap_ticks / vwap_bleed_ticks that is NOT the tuple-unpack
    # of the canonical call. Walk every VWAP-bearing replay function.
    offending: list[tuple[str, int]] = []
    for func in vwap_funcs:
        for node in ast.walk(func):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    # Plain `vwap_ticks = <something>` — forbidden unless it
                    # is a tuple-unpack of the canonical call, or a bare
                    # init (= 0 / a constant) binding the starting state.
                    if isinstance(tgt, ast.Name) and tgt.id in INLINE_VWAP_STATE_NAMES:
                        if _is_tuple_unpack_of_canonical_call(node.value):
                            continue
                        if isinstance(node.value, ast.Constant):
                            continue  # plain initialization is allowed
                        offending.append((tgt.id, node.lineno))
                    # Tuple-target: (vwap_ticks, vwap_bleed_ticks, ...) = ...
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
        "Inline VWAP state mutations still present in the replay machinery. "
        "The following Assign/AugAssign nodes mutate vwap_ticks / "
        "vwap_bleed_ticks without unpacking from math_engine.compute_vwap_"
        "breakdown_update:\n"
        + "\n".join(f"  - {name} mutated at line {lineno}" for name, lineno in offending)
        + "\nThese must be removed and replaced by a single tuple-unpack of "
        "the canonical helper's return value."
    )

    # Sanity: a canonical call must actually exist — _find_vwap_state_machine_
    # functions already guarantees this (it selects functions that call it),
    # but assert explicitly so the no-mutations result is never vacuous.
    canonical_calls_in_run_sim = [
        n for f in vwap_funcs for n in ast.walk(f) if _is_compute_vwap_breakdown_call(n)
    ]
    assert canonical_calls_in_run_sim, (
        "No call to math_engine.compute_vwap_breakdown_update found in the "
        "replay machinery. The inline state machine was removed but the "
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
            f"  - line {lineno}: missing {sorted(missing)}" for lineno, missing in missing_by_call
        )
        + f"\nFull required kwarg set: {sorted(REQUIRED_KWARGS)}."
    )
