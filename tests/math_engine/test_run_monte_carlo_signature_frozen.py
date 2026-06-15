"""
Phase-1 blast-radius defence — run_monte_carlo signature freeze.

WHY THIS TEST EXISTS
--------------------
run_monte_carlo is consumed at 7+ sites across alpha_bot_execution.py,
reporting.py, synthetic_history.py, and autotuner.py (memory:
project_mc_sentinel_consumer_blast_radius). A parameter add, remove, or rename
breaks every consumer simultaneously. Phase-1 freezes the signature until the
last-symphony Phase-2 cutover ceremony (council-converged-migration-plan.md §6
H7). This test makes a freeze violation a CI failure, not a reviewer-attention
dependency.

WHAT IS ASSERTED
----------------
1. The parameter names are exactly the frozen baseline — no add, no remove, no
   rename.
2. The parameter count equals the baseline.
3. Each parameter's positional kind matches the baseline (POSITIONAL_OR_KEYWORD
   vs KEYWORD_ONLY etc.) — a refactor that converts positional args to
   keyword-only silently breaks callers that pass positionally.
4. Parameters WITH defaults have defaults; parameters WITHOUT defaults do not.
5. The ``seed`` parameter's default is None — the only concrete default value
   the contract cares about (all others delegate to named constants).
6. The return-type annotation, if present, is ``float | None`` or is absent
   (unannotated); it must not be annotated to a type that excludes None, since
   that would misrepresent the insufficient-sentinel contract to type checkers.

RED STATUS
----------
All tests in this file are GREEN on the current fork-point (5f44060) because the
freeze-baseline already matches. They go RED if any PR mutates run_monte_carlo's
signature. That is the desired behaviour: the test suite is the structural CI
gate, not a reviewer checklist.

FIXTURE
-------
tests/fixtures/math_engine/run_monte_carlo_signature_frozen/
    run_monte_carlo_signature_baseline.json
"""

from __future__ import annotations

import inspect
import json
import pathlib

import math_engine

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math_engine"
    / "run_monte_carlo_signature_frozen"
)


def _load_fixture() -> dict:
    with (FIXTURE_DIR / "run_monte_carlo_signature_baseline.json").open(
        "r", encoding="utf-8"
    ) as fh:
        return json.load(fh)


def _live_sig() -> inspect.Signature:
    return inspect.signature(math_engine.run_monte_carlo)


# ---------------------------------------------------------------------------
# 1. Parameter names are frozen
# ---------------------------------------------------------------------------


def test_run_monte_carlo_parameter_names_match_frozen_baseline() -> None:
    """
    The parameter name set must equal the baseline exactly. Any add or rename
    fails. Removing a parameter also fails (via count test below and set diff).

    RED trigger: ``run_monte_carlo(holdings, ..., extra_param=None)`` added.
    """
    fx = _load_fixture()
    frozen_names = {p["name"] for p in fx["frozen_parameters"]}
    live_names = set(_live_sig().parameters.keys())

    added = live_names - frozen_names
    removed = frozen_names - live_names

    assert not added and not removed, (
        f"run_monte_carlo signature has drifted from the Phase-1 freeze baseline.\n"
        f"  Added parameters:   {sorted(added)}\n"
        f"  Removed parameters: {sorted(removed)}\n"
        f"Signature changes are BLOCKED until the last-symphony Phase-2 cutover "
        f"ceremony (plan §6 H7). Any parameter change touches 7+ consumer sites."
    )


# ---------------------------------------------------------------------------
# 2. Parameter count is frozen
# ---------------------------------------------------------------------------


def test_run_monte_carlo_parameter_count_matches_frozen_baseline() -> None:
    """
    An extra keyword-only parameter raises the count; a removal lowers it.
    Both are caught here as a fast, unambiguous signal before the name-diff test.

    RED trigger: any parameter added, even a keyword-only one.
    """
    fx = _load_fixture()
    frozen_count = len(fx["frozen_parameters"])
    live_count = len(_live_sig().parameters)

    assert live_count == frozen_count, (
        f"run_monte_carlo has {live_count} parameters; frozen baseline has "
        f"{frozen_count}. A count change means the signature was mutated. "
        f"Phase-1 freeze: the signature is immutable until Phase-2 cutover."
    )


# ---------------------------------------------------------------------------
# 3. Parameter kinds (POSITIONAL_OR_KEYWORD vs KEYWORD_ONLY etc.) are frozen
# ---------------------------------------------------------------------------


