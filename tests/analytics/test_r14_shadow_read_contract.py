"""
RED tests for R14: Kill e95e02e regression — restore AC-M1F.3.1 shadow-history read contract.

Root cause: commit e95e02e introduced two bugs in analytics.py:
  1. analytics.py:419-421 — early-return for triggered symphonies bypasses shadow_history,
     returning bot_state_entry["current_return"] instead of shadow_history.shadow_return.
     Since bot_state.current_return == Composer last_percent_change * 100 (same source as
     if_held), both sides collapse to the same value.
  2. analytics.py:440-442 — fallback to if_held when trading_day is not explicitly injected.
     app.py:386,390,394 call sites do NOT pass trading_day, so the fallback fires for every
     non-triggered symphony too, defeating shadow-history divergence entirely.

M1F contract (AC-M1F.3.1, AC-M1F.3.5):
  - dry_run ALWAYS reads shadow_history.shadow_return for (symphony_id, trading_day).
  - triggered flag does NOT change which table is read.
  - If no shadow_history row exists for that day: dry_run = None (render '—'). NEVER
    fall back to if_held.

Fixture provenance: tests/fixtures/shadow/r14_live_triggered_symphony.json — captured
from live alphabot_state.db on 2026-05-18 for symphony iaSOOUsmnCJHiZvbrWfs. Post-trigger
rows confirm shadow_return=1.68 (frozen at trigger), current_return diverging freely.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
_LIVE_CAPTURE = _FIXTURE_DIR / "shadow" / "r14_live_triggered_symphony.json"
_SHADOW_HISTORY_FIXTURE = _FIXTURE_DIR / "shadow" / "analytics_shadow_history.json"


# ---------------------------------------------------------------------------
# DB helpers: build an in-memory SQLite with shadow_history rows from fixture
# ---------------------------------------------------------------------------

def _build_shadow_db(rows: list[dict], tmp_path: Path) -> str:
    """Write shadow_history rows into a temp SQLite file. Returns the file path."""
    db_file = str(tmp_path / "shadow_test.db")
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
    conn.execute(
        "CREATE INDEX idx_sym_day ON shadow_history (symphony_id, trading_day, ts_utc)"
    )
    for r in rows:
        conn.execute(
            """INSERT INTO shadow_history
               (symphony_id, ts_utc, ts_et, trading_day, current_return, shadow_return, is_post_trigger, trigger_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r["symphony_id"],
                r.get("ts_utc", r.get("ts_et", "2026-05-18T00:00:00Z")),
                r.get("ts_et"),
                r["trading_day"],
                r["current_return"],
                r["shadow_return"],
                r.get("is_post_trigger", 0),
                r.get("trigger_id"),
            ),
        )
    conn.commit()
    conn.close()
    return db_file


# ---------------------------------------------------------------------------
# TIER 0: Structural invariant — no triggered early-return in analytics.py
# ---------------------------------------------------------------------------

class TestNoTriggeredEarlyReturn:
    """Regression guard: the structural bug from e95e02e must not re-appear."""

    def test_analytics_py_has_no_triggered_early_return(self):
        """
        analytics.py must NOT contain 'if bot_state_entry.get("triggered"): return'
        (or equivalent one-liner) before the shadow_history lookup.

        Per M1F AC-M1F.3.1: triggered flag does not change which table is read.
        The early-return introduced by e95e02e is the root cause of the operator bug.
        """
        analytics_path = Path(__file__).parent.parent.parent / "analytics.py"
        source = analytics_path.read_text(encoding="utf-8")

        # The pattern: triggered check that returns without consulting shadow_history.
        # We check that 'get("triggered")' does not appear BEFORE the shadow row lookup.
        triggered_early_return_idx = source.find('bot_state_entry.get("triggered")')
        shadow_lookup_idx = source.find("_load_latest_shadow_row_for_analytics")

        # If triggered check appears at all, it must be AFTER the shadow lookup (or absent).
        if triggered_early_return_idx != -1:
            assert triggered_early_return_idx > shadow_lookup_idx, (
                "analytics.py contains a triggered-check BEFORE the shadow_history lookup — "
                "this is the e95e02e early-return bug. "
                "Per AC-M1F.3.1, triggered flag must NOT bypass shadow_history."
            )


