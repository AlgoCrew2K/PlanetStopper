"""
RED — analytics.compute_windowed_portfolio_strip + compute_windowed_symphony_guard_alpha.

The /api/strip/<window> route (app.py:1733) calls analytics.compute_windowed_portfolio_strip
to re-window EVERY hero metric per the picker (AC-3). The reviewer BLOCKed because the function
is MISSING from analytics.py (route 500s live). This is the analytics-level golden RED that
pins the windowing contract so the implementation is correct, not just present — the companion
to tests/app/test_windowed_strip_route_live.py (which pins the live route 200s).

WINDOWING SEMANTIC — W1 (slice-then-regroup), adjudicated on the record:
  A window = the last N trading days across all rows; re-group the SLICED rows into contiguous
  position-epochs; the windowed guard alpha is the EPOCH-ADDITIVE sum over those (possibly
  partial) in-window epochs — Σ_epoch (∏_E(1+shadow/100) − ∏_E(1+current/100))·100. This is "the
  guard effect that accrued in the last N days." Consequences pinned here:
   - window == "all" (no slice) reproduces the AC-1 LIFETIME epoch-additive value EXACTLY
     (the consistency anchor with what shipped at 86ca6e0).
   - a window SHORTER than the age of the divergence yields a SMALLER alpha (windowing bites).
   - never-triggered -> 0.0 on every window.

F7 VOL GATE: vol_bot/vol_held are None and insufficient_history is True when the window's
trading-day count < _V1_BOOTSTRAP_MIN_DAYS (30, Bailey/de-Prado 2014 floor, analytics.py:1296).

FIXTURE PROVENANCE: reuses tests/fixtures/math/guard_alpha_lifetime_epochs.json (real all-epoch
shadow_history pairs). Every expected value is DERIVED IN-TEST from the rows via the epoch-additive
+ windowing logic — none hardcoded.

These FAIL today (the functions do not exist) and pass when implemented to the W1 contract.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

import database as _db

_FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "math" / "guard_alpha_lifetime_epochs.json"
)

_SCHEMA = """
    CREATE TABLE shadow_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symphony_id TEXT NOT NULL,
        ts_utc TEXT NOT NULL,
        trading_day TEXT NOT NULL,
        current_return REAL NOT NULL,
        shadow_return REAL NOT NULL,
        is_post_trigger INTEGER NOT NULL DEFAULT 0,
        position_epoch TEXT
    )
