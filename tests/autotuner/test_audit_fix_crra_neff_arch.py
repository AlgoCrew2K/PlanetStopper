"""
RED tests — Sprint 2 audit-fix: CRRA-001, TEST-001, NEFF-001, ARCH-001.

These tests will ALL FAIL against the current code and will turn GREEN only
after the four audit findings are fixed by the implementer.

Findings addressed (in dependency order):

  CRRA-001 (CRITICAL): _haircut_select CRRA-EU branch passes raw percent returns
    directly to compute_crra_eu_tstat, which expects U-values. The correct
    implementation must U-transform via derive_floored_wealth_argument +
    compute_crra_utility before calling tstat_fn.
    Documented docstring contract: autotuner.py:1083-1084.

  TEST-001 (MEDIUM, prereq CRRA-001): no test covers the full path
    trial.set_user_attr("daily_returns", raw_pct) -> _haircut_select ->
    compute_crra_eu_tstat. These tests provide the characterization coverage.

  NEFF-001 (CRITICAL, prereq CRRA-001): compute_n_effective is never called
    in production. Both _haircut_select call sites at autotuner.py:1558 and
    :1838 omit n_effective. The wiring tests assert the call is made and the
    resulting value is propagated.

  ARCH-001 (HIGH, prereq NEFF-001): save_autotune_run has no parameters for
    the 9 EUT columns from migration 020. Every autotune_runs row leaves
    spec_bundle_id, n_effective, d_spec, gamma, overfitting_verdict, etc. NULL.

Fixture:
  tests/fixtures/math/crra_haircut_u_transform_contract.json — two scenarios:
    (1) contract test: tail-heavy distribution, 52% relative divergence between
        t_crra_eu and t_from_raw_pct; hand-derived, no SUT output used.
    (2) divergence scenario: extreme loss distribution with floor-hitting day.

Hostile-edge guards (from team-lead mandate):
  - Sortino branch: existing compute_sortino_tstat path UNCHANGED; tests
    assert it uses t.value + len(series), NOT U-transform.
  - get_researcher_dof_ledger_for_run is read-only at the autotune entry
    path; tests do not exercise write paths.
  - bundle_hash TEXT vs integer id: tests guard both TYPE-002 and CRRA-001
    at the wiring points.

References:
  docs/audit/sprint-2-cross-cycle-audit.md §10 (CRRA-001), §11 (TEST-001),
  §2 (ARCH-001), §N (NEFF-001).
  autotuner.py:1083-1084 (documented but unimplemented U-transform contract).
  autotuner.py:893-969 (_haircut_select implementation).
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import pathlib
import statistics
from unittest.mock import MagicMock, call, patch

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_FIXTURE_PATH = (
    _WORKTREE_ROOT
    / "tests"
    / "fixtures"
    / "math"
    / "crra_haircut_u_transform_contract.json"
)

# BHY tolerance — same as test_c4_harvey_liu_haircut.py and
# test_n_effective_additive_accounting.py.
_BHY_TOL = 1e-9


def _load_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _import_autotuner():
    import autotuner
    return autotuner


def _import_database():
    import database
    return database


def _import_math_engine():
    import math_engine
    return math_engine


# ---------------------------------------------------------------------------
# Helpers: minimal fake Optuna trial compatible with _haircut_select
# ---------------------------------------------------------------------------


def _make_trial(value: float, daily_returns: list[float], sentinel_value: float = 1e6) -> MagicMock:
    """Minimal fake Optuna trial. _haircut_select reads .value and .user_attrs only."""
    trial = MagicMock()
    trial.value = value
    trial.user_attrs = {"daily_returns": daily_returns}
    return trial


def _make_phase1_theory_bundle() -> int:
    """Insert an all-THEORY Phase-1 bundle and return its integer id.

    Required because run_autotuner demands spec_bundle_id != None. Idempotent
    within a test's isolated DB (INSERT OR IGNORE).
    """
    import database as _db

    canon_facets = {
        "gamma": "2.0",
        "utility_family": "CRRA",
        "wealth_argument": "compounded_return",
    }
    canonical_json = _db.canonicalize_facets_json(canon_facets)
    bundle_hash = _db.hash_facets_json(canonical_json)
    _db.insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical_json)

    conn = _db.get_connection()
    bundle_id = conn.execute(
        "SELECT id FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
    ).fetchone()[0]
    conn.close()

    existing = _db.get_spec_facets_for_bundle(bundle_hash)
    existing_names = {r["facet_name"] for r in existing}
    for name, value in canon_facets.items():
        if name not in existing_names:
            _db.insert_spec_bundle_facet(
                bundle_hash=bundle_hash,
                facet_name=name,
                facet_value=value,
                freeze_discipline="THEORY",
                justification="audit-fix RED test — all-THEORY bundle",
            )
    return bundle_id


# ---------------------------------------------------------------------------
# §0 — Fixture file precondition
# ---------------------------------------------------------------------------


def test_fixture_file_exists_and_has_required_keys():
    """The fixture must exist and have all scenario keys before math tests run."""
    assert _FIXTURE_PATH.exists(), (
        f"Fixture not found at {_FIXTURE_PATH}. "
        "Create tests/fixtures/math/crra_haircut_u_transform_contract.json."
    )
    data = _load_fixture()
    for key in ("scenario_contract", "scenario_divergence", "constants"):
        assert key in data, f"Fixture missing top-level key '{key}'."
    for key in ("r_pct", "W_series", "U_series", "T", "expected"):
        assert key in data["scenario_contract"], (
            f"scenario_contract missing key '{key}'."
        )
    assert "t_crra_eu" in data["scenario_contract"]["expected"]
    assert "t_from_raw_pct" in data["scenario_contract"]["expected"]


# ===========================================================================
# §1 — CRRA-001 + TEST-001: _haircut_select U-transform contract
# ===========================================================================


class TestHaircutSelectCrraEUUTransformContract:
    """CRRA-001 + TEST-001: _haircut_select must apply U-transform before tstat_fn.

    The docstring at autotuner.py:1083-1084 states: 'The haircut re-transforms
    daily_returns through derive_floored_wealth_argument + compute_crra_utility.'
    This is the documented contract; CRRA-001 confirms it is not implemented.

    These tests will be RED until the CRRA-EU branch applies the transform.
    """

    def test_haircut_select_crra_eu_returns_u_transformed_tstat(self):
        """_haircut_select with tstat_fn=compute_crra_eu_tstat must return
        the t-stat derived from U-transformed values, not raw percent returns.

        Construct a single trial with known daily_returns (raw percent). Assert
        the returned tstat equals compute_crra_eu_tstat(U_series), NOT
        compute_crra_eu_tstat(r_pct_series).

        The fixture's scenario_contract distribution has a 52% relative divergence
        between the two t-stats, so the two values cannot be within 5% of each other
        and an implementation that passes raw percent cannot accidentally pass this test.

        Tolerance: rel=1e-9 on the correct value (deterministic arithmetic).
        """
        autotuner = _import_autotuner()
        fixture = _load_fixture()
        sc = fixture["scenario_contract"]

        r_pct = sc["r_pct"]
        U_series = sc["U_series"]
        t_expected = sc["expected"]["t_crra_eu"]
        t_wrong = sc["expected"]["t_from_raw_pct"]

        # Sortino value irrelevant for CRRA-EU branch; use a placeholder
        trial = _make_trial(value=0.5, daily_returns=r_pct)

        _winner, _p_adj, winner_tstat = autotuner._haircut_select(
            [trial],
            tstat_fn=autotuner.compute_crra_eu_tstat,
        )

        # The returned t-stat must match the U-transformed computation.
        # rel=1e-9: both sides are pure double-precision arithmetic on a fixed
        # input; only last-bit rounding is allowed.
        assert winner_tstat == pytest.approx(t_expected, rel=1e-9), (
            f"_haircut_select CRRA-EU branch returned t={winner_tstat!r}.\n"
            f"  expected (U-transformed): {t_expected!r}\n"
            f"  got                     : {winner_tstat!r}\n"
            f"  wrong (raw pct, Sharpe) : {t_wrong!r}\n"
            f"  The two values differ by ~52% relative. If the returned value\n"
            f"  matches t_wrong ({t_wrong!r}), _haircut_select is passing\n"
            f"  raw percent returns to compute_crra_eu_tstat instead of\n"
            f"  U-transforming them first (CRRA-001 bug).\n"
            f"  Fix: apply derive_floored_wealth_argument + compute_crra_utility\n"
            f"  to each r_pct value before calling tstat_fn."
        )

    def test_haircut_select_crra_eu_tstat_does_not_match_raw_pct_form(self):
        """Negative identity: _haircut_select must NOT return the raw-percent t-stat.

        The fixture is constructed so the broken implementation (raw percent passed
        to compute_crra_eu_tstat) returns t_from_raw_pct, which differs from the
        correct t_crra_eu by ~52% relative.

        Tolerance: rel=0.05 — the two values cannot be within 5% of each other.
        If the assertion PASSES (no error), the implementation is correct.
        If the assertion FAILS (error), the implementation is passing raw pct values.
        """
        autotuner = _import_autotuner()
        fixture = _load_fixture()
        sc = fixture["scenario_contract"]

        r_pct = sc["r_pct"]
        t_wrong = sc["expected"]["t_from_raw_pct"]

        trial = _make_trial(value=0.5, daily_returns=r_pct)

        _winner, _p_adj, winner_tstat = autotuner._haircut_select(
            [trial],
            tstat_fn=autotuner.compute_crra_eu_tstat,
        )

        # The broken value and the correct value differ by >50% relative.
        # asserting NOT approx(t_wrong, rel=0.05): if the test fails, the
        # implementation returned the raw-percent Sharpe-like t-stat.
        assert winner_tstat != pytest.approx(t_wrong, rel=0.05), (
            f"_haircut_select CRRA-EU branch returned t_from_raw_pct={t_wrong!r}.\n"
            f"  This is the CRRA-001 bug: raw percent values passed to\n"
            f"  compute_crra_eu_tstat (which expects U-values).\n"
            f"  The broken t-stat is the Sharpe-like mean(r)/sd(r)*sqrt(T).\n"
            f"  Fix: U-transform daily_returns before calling tstat_fn."
        )

    def test_haircut_select_crra_eu_u_transform_uses_correct_gamma_and_floor(self):
        """The U-transform must use WEALTH_ARG_FLOOR and RETURN_PCT_TO_FRACTION
        from autotuner module constants, not any hardcoded values.

        Verify by checking the returned t-stat matches a reference computed with
        the same constants. If the transform used wrong gamma or wrong floor,
        the U-series would differ and the t-stat would diverge.

        This test is a secondary check that the implementation uses the module
        constants rather than any inlined magic numbers.
        """
        autotuner = _import_autotuner()
        math_engine = _import_math_engine()

        gamma = 2.0  # from Phase-1 THEORY bundle; matches autotuner default
        wealth_floor = autotuner.WEALTH_ARG_FLOOR
        pct_to_frac = autotuner.RETURN_PCT_TO_FRACTION

        r_pct = [-10.0, 5.0, -2.0, 8.0, -1.0]
        W_series = [max(wealth_floor, 1.0 + r / pct_to_frac) for r in r_pct]
        U_expected = [math_engine.compute_crra_utility(W, gamma) for W in W_series]
        t_expected = autotuner.compute_crra_eu_tstat(U_expected)

        trial = _make_trial(value=0.3, daily_returns=r_pct)
        _winner, _p_adj, winner_tstat = autotuner._haircut_select(
            [trial],
            tstat_fn=autotuner.compute_crra_eu_tstat,
        )

        # rel=1e-9: deterministic arithmetic; only last-bit rounding.
        assert winner_tstat == pytest.approx(t_expected, rel=1e-9), (
            f"_haircut_select CRRA-EU branch returned t={winner_tstat!r}.\n"
            f"  expected (using module WEALTH_ARG_FLOOR={wealth_floor}, "
            f"gamma={gamma}): {t_expected!r}\n"
            f"  A mismatch means the U-transform used wrong constants or\n"
            f"  the wrong gamma (e.g. hardcoded 2.0 instead of the bundle value,\n"
            f"  or wrong WEALTH_ARG_FLOOR). The transform must use the same\n"
            f"  WEALTH_ARG_FLOOR and RETURN_PCT_TO_FRACTION as the module."
        )

    def test_haircut_select_sortino_branch_unchanged_no_u_transform(self):
        """Guard: the Sortino branch must NOT apply the U-transform.

        CRRA-001 fix scope is strictly the CRRA-EU branch. The Sortino branch
        passes the raw daily_returns series to compute_sortino_tstat (Decision
        D10 / RM-H1 bootstrap contract) with the trial index as seed. It does
        NOT transform the series through the CRRA utility function.

        Asserting the Sortino path is unchanged ensures the fix does not
        accidentally break the default branch.
        """
        autotuner = _import_autotuner()
        math_engine = _import_math_engine()

        sortino_value = 1.5
        r_pct = [2.0, 3.0, -1.0, 4.0, 1.5]
        trial = _make_trial(value=sortino_value, daily_returns=r_pct)

        _winner, _p_adj, winner_tstat = autotuner._haircut_select(
            [trial],
            tstat_fn=autotuner.compute_sortino_tstat,
        )

        # Sortino branch (D10 / RM-H1): _haircut_select calls
        # tstat_fn(series, seed=trial_idx). For trial_idx=0, seed=0.
        # Expected value is the same call so the test always stays in sync
        # regardless of bootstrap parameter tuning.
        # Tolerance exact equality: same function, same inputs, same seed.
        expected_sortino_tstat = autotuner.compute_sortino_tstat(
            r_pct, seed=0
        )

        # rel=1e-9: deterministic arithmetic (same bootstrap RNG path).
        assert winner_tstat == pytest.approx(expected_sortino_tstat, rel=1e-9), (
            f"Sortino branch _haircut_select returned t={winner_tstat!r}.\n"
            f"  expected (bootstrap t-stat on r_pct, seed=0): "
            f"{expected_sortino_tstat!r}\n"
            f"  The CRRA-001 fix must NOT affect the Sortino branch. The\n"
            f"  Sortino branch passes the raw series — no U-transform."
        )


# ===========================================================================
# §2 — CRRA-001: Tail-heavy distribution divergence test
# ===========================================================================


class TestHaircutSelectCrraVsSharpeDivergence:
    """Tail-heavy distribution where CRRA-EU and Sharpe-like t-stats materially differ.

    Uses the fixture's scenario_contract: 8-day return series with extreme losses
    (-50%, -40%, -30%) that CRRA utility penalizes nonlinearly.

    The test verifies that the _haircut_select t-stat matches the CRRA-EU form,
    which is materially different from the raw-pct Sharpe-like form.

    This is the 'Sharpe-vs-CRRA divergence test' from the team-lead mandate.
    """

    def test_tail_heavy_distribution_crra_not_sharpe(self):
        """For a tail-heavy distribution, the CRRA t-stat != the Sharpe t-stat.

        Uses the fixture's contract scenario (8 days: [-50, 20, -30, 25, 10, -40, 15, 5]%)
        where the relative divergence between t_crra_eu and t_from_raw_pct is ~52%.

        Asserts: returned t-stat matches t_crra_eu, not t_from_raw_pct.

        Tolerance on correct: rel=1e-9.
        Separator tolerance: rel=0.05 (the values differ by >50%, so 5% is conservative).

        H-6 precedent note: using compute_crra_eu_tstat(raw_pct_series) is an H-6
        category error — the function expects U-values, treating raw percent as utility
        values computes mean(r_pct)/(sd(r_pct)/sqrt(T)) = Sharpe * sqrt(T).
        """
        autotuner = _import_autotuner()
        fixture = _load_fixture()
        sc = fixture["scenario_contract"]

        r_pct = sc["r_pct"]
        t_crra_eu = sc["expected"]["t_crra_eu"]
        t_from_raw = sc["expected"]["t_from_raw_pct"]
        rel_div = sc["expected"]["relative_divergence"]

        trial = _make_trial(value=0.5, daily_returns=r_pct)

        _winner, _p_adj, winner_tstat = autotuner._haircut_select(
            [trial],
            tstat_fn=autotuner.compute_crra_eu_tstat,
        )

        # Positive identity: must match CRRA-EU.
        assert winner_tstat == pytest.approx(t_crra_eu, rel=1e-9), (
            f"Expected CRRA-EU t-stat {t_crra_eu!r}, got {winner_tstat!r}.\n"
            f"  Distribution: {r_pct}\n"
            f"  The CRRA t-stat and Sharpe t-stat diverge by {rel_div:.0%}.\n"
            f"  A broken implementation would return {t_from_raw!r} (Sharpe form)."
        )

        # Negative identity: must NOT match the raw-pct Sharpe form.
        assert winner_tstat != pytest.approx(t_from_raw, rel=0.05), (
            f"_haircut_select returned the raw-pct Sharpe t-stat {t_from_raw!r}.\n"
            f"  This is the CRRA-001 bug. The distribution has ~52% relative\n"
            f"  divergence, so the two values cannot be within 5% of each other.\n"
            f"  returned={winner_tstat!r}  wrong_value={t_from_raw!r}"
        )


# ===========================================================================
# §3 — NEFF-001: compute_n_effective called at both _haircut_select sites
# ===========================================================================


class TestNEffectiveWiredIntoAutotunerRun:
    """NEFF-001: compute_n_effective must be called at both production call sites.

    The audit confirmed compute_n_effective has zero production calls. Both
    _haircut_select sites in run_autotuner omit n_effective, so the BHY haircut
    always uses N_effective = N_optuna (the pre-n-effective fallback behavior).

    These tests mock compute_n_effective and _haircut_select, then run
    run_autotuner end-to-end on a minimal fixture, and assert:
      (a) compute_n_effective WAS called (at least once) during the run.
      (b) _haircut_select WAS called with n_effective= the value returned by
          compute_n_effective.

    The tests are RED until run_autotuner wires compute_n_effective into
    both _haircut_select call sites.
    """

    @contextlib.contextmanager
    def _patched_autotuner_run(self, n_effective_return_value: int = 150):
        """Patch harness for run_autotuner wiring tests.

        Mocks: Optuna study, synthetic_history, chart DB, symphony strategy,
        vwap helper, compute_n_effective, _haircut_select.

        Does NOT mock save_autotune_run — left real so the DB write path is
        exercised in the ARCH-001 tests that inherit this context.
        """
        import database as db_module

        fake_trial = MagicMock()
        fake_trial.value = 0.8
        fake_trial.user_attrs = {"daily_returns": [0.5, 1.0, -0.2, 0.3, 0.7]}
        fake_trial.params = {
            "TRIGGER_THRESHOLD_PCT": 15.0,
            "TAKE_PROFIT_MC_PCT": 5.0,
            "VWAP_CROSS_HWM_PCT": 0.51,
            "VWAP_BLEED_MULTIPLIER": 1.5,
            "VWAP_BLEED_TICKS": 10,
            "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
            "MAX_PARABOLIC_SQUEEZE": 0.5,
        }

        fake_study = MagicMock(name="fake_study")
        fake_study.best_params = fake_trial.params.copy()
        fake_study.best_value = 0.8
        fake_study.optimize = MagicMock(return_value=None)
        fake_study.trials = [fake_trial]

        # Minimal 10-day history — enough for a non-empty fold without Optuna work.
        # 10 days: 6 train / 2 validation / 2 frozen-eval (60/20/20).
        tick = {
            "return": 1.5,
            "mc_prob": 50.0,
            "vol": 1.2,
            "vwap_diff": 0.0,
            "base_atr_pct": 1.0,
            "valid_vwap_weight": 1.0,
        }
        history = {
            "sym-1": {f"2026-05-{d:02d}": [tick] for d in range(1, 11)}
        }

        # compute_n_effective is the function under test — spy on its calls.
        # Return the configured value so _haircut_select receives it.
        mock_n_effective = MagicMock(
            return_value=n_effective_return_value,
            side_effect=lambda n_optuna, ledger_query, **kw: n_effective_return_value,
        )

        # _haircut_select mock: return a winning trial so the run completes
        # normally. We spy on it to assert it was called with n_effective.
        mock_haircut = MagicMock(
            return_value=(fake_trial, 0.04, 1.8)
        )

        with patch("autotuner.optuna.create_study", return_value=fake_study), \
             patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()), \
             patch("autotuner.synthetic_history.generate_synthetic_history",
                   return_value=history), \
             patch("autotuner.database.load_chart_history", return_value={}), \
             patch("autotuner.database.save_chart_archive"), \
             patch("autotuner.database.get_symphony_strategy",
                   return_value={
                       "params": db_module.DEFAULT_STRATEGY.copy(),
                       "locked_vars": [],
                   }), \
             patch("autotuner.database.DEFAULT_STRATEGY", db_module.DEFAULT_STRATEGY.copy()), \
             patch("autotuner.math_engine.compute_vwap_breakdown_update",
                   side_effect=lambda **kw: (0, 0, False, False)), \
             patch("autotuner.compute_n_effective", mock_n_effective), \
             patch("autotuner._haircut_select", mock_haircut):
            yield {
                "mock_n_effective": mock_n_effective,
                "mock_haircut": mock_haircut,
                "fake_trial": fake_trial,
            }

    def test_compute_n_effective_is_called_during_autotuner_run(self):
        """run_autotuner must call compute_n_effective at least once per symphony.

        NEFF-001: currently compute_n_effective is never called in production.
        This test is RED until run_autotuner wires compute_n_effective before
        the _haircut_select call.

        We mock compute_n_effective and assert it was called (call_count >= 1).
        """
        import autotuner

        bot_state = {"sym-1": {"name": "Test Symphony A", "account_uuid": "acc-1"}}
        spec_bundle_id = _make_phase1_theory_bundle()

        buf = io.StringIO()
        with self._patched_autotuner_run() as mocks:
            with contextlib.redirect_stdout(buf):
                autotuner.run_autotuner(
                    bot_state, "2026-05-10", ["acc-1"],
                    spec_bundle_id=spec_bundle_id,
                )

        mock_n_effective = mocks["mock_n_effective"]
        assert mock_n_effective.call_count >= 1, (
            f"compute_n_effective was never called during run_autotuner.\n"
            f"  call_count = {mock_n_effective.call_count}\n"
            f"  NEFF-001: both _haircut_select call sites in run_autotuner\n"
            f"  (autotuner.py:1558 and :1838) omit n_effective. Wire:\n"
            f"  n_eff = compute_n_effective(n_optuna=len(haircut_trials),\n"
            f"      ledger_query=lambda: database.get_researcher_dof_ledger_for_run(...))\n"
            f"  then pass n_effective=n_eff to _haircut_select."
        )

    def test_haircut_select_called_with_n_effective_from_compute_n_effective(self):
        """_haircut_select must receive n_effective from compute_n_effective.

        NEFF-001: the wiring contract requires that the value returned by
        compute_n_effective is forwarded to _haircut_select as n_effective=.

        We spy on both functions: compute_n_effective returns 150 (a sentinel
        value different from any natural len(haircut_trials)). Assert _haircut_select
        was called with n_effective=150.
        """
        import autotuner

        n_eff_sentinel = 150
        bot_state = {"sym-1": {"name": "Test Symphony A", "account_uuid": "acc-1"}}
        spec_bundle_id = _make_phase1_theory_bundle()

        buf = io.StringIO()
        with self._patched_autotuner_run(n_effective_return_value=n_eff_sentinel) as mocks:
            with contextlib.redirect_stdout(buf):
                autotuner.run_autotuner(
                    bot_state, "2026-05-10", ["acc-1"],
                    spec_bundle_id=spec_bundle_id,
                )

        mock_haircut = mocks["mock_haircut"]
        assert mock_haircut.call_count >= 1, (
            "Expected _haircut_select to be called at least once."
        )

        # Find any call that was made with n_effective=150.
        n_eff_values_seen = []
        for c in mock_haircut.call_args_list:
            n_eff_values_seen.append(c.kwargs.get("n_effective"))
        assert n_eff_sentinel in n_eff_values_seen, (
            f"_haircut_select was not called with n_effective={n_eff_sentinel}.\n"
            f"  n_effective values seen across all calls: {n_eff_values_seen}\n"
            f"  NEFF-001: compute_n_effective returned {n_eff_sentinel} but it\n"
            f"  was not forwarded to _haircut_select as n_effective=. Wire:\n"
            f"  _haircut_select(haircut_trials, n_effective=n_eff, tstat_fn=...)"
        )

    def test_n_effective_with_backtest_selection_rows_exceeds_n_optuna(self):
        """When researcher_dof_ledger has BACKTEST_SELECTION rows, n_effective > n_optuna.

        NEFF-001 S>0 effect: this tests the end-to-end behavioral consequence of
        wiring compute_n_effective. Seed the researcher_dof_ledger with one
        BACKTEST_SELECTION facet (n_configs_searched=25), then assert that the
        n_effective value passed to _haircut_select is > len(haircut_trials).

        Uses a real (not mocked) compute_n_effective — only _haircut_select
        is mocked so we can observe what n_effective it received.
        """
        import autotuner
        import database as db_module

        bot_state = {"sym-1": {"name": "Test Symphony A", "account_uuid": "acc-1"}}
        spec_bundle_id = _make_phase1_theory_bundle()

        # Seed researcher_dof_ledger with a BACKTEST_SELECTION row.
        # S = 25 extra DOF (n_configs_searched=25 in the ledger row).
        db_module.insert_dof_ledger_row(
            facet_name="test_facet",
            facet_category="specification",
            decision_type="SEARCHED",
            evidence_source="BACKTEST_SELECTION",
            n_configs_searched=25,
            touched_frozen_eval=0,
            spec_bundle_id=None,  # not the winner bundle — must be counted
        )

        mock_haircut = MagicMock(return_value=(
            MagicMock(value=0.8, params={
                "TRIGGER_THRESHOLD_PCT": 15.0,
                "TAKE_PROFIT_MC_PCT": 5.0,
                "VWAP_CROSS_HWM_PCT": 0.51,
                "VWAP_BLEED_MULTIPLIER": 1.5,
                "VWAP_BLEED_TICKS": 10,
                "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
                "MAX_PARABOLIC_SQUEEZE": 0.5,
            }),
            0.04, 1.8,
        ))

        tick = {
            "return": 1.5, "mc_prob": 50.0, "vol": 1.2,
            "vwap_diff": 0.0, "base_atr_pct": 1.0, "valid_vwap_weight": 1.0,
        }
        history = {"sym-1": {f"2026-05-{d:02d}": [tick] for d in range(1, 11)}}

        fake_trial = MagicMock()
        fake_trial.value = 0.8
        fake_trial.user_attrs = {"daily_returns": [0.5, 1.0, -0.2]}
        fake_trial.params = {
            "TRIGGER_THRESHOLD_PCT": 15.0,
            "TAKE_PROFIT_MC_PCT": 5.0,
            "VWAP_CROSS_HWM_PCT": 0.51,
            "VWAP_BLEED_MULTIPLIER": 1.5,
            "VWAP_BLEED_TICKS": 10,
            "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
            "MAX_PARABOLIC_SQUEEZE": 0.5,
        }
        fake_study = MagicMock(name="fake_study_s_positive")
        fake_study.best_params = fake_trial.params.copy()
        fake_study.best_value = 0.8
        fake_study.optimize = MagicMock(return_value=None)
        fake_study.trials = [fake_trial]

        buf = io.StringIO()
        with patch("autotuner.optuna.create_study", return_value=fake_study), \
             patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()), \
             patch("autotuner.synthetic_history.generate_synthetic_history",
                   return_value=history), \
             patch("autotuner.database.load_chart_history", return_value={}), \
             patch("autotuner.database.save_chart_archive"), \
             patch("autotuner.database.get_symphony_strategy",
                   return_value={
                       "params": db_module.DEFAULT_STRATEGY.copy(),
                       "locked_vars": [],
                   }), \
             patch("autotuner.database.DEFAULT_STRATEGY", db_module.DEFAULT_STRATEGY.copy()), \
             patch("autotuner.math_engine.compute_vwap_breakdown_update",
                   side_effect=lambda **kw: (0, 0, False, False)), \
             patch("autotuner._haircut_select", mock_haircut):
            with contextlib.redirect_stdout(buf):
                autotuner.run_autotuner(
                    bot_state, "2026-05-10", ["acc-1"],
                    spec_bundle_id=spec_bundle_id,
                )

        assert mock_haircut.call_count >= 1, (
            "Expected _haircut_select to be called at least once."
        )

        # _haircut_select must have been called with n_effective > n_optuna.
        # n_optuna = len(haircut_trials) = 1 (one fake trial); n_effective >= 1+25=26.
        n_eff_values_seen = [
            c.kwargs.get("n_effective") for c in mock_haircut.call_args_list
        ]
        non_none = [v for v in n_eff_values_seen if v is not None]

        assert non_none, (
            f"_haircut_select was called but n_effective was not passed (got None).\n"
            f"  call_args_list: {mock_haircut.call_args_list}\n"
            f"  NEFF-001: with S>0 BACKTEST_SELECTION rows in researcher_dof_ledger,\n"
            f"  n_effective must be computed and forwarded as n_effective= to _haircut_select."
        )

        max_n_eff = max(non_none)
        # n_optuna (haircut_trials after sentinel filter) is 1 here; with S=25 it must be >= 26.
        assert max_n_eff > 1, (
            f"n_effective={max_n_eff} was not greater than n_optuna=1.\n"
            f"  With S=25 (one BACKTEST_SELECTION row, n_configs_searched=25),\n"
            f"  N_effective = N_optuna + S = 1 + 25 = 26. Got: {max_n_eff}.\n"
            f"  NEFF-001: compute_n_effective must read researcher_dof_ledger and\n"
            f"  accumulate S correctly."
        )

    def test_n_effective_s_positive_increases_yekutieli_c_n(self):
        """S>0 inflates N_effective, which inflates the Yekutieli c(N) factor.

        When S>0, the haircut should be STRICTER (higher c(N) => higher adjusted p-values
        => harder to clear the FDR gate). Verify this property holds at the
        benjamini_hochberg_adjust level with Shape A padding.

        Uses autotuner.benjamini_hochberg_adjust directly — does not require
        run_autotuner to be wired. Confirms that the Shape A padding mechanism
        in _haircut_select correctly tightens the haircut when N_effective > N_optuna.
        """
        autotuner = _import_autotuner()

        # Three trials with the same p-values.
        p_vals = [0.02, 0.04, 0.08]

        # Baseline (S=0): adjust over 3 p-values.
        adj_s0 = autotuner.benjamini_hochberg_adjust(p_vals)

        # S=3: pad with 3 copies of 1.0 (Shape A); N_effective = 6.
        padded = p_vals + [1.0, 1.0, 1.0]
        adj_padded = autotuner.benjamini_hochberg_adjust(padded)
        # adj_padded[:3] are the adjusted values for the original 3 trials.
        adj_s3 = adj_padded[:3]

        # With S>0, every adjusted p-value for real trials must be >= the S=0 value.
        # (Strictness property: larger N inflates c(N) = harmonic number, inflating p_adj.)
        for i, (s0, s3) in enumerate(zip(adj_s0, adj_s3)):
            assert s3 >= s0 - _BHY_TOL, (
                f"Adjusted p-value at index {i} DECREASED with S=3: "
                f"adj_s0={s0!r}, adj_s3={s3!r}. "
                f"Larger N_effective must produce >= adjusted p-values "
                f"(Shape A; plan D3 strictness property)."
            )

        # At least one adjusted p-value must STRICTLY increase (otherwise the
        # padding is doing nothing and the test is vacuous).
        any_strictly_greater = any(s3 > s0 + _BHY_TOL for s0, s3 in zip(adj_s0, adj_s3))
        assert any_strictly_greater, (
            f"No adjusted p-value strictly increased with S=3 padding.\n"
            f"  adj_s0 = {adj_s0}\n"
            f"  adj_s3 = {adj_s3}\n"
            f"  The Yekutieli c(N) must inflate when N grows, making the haircut\n"
            f"  strictly harder to clear. Shape A padding is the mechanism."
        )


# ===========================================================================
# §4 — ARCH-001: save_autotune_run must persist EUT columns
# ===========================================================================


class TestArchSaveAutotuneRunEutColumns:
    """ARCH-001: save_autotune_run must accept and persist all 9 EUT columns.

    Migration 020 added 9 columns to autotune_runs: spec_bundle_id, d_spec,
    n_effective, ce_metric, cvar_feasible, gamma, lambda_budget,
    overfitting_verdict, paired_heuristic_study_name.

    Currently save_autotune_run has no parameters for these and the INSERT
    statement does not include them — every row leaves these NULL forever.

    These tests are RED until save_autotune_run is extended with the parameters.
    """

    _EUT_COLUMNS = [
        "spec_bundle_id",
        "n_effective",
        "d_spec",
        "gamma",
        "overfitting_verdict",
    ]
    # Phase-2 columns that stay NULL in Phase 1 (acceptable NULL in this cycle):
    _PHASE2_NULL_COLUMNS = ["cvar_feasible", "lambda_budget"]

    def _call_save_autotune_run_with_eut_values(self, db_module) -> None:
        """Call save_autotune_run with all Phase-1 EUT values populated."""
        db_module.save_autotune_run(
            run_timestamp="2026-05-26T12:00:00Z",
            symphony_id="test_symphony",
            oos_alpha=0.5,
            train_alpha=1.0,
            baseline_decision="Adopted AI",
            fallback_oos_alpha=0.2,
            default_oos_alpha=0.1,
            selection_tstat=1.8,
            naive_sharpe=2.0,
            validation_sharpe=None,   # CRRA-EU path: Sortino not applicable
            frozen_eval_sharpe=None,  # CRRA-EU path: Sortino not applicable
            # EUT Phase-1 columns (the new parameters):
            spec_bundle_id="abc123bundle",
            n_effective=150,
            d_spec=0,
            gamma=2.0,
            overfitting_verdict="D_spec=0,N_effective=N_optuna,no_violations",
        )

    def test_save_autotune_run_accepts_spec_bundle_id_parameter(self):
        """save_autotune_run must accept spec_bundle_id as a keyword argument.

        ARCH-001: currently save_autotune_run at database.py:407 has no
        spec_bundle_id parameter. This test is RED until the signature is extended.
        """
        import inspect
        import database as db_module

        sig = inspect.signature(db_module.save_autotune_run)
        assert "spec_bundle_id" in sig.parameters, (
            "save_autotune_run must accept spec_bundle_id as a parameter.\n"
            "  ARCH-001: migration 020 added spec_bundle_id TEXT DEFAULT NULL\n"
            "  to autotune_runs, but the write path was not updated."
        )

    def test_save_autotune_run_accepts_n_effective_parameter(self):
        """save_autotune_run must accept n_effective as a keyword argument."""
        import inspect
        import database as db_module

        sig = inspect.signature(db_module.save_autotune_run)
        assert "n_effective" in sig.parameters, (
            "save_autotune_run must accept n_effective as a parameter.\n"
            "  ARCH-001: migration 020 added n_effective INTEGER DEFAULT NULL."
        )

    def test_save_autotune_run_accepts_gamma_parameter(self):
        """save_autotune_run must accept gamma as a keyword argument."""
        import inspect
        import database as db_module

        sig = inspect.signature(db_module.save_autotune_run)
        assert "gamma" in sig.parameters, (
            "save_autotune_run must accept gamma as a parameter.\n"
            "  ARCH-001: migration 020 added gamma REAL DEFAULT NULL."
        )

    def test_save_autotune_run_accepts_overfitting_verdict_parameter(self):
        """save_autotune_run must accept overfitting_verdict as a keyword argument."""
        import inspect
        import database as db_module

        sig = inspect.signature(db_module.save_autotune_run)
        assert "overfitting_verdict" in sig.parameters, (
            "save_autotune_run must accept overfitting_verdict as a parameter.\n"
            "  ARCH-001: migration 020 added overfitting_verdict TEXT DEFAULT NULL."
        )

    def test_save_autotune_run_persists_eut_columns_non_null(self):
        """After calling save_autotune_run with EUT values, the row must have
        non-NULL values for all Phase-1 EUT columns.

        ARCH-001: even if the signature is extended, the INSERT statement must
        include the new columns. This test reads the row back from the DB and
        asserts each EUT column is non-NULL.
        """
        import sqlite3
        import database as db_module

        self._call_save_autotune_run_with_eut_values(db_module)

        conn = db_module.get_connection()
        rows = conn.execute(
            "SELECT spec_bundle_id, n_effective, d_spec, gamma, overfitting_verdict "
            "FROM autotune_runs WHERE symphony_id = 'test_symphony'"
        ).fetchall()
        conn.close()

        assert rows, "Expected one autotune_runs row for 'test_symphony'; got none."
        row = rows[0]
        col_names = ["spec_bundle_id", "n_effective", "d_spec", "gamma", "overfitting_verdict"]

        for i, col in enumerate(col_names):
            assert row[i] is not None, (
                f"autotune_runs.{col} is NULL after save_autotune_run with EUT values.\n"
                f"  ARCH-001: the INSERT in save_autotune_run does not include {col}.\n"
                f"  row = {dict(zip(col_names, row))}"
            )

    def test_save_autotune_run_persists_correct_spec_bundle_id_value(self):
        """The spec_bundle_id written must match the value passed to save_autotune_run."""
        import database as db_module

        self._call_save_autotune_run_with_eut_values(db_module)

        conn = db_module.get_connection()
        row = conn.execute(
            "SELECT spec_bundle_id FROM autotune_runs WHERE symphony_id = 'test_symphony'"
        ).fetchone()
        conn.close()

        assert row is not None, "Expected autotune_runs row; got none."
        # spec_bundle_id is TEXT — must match exactly, not by int id.
        assert row[0] == "abc123bundle", (
            f"autotune_runs.spec_bundle_id = {row[0]!r}, expected 'abc123bundle'.\n"
            f"  TYPE-002 guard: spec_bundle_id is the bundle_hash TEXT, not the\n"
            f"  integer id. The column must store the hash string as passed."
        )

    def test_save_autotune_run_persists_correct_n_effective_value(self):
        """The n_effective written must match the integer value passed."""
        import database as db_module

        self._call_save_autotune_run_with_eut_values(db_module)

        conn = db_module.get_connection()
        row = conn.execute(
            "SELECT n_effective FROM autotune_runs WHERE symphony_id = 'test_symphony'"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 150, (
            f"autotune_runs.n_effective = {row[0]!r}, expected 150.\n"
            f"  ARCH-001: the value must be written, not silently dropped."
        )

    def test_save_autotune_run_persists_overfitting_verdict_string(self):
        """The overfitting_verdict TEXT must be written verbatim."""
        import database as db_module

        self._call_save_autotune_run_with_eut_values(db_module)

        conn = db_module.get_connection()
        row = conn.execute(
            "SELECT overfitting_verdict FROM autotune_runs WHERE symphony_id = 'test_symphony'"
        ).fetchone()
        conn.close()

        assert row is not None
        expected_verdict = "D_spec=0,N_effective=N_optuna,no_violations"
        assert row[0] == expected_verdict, (
            f"autotune_runs.overfitting_verdict = {row[0]!r}, expected {expected_verdict!r}.\n"
            f"  ARCH-001: the verdict string format from the audit report must be\n"
            f"  persisted verbatim."
        )


# ===========================================================================
# §5 — End-to-end smoke: full run with canonical THEORY bundle
# ===========================================================================


class TestEndToEndAutotunerEutAuditTrail:
    """End-to-end smoke: canonical THEORY bundle run produces a complete EUT audit trail.

    Runs run_autotuner end-to-end (mocking Optuna + synthetic_history) with a
    canonical Phase-1 THEORY bundle. Asserts that the autotune_runs row has:
      - Non-NULL spec_bundle_id matching the bundle_hash of the used bundle.
      - Non-NULL n_effective.
      - Non-NULL gamma matching the bundle's frozen gamma facet.
      - Non-NULL overfitting_verdict with the expected format.

    This is the green-criteria end-to-end smoke from the team-lead mandate.
    It is RED until all of CRRA-001, NEFF-001, and ARCH-001 are fixed.
    """

    def test_autotune_run_row_has_full_phase1_eut_audit_trail(self):
        """A full run with a canonical THEORY bundle must produce a complete EUT row.

        GREEN criteria: autotune_runs row is non-NULL for spec_bundle_id,
        n_effective, gamma, and overfitting_verdict after a complete run.
        """
        import autotuner
        import database as db_module

        spec_bundle_id = _make_phase1_theory_bundle()

        # Get the bundle_hash so we can verify spec_bundle_id is written as hash.
        conn = db_module.get_connection()
        bundle_hash = conn.execute(
            "SELECT bundle_hash FROM spec_bundles WHERE id = ?", (spec_bundle_id,)
        ).fetchone()[0]
        conn.close()

        bot_state = {"sym-1": {"name": "Test Symphony A", "account_uuid": "acc-1"}}

        fake_trial = MagicMock()
        fake_trial.value = 0.8
        fake_trial.user_attrs = {"daily_returns": [0.5, 1.0, -0.2, 0.3, 0.7]}
        fake_trial.params = {
            "TRIGGER_THRESHOLD_PCT": 15.0,
            "TAKE_PROFIT_MC_PCT": 5.0,
            "VWAP_CROSS_HWM_PCT": 0.51,
            "VWAP_BLEED_MULTIPLIER": 1.5,
            "VWAP_BLEED_TICKS": 10,
            "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
            "MAX_PARABOLIC_SQUEEZE": 0.5,
        }
        fake_study = MagicMock(name="e2e_fake_study")
        fake_study.best_params = fake_trial.params.copy()
        fake_study.best_value = 0.8
        fake_study.optimize = MagicMock(return_value=None)
        fake_study.trials = [fake_trial]

        tick = {
            "return": 1.5, "mc_prob": 50.0, "vol": 1.2,
            "vwap_diff": 0.0, "base_atr_pct": 1.0, "valid_vwap_weight": 1.0,
        }
        history = {"sym-1": {f"2026-05-{d:02d}": [tick] for d in range(1, 11)}}

        buf = io.StringIO()
        with patch("autotuner.optuna.create_study", return_value=fake_study), \
             patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()), \
             patch("autotuner.synthetic_history.generate_synthetic_history",
                   return_value=history), \
             patch("autotuner.database.load_chart_history", return_value={}), \
             patch("autotuner.database.save_chart_archive"), \
             patch("autotuner.database.get_symphony_strategy",
                   return_value={
                       "params": db_module.DEFAULT_STRATEGY.copy(),
                       "locked_vars": [],
                   }), \
             patch("autotuner.database.DEFAULT_STRATEGY", db_module.DEFAULT_STRATEGY.copy()), \
             patch("autotuner.math_engine.compute_vwap_breakdown_update",
                   side_effect=lambda **kw: (0, 0, False, False)):
            with contextlib.redirect_stdout(buf):
                autotuner.run_autotuner(
                    bot_state, "2026-05-10", ["acc-1"],
                    spec_bundle_id=spec_bundle_id,
                )

        # Read the autotune_runs row back.
        conn = db_module.get_connection()
        row = conn.execute(
            "SELECT spec_bundle_id, n_effective, d_spec, gamma, overfitting_verdict "
            "FROM autotune_runs WHERE symphony_id = 'test symphony a'"
        ).fetchone()
        conn.close()

        assert row is not None, (
            "No autotune_runs row found after a complete run.\n"
            "  Expected a row for symphony_id='test symphony a' "
            "(normalize_name lowercases with spaces, not underscores)."
        )

        spec_bundle_id_db, n_effective_db, d_spec_db, gamma_db, verdict_db = row

        # spec_bundle_id must be the bundle_hash (TEXT), not the integer id.
        assert spec_bundle_id_db is not None, (
            "autotune_runs.spec_bundle_id is NULL after a complete run.\n"
            "ARCH-001: run_autotuner must pass spec_bundle_id (the hash) to save_autotune_run."
        )
        assert spec_bundle_id_db == bundle_hash, (
            f"autotune_runs.spec_bundle_id = {spec_bundle_id_db!r}, "
            f"expected bundle_hash {bundle_hash!r}.\n"
            f"  TYPE-002 guard: the integer id {spec_bundle_id} must be resolved\n"
            f"  to its bundle_hash TEXT before persisting."
        )

        # n_effective must be non-NULL and >= 1 (at minimum equals n_optuna=1).
        assert n_effective_db is not None, (
            "autotune_runs.n_effective is NULL after a complete run.\n"
            "NEFF-001 + ARCH-001: compute_n_effective result must be passed to save_autotune_run."
        )
        assert n_effective_db >= 1, (
            f"autotune_runs.n_effective = {n_effective_db!r} — must be >= 1 (n_optuna)."
        )

        # gamma must be non-NULL and match the bundle's frozen gamma=2.0.
        assert gamma_db is not None, (
            "autotune_runs.gamma is NULL after a complete run.\n"
            "ARCH-001: gamma from spec_facets must be passed to save_autotune_run."
        )
        # Tolerance: 1e-9 absolute — this is a stored float derived from the string '2.0'.
        assert gamma_db == pytest.approx(2.0, abs=1e-9), (
            f"autotune_runs.gamma = {gamma_db!r}, expected 2.0 (from THEORY-frozen facet).\n"
            f"  The gamma column must reflect the bundle's frozen gamma."
        )

        # overfitting_verdict must be non-NULL and contain at least 'D_spec='.
        assert verdict_db is not None, (
            "autotune_runs.overfitting_verdict is NULL after a complete run.\n"
            "ARCH-001: an overfitting verdict string must be assembled and persisted."
        )
        assert "D_spec=" in verdict_db, (
            f"autotune_runs.overfitting_verdict = {verdict_db!r} does not contain 'D_spec='.\n"
            f"  The verdict format must include the D_spec count (audit report §2 ARCH-001)."
        )
