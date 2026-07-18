"""RED tests -- Math Remediation R0, AC-8 (feature-plans/math-r0.md).

DE-MATH-AUDIT-001 / VERDICT.md ma-perf-findings.md MAPERF-04 (MEDIUM, two
stacked defects) + MAPERF-13 (LOW, permanent-arming):

  `/api/strip/<window>` (app.py:2609-2645) fires an intraday cross-day
  exit_triggers estimate whenever `strip.get("insufficient_history") and not
  strip.get("guard_alpha")`. Two bugs:
    1. `not strip.get("guard_alpha")` is TRUE for a legitimate windowed `0.0`
       (an untriggered symphony -- analytics.py:1583 "Untriggered symphonies
       (shadow == current) yield 0.0 on every window") -- a real zero gets
       silently overwritten.
    2. The estimate query (app.py:2617-2622) is UNFILTERED by date -- it sums
       every exit_triggers row ever recorded against the LATEST current_return,
       so a stale (non-today) trigger row is cross-day-incoherent (subtracts
       returns from two different days' bases).
  MAPERF-13: `_WINDOWED_VOL_MIN_DAYS=30` (trading days) vs the "30d" window's
  30-CALENDAR-day cutoff -- 30 calendar days is ~21 trading days, so the 30d
  pick can NEVER clear the floor, permanently arming the fallback for that
  window regardless of how much real history exists.

  AC-8: (a) `guard_alpha is None` check, never falsy; (b) the estimate is
  day-filtered to the current ET trading day; (c) the 30d window gets an
  honest available-trading-days floor or an explicit "insufficient window"
  state -- never the silently-armed fallback.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import analytics
import app as app_module
import database

_ET_ISO = "%Y-%m-%d"
# Same ET-day derivation the routes under test use (app.py:2582:
# `trading_day = datetime.now(_ET).strftime("%Y-%m-%d")`). The bare
# datetime.date.today() built-in returns the machine's LOCAL calendar date,
# which drifts a full day ahead of the ET trading day on CI (UTC clock)
# between 00:00-04:00 UTC, silently seeding rows the day-filtered route can
# never find. NEVER the bare date.today() built-in in this file.
_ET = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _analytics_shadow_db_seam(_isolate_db, monkeypatch):
    """See tests/app/test_performance_symphony_scope_source.py for the full
    rationale: analytics.py's shadow-history readers resolve their DB file via
    the analytics.DB_FILE test-time seam, not the DB_PATH env var _isolate_db
    sets -- point analytics at the same per-test DB."""
    monkeypatch.setattr(analytics, "DB_FILE", os.environ["DB_PATH"])


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _seed_bot_state(entries: dict[str, dict]) -> None:
    database.save_state(entries)


def _seed_shadow_rows(
    symphony_id: str, dates: list[str], *, current_return: float, shadow_return: float
) -> None:
    for i, d in enumerate(dates):
        database.record_shadow_observation(
            symphony_id=symphony_id,
            account_id="acct-1",
            cycle_id=f"cyc-{i}",
            ts_utc=f"{d}T20:00:00Z",
            ts_et=f"{d}T16:00:00",
            trading_day=d,
            current_return=current_return,
            shadow_return=shadow_return,
            is_post_trigger=0,
            trigger_id=None,
        )


def _recent_dates(n: int) -> list[str]:
    """The n most recent calendar dates ending today (inclusive)."""
    today = datetime.now(_ET).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


# ===========================================================================
# RED-12 -- None vs falsy: a legitimate windowed 0.0 must not be overwritten.
# ===========================================================================


def test_ac8_legitimate_zero_guard_alpha_is_not_overwritten_by_fallback(client):
    """An UNTRIGGERED symphony (shadow_return == current_return on every row)
    yields a genuine windowed guard_alpha of 0.0 (analytics.py:1583). With
    fewer than _WINDOWED_VOL_MIN_DAYS(=30) trading days recorded,
    insufficient_history is also True -- but the 0.0 is a REAL value, not a
    missing one, and must survive.

    MUST FAIL at 98901abf: `not strip.get("guard_alpha")` is True for 0.0, so
    the route overwrites it with the exit_triggers cross-day estimate whenever
    exit_triggers rows exist for ANY symphony with a bot_state position value."""
    symphony_id = "sym_never_triggered"
    dates = _recent_dates(10)  # < 30 trading days -> insufficient_history=True
    _seed_shadow_rows(symphony_id, dates, current_return=0.35, shadow_return=0.35)
    _seed_bot_state({symphony_id: {"name": "Never Triggered", "current_value": 5000.0}})

    # A real exit_triggers row for a DIFFERENT symphony with a position value,
    # so the fallback has something non-zero to overwrite with IF it fires.
    other_id = "sym_other_has_exit"
    _seed_shadow_rows(other_id, dates, current_return=1.5, shadow_return=1.5)
    _seed_bot_state(
        {
            symphony_id: {"name": "Never Triggered", "current_value": 5000.0},
            other_id: {"name": "Has Exit", "current_value": 8000.0},
        }
    )
    database.record_exit_trigger(
        symphony_id=other_id,
        account_id="acct-1",
        triggered_reason="trailing_stop",
        at_return=4.0,  # far from other_id's current_return (1.5) -- a big, detectable delta
        cycle_id="cyc-exit-1",
        ts_utc=f"{dates[-1]}T15:54:00Z",
        ts_et=f"{dates[-1]}T11:54:00",
    )

    resp = client.get("/api/strip/all")
    assert resp.status_code == 200
    strip = resp.get_json()

    assert strip.get("insufficient_history") is True, (
        "fixture integrity: expected insufficient_history=True (fewer than 30 "
        f"trading days seeded); got {strip.get('insufficient_history')}"
    )
    assert strip.get("guard_alpha") == pytest.approx(0.0), (
        f"a legitimate windowed guard_alpha of 0.0 (untriggered symphonies only) "
        f"must not be overwritten by the cross-day exit_triggers estimate; got "
        f"{strip.get('guard_alpha')!r}. AC-8 requires an explicit `is None` check."
    )


# ===========================================================================
# RED-13 -- the fallback estimate must be day-filtered, not cross-day.
# ===========================================================================


def test_ac8_fallback_estimate_excludes_stale_exit_trigger_rows(client):
    """When the fallback legitimately fires (guard_alpha is genuinely None --
    no symphony passes the value-weight filter), the estimate must be
    DAY-FILTERED to the current ET trading day -- not summed across every
    exit_triggers row ever recorded regardless of date.

    Two exit_triggers rows for the SAME symphony: one from TODAY, one STALE
    (10 days ago), with DIFFERENT at_return values. The route pairs each row's
    at_return against the symphony's LATEST current_return (unfiltered), so
    today's estimate should be `at_return_today - latest_current_return` alone
    -- not an average that includes the stale row's stale at_return paired
    against today's current_return.

    MUST FAIL at 98901abf: the fallback SELECT has no date filter
    (app.py:2617-2622 `FROM exit_triggers t` with no WHERE clause)."""
    symphony_id = "sym_stale_and_fresh"
    dates = _recent_dates(10)  # < 30 trading days -> insufficient_history=True
    latest_current_return = 2.0
    # Seed the daily shadow rows individually so only the FINAL one carries
    # latest_current_return (the subquery is ORDER BY ts_utc DESC LIMIT 1);
    # earlier rows are irrelevant filler for the >=2-day trajectory guard.
    for i, d in enumerate(dates):
        cr = latest_current_return if d == dates[-1] else 1.0
        database.record_shadow_observation(
            symphony_id=symphony_id,
            account_id="acct-1",
            cycle_id=f"cyc-{i}",
            ts_utc=f"{d}T20:00:00Z",
            ts_et=f"{d}T16:00:00",
            trading_day=d,
            current_return=cr,
            shadow_return=cr,
            is_post_trigger=0,
            trigger_id=None,
        )
    # bot_state carries a REAL current_value (needed for the exit_triggers
    # fallback's own weight check) but DELIBERATELY omits "name" -- the strip
    # route's own symphonies_list build (app.py:2578) requires `"name" in v`,
    # so this symphony is excluded from the strip's OWN weight-sum loop
    # (compute_windowed_portfolio_strip never sees it) and the portfolio
    # windowed_alpha stays genuinely None (weight_sum==0.0). The exit_triggers
    # fallback, however, reads bot_state directly via database.load_state()
    # with no "name" requirement, so it DOES see this symphony's current_value
    # -- exactly the split needed to prove guard_alpha is truly None (not the
    # falsy-0.0 case RED-12 covers) while still giving the fallback something
    # real to compute from.
    _seed_bot_state({symphony_id: {"current_value": 6000.0}})

    at_return_today = 5.0
    at_return_stale = -3.0
    today_str = dates[-1]
    stale_str = dates[0]
    database.record_exit_trigger(
        symphony_id=symphony_id,
        account_id="acct-1",
        triggered_reason="trailing_stop",
        at_return=at_return_today,
        cycle_id="cyc-today",
        ts_utc=f"{today_str}T15:54:00Z",
        ts_et=f"{today_str}T11:54:00",
    )
    database.record_exit_trigger(
        symphony_id=symphony_id,
        account_id="acct-1",
        triggered_reason="trailing_stop",
        at_return=at_return_stale,
        cycle_id="cyc-stale",
        ts_utc=f"{stale_str}T15:54:00Z",
        ts_et=f"{stale_str}T11:54:00",
    )

    # Fixture-integrity check (OFFLINE, pre-fallback): the strip route builds
    # symphonies_list only from bot_state entries carrying a "name" key
    # (app.py:2578); this symphony has none, so it's excluded from that list
    # and compute_windowed_portfolio_strip's OWN weight-sum loop never sees
    # it -- an empty symphonies_list yields a genuinely None guard_alpha
    # (weight_sum stays 0.0), proving this is NOT the falsy-0.0 case RED-12
    # covers. We can't observe this pre-fallback value through the route's
    # single HTTP response (the fallback, if it fires, overwrites it
    # server-side before the JSON is built) -- so we verify it directly.
    raw_strip = analytics.compute_windowed_portfolio_strip([], {}, window="all")
    assert raw_strip.get("guard_alpha") is None, (
        "fixture integrity: an empty symphonies_list must yield guard_alpha=None "
        f"(weight_sum==0.0); got {raw_strip.get('guard_alpha')!r} -- the day-filter "
        "assertion below is meaningless without a genuine None precondition"
    )
    assert raw_strip.get("insufficient_history") is True, (
        "fixture integrity: an empty symphonies_list must report "
        f"insufficient_history=True; got {raw_strip.get('insufficient_history')!r}"
    )

    resp = client.get("/api/strip/all")
    assert resp.status_code == 200
    strip = resp.get_json()

    expected_today_only = at_return_today - latest_current_return
    armed_value = strip.get("guard_alpha")
    assert armed_value is not None, (
        "fixture integrity: expected the intraday fallback to arm (guard_alpha "
        "was None and insufficient_history was True) -- got no fallback value "
        f"at all; strip={strip}"
    )
    assert armed_value == pytest.approx(expected_today_only), (
        f"the fallback estimate must be day-filtered to TODAY's exit_triggers row "
        f"only ({expected_today_only} = at_return_today({at_return_today}) - "
        f"latest_current_return({latest_current_return})); got {armed_value} -- "
        "a stale (non-today) trigger row is polluting the cross-day estimate"
    )


# ===========================================================================
# RED-14 -- the 30d window must not be permanently, silently armed for a
# portfolio with genuine, substantial trading history.
# ===========================================================================


def test_ac8_30d_window_with_ample_real_history_is_not_silently_armed(client):
    """A portfolio with 60 REAL trading days of history (well past the day-1
    droplet case) must not be treated identically to a data-starved portfolio
    just because the literal 30-CALENDAR-day window (~21 trading days) can
    never clear the 30-trading-day floor (MAPERF-13's permanent-arming bug).

    We do not dictate r0-fe's exact mechanism (raised floor vs an explicit
    'insufficient window' state) -- we pin the OBSERVABLE symptom: the sketchy
    intraday cross-day exit_triggers estimate (`intraday_only=True`) must not
    silently arm for the 30d window when 60 days of real, non-trigger-based
    history exist elsewhere.

    MUST FAIL at 98901abf: `_WINDOWED_VOL_MIN_DAYS=30` is a flat trading-day
    floor applied to whatever the window's calendar cutoff yields; the 30d
    window's calendar cutoff can never contain 30 trading days, so
    insufficient_history is unconditionally True for "30d" and the fallback
    arms whenever a matching exit_triggers row exists."""
    symphony_id = "sym_ample_history"
    # 60 real trading days spread across ~84 calendar days (untriggered ->
    # guard_alpha would genuinely be 0.0 across "all", proving the history
    # is real, not a day-1 gap).
    all_dates = [f"2025-{m:02d}-{d:02d}" for m in range(1, 4) for d in range(1, 21)]
    _seed_shadow_rows(symphony_id, all_dates, current_return=0.5, shadow_return=0.5)
    _seed_bot_state({symphony_id: {"name": "Ample History", "current_value": 4000.0}})
    database.record_exit_trigger(
        symphony_id=symphony_id,
        account_id="acct-1",
        triggered_reason="trailing_stop",
        at_return=9.0,
        cycle_id="cyc-x",
        ts_utc=f"{all_dates[-1]}T15:54:00Z",
        ts_et=f"{all_dates[-1]}T11:54:00",
    )

    resp = client.get("/api/strip/30d")
    assert resp.status_code == 200
    strip = resp.get_json()

    assert not strip.get("intraday_only"), (
        "the 30d window silently armed the sketchy intraday cross-day estimate "
        f"(intraday_only={strip.get('intraday_only')!r}) despite 60 trading days of "
        "REAL history existing for this symphony -- MAPERF-13's permanent-arming bug. "
        f"Full strip: {strip}"
    )


# ===========================================================================
# AC-8b (ADDENDUM, feature-plans/math-r0.md -- PM ruling on r0-test's
# escalation, 2026-07-17): compute_windowed_symphony_guard_alpha collapses TWO
# structurally different states into the identical 0.0 (analytics.py:1672-1673
# `if trajectory is None: return 0.0`):
#   (1) the deliberate <2-in-window-rows conservatism floor
#       (_get_windowed_divergence_trajectory, analytics.py:1622-1623) -- even
#       though _epoch_additive_divergence's compound-product-per-epoch formula
#       is mathematically valid from a SINGLE row;
#   (2) a genuinely-computed zero divergence (>=2 rows, shadow == current
#       throughout).
# AC-8's `guard_alpha is None` check (RED-12/13/14 above) can't tell these
# apart, so it ALSO withholds the fallback for case (1) -- silently
# regressing the previously-shipped DE-PROD-ACCURACY-001 day-1 behavior (a
# real divergence on a thin-window state should still surface via the
# fallback; a genuinely-zero divergence should not).
#
# AC-8b-1: the <2-row floor case propagates None (not 0.0) up through
#   compute_windowed_symphony_guard_alpha -- the floor itself is unchanged
#   (its statistical conservatism is not R0's to relitigate), only its return
#   ENCODING changes so callers can discriminate "unknown" from "zero".
# AC-8b-2 (caller sweep): compute_windowed_symphony_guard_alpha has exactly
#   ONE production caller (compute_windowed_portfolio_strip's VW loop,
#   analytics.py:1735) -- and it ALREADY does `if sym_alpha is None: continue`
#   (skip-and-count), so no caller-side change is required; this file's
#   route-level test proves that end-to-end (not just asserted by inspection).
# AC-8b-3: the discriminating test pair below.
# ===========================================================================


def _sym_dict_for(symphony_id: str) -> dict:
    """Minimal symphonies_list entry -- only "id" is read by
    compute_windowed_symphony_guard_alpha (bot_state_entry is unused in its
    body); mirrors the shape app.py's strip route builds."""
    return {"id": symphony_id}