def test_run_monte_carlo_parameter_kinds_match_frozen_baseline() -> None:
    """
    Converting a POSITIONAL_OR_KEYWORD parameter to KEYWORD_ONLY silently breaks
    callers that pass it positionally (e.g. ``run_monte_carlo(h, d, s, 5000, 150,
    seed=42)`` works today but breaks if ``simulation_paths`` becomes keyword-only).

    RED trigger: ``def run_monte_carlo(holdings, historical_data, spy_today_return,
    *, simulation_paths=..., ...)`` (star forces keyword-only).
    """
    fx = _load_fixture()
    sig = _live_sig()

    kind_mismatches = []
    for spec in fx["frozen_parameters"]:
        param_name = spec["name"]
        if param_name not in sig.parameters:
            # Already caught by the name test; don't double-report.
            continue
        live_kind = sig.parameters[param_name].kind.name
        frozen_kind = spec["kind"]
        if live_kind != frozen_kind:
            kind_mismatches.append(f"  {param_name!r}: frozen={frozen_kind!r}, live={live_kind!r}")

    assert not kind_mismatches, (
        "run_monte_carlo parameter kinds have changed:\n"
        + "\n".join(kind_mismatches)
        + "\nChanging a parameter's kind breaks positional callers silently."
    )


# ---------------------------------------------------------------------------
# 4. Parameters that have defaults still have defaults; those without do not
# ---------------------------------------------------------------------------


def test_run_monte_carlo_default_presence_matches_frozen_baseline() -> None:
    """
    Removing a default turns an optional call into a required one, silently
    breaking all callers that relied on the default. Adding a default to a
    required parameter is less dangerous but still a contract change.

    RED trigger: removing ``simulation_paths=MC_DEFAULT_SIMULATION_PATHS``.
    """
    fx = _load_fixture()
    sig = _live_sig()

    mismatch = []
    for spec in fx["frozen_parameters"]:
        param_name = spec["name"]
        if param_name not in sig.parameters:
            continue
        param = sig.parameters[param_name]
        live_has_default = param.default is not inspect.Parameter.empty
        frozen_has_default = spec["has_default"]
        if live_has_default != frozen_has_default:
            mismatch.append(
                f"  {param_name!r}: frozen has_default={frozen_has_default}, "
                f"live has_default={live_has_default}"
            )

    assert not mismatch, "run_monte_carlo parameter default presence has changed:\n" + "\n".join(
        mismatch
    )


# ---------------------------------------------------------------------------
# 5. seed default is None (the only concrete default value we pin)
# ---------------------------------------------------------------------------


def test_run_monte_carlo_seed_default_is_none() -> None:
    """
    ``seed``'s default of None is semantically load-bearing: callers that omit
    ``seed`` get a non-deterministic run (desired for live cycles). A default of
    0 or any integer would silently make all unseeded calls deterministic and
    correlated.

    RED trigger: ``def run_monte_carlo(..., seed: int | None = 0)``.
    """
    sig = _live_sig()
    assert "seed" in sig.parameters, (
        "run_monte_carlo has no 'seed' parameter — the signature baseline is violated."
    )
    seed_default = sig.parameters["seed"].default
    assert seed_default is None, (
        f"run_monte_carlo seed default is {seed_default!r}; must be None. "
        f"Any non-None default makes unseeded live cycles deterministic, "
        f"introducing correlation across the 98k annual execution cycles."
    )


# ---------------------------------------------------------------------------
# 6. Return-type annotation does not exclude None
# ---------------------------------------------------------------------------


def test_run_monte_carlo_return_annotation_does_not_exclude_none() -> None:
    """
    If a future PR annotates the return type as ``float`` (excluding None),
    type checkers will flag every ``if prob_underperforming is None`` guard as dead code
    and may be silently removed by automated refactors. The insufficient sentinel
    is None by contract (MC_INSUFFICIENT_HISTORY_SENTINEL = None).

    Acceptable states: unannotated (no return annotation) or annotated with a
    type that is a union including None (e.g. ``float | None``).

    RED trigger: ``def run_monte_carlo(...) -> float:`` (annotation excludes None).
    """
    sig = _live_sig()
    ret = sig.return_annotation

    if ret is inspect.Parameter.empty:
        # Unannotated — acceptable; no annotation is not a false exclusion.
        return

    # If annotated, the annotation must admit None.
    # We check via __args__ on a Union/Optional, or direct None comparison.
    annotation_str = str(ret)
    # Acceptable forms: float | None, Optional[float], Union[float, None],
    # the runtime type 'float | None', etc. All contain 'None'.
    assert "None" in annotation_str, (
        f"run_monte_carlo is annotated -> {ret!r}. This annotation excludes None, "
        f"which misrepresents the out-of-band insufficient-history sentinel "
        f"(MC_INSUFFICIENT_HISTORY_SENTINEL = None). Type checkers will treat every "
        f"'if prob_underperforming is None' guard as dead code. The annotation must admit "
        f"None or be removed."
    )
