"""
Cluster 4 — AC-3 / Decision D4: DSR `T` is the in-sample return-OBSERVATION count.

RED tests, written before the implementation.

Two coupled changes, per the Cluster 4 plan (feature-plans/autotuner-statistics.md)
and audit findings H-7 / L-2 / finding-12:

  D4   — `compute_dsr_T` is DELETED. It is dead code on `main` (referenced only by
         tests/portmode/test_autotuner_portmode.py, never by run_autotuner or
         run_calibration_sweep), and the `T = validation_calendar_days * n_symphonies`
         inflation it would apply is statistically invalid: it counts the same
         cross-sectionally-correlated calendar days `n_symphonies` times, inflating
         sqrt(T-1) and overstating significance. The whole function plus its tests
         must go — no n_symphonies T-inflation is introduced anywhere.

  AC-3 — The DSR `T` is the number of return OBSERVATIONS in the in-sample series
         the Sortino was computed over: `T = len(daily_returns)`. The live code uses
         `len(validation_dates_purged)` — the calendar-day count of the validation
         fold. Only TRIGGERED days contribute an observation (`_collect_sim_returns`
         returns one entry per triggered day, not per calendar day), so the
         calendar-day count is systematically LARGER than the true observation
         count, inflating sqrt(T-1) and making the DSR anti-conservative.

These tests do NOT pin a numeric DSR value to T — `compute_deflated_sharpe_ratio`
is exercised numerically by tests/autotuner/test_o2_deflated_sharpe.py. Here the
contract under test is purely: (1) the dead function is gone, (2) `T` is sourced
from the observation count, not the calendar-day count.

Reference: Bailey & López de Prado 2014, "The Deflated Sharpe Ratio", JPM 40(5),
DOI 10.3905/jpm.2014.40.5.094 — `T` is the number of (non-overlapping) return
observations in the in-sample series.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"
_PORTMODE_TEST = _WORKTREE_ROOT / "tests" / "portmode" / "test_autotuner_portmode.py"


def _import_autotuner():
    import autotuner
    return autotuner


def _autotuner_tree() -> ast.Module:
    return ast.parse(_AUTOTUNER_SRC.read_text(encoding="utf-8"))


# ===========================================================================
# D4 — compute_dsr_T is deleted (function + tests)
# ===========================================================================


def test_compute_dsr_t_function_is_deleted():
    """
    D4: `compute_dsr_T` must no longer be defined in autotuner.py.

    It is dead code on main and the n_symphonies T-inflation it would apply is
    statistically invalid. Decision D4 is DELETE — not wire-in, not repair.
    """
    autotuner = _import_autotuner()

    assert not hasattr(autotuner, "compute_dsr_T"), (
        "autotuner.compute_dsr_T must be DELETED (Decision D4). It is dead code "
        "and its n_symphonies T-inflation double-counts correlated calendar days. "
        "The function is still present — D4 has not been applied."
    )

    # AST-level confirmation: no FunctionDef named compute_dsr_T anywhere in the module.
    tree = _autotuner_tree()
    dsr_t_defs = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "compute_dsr_T"
    ]
    assert not dsr_t_defs, (
        "autotuner.py still contains a `def compute_dsr_T(...)`. "
        "Decision D4 requires the function definition to be removed entirely."
    )


def test_n_symphonies_t_inflation_is_not_reintroduced():
    """
    D4: the `T = validation_days * n_symphonies` inflation must not appear anywhere
    in autotuner.py — not in compute_dsr_T, and not inlined into a DSR call site.

    Multiplying the day count by the symphony count counts cross-sectionally
    correlated days repeatedly and inflates sqrt(T-1). Guard against a future
    re-inline of the invalid correction.
    """
    src = _AUTOTUNER_SRC.read_text(encoding="utf-8")
    tree = _autotuner_tree()

    # Any BinOp multiplying something by a name containing "symphon" (case-insensitive)
    # is a candidate T-inflation. The honest T must never scale with the symphony count.
    offending: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            for side in (node.left, node.right):
                if isinstance(side, ast.Name) and "symphon" in side.id.lower():
                    offending.append(
                        f"line {getattr(node, 'lineno', '?')}: multiplication by "
                        f"`{side.id}`"
                    )

    assert not offending, (
        "autotuner.py contains a multiplication by a symphony-count name — a "
        "re-introduced DSR T-inflation. D4 forbids n_symphonies T-inflation:\n  "
        + "\n  ".join(offending)
    )

    # Defence-in-depth: the literal phrase from the old dead docstring must be gone.
    assert "validation_calendar_days * n_symphonies" not in src, (
        "autotuner.py still references `validation_calendar_days * n_symphonies` "
        "(the invalid Amendment F1 inflation). D4 requires it removed."
    )


def test_portmode_test_no_longer_references_compute_dsr_t():
    """
    D4: the tests covering `compute_dsr_T` must be deleted along with the function.

    tests/portmode/test_autotuner_portmode.py imports `compute_dsr_T` and exercises
    it in the `TestDSRTCorrectionCallSite` class. A deleted function with live tests
    that import it would crash the suite at collection time — the tests must go too.
    """
    src = _PORTMODE_TEST.read_text(encoding="utf-8")

    assert "compute_dsr_T" not in src, (
        "tests/portmode/test_autotuner_portmode.py still references compute_dsr_T. "
        "D4 deletes the function AND its tests — remove the TestDSRTCorrectionCallSite "
        "class and the `from autotuner import compute_dsr_T` import."
    )


# ===========================================================================
# AC-3 — DSR T = len(daily_returns), the in-sample return-observation count
# ===========================================================================


def test_dsr_t_is_not_the_validation_calendar_day_count():
    """
    AC-3: the DSR `T` must NOT be `len(validation_dates_purged)`.

    `validation_dates_purged` is the set of validation CALENDAR days. The Sortino
    being deflated is computed over per-TRIGGERED-day guard-alpha values — only
    triggering days contribute an observation. Sourcing `T` from the calendar-day
    count systematically overstates the observation count.

    Source-level AST check: no `T=` keyword argument in a `compute_deflated_sharpe_ratio`
    call may be the bare name `validation_dates_purged` wrapped in `len(...)`, and no
    `T_train`/`T_val` binding may be `len(validation_dates_purged)`.
    """
    tree = _autotuner_tree()

    def _is_len_of(node: ast.AST, target_name: str) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "len"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == target_name
        )

    offending: list[str] = []

    # (a) Any assignment `T_xxx = len(validation_dates_purged)`.
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (
                    isinstance(tgt, ast.Name)
                    and tgt.id.startswith("T")
                    and _is_len_of(node.value, "validation_dates_purged")
                ):
                    offending.append(
                        f"line {node.lineno}: `{tgt.id} = len(validation_dates_purged)`"
                    )

    # (b) Any `compute_deflated_sharpe_ratio(..., T=len(validation_dates_purged))`.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "compute_deflated_sharpe_ratio"
        ):
            for kw in node.keywords:
                if kw.arg == "T" and _is_len_of(kw.value, "validation_dates_purged"):
                    offending.append(
                        f"line {node.lineno}: DSR call passes "
                        f"`T=len(validation_dates_purged)`"
                    )

    assert not offending, (
        "autotuner.py still sources the DSR `T` from the validation calendar-day "
        "count. AC-3 requires `T = len(daily_returns)` — the in-sample return-"
        "OBSERVATION count of the series the Sortino was computed over. Offending "
        "sites:\n  " + "\n  ".join(offending)
    )


def test_dsr_t_is_sourced_from_a_return_series_length():
    """
    AC-3: the DSR re-ranking loop must pass `T` derived from a per-trial return
    SERIES length (the `daily_returns` the Sortino was computed over), not a
    calendar-day count.

    This test resolves one level of name binding: when a DSR call passes
    `T=<name>`, it finds the module/function assignment `<name> = ...` and rejects
    the call if that assignment is `len(<dates-named set>)`. So both
    `T=len(validation_dates_purged)` and the indirect
    `T_train = len(validation_dates_purged); ... T=T_train` are caught.

    It is deliberately tolerant of the implementer's exact variable name — the
    positive provenance (the bound series really is the trial's OWN return series)
    is enforced by the AC-1 moment/return-series-provenance tests. Here we pin the
    weaker but unambiguous contract: `T` must not resolve to a `*_dates_*` /
    calendar-day count.

    The fallback DSR path (variable `dsr_fallback`, <2 completed trials) is exempt
    from the loop-specific shape, but it too must not resolve to the calendar-day
    count.
    """
    tree = _autotuner_tree()

    def _is_len_of_dates(node: ast.AST) -> str | None:
        """If `node` is `len(<name containing 'dates'>)`, return that name."""
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "len"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and "dates" in node.args[0].id.lower()
        ):
            return node.args[0].id
        return None

    # Build a name -> RHS map for every simple `NAME = <expr>` assignment.
    assignments: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name):
                assignments[tgt.id] = node.value

    dsr_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "compute_deflated_sharpe_ratio"
    ]

    assert len(dsr_calls) >= 2, (
        f"Expected at least 2 compute_deflated_sharpe_ratio call sites in "
        f"autotuner.py (per-symphony + calibration sweep); found {len(dsr_calls)}."
    )

    offending: list[str] = []
    for call in dsr_calls:
        t_kwargs = [kw for kw in call.keywords if kw.arg == "T"]
        assert t_kwargs, (
            f"line {call.lineno}: compute_deflated_sharpe_ratio call has no "
            f"explicit `T=` keyword argument. AC-3 requires `T` passed explicitly "
            f"as an observation count."
        )
        t_value = t_kwargs[0].value

        # (a) Direct: T=len(<dates set>).
        direct = _is_len_of_dates(t_value)
        if direct is not None:
            offending.append(
                f"line {call.lineno}: DSR `T=len({direct})` — a calendar-day count."
            )
            continue

        # (b) Indirect: T=<name>, where <name> = len(<dates set>).
        if isinstance(t_value, ast.Name):
            rhs = assignments.get(t_value.id)
            if rhs is not None:
                via = _is_len_of_dates(rhs)
                if via is not None:
                    offending.append(
                        f"line {call.lineno}: DSR `T={t_value.id}`, bound from "
                        f"`{t_value.id} = len({via})` — a calendar-day count."
                    )

    assert not offending, (
        "autotuner.py sources the DSR `T` from a validation calendar-day count "
        "(directly or via a one-hop binding). AC-3 requires `T = len(daily_returns)` "
        "— the in-sample return-OBSERVATION count of the series the Sortino was "
        "computed over. Offending sites:\n  " + "\n  ".join(offending)
    )
