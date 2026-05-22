"""
Cluster 4 — AC-2 / Decision D3: the autotuner selection-bias correction is a
Harvey & Liu 2015 Benjamini-Hochberg-Yekutieli (BHY) multiple-testing haircut
applied to the Sortino — NOT a Sortino fed into the Sharpe-derived DSR Eq. 9.

RED tests, written before the implementation.

Decision D3 (resolved 2026-05-22, feature-plans/autotuner-statistics.md): the
Sharpe-derived Deflated Sharpe Ratio (Bailey & López de Prado 2014 Eq. 9) is
REMOVED from the autotuner selection path. Eq. 9's denominator is the asymptotic
standard error of the SHARPE estimator; a Sortino's sampling distribution is
different and Eq. 9 does not describe it (audit M-3). It is replaced by a Harvey
& Liu 2015 multiple-testing haircut — metric-agnostic, it haircuts a p-value for
the number of tests tried.

Finding H1 (risk-engine-specialist, team-lead-confirmed Option A, 2026-05-22):
the multiple-testing adjustment is Benjamini-Hochberg-YEKUTIELI (BHY) — Benjamini,
Hochberg & Yekutieli (2001), Annals of Statistics 29(4) — NOT plain
Benjamini-Hochberg 1995. BHY adds the Yekutieli arbitrary-dependence correction
factor c(N) = sum_{j=1}^{N} 1/j (the N-th harmonic number). Harvey & Liu 2015
prescribe BHY precisely because strategy test statistics are dependent (Optuna
TPE concentrates the search, so the trial Sortinos are strongly dependent — not
independent). Plain BH-1995's FDR guarantee needs independence/PRDS, which does
not hold here; it under-corrects by ~c(N)x (c(500) ≈ 6.79).

The haircut pipeline, per trial i over the N sentinel-filtered completed trials:
  1. t-statistic:  t_i  = Sortino_i · sqrt(T_i),  T_i = len(daily_returns_i)
  2. one-sided p:  p_i  = 1 − Φ(t_i)              (large-sample normal survival)
  3. BHY adjust:   p_adj_(k) = min_{j>=k} [ (N·c(N)/j) · p_(j) ], clamp [0,1]
  4. selection:    deploy argmin p_adj, gated by p_adj <= HARVEY_LIU_FDR_Q;
                   if no trial clears the gate the AI proposal falls through.

THE CONTRACT this test pins (the implementer must expose these names; if the
implementer's names differ, reconcile here — but the pure functions must exist so
the haircut is unit-testable, not buried inside run_autotuner):
  - autotuner.compute_sortino_tstat(sortino: float, T: int) -> float
  - autotuner.benjamini_hochberg_adjust(p_values: list[float]) -> list[float]
    (the BHY step-up with the c(N) factor — the function name is kept for
    continuity; the procedure it implements is BHY-2001, not BH-1995)
  - autotuner.HARVEY_LIU_FDR_Q : float  (named FDR policy-dial constant)

Fixture provenance: tests/fixtures/math/harvey_liu_haircut.json — every t / p /
p_adj is hand-derived from the Harvey & Liu BHY formula (scipy.stats.norm.cdf for
Φ, the BHY step-up with the c(N) factor by hand). No producer output is pinned.
Tolerance 1e-9 absolute: the pipeline is deterministic; larger error is a wrong
formula.

Reference: Harvey, C.R. & Liu, Y. (2015). "Backtesting." Journal of Portfolio
Management 42(1), 13-28. DOI 10.3905/jpm.2015.42.1.013. Benjamini, Y., Hochberg,
Y. & Yekutieli, D. (2001). "The Control of the False Discovery Rate in Multiple
Testing under Dependency." Annals of Statistics 29(4), 1165-1188.
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_FIXTURE = _WORKTREE_ROOT / "tests" / "fixtures" / "math" / "harvey_liu_haircut.json"

# Tolerance for the deterministic BHY pipeline. 1e-9 absolute: scipy norm.cdf is
# ~15 sig figs over this argument range and the BHY step-up is exact arithmetic;
# anything larger indicates a wrong formula, not floating-point rounding.
_TOL = 1e-9


def _import_autotuner():
    import autotuner
    return autotuner


def _load_fixture() -> dict:
    with open(str(_FIXTURE), encoding="utf-8") as f:
        return json.load(f)


# ===========================================================================
# AC-2 — the t-statistic mapping: Sortino -> Sortino · sqrt(T)
# ===========================================================================


def test_compute_sortino_tstat_exists():
    """
    AC-2: autotuner must expose a pure `compute_sortino_tstat(sortino, T)`.

    The haircut needs a per-trial test statistic. The metric-neutral bridge from a
    ratio to a significance statistic is `t = ratio · sqrt(T)` — a per-observation
    effect size scaled by the sample length. It must be a named pure function so
    it is unit-testable, not inlined in run_autotuner.
    """
    autotuner = _import_autotuner()
    assert hasattr(autotuner, "compute_sortino_tstat"), (
        "autotuner must expose compute_sortino_tstat(sortino, T) — the "
        "Sortino -> t-statistic bridge for the Harvey & Liu haircut (D3). "
        "Not implemented yet (expected RED)."
    )


@pytest.mark.parametrize("scenario_key", [
    "mixed_one_clear_winner",
    "noise_only_none_clears",
    "one_signal_among_noise",
])
def test_sortino_tstat_matches_fixture(scenario_key):
    """
    AC-2: compute_sortino_tstat(sortino, T) must equal Sortino · sqrt(T).

    Pinned against the hand-derived fixture t-stats for every trial in each
    scenario. Tolerance 1e-9 absolute.
    """
    autotuner = _import_autotuner()
    scenario = _load_fixture()["scenarios"][scenario_key]

    trials = scenario["trials"]
    expected_t = scenario["expected"]["t_stat"]

    for trial, exp in zip(trials, expected_t):
        result = autotuner.compute_sortino_tstat(trial["sortino"], trial["T"])
        assert result == pytest.approx(exp, abs=_TOL), (
            f"compute_sortino_tstat mismatch for trial '{trial['label']}' "
            f"({scenario_key}).\n"
            f"  sortino={trial['sortino']}, T={trial['T']}\n"
            f"  expected t = sortino·sqrt(T) = {exp!r}\n"
            f"  got = {result!r}"
        )


# ===========================================================================
# AC-2 — the Benjamini-Hochberg step-up adjustment
# ===========================================================================


def test_benjamini_hochberg_adjust_exists():
    """AC-2: autotuner must expose `benjamini_hochberg_adjust(p_values)`."""
    autotuner = _import_autotuner()
    assert hasattr(autotuner, "benjamini_hochberg_adjust"), (
        "autotuner must expose benjamini_hochberg_adjust(p_values) — the BHY "
        "step-up multiple-testing adjustment (Harvey & Liu 2015, D3). "
        "Not implemented yet (expected RED)."
    )


def test_benjamini_hochberg_adjust_applies_the_bhy_dependency_factor():
    """
    AC-2 / Finding H1: benjamini_hochberg_adjust must implement BHY (Benjamini-
    Hochberg-Yekutieli 2001), NOT plain Benjamini-Hochberg 1995.

    BHY multiplies the BH step-up by the Yekutieli arbitrary-dependence factor
    c(N) = sum_{j=1}^{N} 1/j (the N-th harmonic number):
        p_adj_(k) = min_{j>=k} [ (N·c(N)/j) · p_(j) ],  clamp [0,1].
    Plain BH-1995 omits the c(N) factor and under-corrects by exactly c(N)x.

    This test uses the fixture's `bh_vs_bhy_discriminator` — a 3-trial p-value
    vector chosen so the two procedures differ by exactly c(3)=1.8333... (no
    clamping). The implementation's output must equal the BHY vector and must
    NOT equal the plain-BH-1995 vector.

    Harvey & Liu 2015 prescribe BHY because strategy test statistics are
    dependent (TPE concentrates the search). Tolerance 1e-9 absolute.
    """
    autotuner = _import_autotuner()
    disc = _load_fixture()["bh_vs_bhy_discriminator"]

    raw_p = disc["raw_p_values"]
    bhy_expected = disc["expected"]["bhy_p_adj"]
    bh_1995 = disc["expected"]["plain_bh_1995_p_adj"]

    result = autotuner.benjamini_hochberg_adjust(list(raw_p))

    assert len(result) == len(bhy_expected), (
        f"benjamini_hochberg_adjust returned {len(result)} values for a "
        f"{len(raw_p)}-element input."
    )
    for i, (got, bhy_exp, bh_exp) in enumerate(zip(result, bhy_expected, bh_1995)):
        assert got == pytest.approx(bhy_exp, abs=_TOL), (
            f"benjamini_hochberg_adjust must apply the BHY c(N) harmonic-number "
            f"dependency factor (finding H1).\n"
            f"  index {i}: raw p = {raw_p[i]!r}\n"
            f"  expected BHY p_adj   = {bhy_exp!r}  (BH-1995 × c(3)=1.8333...)\n"
            f"  plain BH-1995 p_adj  = {bh_exp!r}  (the WRONG, under-correcting value)\n"
            f"  got = {got!r}\n"
            f"BHY: p_adj_(k) = min_(j>=k) [ N·c(N)/j · p_(j) ]. Harvey & Liu 2015 "
            f"prescribe BHY because the trial statistics are dependent."
        )
        # The got value must NOT match plain BH-1995 (the discriminator is
        # constructed so the two procedures are distinguishable).
        assert got != pytest.approx(bh_exp, abs=_TOL), (
            f"index {i}: benjamini_hochberg_adjust produced the plain BH-1995 "
            f"value {bh_exp!r} — the c(N) Yekutieli dependency factor is missing."
        )


@pytest.mark.parametrize("scenario_key", [
    "mixed_one_clear_winner",
    "noise_only_none_clears",
    "one_signal_among_noise",
])
def test_benjamini_hochberg_adjust_matches_fixture(scenario_key):
    """
    AC-2: benjamini_hochberg_adjust must implement the BHY step-up exactly.

    BHY step-up: sort p ascending, p_adj_(k) = min over j>=k of
    [ N·c(N)/j · p_(j) ] where c(N) = sum_{j=1}^{N} 1/j, clamp to [0,1], map
    back to the input order. Pinned against the hand-derived fixture p_adj
    vector. The input raw p-values are themselves derived from the fixture's
    hand-computed Φ survival values, so this test is end-to-end against the
    literature formula, not the producer.

    Tolerance 1e-9 absolute.
    """
    autotuner = _import_autotuner()
    scenario = _load_fixture()["scenarios"][scenario_key]

    raw_p = scenario["expected"]["raw_p"]
    expected_p_adj = scenario["expected"]["p_adj"]

    result = autotuner.benjamini_hochberg_adjust(list(raw_p))

    assert len(result) == len(expected_p_adj), (
        f"benjamini_hochberg_adjust must return one adjusted p per input p "
        f"({scenario_key}): expected {len(expected_p_adj)}, got {len(result)}."
    )
    for i, (got, exp) in enumerate(zip(result, expected_p_adj)):
        assert got == pytest.approx(exp, abs=_TOL), (
            f"BHY adjusted p-value mismatch at index {i} "
            f"(trial '{scenario['trials'][i]['label']}', {scenario_key}).\n"
            f"  raw p = {raw_p[i]!r}\n"
            f"  expected p_adj = {exp!r}\n"
            f"  got = {got!r}\n"
            f"  BHY step-up: p_adj_(k) = min_(j>=k) [ N·c(N)/j · p_(j) ], "
            f"clamp [0,1]; c(N) = sum_(j=1..N) 1/j."
        )


def test_benjamini_hochberg_adjust_results_are_in_unit_interval():
    """
    AC-2 property: every BHY-adjusted p-value must lie in [0, 1].

    The step-up multiplies by N·c(N)/j (which readily exceeds 1, especially with
    the c(N) dependency inflation) so the clamp to 1.0 is load-bearing. A p_adj
    outside [0,1] is not a probability and would corrupt the q-gate comparison.
    """
    autotuner = _import_autotuner()

    for scenario_key in ("mixed_one_clear_winner", "noise_only_none_clears",
                         "one_signal_among_noise"):
        raw_p = _load_fixture()["scenarios"][scenario_key]["expected"]["raw_p"]
        result = autotuner.benjamini_hochberg_adjust(list(raw_p))
        for i, p in enumerate(result):
            assert 0.0 <= p <= 1.0, (
                f"BHY adjusted p-value out of [0,1] at index {i} ({scenario_key}): "
                f"{p!r}. The N·c(N)/j step-up factor must be clamped to 1.0."
            )


def test_benjamini_hochberg_adjust_is_monotone_in_rank():
    """
    AC-2 property: BHY adjusted p-values, taken in ascending raw-p order, must be
    non-decreasing — the defining monotonicity of the step-up procedure.

    A trial with a smaller raw p can never receive a larger p_adj than a trial
    with a larger raw p. If this fails the step-up's running-minimum is wrong.
    """
    autotuner = _import_autotuner()

    raw_p = _load_fixture()["scenarios"]["mixed_one_clear_winner"]["expected"]["raw_p"]
    p_adj = autotuner.benjamini_hochberg_adjust(list(raw_p))

    paired = sorted(zip(raw_p, p_adj), key=lambda t: t[0])
    adj_in_rank_order = [a for _, a in paired]
    for i in range(len(adj_in_rank_order) - 1):
        assert adj_in_rank_order[i] <= adj_in_rank_order[i + 1] + _TOL, (
            f"BHY monotonicity violated: adjusted p-values in ascending raw-p "
            f"order must be non-decreasing. Index {i}: "
            f"{adj_in_rank_order[i]!r} > {adj_in_rank_order[i + 1]!r}."
        )


# ===========================================================================
# AC-2 — the FDR level is a named constant
# ===========================================================================


def test_harvey_liu_fdr_q_is_a_named_constant():
    """
    AC-2: the BHY false-discovery-rate level must be a named module constant with
    a sourced rationale comment (project no-magic-numbers rule).

    The value is a policy dial (conventional 0.05). This test pins that the
    constant EXISTS and is a probability in (0, 1) — it does not pin the exact
    value (that would hardcode a policy choice).
    """
    autotuner = _import_autotuner()

    assert hasattr(autotuner, "HARVEY_LIU_FDR_Q"), (
        "autotuner must define a named HARVEY_LIU_FDR_Q constant — the "
        "Benjamini-Hochberg FDR level for the Harvey & Liu haircut. A bare 0.05 "
        "literal violates the no-magic-numbers rule."
    )
    q = autotuner.HARVEY_LIU_FDR_Q
    assert isinstance(q, float) and 0.0 < q < 1.0, (
        f"HARVEY_LIU_FDR_Q must be a probability in (0, 1); got {q!r}."
    )


def test_harvey_liu_fdr_q_has_sourced_comment():
    """
    AC-2: the HARVEY_LIU_FDR_Q constant must carry a Harvey & Liu 2015 source
    citation in autotuner.py.
    """
    src = (_WORKTREE_ROOT / "autotuner.py").read_text(encoding="utf-8")
    assert "HARVEY_LIU_FDR_Q" in src, "HARVEY_LIU_FDR_Q not defined in autotuner.py."
    assert "Harvey" in src and "Liu" in src and "2015" in src, (
        "autotuner.py must cite Harvey & Liu 2015 near the haircut constants — "
        "the FDR level and the BHY procedure are from that paper."
    )


# ===========================================================================
# AC-2 — the haircut is a real selection GATE (overfitting protection)
# ===========================================================================


def test_haircut_rejects_an_all_noise_trial_set():
    """
    AC-2 / overfitting protection: a trial set of pure-noise Sortinos must be
    REJECTED IN FULL by the haircut — no trial's BHY-adjusted p-value clears the
    FDR gate.

    This is the property the mis-specified DSR was nominally there to provide. A
    haircut that always finds a "winner" in pure noise is not protecting against
    overfitting at all. The noise fixture's Sortino values are small and
    both-signed — consistent with a zero-skill strategy's sampling variation.

    Assertion: with q = the fixture's fdr_q, NO trial in the noise scenario has
    p_adj <= q.
    """
    autotuner = _import_autotuner()
    fixture = _load_fixture()
    q = fixture["fdr_q"]
    scenario = fixture["scenarios"]["noise_only_none_clears"]

    trials = scenario["trials"]
    p_values = [
        1.0 - _normal_cdf(autotuner.compute_sortino_tstat(t["sortino"], t["T"]))
        for t in trials
    ]
    p_adj = autotuner.benjamini_hochberg_adjust(p_values)

    cleared = [trials[i]["label"] for i, p in enumerate(p_adj) if p <= q]
    assert not cleared, (
        f"OVERFITTING-PROTECTION FAILURE: the Harvey & Liu haircut let "
        f"{cleared} clear the q={q} FDR gate on a PURE-NOISE trial set. "
        f"A noise-only set must be rejected in full — otherwise the haircut is "
        f"not a real selection gate. p_adj values: {p_adj}"
    )


def test_haircut_passes_a_genuine_signal_among_noise():
    """
    AC-2 companion: the haircut must NOT be so strict it rejects everything. A
    trial set with one genuinely-skilled trial among noise must let exactly that
    trial clear the FDR gate.

    Together with test_haircut_rejects_an_all_noise_trial_set this pins the gate
    as calibrated — it rejects noise AND admits real signal.
    """
    autotuner = _import_autotuner()
    fixture = _load_fixture()
    q = fixture["fdr_q"]
    scenario = fixture["scenarios"]["one_signal_among_noise"]

    trials = scenario["trials"]
    p_values = [
        1.0 - _normal_cdf(autotuner.compute_sortino_tstat(t["sortino"], t["T"]))
        for t in trials
    ]
    p_adj = autotuner.benjamini_hochberg_adjust(p_values)

    cleared = [trials[i]["label"] for i, p in enumerate(p_adj) if p <= q]
    assert cleared == scenario["expected"]["cleared_q_05"], (
        f"The haircut must let exactly the genuine signal clear q={q}.\n"
        f"  expected cleared = {scenario['expected']['cleared_q_05']}\n"
        f"  got cleared      = {cleared}\n"
        f"  p_adj = {p_adj}"
    )

    # The winner (argmin p_adj) must be the genuine signal.
    winner_idx = min(range(len(p_adj)), key=lambda i: p_adj[i])
    assert trials[winner_idx]["label"] == scenario["expected"]["winner_label"], (
        f"argmin p_adj must select the genuine signal "
        f"'{scenario['expected']['winner_label']}'; got "
        f"'{trials[winner_idx]['label']}'."
    )


def _normal_cdf(x: float) -> float:
    """Standard-normal CDF via math.erf — the test's own reference Φ.

    Used so the noise/signal gate tests derive their p-values independently of
    scipy and of the producer. Φ(x) = 0.5·(1 + erf(x/√2)).
    """
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
