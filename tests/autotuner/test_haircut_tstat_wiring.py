"""
RED tests — H-1 audit finding: _haircut_select ignores its tstat_fn parameter
and also violates the CRRA-001 U-transform contract.

Audit finding H-1 (autotuner.py:1251):
  _haircut_select accepts a tstat_fn parameter. Its docstring (:1203-1207) says
  swapping tstat_fn is 'the ONLY permitted change'. The call site passes
  tstat_fn=compute_crra_eu_tstat for the canonical CRRA bundle (:1953-1954,
  :1976-1977). BUT the loop hardcodes compute_sortino_tstat(series, seed=trial_idx)
  at :1251 and never calls tstat_fn. Result: CRRA-EU bundles get their overfitting
  haircut scored by the Sortino t-stat, not the CRRA-EU one — live on the
  canonical path.

CRRA-001 sub-finding (autotuner.py:1238-1251, test_audit_fix_crra_neff_arch.py):
  Even if tstat_fn were called, the raw percent daily_returns cannot be passed
  directly to compute_crra_eu_tstat. That function expects U-values (utility-
  transformed). The CRRA branch must U-transform via:
    W = max(WEALTH_ARG_FLOOR, 1 + r / RETURN_PCT_TO_FRACTION)
    U = compute_crra_utility(W, gamma)
  then pass the U_series to tstat_fn.

  Reference: autotuner.py:1855-1859 (T5 provenance contract — daily_returns
  are stored in RAW PERCENT), autotuner.py:1238-1243 (_crra_gamma is computed
  but unused), test_audit_fix_crra_neff_arch.py (CRRA-001 characterization tests).

What the fix must do (WIRING ONLY):
  1. Replace the hardcoded compute_sortino_tstat(series, seed=trial_idx) with
     a call to the passed tstat_fn.
  2. For the CRRA-EU branch (tstat_fn is not compute_sortino_tstat): U-transform
     daily_returns via derive_floored_wealth_argument + compute_crra_utility
     before passing to tstat_fn.
  3. For the Sortino branch (tstat_fn is compute_sortino_tstat): call
     tstat_fn(series, seed=trial_idx) directly (no U-transform).
  4. The existing _crra_gamma local at :1243 must be forwarded to the U-transform.

Scope guard (HARD — enforced by tests):
  Any change to the BHY machinery, sentinel filter, n_effective padding, or
  return statement is OUT OF SCOPE and will be caught by the scope guard tests.

Fixture provenance:
  tests/fixtures/math/haircut_tstat_wiring.json — t-stats derived from closed-form
  U-transform arithmetic (W = max(0.001, 1+r/100), U = (W^(-1)-1)/(-1) for gamma=2)
  then compute_crra_eu_tstat(U_series). The 'wrong' values (raw pct, Sortino) document
  what the buggy implementation produces. No SUT output pinned.

Tolerance: rel=1e-9 for t-stats (deterministic double-precision arithmetic).
Alignment: these tests are compatible with and supplementary to
  test_audit_fix_crra_neff_arch.py (CRRA-001 characterization + gamma/floor contract).
"""

from __future__ import annotations

import importlib
import json
import pathlib
import re

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_FIXTURE = _WORKTREE_ROOT / "tests" / "fixtures" / "math" / "haircut_tstat_wiring.json"


def _load_fixture() -> dict:
    with open(str(_FIXTURE), encoding="utf-8") as fh:
        return json.load(fh)


def _make_trial(idx: int, value: float, returns_pct: list) -> object:
    """Construct a minimal FakeTrial object matching _haircut_select's expectations.

    daily_returns stored as raw PERCENT values (e.g. 5.0 for 5%), matching
    production's autotuner.py:1855-1859 (T5 provenance contract).
    """
    return type(
        "FakeTrial",
        (),
        {
            "value": value,
            "user_attrs": {"daily_returns": returns_pct},
            "params": {"TRIGGER_THRESHOLD_PCT": 10.0 + idx},
            "number": idx,
        },
    )()


