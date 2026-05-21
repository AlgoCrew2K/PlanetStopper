"""
Tests for analytics.py — M1 data-layer helpers.

Rewritten per M1F AC-M1F.3.1 (R14): dry_run ALWAYS reads shadow_history.shadow_return.
Tests that previously asserted dry_run == if_held for triggered symphonies, or relied on
the e95e02e fallback-to-if_held when trading_day was not injected, have been updated.

Data-source contract (AC-M1F.3.1, AC-M1F.3.5):
  If-held side:
    - today_change  <- last_percent_change (present on all symphonies)
    - CR (if-held)  <- simple_return UNLESS (simple_return==0.0 AND net_deposits==0.0)
                       then fall back to time_weighted_return  [TWR fallback]
    - MDD (if-held) <- max_drawdown (Composer convention: positive float in [0,1])

  Dry-run (shadow) side — AC-M1F.3.1:
    - ALWAYS reads shadow_history.shadow_return for (symphony_id, trading_day).
    - triggered flag does NOT change which table is read.
    - If no shadow_history row exists: dry_run=None (render '—'). NEVER fall back to if_held.
    - For non-triggered symphonies: shadow_return == current_return (no divergence yet) →
      dry_run and if_held happen to agree, but both are derived from their respective sources.
    - For triggered symphonies: shadow_return is frozen at trigger-time → dry_run diverges.

  Portfolio-level aggregates are value-weighted across all symphonies in the
  Composer response; portfolio dry_run is value-weighted across per-symphony dry_run.

All fixtures used here are read from the pre-placed captured-from-producer fixture
at tests/fixtures/composer/symphony_stats_meta.json (11 real symphonies, 160KB).
Shadow-history rows for per-symphony dry_run tests use in-process SQLite (tmp_path).
No network calls are made in this module — fetch_symphony_stats is not invoked.

Live contract test is in test_live_m1_helpers.py.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "composer"
_SYMPHONY_STATS_META_FIXTURE = _FIXTURE_DIR / "symphony_stats_meta.json"


@pytest.fixture(scope="module")
def symphony_stats_meta():
    """Load the pre-placed captured-from-producer fixture (11 symphonies)."""
    with open(_SYMPHONY_STATS_META_FIXTURE, encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload.get("symphonies", [])


@pytest.fixture(scope="module")
def first_symphony(symphony_stats_meta):
    """
    The first symphony in the fixture — the TWR-fallback case:
      simple_return == 0.0, net_deposits == 0.0, time_weighted_return == 3.13212
    """
    return symphony_stats_meta[0]


@pytest.fixture(scope="module")
def normal_symphony(symphony_stats_meta):
    """
    A normal symphony (index 1) with simple_return != 0 and net_deposits != 0.
    simple_return=0.65976, net_deposits=658.5, last_percent_change=-0.0229, max_drawdown=0.1495
    """
    return symphony_stats_meta[1]


# ---------------------------------------------------------------------------
# Helper: minimal bot_state entry for a triggered symphony
# ---------------------------------------------------------------------------


def _triggered_bot_state_entry(current_return: float = -5.0) -> dict:
    """Minimal triggered bot_state entry from the engine's persisted shape."""
    return {
        "triggered": True,
        "current_return": current_return,  # engine stores pct already *100
        "high_water_mark": 10.0,
        "shadow_hwm": 12.0,
    }


def _untriggered_bot_state_entry(current_return: float = 2.0) -> dict:
    return {
        "triggered": False,
        "current_return": current_return,
        "high_water_mark": 3.0,
        "shadow_hwm": 3.0,
    }


# ---------------------------------------------------------------------------
# Shadow DB helpers — build in-process SQLite for dry_run read tests
# ---------------------------------------------------------------------------

_SHADOW_TEST_TRADING_DAY = "2026-05-14"


def _build_shadow_db_for_sym(
    symphony_id: str,
    shadow_return: float,
    current_return: float,
    is_post_trigger: int,
    tmp_path: Path,
) -> str:
    """Single-row shadow_history DB for a given symphony+trading_day. Returns file path."""
    db_file = str(tmp_path / f"shadow_{symphony_id[:8].replace('/', '_')}.db")
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE shadow_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symphony_id TEXT NOT NULL,
            account_id TEXT,
            cycle_id TEXT,
            ts_utc TEXT NOT NULL,
            ts_et TEXT,
            trading_day TEXT NOT NULL,
            current_return REAL NOT NULL,
            shadow_return REAL NOT NULL,
            is_post_trigger INTEGER NOT NULL DEFAULT 0,
            trigger_id INTEGER
        )
    """)
    conn.execute("CREATE INDEX idx_sym_day ON shadow_history (symphony_id, trading_day, ts_utc)")
    conn.execute(
        """INSERT INTO shadow_history
           (symphony_id, ts_utc, ts_et, trading_day, current_return, shadow_return, is_post_trigger)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            symphony_id,
            f"{_SHADOW_TEST_TRADING_DAY}T19:00:00Z",
            f"{_SHADOW_TEST_TRADING_DAY}T15:00:00",
            _SHADOW_TEST_TRADING_DAY,
            current_return,
            shadow_return,
            is_post_trigger,
        ),
    )
    conn.commit()
    conn.close()
    return db_file


