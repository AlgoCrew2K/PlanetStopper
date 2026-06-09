"""RED tests — C2b: the BHY haircut t-stat must not be inflated by CPCV path duplication.

Audit finding C2b (validation/overfitting.md, GATE2-PLAN A3):
  The Optuna objective accumulates the per-trial return series with
  `all_path_returns.extend(path_returns)` over _CPCV_N_PATHS paths
  (autotuner.py:2172), then persists it as the `daily_returns` user_attr
  (autotuner.py:2183). _haircut_select reads daily_returns and computes
  compute_crra_eu_tstat = mean(U) / (sd(U) / sqrt(T)).

  Because the CPCV paths are collapsed (C2a — all 5 identical) the extend
  produces 5 copies of the same series, so T is 5x too large and the t-stat
  is inflated by ~sqrt(5) (2.32x observed; slightly above sqrt(5) due to the
  ddof=1 Bessel correction). An inflated t -> smaller one-sided p-value ->
  the Benjamini-Hochberg-Yekutieli FDR gate (HARVEY_LIU_FDR_Q) is too easy to
  clear -> noise-grade AI proposals pass.

  The date-keyed cscv_date_returns dict (autotuner.py:2178) is ALREADY
  collision-free (one entry per distinct triggered date); only the
  list-extended daily_returns is inflated.

What the fix must do (autotuner.py objective closure):
  Aggregate daily_returns per-date (mirror the cscv_date_returns dict pattern)
  so T == the distinct triggered-date count, NOT _CPCV_N_PATHS x that count.
  _haircut_select and compute_crra_eu_tstat must NOT change.

  DEPENDENCY: this rides on the C2a aggregation fix. The invariant asserted
  here (T == distinct dates) holds regardless of whether the paths are
  distinct, because the per-day sim is state-independent (autotuner.py:1335
  re-inits day_state per day) — the live impact is the t-stat T, not path
  diversity. See the team-lead escalation re: C2a distinctness being
  unachievable under the current architecture.

RED marker:
  - test_persisted_daily_returns_length_equals_distinct_dates: pre-fix
    len(daily_returns) == _CPCV_N_PATHS x distinct count (15 for a 3-date sim);
    post-fix it equals the distinct count (3 == len(cscv_date_returns)).

Characterization pins (prove the inflation BITES; remain valid pre/post fix —
they pin compute_crra_eu_tstat / _haircut_select behavior, which must NOT change):
  - test_five_x_duplication_inflates_tstat_by_sqrt_n_paths
  - test_fdr_gate_flips_when_series_is_five_x_duplicated

No producer-computed value is hardcoded: the inflation ratio and the gate
decisions are derived at runtime from the live functions; the length invariant
compares two live-persisted lengths against each other, not a literal.

Fixture: tests/fixtures/math/haircut_tstat_no_path_duplication.json
"""
from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_PATH = (
    _WORKTREE_ROOT / "tests" / "fixtures" / "math" / "haircut_tstat_no_path_duplication.json"
)


@pytest.fixture(scope="module")
def c2b_fixture() -> dict:
    assert _FIXTURE_PATH.exists(), (
        f"Fixture not found: {_FIXTURE_PATH}. "
        "Create tests/fixtures/math/haircut_tstat_no_path_duplication.json first."
    )
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _make_trial(returns_pct, value=0.5):
    """Minimal fake Optuna trial: daily_returns in raw percent (T5 provenance)."""
    return type("FakeTrial", (), {
        "value": value,
        "user_attrs": {"daily_returns": returns_pct},
        "params": {"TRIGGER_THRESHOLD_PCT": 10.0},
        "number": 0,
    })()


# ===========================================================================
# Characterization: the 5x duplication inflates the t-stat ~sqrt(N_PATHS) and
# flips the FDR gate. These pin the MECHANISM (compute_crra_eu_tstat /
# _haircut_select must remain inflation-prone to a duplicated input — the fix
# is in the OBJECTIVE that builds the series, not in these functions).
# ===========================================================================