def test_ac8b_thin_window_single_row_returns_none_not_zero():
    """UNIT pin (AC-8b-1): a symphony with exactly 1 in-window shadow_history
    row -- a REAL divergence (shadow_return != current_return), so there is
    genuine information, just not enough rows to clear the deliberate <2-row
    floor -- must return None from compute_windowed_symphony_guard_alpha, not
    a fabricated 0.0.

    MUST FAIL at 1289ff0b: `if trajectory is None: return 0.0` collapses this
    case (and the genuine-zero case below) to the same 0.0."""
    symphony_id = "sym_thin_window_real_divergence"
    today = datetime.now(_ET).date().isoformat()
    database.record_shadow_observation(
        symphony_id=symphony_id,
        account_id="acct-1",
        cycle_id="cyc-1",
        ts_utc=f"{today}T20:00:00Z",
        ts_et=f"{today}T16:00:00",
        trading_day=today,
        current_return=-0.5,
        shadow_return=2.5,  # real 3.0pp divergence -- NOT a genuine zero
        is_post_trigger=1,
        trigger_id=None,
    )

    result = analytics.compute_windowed_symphony_guard_alpha(
        _sym_dict_for(symphony_id), None, window="30d"
    )
    assert result is None, (
        f"a single-row (thin-window) real-divergence symphony must return None "
        f"(insufficient windowed data, not a computed zero); got {result!r}. "
        "AC-8b-1 requires the <2-row floor to propagate None, distinguishing it "
        "from a genuinely-computed zero divergence."
    )


