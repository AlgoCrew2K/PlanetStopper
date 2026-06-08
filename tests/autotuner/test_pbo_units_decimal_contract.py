"""RED tests — C1: the PBO gate must store DECIMAL returns, not RAW PERCENT.

Audit finding C1 (validation/overfitting.md, GATE2-PLAN A1):
  The Optuna objective closure in run_autotuner builds cscv_date_returns and
  persists it via trial.set_user_attr("cscv_date_returns", ...). The store site
  (autotuner.py:2178) does `cscv_date_returns[_date] = _ga` where `_ga` is the
  RAW PERCENT guard-alpha from _collect_sim_returns_dated (a -1% day is -1.0).

  This dict is consumed by math_engine.compute_pbo -> compute_crra_eu_objective,
  which contracts on DECIMAL returns: W = max(WEALTH_ARG_FLOOR, 1 + r). Feeding
  raw percent floors W to WEALTH_ARG_FLOOR on any day <= -1% (W = 1 + (-1.0) = 0.0
  -> floored to 0.001 -> U = -999), corrupting the IS-best argmax and the OOS rank
  inside the PBO gate -> a wrong overfitting veto.

  The sibling inline path-score (autotuner.py:2169) ALREADY divides by
  RETURN_PCT_TO_FRACTION; only the cscv_date_returns store site does not.

What the fix must do (CALLER-SIDE, autotuner.py only):
  Divide each stored value by RETURN_PCT_TO_FRACTION at the store site so the
  PBO gate receives decimals matching its contract. compute_pbo and
  compute_crra_eu_objective must NOT change (their existing golden tests in
  tests/math_engine/test_compute_pbo.py must stay GREEN).

RED markers:
  - The behavioral integration test observes the value actually persisted via
    trial.set_user_attr("cscv_date_returns", ...): pre-fix a -1% day stores -1.0;
    post-fix it stores -0.01.
  - The consumer-contract tests prove the decimal-unit utility over the stored
    series is finite and > -1.0 (no floor collapse), while raw-percent collapses.
  - A regression pin proves a config set that SHOULD pass the PBO_REJECT_THRESHOLD
    gate still passes under correct (decimal) units (no flipped veto).

No producer-computed value is hardcoded: the expected decimal is DERIVED at
runtime from the raw input via the live autotuner.RETURN_PCT_TO_FRACTION
constant; the floor-collapse assertions are inequalities, not exact floats.

Fixture: tests/fixtures/math/pbo_units_decimal_contract.json
"""
from __future__ import annotations

import json
import math
import pathlib
from unittest.mock import MagicMock, patch

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_PATH = (
    _WORKTREE_ROOT / "tests" / "fixtures" / "math" / "pbo_units_decimal_contract.json"
)


@pytest.fixture(scope="module")
def units_fixture() -> dict:
    assert _FIXTURE_PATH.exists(), (
        f"Fixture not found: {_FIXTURE_PATH}. "
        "Create tests/fixtures/math/pbo_units_decimal_contract.json first."
    )
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


# ===========================================================================
# Consumer-contract: decimal units do not floor; raw percent collapses.
# These pin the consequence of the unit choice on the live consumer
# (compute_crra_eu_objective, which compute_pbo calls per IS/OOS combination).
# ===========================================================================