class TestPathDuplicationInflatesTstat:
    """A 5x-concatenated U-series inflates the CRRA-EU t-stat ~sqrt(5)."""

    def test_five_x_duplication_inflates_tstat_by_sqrt_n_paths(self, c2b_fixture):
        """compute_crra_eu_tstat over a 5x series is ~sqrt(5) times the single-series
        value. The ratio is derived from the live function (no literal pinned) and
        must fall in the fixture's (1.5, 3.0) band straddling sqrt(5)~=2.236.
        """
        import autotuner

        sc = c2b_fixture["scenarios"]["tstat_inflation_ratio"]
        series_pct = sc["single_series_pct"]
        factor = sc["duplication_factor"]

        # U-transform mirrors _haircut_select's CRRA branch (raw pct -> floored W -> U).
        u_single = [
            __import__("math_engine").compute_crra_utility(
                autotuner.derive_floored_wealth_argument(r / autotuner.RETURN_PCT_TO_FRACTION),
                2.0,
            )
            for r in series_pct
        ]
        u_dup = u_single * factor

        t_single = autotuner.compute_crra_eu_tstat(u_single)
        t_dup = autotuner.compute_crra_eu_tstat(u_dup)

        assert t_single > 0, (
            f"Characterization precondition: single-series t-stat must be positive "
            f"(positive-mean series); got {t_single!r}."
        )
        ratio = t_dup / t_single
        lo = sc["ratio_lower_bound_exclusive"]
        hi = sc["ratio_upper_bound_exclusive"]
        assert lo < ratio < hi, (
            f"5x duplication inflated the t-stat by {ratio:.4f}x "
            f"(t_single={t_single:.4f}, t_dup={t_dup:.4f}); expected in ({lo}, {hi}) "
            f"straddling sqrt(5)~=2.236. If ratio~=1, T is not being inflated and the "
            "characterization is wrong; if >3, the series/T relationship is off."
        )

    def test_fdr_gate_flips_when_series_is_five_x_duplicated(self, c2b_fixture):
        """A borderline series is REJECTED by the BHY FDR gate at its true single T
        but PASSES when 5x-duplicated — the live consequence of the inflation.

        Gate decisions are derived from _haircut_select (winner is None when
        p_adj > HARVEY_LIU_FDR_Q). No p-value literal pinned.
        """
        import autotuner

        sc = c2b_fixture["scenarios"]["fdr_gate_flips_on_path_duplication"]
        series_pct = sc["single_series_pct"]
        factor = sc["duplication_factor"]

        winner_single, _, _ = autotuner._haircut_select(
            [_make_trial(series_pct)], tstat_fn=autotuner.compute_crra_eu_tstat
        )
        winner_dup, _, _ = autotuner._haircut_select(
            [_make_trial(series_pct * factor)], tstat_fn=autotuner.compute_crra_eu_tstat
        )

        single_clears = winner_single is not None
        dup_clears = winner_dup is not None

        assert single_clears == sc["single_series_clears_gate_expected"], (
            f"Single-series gate decision = {single_clears}; expected "
            f"{sc['single_series_clears_gate_expected']}. The borderline series must be "
            "REJECTED at its true T (p_adj > HARVEY_LIU_FDR_Q)."
        )
        assert dup_clears == sc["duplicated_series_clears_gate_expected"], (
            f"5x-duplicated gate decision = {dup_clears}; expected "
            f"{sc['duplicated_series_clears_gate_expected']}. The inflated t drives "
            "p_adj below the gate — noise passes. This is the C2b live impact."
        )
        # The two MUST differ — proves the duplication is decision-altering.
        assert single_clears != dup_clears, (
            "The FDR gate decision must FLIP between single and 5x-duplicated series; "
            f"both were {single_clears}. If they match, the borderline scenario lost "
            "its discriminating power (re-tune the series magnitude)."
        )