# ===========================================================================
# Scenario 1 — Single all-positive trial: CRRA tstat_fn with U-transform must
# clear the gate; hardcoded Sortino must not.
#
# All-positive daily_returns in percent (5.0, 3.0, ...):
#   - compute_sortino_tstat: returns 0.0 (all bootstrap resamples yield
#     SORTINO_SENTINEL for an all-positive series; SE unavailable; conservative
#     fallback). gate: FAILS.
#   - compute_crra_eu_tstat on U-transformed series: returns ~19.91.
#     gate: CLEARS at p_adj ~1.5e-5 << q=0.05.
#
# After the fix: _haircut_select with tstat_fn=compute_crra_eu_tstat must
# U-transform the series and return a winner. The bug returns None.
# ===========================================================================


def test_crra_eu_tstat_fn_clears_gate_on_all_positive_series():
    """_haircut_select with tstat_fn=compute_crra_eu_tstat must return a winner
    for a single all-positive-return trial after U-transforming the series.

    daily_returns are stored in raw percent (e.g. 5.0 for 5%). The CRRA branch
    must apply W = max(WEALTH_ARG_FLOOR, 1+r/RETURN_PCT_TO_FRACTION) then
    U = compute_crra_utility(W, gamma) before calling tstat_fn.

    The U-transformed all-positive series produces t ~19.91 which clears the
    BHY gate (p_adj ~1.5e-5 << q=0.05).

    H-1 discriminating power: if _haircut_select still hardcodes
    compute_sortino_tstat (the bug), it returns tstat=0.0 (SE unavailable for
    all-positive series) => p=0.5 => no winner, regardless of tstat_fn.

    Fixture: tests/fixtures/math/haircut_tstat_wiring.json
    scenario_1.with_crra_eu_tstat_fn_u_transformed.
    """
    fixture = _load_fixture()
    sc = fixture["scenario_1_single_all_positive_trial"]
    autotuner = importlib.import_module("autotuner")

    trial = _make_trial(0, sc["trial_value_not_sentinel"], sc["daily_returns_pct"])

    winner_trial, winner_p_adj, winner_tstat = autotuner._haircut_select(
        [trial],
        tstat_fn=autotuner.compute_crra_eu_tstat,
    )

    assert winner_trial is not None, (
        "_haircut_select with tstat_fn=compute_crra_eu_tstat returned no winner "
        "(winner_trial is None) for an all-positive pct series.\n"
        f"  winner_p_adj = {winner_p_adj!r}, winner_tstat = {winner_tstat!r}\n"
        "  Expected: after U-transform, t~19.91, p_adj~1.5e-5 << q=0.05 => winner.\n"
        "  H-1 BUG SIGNATURE: if the loop hardcodes compute_sortino_tstat, it\n"
        "  returns tstat=0.0 (SE unavailable for all-positive series) and no winner."
    )

    # The t-stat must match the U-transformed CRRA oracle, not the Sortino 0.0.
    expected_tstat = sc["with_crra_eu_tstat_fn_u_transformed"]["tstat"]
    assert winner_tstat == pytest.approx(expected_tstat, rel=1e-9), (
        f"winner_tstat={winner_tstat!r} does not match the U-transformed CRRA oracle "
        f"{expected_tstat!r}.\n"
        "  Tolerance rel=1e-9: deterministic double-precision arithmetic.\n"
        "  If winner_tstat is 0.0: tstat_fn is being ignored (H-1 bug).\n"
        f"  If winner_tstat is {sc['with_crra_eu_tstat_fn_wrong_raw']['tstat']!r}: "
        "the U-transform was skipped (CRRA-001 bug — raw pct passed directly)."
    )

    q = fixture["harvey_liu_fdr_q"]
    assert winner_p_adj <= q, (
        f"winner_p_adj={winner_p_adj!r} does not clear q={q}.\n"
        f"  Expected p_adj~{sc['with_crra_eu_tstat_fn_u_transformed']['p_adj_single_trial']!r}."
    )


