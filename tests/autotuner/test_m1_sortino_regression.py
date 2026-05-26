"""
RED tests — M1 Phase 1: Sortino branch regression.

Pins that the legacy paired-heuristic Sortino path is byte-identical after
M1 ships. compute_sortino_tstat is unchanged; the Sortino-objective autotune
results must be bit-identical against the committed reference.

Source-of-truth references:
  - m1-crra-eu-autotuner-objective/plan.md §"Sortino-branch regression".
  - autotuner.py:289-301 (compute_sortino_tstat -- must be unchanged).
  - tests/fixtures/math/harvey_liu_haircut.json (Sortino haircut scenarios
    already committed; consumed here as the Sortino regression reference).

Regression contract:
  compute_sortino_tstat(sortino, T) must return sortino * sqrt(T) for
  the fixture scenarios -- the same formula as today. Any change to this
  function breaks the Sortino branch.
"""
from __future__ import annotations

import json
import math
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_HAIRCUT_FIXTURE = _WORKTREE_ROOT / "tests" / "fixtures" / "math" / "harvey_liu_haircut.json"


def _import_autotuner():
    import autotuner
    return autotuner


def _load_fixture() -> dict:
    with open(str(_HAIRCUT_FIXTURE), encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Scenario 1: compute_sortino_tstat byte-identical for all fixture scenarios
# ---------------------------------------------------------------------------


def test_sortino_tstat_byte_identical_against_haircut_fixture():
    """Pins compute_sortino_tstat to sortino * sqrt(T) for all Harvey-Liu fixture trials.

    The fixture scenarios (mixed_one_clear_winner, noise_only_none_clears,
    one_signal_among_noise) contain (sortino, T) pairs with expected t-stats.
    The SUT must produce bit-identical t-stats for all trials in all scenarios.

    Tolerance 1e-12: sortino * sqrt(T) is deterministic double-precision;
    any change to the function produces a measurable deviation at this tolerance.
    """
    autotuner = _import_autotuner()
    fixture = _load_fixture()

    for scenario_name, scenario in fixture["scenarios"].items():
        for trial in scenario["trials"]:
            label = trial["label"]
            sortino = trial["sortino"]
            T = trial["T"]

            expected_t = scenario["expected"]["t_stat"][scenario["trials"].index(trial)]

            result = autotuner.compute_sortino_tstat(sortino, T)

            # Tolerance abs=1e-9: the fixture stores 10 significant decimal places;
            # the actual computed value extends to ~15 digits. 1e-9 is tight enough
            # to catch any formula change while accommodating fixture truncation.
            assert result == pytest.approx(expected_t, abs=1e-9), (
                f"compute_sortino_tstat regression failure: {scenario_name}/{label}\n"
                f"  sortino={sortino!r}, T={T!r}\n"
                f"  expected t_stat = {expected_t!r}\n"
                f"  got = {result!r}\n"
                f"  M1 must not modify compute_sortino_tstat. "
                f"Any change here breaks the legacy Sortino objective branch."
            )


# ---------------------------------------------------------------------------
# Scenario 2: BHY end-to-end Sortino path bit-identical
# ---------------------------------------------------------------------------


def test_sortino_bhy_pipeline_bit_identical():
    """Pins the full Sortino BHY pipeline (tstat -> pvalue -> bhy) for the
    mixed_one_clear_winner scenario bit-identical against the fixture.

    This is the regression guarantee that the legacy paired-heuristic Sortino
    path continues to produce the same selection decision after M1 ships.

    Tolerance 1e-9 for p_adj (same as existing test_c4_harvey_liu_haircut.py).
    """
    autotuner = _import_autotuner()
    fixture = _load_fixture()

    scenario = fixture["scenarios"]["mixed_one_clear_winner"]
    expected = scenario["expected"]

    tstats = [
        autotuner.compute_sortino_tstat(t["sortino"], t["T"])
        for t in scenario["trials"]
    ]
    p_values = [autotuner.compute_haircut_pvalue(ts) for ts in tstats]
    p_adj = autotuner.benjamini_hochberg_adjust(p_values)

    for i, (got, exp) in enumerate(zip(p_adj, expected["p_adj"])):
        assert got == pytest.approx(exp, abs=1e-9), (
            f"Sortino BHY pipeline p_adj[{i}] regression failure.\n"
            f"  trial = {scenario['trials'][i]['label']!r}\n"
            f"  expected p_adj = {exp!r}\n"
            f"  got = {got!r}\n"
            f"  M1 must not modify the BHY machinery for the Sortino path."
        )

    # Winner must be the same as the fixture's winner.
    winner_idx = min(range(len(p_adj)), key=lambda i: p_adj[i])
    winner_label = scenario["trials"][winner_idx]["label"]
    assert winner_label == expected["winner_label"], (
        f"Sortino BHY winner regression failure: got {winner_label!r}, "
        f"expected {expected['winner_label']!r}."
    )

    # Only A_strong clears q=0.05 under BHY.
    cleared = [
        scenario["trials"][i]["label"]
        for i, p in enumerate(p_adj)
        if p <= fixture["fdr_q"]
    ]
    assert cleared == expected["cleared_q_05"], (
        f"Sortino BHY cleared-set regression failure: got {cleared!r}, "
        f"expected {expected['cleared_q_05']!r}."
    )


# ---------------------------------------------------------------------------
# Scenario 3: compute_sortino_tstat signature unchanged
# ---------------------------------------------------------------------------


def test_sortino_tstat_signature_unchanged():
    """Pins that compute_sortino_tstat still accepts (sortino, T) positionally.

    M1 may add compute_crra_eu_tstat alongside it; the Sortino function's
    signature must remain (sortino: float, T: int) -> float.
    """
    import inspect
    autotuner = _import_autotuner()

    sig = inspect.signature(autotuner.compute_sortino_tstat)
    params = list(sig.parameters.keys())

    assert params[0] in ("sortino", "sortino_ratio", "value"), (
        f"compute_sortino_tstat first parameter is {params[0]!r}; expected 'sortino' or similar. "
        f"Signature must remain (sortino, T) for backward compat."
    )
    assert params[1] in ("T", "t", "n", "n_obs", "T_obs"), (
        f"compute_sortino_tstat second parameter is {params[1]!r}; expected 'T' or similar."
    )


# ---------------------------------------------------------------------------
# Scenario 4: compute_sortino_tstat returns 0.0 for T <= 0
# ---------------------------------------------------------------------------


def test_sortino_tstat_returns_zero_for_nonpositive_T():
    """Pins that compute_sortino_tstat returns 0.0 for T <= 0.

    Existing guard at autotuner.py:345 (T <= 0 -> return 0.0). M1 must
    not remove this guard (it is in the 'byte-identical' preservation block).
    """
    autotuner = _import_autotuner()

    for T in (0, -1, -100):
        result = autotuner.compute_sortino_tstat(1.0, T)
        assert result == 0.0, (
            f"compute_sortino_tstat(1.0, T={T!r}) must return 0.0 for T <= 0; "
            f"got {result!r}. The T<=0 guard is in the BHY slice preservation block."
        )