def test_ac8b_computed_zero_divergence_still_returns_real_zero():
    """UNIT pin (AC-8b-1, companion): a symphony with >=2 in-window rows where
    shadow_return == current_return on EVERY row -- a genuinely-computed zero
    divergence, not an insufficient-data case -- must still return exactly
    0.0, not None. The floor-vs-computed distinction must not accidentally
    turn every zero into None.

    Self-guard: this must PASS both before and after the AC-8b-1 fix (it pins
    the case AC-8b-1 explicitly says stays 0.0)."""
    symphony_id = "sym_computed_zero_divergence"
    dates = _recent_dates(5)  # >=2 rows clears the floor
    for i, d in enumerate(dates):
        database.record_shadow_observation(
            symphony_id=symphony_id,
            account_id="acct-1",
            cycle_id=f"cyc-{i}",
            ts_utc=f"{d}T20:00:00Z",
            ts_et=f"{d}T16:00:00",
            trading_day=d,
            current_return=0.2,
            shadow_return=0.2,  # identical every day -- genuinely zero divergence
            is_post_trigger=0,
            trigger_id=None,
        )

    result = analytics.compute_windowed_symphony_guard_alpha(
        _sym_dict_for(symphony_id), None, window="30d"
    )
    assert result == pytest.approx(0.0), (
        f"a >=2-row genuinely-zero-divergence symphony must return a real 0.0 "
        f"(not None -- there IS enough data, and it computed to zero); got {result!r}"
    )