def test_sortino_tstat_fn_does_not_clear_gate_on_all_positive_series():
    """_haircut_select with tstat_fn=compute_sortino_tstat must NOT return a winner
    for a single all-positive-return trial.

    Companion guard: the Sortino path is conservative for zero-downside inputs.
    This pins that the two paths produce different gate outcomes, confirming they
    are distinguishable. After the fix: CRRA clears, Sortino does not.

    Fixture: scenario_1.with_sortino_hardcoded_bug (same outcome as Sortino tstat_fn).
    """
    fixture = _load_fixture()
    sc = fixture["scenario_1_single_all_positive_trial"]
    autotuner = importlib.import_module("autotuner")

    trial = _make_trial(0, sc["trial_value_not_sentinel"], sc["daily_returns_pct"])

    winner_trial, winner_p_adj, winner_tstat = autotuner._haircut_select(
        [trial],
        tstat_fn=autotuner.compute_sortino_tstat,
    )

    assert winner_trial is None, (
        "_haircut_select with tstat_fn=compute_sortino_tstat must NOT return a winner "
        "for an all-positive series (SE unavailable => tstat=0.0 => p=0.5 => fails gate).\n"
        f"  winner_tstat = {winner_tstat!r}"
    )

    expected_tstat = sc["with_sortino_hardcoded_bug"]["tstat"]
    assert winner_tstat == pytest.approx(expected_tstat, rel=1e-9), (
        f"winner_tstat={winner_tstat!r} does not match the Sortino oracle {expected_tstat!r}."
    )


# ===========================================================================
# Scenario 2 — Two-trial winner discriminator: gate outcome must differ when
# tstat_fn is swapped.
#
# Trial 0: tail-heavy (from crra_haircut_u_transform_contract fixture)
#   CRRA t (U-transformed) = -1.13 (fails gate), Sortino t = -0.59 (fails gate)
# Trial 1: all-positive 8-obs percent series
#   CRRA t (U-transformed) = 9.22 (clears gate), Sortino t = 0.0 (fails gate)
#
# With CRRA tstat_fn: trial 1 clears gate, wins. (gate_clears=True)
# With Sortino tstat_fn: neither clears. (gate_clears=False)
# Proves tstat_fn is actually dispatched.
# ===========================================================================


def test_tstat_fn_swap_changes_gate_outcome():
    """Swapping tstat_fn between compute_crra_eu_tstat and compute_sortino_tstat
    must produce different gate outcomes (winner vs no winner).

    Two-trial set:
      Trial 0: tail-heavy pct series  -> CRRA t(U)=-1.13 (fails gate), Sortino=-0.59
      Trial 1: all-positive pct series -> CRRA t(U)=9.22 (clears gate), Sortino=0.0

    With CRRA + U-transform: trial 1 clears BHY gate (q=0.05).
    With Sortino (hardcoded bug or explicit): neither trial clears.

    H-1 discriminating power: if tstat_fn is ignored and compute_sortino_tstat
    is always called, BOTH invocations produce the same Sortino-based decision
    (no winner in either case). After the fix, CRRA must return a winner.

    Fixture: scenario_2_two_trial_winner_discriminator.
    """
    fixture = _load_fixture()
    sc = fixture["scenario_2_two_trial_winner_discriminator"]
    autotuner = importlib.import_module("autotuner")
    q = fixture["harvey_liu_fdr_q"]

    trial_0 = _make_trial(
        0, sc["trial_0"]["trial_value_not_sentinel"], sc["trial_0"]["daily_returns_pct"]
    )
    trial_1 = _make_trial(
        1, sc["trial_1"]["trial_value_not_sentinel"], sc["trial_1"]["daily_returns_pct"]
    )

    winner_crra, p_adj_crra, tstat_crra = autotuner._haircut_select(
        [trial_0, trial_1],
        tstat_fn=autotuner.compute_crra_eu_tstat,
    )
    winner_sort, p_adj_sort, tstat_sort = autotuner._haircut_select(
        [trial_0, trial_1],
        tstat_fn=autotuner.compute_sortino_tstat,
    )

    # With CRRA tstat_fn, trial 1 must win.
    assert winner_crra is not None, (
        f"_haircut_select(tstat_fn=compute_crra_eu_tstat) returned no winner.\n"
        f"  p_adj={p_adj_crra!r}, tstat={tstat_crra!r}\n"
        f"  Fixture: trial 1 CRRA t(U)=9.22, p_adj="
        f"{sc['with_crra_eu_tstat_fn_u_transformed']['p_adj_trial_1']!r} < q={q}.\n"
        "  H-1 BUG: if tstat_fn is ignored (hardcoded Sortino), trial 1 gets "
        "tstat=0.0 (all-positive series) and neither trial clears the gate."
    )
    expected_winner_params = trial_1.params
    assert winner_crra.params == expected_winner_params, (
        f"CRRA tstat_fn must select trial 1 (highest CRRA t-stat after U-transform).\n"
        f"  selected params: {winner_crra.params}\n"
        f"  expected params: {expected_winner_params}\n"
        f"  CRRA t-stats: trial_0={sc['with_crra_eu_tstat_fn_u_transformed']['tstat_trial_0']!r}, "
        f"trial_1={sc['with_crra_eu_tstat_fn_u_transformed']['tstat_trial_1']!r}."
    )

    # With Sortino tstat_fn, neither trial must win.
    assert winner_sort is None, (
        f"_haircut_select(tstat_fn=compute_sortino_tstat) must return no winner "
        f"for this two-trial set.\n"
        f"  winner_sort.params = {winner_sort.params if winner_sort else None}\n"
        f"  Sortino t-stats: trial_0={sc['with_sortino_hardcoded_bug_seeds_0_and_1']['tstat_trial_0']!r}, "
        f"trial_1={sc['with_sortino_hardcoded_bug_seeds_0_and_1']['tstat_trial_1']!r}, both fail BHY gate."
    )

    # The two calls must produce different gate outcomes — proving tstat_fn dispatch.
    assert (winner_crra is not None) != (winner_sort is not None), (
        "Both tstat_fn variants produced the same gate outcome.\n"
        f"  crra winner: {winner_crra is not None}, sort winner: {winner_sort is not None}\n"
        "  tstat_fn is not being dispatched."
    )