class TestDecimalUnitsDoNotFloor:
    """The decimal-unit utility must be finite and > -1.0; raw-percent floors."""

    def test_sub_one_percent_decimal_does_not_floor_wealth_argument(self, units_fixture):
        """A -1% day in DECIMAL (-0.01) must not floor W; in RAW PERCENT (-1.0) it does.

        W = max(WEALTH_ARG_FLOOR, 1 + r). Decimal -0.01 -> W = 0.99 (no floor).
        Raw percent -1.0 -> W = max(0.001, 0.0) = 0.001 (FLOORED). The expected
        decimal is derived from the raw value via the live RETURN_PCT_TO_FRACTION.
        """
        import autotuner
        import math_engine

        sc = units_fixture["scenarios"]["sub_one_percent_day_raw_vs_decimal"]
        raw = sc["raw_percent_minus_one_percent"]
        decimal = raw / autotuner.RETURN_PCT_TO_FRACTION  # derived, not hardcoded

        w_raw = max(math_engine.WEALTH_ARG_FLOOR, 1.0 + raw)
        w_decimal = max(math_engine.WEALTH_ARG_FLOOR, 1.0 + decimal)

        assert w_raw == pytest.approx(math_engine.WEALTH_ARG_FLOOR, abs=1e-12), (
            f"A -1% day stored as RAW PERCENT ({raw}) must floor W to "
            f"WEALTH_ARG_FLOOR ({math_engine.WEALTH_ARG_FLOOR}); got W={w_raw}. "
            "This is the C1 collapse — the PBO gate must NOT receive raw percent."
        )
        assert w_decimal > math_engine.WEALTH_ARG_FLOOR, (
            f"A -1% day stored as DECIMAL ({decimal}) must NOT floor W; "
            f"got W={w_decimal} (expected ~0.99). The store site must divide by "
            "RETURN_PCT_TO_FRACTION so the PBO gate receives decimals."
        )

    def test_decimal_series_utility_is_finite_and_above_minus_one(self, units_fixture):
        """compute_crra_eu_objective over the DECIMAL series is finite and > -1.0.

        The raw-percent series collapses to a large-negative value (floor on any
        sub--1% day). Both bounds are derived from the live functions; the decimal
        bound is an inequality (no exact float pinned). Tolerance n/a (inequality).
        """
        import autotuner
        import math_engine

        sc = units_fixture["scenarios"]["mixed_guard_alpha_series_raw_percent"]
        raw_series = sc["raw_percent_series"]
        gamma = sc["gamma"]
        decimal_series = [r / autotuner.RETURN_PCT_TO_FRACTION for r in raw_series]

        u_decimal = math_engine.compute_crra_eu_objective(decimal_series, gamma)
        u_raw = math_engine.compute_crra_eu_objective(raw_series, gamma)

        assert math.isfinite(u_decimal), (
            f"Decimal-unit mean(U) must be finite; got {u_decimal!r}."
        )
        assert u_decimal > sc["decimal_utility_lower_bound_exclusive"], (
            f"Decimal-unit mean(U)={u_decimal!r} must be > "
            f"{sc['decimal_utility_lower_bound_exclusive']} (no WEALTH_ARG_FLOOR "
            "collapse). A value near -999 means raw percent reached the utility."
        )
        assert u_raw <= sc["raw_utility_collapses_below"], (
            f"Raw-percent mean(U)={u_raw!r} must collapse <= "
            f"{sc['raw_utility_collapses_below']} (the C1 floor). If this does NOT "
            "collapse, the scenario is not exercising the sub--1% floor and the "
            "test is not discriminating."
        )
        # The two must be materially different — proves the unit choice is load-bearing.
        assert u_raw != pytest.approx(u_decimal, rel=1.0), (
            "Raw and decimal mean(U) must differ materially (the unit error flips "
            f"the value): raw={u_raw!r}, decimal={u_decimal!r}."
        )


# ===========================================================================
# Behavioral integration: the value persisted via set_user_attr must be decimal.
# Drives the real objective(trial) closure inside run_autotuner with a mocked
# _collect_sim_returns_dated yielding a known raw-percent -1% day. Pre-fix the
# stored cscv_date_returns value is -1.0; post-fix it is -0.01.
# Mock only network/IO/heavy machinery; the math engine is exercised directly.
# ===========================================================================


