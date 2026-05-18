"""
RED tests for autotuner port-mode extensions (AC-P2.11.*) and methodology
amendments F1-F4, N1, N3+N4.

Surfaces covered:
  F1  — DSR T-correction call site pinning (T = validation_calendar_days * N_symphonies for port studies)
  F2  — TRAIN/VAL/FROZEN split: 50/20/30 for port-mode (was 60/20/20)
  F3  — sortino_sentinel_pct telemetry on autotune_runs row
  F4  — mode-invariant set is correct; VWAP_BREAK_CONFIRM_TICKS is a constant (not tuned)
  N1  — study-name shape: {timestamp}__{account_id}__port vs {timestamp}__{symphony_id}
  N3+N4 — get_latest_autotune_run new signature (math_mode + account_id optional params)
  AC-P2.11.1 — math_mode column on autotune_runs
  AC-P2.11.2 — mode-specific vs mode-invariant search space split
  AC-P2.11.3 — DSR T-correction
  AC-P2.11.4 — frozen-eval >= 25 trading days for port-mode
  AC-P2.11.5 — fail-STOP if port-mode params missing
  AC-P2.11.7 — get_latest_autotune_run accepts math_mode and account_id
"""

from __future__ import annotations

import inspect
import re

import pytest


# These imports WILL FAIL until implemented (RED intent)
from autotuner import (  # noqa: F401
    PORT_TRAIN_RATIO,
    PORT_VALIDATION_RATIO,
    PORT_FROZEN_EVAL_RATIO,
    MODE_SPECIFIC_PARAMS,
    MODE_INVARIANT_PARAMS,
    build_port_study_name,
    build_symphony_study_name,
)
from database import get_latest_autotune_run  # noqa: F401


# ---------------------------------------------------------------------------
# F2: TRAIN/VAL/FROZEN split for port-mode: 50/20/30
# ---------------------------------------------------------------------------

class TestPortModeTrainValFrozenSplit:
    """Amendment F2: Port-mode split is 50/20/30 (not 60/20/20)."""

    def test_port_train_ratio_is_0_50(self):
        assert PORT_TRAIN_RATIO == pytest.approx(0.50, rel=1e-6), (
            "Amendment F2: PORT_TRAIN_RATIO must be 0.50 (not 0.60)"
        )

    def test_port_validation_ratio_is_0_20(self):
        assert PORT_VALIDATION_RATIO == pytest.approx(0.20, rel=1e-6)

    def test_port_frozen_eval_ratio_is_0_30(self):
        assert PORT_FROZEN_EVAL_RATIO == pytest.approx(0.30, rel=1e-6), (
            "Amendment F2: PORT_FROZEN_EVAL_RATIO must be 0.30 (not 0.20)"
        )

    def test_port_split_sums_to_one(self):
        total = PORT_TRAIN_RATIO + PORT_VALIDATION_RATIO + PORT_FROZEN_EVAL_RATIO
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_port_split_differs_from_per_symphony_split(self):
        """Port-mode split must differ from the per-symphony 60/20/20 split."""
        import autotuner
        assert PORT_TRAIN_RATIO != autotuner.TRAIN_RATIO, (
            "PORT_TRAIN_RATIO (0.50) must differ from per-symphony TRAIN_RATIO (0.60)"
        )
        assert PORT_FROZEN_EVAL_RATIO != autotuner.FROZEN_EVAL_RATIO, (
            "PORT_FROZEN_EVAL_RATIO (0.30) must differ from per-symphony FROZEN_EVAL_RATIO (0.20)"
        )

    def test_frozen_eval_at_least_25_trading_days_for_port_mode(self):
        """
        AC-P2.11.4: frozen-eval fold >= 25 trading days for port-mode.
        At 125 trading days total, PORT_FROZEN_EVAL_RATIO=0.30 -> 37.5 days (>= 25). PASS.
        At minimum viable total_days, check that 30% still meets the floor.
        """
        TOTAL_DAYS = 125
        frozen_days = int(TOTAL_DAYS * PORT_FROZEN_EVAL_RATIO)
        assert frozen_days >= 25, (
            f"AC-P2.11.4: frozen-eval must be >= 25 trading days for port-mode; got {frozen_days}"
        )