def test_crra_tstat_values_match_u_transformed_fixture():
    """The t-stat produced by _haircut_select with tstat_fn=compute_crra_eu_tstat
    must match the U-transformed CRRA oracle for the winning trial.

    Probes the internal loop: checks that winner_tstat equals the expected
    U-transformed CRRA value, not the raw-pct value or a Sortino value.
    A partial fix that dispatches tstat_fn correctly but skips the U-transform
    would return the raw-pct t-stat (~19.03 for the all-positive series) instead
    of the U-transformed value (~19.91). This test catches that.

    Fixture: scenario_2.with_crra_eu_tstat_fn_u_transformed.tstat_trial_1.
    Tolerance: rel=1e-9.
    """
    fixture = _load_fixture()
    sc = fixture["scenario_2_two_trial_winner_discriminator"]
    autotuner = importlib.import_module("autotuner")

    trial_0 = _make_trial(
        0, sc["trial_0"]["trial_value_not_sentinel"], sc["trial_0"]["daily_returns_pct"]
    )
    trial_1 = _make_trial(
        1, sc["trial_1"]["trial_value_not_sentinel"], sc["trial_1"]["daily_returns_pct"]
    )

    winner_trial, winner_p_adj, winner_tstat = autotuner._haircut_select(
        [trial_0, trial_1],
        tstat_fn=autotuner.compute_crra_eu_tstat,
    )

    assert winner_trial is not None, (
        "_haircut_select(tstat_fn=compute_crra_eu_tstat) returned no winner. "
        "Cannot verify t-stat value."
    )

    # Winner is trial 1; its U-transformed CRRA t-stat must match the fixture.
    expected_tstat = sc["with_crra_eu_tstat_fn_u_transformed"]["tstat_trial_1"]
    assert winner_tstat == pytest.approx(expected_tstat, rel=1e-9), (
        f"winner_tstat={winner_tstat!r} does not match the U-transformed CRRA oracle "
        f"({expected_tstat!r}) for trial 1.\n"
        "  Tolerance rel=1e-9: deterministic double-precision arithmetic.\n"
        "  If the value is ~19.03: raw pct values were passed without U-transform.\n"
        "  If the value is 0.0: tstat_fn is still hardcoded to compute_sortino_tstat."
    )

    expected_p_adj = sc["with_crra_eu_tstat_fn_u_transformed"]["p_adj_trial_1"]
    assert winner_p_adj == pytest.approx(expected_p_adj, rel=1e-9), (
        f"winner_p_adj={winner_p_adj!r} does not match the CRRA oracle p_adj ({expected_p_adj!r})."
    )