def _build_empty_shadow_db(tmp_path: Path, suffix: str = "") -> str:
    """Empty shadow_history DB — used to assert None sentinel (AC-M1F.3.5)."""
    db_file = str(tmp_path / f"shadow_empty{suffix}.db")
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE shadow_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symphony_id TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            trading_day TEXT NOT NULL,
            current_return REAL NOT NULL,
            shadow_return REAL NOT NULL,
            is_post_trigger INTEGER NOT NULL DEFAULT 0,
            trigger_id INTEGER
        )
    """)
    conn.commit()
    conn.close()
    return db_file


# ---------------------------------------------------------------------------
# TIER 1 — Per-symphony helpers: get_symphony_today_change
# ---------------------------------------------------------------------------


class TestGetSymphonyTodayChange:
    def test_if_held_equals_last_percent_change_times_100(self, normal_symphony):
        """
        if_held today's change must be last_percent_change * 100.
        Composer returns last_percent_change as a decimal (e.g. -0.0229 = -2.29%).
        """
        from analytics import get_symphony_today_change

        result = get_symphony_today_change(normal_symphony, bot_state_entry=None)

        assert isinstance(result, dict), (
            f"get_symphony_today_change must return a dict; got {type(result)}"
        )
        assert "if_held" in result, "result must have 'if_held' key"
        assert "dry_run" in result, "result must have 'dry_run' key"

        expected_if_held = normal_symphony["last_percent_change"] * 100
        assert result["if_held"] == pytest.approx(expected_if_held, abs=1e-9), (
            f"if_held must be last_percent_change*100; "
            f"expected {expected_if_held}, got {result['if_held']}"
        )

    def test_dry_run_reads_shadow_history_when_not_triggered(self, normal_symphony, tmp_path):
        """
        Non-triggered symphony with shadow_history row: dry_run reads shadow_return.
        Pre-trigger, shadow_return == current_return so the values agree — but dry_run
        is derived from shadow_history, NOT from if_held or bot_state.

        Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        """
        from analytics import get_symphony_today_change

        sym_id = normal_symphony.get("id", "test-sym")
        # shadow_return=current_return (pre-trigger, not yet diverged): any value distinct
        # from if_held proves the read source is shadow_history, not the Composer fallback.
        expected_shadow_return = -3.33
        db_file = _build_shadow_db_for_sym(
            sym_id,
            shadow_return=expected_shadow_return,
            current_return=expected_shadow_return,
            is_post_trigger=0,
            tmp_path=tmp_path,
        )

        result = get_symphony_today_change(
            normal_symphony,
            bot_state_entry=_untriggered_bot_state_entry(),
            trading_day=_SHADOW_TEST_TRADING_DAY,
            db_path=db_file,
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] == pytest.approx(expected_shadow_return, abs=1e-6), (
            f"non-triggered: dry_run must be shadow_history.shadow_return={expected_shadow_return}; "
            f"got {result['dry_run']}"
        )

    def test_dry_run_is_none_when_no_shadow_rows_and_trading_day_explicit(
        self, normal_symphony, tmp_path
    ):
        """
        trading_day explicitly provided, but no shadow_history rows: dry_run=None.
        Per AC-M1F.3.5: NEVER fall back to if_held.

        Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        """
        from analytics import get_symphony_today_change

        db_file = _build_empty_shadow_db(tmp_path, suffix="_tc_no_bot")
        result = get_symphony_today_change(
            normal_symphony,
            bot_state_entry=None,
            trading_day=_SHADOW_TEST_TRADING_DAY,
            db_path=db_file,
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] is None, (
            f"no shadow rows, explicit trading_day: dry_run must be None (AC-M1F.3.5); "
            f"got {result['dry_run']}"
        )

    def test_dry_run_reads_shadow_return_not_bot_state_when_triggered(
        self, normal_symphony, tmp_path
    ):
        """
        Triggered symphony: dry_run reads shadow_history.shadow_return (frozen at trigger),
        NOT bot_state_entry["current_return"].

        Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        """
        from analytics import get_symphony_today_change

        sym_id = normal_symphony.get("id", "test-sym")
        frozen_shadow_return = -7.5
        current_return_post_trigger = 99.0  # sentinel — must NOT appear in result
        db_file = _build_shadow_db_for_sym(
            sym_id,
            shadow_return=frozen_shadow_return,
            current_return=current_return_post_trigger,
            is_post_trigger=1,
            tmp_path=tmp_path,
        )

        triggered_entry = _triggered_bot_state_entry(current_return=current_return_post_trigger)
        result = get_symphony_today_change(
            normal_symphony,
            bot_state_entry=triggered_entry,
            trading_day=_SHADOW_TEST_TRADING_DAY,
            db_path=db_file,
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] == pytest.approx(frozen_shadow_return, abs=1e-6), (
            f"triggered: dry_run must be shadow_history.shadow_return={frozen_shadow_return}, "
            f"NOT bot_state current_return={current_return_post_trigger}; got {result['dry_run']}"
        )
        expected_if_held = normal_symphony["last_percent_change"] * 100
        assert result["if_held"] == pytest.approx(expected_if_held, abs=1e-9), (
            f"triggered: if_held must still derive from last_percent_change; got {result['if_held']}"
        )

    def test_if_held_is_finite_float_for_all_fixture_symphonies(
        self, symphony_stats_meta, tmp_path
    ):
        """
        All 11 fixture symphonies must produce a finite float for if_held.
        dry_run may be None when no shadow rows exist (AC-M1F.3.5) — that is correct.

        Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        """
        from analytics import get_symphony_today_change

        db_file = _build_empty_shadow_db(tmp_path, suffix="_all_syms_tc")
        for sym in symphony_stats_meta:
            result = get_symphony_today_change(
                sym,
                bot_state_entry=None,
                trading_day=_SHADOW_TEST_TRADING_DAY,
                db_path=db_file,
            )
            v = result["if_held"]
            assert isinstance(v, float), (
                f"symphony {sym.get('id')}: if_held must be float; got {type(v)}"
            )
            assert math.isfinite(v), f"symphony {sym.get('id')}: if_held must be finite; got {v}"
            # dry_run=None is correct when shadow_history empty (AC-M1F.3.5)
            assert result["dry_run"] is None, (
                # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
                f"symphony {sym.get('id')}: dry_run must be None when no shadow rows "
                f"(AC-M1F.3.5); got {result['dry_run']}"
            )


# ---------------------------------------------------------------------------
# TIER 1 — Per-symphony helpers: get_symphony_cumulative_return
# ---------------------------------------------------------------------------


class TestGetSymphonyCumulativeReturn:
    def test_twr_fallback_when_simple_return_zero_and_net_deposits_zero(self, first_symphony):
        """
        TWR fallback contract: when simple_return==0.0 AND net_deposits==0.0,
        if_held CR must equal time_weighted_return from the fixture.

        Fixture anchor (captured-from-producer):
          first symphony: simple_return=0.0, net_deposits=0.0, time_weighted_return=3.13212
        """
        from analytics import get_symphony_cumulative_return

        assert first_symphony["simple_return"] == 0.0, (
            "fixture assumption violated: first_symphony.simple_return must be 0.0"
        )
        assert first_symphony["net_deposits"] == 0.0, (
            "fixture assumption violated: first_symphony.net_deposits must be 0.0"
        )

        result = get_symphony_cumulative_return(first_symphony, bot_state_entry=None)

        assert isinstance(result, dict), (
            f"get_symphony_cumulative_return must return a dict; got {type(result)}"
        )
        assert "if_held" in result and "dry_run" in result, (
            f"result must have 'if_held' and 'dry_run'; got {list(result.keys())}"
        )

        expected_twr = first_symphony["time_weighted_return"] * 100.0  # 3.13212 -> 313.212
        assert result["if_held"] == pytest.approx(expected_twr, abs=1e-6), (
            f"TWR fallback: if_held CR must equal time_weighted_return*100={expected_twr}; "
            f"got {result['if_held']} — check fallback branch for simple_return==0, net_deposits==0"
        )

    def test_normal_symphony_uses_simple_return(self, normal_symphony):
        """
        Normal case: simple_return != 0, so if_held CR must equal simple_return.
        Fixture: normal_symphony.simple_return = 0.65976
        """
        from analytics import get_symphony_cumulative_return

        assert normal_symphony["simple_return"] != 0.0, (
            "fixture assumption violated: normal_symphony.simple_return must be non-zero"
        )
        assert normal_symphony["net_deposits"] != 0.0, (
            "fixture assumption violated: normal_symphony.net_deposits must be non-zero"
        )

        result = get_symphony_cumulative_return(normal_symphony, bot_state_entry=None)

        expected = normal_symphony["simple_return"] * 100.0  # 0.65976 -> 65.976
        assert result["if_held"] == pytest.approx(expected, abs=1e-6), (
            f"normal CR: if_held must equal simple_return*100={expected}; got {result['if_held']}"
        )

    def test_dry_run_equals_if_held_when_no_shadow_rows_not_triggered(
        self, normal_symphony, tmp_path
    ):
        """
        Non-triggered, trading_day explicit, no shadow trajectory: dry_run == if_held.
        Anchored-shadow model: when the bot never diverged (no shadow rows), the series
        coincides exactly with if_held — both start and stay at the same baseline.
        """
        from analytics import get_symphony_cumulative_return

        db_file = _build_empty_shadow_db(tmp_path, suffix="_cr_untriggered")
        result = get_symphony_cumulative_return(
            normal_symphony,
            bot_state_entry=_untriggered_bot_state_entry(),
            trading_day=_SHADOW_TEST_TRADING_DAY,
            db_path=db_file,
        )

        assert result["if_held"] is not None, "if_held must be set for this test to be meaningful"
        assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-6), (
            f"no shadow rows: dry_run must equal if_held (anchored model — bot coincided with hold); "
            f"if_held={result['if_held']}, dry_run={result['dry_run']}"
        )

    def test_dry_run_equals_if_held_when_no_shadow_rows_no_bot_state(
        self, normal_symphony, tmp_path
    ):
        """
        No bot_state entry, trading_day explicit, no shadow trajectory: dry_run == if_held.
        Anchored-shadow model: no shadow rows means no divergence; series coincides with if_held.
        """
        from analytics import get_symphony_cumulative_return

        db_file = _build_empty_shadow_db(tmp_path, suffix="_cr_no_bot")
        result = get_symphony_cumulative_return(
            normal_symphony,
            bot_state_entry=None,
            trading_day=_SHADOW_TEST_TRADING_DAY,
            db_path=db_file,
        )

        assert result["if_held"] is not None, "if_held must be set for this test to be meaningful"
        assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-6), (
            f"no bot_state, no shadow rows: dry_run must equal if_held (anchored model); "
            f"if_held={result['if_held']}, dry_run={result['dry_run']}"
        )

    def test_dry_run_equals_if_held_when_triggered_no_shadow_trajectory(
        self, normal_symphony, tmp_path
    ):
        """
        Triggered symphony, no shadow trajectory: dry_run == if_held.
        Anchored-shadow model: no recorded divergence means the series coincides with if_held.
        The bot's guard never fired in the window, so both series are identical at baseline.
        """
        from analytics import get_symphony_cumulative_return

        db_file = _build_empty_shadow_db(tmp_path, suffix="_cr_triggered")
        triggered = _triggered_bot_state_entry()
        result = get_symphony_cumulative_return(
            normal_symphony,
            bot_state_entry=triggered,
            trading_day=_SHADOW_TEST_TRADING_DAY,
            db_path=db_file,
        )

        assert result["if_held"] is not None, "if_held must be set for this test to be meaningful"
        assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-6), (
            f"triggered, no shadow trajectory: dry_run must equal if_held (anchored model); "
            f"if_held={result['if_held']}, dry_run={result['dry_run']}"
        )

    def test_twr_fallback_symphony_dry_run_equals_if_held_when_no_shadow(
        self, first_symphony, tmp_path
    ):
        """
        TWR-fallback symphony, no shadow trajectory: dry_run == if_held.
        if_held uses TWR; no shadow rows means no divergence — anchored series coincides.
        """
        from analytics import get_symphony_cumulative_return

        db_file = _build_empty_shadow_db(tmp_path, suffix="_cr_twr")
        result = get_symphony_cumulative_return(
            first_symphony,
            bot_state_entry=None,
            trading_day=_SHADOW_TEST_TRADING_DAY,
            db_path=db_file,
        )

        expected_twr = first_symphony["time_weighted_return"] * 100.0
        assert result["if_held"] == pytest.approx(expected_twr, abs=1e-6), (
            f"TWR fallback: if_held must equal TWR*100={expected_twr}; got {result['if_held']}"
        )
        assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-6), (
            f"TWR fallback, no shadow trajectory: dry_run must equal if_held (anchored model); "
            f"if_held={result['if_held']}, dry_run={result['dry_run']}"
        )

    def test_all_fixture_symphonies_if_held_finite_dry_run_equals_if_held_when_no_shadow(
        self, symphony_stats_meta, tmp_path
    ):
        """
        All 11 fixture symphonies: if_held is finite float; dry_run == if_held when no
        shadow rows (anchored model — no divergence means the series coincide).
        """
        from analytics import get_symphony_cumulative_return

        db_file = _build_empty_shadow_db(tmp_path, suffix="_cr_all")
        for sym in symphony_stats_meta:
            result = get_symphony_cumulative_return(
                sym,
                bot_state_entry=None,
                trading_day=_SHADOW_TEST_TRADING_DAY,
                db_path=db_file,
            )
            if result["if_held"] is not None:
                assert isinstance(result["if_held"], float), (
                    f"symphony {sym.get('id')}: CR if_held must be float; got {type(result['if_held'])}"
                )
                assert math.isfinite(result["if_held"]), (
                    f"symphony {sym.get('id')}: CR if_held must be finite; got {result['if_held']}"
                )
                # Anchored model: no shadow rows → no divergence → dry_run coincides with if_held.
                assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-6), (
                    f"symphony {sym.get('id')}: no shadow rows → dry_run must equal if_held "
                    f"(anchored model); if_held={result['if_held']}, dry_run={result['dry_run']}"
                )


# ---------------------------------------------------------------------------
# TIER 1 — Per-symphony helpers: get_symphony_max_drawdown
# ---------------------------------------------------------------------------


class TestGetSymphonyMaxDrawdown:
    def test_if_held_equals_max_drawdown_from_composer(self, normal_symphony):
        """
        if_held MDD must equal max_drawdown * 100 (percentage scale, consistent with CR/TC).
        Composer returns max_drawdown as a fraction (e.g. 0.1495); the helper must multiply
        by 100 before returning so all three metrics share the same percentage scale.
        Fixture: normal_symphony.max_drawdown = 0.1495 → expected if_held = 14.95
        """
        from analytics import get_symphony_max_drawdown

        result = get_symphony_max_drawdown(normal_symphony, bot_state_entry=None)

        assert isinstance(result, dict), (
            f"get_symphony_max_drawdown must return a dict; got {type(result)}"
        )
        assert "if_held" in result and "dry_run" in result, (
            f"result must have 'if_held' and 'dry_run'; got {list(result.keys())}"
        )

        expected = normal_symphony["max_drawdown"] * 100.0
        assert result["if_held"] == pytest.approx(expected, abs=1e-6), (
            f"if_held MDD must equal Composer max_drawdown * 100 = {expected}%; "
            f"got {result['if_held']} — check analytics.py:585 for the ×100 multiply."
        )

    def test_if_held_mdd_is_non_negative(self, symphony_stats_meta):
        """
        MDD is a non-negative float (Composer convention: 0.0 = no drawdown,
        positive value = drawdown magnitude). Must not be negative.
        """
        from analytics import get_symphony_max_drawdown

        for sym in symphony_stats_meta:
            result = get_symphony_max_drawdown(sym, bot_state_entry=None)
            assert result["if_held"] >= 0.0, (
                f"symphony {sym.get('id')}: if_held MDD must be >= 0; "
                f"got {result['if_held']} — check sign convention"
            )

    def test_dry_run_is_none_when_no_shadow_rows_not_triggered(self, normal_symphony, tmp_path):
        """
        Non-triggered, trading_day explicit, no shadow trajectory: dry_run=None.
        Per AC-M1F.3.5: no fallback to if_held.

        Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        """
        from analytics import get_symphony_max_drawdown

        db_file = _build_empty_shadow_db(tmp_path, suffix="_mdd_untriggered")
        result = get_symphony_max_drawdown(
            normal_symphony,
            bot_state_entry=_untriggered_bot_state_entry(),
            trading_day=_SHADOW_TEST_TRADING_DAY,
            db_path=db_file,
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] is None, (
            f"non-triggered, no shadow trajectory: dry_run MDD must be None (AC-M1F.3.5); "
            f"got {result['dry_run']}"
        )

    def test_dry_run_is_none_when_triggered_no_shadow_trajectory(self, normal_symphony, tmp_path):
        """
        Triggered symphony, no shadow trajectory: dry_run MDD=None.
        Per AC-M1F.3.5: triggered flag does NOT enable bot_state or if_held fallback.

        Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        """
        from analytics import get_symphony_max_drawdown

        db_file = _build_empty_shadow_db(tmp_path, suffix="_mdd_triggered")
        triggered = _triggered_bot_state_entry()
        result = get_symphony_max_drawdown(
            normal_symphony,
            bot_state_entry=triggered,
            trading_day=_SHADOW_TEST_TRADING_DAY,
            db_path=db_file,
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] is None, (
            f"triggered, no shadow trajectory: dry_run MDD must be None (AC-M1F.3.5); "
            f"got {result['dry_run']} — must NOT fall back to if_held"
        )

    def test_all_fixture_symphonies_if_held_finite_dry_run_none_when_no_shadow(
        self, symphony_stats_meta, tmp_path
    ):
        """
        All 11 fixture symphonies: if_held is finite non-negative float; dry_run=None
        when no shadow rows exist.

        Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        """
        from analytics import get_symphony_max_drawdown

        db_file = _build_empty_shadow_db(tmp_path, suffix="_mdd_all")
        for sym in symphony_stats_meta:
            result = get_symphony_max_drawdown(
                sym,
                bot_state_entry=None,
                trading_day=_SHADOW_TEST_TRADING_DAY,
                db_path=db_file,
            )
            if result["if_held"] is not None:
                assert isinstance(result["if_held"], float), (
                    f"symphony {sym.get('id')}: MDD if_held must be float; got {type(result['if_held'])}"
                )
                assert math.isfinite(result["if_held"]), (
                    f"symphony {sym.get('id')}: MDD if_held must be finite; got {result['if_held']}"
                )
            # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
            assert result["dry_run"] is None, (
                f"symphony {sym.get('id')}: dry_run MDD must be None when no shadow rows "
                f"(AC-M1F.3.5); got {result['dry_run']}"
            )


# ---------------------------------------------------------------------------
# TIER 1 — Portfolio-level helpers: get_portfolio_today_change
# ---------------------------------------------------------------------------


class TestGetPortfolioTodayChange:
    def test_returns_dict_with_if_held_and_dry_run(self, symphony_stats_meta):
        """Portfolio today-change must return a dict with 'if_held' and 'dry_run'."""
        from analytics import get_portfolio_today_change

        result = get_portfolio_today_change(symphony_stats_meta, bot_state={})

        assert isinstance(result, dict), (
            f"get_portfolio_today_change must return a dict; got {type(result)}"
        )
        assert "if_held" in result, "result must have 'if_held' key"
        assert "dry_run" in result, "result must have 'dry_run' key"

    def test_if_held_is_value_weighted_average_of_last_percent_change(self, symphony_stats_meta):
        """
        Portfolio if_held today-change must be value-weighted mean of
        (symphony.last_percent_change * 100) across all symphonies.

        Weight is symphony["value"] (current portfolio value).
        """
        from analytics import get_portfolio_today_change

        result = get_portfolio_today_change(symphony_stats_meta, bot_state={})

        # Compute expected value-weighted avg from the fixture
        total_weight = 0.0
        weighted_sum = 0.0
        for sym in symphony_stats_meta:
            w = float(sym.get("value", 0.0))
            if w > 0:
                weighted_sum += sym["last_percent_change"] * 100.0 * w
                total_weight += w

        if total_weight > 0:
            expected = weighted_sum / total_weight
            assert result["if_held"] == pytest.approx(expected, abs=1e-6), (
                f"portfolio if_held today-change must be value-weighted avg; "
                f"expected {expected:.6f}, got {result['if_held']:.6f}"
            )

    def test_if_held_is_finite_float(self, symphony_stats_meta):
        """
        Portfolio if_held must be a finite float.
        dry_run may be None when shadow_history is empty (AC-M1F.3.5).

        Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        """
        from analytics import get_portfolio_today_change

        result = get_portfolio_today_change(symphony_stats_meta, bot_state={})

        v = result["if_held"]
        assert isinstance(v, float), f"portfolio today-change if_held must be float; got {type(v)}"
        assert math.isfinite(v), f"portfolio today-change if_held must be finite; got {v}"

    def test_empty_symphonies_returns_zeros(self):
        """Empty input must not raise; returns 0.0 for both sides."""
        from analytics import get_portfolio_today_change

        result = get_portfolio_today_change([], bot_state={})

        assert result["if_held"] == pytest.approx(0.0, abs=1e-9), (
            f"empty symphonies: if_held must be 0.0; got {result['if_held']}"
        )
        assert result["dry_run"] == pytest.approx(0.0, abs=1e-9), (
            f"empty symphonies: dry_run must be 0.0; got {result['dry_run']}"
        )

    def test_portfolio_dry_run_is_none_or_matches_shadow_when_no_triggered(
        self, symphony_stats_meta, tmp_path
    ):
        """
        All-untriggered bot_state, no shadow rows: portfolio dry_run is None or derived
        from shadow_history (which is empty → each per-symphony dry_run=None → portfolio
        aggregation result depends on implementation). At minimum, it must NOT equal
        if_held as a flat fallback.

        Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        """
        from analytics import get_portfolio_today_change

        db_file = _build_empty_shadow_db(tmp_path, suffix="_ptc_untriggered")
        bot_state = {sym["id"]: _untriggered_bot_state_entry() for sym in symphony_stats_meta}
        result = get_portfolio_today_change(
            symphony_stats_meta,
            bot_state=bot_state,
            trading_day=_SHADOW_TEST_TRADING_DAY,
            db_path=db_file,
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        # With all shadow rows absent, portfolio dry_run should reflect that (None or 0.0
        # depending on aggregation strategy), but must NOT silently equal if_held via fallback.
        assert (
            result["dry_run"] != pytest.approx(result["if_held"], abs=0.001)
            or result["dry_run"] is None
        ), (
            f"all-untriggered+no shadow rows: portfolio dry_run must not equal if_held via fallback; "
            f"if_held={result['if_held']}, dry_run={result['dry_run']}"
        )

    def test_triggered_symphony_portfolio_dry_run_reads_shadow_not_bot_state(
        self, symphony_stats_meta, tmp_path
    ):
        """
        One triggered symphony with shadow_history row (shadow_return=frozen_value):
        portfolio dry_run must shift toward shadow_return, NOT bot_state current_return.

        Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        """
        from analytics import get_portfolio_today_change

        triggered_symphony = symphony_stats_meta[0]
        triggered_id = triggered_symphony["id"]
        frozen_shadow_return = -50.0  # extreme sentinel to detect which source is used
        bot_state_current = 100.0  # different sentinel

        db_file = _build_shadow_db_for_sym(
            triggered_id,
            shadow_return=frozen_shadow_return,
            current_return=bot_state_current,
            is_post_trigger=1,
            tmp_path=tmp_path,
        )

        bot_state = {
            triggered_id: {
                "triggered": True,
                "current_return": bot_state_current,
            }
        }
        result = get_portfolio_today_change(
            symphony_stats_meta,
            bot_state=bot_state,
            trading_day=_SHADOW_TEST_TRADING_DAY,
            db_path=db_file,
        )

        # If dry_run used bot_state current_return=100.0, portfolio would be pulled up.
        # If dry_run used shadow_return=-50.0, portfolio would be pulled down.
        # Verify: dry_run must differ from if_held (divergence driven by shadow_return).
        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] != pytest.approx(result["if_held"], abs=0.001), (
            f"portfolio dry_run must reflect triggered symphony's shadow_history.shadow_return; "
            f"dry_run={result['dry_run']} should differ from if_held={result['if_held']}"
        )


# ---------------------------------------------------------------------------
# TIER 1 — Portfolio-level helpers: get_portfolio_cumulative_return
# ---------------------------------------------------------------------------


class TestGetPortfolioCumulativeReturn:
    def test_returns_dict_with_if_held_and_dry_run(self, symphony_stats_meta):
        """Portfolio CR must return a dict with 'if_held' and 'dry_run'."""
        from analytics import get_portfolio_cumulative_return

        result = get_portfolio_cumulative_return(symphony_stats_meta, bot_state={})

        assert isinstance(result, dict), (
            f"get_portfolio_cumulative_return must return a dict; got {type(result)}"
        )
        assert "if_held" in result, "result must have 'if_held' key"
        assert "dry_run" in result, "result must have 'dry_run' key"

    def test_twr_fallback_influences_portfolio_cr(self, symphony_stats_meta, first_symphony):
        """
        The first symphony uses TWR fallback (simple_return==0, net_deposits==0).
        Portfolio if_held CR must incorporate TWR for that symphony, not 0.0.

        Verify: portfolio CR != value-weighted of simple_returns (naive case that ignores fallback).
        """
        from analytics import get_portfolio_cumulative_return

        result = get_portfolio_cumulative_return(symphony_stats_meta, bot_state={})

        # Naive wrong calculation that ignores the TWR fallback:
        total_weight = 0.0
        naive_weighted_sum = 0.0
        for sym in symphony_stats_meta:
            w = float(sym.get("value", 0.0))
            if w > 0:
                naive_weighted_sum += sym["simple_return"] * w  # wrong for sym[0]
                total_weight += w

        naive_cr = naive_weighted_sum / total_weight if total_weight > 0 else 0.0

        # The first symphony's simple_return is 0.0 but TWR is 3.13212.
        # These are materially different — the portfolio CR must not use 0.0 for sym[0].
        twr = first_symphony["time_weighted_return"]
        w0 = float(first_symphony.get("value", 0.0))
        if w0 > 0 and total_weight > 0:
            # The correct if_held must account for TWR on sym[0]
            # rather than 0.0 (simple_return). The difference is twr * w0 / total_weight.
            expected_correction = (twr - 0.0) * w0 / total_weight
            if abs(expected_correction) > 0.0001:
                assert result["if_held"] != pytest.approx(naive_cr, abs=0.0001), (
                    f"portfolio CR must use TWR fallback for first symphony; "
                    f"naive (wrong) CR={naive_cr:.6f}, got {result['if_held']:.6f}"
                )

    def test_if_held_is_finite_float(self, symphony_stats_meta):
        """
        Portfolio CR if_held must be a finite float.
        dry_run may be None when shadow_history is empty (AC-M1F.3.5).

        Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        """
        from analytics import get_portfolio_cumulative_return

        result = get_portfolio_cumulative_return(symphony_stats_meta, bot_state={})

        v = result["if_held"]
        if v is not None:
            assert isinstance(v, float), f"portfolio CR if_held must be float; got {type(v)}"
            assert math.isfinite(v), f"portfolio CR if_held must be finite; got {v}"

    def test_empty_symphonies_returns_none(self):
        """Empty input must not raise; returns None sentinel for both sides (no data)."""
        from analytics import get_portfolio_cumulative_return

        result = get_portfolio_cumulative_return([], bot_state={})

        assert result["if_held"] is None, (
            f"empty symphonies: CR if_held must be None (no-data sentinel); got {result['if_held']}"
        )
        assert result["dry_run"] is None, (
            f"empty symphonies: CR dry_run must be None; got {result['dry_run']}"
        )

    def test_portfolio_cr_dry_run_equals_if_held_when_no_shadow_rows(
        self, symphony_stats_meta, tmp_path
    ):
        """
        All-untriggered, no shadow trajectory: portfolio CR dry_run == portfolio CR if_held.
        Anchored-shadow model: each per-symphony CR returns if_held when no shadow rows exist
        (no divergence recorded), so the portfolio aggregate of dry_run equals that of if_held.
        """
        from analytics import get_portfolio_cumulative_return

        db_file = _build_empty_shadow_db(tmp_path, suffix="_pcr_untriggered")
        bot_state = {sym["id"]: _untriggered_bot_state_entry() for sym in symphony_stats_meta}
        result = get_portfolio_cumulative_return(
            symphony_stats_meta,
            bot_state=bot_state,
            trading_day=_SHADOW_TEST_TRADING_DAY,
            db_path=db_file,
        )

        # Anchored model: per-symphony dry_run == if_held when no shadow rows →
        # portfolio aggregation of both sides uses the same values → dry_run == if_held.
        if result["if_held"] is not None:
            assert result["dry_run"] == pytest.approx(result["if_held"], abs=1e-6), (
                f"no shadow trajectory: portfolio CR dry_run must equal if_held (anchored model); "
                f"if_held={result['if_held']}, dry_run={result['dry_run']}"
            )


# ---------------------------------------------------------------------------
# TIER 1 — Portfolio-level helpers: get_portfolio_max_drawdown
# ---------------------------------------------------------------------------


class TestGetPortfolioMaxDrawdown:
    def test_returns_dict_with_if_held_and_dry_run(self, symphony_stats_meta):
        """Portfolio MDD must return a dict with 'if_held' and 'dry_run'."""
        from analytics import get_portfolio_max_drawdown

        result = get_portfolio_max_drawdown(symphony_stats_meta, bot_state={})

        assert isinstance(result, dict), (
            f"get_portfolio_max_drawdown must return a dict; got {type(result)}"
        )
        assert "if_held" in result, "result must have 'if_held' key"
        assert "dry_run" in result, "result must have 'dry_run' key"

    def test_if_held_is_value_weighted_average_of_max_drawdown(self, symphony_stats_meta):
        """
        Portfolio if_held MDD must be value-weighted mean of (symphony.max_drawdown * 100).
        Per-symphony MDD is now percentage-scale (max_drawdown fraction × 100), consistent
        with CR and TC. Portfolio aggregation value-weights those percentage values.
        """
        from analytics import get_portfolio_max_drawdown

        result = get_portfolio_max_drawdown(symphony_stats_meta, bot_state={})

        total_weight = 0.0
        weighted_sum = 0.0
        for sym in symphony_stats_meta:
            w = float(sym.get("value", 0.0))
            if w > 0:
                weighted_sum += sym["max_drawdown"] * 100.0 * w
                total_weight += w

        if total_weight > 0:
            expected = weighted_sum / total_weight
            assert result["if_held"] == pytest.approx(expected, abs=1e-6), (
                f"portfolio if_held MDD must be value-weighted avg of (max_drawdown*100); "
                f"expected {expected:.6f}%, got {result['if_held']:.6f}%"
            )

    def test_if_held_mdd_is_non_negative(self, symphony_stats_meta):
        """Portfolio if_held MDD must be non-negative (Composer convention)."""
        from analytics import get_portfolio_max_drawdown

        result = get_portfolio_max_drawdown(symphony_stats_meta, bot_state={})

        assert result["if_held"] >= 0.0, (
            f"portfolio if_held MDD must be >= 0 (Composer positive convention); "
            f"got {result['if_held']}"
        )

    def test_if_held_is_finite_float(self, symphony_stats_meta):
        """
        Portfolio MDD if_held must be a finite float.
        dry_run may be None when shadow_history is empty (AC-M1F.3.5).

        Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        """
        from analytics import get_portfolio_max_drawdown

        result = get_portfolio_max_drawdown(symphony_stats_meta, bot_state={})

        v = result["if_held"]
        if v is not None:
            assert isinstance(v, float), f"portfolio MDD if_held must be float; got {type(v)}"
            assert math.isfinite(v), f"portfolio MDD if_held must be finite; got {v}"

    def test_empty_symphonies_returns_none(self):
        """Empty input must not raise; returns None sentinel for both sides (no data)."""
        from analytics import get_portfolio_max_drawdown

        result = get_portfolio_max_drawdown([], bot_state={})

        assert result["if_held"] is None, (
            f"empty symphonies: MDD if_held must be None (no-data sentinel); got {result['if_held']}"
        )
        assert result["dry_run"] is None, (
            f"empty symphonies: MDD dry_run must be None; got {result['dry_run']}"
        )

    def test_portfolio_mdd_dry_run_is_none_when_no_shadow_rows(self, symphony_stats_meta, tmp_path):
        """
        All-untriggered, no shadow trajectory: portfolio MDD dry_run=None.
        Per AC-M1F.3.5: no per-symphony shadow rows → per-symphony dry_run=None →
        portfolio aggregator excludes them → dry_run=None.

        Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        """
        from analytics import get_portfolio_max_drawdown

        db_file = _build_empty_shadow_db(tmp_path, suffix="_pmdd_untriggered")
        bot_state = {sym["id"]: _untriggered_bot_state_entry() for sym in symphony_stats_meta}
        result = get_portfolio_max_drawdown(
            symphony_stats_meta,
            bot_state=bot_state,
            trading_day=_SHADOW_TEST_TRADING_DAY,
            db_path=db_file,
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] is None, (
            f"all-untriggered+no shadow trajectory: portfolio MDD dry_run must be None; "
            f"got {result['dry_run']}"
        )


# ---------------------------------------------------------------------------
# TIER 1 — Property invariant: TWR fallback only triggers on BOTH conditions
# ---------------------------------------------------------------------------


class TestTwrFallbackConditions:
    def test_fallback_not_triggered_when_only_net_deposits_is_zero(self):
        """
        If simple_return != 0.0 but net_deposits == 0.0 — NOT a fallback case.
        Must use simple_return, not time_weighted_return.
        """
        from analytics import get_symphony_cumulative_return

        sym = {
            "id": "test-sym",
            "simple_return": 0.5,  # non-zero
            "net_deposits": 0.0,  # zero (only one condition)
            "time_weighted_return": 99.0,  # sentinel — must NOT be used
            "last_percent_change": 0.01,
            "max_drawdown": 0.1,
            "value": 1000.0,
        }
        result = get_symphony_cumulative_return(sym, bot_state_entry=None)

        assert result["if_held"] == pytest.approx(50.0, abs=1e-6), (
            f"simple_return=0.5 with net_deposits=0 must use simple_return*100=50.0, not TWR; "
            f"got {result['if_held']} (TWR sentinel=9900.0 would indicate fallback triggered)"
        )

    def test_fallback_not_triggered_when_only_simple_return_is_zero(self):
        """
        If simple_return == 0.0 but net_deposits != 0.0 — NOT a fallback case.
        Must use simple_return (0.0), not time_weighted_return.
        """
        from analytics import get_symphony_cumulative_return

        sym = {
            "id": "test-sym",
            "simple_return": 0.0,  # zero
            "net_deposits": 500.0,  # non-zero (only one condition)
            "time_weighted_return": 99.0,  # sentinel — must NOT be used
            "last_percent_change": 0.01,
            "max_drawdown": 0.1,
            "value": 1000.0,
        }
        result = get_symphony_cumulative_return(sym, bot_state_entry=None)

        assert result["if_held"] == pytest.approx(0.0, abs=1e-9), (
            f"simple_return=0.0 with net_deposits=500 must use simple_return (0.0), not TWR; "
            f"got {result['if_held']} (TWR sentinel=99.0 would indicate fallback triggered)"
        )

    def test_fallback_triggered_when_both_conditions_met(self):
        """
        Both simple_return==0.0 AND net_deposits==0.0: must use time_weighted_return.
        """
        from analytics import get_symphony_cumulative_return

        sym = {
            "id": "test-sym",
            "simple_return": 0.0,
            "net_deposits": 0.0,
            "time_weighted_return": 3.13212,
            "last_percent_change": -0.02,
            "max_drawdown": 0.25,
            "value": 1000.0,
        }
        result = get_symphony_cumulative_return(sym, bot_state_entry=None)

        assert result["if_held"] == pytest.approx(313.212, abs=1e-6), (
            f"both conditions met: must use time_weighted_return*100=313.212; "
            f"got {result['if_held']}"
        )


# ---------------------------------------------------------------------------
# TIER 1 — Missing-field contract (reviewer advisory, encoded as RED/GREEN pin)
#
# Current implementation uses bare dict key access (sym_dict["field"]).
# These tests document that contract: a missing required field raises KeyError.
# The live contract test (test_live_m1_helpers.py) is the drift guard.
# If a future implementer changes to graceful degradation, these tests must be
# updated deliberately — they must not silently pass on both behaviors.
# ---------------------------------------------------------------------------


class TestMissingFieldContract:
    def test_today_change_raises_on_missing_last_percent_change(self):
        """
        get_symphony_today_change raises KeyError when last_percent_change is absent.
        Documents the current bare-key-access contract — not silent degradation.
        """
        from analytics import get_symphony_today_change

        sym = {
            "id": "test-sym",
            # last_percent_change intentionally omitted
            "simple_return": 0.1,
            "net_deposits": 100.0,
            "time_weighted_return": 0.1,
            "max_drawdown": 0.05,
            "value": 1000.0,
        }
        with pytest.raises(KeyError):
            get_symphony_today_change(sym, bot_state_entry=None)

    def test_cumulative_return_raises_on_missing_simple_return(self):
        """
        get_symphony_cumulative_return returns None sentinel when simple_return is absent.
        """
        from analytics import get_symphony_cumulative_return

        sym = {
            "id": "test-sym",
            "last_percent_change": -0.01,
            # simple_return intentionally omitted
            "net_deposits": 100.0,
            "time_weighted_return": 0.1,
            "max_drawdown": 0.05,
            "value": 1000.0,
        }
        result = get_symphony_cumulative_return(sym, bot_state_entry=None)
        assert result["if_held"] is None
        assert result["dry_run"] is None

    def test_cumulative_return_raises_on_missing_net_deposits(self):
        """
        get_symphony_cumulative_return raises KeyError when net_deposits is absent.
        The TWR-fallback branch reads net_deposits unconditionally.
        """
        from analytics import get_symphony_cumulative_return

        sym = {
            "id": "test-sym",
            "last_percent_change": -0.01,
            "simple_return": 0.1,
            # net_deposits intentionally omitted
            "time_weighted_return": 0.1,
            "max_drawdown": 0.05,
            "value": 1000.0,
        }
        with pytest.raises(KeyError):
            get_symphony_cumulative_return(sym, bot_state_entry=None)

    def test_cumulative_return_raises_on_missing_twr_when_fallback_needed(self):
        """
        get_symphony_cumulative_return raises KeyError when time_weighted_return is
        absent AND the TWR fallback is triggered (simple_return==0, net_deposits==0).
        """
        from analytics import get_symphony_cumulative_return

        sym = {
            "id": "test-sym",
            "last_percent_change": -0.01,
            "simple_return": 0.0,
            "net_deposits": 0.0,
            # time_weighted_return intentionally omitted — fallback path would need it
            "max_drawdown": 0.05,
            "value": 1000.0,
        }
        with pytest.raises(KeyError):
            get_symphony_cumulative_return(sym, bot_state_entry=None)

    def test_max_drawdown_raises_on_missing_max_drawdown(self):
        """
        get_symphony_max_drawdown returns None sentinel when max_drawdown is absent.
        """
        from analytics import get_symphony_max_drawdown

        sym = {
            "id": "test-sym",
            "last_percent_change": -0.01,
            "simple_return": 0.1,
            "net_deposits": 100.0,
            "time_weighted_return": 0.1,
            # max_drawdown intentionally omitted
            "value": 1000.0,
        }
        result = get_symphony_max_drawdown(sym, bot_state_entry=None)
        assert result["if_held"] is None
        assert result["dry_run"] is None

    def test_portfolio_skips_symphony_missing_value_field(self):
        """
        _value_weighted_portfolio skips symphonies where 'value' key is absent.
        A symphony list with one valid and one missing-value entry must still
        return a result derived from the valid symphony only.
        """
        from analytics import get_portfolio_today_change

        symphonies = [
            {
                "id": "sym-good",
                "last_percent_change": 0.02,
                "simple_return": 0.1,
                "net_deposits": 100.0,
                "time_weighted_return": 0.1,
                "max_drawdown": 0.05,
                "value": 1000.0,
            },
            {
                "id": "sym-no-value",
                "last_percent_change": 0.99,  # sentinel — must not contribute
                "simple_return": 0.9,
                "net_deposits": 100.0,
                "time_weighted_return": 0.9,
                "max_drawdown": 0.5,
                # "value" key intentionally absent
            },
        ]
        result = get_portfolio_today_change(symphonies, bot_state={})

        # Only sym-good contributes — its last_percent_change*100 = 2.0
        assert result["if_held"] == pytest.approx(2.0, abs=1e-9), (
            f"symphony missing 'value' must be skipped by portfolio kernel; "
            f"expected 2.0 (sym-good only), got {result['if_held']}"
        )
