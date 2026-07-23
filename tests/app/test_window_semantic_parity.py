"""RED tests -- Math Remediation R0, AC-5 (feature-plans/math-r0.md).

DE-MATH-AUDIT-001 / VERDICT.md ma-perf-findings.md MAPERF-03 (MEDIUM):

  The SAME picker click windows the hero chart by TRADING days and the strip
  by CALENDAR days. `/api/hero-chart/<window>` (app.py:2459-2546) maps each
  window token to a trading-day COUNT and slices
  `get_portfolio_bot_and_held_daily_returns(days=fetch_days)` positionally
  (`all_days[-fetch_days:]` -- a trading-day slice). `/api/strip/<window>`
  (via analytics.compute_windowed_portfolio_strip ->
  analytics._window_cutoff_date) resolves the SAME token to a CALENDAR cutoff.
  "30d" -> chart = last 30 trading days (~6 calendar weeks) vs strip = trading
  days within the last 30 calendar days (~21 trading days); "1y" -> chart =
  365 trading days (~17.4 months) vs strip = 365 calendar days (~252 trading
  days) -- a ~40% window mismatch.

  A SEPARATE half of the same class (performance.js:441-445's `ytdDays()`):
  the Performance tab sends a CALENDAR day-count for "YTD" into `/api/
  performance`'s `days` query param, which the route treats as a TRADING-day
  count (`all_days[-days:]`) -- over-fetching past Jan 1.

  AC-5 (as approved, cross-plan correction from team-lead): resolveDays('ytd')
  will send the literal string "ytd" (NOT a computed calendar count); /api/
  performance special-cases "ytd" to a Jan-1 calendar cutoff. The other six
  Performance-tab buttons (30/60/90/125/252/1260) REMAIN deliberate
  trading-day counts by design -- only YTD was broken, and only YTD changes
  contract. Separately, the hero chart's trading-day slicing for 30d/60d/90d/
  125d/1y must convert FROM the calendar cutoff analytics._window_cutoff_date
  already canonicalizes (the same function /api/strip uses), so the SAME
  window token covers the SAME calendar span on both surfaces.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

import analytics
import app as app_module
import database


@pytest.fixture(autouse=True)
def _analytics_shadow_db_seam(_isolate_db, monkeypatch):
    """See tests/app/test_performance_symphony_scope_source.py for the full
    rationale -- point analytics.py's shadow-history readers at the same
    per-test DB _isolate_db just initialised."""
    monkeypatch.setattr(analytics, "DB_FILE", os.environ["DB_PATH"])


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _seed_sparse_shadow_history(symphony_id: str, n_trading_days: int, spread_days: int) -> None:
    """n_trading_days rows, one every `spread_days // n_trading_days`-ish
    calendar days, ending TODAY -- so the trading-day count and the calendar
    span diverge sharply (the exact condition that exposes MAPERF-03)."""
    today = date.today()
    step = max(1, spread_days // n_trading_days)
    dates = [today - timedelta(days=i * step) for i in range(n_trading_days)]
    dates.sort()
    for i, d in enumerate(dates):
        iso = d.isoformat()
        database.record_shadow_observation(
            symphony_id=symphony_id,
            account_id="acct-1",
            cycle_id=f"cyc-{i}",
            ts_utc=f"{iso}T20:00:00Z",
            ts_et=f"{iso}T16:00:00",
            trading_day=iso,
            current_return=0.1,
            shadow_return=0.1,
            is_post_trigger=0,
            trigger_id=None,
        )


# ===========================================================================
# RED-6 -- hero-chart's trading-day slice must be bounded by the SAME
# calendar cutoff analytics._window_cutoff_date resolves for the strip.
# ===========================================================================


def test_ac5_hero_chart_30d_does_not_exceed_the_calendar_cutoff(client):
    """30 trading days spread across ~90 calendar days: the hero chart's "30d"
    window must not reach back further than the real 30-CALENDAR-day cutoff
    (analytics._window_cutoff_date("30d"), called for real -- not hardcoded).

    MUST FAIL at 98901abf: /api/hero-chart/30d trading-day-slices
    (`all_days[-30:]`), so with only 30 total rows every one of them is
    returned -- spanning the full ~90 calendar days, far older than the true
    30-calendar-day cutoff."""
    symphony_id = "sym_sparse_30"
    _seed_sparse_shadow_history(symphony_id, n_trading_days=30, spread_days=90)
    database.save_state({symphony_id: {"name": "Sparse 30", "current_value": 1000.0}})

    cutoff = analytics._window_cutoff_date("30d")
    assert cutoff is not None, "fixture integrity: 30d must resolve to a real calendar cutoff"

    resp = client.get("/api/hero-chart/30d")
    assert resp.status_code == 200
    body = resp.get_json()
    dates = body["hist_dates"]
    assert dates, "fixture integrity: hero-chart must return a non-empty series"

    earliest = min(dates)
    assert earliest >= cutoff.isoformat(), (
        f"hero-chart's earliest returned date ({earliest}) is BEFORE the real "
        f"30-calendar-day cutoff ({cutoff.isoformat()}) -- the chart is still "
        "trading-day-slicing instead of converting from the calendar cutoff "
        "(MAPERF-03)"
    )


# ===========================================================================
# RED-7 (revised per team-lead's cross-plan correction) -- YTD sends the
# literal string "ytd", and /api/performance resolves it to a Jan-1 cutoff.
# The other six Performance-tab buttons remain deliberate trading-day counts.
# ===========================================================================


def test_ac5_resolve_days_emits_the_literal_ytd_token_not_a_calendar_count():
    """performance.js's resolveDays('ytd') must emit the literal string 'ytd'
    -- NOT a computed calendar-days-since-Jan-1 count via ytdDays().

    MUST FAIL at 98901abf: resolveDays returns `String(ytdDays())`."""
    import re
    from pathlib import Path

    js_path = Path(__file__).parent.parent.parent / "static" / "performance.js"
    content = js_path.read_text(encoding="utf-8")
    match = re.search(r"function\s+resolveDays\s*\([^)]*\)\s*\{(.+?)\n\s*\}", content, re.DOTALL)
    assert match is not None, "resolveDays function not found in performance.js"
    fn_body = match.group(1)

    assert "ytdDays" not in fn_body, (
        "resolveDays still calls ytdDays() -- AC-5 (as corrected) requires "
        f"resolveDays('ytd') to return the literal string 'ytd' instead. Body:\n{fn_body}"
    )
    assert re.search(r"return\s+['\"]ytd['\"]", fn_body), (
        "resolveDays does not return the literal string 'ytd' for the ytd case -- "
        f"got body:\n{fn_body}"
    )


def test_ac5_performance_route_resolves_literal_ytd_to_jan1_calendar_cutoff(client):
    """/api/performance?scope=aggregate&days=ytd (the literal string the
    corrected resolveDays sends) must return a series starting at/after Jan 1
    of the current year -- the route special-cases the 'ytd' token to a
    calendar cutoff, per team-lead's approved AC-5 contract.

    MUST FAIL at 98901abf: `days = int(request.args.get("days", 60))` raises
    ValueError on the literal string "ytd" -> 400, and even if it didn't,
    "days" is otherwise always a trading-day count with no ytd special case."""
    symphony_id = "sym_ytd_boundary"
    today = date.today()
    jan1_this_year = date(today.year, 1, 1)
    # Rows spanning from before last Jan 1 through today, so a correct Jan-1
    # cutoff and a "365 trading days back" trading-day slice diverge.
    span_start = jan1_this_year - timedelta(days=60)
    n_days = (today - span_start).days + 1
    dates = [(span_start + timedelta(days=i)).isoformat() for i in range(0, n_days, 3)]
    for i, d in enumerate(dates):
        database.record_shadow_observation(
            symphony_id=symphony_id,
            account_id="acct-1",
            cycle_id=f"cyc-{i}",
            ts_utc=f"{d}T20:00:00Z",
            ts_et=f"{d}T16:00:00",
            trading_day=d,
            current_return=0.05,
            shadow_return=0.05,
            is_post_trigger=0,
            trigger_id=None,
        )
    database.save_state({symphony_id: {"name": "YTD Boundary", "current_value": 2000.0}})

    resp = client.get("/api/performance?scope=aggregate&days=ytd")
    assert resp.status_code == 200, (
        f"GET /api/performance?scope=aggregate&days=ytd must return 200 under the "
        f"corrected literal-'ytd' contract; got {resp.status_code}: {resp.get_data(as_text=True)}"
    )
    body = resp.get_json()
    dates_out = body["dates"]
    assert dates_out, "fixture integrity: expected a non-empty YTD series"
    earliest = min(dates_out)
    assert earliest >= jan1_this_year.isoformat(), (
        f"scope=aggregate&days=ytd earliest date ({earliest}) is before Jan 1 of the "
        f"current year ({jan1_this_year.isoformat()}) -- the route is not resolving "
        "the literal 'ytd' token to a calendar cutoff"
    )


# ===========================================================================
# RED-8 -- hero-chart must consult the SAME cutoff function analytics.py
# already canonicalizes for the strip (wiring proof, not just behavior).
# ===========================================================================


def test_ac5_hero_chart_consults_the_canonical_window_cutoff_function(client, monkeypatch):
    """WIRING proof: /api/hero-chart/<window> must call
    analytics._window_cutoff_date (the SAME function /api/strip's
    compute_windowed_portfolio_strip already uses) for its non-"all" windows
    -- proving the chart and strip share one window-cutoff semantic rather
    than the chart maintaining its own independent day-count table.

    MUST FAIL at 98901abf: get_hero_chart's window->fetch_days mapping
    (app.py:2469-2485) is a hardcoded per-token table, never calling
    _window_cutoff_date."""
    symphony_id = "sym_wiring_check"
    _seed_sparse_shadow_history(symphony_id, n_trading_days=40, spread_days=150)
    database.save_state({symphony_id: {"name": "Wiring Check", "current_value": 1500.0}})

    calls: list[object] = []
    real_cutoff_fn = analytics._window_cutoff_date

    def _spy(window):
        calls.append(window)
        return real_cutoff_fn(window)

    monkeypatch.setattr(analytics, "_window_cutoff_date", _spy)

    resp = client.get("/api/hero-chart/90d")
    assert resp.status_code == 200

    assert calls, (
        "GET /api/hero-chart/90d never called analytics._window_cutoff_date -- "
        "the hero chart must derive its window boundary from the SAME "
        "calendar-cutoff function the strip route uses (AC-5), not an "
        "independent trading-day-count table"
    )
    assert "90d" in [str(c).lower() for c in calls], (
        f"_window_cutoff_date was called with {calls!r}, none matching the "
        "requested '90d' window token"
    )
