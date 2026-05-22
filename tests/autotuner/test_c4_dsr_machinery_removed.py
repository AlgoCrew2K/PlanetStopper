"""
Cluster 4 — AC-1 + AC-5 + Decision D3: the Sharpe-derived DSR machinery is
REMOVED from the codebase. The selection path cannot feed a cross-trial moment
distribution into a per-strategy non-normality denominator, and cannot consume an
expected-max-Sharpe SR_0, because those functions no longer exist.

RED tests, written before the implementation.

Decision D3 (resolved 2026-05-22, feature-plans/autotuner-statistics.md) replaces
the Bailey & López de Prado 2014 Eq. 9 DSR with a Harvey & Liu 2015 BHY haircut
(see test_c4_harvey_liu_haircut.py). The team-lead ruling: after unwiring the DSR
from the selection path, grep the FULL production caller set — no callers remain
→ DELETE the functions and their dedicated tests (project no-dead-code standard;
keeping a dead function to dodge a golden-fixture obligation is itself the
forbidden hack).

CALLER-SET EVIDENCE (grep at the worktree, production *.py excluding tests):
  - compute_deflated_sharpe_ratio : defined in autotuner.py; called ONLY by the
    autotuner DSR re-ranking blocks (the code D3 removes). No other production
    caller. → DELETE.
  - compute_expected_max_sharpe   : defined in math_engine.py; called ONLY by the
    same autotuner DSR blocks. No other production caller. → DELETE.
  - compute_dsr_T                 : dead on main (D4) — unconditional DELETE.
The `deflated_sharpe` DB column / reporting.py / alpha_bot_execution.py
references are persistence + Discord surfacing of the stored VALUE — they survive
and are repurposed to carry the Harvey & Liu adjusted statistic; they are not
callers of the deleted functions.

This file folds together what were the pre-D3 AC-1 (γ3/γ4 moment provenance) and
AC-5 (null-mean SR_0) ACs: under D3 there is no Eq. 9 denominator and no SR_0, so
both collapse to "the removed Sharpe machinery cannot recur on the selection
path."

Reference: feature-plans/autotuner-statistics.md, Decisions D3 / D4.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"
_MATH_ENGINE_SRC = _WORKTREE_ROOT / "math_engine.py"


def _import_autotuner():
    import autotuner
    return autotuner


def _import_math_engine():
    import math_engine
    return math_engine


# ===========================================================================
# D3 — the three Sharpe-DSR functions are deleted
# ===========================================================================


def test_compute_deflated_sharpe_ratio_is_deleted():
    """
    D3 / AC-2: `compute_deflated_sharpe_ratio` (the Bailey & López de Prado Eq. 9
    DSR) must be removed entirely — it has no production caller after the haircut
    replaces it.
    """
    autotuner = _import_autotuner()
    assert not hasattr(autotuner, "compute_deflated_sharpe_ratio"), (
        "autotuner.compute_deflated_sharpe_ratio must be DELETED (Decision D3). "
        "The Sharpe-derived DSR Eq. 9 is replaced by the Harvey & Liu haircut; "
        "no production caller remains, so the dead function must go."
    )


def test_compute_expected_max_sharpe_is_deleted():
    """
    D3 / AC-5: `compute_expected_max_sharpe` (the DSR SR_0 expected-max term) must
    be removed — the BHY multiple-testing adjustment IS the selection-bias
    correction, so there is no SR_0 term, and the function has no other
    production caller.
    """
    me = _import_math_engine()
    assert not hasattr(me, "compute_expected_max_sharpe"), (
        "math_engine.compute_expected_max_sharpe must be DELETED (Decision D3). "
        "The Harvey & Liu haircut needs no expected-max-Sharpe SR_0 term, and "
        "the function has no other production caller — dead code must go."
    )


def test_compute_dsr_t_is_deleted():
    """D4: `compute_dsr_T` (dead on main) must be removed — unconditional."""
    autotuner = _import_autotuner()
    assert not hasattr(autotuner, "compute_dsr_T"), (
        "autotuner.compute_dsr_T must be DELETED (Decision D4)."
    )


def test_gamma_euler_mascheroni_constant_is_removed():
    """
    D3: `_GAMMA_EULER_MASCHERONI` exists ONLY for compute_expected_max_sharpe.
    When that function is deleted the constant becomes dead and must go too
    (no-magic-numbers cuts both ways — an unused named constant is still dead
    code).
    """
    me = _import_math_engine()
    assert not hasattr(me, "_GAMMA_EULER_MASCHERONI"), (
        "math_engine._GAMMA_EULER_MASCHERONI must be DELETED with "
        "compute_expected_max_sharpe — it has no other use (Decision D3)."
    )


# ===========================================================================
# D3 — the Sharpe-DSR machinery does not recur anywhere on the selection path
# ===========================================================================


def test_no_eq9_dsr_denominator_recurs_in_autotuner():
    """
    AC-1/AC-2: the Eq. 9 DSR denominator `sqrt(1 - γ3·SR + (γ4-1)/4·SR²)` must
    not recur anywhere in autotuner.py — feeding a Sortino through it is the H-6
    category error D3 eliminates.

    Heuristic source check: the autotuner source must contain no `gamma3` /
    `gamma4` identifier (the Eq. 9 non-normality moments) and no "Eq. 9" / "Eq.9"
    citation. The Harvey & Liu haircut has no such term — its presence would mean
    the Sharpe-DSR machinery was re-introduced.
    """
    src = _AUTOTUNER_SRC.read_text(encoding="utf-8")
    lowered = src.lower()

    for token in ("gamma3", "gamma4"):
        assert token not in lowered, (
            f"autotuner.py still references `{token}` — a Bailey & López de "
            f"Prado Eq. 9 non-normality moment. D3 removes the Sharpe-DSR; the "
            f"Harvey & Liu haircut has no γ3/γ4 term."
        )
    assert "eq. 9" not in lowered and "eq.9" not in lowered, (
        "autotuner.py still cites 'Eq. 9' — the Sharpe-DSR formula D3 removes."
    )


def test_cross_trial_moments_not_fed_into_a_non_normality_denominator():
    """
    AC-1: the cross-trial Sortino distribution (`trial_values`) must not be fed
    into any per-strategy non-normality / deflation denominator.

    The pre-D3 bug (audit H-5) computed γ3/γ4 from `trial_values` and passed them
    into the Eq. 9 denominator — a category error. Under D3 the haircut consumes
    `trial_values` only to count N (the number of tests) and to derive each
    trial's p-value; there is no non-normality denominator at all.

    AST check: no `**3` / `**4` power expression may iterate over `trial_values`
    — that pattern is the skewness / kurtosis moment computation, which D3 deletes.
    """
    tree = ast.parse(_AUTOTUNER_SRC.read_text(encoding="utf-8"))

    offending: list[str] = []
    for node in ast.walk(tree):
        # A skew/kurtosis moment is sum((v - mean)**3 ...) over trial_values.
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            exponent = node.right
            if isinstance(exponent, ast.Constant) and exponent.value in (3, 4):
                # Is this power expression inside a comprehension over trial_values?
                for ancestor in ast.walk(tree):
                    if isinstance(ancestor, (ast.GeneratorExp, ast.ListComp)):
                        for gen in ancestor.generators:
                            if (
                                isinstance(gen.iter, ast.Name)
                                and gen.iter.id == "trial_values"
                                and _contains(ancestor, node)
                            ):
                                offending.append(
                                    f"line {getattr(node, 'lineno', '?')}: "
                                    f"**{exponent.value} power over trial_values"
                                )

    assert not offending, (
        "autotuner.py computes a 3rd/4th-moment (skew/kurtosis) over "
        "`trial_values` — the cross-trial score distribution. AC-1 (audit H-5): "
        "the cross-trial moments must not feed a non-normality denominator. The "
        "Harvey & Liu haircut has no such term. Offending:\n  "
        + "\n  ".join(offending)
    )


def _contains(parent: ast.AST, target: ast.AST) -> bool:
    """True if `target` is a descendant of `parent`."""
    return any(child is target for child in ast.walk(parent))


# ===========================================================================
# D3 — the obsolete DSR characterization tests are removed
# ===========================================================================


@pytest.mark.parametrize("obsolete_test", [
    "test_o2_deflated_sharpe.py",
    "test_r5_dsr_expected_max_sharpe.py",
    "test_r6_sortino_sentinel_trim.py",
])
def test_obsolete_dsr_test_files_are_removed(obsolete_test):
    """
    D3: the test files that pin the OLD (Sharpe-DSR) behaviour must be deleted —
    they exercise `compute_deflated_sharpe_ratio` / `compute_expected_max_sharpe`,
    which D3 removes. Keeping them would crash collection (import of a deleted
    symbol) and would regression-pin the discarded mis-specified statistic.

    - test_o2_deflated_sharpe.py     : pins the Eq. 9 DSR formula + DSR re-ranking.
    - test_r5_dsr_expected_max_sharpe.py : pins compute_expected_max_sharpe / SR_0.
    - test_r6_sortino_sentinel_trim.py   : pins sentinel-trimming of the moments /
      SR_0 fed to the deleted DSR machinery.

    The Harvey & Liu haircut's own coverage lives in test_c4_harvey_liu_haircut.py.
    """
    path = _WORKTREE_ROOT / "tests" / "autotuner" / obsolete_test
    assert not path.exists(), (
        f"tests/autotuner/{obsolete_test} still exists. D3 removes the "
        f"Sharpe-DSR machinery it pins; this characterization file must be "
        f"deleted (it would crash collection importing a deleted symbol, and "
        f"regression-pins the discarded statistic)."
    )


def test_portmode_test_no_longer_references_deleted_dsr_symbols():
    """
    D3 / D4: tests/portmode/test_autotuner_portmode.py must not reference any of
    the deleted symbols (`compute_dsr_T`, `compute_deflated_sharpe_ratio`,
    `compute_expected_max_sharpe`) — a live import of a deleted symbol crashes
    collection of the whole portmode suite.
    """
    src = (
        _WORKTREE_ROOT / "tests" / "portmode" / "test_autotuner_portmode.py"
    ).read_text(encoding="utf-8")

    for symbol in ("compute_dsr_T", "compute_deflated_sharpe_ratio",
                   "compute_expected_max_sharpe"):
        assert symbol not in src, (
            f"tests/portmode/test_autotuner_portmode.py still references "
            f"`{symbol}` — a D3-deleted symbol. Remove the import and its tests."
        )
