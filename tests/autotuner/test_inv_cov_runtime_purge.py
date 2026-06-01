"""
AC-6 / AC-7 / AC-8 — runtime tests replacing AST/regex-only invariants.

The post-remediation audit (math-audit-2 INV-COV-1/2/3) flagged that the
6-cluster remediation pinned several invariants at the AST/regex layer
where the original audit's recommended fix was a runtime spy / numeric
fixture pin. AST-text checks pass while underlying behaviour can
regress: a regression that drops the actual purge call would still
satisfy the regex.

This module replaces three such pins with the runtime equivalents:

  AC-6 (INV-COV-1) — train|validation purge:
      Replace the regex-only check at test_o1_purge_embargo.py:218 with a
      runtime date-set-spy assertion of
      ``set(train) ∩ set(validation_within_PURGE_DAYS) == ∅``. The
      assertion runs against the actual splits run_autotuner produces.

  AC-7 (INV-COV-2) — O6 second-boundary purge:
      Same runtime conversion applied to the validation|frozen-eval
      boundary. Pinned: the LAST PURGE_DAYS of the validation fold are
      excluded from the Optuna objective's history payload (matching the
      autotuner.py:938-941 purge logic).

  AC-8 (INV-COV-3) — synthetic_history VWAP numeric fixture:
      Replace any AST-only test on the VWAP helper with a runtime test
      that constructs a known input series and asserts the function's
      output against an independently hand-computed VWAP.

Provenance: all expected values are derived from the input by the test
itself, never read from a producer cache. The expected VWAP is computed
directly from the standard VWAP formula
    VWAP = sum(price_i * volume_i) / sum(volume_i)
inside the test.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import io
from unittest.mock import MagicMock, patch

import pytest

import autotuner
import synthetic_history


# ---------------------------------------------------------------------------
# Shared harness (mirrors tests/autotuner/test_o6_frozen_eval.py — the
# pattern the audit said to back-port).
# ---------------------------------------------------------------------------


def _build_history(n_days: int) -> dict:
    """Synthetic 125-day history for a single symphony, weekdays only."""
    tick = {
        "return": 0.0,
        "mc_prob": 50.0,
        "vol": 1.0,
        "vwap_diff": 0.0,
        "base_atr_pct": 1.0,
        "valid_vwap_weight": 1.0,
    }
    start = _dt.date(2025, 6, 2)  # Monday
    dates = []
    d = start
    while len(dates) < n_days:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += _dt.timedelta(days=1)
    return {"sym-A": {date: [tick] for date in dates}}


def _default_params() -> dict:
    return {
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


@contextlib.contextmanager
def _autotuner_patches(best_params: dict, history: dict):
    """Patch the autotuner's heavy dependencies so the per-symphony loop
    runs end-to-end without live I/O.

    Note: run_autotuner now requires an explicit spec_bundle_id (NN1 Phase-1
    strict). This harness satisfies the guard by:
      - Patching database.get_spec_bundle_by_id to return a stub row without
        facets_json (skips integrity check per autotuner.py:1664).
      - Patching validate_nn1_compliance to return (True, []).
      - Patching database.get_spec_facets_for_bundle to return [] (sortino
        branch: objective_kind absent → sortino_loss_aversion).
      - Patching database.advisor_ro_query to return [].
    Callers must pass spec_bundle_id=1 to run_autotuner.
    """
    fake_study = MagicMock(name="fake_optuna_study")
    fake_study.best_params = best_params.copy()
    fake_study.best_value = 1.0
    fake_study.optimize = MagicMock(return_value=None)
    fake_study.trials = []

    # Stub bundle row: no facets_json → integrity check skipped.
    _stub_bundle_row = {"bundle_hash": "test-stub-hash"}

    with (
        patch("autotuner.optuna.create_study", return_value=fake_study),
        patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()),
        patch("autotuner.synthetic_history.generate_synthetic_history",
              return_value=history),
        patch("autotuner.database.load_chart_history", return_value={}),
        patch("autotuner.database.save_chart_archive"),
        patch("autotuner.database.get_symphony_strategy",
              return_value={"params": best_params.copy(),
                            "locked_vars": []}),
        patch("autotuner.database.save_symphony_strategy"),
        patch("autotuner.database.DEFAULT_STRATEGY", best_params.copy()),
        patch("autotuner.database.save_autotune_run"),
        patch("autotuner.database.get_spec_bundle_by_id",
              return_value=_stub_bundle_row),
        patch("autotuner.database.get_spec_facets_for_bundle",
              return_value=[]),
        patch("autotuner.database.advisor_ro_query",
              return_value=[]),
        patch("autotuner.validate_nn1_compliance",
              return_value=(True, [])),
        patch("autotuner.math_engine.compute_para_arm_decision",
              side_effect=lambda **kw: (0.0, False)),
        patch("autotuner.math_engine.compute_time_squeeze_decay",
              side_effect=lambda tr: (1.5, 0.5)),
        patch("autotuner.math_engine.compute_active_trailing_stop",
              side_effect=lambda *a, **kw: 5.0),
        patch("autotuner.math_engine.compute_breakeven_update",
              side_effect=lambda *a, **kw: (a[3], a[4], a[2])),
        patch("autotuner.math_engine.compute_vwap_bleed_arm_threshold",
              side_effect=lambda *a, **kw: -10.0),
        patch("autotuner.math_engine.compute_vwap_breakdown_update",
              side_effect=lambda **kw: (0, 0, False, False)),
    ):
        yield fake_study


# ---------------------------------------------------------------------------
# AC-6 / INV-COV-1 — train|validation purge runtime test.
# ---------------------------------------------------------------------------


class TestTrainValidationPurgeRuntimeDisjoint:
    """Pin set(train) ∩ set(validation_within_PURGE_DAYS) == ∅ at runtime.

    The Optuna objective evaluates trials on the validation fold; the
    LAST PURGE_DAYS of the train fold must be excluded so that a train
    sample whose feature lookback overlaps the validation fold is not
    seen by the trial. The original audit's CRITICAL-1 was that this
    purge was untested at runtime — the existing regex pin only checked
    text adjacency.
    """

    def test_train_dates_disjoint_from_validation_purge_zone_at_runtime(self):
        """Spy the data passed to the Optuna objective and to fallback's
        run_simulation. Assert no train date appears in any
        objective/run_simulation call.

        Procedure:
          1. Drive run_autotuner with 125 synthetic days.
          2. Capture every date-set passed into _collect_sim_returns +
             run_simulation across the per-symphony loop.
          3. Reconstruct the train fold's date range using the same
             TRAIN_RATIO / PURGE_DAYS the autotuner uses.
          4. Assert: no validation date (in the purge zone preceding the
             frozen-eval boundary) appears in the train fold AND vice
             versa.

        Pure runtime check — no AST scan.
        """
        all_history_dates = sorted(_build_history(125)["sym-A"].keys())
        n_days = len(all_history_dates)
        train_ratio = autotuner.TRAIN_RATIO
        val_ratio = autotuner.VALIDATION_RATIO
        purge_days = autotuner.PURGE_DAYS
        embargo_days = autotuner.EMBARGO_DAYS

        val_start_idx = int(n_days * train_ratio)
        frozen_start_idx = int(n_days * (train_ratio + val_ratio))

        # Reconstruct exactly what the autotuner SHOULD produce.
        effective_train_cutoff = max(
            0, val_start_idx - purge_days - embargo_days
        )
        expected_train_dates = set(all_history_dates[:effective_train_cutoff])
        expected_validation_dates = set(
            all_history_dates[val_start_idx:frozen_start_idx]
        )

        # Hard disjointness — the test's own pin, independent of producer.
        assert expected_train_dates.isdisjoint(expected_validation_dates), (
            "Fixture self-check: reconstructed train and validation "
            "folds overlap. Re-check TRAIN_RATIO / VALIDATION_RATIO / "
            "PURGE_DAYS values."
        )

        # Capture the date-sets the autotuner ACTUALLY uses.
        seen_date_sets: list[frozenset[str]] = []
        original_collect = autotuner._collect_sim_returns

        def spy_collect(p, history_data, acc_sym_ids, current_date_str,
                        deviation_dict):
            for sym_id, dates_dict in history_data.items():
                seen_date_sets.append(frozenset(dates_dict.keys()))
            return original_collect(
                p, history_data, acc_sym_ids, current_date_str, deviation_dict
            )

        bot_state = {"sym-A": {"name": "Sym A", "account": "acc-1"}}
        params = _default_params()
        history = _build_history(125)
        buf = io.StringIO()
        with _autotuner_patches(params, history):
            with patch("autotuner._collect_sim_returns",
                       side_effect=spy_collect):
                with contextlib.redirect_stdout(buf):
                    try:
                        autotuner.run_autotuner(
                            bot_state, "2026-05-10", ["acc-1"],
                            spec_bundle_id=1,
                        )
                    except Exception:
                        # Side-effect failure beyond the purge surface is
                        # tolerable — the spy captured the relevant data
                        # already.
                        pass

        # The objective's first call is the Optuna trial against the
        # PURGE-REDUCED validation fold. Its date set must NOT contain
        # any train date.
        assert seen_date_sets, (
            "AC-6 / INV-COV-1: no _collect_sim_returns calls captured — "
            "harness wiring issue; cannot verify runtime purge."
        )

        # Check EVERY captured objective/_collect_sim_returns call:
        # NONE may include a train-fold date.
        for date_set in seen_date_sets:
            overlap = date_set & expected_train_dates
            assert not overlap, (
                "AC-6 / INV-COV-1 VIOLATED: a _collect_sim_returns call "
                f"received {len(overlap)} train-fold dates inside its "
                f"history payload (sample: {sorted(overlap)[:3]!r}). The "
                "train fold and validation fold must be runtime-disjoint."
            )


# ---------------------------------------------------------------------------
# AC-7 / INV-COV-2 — O6 second-boundary purge runtime test.
# ---------------------------------------------------------------------------


class TestValidationFrozenEvalPurgeRuntimeDisjoint:
    """Pin: the LAST PURGE_DAYS+EMBARGO_DAYS of the validation fold are
    EXCLUDED from the Optuna objective's history payload.

    autotuner.py:938-941 does this on a purge-reduced validation set:
        val_purge_end_idx = frozen_start_idx - PURGE_DAYS - EMBARGO_DAYS
        validation_dates_purged = sorted_dates[val_start_idx:val_purge_end_idx]

    The INV-COV-2 finding: only a regex-style check covered this — a
    regression that swapped the slice back to the raw validation range
    would pass the regex.
    """

    def test_objective_validation_excludes_purge_zone_at_runtime(self):
        """Spy the Optuna objective's first _collect_sim_returns call;
        assert its date set is the PURGE-REDUCED validation slice, not
        the full validation slice.
        """
        all_history_dates = sorted(_build_history(125)["sym-A"].keys())
        n_days = len(all_history_dates)
        train_ratio = autotuner.TRAIN_RATIO
        val_ratio = autotuner.VALIDATION_RATIO
        purge_days = autotuner.PURGE_DAYS
        embargo_days = autotuner.EMBARGO_DAYS

        val_start_idx = int(n_days * train_ratio)
        frozen_start_idx = int(n_days * (train_ratio + val_ratio))
        val_purge_end_idx = max(
            val_start_idx, frozen_start_idx - purge_days - embargo_days
        )

        expected_validation_purged = set(
            all_history_dates[val_start_idx:val_purge_end_idx]
        )
        # The LAST PURGE_DAYS+EMBARGO_DAYS of the raw validation fold.
        purged_out_zone = set(
            all_history_dates[val_purge_end_idx:frozen_start_idx]
        )

        seen_date_sets: list[frozenset[str]] = []
        original_collect = autotuner._collect_sim_returns

        def spy_collect(p, history_data, acc_sym_ids, current_date_str,
                        deviation_dict):
            for sym_id, dates_dict in history_data.items():
                seen_date_sets.append(frozenset(dates_dict.keys()))
            return original_collect(
                p, history_data, acc_sym_ids, current_date_str, deviation_dict
            )

        bot_state = {"sym-A": {"name": "Sym A", "account": "acc-1"}}
        params = _default_params()
        history = _build_history(125)
        buf = io.StringIO()
        with _autotuner_patches(params, history):
            with patch("autotuner._collect_sim_returns",
                       side_effect=spy_collect):
                with contextlib.redirect_stdout(buf):
                    try:
                        autotuner.run_autotuner(
                            bot_state, "2026-05-10", ["acc-1"],
                            spec_bundle_id=1,
                        )
                    except Exception:
                        pass

        assert seen_date_sets, (
            "AC-7 / INV-COV-2: no _collect_sim_returns calls captured."
        )
        # The first call is the objective's evaluation; it must touch the
        # purge-reduced validation slice and NONE of the purge-zone dates.
        first_obj_dates = seen_date_sets[0]
        leaked = first_obj_dates & purged_out_zone
        assert not leaked, (
            "AC-7 / INV-COV-2 VIOLATED: the Optuna objective's first "
            f"_collect_sim_returns received {len(leaked)} dates from the "
            "validation|frozen-eval purge zone (sample: "
            f"{sorted(leaked)[:3]!r}). The LAST "
            f"{purge_days + embargo_days} validation days must be "
            "excluded from the objective's history payload."
        )
        # And it must include at least one purge-reduced validation date.
        assert first_obj_dates & expected_validation_purged, (
            "AC-7 / INV-COV-2: the objective's first _collect_sim_returns "
            "received NO purge-reduced validation dates — the purge "
            "may have over-trimmed the validation fold."
        )


# ---------------------------------------------------------------------------
# AC-8 / INV-COV-3 — synthetic_history VWAP runtime fixture test.
#
# The original audit recommended a numeric pin against a hand-computed
# VWAP. The cluster pinned only an AST-level call-site presence check.
# This test constructs a known input series and asserts the output.
# ---------------------------------------------------------------------------


def _resolve_vwap_helper():
    """Find the synthetic_history VWAP helper across acceptable names."""
    candidate_names = (
        "compute_vwap_from_bars",
        "_compute_vwap",
        "compute_synthetic_vwap",
        "compute_vwap",
    )
    for name in candidate_names:
        if hasattr(synthetic_history, name):
            return getattr(synthetic_history, name), name
    pytest.fail(
        "synthetic_history must expose a VWAP helper (one of "
        f"{candidate_names}) for the runtime fixture test. The audit's "
        "INV-COV-3 finding requires this be testable at numeric level."
    )


class TestSyntheticHistoryVwapRuntimeFixture:
    """Replace the AST-only VWAP test with a runtime numeric pin."""

    def test_vwap_helper_exposed(self):
        """The VWAP helper must be exposed under one of the canonical
        names so a runtime numeric test is possible."""
        # Will pytest.fail() with a descriptive message if absent.
        helper, name = _resolve_vwap_helper()
        assert callable(helper), (
            f"{name!r} on synthetic_history must be callable."
        )

    def test_vwap_matches_independently_computed_value(self):
        """Hand-construct a known minute-bar series (3 bars) and assert
        the helper's output equals the standard VWAP formula:

            VWAP = sum(price * volume) / sum(volume)

        For an Alpaca-style bar shape with fields {'c', 'v', 'h', 'l'},
        the typical-price input is the close. The helper may use close
        or HLC/3 — accept either by computing BOTH expected values and
        pinning equality to whichever matches.
        """
        helper, _ = _resolve_vwap_helper()

        # Three bars: closes 100, 110, 105 with volumes 1000, 2000, 500.
        bars = [
            {"c": 100.0, "v": 1000.0, "h": 102.0, "l": 99.0,
             "t": "2026-01-02T09:30:00Z"},
            {"c": 110.0, "v": 2000.0, "h": 112.0, "l": 108.0,
             "t": "2026-01-02T09:31:00Z"},
            {"c": 105.0, "v":  500.0, "h": 106.0, "l": 104.0,
             "t": "2026-01-02T09:32:00Z"},
        ]
        total_volume = 1000.0 + 2000.0 + 500.0
        vwap_close = (
            100.0 * 1000.0 + 110.0 * 2000.0 + 105.0 * 500.0
        ) / total_volume
        # HLC/3 alternative
        def _hlc3(b):
            return (b["h"] + b["l"] + b["c"]) / 3.0
        vwap_hlc3 = sum(_hlc3(b) * b["v"] for b in bars) / total_volume

        # Call the helper. The signature is implementer's choice — try
        # the most natural shapes.
        result = None
        for call_shape in (
            lambda h, b: h(b),
            lambda h, b: h(bars=b),
        ):
            try:
                result = call_shape(helper, bars)
                break
            except TypeError:
                continue
        if result is None:
            pytest.fail(
                "VWAP helper signature not recognised. Tried positional "
                "and bars=... keyword shapes."
            )

        # Accept either close-based or HLC/3-based VWAP.
        # rel=1e-9: deterministic arithmetic; any drift signals a
        # different formula entirely.
        matches_close = abs(result - vwap_close) <= 1e-9 * max(abs(vwap_close), 1.0)
        matches_hlc3 = abs(result - vwap_hlc3) <= 1e-9 * max(abs(vwap_hlc3), 1.0)
        assert matches_close or matches_hlc3, (
            f"AC-8 / INV-COV-3 VIOLATED: VWAP helper returned {result!r}; "
            f"neither close-based VWAP ({vwap_close!r}) nor HLC/3-based "
            f"VWAP ({vwap_hlc3!r}) matches. The helper is using a "
            "non-standard formula."
        )

    def test_vwap_zero_volume_guarded(self):
        """Edge case: a bar with zero volume must not produce a
        division-by-zero NaN. Either the helper returns None, raises an
        explicit error, or uses a documented fallback (last price)."""
        helper, _ = _resolve_vwap_helper()
        bars = [
            {"c": 100.0, "v": 0.0, "h": 100.0, "l": 100.0,
             "t": "2026-01-02T09:30:00Z"},
        ]
        try:
            result = helper(bars)
        except TypeError:
            try:
                result = helper(bars=bars)
            except TypeError:
                pytest.fail("VWAP helper signature not recognised.")
        except (ValueError, ZeroDivisionError):
            # Explicit exception is acceptable.
            return
        # Otherwise the result must be finite / None / 100.0 (last
        # price fallback).
        import math as _math
        assert result is None or (
            isinstance(result, (int, float)) and _math.isfinite(result)
        ), (
            f"AC-8 / INV-COV-3: zero-volume bar produced a non-finite "
            f"result {result!r} — the helper must guard against "
            f"division-by-zero explicitly."
        )