# ---------------------------------------------------------------------------
# F1: DSR T-correction call site pinning
# ---------------------------------------------------------------------------

class TestDSRTCorrectionCallSite:
    """
    Amendment F1: DSR T-correction at autotuner.py:724 (approx).
    For port-mode studies: T = validation_calendar_days * N_symphonies_in_account.
    For per-symphony studies: T = validation_calendar_days (existing behavior).
    """

    def test_dsr_t_uses_n_symphonies_for_port_mode(self):
        """
        For port studies, T must be multiplied by N_symphonies in the account.
        The function compute_port_study_dsr_T must exist and implement this.
        """
        from autotuner import compute_dsr_T  # noqa: F401
        # Per-symphony: T = validation_calendar_days * 1
        t_per_sym = compute_dsr_T(
            validation_calendar_days=25,
            math_mode="per_symphony",
            n_symphonies=1,
        )
        assert t_per_sym == 25

        # Port-mode: T = validation_calendar_days * N_symphonies
        t_port = compute_dsr_T(
            validation_calendar_days=25,
            math_mode="port_level",
            n_symphonies=4,
        )
        assert t_port == 100, (
            "Amendment F1: port DSR T = validation_calendar_days * N_symphonies = 25*4 = 100"
        )

    def test_dsr_t_per_symphony_ignores_n_symphonies(self):
        """Per-symphony DSR T is NOT multiplied by N_symphonies."""
        from autotuner import compute_dsr_T
        t_1 = compute_dsr_T(validation_calendar_days=20, math_mode="per_symphony", n_symphonies=1)
        t_3 = compute_dsr_T(validation_calendar_days=20, math_mode="per_symphony", n_symphonies=3)
        assert t_1 == t_3 == 20, (
            "Per-symphony DSR T must NOT be multiplied by n_symphonies (F1: ELSE branch)"
        )

    def test_dsr_t_is_always_positive(self):
        from autotuner import compute_dsr_T
        for mode in ("per_symphony", "port_level"):
            t = compute_dsr_T(validation_calendar_days=10, math_mode=mode, n_symphonies=2)
            assert t > 0


# ---------------------------------------------------------------------------
# F4: Mode-invariant vs mode-specific search space split
# ---------------------------------------------------------------------------

class TestModeInvariantAndSpecificParams:
    """
    Amendment F4:
    MODE_INVARIANT: TAKE_PROFIT_MC_PCT, VWAP_BLEED_MULTIPLIER, VWAP_BLEED_TICKS,
                    MAX_PARABOLIC_SQUEEZE (single shared study, mode-blind).
    MODE_SPECIFIC:  PARABOLIC_VELOCITY_THRESHOLD, VWAP_CROSS_HWM_PCT.
    VWAP_BREAK_CONFIRM_TICKS is a math_engine.py constant — NOT tuned.
    """

    def test_mode_invariant_set_contains_required_params(self):
        expected_invariant = {
            "TAKE_PROFIT_MC_PCT",
            "VWAP_BLEED_MULTIPLIER",
            "VWAP_BLEED_TICKS",
            "MAX_PARABOLIC_SQUEEZE",
        }
        assert expected_invariant.issubset(MODE_INVARIANT_PARAMS), (
            f"MODE_INVARIANT_PARAMS missing: {expected_invariant - set(MODE_INVARIANT_PARAMS)}"
        )

    def test_mode_specific_set_contains_required_params(self):
        expected_specific = {"PARABOLIC_VELOCITY_THRESHOLD", "VWAP_CROSS_HWM_PCT"}
        assert expected_specific.issubset(MODE_SPECIFIC_PARAMS), (
            f"MODE_SPECIFIC_PARAMS missing: {expected_specific - set(MODE_SPECIFIC_PARAMS)}"
        )

    def test_vwap_break_confirm_ticks_not_in_tuned_params(self):
        """
        Amendment F4: VWAP_BREAK_CONFIRM_TICKS is a math_engine.py constant, NOT tuned.
        Must NOT appear in either mode-invariant or mode-specific search spaces.
        """
        all_tuned = set(MODE_INVARIANT_PARAMS) | set(MODE_SPECIFIC_PARAMS)
        assert "VWAP_BREAK_CONFIRM_TICKS" not in all_tuned, (
            "Amendment F4: VWAP_BREAK_CONFIRM_TICKS is a constant, must NOT be in search space"
        )

    def test_mode_invariant_and_specific_are_disjoint(self):
        """No param should appear in both sets."""
        overlap = set(MODE_INVARIANT_PARAMS) & set(MODE_SPECIFIC_PARAMS)
        assert not overlap, f"Params appear in both mode-invariant and mode-specific: {overlap}"

    def test_port_mode_search_space_only_includes_specific_params(self):
        """
        In port-mode, only MODE_SPECIFIC params are re-tuned per study.
        MODE_INVARIANT params come from the shared study.
        Verify get_port_mode_search_space returns only the mode-specific subset.
        """
        from autotuner import get_port_mode_search_space
        space = get_port_mode_search_space()
        assert "PARABOLIC_VELOCITY_THRESHOLD" in space
        assert "VWAP_CROSS_HWM_PCT" in space
        # Mode-invariant params must NOT be in the port-specific study
        for invariant_param in MODE_INVARIANT_PARAMS:
            assert invariant_param not in space, (
                f"Mode-invariant param {invariant_param} must not be in port-mode-specific search space"
            )


