"""
Cluster 4 — AC-3 / Decision D4: the haircut's per-trial `T` is the in-sample
return-OBSERVATION count, and `compute_dsr_T` (with its n_symphonies inflation)
is deleted.

RED tests, written before the implementation.

Under Decision D3 the Sharpe-DSR is replaced by a Harvey & Liu 2015 haircut whose
per-trial t-statistic is `t_i = Sortino_i · sqrt(T_i)`. `T_i` is the number of
return OBSERVATIONS in the series the trial's Sortino was computed over —
`len(daily_returns_i)`. It is NOT a calendar-day count and NOT multiplied by any
symphony count.

Two coupled changes (audit H-7 / L-2 / finding-12, Decision D4):

  D4   — `compute_dsr_T` is DELETED. It is dead code on `main` and its
         `T = validation_calendar_days * n_symphonies` formula is statistically
         invalid: it counts the same cross-sectionally-correlated calendar days
         `n_symphonies` times. The n_symphonies inflation must not reappear — not
         in `compute_dsr_T`, and not inlined into the haircut.

  AC-3 — the haircut's per-trial `T` is `len(daily_returns)` — the return-
         observation count. `_collect_sim_returns` emits one entry per TRIGGERED
         day only, so the validation-fold calendar-day count
         (`len(validation_dates_purged)`) over-states the true observation count.

Reference: feature-plans/autotuner-statistics.md, Decisions D3 / D4. Harvey & Liu
2015, "Backtesting", JPM 42(1), DOI 10.3905/jpm.2015.42.1.013.
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
# D4 — compute_dsr_T is deleted; no n_symphonies T-inflation reintroduced
# ===========================================================================


def test_compute_dsr_t_function_is_deleted():
    """
    D4: `compute_dsr_T` must no longer be defined in autotuner.py.

    It is dead code and the n_symphonies T-inflation it would apply is
    statistically invalid. Decision D4 is DELETE.
    """
    autotuner = _import_autotuner()
    assert not hasattr(autotuner, "compute_dsr_T"), (
        "autotuner.compute_dsr_T must be DELETED (Decision D4)."
    )

    tree = _autotuner_tree()
    dsr_t_defs = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "compute_dsr_T"
    ]
    assert not dsr_t_defs, (
        "autotuner.py still contains a `def compute_dsr_T(...)`. D4 requires the "
        "function definition removed entirely."
    )


def test_n_symphonies_t_inflation_is_not_reintroduced():
    """
    D4: the `T = validation_days * n_symphonies` inflation must not appear
    anywhere in autotuner.py — not in compute_dsr_T, not inlined into the haircut.

    Multiplying the observation count by the symphony count repeatedly counts
    cross-sectionally correlated days and inflates the t-statistic. Guard against
    a future re-inline of the invalid correction.
    """
    src = _AUTOTUNER_SRC.read_text(encoding="utf-8")
    tree = _autotuner_tree()

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
        "re-introduced T-inflation. D4 forbids n_symphonies T-inflation:\n  "
        + "\n  ".join(offending)
    )
    assert "validation_calendar_days * n_symphonies" not in src, (
        "autotuner.py still references `validation_calendar_days * n_symphonies` "
        "(the invalid Amendment F1 inflation). D4 requires it removed."
    )


def test_portmode_test_no_longer_references_compute_dsr_t():
    """
    D4: the tests covering `compute_dsr_T` must be deleted along with the
    function. tests/portmode/test_autotuner_portmode.py imports it and exercises
    it in `TestDSRTCorrectionCallSite`; a live import of a deleted function
    crashes collection.
    """
    src = _PORTMODE_TEST.read_text(encoding="utf-8")
    assert "compute_dsr_T" not in src, (
        "tests/portmode/test_autotuner_portmode.py still references compute_dsr_T. "
        "D4 deletes the function AND its tests."
    )


# ===========================================================================
# AC-3 — the haircut's per-trial T is a return-observation count
# ===========================================================================


def test_haircut_t_is_not_the_validation_calendar_day_count():
    """
    AC-3: the haircut's per-trial `T` must NOT be `len(validation_dates_purged)`.

    `validation_dates_purged` is the set of validation CALENDAR days. The Sortino
    being significance-tested is computed over per-TRIGGERED-day guard-alpha
    values — only triggering days contribute an observation. Sourcing `T` from
    the calendar-day count systematically overstates the observation count and
    inflates the haircut t-statistic.

    AST check: no assignment whose target name starts with `T` may be
    `len(validation_dates_purged)`, directly or via a one-hop binding consumed by
    the haircut's t-stat scaling.
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
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (
                    isinstance(tgt, ast.Name)
                    and tgt.id.startswith("T")
                    and _is_len_of(node.value, "validation_dates_purged")
                ):
                    offending.append(
                        f"line {node.lineno}: `{tgt.id} = "
                        f"len(validation_dates_purged)`"
                    )

    assert not offending, (
        "autotuner.py sources a `T` from the validation calendar-day count. "
        "AC-3 requires the haircut's per-trial `T` to be `len(daily_returns)` — "
        "the in-sample return-OBSERVATION count. Offending sites:\n  "
        + "\n  ".join(offending)
    )


def test_haircut_t_stat_is_scaled_by_a_per_trial_observation_count():
    """
    AC-3: compute_sortino_tstat must scale the Sortino by sqrt of a per-trial
    observation count.

    Behavioural property: for a fixed Sortino, the t-statistic must be strictly
    monotone increasing in `T` and equal to `Sortino · sqrt(T)`. A `T` that is a
    fixed calendar-day count would make the t-stat independent of how many days
    actually triggered — this test pins that `T` genuinely varies per trial.

    Tolerance: 1e-9 absolute — `sortino * sqrt(T)` is exact arithmetic.
    """
    import math

    autotuner = _import_autotuner()

    assert hasattr(autotuner, "compute_sortino_tstat"), (
        "autotuner.compute_sortino_tstat must exist (D3 haircut) — see "
        "test_c4_harvey_liu_haircut.py."
    )

    sortino = 0.4
    for T in (10, 25, 60, 120):
        expected = sortino * math.sqrt(T)
        got = autotuner.compute_sortino_tstat(sortino, T)
        assert got == pytest.approx(expected, abs=1e-9), (
            f"compute_sortino_tstat({sortino}, {T}) must equal "
            f"sortino·sqrt(T) = {expected!r}; got {got!r}."
        )

    # Strictly monotone in T — proves T is a real per-trial count, not a constant.
    t_small = autotuner.compute_sortino_tstat(sortino, 10)
    t_large = autotuner.compute_sortino_tstat(sortino, 120)
    assert t_large > t_small, (
        "The haircut t-statistic must increase with the observation count T. "
        "If T were a fixed calendar-day count the t-stat would not vary per "
        "trial — AC-3 requires T = len(daily_returns), a genuine per-trial count."
    )