# ---------------------------------------------------------------------------
# TIER 1: Operator-reported scenario — exact live reproduction
# ---------------------------------------------------------------------------

class TestOperatorReportedScenario:
    """
    Reproduces the exact TC +1.66 (+1.66) bug the operator flagged on 2026-05-18.
    Symphony iaSOOUsmnCJHiZvbrWfs, triggered=True, shadow_return=1.68 (frozen),
    current_return varies post-trigger, last_percent_change=0.0166 (→ if_held=1.66).
    Both sides should show distinct values; bug collapsed them to the same.
    """

    @pytest.fixture(scope="class")
    def live_fixture(self):
        with open(_LIVE_CAPTURE, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def test_dry_run_reads_shadow_return_not_current_return_for_triggered(
        self, live_fixture, tmp_path
    ):
        """
        Per M1F AC-M1F.3.1: dry_run for a triggered symphony must be shadow_history.shadow_return,
        NOT bot_state_entry["current_return"].

        Fixture: shadow_return=1.68 (frozen at trigger). latest current_return=1.63.
        Operator reported last_percent_change≈0.0166, giving if_held≈1.66.
        dry_run must be 1.68, NOT 1.63 and NOT 1.66.
        """
        from analytics import get_symphony_today_change

        db_file = _build_shadow_db(live_fixture["rows"], tmp_path)
        trading_day = live_fixture["trading_day"]

        sym_dict = {
            "id": live_fixture["symphony_id"],
            "last_percent_change": 0.0166,  # → if_held = 1.66
            "trading_day": trading_day,
        }
        bot_state_entry = {
            "triggered": True,
            "current_return": 1.63,  # latest live current_return post-trigger
            "triggered_at_return": 1.68,
            "high_water_mark": 2.0,
        }

        result = get_symphony_today_change(
            sym_dict, bot_state_entry, trading_day=trading_day, db_path=db_file
        )

        # Per AC-M1F.3.1: dry_run reads shadow_history.shadow_return = 1.68
        assert result["dry_run"] == pytest.approx(1.68, abs=1e-9), (
            # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
            f"dry_run must be shadow_history.shadow_return=1.68, "
            f"not bot_state current_return or if_held; got {result['dry_run']}"
        )

    def test_if_held_and_dry_run_diverge_for_triggered_symphony(
        self, live_fixture, tmp_path
    ):
        """
        The operator-visible symptom: TC should show two different values.
        if_held derives from Composer last_percent_change. dry_run from shadow_history.
        For a triggered symphony with frozen shadow_return, they MUST differ.
        """
        from analytics import get_symphony_today_change

        db_file = _build_shadow_db(live_fixture["rows"], tmp_path)
        trading_day = live_fixture["trading_day"]

        sym_dict = {
            "id": live_fixture["symphony_id"],
            "last_percent_change": 0.0166,
            "trading_day": trading_day,
        }
        bot_state_entry = {
            "triggered": True,
            "current_return": 1.63,
            "triggered_at_return": 1.68,
        }

        result = get_symphony_today_change(
            sym_dict, bot_state_entry, trading_day=trading_day, db_path=db_file
        )

        assert result["if_held"] != pytest.approx(result["dry_run"], abs=0.001), (
            # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
            f"Operator bug: if_held and dry_run must diverge for a triggered symphony. "
            f"if_held={result['if_held']}, dry_run={result['dry_run']} — both identical means "
            "dry_run is still collapsing to if_held instead of reading shadow_history."
        )

    def test_exact_operator_scenario_divergence(self, live_fixture, tmp_path):
        """
        Exact values from operator report and live capture:
          last_percent_change=0.0220 → if_held=2.20 (as reported at peak)
          shadow_return=1.68 (frozen) → dry_run=1.68
          Result must be {"if_held": 2.20, "dry_run": 1.68}

        This directly asserts the AC described in the team-lead mandate.
        """
        from analytics import get_symphony_today_change

        db_file = _build_shadow_db(live_fixture["rows"], tmp_path)
        trading_day = live_fixture["trading_day"]

        sym_dict = {
            "id": live_fixture["symphony_id"],
            "last_percent_change": 0.0220,  # peak value from operator report
            "trading_day": trading_day,
        }
        bot_state_entry = {
            "triggered": True,
            "current_return": 2.20,
            "triggered_at_return": 1.68,
        }

        result = get_symphony_today_change(
            sym_dict, bot_state_entry, trading_day=trading_day, db_path=db_file
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["if_held"] == pytest.approx(2.20, abs=1e-6), (
            f"if_held must be last_percent_change*100=2.20; got {result['if_held']}"
        )
        assert result["dry_run"] == pytest.approx(1.68, abs=1e-6), (
            f"dry_run must be shadow_history.shadow_return=1.68; got {result['dry_run']}"
        )


# ---------------------------------------------------------------------------
# TIER 2: AC-M1F.3.1 — triggered flag does NOT change read path
# ---------------------------------------------------------------------------

class TestTriggeredFlagDoesNotChangeShadowRead:
    """
    Per AC-M1F.3.1: shadow_history is ALWAYS the source for dry_run.
    Triggered and non-triggered symphonies both read shadow_history.shadow_return.
    """

    @pytest.fixture(scope="class")
    def shadow_rows(self):
        with open(_SHADOW_HISTORY_FIXTURE, "r", encoding="utf-8") as fh:
            return json.load(fh)["rows"]

    def test_triggered_symphony_dry_run_reads_shadow_history(self, shadow_rows, tmp_path):
        """
        Triggered symphony on a day with shadow_history rows:
        dry_run must equal the latest shadow_return for that symphony+trading_day.
        Must NOT use bot_state_entry["current_return"].
        """
        from analytics import get_symphony_today_change

        # SYM_ALPHA on 2026-05-14: latest row has shadow_return=3.50 (post-trigger frozen)
        db_file = _build_shadow_db(shadow_rows, tmp_path)
        sym_dict = {
            "id": "SYM_ALPHA",
            "last_percent_change": 0.02,  # if_held = 2.0
            "trading_day": "2026-05-14",
        }
        bot_state_entry = {
            "triggered": True,
            "current_return": 99.0,  # sentinel — must NOT appear in result
        }

        result = get_symphony_today_change(
            sym_dict, bot_state_entry, trading_day="2026-05-14", db_path=db_file
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] == pytest.approx(3.50, abs=1e-6), (
            f"triggered+shadow_history: dry_run must be shadow_return=3.50, "
            f"not bot_state current_return=99.0; got {result['dry_run']}"
        )

    def test_non_triggered_symphony_dry_run_reads_shadow_history(self, shadow_rows, tmp_path):
        """
        Non-triggered symphony with shadow_history rows:
        dry_run reads shadow_history.shadow_return (same as current for non-triggered).
        """
        from analytics import get_symphony_today_change

        # SYM_BETA on 2026-05-14: shadow_return=0.80 (non-triggered, shadow=current)
        db_file = _build_shadow_db(shadow_rows, tmp_path)
        sym_dict = {
            "id": "SYM_BETA",
            "last_percent_change": 0.008,  # if_held = 0.80
            "trading_day": "2026-05-14",
        }
        bot_state_entry = {
            "triggered": False,
            "current_return": 0.80,
        }

        result = get_symphony_today_change(
            sym_dict, bot_state_entry, trading_day="2026-05-14", db_path=db_file
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] == pytest.approx(0.80, abs=1e-6), (
            f"non-triggered: dry_run must be shadow_return=0.80 from shadow_history; got {result['dry_run']}"
        )

    def test_same_contract_triggered_and_non_triggered_both_read_shadow(
        self, shadow_rows, tmp_path
    ):
        """
        Both triggered and non-triggered paths hit shadow_history. The triggered flag
        must not create a divergent code path that bypasses the DB read.
        """
        from analytics import get_symphony_today_change

        db_file = _build_shadow_db(shadow_rows, tmp_path)

        # Triggered call: bot_state has current_return=99 (sentinel, must not appear)
        triggered_result = get_symphony_today_change(
            {"id": "SYM_ALPHA", "last_percent_change": 0.02, "trading_day": "2026-05-15"},
            {"triggered": True, "current_return": 99.0},
            trading_day="2026-05-15",
            db_path=db_file,
        )

        # Non-triggered call: same symphony+day
        untriggered_result = get_symphony_today_change(
            {"id": "SYM_ALPHA", "last_percent_change": 0.02, "trading_day": "2026-05-15"},
            {"triggered": False, "current_return": 1.20},
            trading_day="2026-05-15",
            db_path=db_file,
        )

        # Both must read shadow_return=1.20 (SYM_ALPHA day2 EOD)
        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert triggered_result["dry_run"] == pytest.approx(1.20, abs=1e-6), (
            f"triggered: dry_run must read shadow_history shadow_return=1.20; got {triggered_result['dry_run']}"
        )
        assert untriggered_result["dry_run"] == pytest.approx(1.20, abs=1e-6), (
            f"untriggered: dry_run must read shadow_history shadow_return=1.20; got {untriggered_result['dry_run']}"
        )


# ---------------------------------------------------------------------------
# TIER 3: AC-M1F.3.5 — None sentinel when shadow_history empty (NO fallback)
# ---------------------------------------------------------------------------

class TestNoneSentinelWhenNoShadowRows:
    """
    Per AC-M1F.3.5: dry_run=None when shadow_history has no rows for symphony+trading_day.
    NEVER fall back to if_held. UI renders '—'.
    """

    def test_today_change_dry_run_is_none_when_no_shadow_rows(self, tmp_path):
        """
        trading_day is explicitly provided but shadow_history has no rows for that day.
        dry_run must be None, NOT if_held.
        """
        from analytics import get_symphony_today_change

        db_file = _build_shadow_db([], tmp_path)  # empty DB
        sym_dict = {
            "id": "SYM_NO_HISTORY",
            "last_percent_change": 0.02,
            "trading_day": "2026-05-18",
        }
        bot_state_entry = {"triggered": False, "current_return": 2.0}

        result = get_symphony_today_change(
            sym_dict, bot_state_entry, trading_day="2026-05-18", db_path=db_file
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] is None, (
            f"no shadow rows: dry_run must be None (AC-M1F.3.5), not if_held={result['if_held']}; "
            f"got {result['dry_run']}"
        )

    def test_today_change_dry_run_is_none_for_triggered_with_no_shadow_rows(self, tmp_path):
        """
        Triggered symphony, trading_day explicit, but no shadow_history rows.
        dry_run must still be None — triggered flag does not enable bot_state fallback.
        """
        from analytics import get_symphony_today_change

        db_file = _build_shadow_db([], tmp_path)  # empty DB
        sym_dict = {
            "id": "SYM_TRIGGERED_NO_HISTORY",
            "last_percent_change": 0.02,
            "trading_day": "2026-05-18",
        }
        bot_state_entry = {
            "triggered": True,
            "current_return": 5.0,  # sentinel — must NOT appear in result
            "triggered_at_return": 3.0,
        }

        result = get_symphony_today_change(
            sym_dict, bot_state_entry, trading_day="2026-05-18", db_path=db_file
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] is None, (
            f"triggered+no shadow rows: dry_run must be None, "
            f"not bot_state current_return=5.0 or if_held={result['if_held']}; "
            f"got {result['dry_run']}"
        )

    def test_cumulative_return_dry_run_is_none_when_no_shadow_rows(self, tmp_path):
        """
        get_symphony_cumulative_return: dry_run=None when no shadow trajectory exists
        and trading_day is explicit. Per AC-M1F.3.5.
        """
        from analytics import get_symphony_cumulative_return

        db_file = _build_shadow_db([], tmp_path)
        sym_dict = {
            "id": "SYM_NO_HISTORY",
            "simple_return": 0.15,
            "net_deposits": 500.0,
            "time_weighted_return": 0.15,
            "last_percent_change": 0.01,
            "max_drawdown": 0.05,
            "value": 1000.0,
            "trading_day": "2026-05-18",
        }

        result = get_symphony_cumulative_return(
            sym_dict, bot_state_entry=None, trading_day="2026-05-18", db_path=db_file
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] is None, (
            f"no shadow trajectory: dry_run must be None (AC-M1F.3.5), "
            f"not if_held={result['if_held']}; got {result['dry_run']}"
        )

    def test_max_drawdown_dry_run_is_none_when_no_shadow_rows(self, tmp_path):
        """
        get_symphony_max_drawdown: dry_run=None when no shadow trajectory exists
        and trading_day is explicit. Per AC-M1F.3.5.
        """
        from analytics import get_symphony_max_drawdown

        db_file = _build_shadow_db([], tmp_path)
        sym_dict = {
            "id": "SYM_NO_HISTORY",
            "max_drawdown": 0.15,
            "simple_return": 0.10,
            "net_deposits": 500.0,
            "time_weighted_return": 0.10,
            "last_percent_change": 0.01,
            "value": 1000.0,
            "trading_day": "2026-05-18",
        }

        result = get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, trading_day="2026-05-18", db_path=db_file
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] is None, (
            f"no shadow trajectory: dry_run must be None (AC-M1F.3.5), "
            f"not if_held={result['if_held']}; got {result['dry_run']}"
        )

    def test_cumulative_return_dry_run_none_for_triggered_no_shadow(self, tmp_path):
        """
        Triggered symphony, no shadow trajectory: dry_run=None, NOT if_held.
        Per AC-M1F.3.5 — triggered flag does not reintroduce if_held fallback.
        """
        from analytics import get_symphony_cumulative_return

        db_file = _build_shadow_db([], tmp_path)
        sym_dict = {
            "id": "SYM_TRIGGERED_NO_CR",
            "simple_return": 0.15,
            "net_deposits": 500.0,
            "time_weighted_return": 0.15,
            "last_percent_change": 0.01,
            "max_drawdown": 0.05,
            "value": 1000.0,
            "trading_day": "2026-05-18",
        }
        bot_state_entry = {
            "triggered": True,
            "current_return": 5.0,
        }

        result = get_symphony_cumulative_return(
            sym_dict, bot_state_entry=bot_state_entry, trading_day="2026-05-18", db_path=db_file
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] is None, (
            f"triggered+no shadow CR: dry_run must be None (AC-M1F.3.5); got {result['dry_run']}"
        )

    def test_max_drawdown_dry_run_none_for_triggered_no_shadow(self, tmp_path):
        """
        Triggered symphony, no shadow trajectory: MDD dry_run=None, NOT if_held.
        """
        from analytics import get_symphony_max_drawdown

        db_file = _build_shadow_db([], tmp_path)
        sym_dict = {
            "id": "SYM_TRIGGERED_NO_MDD",
            "max_drawdown": 0.15,
            "simple_return": 0.10,
            "net_deposits": 500.0,
            "time_weighted_return": 0.10,
            "last_percent_change": 0.01,
            "value": 1000.0,
            "trading_day": "2026-05-18",
        }
        bot_state_entry = {
            "triggered": True,
            "current_return": 5.0,
        }

        result = get_symphony_max_drawdown(
            sym_dict, bot_state_entry=bot_state_entry, trading_day="2026-05-18", db_path=db_file
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] is None, (
            f"triggered+no shadow MDD: dry_run must be None (AC-M1F.3.5); got {result['dry_run']}"
        )


# ---------------------------------------------------------------------------
# TIER 4: trading_day injection at app.py call sites
# ---------------------------------------------------------------------------

class TestTradingDayInjection:
    """
    app.py:386,390,394 do NOT pass trading_day. This means the second bug from
    e95e02e fires: the fallback-to-if_held path triggers for every symphony.
    The fix requires those call sites to pass trading_day=<ET today>.
    """

    def test_app_py_injects_trading_day_in_tc_call(self):
        """
        app.py get_symphony_today_change call must pass trading_day kwarg explicitly.
        Without it, the fallback fires for every symphony without shadow_history rows.
        """
        analytics_path = Path(__file__).parent.parent.parent / "app.py"
        source = analytics_path.read_text(encoding="utf-8")

        # Locate the get_symphony_today_change call in the dashboard loop
        tc_call_idx = source.find("get_symphony_today_change(")
        assert tc_call_idx != -1, "Could not find get_symphony_today_change call in app.py"

        # Find the snippet around the call and assert trading_day= is present
        snippet_end = source.find(")", tc_call_idx)
        snippet = source[tc_call_idx:snippet_end + 1]

        assert "trading_day" in snippet, (
            f"app.py get_symphony_today_change call must pass trading_day=<ET today>; "
            f"found call: {snippet!r}"
        )

    def test_app_py_injects_trading_day_in_cr_call(self):
        """
        app.py get_symphony_cumulative_return call must pass trading_day kwarg explicitly.
        """
        analytics_path = Path(__file__).parent.parent.parent / "app.py"
        source = analytics_path.read_text(encoding="utf-8")

        cr_call_idx = source.find("get_symphony_cumulative_return(")
        assert cr_call_idx != -1, "Could not find get_symphony_cumulative_return call in app.py"

        snippet_end = source.find(")", cr_call_idx)
        snippet = source[cr_call_idx:snippet_end + 1]

        assert "trading_day" in snippet, (
            f"app.py get_symphony_cumulative_return call must pass trading_day=<ET today>; "
            f"found call: {snippet!r}"
        )

    def test_app_py_injects_trading_day_in_mdd_call(self):
        """
        app.py get_symphony_max_drawdown call must pass trading_day kwarg explicitly.
        """
        analytics_path = Path(__file__).parent.parent.parent / "app.py"
        source = analytics_path.read_text(encoding="utf-8")

        mdd_call_idx = source.find("get_symphony_max_drawdown(")
        assert mdd_call_idx != -1, "Could not find get_symphony_max_drawdown call in app.py"

        snippet_end = source.find(")", mdd_call_idx)
        snippet = source[mdd_call_idx:snippet_end + 1]

        assert "trading_day" in snippet, (
            f"app.py get_symphony_max_drawdown call must pass trading_day=<ET today>; "
            f"found call: {snippet!r}"
        )


# ---------------------------------------------------------------------------
# TIER 5: Portfolio strip call sites — trading_day injection (reviewer BLOCK)
# ---------------------------------------------------------------------------

class TestPortfolioStripTradingDayInjection:
    """
    Reviewer BLOCK: app.py:400-402 portfolio_strip calls omit trading_day.
    After 20:00 UTC the UTC date advances past ET trading_day, so the portfolio
    aggregators query shadow_history with tomorrow's date and return dry_run=None.
    Per-symphony strip (387/391/395) is correctly fixed; portfolio strip is not.
    """

    def test_app_py_portfolio_today_change_injects_trading_day(self):
        """
        app.py get_portfolio_today_change call must pass trading_day=_today_et.
        Without it, the portfolio aggregates query with UTC date after 20:00 UTC.
        """
        analytics_path = Path(__file__).parent.parent.parent / "app.py"
        source = analytics_path.read_text(encoding="utf-8")

        call_idx = source.find("get_portfolio_today_change(")
        assert call_idx != -1, "Could not find get_portfolio_today_change call in app.py"

        # Multiline calls: find closing paren by counting depth
        depth = 0
        end_idx = call_idx
        for i, ch in enumerate(source[call_idx:], start=call_idx):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break
        snippet = source[call_idx:end_idx + 1]

        assert "trading_day" in snippet, (
            f"app.py get_portfolio_today_change must pass trading_day=_today_et; "
            f"found call: {snippet!r}"
        )

    def test_app_py_portfolio_cumulative_return_injects_trading_day(self):
        """
        app.py get_portfolio_cumulative_return call must pass trading_day=_today_et.
        """
        analytics_path = Path(__file__).parent.parent.parent / "app.py"
        source = analytics_path.read_text(encoding="utf-8")

        call_idx = source.find("get_portfolio_cumulative_return(")
        assert call_idx != -1, "Could not find get_portfolio_cumulative_return call in app.py"

        depth = 0
        end_idx = call_idx
        for i, ch in enumerate(source[call_idx:], start=call_idx):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break
        snippet = source[call_idx:end_idx + 1]

        assert "trading_day" in snippet, (
            f"app.py get_portfolio_cumulative_return must pass trading_day=_today_et; "
            f"found call: {snippet!r}"
        )

    def test_app_py_portfolio_max_drawdown_injects_trading_day(self):
        """
        app.py get_portfolio_max_drawdown call must pass trading_day=_today_et.
        """
        analytics_path = Path(__file__).parent.parent.parent / "app.py"
        source = analytics_path.read_text(encoding="utf-8")

        call_idx = source.find("get_portfolio_max_drawdown(")
        assert call_idx != -1, "Could not find get_portfolio_max_drawdown call in app.py"

        depth = 0
        end_idx = call_idx
        for i, ch in enumerate(source[call_idx:], start=call_idx):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break
        snippet = source[call_idx:end_idx + 1]

        assert "trading_day" in snippet, (
            f"app.py get_portfolio_max_drawdown must pass trading_day=_today_et; "
            f"found call: {snippet!r}"
        )

    def test_portfolio_kwargs_thread_through_to_per_symphony_helpers(self, tmp_path):
        """
        When trading_day is passed to get_portfolio_today_change, the per-symphony
        helper receives it — confirming **kwargs threading works end-to-end.

        With shadow_history rows for symphony_id on the given trading_day, the
        portfolio dry_run must reflect shadow_return rather than being None.
        """
        from analytics import get_portfolio_today_change

        sym_id = "SYM_PORTFOLIO_THREADING_TEST"
        shadow_return_val = -4.44
        rows = [
            {
                "symphony_id": sym_id,
                "ts_utc": "2026-05-14T19:00:00Z",
                "ts_et": "2026-05-14T15:00:00",
                "trading_day": "2026-05-14",
                "current_return": shadow_return_val,
                "shadow_return": shadow_return_val,
                "is_post_trigger": 0,
                "trigger_id": None,
            }
        ]
        db_file = _build_shadow_db(rows, tmp_path)

        symphonies = [
            {
                "id": sym_id,
                "last_percent_change": 0.01,
                "simple_return": 0.05,
                "net_deposits": 100.0,
                "time_weighted_return": 0.05,
                "max_drawdown": 0.05,
                "value": 1000.0,
            }
        ]
        bot_state = {sym_id: {"triggered": False, "current_return": shadow_return_val}}

        result = get_portfolio_today_change(
            symphonies, bot_state,
            trading_day="2026-05-14", db_path=db_file,
        )

        # Per M1F AC-M1F.3.1: dry_run reads shadow_history, never falls back to if_held.
        assert result["dry_run"] == pytest.approx(shadow_return_val, abs=1e-6), (
            f"portfolio trading_day threading: dry_run must be shadow_return={shadow_return_val}; "
            f"got {result['dry_run']} — kwargs not reaching per-symphony helper"
        )