# ---------------------------------------------------------------------------
# N1: Study-name shape
# ---------------------------------------------------------------------------

class TestStudyNameShape:
    """
    Amendment N1:
    Port-mode: {timestamp}__{account_id}__port
    Per-symphony: {timestamp}__{symphony_id} (preserves O3 convention)
    """

    def test_port_study_name_shape(self):
        name = build_port_study_name(timestamp="20240501T100000", account_id="acct-test-001")
        assert name == "20240501T100000__acct-test-001__port", (
            f"Port study name shape must be '{{timestamp}}__{{account_id}}__port', got {name!r}"
        )

    def test_symphony_study_name_shape(self):
        name = build_symphony_study_name(timestamp="20240501T100000", symphony_id="my-symphony")
        assert name == "20240501T100000__my-symphony", (
            f"Symphony study name shape must be '{{timestamp}}__{{symphony_id}}', got {name!r}"
        )

    def test_port_study_name_ends_with_port_suffix(self):
        name = build_port_study_name(timestamp="20240101T120000", account_id="acct-x")
        assert name.endswith("__port"), "Port study name must end with '__port'"

    def test_symphony_study_name_does_not_contain_port_suffix(self):
        name = build_symphony_study_name(timestamp="20240101T120000", symphony_id="sym-y")
        assert "__port" not in name, "Symphony study name must not contain '__port'"

    def test_port_study_name_contains_double_underscore_separators(self):
        name = build_port_study_name(timestamp="20240101T120000", account_id="acct-001")
        parts = name.split("__")
        assert len(parts) == 3, f"Port study name must have 3 parts separated by '__', got: {name!r}"
        assert parts[2] == "port"


# ---------------------------------------------------------------------------
# N3+N4: get_latest_autotune_run new signature
# ---------------------------------------------------------------------------