def test_ac8b_thin_window_real_divergence_surfaces_via_route_fallback(client):
    """ROUTE-level pin (AC-8b-2/3, the regression-fix proof): with the
    compute_windowed_symphony_guard_alpha fix landed, a thin-window (1-day)
    symphony with a REAL divergence must have its portfolio-level guard_alpha
    become genuinely None (the sole symphony is skipped by the VW loop's
    existing `if sym_alpha is None: continue`) -- which correctly ARMS the
    day-filtered intraday fallback (AC-8's `is None` check), surfacing the
    real divergence instead of a flat 0.0. This is the exact scenario the 6
    held tests in test_live_dashboard_metrics.py were probing.

    MUST FAIL until AC-8b-1 lands: today compute_windowed_symphony_guard_alpha
    returns 0.0 (not None) for this fixture, so guard_alpha is a real 0.0 at
    the portfolio level and AC-8's is-None check correctly (but, for THIS
    case, wrongly) withholds the fallback -- flattening a real divergence to
    0.0."""
    symphony_id = "sym_thin_window_route_level"
    today = datetime.now(_ET).date().isoformat()
    at_return = 2.5
    current_return = -0.5  # matches the shadow row below -- real 3.0pp divergence
    database.record_shadow_observation(
        symphony_id=symphony_id,
        account_id="acct-1",
        cycle_id="cyc-1",
        ts_utc=f"{today}T20:00:00Z",
        ts_et=f"{today}T16:00:00",
        trading_day=today,
        current_return=current_return,
        shadow_return=at_return,
        is_post_trigger=1,
        trigger_id=None,
    )
    _seed_bot_state({symphony_id: {"name": "Thin Window Route", "current_value": 15000.0}})
    database.record_exit_trigger(
        symphony_id=symphony_id,
        account_id="acct-1",
        triggered_reason="take_profit",
        at_return=at_return,
        cycle_id="cyc-2",
        ts_utc=f"{today}T15:54:00Z",
        ts_et=f"{today}T11:54:00",
    )

    resp = client.get("/api/strip/30d")
    assert resp.status_code == 200
    strip = resp.get_json()

    expected_estimate = at_return - current_return
    assert strip.get("intraday_only") is True, (
        f"a thin-window (1-day) symphony with a real divergence must arm the "
        f"day-filtered intraday fallback; got intraday_only={strip.get('intraday_only')!r}, "
        f"full strip: {strip}"
    )
    assert strip.get("guard_alpha") == pytest.approx(expected_estimate), (
        f"the armed fallback must surface the real divergence "
        f"({expected_estimate} = at_return({at_return}) - current_return({current_return})); "
        f"got {strip.get('guard_alpha')!r}"
    )