# ===========================================================================
# Scenario 3 — U-transform contract: the CRRA branch must apply the correct
# wealth formula and gamma, not pass raw percent values.
#
# Uses the tail-heavy scenario from crra_haircut_u_transform_contract.json
# (t_crra_eu=-1.131, t_from_raw_pct=-0.538). These differ by ~52%.
# If the U-transform is skipped, the returned t-stat will match t_from_raw_pct.
# ===========================================================================


def test_crra_branch_applies_u_transform_not_raw_pct():
    """_haircut_select CRRA branch must U-transform daily_returns before calling
    compute_crra_eu_tstat — NOT pass raw percent values directly.

    Uses the tail-heavy scenario from crra_haircut_u_transform_contract.json:
      r_pct = [-50, 20, -30, 25, 10, -40, 15, 5]
      t_crra_eu (correct) = -1.131
      t_from_raw_pct (wrong) = -0.538
      Relative divergence: ~52%

    This test is the H-1 wiring companion to CRRA-001 in
    test_audit_fix_crra_neff_arch.py. It exercises the same contract but
    focuses on the H-1 tstat_fn dispatch rather than the CRRA-001 formula pin.

    Fixture: tests/fixtures/math/crra_haircut_u_transform_contract.json
    scenario_contract (both files reference the same canonical values).
    Tolerance: rel=1e-9 for the positive-identity assertion.
    """
    import json as _json

    u_transform_fixture = (
        _WORKTREE_ROOT / "tests" / "fixtures" / "math" / "crra_haircut_u_transform_contract.json"
    )
    sc = _json.loads(u_transform_fixture.read_text(encoding="utf-8"))["scenario_contract"]
    autotuner = importlib.import_module("autotuner")

    r_pct = sc["r_pct"]
    t_correct = sc["expected"]["t_crra_eu"]
    t_wrong_raw = sc["expected"]["t_from_raw_pct"]

    trial = _make_trial(0, 0.5, r_pct)  # value=0.5, not a sentinel

    winner_trial, winner_p_adj, winner_tstat = autotuner._haircut_select(
        [trial],
        tstat_fn=autotuner.compute_crra_eu_tstat,
    )

    # The U-transformed and raw-pct values differ by ~52% — tighten to rel=0.05
    # to reject the wrong path. The positive identity (rel=1e-9) catches everything.
    assert winner_tstat == pytest.approx(t_correct, rel=1e-9), (
        f"_haircut_select CRRA branch returned t={winner_tstat!r}.\n"
        f"  expected (U-transformed): {t_correct!r}\n"
        f"  wrong (raw pct Sharpe-like): {t_wrong_raw!r}\n"
        "  The two values differ by ~52% relative. If the returned value is\n"
        f"  near {t_wrong_raw!r}, _haircut_select is passing raw percent values\n"
        "  to compute_crra_eu_tstat without U-transforming (CRRA-001 sub-finding).\n"
        "  Fix: apply max(WEALTH_ARG_FLOOR, 1+r/RETURN_PCT_TO_FRACTION) -> "
        "compute_crra_utility(W, gamma) to each r_pct before calling tstat_fn."
    )

    # Negative identity: must NOT match the raw-pct wrong value.
    assert winner_tstat != pytest.approx(t_wrong_raw, rel=0.05), (
        f"_haircut_select returned t_from_raw_pct={t_wrong_raw!r} — the CRRA-001 bug.\n"
        "  Raw percent values were passed directly to compute_crra_eu_tstat.\n"
        f"  The correct U-transformed value is {t_correct!r} (~52% different)."
    )