class TestCscvDateReturnsStoredInDecimalUnits:
    """The persisted cscv_date_returns value must be in decimal units."""

    def _run_objective_once_and_capture_user_attrs(self, dated_returns_pct):
        """Drive run_autotuner's objective(trial) once; return the dict of
        user_attrs captured from the MagicMock trial's set_user_attr calls.

        dated_returns_pct: list[tuple[str, float]] yielded by the mocked
        _collect_sim_returns_dated (RAW PERCENT guard-alpha per date).
        """
        import autotuner
        import math_engine

        # Flat returns for the inline path-score path (not under test here).
        flat_returns_pct = [v for (_d, v) in dated_returns_pct]

        crra_facets = [
            {"facet_name": "objective_kind", "facet_value": "crra_eu"},
            {"facet_name": "gamma", "facet_value": "2.0"},
            {"facet_name": "utility_family", "facet_value": "CRRA"},
        ]

        mock_trial = MagicMock()
        mock_trial.suggest_float.return_value = 0.5
        mock_trial.suggest_int.return_value = 2

        # Capture set_user_attr(name, value) calls into a plain dict.
        captured: dict = {}

        def _capture_set_user_attr(name, value):
            captured[name] = value

        mock_trial.set_user_attr.side_effect = _capture_set_user_attr

        mock_study = MagicMock()
        mock_study.best_value = -0.1
        mock_study.best_params = {
            "TAKE_PROFIT_MC_PCT": 0.5, "VWAP_CROSS_HWM_PCT": 0.5,
            "VWAP_BLEED_MULTIPLIER": 0.5, "VWAP_BLEED_TICKS": 2,
            "PARABOLIC_VELOCITY_THRESHOLD": 0.5, "MAX_PARABOLIC_SQUEEZE": 0.5,
        }
        mock_study.trials = []

        def _fake_optimize(objective_fn, n_trials=500, n_jobs=-1):
            mock_study.best_value = objective_fn(mock_trial)

        mock_study.optimize = _fake_optimize

        fake_sym_id = "acc123__sym456"
        fake_bot_state = {fake_sym_id: {"name": "TestSymphony", "is_live": False}}
        fake_strat_data = {"locked_vars": [], "params": {
            "TAKE_PROFIT_MC_PCT": 0.5, "VWAP_CROSS_HWM_PCT": 0.5,
            "VWAP_BLEED_MULTIPLIER": 0.5, "VWAP_BLEED_TICKS": 2,
            "PARABOLIC_VELOCITY_THRESHOLD": 0.5, "MAX_PARABOLIC_SQUEEZE": 0.5,
        }}
        # >= 60 dates so the CPCV machinery produces non-empty folds.
        fake_history = {
            fake_sym_id: {
                f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}": [{"return": 0.5}]
                for i in range(90)
            }
        }

        with patch("autotuner.database") as mock_db, \
             patch("autotuner._collect_sim_returns", return_value=flat_returns_pct), \
             patch("autotuner._collect_sim_returns_dated", return_value=dated_returns_pct), \
             patch("autotuner.compute_sortino_ratio", return_value=1.0), \
             patch("autotuner.optuna") as mock_optuna, \
             patch("autotuner._apply_optuna_archive_migration_if_needed"), \
             patch("autotuner.validate_nn1_compliance", return_value=(True, [])), \
             patch("autotuner.validate_search_space_nn1"), \
             patch("autotuner.calculate_historical_deviation", return_value={}), \
             patch("autotuner.synthetic_history.generate_synthetic_history",
                   return_value=fake_history), \
             patch("autotuner.synthetic_history.HistoryShortfallError", Exception):

            mock_db.get_spec_bundle_by_id.return_value = {
                "bundle_hash": "fakehash123",
                "frozen_at": "2026-05-26T00:00:00Z",
            }
            mock_db.get_spec_facets_for_bundle.return_value = crra_facets
            mock_db.get_symphony_strategy.return_value = fake_strat_data
            mock_db.normalize_name.side_effect = lambda x: x.lower().replace(" ", "_")
            mock_db.load_chart_history.return_value = {}
            mock_db.save_chart_archive.return_value = None
            mock_db.record_autotune_run.return_value = None
            mock_db.update_symphony_strategy.return_value = None
            mock_db.get_researcher_dof_ledger_for_run.return_value = []
            mock_db.PHASE1_THEORY_GAMMA = "2.0"
            mock_db.DEFAULT_STRATEGY = {
                "TAKE_PROFIT_MC_PCT": 0.5, "VWAP_CROSS_HWM_PCT": 0.5,
                "VWAP_BLEED_MULTIPLIER": 0.5, "VWAP_BLEED_TICKS": 2,
                "PARABOLIC_VELOCITY_THRESHOLD": 0.5, "MAX_PARABOLIC_SQUEEZE": 0.5,
            }

            mock_optuna.create_study.return_value = mock_study
            mock_optuna.logging.WARNING = 30
            mock_optuna.logging.set_verbosity = MagicMock()
            mock_optuna.storages.RDBStorage.return_value = MagicMock()
            mock_optuna.samplers.TPESampler.return_value = MagicMock()
            mock_optuna.pruners.NopPruner.return_value = MagicMock()

            autotuner.run_autotuner(
                fake_bot_state, "2026-05-26", ["acc123"],
                is_forced=True, spec_bundle_id=1,
            )

        return captured

    def test_minus_one_percent_day_persisted_as_decimal_not_raw(self, units_fixture):
        """cscv_date_returns must store a -1% day as -0.01 (decimal), not -1.0 (raw).

        The mocked _collect_sim_returns_dated yields a raw-percent -1% day (-1.0).
        The objective must divide by RETURN_PCT_TO_FRACTION before storing, so the
        persisted value is -0.01. Pre-fix it is -1.0 (RED). The expected decimal is
        derived from the raw value via the live RETURN_PCT_TO_FRACTION constant.
        """
        import autotuner

        raw_minus_one = units_fixture["scenarios"][
            "sub_one_percent_day_raw_vs_decimal"
        ]["raw_percent_minus_one_percent"]
        the_date = "2026-01-05"
        dated_returns_pct = [(the_date, raw_minus_one)]

        captured = self._run_objective_once_and_capture_user_attrs(dated_returns_pct)

        assert "cscv_date_returns" in captured, (
            "The objective closure must persist cscv_date_returns via set_user_attr. "
            f"Captured user_attrs: {sorted(captured.keys())}."
        )
        stored = captured["cscv_date_returns"]
        assert the_date in stored, (
            f"cscv_date_returns must contain the triggered date {the_date}; "
            f"got keys {sorted(stored.keys())}."
        )

        expected_decimal = raw_minus_one / autotuner.RETURN_PCT_TO_FRACTION
        assert stored[the_date] == pytest.approx(expected_decimal, abs=1e-12), (
            # Tolerance abs=1e-12: exact double-precision division by 100.0; the
            # store is a single divide so no accumulated error.
            f"cscv_date_returns[{the_date!r}] = {stored[the_date]!r}; expected "
            f"{expected_decimal!r} (= {raw_minus_one!r} / "
            f"{autotuner.RETURN_PCT_TO_FRACTION!r}).\n"
            "  C1 BUG: pre-fix the store site keeps RAW PERCENT (-1.0), which floors "
            "WEALTH_ARG_FLOOR inside compute_pbo. The store must divide by "
            "RETURN_PCT_TO_FRACTION, mirroring the inline path-score at autotuner.py:2169."
        )

    def test_persisted_cscv_value_does_not_floor_in_pbo_utility(self, units_fixture):
        """The persisted cscv_date_returns value, fed to compute_crra_eu_objective
        (as compute_pbo does), must NOT floor the wealth argument.

        End-to-end consequence of the store-unit choice: take the value the
        objective actually persisted for a -1% day and run it through the same
        utility compute_pbo uses. Decimal -> finite, > -1.0; raw -> -999 collapse.
        """
        import autotuner
        import math_engine

        raw_minus_one = units_fixture["scenarios"][
            "sub_one_percent_day_raw_vs_decimal"
        ]["raw_percent_minus_one_percent"]
        the_date = "2026-01-05"

        captured = self._run_objective_once_and_capture_user_attrs(
            [(the_date, raw_minus_one)]
        )
        stored = captured["cscv_date_returns"]
        u_stored = math_engine.compute_crra_eu_objective([stored[the_date]], 2.0)

        assert u_stored > -1.0, (
            f"compute_crra_eu_objective over the persisted cscv value "
            f"({stored[the_date]!r}) = {u_stored!r} must be > -1.0 (no floor). "
            "A value near -999 proves the store kept raw percent and the PBO gate "
            "floors on a sub--1% day (C1)."
        )


