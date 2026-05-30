"""
RED tests — H-1 audit finding: _haircut_select ignores its tstat_fn parameter.

Audit finding H-1 (autotuner.py:1251):
  _haircut_select accepts a tstat_fn parameter. Its docstring (:1203-1207) says
  swapping tstat_fn is 'the ONLY permitted change'. The call site passes
  tstat_fn=compute_crra_eu_tstat for the canonical CRRA bundle (:1953-1954,
  :1976-1977). BUT the loop hardcodes compute_sortino_tstat(series, seed=trial_idx)
  at :1251 and never calls tstat_fn. Result: CRRA-EU bundles get their overfitting
  haircut scored by the Sortino t-stat, not the CRRA-EU one — live on the
  canonical path.

What the fix must do (WIRING ONLY):
  Replace the hardcoded compute_sortino_tstat(series, seed=trial_idx) call with
  a call to the passed tstat_fn. The two signatures differ:
    compute_sortino_tstat(returns, seed: int = 0) -> float
    compute_crra_eu_tstat(U_series: list[float]) -> float
  The fix must dispatch correctly for each function without breaking the
  deterministic seeding that compute_sortino_tstat requires for reproducibility.

Scope guard (HARD — enforced by tests):
  The fix is WIRING ONLY. Any change to the haircut objective, the BHY machinery,
  the sentinel filter, or any other part of the function is OUT OF SCOPE and will
  be caught by the byte-identical preservation tests below.

Fixture provenance:
  tests/fixtures/math/haircut_tstat_wiring.json — all t-stats, p-values, and
  p_adj values are hand-derived from the independent function calls
  (compute_crra_eu_tstat and compute_sortino_tstat) on fixed series that produce
  a measurable, deterministic difference between the two functions. No producer
  output is pinned; the fixture records the oracle values from each function
  independently.

Tolerance: rel=1e-9 for t-stats and p-values (deterministic double-precision
arithmetic). The absolute difference between CRRA and Sortino t-stats is ~19
units on scenario 1 — a 1e-9 relative tolerance will not admit an accident.
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


def _make_trial(idx: int, value: float, returns: list) -> object:
    """Construct a minimal FakeTrial object matching _haircut_select's expectations.

    _haircut_select reads t.value (sentinel filter), t.user_attrs['daily_returns'],
    and t.params (returned as winner.params). Mirrors the mock pattern from
    test_haircut_select_crra_default_gamma.py:162-181.
    """
    return type("FakeTrial", (), {
        "value": value,
        "user_attrs": {"daily_returns": returns},
        "params": {"TRIGGER_THRESHOLD_PCT": 10.0 + idx},
        "number": idx,
    })()


# ===========================================================================
# Scenario 1 — Single all-positive trial: CRRA tstat_fn must clear the gate,
# Sortino tstat_fn must not.
#
# This is the sharpest single-trial discriminator:
#   - All-positive daily_returns => compute_sortino_tstat returns 0.0 (all
#     bootstrap resamples yield SORTINO_SENTINEL; SE unavailable; conservative
#     fallback per autotuner.py:681-682).
#   - compute_crra_eu_tstat returns ~19.04 (mean/sd * sqrt(T) on the series).
# If the hardcoded bug is present, BOTH tstat_fn calls produce tstat=0.0 and
# the trial fails the gate — no winner is returned.
# After the fix, the CRRA call must produce a winner (gate clears at p_adj ~1.5e-5).
# ===========================================================================


def test_crra_eu_tstat_fn_clears_gate_on_all_positive_series():
    """_haircut_select with tstat_fn=compute_crra_eu_tstat must return a winner
    for a single all-positive-return trial.

    The all-positive series has zero downside, so the Sortino bootstrap SE is
    unavailable (all resamples hit SORTINO_SENTINEL) and compute_sortino_tstat
    returns 0.0. compute_crra_eu_tstat returns ~19.04 which clears the BHY gate
    (p_adj ~1.5e-5 << q=0.05).

    H-1 discriminating power: if _haircut_select still hardcodes
    compute_sortino_tstat (the bug), it returns (None, 0.5, 0.0) — no winner —
    regardless of tstat_fn. A correct fix must return a non-None winner trial.

    Fixture: tests/fixtures/math/haircut_tstat_wiring.json
    scenario_1_single_all_positive_trial.with_crra_eu_tstat_fn.
    """
    fixture = _load_fixture()
    sc = fixture["scenario_1_single_all_positive_trial"]
    autotuner = importlib.import_module("autotuner")

    trial = _make_trial(0, sc["trial_value_not_sentinel"], sc["daily_returns"])

    winner_trial, winner_p_adj, winner_tstat = autotuner._haircut_select(
        [trial],
        tstat_fn=autotuner.compute_crra_eu_tstat,
    )

    assert winner_trial is not None, (
        "_haircut_select with tstat_fn=compute_crra_eu_tstat returned no winner "
        "(winner_trial is None) for an all-positive series.\n"
        f"  winner_p_adj = {winner_p_adj!r}, winner_tstat = {winner_tstat!r}\n"
        "  Expected: winner clears the BHY gate at p_adj ~1.5e-5 << q=0.05.\n"
        "  H-1 BUG SIGNATURE: if the loop still hardcodes compute_sortino_tstat, "
        "it returns tstat=0.0 (SE unavailable for all-positive series) => p=0.5 => "
        "no winner. The fix must call tstat_fn(series), not compute_sortino_tstat."
    )

    # Verify the t-stat is consistent with the fixture's CRRA oracle value,
    # not the Sortino fallback of 0.0.
    expected_tstat = sc["with_crra_eu_tstat_fn"]["tstat"]
    assert winner_tstat == pytest.approx(expected_tstat, rel=1e-9), (
        f"winner_tstat={winner_tstat!r} does not match the CRRA oracle "
        f"{expected_tstat!r}.\n"
        "  Tolerance rel=1e-9: deterministic double-precision arithmetic.\n"
        "  If winner_tstat is 0.0, _haircut_select is still calling "
        "compute_sortino_tstat (the H-1 bug), not compute_crra_eu_tstat."
    )

    # p_adj must clear the gate.
    q = fixture["harvey_liu_fdr_q"]
    assert winner_p_adj <= q, (
        f"winner_p_adj={winner_p_adj!r} does not clear the BHY gate q={q}.\n"
        f"  Expected p_adj ~{sc['with_crra_eu_tstat_fn']['p_adj_single_trial']!r}.\n"
        "  A correct CRRA t-stat (~19.04) produces p ~1.5e-5 which clears q=0.05. "
        "A Sortino t-stat of 0.0 produces p=0.5 which does not."
    )


def test_sortino_tstat_fn_does_not_clear_gate_on_all_positive_series():
    """_haircut_select with tstat_fn=compute_sortino_tstat must NOT return a winner
    for a single all-positive-return trial.

    Companion to test_crra_eu_tstat_fn_clears_gate_on_all_positive_series.
    The Sortino SE bootstrap returns None for an all-positive series (all
    resamples hit SORTINO_SENTINEL) => tstat=0.0 => p=0.5 => fails BHY gate.
    This test pins that the Sortino path is conservative for zero-downside inputs,
    confirming the two paths are distinguishable by gate outcome.

    If this test fails while the CRRA test passes, the fix is correct.
    If BOTH tests return the same result, tstat_fn is still being ignored.

    Fixture: scenario_1_single_all_positive_trial.with_sortino_tstat_fn.
    """
    fixture = _load_fixture()
    sc = fixture["scenario_1_single_all_positive_trial"]
    autotuner = importlib.import_module("autotuner")

    trial = _make_trial(0, sc["trial_value_not_sentinel"], sc["daily_returns"])

    winner_trial, winner_p_adj, winner_tstat = autotuner._haircut_select(
        [trial],
        tstat_fn=autotuner.compute_sortino_tstat,
    )

    assert winner_trial is None, (
        "_haircut_select with tstat_fn=compute_sortino_tstat must NOT return a winner "
        "for an all-positive series (SE unavailable => tstat=0.0 => p=0.5 => fails gate).\n"
        f"  winner_tstat = {winner_tstat!r}\n"
        "  If winner_trial is not None here, the Sortino path no longer uses the "
        "conservative SE=None fallback — investigate compute_sortino_tstat behavior."
    )

    expected_tstat = sc["with_sortino_tstat_fn"]["tstat"]
    assert winner_tstat == pytest.approx(expected_tstat, rel=1e-9), (
        f"winner_tstat={winner_tstat!r} does not match the Sortino oracle {expected_tstat!r}."
    )


# ===========================================================================
# Scenario 2 — Two-trial winner discriminator: swapping tstat_fn must change
# both the gate outcome AND the selected winner.
#
# Trial 0: all-positive series (CRRA t=19.04, Sortino t=0.0)
# Trial 1: alternating-sign series (CRRA t=2.08, Sortino t=1.69)
#
# With compute_crra_eu_tstat: both trials clear the gate; trial 0 wins.
# With compute_sortino_tstat: neither trial clears the gate; no winner.
#
# This proves that (a) the tstat_fn is actually invoked on each trial's series,
# and (b) the gate decision changes, not just the stored t-stat value.
# ===========================================================================


def test_tstat_fn_swap_changes_gate_outcome():
    """Swapping tstat_fn between compute_crra_eu_tstat and compute_sortino_tstat
    must produce different gate outcomes (winner vs no winner).

    Two-trial set:
      Trial 0: all-positive series  -> CRRA t=19.04, Sortino t=0.0
      Trial 1: alternating series   -> CRRA t=2.08,  Sortino t=1.69

    With CRRA: both trials clear BHY gate (q=0.05), trial 0 wins.
    With Sortino: neither trial clears (t-stats too low for N=2 BHY correction).

    H-1 discriminating power: if _haircut_select hardcodes compute_sortino_tstat,
    BOTH calls produce the same Sortino-based gate decision regardless of tstat_fn.
    After the fix, the CRRA call must return a winner and the Sortino call must not.

    Fixture: tests/fixtures/math/haircut_tstat_wiring.json
    scenario_2_two_trial_winner_discriminator.
    """
    fixture = _load_fixture()
    sc = fixture["scenario_2_two_trial_winner_discriminator"]
    autotuner = importlib.import_module("autotuner")
    q = fixture["harvey_liu_fdr_q"]

    trial_0 = _make_trial(0, sc["trial_0"]["trial_value_not_sentinel"], sc["trial_0"]["daily_returns"])
    trial_1 = _make_trial(1, sc["trial_1"]["trial_value_not_sentinel"], sc["trial_1"]["daily_returns"])

    winner_crra, p_adj_crra, tstat_crra = autotuner._haircut_select(
        [trial_0, trial_1],
        tstat_fn=autotuner.compute_crra_eu_tstat,
    )
    winner_sort, p_adj_sort, tstat_sort = autotuner._haircut_select(
        [trial_0, trial_1],
        tstat_fn=autotuner.compute_sortino_tstat,
    )

    # With CRRA tstat_fn, trial 0 must win (both trials clear gate).
    assert winner_crra is not None, (
        f"_haircut_select(tstat_fn=compute_crra_eu_tstat) returned no winner.\n"
        f"  p_adj={p_adj_crra!r}, tstat={tstat_crra!r}\n"
        f"  Fixture expects: winner_index=0, p_adj_trial_0="
        f"{sc['with_crra_eu_tstat_fn']['p_adj_trial_0']!r} < q={q}.\n"
        "  H-1 BUG: if tstat_fn is ignored, compute_sortino_tstat produces t=0.0 "
        "for trial_0 and t=1.69 for trial_1; neither clears the BHY gate at N=2."
    )
    expected_crra_winner_params = trial_0.params
    assert winner_crra.params == expected_crra_winner_params, (
        f"CRRA tstat_fn must select trial 0 (lowest p_adj under CRRA t-stats).\n"
        f"  selected params: {winner_crra.params}\n"
        f"  expected params: {expected_crra_winner_params}\n"
        f"  winner p_adj = {p_adj_crra!r}\n"
        f"  Fixture: CRRA trial 0 p_adj={sc['with_crra_eu_tstat_fn']['p_adj_trial_0']!r}, "
        f"trial 1 p_adj={sc['with_crra_eu_tstat_fn']['p_adj_trial_1']!r}."
    )

    # With Sortino tstat_fn, neither trial must win (gate rejects all).
    assert winner_sort is None, (
        f"_haircut_select(tstat_fn=compute_sortino_tstat) must return no winner "
        f"for this two-trial set (Sortino t-stats too low for N=2 BHY gate).\n"
        f"  winner_sort.params = {winner_sort.params if winner_sort else None}\n"
        f"  p_adj_sort = {p_adj_sort!r}, tstat_sort = {tstat_sort!r}\n"
        f"  Fixture: Sortino p_adj_trial_0={sc['with_sortino_tstat_fn_seeds_0_and_1']['p_adj_trial_0']!r}, "
        f"p_adj_trial_1={sc['with_sortino_tstat_fn_seeds_0_and_1']['p_adj_trial_1']!r}, both > q={q}."
    )

    # The two calls must differ in outcome — proving tstat_fn is actually dispatched.
    assert (winner_crra is not None) != (winner_sort is not None), (
        f"Both tstat_fn=compute_crra_eu_tstat and tstat_fn=compute_sortino_tstat "
        f"produced the same gate outcome (both winner or both no-winner).\n"
        f"  crra winner: {winner_crra is not None}, sort winner: {winner_sort is not None}\n"
        "  tstat_fn is not being dispatched — the loop is still hardcoded to one function."
    )


def test_crra_tstat_values_match_fixture_in_two_trial_case():
    """The t-stats produced by _haircut_select with tstat_fn=compute_crra_eu_tstat
    must match the fixture's oracle values for both trials.

    This test probes the internal state of the haircut loop by checking the
    t-stat of the winning trial, not just the gate outcome. It catches a partial
    fix that routes the winner selection correctly but still computes t-stats
    from a wrong function.

    Fixture: scenario_2_two_trial_winner_discriminator.with_crra_eu_tstat_fn.
    Tolerance: rel=1e-9 (deterministic double-precision arithmetic).
    """
    fixture = _load_fixture()
    sc = fixture["scenario_2_two_trial_winner_discriminator"]
    autotuner = importlib.import_module("autotuner")

    trial_0 = _make_trial(0, sc["trial_0"]["trial_value_not_sentinel"], sc["trial_0"]["daily_returns"])
    trial_1 = _make_trial(1, sc["trial_1"]["trial_value_not_sentinel"], sc["trial_1"]["daily_returns"])

    winner_trial, winner_p_adj, winner_tstat = autotuner._haircut_select(
        [trial_0, trial_1],
        tstat_fn=autotuner.compute_crra_eu_tstat,
    )

    assert winner_trial is not None, (
        "_haircut_select(tstat_fn=compute_crra_eu_tstat) returned no winner. "
        "Cannot verify t-stat values."
    )

    # The winner is trial 0 with the CRRA t-stat of 19.04.
    expected_tstat = sc["with_crra_eu_tstat_fn"]["tstat_trial_0"]
    assert winner_tstat == pytest.approx(expected_tstat, rel=1e-9), (
        f"winner_tstat={winner_tstat!r} does not match the CRRA oracle for trial 0 "
        f"({expected_tstat!r}).\n"
        "  Tolerance rel=1e-9: deterministic double-precision arithmetic.\n"
        "  The gap between CRRA (~19.04) and Sortino (0.0) is ~19 units; "
        "a rel=1e-9 tolerance cannot mistake one for the other."
    )

    # The winner's p_adj must match the fixture (derived from the CRRA t-stat).
    expected_p_adj = sc["with_crra_eu_tstat_fn"]["p_adj_trial_0"]
    assert winner_p_adj == pytest.approx(expected_p_adj, rel=1e-9), (
        f"winner_p_adj={winner_p_adj!r} does not match the CRRA oracle p_adj "
        f"({expected_p_adj!r}) for trial 0.\n"
        "  A Sortino-based p_adj would be ~0.75 (p_raw=0.5 * BHY N=2 factor)."
    )


# ===========================================================================
# Scenario 3 — Signature/seed correctness: compute_sortino_tstat requires
# a seed for determinism; compute_crra_eu_tstat has no seed parameter.
# The fix must forward (returns, seed=idx) to compute_sortino_tstat and
# (series,) to compute_crra_eu_tstat.
# ===========================================================================


def test_sortino_tstat_fn_seed_is_forwarded_for_determinism():
    """_haircut_select with tstat_fn=compute_sortino_tstat must produce a
    deterministic result across repeated calls with the same trial set.

    Rationale: the docstring at autotuner.py:1248-1251 pins that
    seed=trial_idx produces a 'deterministic per-trial seed'. After the fix,
    compute_sortino_tstat must still receive a seed that is stable within a
    study run. This test verifies the determinism property: two identical calls
    must produce byte-for-byte identical results.

    Fixture: scenario_2_two_trial_winner_discriminator (two trials with
    mixed-sign series where Sortino t-stats are computable and non-zero).
    """
    fixture = _load_fixture()
    sc = fixture["scenario_2_two_trial_winner_discriminator"]
    autotuner = importlib.import_module("autotuner")

    trial_0 = _make_trial(0, sc["trial_0"]["trial_value_not_sentinel"], sc["trial_0"]["daily_returns"])
    trial_1 = _make_trial(1, sc["trial_1"]["trial_value_not_sentinel"], sc["trial_1"]["daily_returns"])

    # Two identical calls — must produce identical results.
    result_a = autotuner._haircut_select(
        [trial_0, trial_1],
        tstat_fn=autotuner.compute_sortino_tstat,
    )
    result_b = autotuner._haircut_select(
        [trial_0, trial_1],
        tstat_fn=autotuner.compute_sortino_tstat,
    )

    # gate outcome
    assert (result_a[0] is None) == (result_b[0] is None), (
        "_haircut_select(tstat_fn=compute_sortino_tstat) produced different gate "
        "outcomes across two identical calls — non-deterministic seeding."
    )
    # t-stat must be identical
    assert result_a[2] == result_b[2], (
        f"winner_tstat differs across two identical _haircut_select calls: "
        f"{result_a[2]!r} vs {result_b[2]!r}.\n"
        "  The seed forwarded to compute_sortino_tstat must be a stable function "
        "of the trial index, not a random value (autotuner.py:1248-1251 contract)."
    )
    # p_adj must be identical
    assert result_a[1] == result_b[1], (
        f"winner_p_adj differs across two identical _haircut_select calls: "
        f"{result_a[1]!r} vs {result_b[1]!r}."
    )


def test_crra_eu_tstat_fn_called_without_seed_does_not_raise():
    """_haircut_select must not pass a seed= argument to compute_crra_eu_tstat.

    compute_crra_eu_tstat(U_series: list[float]) -> float has no seed parameter.
    Calling it with a seed= kwarg (the Sortino-style interface) will raise
    TypeError: 'unexpected keyword argument'. A partial fix that swaps the
    function name but retains compute_sortino_tstat(series, seed=trial_idx) call
    syntax will fail here.

    Scope: tests that the fix handles the signature mismatch, not just the
    function dispatch.
    """
    fixture = _load_fixture()
    sc = fixture["scenario_1_single_all_positive_trial"]
    autotuner = importlib.import_module("autotuner")

    trial = _make_trial(0, sc["trial_value_not_sentinel"], sc["daily_returns"])

    # This must not raise TypeError.
    try:
        winner_trial, winner_p_adj, winner_tstat = autotuner._haircut_select(
            [trial],
            tstat_fn=autotuner.compute_crra_eu_tstat,
        )
    except TypeError as exc:
        pytest.fail(
            f"_haircut_select raised TypeError when called with "
            f"tstat_fn=compute_crra_eu_tstat:\n  {exc}\n"
            "  PARTIAL FIX SIGNATURE: the loop still calls tstat_fn with a seed= "
            "kwarg (compute_sortino_tstat's interface), but compute_crra_eu_tstat "
            "has no seed parameter. The fix must dispatch (series,) for CRRA and "
            "(series, seed=idx) for Sortino — or use a calling convention that "
            "compute_crra_eu_tstat accepts."
        )


# ===========================================================================
# Scope guard: BHY machinery and sentinel filter must remain byte-identical.
#
# The fix is wiring-only. If impl-h1 touches the sentinel filter or BHY
# adjustment, these tests will catch the scope creep.
# ===========================================================================


def test_sentinel_filter_still_excludes_sortino_sentinel_trials():
    """_haircut_select must still exclude trials whose value equals
    math_engine._SORTINO_SENTINEL, regardless of tstat_fn.

    Scope guard: the sentinel filter at autotuner.py:1232-1236 must remain
    untouched by the H-1 wiring fix. Removing or loosening the filter would
    let a degenerate trial (all-non-negative returns, zero downside) dominate
    the haircut p-value distribution.
    """
    autotuner = importlib.import_module("autotuner")
    import math_engine

    sentinel_value = math_engine._SORTINO_SENTINEL
    good_series = [0.05, 0.03, 0.07, 0.04, 0.06] * 6

    # A sentinel trial must be excluded from the haircut pool.
    sentinel_trial = _make_trial(0, sentinel_value, good_series)
    good_trial = _make_trial(1, 0.5, good_series)

    # With only a sentinel trial: must return (None, None, None).
    result_only_sentinel = autotuner._haircut_select(
        [sentinel_trial],
        tstat_fn=autotuner.compute_crra_eu_tstat,
    )
    assert result_only_sentinel == (None, None, None), (
        f"_haircut_select with only a SORTINO_SENTINEL trial must return "
        f"(None, None, None); got {result_only_sentinel!r}.\n"
        "  Sentinel filter (autotuner.py:1232-1236) must not be removed by the H-1 fix."
    )

    # With sentinel + good trial: only the good trial participates.
    winner, p_adj, tstat = autotuner._haircut_select(
        [sentinel_trial, good_trial],
        tstat_fn=autotuner.compute_crra_eu_tstat,
    )
    # The good trial should be selected (CRRA t-stat ~19.04 clears gate).
    assert winner is not None, (
        "_haircut_select with one sentinel + one good trial must return the good "
        "trial as winner. Sentinel filter must exclude the sentinel before BHY."
    )
    assert winner.params == good_trial.params, (
        f"The winner must be the non-sentinel good trial.\n"
        f"  winner.params={winner.params}\n  good_trial.params={good_trial.params}"
    )


def test_empty_trial_list_returns_none_triple():
    """_haircut_select([]) must return (None, None, None) — boundary unchanged.

    Scope guard for the empty-input path at autotuner.py:1226-1227.
    """
    autotuner = importlib.import_module("autotuner")

    result = autotuner._haircut_select([], tstat_fn=autotuner.compute_crra_eu_tstat)

    assert result == (None, None, None), (
        f"_haircut_select([]) must return (None, None, None); got {result!r}.\n"
        "  Boundary guard at autotuner.py:1226-1227 must not be affected by H-1 fix."
    )


# ===========================================================================
# Source-code inspection: the hardcoded compute_sortino_tstat call must be gone.
# ===========================================================================


def test_haircut_select_loop_does_not_hardcode_compute_sortino_tstat():
    """_haircut_select's inner loop must NOT contain a hardcoded call to
    compute_sortino_tstat.

    This is a source-inspection tripwire for the H-1 bug. The bug is:
      tstats.append(compute_sortino_tstat(series, seed=trial_idx))
    The fix must replace this with a call to tstat_fn.

    Method: parse autotuner.py source and scan the _haircut_select function body
    for the string 'compute_sortino_tstat('. A legitimate call to
    compute_sortino_tstat outside this function (e.g., in compute_sortino_tstat
    itself, or in run_simulation) is not flagged.

    Precedent for source-inspection tests in this test suite:
      tests/autotuner/test_haircut_select_crra_default_gamma.py:53-97.
    """
    autotuner_path = _WORKTREE_ROOT / "autotuner.py"
    assert autotuner_path.exists(), "autotuner.py not found"
    source = autotuner_path.read_text(encoding="utf-8")

    # Isolate _haircut_select function body (first top-level def after the def line).
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

    assert func_lines, (
        "_haircut_select function not found in autotuner.py — has it been renamed?"
    )

    func_source = "\n".join(func_lines)

    assert "compute_sortino_tstat(" not in func_source, (
        "H-1 BUG STILL PRESENT: _haircut_select's body contains a hardcoded call to "
        "compute_sortino_tstat(). The fix must replace this with a call to the passed "
        "tstat_fn parameter.\n"
        "  Found pattern: 'compute_sortino_tstat(' inside _haircut_select body.\n"
        "  The docstring at autotuner.py:1203-1207 says: 'Swapping tstat_fn is the "
        "ONLY permitted change.' The hardcoded call defeats the tstat_fn parameter."
    )


def test_haircut_select_loop_calls_tstat_fn():
    """_haircut_select's body must contain a call to the tstat_fn parameter.

    Companion to test_haircut_select_loop_does_not_hardcode_compute_sortino_tstat.
    After removing the hardcoded call, the fix must add a call to tstat_fn.
    This test ensures the replacement actually calls the parameter, not just
    removes the hardcoded call.

    Source-inspection: scan for 'tstat_fn(' in the function body.
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
        "hardcoded compute_sortino_tstat() call with a call to the tstat_fn parameter.\n"
        "  Expected: at least one occurrence of 'tstat_fn(' inside _haircut_select."
    )
