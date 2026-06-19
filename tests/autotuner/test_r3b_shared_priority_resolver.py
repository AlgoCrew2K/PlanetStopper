"""
RED tests for R3b — shared priority resolver (live-replay parity, H2 order canonical).

Background (task r3b-h2-priority-shared):

  Math audit found autotuner.py resolves triggers as TP > VWAP-Breakdown > VWAP-Bleed >
  Trailing-Stop — the OPPOSITE of live (alpha_bot_execution.py:1080-1093, fixed by H2).
  This inversion distorts per-reason guard-alpha penalty values feeding the Sortino
  objective; every Optuna retune inherits the drift.

  The fix replaces BOTH autotuner.py inline if/elif ladders AND the existing live
  alpha_bot_execution.py if/elif with a SINGLE shared resolver:
  math_engine.resolve_trigger_priority(). Single source of priority truth.

Acceptance Criteria exercised here:

  AC1  — math_engine.resolve_trigger_priority exists as a callable pure function
  AC2  — priority order canonical: VWAP Breakdown > Take-Profit > VWAP Bleed Cut >
          Trailing Stop (exhaustive pairwise + all-four-true)
  AC3  — autotuner.py NO LONGER contains inline if/elif ladders at the two
          cascade sites; both call math_engine.resolve_trigger_priority (AST/grep)
  AC4  — alpha_bot_execution.py NO LONGER contains inline if/elif ladder at
          the dispatch site; calls math_engine.resolve_trigger_priority (AST/grep)
  AC5  — live-vs-replay parity: TP + VWAP Breakdown both True → both resolve to
          VWAP Breakdown via the shared function (was: replay returned Take-Profit)
  AC6  — replay records also_true in per-tick metadata returned from the resolver
  AC7  — zero-trigger: resolve_trigger_priority returns (None, [])
  AC8  — single-trigger: resolve_trigger_priority returns (reason, []) with empty also_true
  AC9  — all-four-true: returns ("VWAP Breakdown", ["Take-Profit", "VWAP Bleed Cut", "Trailing Stop"])
  AC10 — _TRIGGER_PRIORITY (or its shared resolver equivalent) defined in ONE place;
          no duplicate priority-order definitions in autotuner.py or alpha_bot_execution.py
  AC11 — no inline duplicate priority constants survive post-fix (grep structural)

Fixture provenance:
  ALL fixtures under tests/fixtures/engine/shared_resolver/ — schema-derived per
  the R3b acceptance criteria. Zero producer-computed values hardcoded in assertions.

PA-18: fixtures captured-from-producer or schema-derived.
PA-19: explicit APPROVE from quant-code-reviewer + risk-engine-specialist required.

Tolerance policy:
  - No float math in these tests — resolver is a pure priority-selection function;
    all assertions are on string identity and list membership.
  - No tolerance on structural assertions (function existence, kwarg presence).
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Project root on sys.path (worktree cwd is project root)
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures" / "engine" / "shared_resolver"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text())


# ---------------------------------------------------------------------------
# Helpers: AST inspection utilities
# ---------------------------------------------------------------------------


def _load_ast(rel_path: str) -> ast.Module:
    src = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
    return ast.parse(src, filename=rel_path)


def _find_call_names(tree: ast.Module, func_name: str) -> list[ast.Call]:
    """Return all Call nodes whose func is a dotted name ending in func_name."""
    results = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == func_name
            or isinstance(func, ast.Name)
            and func.id == func_name
        ):
            results.append(node)
    return results


def _find_inline_trigger_cascade(tree: ast.Module) -> list[tuple[int, str]]:
    """
    Return (lineno, snippet) for any inline if/elif ladder that encodes a
    trigger priority decision by assigning 'reason_str' or similar based on
    is_tp_hit / is_vwap_broken / is_trailing_hit / is_vwap_bleed_broken.

    A cascade is characterized by an if block that assigns a string constant to
    a variable (reason_str / reason), where the test condition references one of
    the four trigger flag names.

    This pattern MUST NOT appear post-fix — both call sites must delegate to
    math_engine.resolve_trigger_priority instead.
    """
    trigger_flag_names = {
        "is_tp_hit",
        "is_trailing_hit",
        "is_trailing_stop_hit",
        "is_vwap_broken",
        "is_vwap_bleed_broken",
        "tp_triggered_now",
    }

    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        body_assigns = [
            n
            for n in node.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in {"reason_str", "reason"} for t in n.targets)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
        ]
        if not body_assigns:
            continue
        condition_names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if not condition_names & trigger_flag_names:
            continue
        hits.append((node.lineno, f"inline trigger cascade at line {node.lineno}"))
    return hits


# ---------------------------------------------------------------------------
# AC1: math_engine.resolve_trigger_priority is a callable pure function
# ---------------------------------------------------------------------------


def test_resolve_trigger_priority_exists_in_math_engine():
    """
    AC1: math_engine must export resolve_trigger_priority as a callable.

    RED: this function does not yet exist in math_engine.py.
    """
    import math_engine  # noqa: PLC0415

    assert hasattr(math_engine, "resolve_trigger_priority"), (
        "math_engine.resolve_trigger_priority does not exist. "
        "Implementer must add this pure function to math_engine.py."
    )
    assert callable(math_engine.resolve_trigger_priority), (
        "math_engine.resolve_trigger_priority must be callable."
    )


# ---------------------------------------------------------------------------
# AC7: zero-trigger case → (None, [])
# ---------------------------------------------------------------------------


def test_resolve_trigger_zero_flags_returns_none_and_empty_list():
    """
    AC7: when no candidate flag is True, resolve_trigger_priority returns (None, []).
    """
    import math_engine  # noqa: PLC0415

    fixture = _load("resolve_trigger_zero_trigger.json")
    flags = fixture["input"]

    result = math_engine.resolve_trigger_priority(
        is_vwap_broken=flags["is_vwap_broken"],
        is_tp_hit=flags["is_tp_hit"],
        is_vwap_bleed_broken=flags["is_vwap_bleed_broken"],
        is_trailing_stop_hit=flags["is_trailing_stop_hit"],
    )

    assert isinstance(result, tuple) and len(result) == 2, (
        f"resolve_trigger_priority must return a 2-tuple; got {type(result)}"
    )
    reason, also_true = result

    assert reason is None, f"Zero-trigger: expected reason=None; got {reason!r}"
    assert also_true == fixture["expected_also_true"], (
        f"Zero-trigger: expected also_true=[]; got {also_true!r}"
    )


# ---------------------------------------------------------------------------
# AC8: single-trigger case → (reason, []) — no also_true entries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name,flag_key,expected_reason",
    [
        ("resolve_trigger_single_trailing_stop.json", "is_trailing_stop_hit", "Trailing Stop"),
        ("resolve_trigger_single_vwap_breakdown.json", "is_vwap_broken", "VWAP Breakdown"),
    ],
)
def test_resolve_trigger_single_flag_returns_reason_with_empty_also_true(
    fixture_name: str, flag_key: str, expected_reason: str
):
    """
    AC8: single trigger → (reason, []) with empty also_true.
    Parameterized over Trailing Stop and VWAP Breakdown as representative single cases.
    """
    import math_engine  # noqa: PLC0415

    fixture = _load(fixture_name)
    flags = fixture["input"]

    reason, also_true = math_engine.resolve_trigger_priority(
        is_vwap_broken=flags["is_vwap_broken"],
        is_tp_hit=flags["is_tp_hit"],
        is_vwap_bleed_broken=flags["is_vwap_bleed_broken"],
        is_trailing_stop_hit=flags["is_trailing_stop_hit"],
    )

    assert reason == fixture["expected_reason"], (
        f"Single-trigger ({flag_key}): expected reason={fixture['expected_reason']!r}; got {reason!r}"
    )
    assert also_true == fixture["expected_also_true"], (
        f"Single-trigger ({flag_key}): expected also_true=[]; got {also_true!r}"
    )


# ---------------------------------------------------------------------------
# AC9: all-four-true → VWAP Breakdown wins, three others in also_true
# ---------------------------------------------------------------------------


def test_resolve_trigger_all_four_true_vwap_breakdown_wins():
    """
    AC9: all four trigger flags True → VWAP Breakdown wins; also_true contains
    Take-Profit, VWAP Bleed Cut, Trailing Stop.
    """
    import math_engine  # noqa: PLC0415

    fixture = _load("resolve_trigger_all_four_true.json")
    flags = fixture["input"]

    reason, also_true = math_engine.resolve_trigger_priority(
        is_vwap_broken=flags["is_vwap_broken"],
        is_tp_hit=flags["is_tp_hit"],
        is_vwap_bleed_broken=flags["is_vwap_bleed_broken"],
        is_trailing_stop_hit=flags["is_trailing_stop_hit"],
    )

    assert reason == fixture["expected_reason"], (
        f"All-four-true: expected winner={fixture['expected_reason']!r}; got {reason!r}. "
        f"Priority order must be VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop."
    )

    expected_also = set(fixture["expected_also_true"])
    actual_also = set(also_true)
    assert expected_also == actual_also, (
        f"All-four-true: expected also_true={expected_also}; got {actual_also}"
    )


# ---------------------------------------------------------------------------
# AC2: exhaustive pairwise priority order
# ---------------------------------------------------------------------------


def test_resolve_trigger_pairwise_priority_order_exhaustive():
    """
    AC2: for every pair of triggers, the higher-priority one always wins.
    Exercises all 6 pairwise combinations from the fixture table.
    """
    import math_engine  # noqa: PLC0415

    fixture = _load("resolve_trigger_priority_order_exhaustive.json")

    for case in fixture["pairwise_cases"]:
        flags = case["flags"]
        reason, also_true = math_engine.resolve_trigger_priority(
            is_vwap_broken=flags["is_vwap_broken"],
            is_tp_hit=flags["is_tp_hit"],
            is_vwap_bleed_broken=flags["is_vwap_bleed_broken"],
            is_trailing_stop_hit=flags["is_trailing_stop_hit"],
        )
        assert reason == case["expected_winner"], (
            f"Pairwise case {case['id']!r}: expected winner={case['expected_winner']!r}; "
            f"got {reason!r}. Priority order violation."
        )
        assert case["expected_loser_in_also_true"] in also_true, (
            f"Pairwise case {case['id']!r}: {case['expected_loser_in_also_true']!r} must "
            f"appear in also_true; got also_true={also_true!r}"
        )


# ---------------------------------------------------------------------------
# AC5: live-vs-replay parity — TP + Breakdown both True → VWAP Breakdown wins
# ---------------------------------------------------------------------------


def test_resolve_trigger_live_replay_parity_breakdown_beats_tp():
    """
    AC5: the canonical parity regression case. TP + VWAP Breakdown both True.
    Pre-fix replay returned Take-Profit (inverted cascade). Post-fix BOTH call
    sites must return VWAP Breakdown via the shared resolver.

    This test calls math_engine.resolve_trigger_priority directly — it will be
    RED until the shared function exists with the correct priority order.
    """
    import math_engine  # noqa: PLC0415

    fixture = _load("resolve_trigger_breakdown_wins_over_tp.json")
    flags = fixture["input"]

    reason, also_true = math_engine.resolve_trigger_priority(
        is_vwap_broken=flags["is_vwap_broken"],
        is_tp_hit=flags["is_tp_hit"],
        is_vwap_bleed_broken=flags["is_vwap_bleed_broken"],
        is_trailing_stop_hit=flags["is_trailing_stop_hit"],
    )

    assert reason == fixture["expected_reason"], (
        f"Live-replay parity: TP + VWAP Breakdown both True — expected "
        f"{fixture['expected_reason']!r}; got {reason!r}. "
        f"Pre-fix autotuner would have returned 'Take-Profit' (inverted cascade). "
        f"Shared resolver must encode VWAP Breakdown > Take-Profit."
    )
    assert set(fixture["expected_also_true"]) == set(also_true), (
        f"also_true mismatch: expected {fixture['expected_also_true']}; got {also_true}"
    )


# ---------------------------------------------------------------------------
# AC3 + AC4: structural — both autotuner sites AND live site delegate to resolver
# ---------------------------------------------------------------------------


def test_autotuner_collect_sim_returns_calls_resolve_trigger_priority():
    """
    AC3 (site 1): autotuner.py _collect_sim_returns must call
    math_engine.resolve_trigger_priority — no inline if/elif cascade.

    RED: autotuner.py:329-333 contains an inline ladder; this call does not exist.
    """
    tree = _load_ast("autotuner.py")
    calls = _find_call_names(tree, "resolve_trigger_priority")
    assert len(calls) >= 1, (
        "autotuner.py must contain at least one call to "
        "math_engine.resolve_trigger_priority (or resolve_trigger_priority). "
        "Found zero — _collect_sim_returns and run_simulation inline ladders must "
        "be replaced with the shared resolver call."
    )


def test_autotuner_run_simulation_calls_resolve_trigger_priority():
    """
    AC3: autotuner.py's replay must call math_engine.resolve_trigger_priority
    for the trigger-priority decision — the inline if/elif cascade must be
    gone.

    Cluster-3 relaxation: the original assertion expected >= 2 call sites
    (one lexically inside each of run_simulation and _collect_sim_returns).
    The autotuner-replay-parity cycle extracted a single shared per-tick
    exit core (_replay_exit_tick) that run_simulation, _collect_sim_returns
    and replay_exit_sequence all call — so resolve_trigger_priority now has
    ONE canonical call site, which is strictly better (single source of
    truth, zero duplication) than two. The contract this test enforces is
    "the canonical resolver is called, not open-coded" — satisfied by >= 1.
    That both replay functions REACH it (via the shared core) is covered by
    tests/autotuner/test_c3_replay_internal_lockstep.py; the absence of any
    inline cascade is covered by test_autotuner_has_no_inline_trigger_cascade
    below.

    RED (pre-fix): zero calls — the replay open-coded an inline if/elif ladder.
    """
    tree = _load_ast("autotuner.py")
    calls = _find_call_names(tree, "resolve_trigger_priority")
    assert len(calls) >= 1, (
        f"autotuner.py must call math_engine.resolve_trigger_priority for the "
        f"replay's trigger-priority decision. Found {len(calls)} call(s). The "
        f"inline if/elif trigger ladders must be replaced with the shared "
        f"resolver call (one canonical call site via the shared per-tick "
        f"exit core is the correct post-cluster-3 architecture)."
    )


def test_autotuner_has_no_inline_trigger_cascade():
    """
    AC3 structural: the inline if/elif trigger-reason assignment pattern must be
    absent from autotuner.py post-fix. Any surviving inline cascade is a bug.

    RED: two cascades exist at autotuner.py:329 and autotuner.py:474.
    """
    tree = _load_ast("autotuner.py")
    hits = _find_inline_trigger_cascade(tree)
    assert hits == [], (
        f"autotuner.py still contains {len(hits)} inline trigger-priority cascade(s): "
        f"{hits}. All inline if/elif reason-assignment ladders must be replaced by "
        f"calls to math_engine.resolve_trigger_priority."
    )


def test_alpha_bot_execution_calls_resolve_trigger_priority():
    """
    AC4: alpha_bot_execution.py must call math_engine.resolve_trigger_priority
    at the live dispatch site (replacing the _TRIGGER_PRIORITY local variable +
    next() pattern at alpha_bot_execution.py:1083-1093).

    RED: the current live site uses a local _TRIGGER_PRIORITY table, not the
    shared resolver call. After the fix it must delegate.
    """
    tree = _load_ast("alpha_bot_execution.py")
    calls = _find_call_names(tree, "resolve_trigger_priority")
    assert len(calls) >= 1, (
        "alpha_bot_execution.py must call math_engine.resolve_trigger_priority at "
        "the trigger dispatch site. Found zero calls — live site still uses inline "
        "_TRIGGER_PRIORITY table. Implementer must replace with shared resolver."
    )


def test_alpha_bot_execution_has_no_inline_trigger_cascade():
    """
    AC4 structural: the local _TRIGGER_PRIORITY variable must be replaced by the
    shared resolver.

    RED: alpha_bot_execution.py:1083-1088 defines local _TRIGGER_PRIORITY.
    """
    tree = _load_ast("alpha_bot_execution.py")
    trigger_priority_assignments = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "_TRIGGER_PRIORITY":
                trigger_priority_assignments.append(node.lineno)

    assert trigger_priority_assignments == [], (
        f"alpha_bot_execution.py defines local _TRIGGER_PRIORITY at line(s) "
        f"{trigger_priority_assignments}. Post-fix this local variable must not exist; "
        f"the shared resolver in math_engine.py is the single source of truth."
    )


# ---------------------------------------------------------------------------
# AC10: priority ordering lives in ONE place (math_engine.py only)
# ---------------------------------------------------------------------------


def test_no_duplicate_priority_order_in_autotuner():
    """
    AC10: autotuner.py must not define its own priority ordering. The shared
    resolver is the sole authority — any duplicate definition is a drift surface.

    RED: autotuner.py:474-478 encodes its own ordering (TP > Breakdown > Bleed > TS).
    """
    tree = _load_ast("autotuner.py")
    reason_strings = {"VWAP Breakdown", "Take-Profit", "VWAP Bleed Cut", "Trailing Stop"}

    parallel_tables = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        string_elts = [
            e.value
            for e in node.elts
            if isinstance(e, ast.Constant)
            and isinstance(e.value, str)
            and e.value in reason_strings
        ]
        if len(string_elts) >= 3:
            parallel_tables.append((getattr(node, "lineno", "?"), string_elts))

    assert parallel_tables == [], (
        f"autotuner.py contains {len(parallel_tables)} inline priority table(s) "
        f"(lists/tuples with 3+ trigger-reason strings): {parallel_tables}. "
        f"All priority ordering must live exclusively in math_engine.resolve_trigger_priority."
    )


def test_no_duplicate_priority_order_in_alpha_bot_execution():
    """
    AC10: alpha_bot_execution.py must not define a parallel priority table after
    the shared resolver is introduced.

    RED: alpha_bot_execution.py:1083-1088 defines _TRIGGER_PRIORITY as a local
    list of 3-tuples.
    """
    tree = _load_ast("alpha_bot_execution.py")
    reason_strings = {"VWAP Breakdown", "Take-Profit", "VWAP Bleed Cut", "Trailing Stop"}

    parallel_tables = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.List):
            continue
        if not node.elts:
            continue
        trigger_tuples = 0
        for elt in node.elts:
            if not isinstance(elt, ast.Tuple):
                continue
            if elt.elts and isinstance(elt.elts[0], ast.Constant):
                if elt.elts[0].value in reason_strings:
                    trigger_tuples += 1
        if trigger_tuples >= 3:
            parallel_tables.append((getattr(node, "lineno", "?"), trigger_tuples))

    assert parallel_tables == [], (
        f"alpha_bot_execution.py contains {len(parallel_tables)} inline priority list(s) "
        f"(list of 3-tuples with trigger-reason strings): {parallel_tables}. "
        f"Post-fix, the shared resolver is the single source of truth — remove the "
        f"local _TRIGGER_PRIORITY definition."
    )


# ---------------------------------------------------------------------------
# AC6: resolve_trigger_priority return shape enables also_true recording
# ---------------------------------------------------------------------------


def test_resolve_trigger_priority_return_shape_for_multi_trigger():
    """
    AC6: resolve_trigger_priority must return a 2-tuple (reason: str, also_true: list[str])
    for any multi-trigger scenario. Replay code can record also_true in per-tick metadata.

    Shape/type contract only — no hardcoded reason string literals beyond fixture.
    """
    import math_engine  # noqa: PLC0415

    fixture = _load("resolve_trigger_breakdown_wins_over_tp.json")
    flags = fixture["input"]

    result = math_engine.resolve_trigger_priority(
        is_vwap_broken=flags["is_vwap_broken"],
        is_tp_hit=flags["is_tp_hit"],
        is_vwap_bleed_broken=flags["is_vwap_bleed_broken"],
        is_trailing_stop_hit=flags["is_trailing_stop_hit"],
    )

    assert isinstance(result, tuple), f"Return type must be tuple; got {type(result)}"
    assert len(result) == 2, f"Return tuple must have 2 elements; got {len(result)}"

    reason, also_true = result
    assert isinstance(reason, str), f"reason must be str; got {type(reason)}"
    assert isinstance(also_true, list), f"also_true must be list; got {type(also_true)}"
    assert all(isinstance(x, str) for x in also_true), (
        f"all elements of also_true must be str; got {[type(x) for x in also_true]}"
    )


# ---------------------------------------------------------------------------
# AC2 invariant: resolver is pure (deterministic, no side effects)
# ---------------------------------------------------------------------------


def test_resolve_trigger_priority_is_deterministic():
    """
    AC2 invariant: same inputs → same output on repeated calls. Pure function.
    """
    import math_engine  # noqa: PLC0415

    fixture = _load("resolve_trigger_all_four_true.json")
    flags = fixture["input"]

    results = [
        math_engine.resolve_trigger_priority(
            is_vwap_broken=flags["is_vwap_broken"],
            is_tp_hit=flags["is_tp_hit"],
            is_vwap_bleed_broken=flags["is_vwap_bleed_broken"],
            is_trailing_stop_hit=flags["is_trailing_stop_hit"],
        )
        for _ in range(5)
    ]

    first = results[0]
    for i, r in enumerate(results[1:], start=1):
        assert r == first, (
            f"resolve_trigger_priority is non-deterministic: "
            f"run 0 returned {first!r}, run {i} returned {r!r}."
        )