"""


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _make_db(tmp_path: Path, scenarios: dict) -> str:
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    db_file = str(Path(tmp_path) / "windowed_shadow.db")
    conn = sqlite3.connect(db_file)
    conn.execute(_SCHEMA)
    for scen in scenarios.values():
        sym_id = scen["symphony_id"]
        for row in scen["shadow_history_rows"]:
            conn.execute(
                "INSERT INTO shadow_history "
                "(symphony_id, ts_utc, trading_day, current_return, shadow_return, "
                "is_post_trigger, position_epoch) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sym_id, row["ts_utc"], row["trading_day"], row["current_return"],
                 row["shadow_return"], row.get("is_post_trigger", 0), row["position_epoch"]),
            )
    conn.commit()
    conn.close()
    _db._shadow_cr_cache.clear()
    return db_file


def _sym_dict(sym_id: str, if_held_pct: float, value: float) -> dict:
    return {
        "id": sym_id, "value": value,
        "simple_return": if_held_pct / 100.0, "net_deposits": 1000.0,
        "time_weighted_return": if_held_pct / 100.0, "max_drawdown": 0.08,
    }


# ---------------------------------------------------------------------------
# Epoch-additive + W1 windowing reference (derives expected values)
# ---------------------------------------------------------------------------

def _epoch_groups(rows):
    groups, cur = [], object()
    for r in rows:
        if r["position_epoch"] != cur:
            groups.append([])
            cur = r["position_epoch"]
        groups[-1].append(r)
    return groups


def _epoch_additive_alpha(rows):
    total = 0.0
    for g in _epoch_groups(rows):
        ps = pc = 1.0
        for r in g:
            ps *= 1.0 + r["shadow_return"] / 100.0
            pc *= 1.0 + r["current_return"] / 100.0
        total += (ps - pc) * 100.0
    return total


def _last_n_days(rows, n):
    """W1: the last N trading days (rows are day-ascending; one row per day here)."""
    return rows[-n:] if n < len(rows) else rows


# ===========================================================================
# Existence (the literal BLOCK) + window echo
# ===========================================================================

class TestWindowedFunctionsExist:
    def test_functions_are_defined_and_callable(self):
        import analytics
        assert hasattr(analytics, "compute_windowed_portfolio_strip") and callable(
            analytics.compute_windowed_portfolio_strip
        ), "analytics.compute_windowed_portfolio_strip must be defined (app.py:1771 calls it)."
        assert hasattr(analytics, "compute_windowed_symphony_guard_alpha") and callable(
            analytics.compute_windowed_symphony_guard_alpha
        ), "analytics.compute_windowed_symphony_guard_alpha must be defined."


# ===========================================================================
# W1 windowing — ALL == AC-1 lifetime; windowing bites; never-triggered == 0
# ===========================================================================

class TestWindowedGuardAlpha:
    def test_all_window_equals_lifetime_epoch_additive(self, fixture, tmp_path):
        """The 'all' window guard alpha == the AC-1 lifetime epoch-additive value EXACTLY —
        the consistency anchor with what shipped at 86ca6e0."""
        import analytics
        scen = fixture["scenarios"]["triggered_iaSOO_saved_in_prior_epochs"]
        rows = scen["shadow_history_rows"]
        if_held = scen["if_held_pct"]
        sym_id = scen["symphony_id"]
        db_file = _make_db(tmp_path, {"a": scen})

        expected_lifetime = _epoch_additive_alpha(rows)
        produced = analytics.compute_windowed_symphony_guard_alpha(
            _sym_dict(sym_id, if_held, 10000.0), None, window="all", db_path=db_file
        )
        assert produced == pytest.approx(expected_lifetime, abs=1e-6), (
            f"ALL-window guard alpha {produced} != lifetime epoch-additive "
            f"{expected_lifetime}. window='all' must reproduce the AC-1 lifetime value."
        )

    def test_short_window_bites_smaller_than_all(self, fixture, tmp_path):
        """A window SHORTER than the age of the prior-epoch divergence yields a smaller
        alpha than ALL — windowing must actually bite (W1 slice-then-regroup)."""
        import analytics
        scen = fixture["scenarios"]["triggered_iaSOO_saved_in_prior_epochs"]
        rows = scen["shadow_history_rows"]
        if_held = scen["if_held_pct"]
        sym_id = scen["symphony_id"]
        db_file = _make_db(tmp_path, {"a": scen})

        # iaSOO's largest divergence is in EPOCH_A (2026-05-18..22); a window covering only
        # the last 2 rows excludes it. DERIVED expected via the same W1 logic.
        windowed_rows = _last_n_days(rows, 2)
        expected_windowed = _epoch_additive_alpha(windowed_rows)
        expected_all = _epoch_additive_alpha(rows)
        assert expected_windowed < expected_all - 1e-6, (
            "fixture sanity: a 2-day window must exclude the bulk of iaSOO's prior-epoch "
            f"divergence ({expected_windowed} vs all {expected_all})"
        )

        # Pass the window as a day-count token via the int-day path the route maps. We can't
        # pass an arbitrary N through the token enum, so we assert the MONOTONE property over
        # the supported tokens: 'all' >= '30d' for a fixture whose divergence is within 30d,
        # and that a window strictly inside the divergence span is smaller. Use the supported
        # token set: build a SECOND fixture whose divergence is OLDER than 30 days so '30d' bites.
        old_scen = _aged_scenario(scen, days_old=40)
        db_old = _make_db(tmp_path / "old", {"a": old_scen})
        alpha_all = analytics.compute_windowed_symphony_guard_alpha(
            _sym_dict(sym_id, if_held, 10000.0), None, window="all", db_path=db_old
        )
        alpha_30 = analytics.compute_windowed_symphony_guard_alpha(
            _sym_dict(sym_id, if_held, 10000.0), None, window="30d", db_path=db_old
        )
        assert alpha_all == pytest.approx(_epoch_additive_alpha(old_scen["shadow_history_rows"]), abs=1e-6)
        assert abs(alpha_30) < abs(alpha_all) - 1e-6, (
            f"30d-window alpha {alpha_30} must be smaller than ALL {alpha_all} when the "
            "divergence is OLDER than 30 days — windowing must bite."
        )

    def test_never_triggered_zero_on_every_window(self, fixture, tmp_path):
        import analytics
        scen = fixture["scenarios"]["never_triggered_n2ooA"]
        if_held = scen["if_held_pct"]
        sym_id = scen["symphony_id"]
        db_file = _make_db(tmp_path, {"a": scen})
        for window in ("all", "30d", "90d", "1y", "ytd"):
            alpha = analytics.compute_windowed_symphony_guard_alpha(
                _sym_dict(sym_id, if_held, 10000.0), None, window=window, db_path=db_file
            )
            assert alpha == pytest.approx(0.0, abs=1e-9), (
                f"never-triggered guard alpha must be 0.0 on window={window}; got {alpha}"
            )


# ===========================================================================
# Portfolio strip shape + window echo + F7 vol gate
# ===========================================================================

class TestWindowedPortfolioStrip:
    def test_strip_echoes_window_and_has_expected_keys(self, fixture, tmp_path):
        import analytics
        scen = fixture["scenarios"]["triggered_iaSOO_saved_in_prior_epochs"]
        sym_id = scen["symphony_id"]
        db_file = _make_db(tmp_path, {"a": scen})
        symphonies = [_sym_dict(sym_id, scen["if_held_pct"], 10000.0)]

        strip = analytics.compute_windowed_portfolio_strip(
            symphonies, {}, window="all", db_path=db_file
        )
        assert isinstance(strip, dict), f"strip must be a dict; got {type(strip)}"
        assert str(strip.get("window")).lower() == "all", (
            f"strip must echo the resolved window; got {strip.get('window')!r}"
        )
        for key in ("cumulative_return", "max_drawdown"):
            assert key in strip, f"strip missing '{key}'; got {list(strip.keys())}"

    def test_vol_gate_none_when_window_below_min_days(self, fixture, tmp_path):
        """F7: vol_bot/vol_held must be None (and insufficient_history True) when the window's
        day-count < _V1_BOOTSTRAP_MIN_DAYS (30). The fixture has 9 trading days, so a 30d
        window over it is still only 9 days -> below the floor -> vol gated off."""
        import analytics
        scen = fixture["scenarios"]["triggered_iaSOO_saved_in_prior_epochs"]
        sym_id = scen["symphony_id"]
        db_file = _make_db(tmp_path, {"a": scen})
        symphonies = [_sym_dict(sym_id, scen["if_held_pct"], 10000.0)]

        strip = analytics.compute_windowed_portfolio_strip(
            symphonies, {}, window="30d", db_path=db_file
        )
        assert strip.get("vol_bot") is None, (
            "F7 FAIL: vol_bot must be None when the window has < 30 trading days "
            f"(fixture has 9); got {strip.get('vol_bot')}."
        )
        assert strip.get("insufficient_history") is True, (
            "F7 FAIL: insufficient_history must be True for a sub-30-day window."
        )


# ---------------------------------------------------------------------------
# Helper — age a scenario so its divergence falls OUTSIDE a 30-day window
# ---------------------------------------------------------------------------

def _aged_scenario(scen: dict, *, days_old: int) -> dict:
    """Shift all trading_days back by `days_old` so the divergence is older than 30 days,
    making a '30d' window exclude it. Preserves epoch structure + returns verbatim."""
    rows = []
    base_shift = timedelta(days=days_old)
    for r in scen["shadow_history_rows"]:
        d = date.fromisoformat(r["trading_day"]) - base_shift
        rows.append({**r, "trading_day": d.isoformat(),
                     "ts_utc": d.isoformat() + "T20:00:00Z"})
    return {**scen, "shadow_history_rows": rows}