# ===========================================================================
# Scenario 4 — Seed correctness: Sortino path must remain deterministic.
# ===========================================================================


def test_sortino_tstat_fn_produces_deterministic_results():
    """_haircut_select with tstat_fn=compute_sortino_tstat must produce identical
    results across two identical calls.

    The seed=trial_idx contract (autotuner.py:1248-1251) must be preserved in
    the Sortino branch after the fix. Two identical calls on the same trial set
    must return byte-identical results.

    Uses the two-trial set where Sortino t-stats are finite (trial_0 has
    mixed-sign series, seed=0 produces a non-zero t-stat).
    """
    fixture = _load_fixture()
    sc = fixture["scenario_2_two_trial_winner_discriminator"]
    autotuner = importlib.import_module("autotuner")

    trial_0 = _make_trial(
        0, sc["trial_0"]["trial_value_not_sentinel"], sc["trial_0"]["daily_returns_pct"]
    )
    trial_1 = _make_trial(
        1, sc["trial_1"]["trial_value_not_sentinel"], sc["trial_1"]["daily_returns_pct"]
    )

    result_a = autotuner._haircut_select(
        [trial_0, trial_1],
        tstat_fn=autotuner.compute_sortino_tstat,
    )
    result_b = autotuner._haircut_select(
        [trial_0, trial_1],
        tstat_fn=autotuner.compute_sortino_tstat,
    )

    assert (result_a[0] is None) == (result_b[0] is None), (
        "_haircut_select(tstat_fn=compute_sortino_tstat) produced different gate "
        "outcomes across two identical calls — non-deterministic seeding."
    )
    assert result_a[2] == result_b[2], (
        f"winner_tstat differs across identical _haircut_select calls: "
        f"{result_a[2]!r} vs {result_b[2]!r}.\n"
        "  seed=trial_idx contract must be preserved in the Sortino branch."
    )
    assert result_a[1] == result_b[1], f"winner_p_adj differs: {result_a[1]!r} vs {result_b[1]!r}."


def test_crra_eu_tstat_fn_called_without_seed_does_not_raise():
    """_haircut_select must not pass a seed= kwarg to compute_crra_eu_tstat.

    compute_crra_eu_tstat(U_series: list[float]) has no seed parameter.
    A partial fix that swaps the function name but retains the
    compute_sortino_tstat(series, seed=trial_idx) call syntax will raise:
      TypeError: unexpected keyword argument 'seed'

    Scope: catches a partial fix that handles dispatch but not the signature mismatch.
    """
    fixture = _load_fixture()
    sc = fixture["scenario_1_single_all_positive_trial"]
    autotuner = importlib.import_module("autotuner")

    trial = _make_trial(0, sc["trial_value_not_sentinel"], sc["daily_returns_pct"])

    try:
        autotuner._haircut_select(
            [trial],
            tstat_fn=autotuner.compute_crra_eu_tstat,
        )
    except TypeError as exc:
        pytest.fail(
            f"_haircut_select raised TypeError with tstat_fn=compute_crra_eu_tstat:\n"
            f"  {exc}\n"
            "  PARTIAL FIX: the loop is calling tstat_fn with seed= kwarg, but "
            "compute_crra_eu_tstat has no seed parameter. Dispatch must use "
            "(U_series,) for CRRA and (series, seed=idx) for Sortino."
        )


# ===========================================================================
# Scope guards: sentinel filter and BHY machinery must remain untouched.
# ===========================================================================