class TestGetLatestAutotuneRunSignature:
    """
    Amendment N3+N4: get_latest_autotune_run gets optional math_mode and account_id params.
    Legacy callers (no new params) unchanged.
    """

    def test_signature_accepts_math_mode_and_account_id(self):
        sig = inspect.signature(get_latest_autotune_run)
        assert "math_mode" in sig.parameters, (
            "N3+N4: get_latest_autotune_run must accept 'math_mode' optional parameter"
        )
        assert "account_id" in sig.parameters, (
            "N3+N4: get_latest_autotune_run must accept 'account_id' optional parameter"
        )

    def test_math_mode_defaults_to_none_or_per_symphony(self):
        sig = inspect.signature(get_latest_autotune_run)
        default = sig.parameters["math_mode"].default
        assert default in (None, "per_symphony", inspect.Parameter.empty) or default is None, (
            "N3+N4: math_mode default should preserve legacy behavior (None or 'per_symphony')"
        )

    def test_legacy_call_signature_still_works(self, tmp_path, monkeypatch):
        """Legacy callers that pass only symphony_id must still work unchanged."""
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test_atr.db"))
        import database
        database.init_db()
        # Should not raise even when called with old-style single-arg
        result = get_latest_autotune_run("sym-does-not-exist")
        assert result is None

    def test_new_account_id_column_on_autotune_runs(self, tmp_path, monkeypatch):
        """
        AC-P2.11.1 + N3+N4: autotune_runs table must have account_id column (migration 012).
        """
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test_012.db"))
        import database
        database.init_db()
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(autotune_runs)")
        cols = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert "account_id" in cols, "N3+N4 / AC-P2.11.1: autotune_runs must have account_id column"
        assert "math_mode" in cols, "AC-P2.11.1: autotune_runs must have math_mode column"

    def test_port_mode_query_by_account_id_and_math_mode(self, tmp_path, monkeypatch):
        """Port-mode lookup: WHERE account_id=? AND math_mode='port_level'."""
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test_port_lookup.db"))
        import database
        database.init_db()
        # Insert a fake port-mode run
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO autotune_runs (run_timestamp, symphony_id, account_id, math_mode) "
            "VALUES (?, ?, ?, ?)",
            ("2024-11-01T10:00:00", "__port__", "acct-lookup-001", "port_level"),
        )
        conn.commit()
        conn.close()

        result = get_latest_autotune_run(
            symphony_id="__port__",
            account_id="acct-lookup-001",
            math_mode="port_level",
        )
        assert result is not None, "Port-mode lookup must find the inserted port-level run"
        assert result["math_mode"] == "port_level"
        assert result["account_id"] == "acct-lookup-001"

    def test_fail_stop_if_port_level_params_missing(self, tmp_path, monkeypatch):
        """
        AC-P2.11.5: Engine must fail-STOP if port_level authority requested but no
        port-level autotune_run exists for that account.
        """
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test_failstop.db"))
        import database
        database.init_db()

        from autotuner import validate_port_mode_params_available
        result = validate_port_mode_params_available(account_id="acct-no-port-runs")
        assert result["available"] is False
        assert result["fail_stop"] is True, (
            "AC-P2.11.5: missing port-level params must trigger fail-STOP"
        )


# ---------------------------------------------------------------------------
# AC-P2.11.1: math_mode column on autotune_runs; NULL backfill safe
# ---------------------------------------------------------------------------

class TestMathModeColumn:

    def test_math_mode_column_exists(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test_mm.db"))
        import database
        database.init_db()
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(autotune_runs)")
        cols = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert "math_mode" in cols

    def test_new_per_symphony_run_records_math_mode(self, tmp_path, monkeypatch):
        """New per-symphony runs must record math_mode='per_symphony'."""
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test_mm2.db"))
        import database
        database.init_db()
        database.record_autotune_run(
            run_timestamp="2024-11-01T11:00:00",
            symphony_id="sym-test",
            math_mode="per_symphony",
            oos_alpha=0.05,
            train_alpha=0.04,
        )
        result = get_latest_autotune_run("sym-test", math_mode="per_symphony")
        assert result is not None
        assert result["math_mode"] == "per_symphony"


# ---------------------------------------------------------------------------
# F3: sortino_sentinel_pct telemetry on autotune_runs row
# ---------------------------------------------------------------------------

class TestSortinoSentinelPctTelemetry:
    """Amendment F3: sortino_sentinel_pct field on autotune_runs row."""

    def test_autotune_runs_has_sortino_sentinel_pct_column(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test_ssp.db"))
        import database
        database.init_db()
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(autotune_runs)")
        cols = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert "sortino_sentinel_pct" in cols, (
            "Amendment F3: autotune_runs must have sortino_sentinel_pct column"
        )

    def test_record_autotune_run_accepts_sortino_sentinel_pct(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test_ssp2.db"))
        import database
        database.init_db()
        database.record_autotune_run(
            run_timestamp="2024-11-02T09:00:00",
            symphony_id="sym-ssp",
            math_mode="per_symphony",
            sortino_sentinel_pct=0.03,
        )
        result = get_latest_autotune_run("sym-ssp", math_mode="per_symphony")
        assert result is not None
        assert result["sortino_sentinel_pct"] == pytest.approx(0.03, rel=1e-6), (
            "Amendment F3: sortino_sentinel_pct must be persisted on autotune_runs"
        )