# ===========================================================================
# Regression pin: the unit fix must not flip a correct PBO veto decision.
# compute_pbo itself is caller-agnostic and unchanged; this pins that under
# correct (decimal) units a config set that SHOULD pass the gate still passes.
# ===========================================================================


class TestPboVetoDecisionUnchangedUnderCorrectUnits:
    """A config set that passes the PBO gate under decimal units must keep passing."""

    def test_low_pbo_config_set_still_passes_gate(self, units_fixture):
        """A config set with a dominating IS-best (low PBO) passes the
        PBO_REJECT_THRESHOLD gate under correct decimal units.

        Inputs are DECIMAL (compute_pbo's contract). The gate decision is derived
        from the computed PBO vs the live PBO_REJECT_THRESHOLD; no PBO float is
        hardcoded.
        """
        import math_engine

        sc = units_fixture["scenarios"]["pbo_veto_preserved_under_correct_units"]
        configs = sc["configs_date_returns_decimal"]
        eligible_dates = sc["eligible_dates"]
        gamma = sc["gamma"]
        S = sc["S"]

        pbo = math_engine.compute_pbo(configs, eligible_dates, gamma, S=S)

        assert math.isfinite(pbo) and 0.0 <= pbo <= 1.0, (
            f"compute_pbo must return a finite PBO in [0,1]; got {pbo!r}."
        )
        gate_passes = pbo <= math_engine.PBO_REJECT_THRESHOLD
        assert gate_passes, (
            f"PBO={pbo!r} must be <= PBO_REJECT_THRESHOLD "
            f"({math_engine.PBO_REJECT_THRESHOLD}) so the veto does NOT fire. "
            "Config 0 dominates IS and OOS; the IS-best generalizes. If the veto "
            "fires here, the unit fix flipped a correct decision (or compute_pbo "
            "changed — it must not)."
        )


# ===========================================================================
# Scope guard: compute_pbo must remain caller-agnostic (no math_engine change
# for C1). The C1 fix is CALLER-SIDE in autotuner.py.
# ===========================================================================


class TestComputePboUnchangedByC1:
    """compute_pbo must already contract on decimal inputs (C1 is caller-side)."""

    def test_compute_pbo_contracts_on_decimal_inputs(self):
        """compute_pbo on decimal inputs returns a sensible PBO; the C1 fix does
        NOT touch compute_pbo (its existing golden tests must stay GREEN)."""
        import math_engine

        dates = [f"2024-01-{i + 1:02d}" for i in range(16)]
        # Config 0 dominates with higher decimal returns; config 1 is weaker.
        configs = [
            {d: 0.012 for d in dates},
            {d: 0.003 for d in dates},
        ]
        pbo = math_engine.compute_pbo(configs, dates, gamma=2.0, S=4)
        assert isinstance(pbo, float) and math.isfinite(pbo) and 0.0 <= pbo <= 1.0, (
            f"compute_pbo on decimal inputs must return a finite PBO in [0,1]; "
            f"got {pbo!r}. C1 must not alter compute_pbo's contract."
        )