def test_sentinel_filter_excludes_sentinel_trials_regardless_of_tstat_fn():
    """_haircut_select must still exclude trials with value == _SORTINO_SENTINEL,
    regardless of tstat_fn. Scope guard for autotuner.py:1232-1236.
    """
    autotuner = importlib.import_module("autotuner")
    import math_engine

    sentinel_value = math_engine._SORTINO_SENTINEL
    good_returns_pct = [5.0, 3.0, 7.0, 4.0, 6.0] * 6  # 30 obs all-positive pct

    sentinel_trial = _make_trial(0, sentinel_value, good_returns_pct)
    good_trial = _make_trial(1, 0.5, good_returns_pct)

    # Sentinel-only: must return (None, None, None).
    result_only_sentinel = autotuner._haircut_select(
        [sentinel_trial],
        tstat_fn=autotuner.compute_crra_eu_tstat,
    )
    assert result_only_sentinel == (None, None, None), (
        f"_haircut_select with only a SORTINO_SENTINEL trial must return "
        f"(None, None, None); got {result_only_sentinel!r}.\n"
        "  Sentinel filter at autotuner.py:1232-1236 must not be removed by H-1 fix."
    )

    # Sentinel + good: only good trial participates; with CRRA it should win.
    winner, p_adj, tstat = autotuner._haircut_select(
        [sentinel_trial, good_trial],
        tstat_fn=autotuner.compute_crra_eu_tstat,
    )
    assert winner is not None, (
        "_haircut_select with one sentinel + one good trial must return the good trial.\n"
        "  After H-1 fix: the good trial's U-transformed CRRA t-stat (~19.91) clears gate."
    )
    assert winner.params == good_trial.params, (
        f"The winner must be the non-sentinel good trial.\n"
        f"  winner.params={winner.params}, good_trial.params={good_trial.params}"
    )


def test_empty_trial_list_returns_none_triple():
    """_haircut_select([]) must return (None, None, None). Scope guard."""
    autotuner = importlib.import_module("autotuner")

    result = autotuner._haircut_select([], tstat_fn=autotuner.compute_crra_eu_tstat)

    assert result == (None, None, None), (
        f"_haircut_select([]) must return (None, None, None); got {result!r}."
    )


# ===========================================================================
# Source-code inspection: the hardcoded call must be replaced with tstat_fn.
# ===========================================================================


def test_haircut_select_loop_does_not_hardcode_compute_sortino_tstat():
    """_haircut_select's inner loop must NOT contain a hardcoded call to
    compute_sortino_tstat. Source-inspection tripwire for H-1.

    Precedent: tests/autotuner/test_haircut_select_crra_default_gamma.py:53-97.
    """
    autotuner_path = _WORKTREE_ROOT / "autotuner.py"
    assert autotuner_path.exists(), "autotuner.py not found"
    source = autotuner_path.read_text(encoding="utf-8")

    lines = source.splitlines()
    in_func = False
    func_lines: list[str] = []
    for line in lines:
        if re.match(r"^def _haircut_select\b", line):
            in_func = True
        if in_func:
            if func_lines and re.match(r"^(def |class )\w", line):
                break
            func_lines.append(line)

    assert func_lines, "_haircut_select not found in autotuner.py"
    func_source = "\n".join(func_lines)

    assert "compute_sortino_tstat(" not in func_source, (
        "H-1 BUG STILL PRESENT: _haircut_select body contains a hardcoded call to "
        "compute_sortino_tstat(). Replace with a call to the passed tstat_fn.\n"
        "  The docstring at autotuner.py:1203-1207: 'Swapping tstat_fn is the ONLY "
        "permitted change.' The hardcoded call defeats the tstat_fn parameter."
    )


def test_haircut_select_loop_calls_tstat_fn():
    """_haircut_select's body must contain a call to tstat_fn(. Source-inspection
    companion to test_haircut_select_loop_does_not_hardcode_compute_sortino_tstat.
    """
    autotuner_path = _WORKTREE_ROOT / "autotuner.py"
    source = autotuner_path.read_text(encoding="utf-8")

    lines = source.splitlines()
    in_func = False
    func_lines: list[str] = []
    for line in lines:
        if re.match(r"^def _haircut_select\b", line):
            in_func = True
        if in_func:
            if func_lines and re.match(r"^(def |class )\w", line):
                break
            func_lines.append(line)

    assert func_lines, "_haircut_select not found in autotuner.py"
    func_source = "\n".join(func_lines)

    assert "tstat_fn(" in func_source, (
        "_haircut_select's body does not call tstat_fn(). The fix must replace the "
        "hardcoded compute_sortino_tstat() call with a call to tstat_fn.\n"
        "  Expected: at least one occurrence of 'tstat_fn(' inside _haircut_select."
    )