# ===========================================================================
# Behavioral RED: the persisted daily_returns must have T == distinct dates,
# not _CPCV_N_PATHS x distinct. Driven via the real objective(trial) closure.
# Pre-fix len(daily_returns) == n_paths x distinct (the extend duplication);
# post-fix it equals len(cscv_date_returns) (the date-keyed distinct count).
# ===========================================================================


class TestPersistedDailyReturnsNotPathInflated:
    """The objective must persist daily_returns at T == distinct triggered-date count."""

    def _run_objective_once_and_capture_user_attrs(self, per_path_pct, dated_returns):
        """Drive run_autotuner's objective(trial) once; return captured user_attrs.

        per_path_pct: list[float] returned by mocked _collect_sim_returns (per path).
        dated_returns: list[[date, val]] returned by mocked _collect_sim_returns_dated.
        """
        import autotuner

        crra_facets = [
            {"facet_name": "objective_kind", "facet_value": "crra_eu"},
            {"facet_name": "gamma", "facet_value": "2.0"},
            {"facet_name": "utility_family", "facet_value": "CRRA"},
        ]

        mock_trial = MagicMock()
        mock_trial.suggest_float.return_value = 0.5
        mock_trial.suggest_int.return_value = 2
        captured: dict = {}
        mock_trial.set_user_attr.side_effect = lambda n, v: captured.__setitem__(n, v)

        params = {
            "TAKE_PROFIT_MC_PCT": 0.5, "VWAP_CROSS_HWM_PCT": 0.5,
            "VWAP_BLEED_MULTIPLIER": 0.5, "VWAP_BLEED_TICKS": 2,
            "PARABOLIC_VELOCITY_THRESHOLD": 0.5, "MAX_PARABOLIC_SQUEEZE": 0.5,
        }
        mock_study = MagicMock()
        mock_study.best_value = -0.1
        mock_study.best_params = dict(params)
        mock_study.trials = []
        mock_study.optimize = lambda fn, n_trials=500, n_jobs=-1: mock_study.__setattr__(
            "best_value", fn(mock_trial)
        )

        sid = "acc123__sym456"
        bot_state = {sid: {"name": "TestSymphony", "is_live": False}}
        strat_data = {"locked_vars": [], "params": dict(params)}
        history = {
            sid: {
                f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}": [{"return": 0.5}]
                for i in range(90)
            }
        }

        with patch("autotuner.database") as mock_db, \
             patch("autotuner._collect_sim_returns", return_value=per_path_pct), \
             patch("autotuner._collect_sim_returns_dated",
                   return_value=[tuple(p) for p in dated_returns]), \
             patch("autotuner.compute_sortino_ratio", return_value=1.0), \
             patch("autotuner.optuna") as mock_optuna, \
             patch("autotuner._apply_optuna_archive_migration_if_needed"), \
             patch("autotuner.validate_nn1_compliance", return_value=(True, [])), \
             patch("autotuner.validate_search_space_nn1"), \
             patch("autotuner.calculate_historical_deviation", return_value={}), \
             patch("autotuner.synthetic_history.generate_synthetic_history",
                   return_value=history), \
             patch("autotuner.synthetic_history.HistoryShortfallError", Exception):

            mock_db.get_spec_bundle_by_id.return_value = {
                "bundle_hash": "fakehash123", "frozen_at": "2026-05-26T00:00:00Z",
            }
            mock_db.get_spec_facets_for_bundle.return_value = crra_facets
            mock_db.get_symphony_strategy.return_value = strat_data
            mock_db.normalize_name.side_effect = lambda x: x.lower().replace(" ", "_")
            mock_db.load_chart_history.return_value = {}
            mock_db.get_researcher_dof_ledger_for_run.return_value = []
            mock_db.save_autotune_run.return_value = 1
            mock_db.PHASE1_THEORY_GAMMA = "2.0"
            mock_db.DEFAULT_STRATEGY = dict(params)

            mock_optuna.create_study.return_value = mock_study
            mock_optuna.logging.WARNING = 30
            mock_optuna.logging.set_verbosity = MagicMock()
            mock_optuna.storages.RDBStorage.return_value = MagicMock()
            mock_optuna.samplers.TPESampler.return_value = MagicMock()
            mock_optuna.pruners.NopPruner.return_value = MagicMock()

            autotuner.run_autotuner(
                bot_state, "2026-05-26", ["acc123"],
                is_forced=True, spec_bundle_id=1,
            )

        return captured

    def test_persisted_daily_returns_length_equals_distinct_dates(self, c2b_fixture):
        """len(daily_returns) must equal the distinct triggered-date count
        (== len(cscv_date_returns)), NOT _CPCV_N_PATHS x that count.

        The mocked sim yields a 3-date series. Pre-fix the extend accumulation
        makes daily_returns 5x longer (15); cscv_date_returns is already 3.
        Post-fix daily_returns is aggregated per-date -> 3. The assertion compares
        two live-persisted lengths against each other plus the fixture's distinct
        count — no raw 5x literal is compared.
        """
        import autotuner

        sc = c2b_fixture["scenarios"]["daily_returns_length_equals_distinct_dates"]
        per_path = sc["mocked_per_path_returns_pct"]
        dated = sc["mocked_dated_returns"]
        distinct = sc["distinct_date_count"]

        captured = self._run_objective_once_and_capture_user_attrs(per_path, dated)

        assert "daily_returns" in captured and "cscv_date_returns" in captured, (
            "The objective must persist both daily_returns and cscv_date_returns; "
            f"captured: {sorted(captured.keys())}."
        )
        daily = captured["daily_returns"]
        cscv = captured["cscv_date_returns"]

        # Sanity: the date-keyed dict already has the distinct count.
        assert len(cscv) == distinct, (
            f"Precondition: cscv_date_returns should have {distinct} distinct dates; "
            f"got {len(cscv)}. (Mocked sim yields {distinct} dates.)"
        )
        # The RED: daily_returns must equal the distinct count, not n_paths x it.
        assert len(daily) == len(cscv), (
            f"len(daily_returns)={len(daily)} must equal len(cscv_date_returns)="
            f"{len(cscv)} (the distinct triggered-date count).\n"
            f"  C2b BUG: pre-fix the objective does all_path_returns.extend(...) over "
            f"_CPCV_N_PATHS={autotuner._CPCV_N_PATHS} paths, so daily_returns holds "
            f"{autotuner._CPCV_N_PATHS}x the series ({len(daily)} entries). This inflates "
            "the haircut t-stat ~sqrt(N_PATHS) and makes the FDR gate too easy.\n"
            "  Fix: aggregate daily_returns per-date (mirror cscv_date_returns) so T == "
            "distinct eligible-date count."
        )
        assert len(daily) == distinct, (
            f"len(daily_returns)={len(daily)} must equal the distinct-date count "
            f"{distinct}; got {len(daily)}."
        )

    def test_daily_returns_not_n_paths_inflated(self, c2b_fixture):
        """Explicit anti-inflation pin: len(daily_returns) must NOT be
        _CPCV_N_PATHS x the distinct-date count. Catches a partial fix that
        de-duplicates only some paths.
        """
        import autotuner

        sc = c2b_fixture["scenarios"]["daily_returns_length_equals_distinct_dates"]
        per_path = sc["mocked_per_path_returns_pct"]
        dated = sc["mocked_dated_returns"]
        distinct = sc["distinct_date_count"]

        captured = self._run_objective_once_and_capture_user_attrs(per_path, dated)
        daily = captured["daily_returns"]
        inflated_count = autotuner._CPCV_N_PATHS * distinct

        assert len(daily) != inflated_count, (
            f"len(daily_returns)={len(daily)} equals the inflated count "
            f"_CPCV_N_PATHS({autotuner._CPCV_N_PATHS}) x distinct({distinct}) = "
            f"{inflated_count}. The extend duplication (autotuner.py:2172) must be "
            "replaced with per-date aggregation."
        )
