"""RED tests -- Math Remediation R0, AC-3 + AC-4 (feature-plans/math-r0.md).

DE-MATH-AUDIT-001 / VERDICT.md MA-6 (ma-perf-findings.md MAPERF-01, HIGH) + MA-7
(MAPERF-02, HIGH):

  AC-3: `/api/performance?scope=symphony` sources its per-symphony series from
  `analytics.compute_per_symphony_returns` -- which reads post-mortem trigger
  arrays (`reporting.py:56 if sym.get("triggered")`), i.e. ONLY the days a
  symphony fired. compute_quantstats_metrics then places those K trigger-day
  observations on a SYNTHETIC CONSECUTIVE daily index (analytics.py:378) and
  annualizes as if they were K consecutive trading days -- a 4-trigger sample
  averaging ~0.45%/day annualizes to ~209.8% CAGR (ma-perf PROBE-1). The route's
  OWN Finding-4 comment (app.py:3163-3167) condemns exactly this source for the
  aggregate scope, which was moved off it -- symphony scope was left on.
  Fix direction (name only, not tested here): source the per-symphony series from
  shadow_history per-day rows -- the per-symphony analogue of
  get_portfolio_bot_and_held_daily_returns.

  AC-4: both `if not dates:` fallbacks in the performance route (app.py:3189,
  3203) are NOT scope-gated -- a scope=symphony request for a symphony with zero
  post-mortem triggers falls through to the whole-PORTFOLIO shadow_history series,
  rendered under that symphony's name/header.

THIS FILE tests BEHAVIOR at the route boundary, not a specific new helper name
or internal call graph -- so it does not over-constrain r0-fe's implementation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import analytics
import app as app_module
import database

_ET_DATES_A = [f"2025-{m:02d}-{d:02d}" for m in range(1, 4) for d in range(1, 29)]  # 84 days


@pytest.fixture(autouse=True)
def _analytics_shadow_db_seam(_isolate_db, monkeypatch):
    """analytics.py's shadow-history readers (get_portfolio_bot_and_held_daily_
    returns et al.) resolve their DB file via the analytics.DB_FILE test-time
    override seam (analytics.py:519, docstring: "honours test-time DB_FILE
    override") -- NOT via the DB_PATH env var conftest's _isolate_db sets.
    Without this, those readers silently see a stale/uninitialized session-level
    DB and return None, even though database.record_shadow_observation (which
    DOES honour DB_PATH dynamically via database._db_file()) wrote real rows to
    the per-test DB. Point analytics at the SAME per-test DB _isolate_db just
    initialised -- depends on _isolate_db by name so ordering is guaranteed."""
    monkeypatch.setattr(analytics, "DB_FILE", os.environ["DB_PATH"])


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def pm_dir(tmp_path, monkeypatch) -> Path:
    pm = tmp_path / "post_mortems"
    pm.mkdir()
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(pm))
    return pm


def _write_post_mortem_file(pm_dir: Path, date_str: str, triggers: list[dict]) -> None:
    (pm_dir / f"post_mortem_{date_str}.json").write_text(
        json.dumps({"date": date_str, "triggers": triggers}), encoding="utf-8"
    )


def _seed_full_daily_shadow_history(
    symphony_id: str, dates: list[str], *, daily_pct: float
) -> None:
    """A full, BOUNDED daily series for one symphony -- current_return and
    shadow_return both = daily_pct on every date (both live and guarded track
    identically; the point is the DURATION, not divergence)."""
    for i, d in enumerate(dates):
        database.record_shadow_observation(
            symphony_id=symphony_id,
            account_id="acct-1",
            cycle_id=f"cyc-{i}",
            ts_utc=f"{d}T20:00:00Z",
            ts_et=f"{d}T16:00:00",
            trading_day=d,
            current_return=daily_pct,
            shadow_return=daily_pct,
            is_post_trigger=0,
            trigger_id=None,
        )


def _seed_four_trigger_event_sample(pm_dir: Path, symphony_id: str, dates: list[str]) -> None:
    """The OLD (buggy) source: 4 trigger-day-only entries at ~+0.45%/day --
    reproduces ma-perf PROBE-1's 209.8%-CAGR magnitude when treated as 4
    consecutive trading days."""
    per_day_pct = [0.5, 0.3, 0.8, 0.2]  # matches ma-perf PROBE-1's exact fixture
    for d, pct in zip(dates, per_day_pct, strict=True):
        _write_post_mortem_file(
            pm_dir,
            d,
            [
                {
                    "symphony_name": symphony_id,
                    "symphony_value": 10000.0,
                    "shadow_return": pct,
                    "exit_return": pct,
                    "triggered": True,
                }
            ],
        )


# ===========================================================================
# AC-3 -- per-symphony series must NOT be a trigger-day event sample treated
# as consecutive trading days.
# ===========================================================================


def test_ac3_four_trigger_symphony_does_not_annualize_to_triple_digit_cagr(
    client, pm_dir, monkeypatch
):
    """Regression pin (ma-perf MAPERF-01 magnitude, not a literal): a symphony
    with exactly 4 (widely-spaced) trigger days but a FULL bounded daily
    shadow_history series must not report a triple-digit annualized CAGR on
    scope=symphony -- the event-sample-as-consecutive-days defect.

    MUST FAIL at 98901abf: the route sources scope=symphony exclusively from
    compute_per_symphony_returns (the post-mortem trigger array), so the 4-event
    sample gets annualized as 4 consecutive trading days -- reproducing ma-perf's
    ~209.8% CAGR magnitude."""
    symphony_id = "sym_four_trigger"
    trigger_dates = [_ET_DATES_A[10], _ET_DATES_A[30], _ET_DATES_A[50], _ET_DATES_A[70]]
    _seed_four_trigger_event_sample(pm_dir, symphony_id, trigger_dates)
    # The REAL daily history spans 84 days at a modest, bounded daily return --
    # this is what a genuine per-day shadow_history source would report.
    _seed_full_daily_shadow_history(symphony_id, _ET_DATES_A, daily_pct=0.05)

    resp = client.get(f"/api/performance?scope=symphony&symphony_id={symphony_id}&days=90")
    assert resp.status_code == 200
    body = resp.get_json()

    for metrics_key in ("live_metrics", "shadow_metrics"):
        ann = body[metrics_key].get("annualized_return")
        if ann is None:
            continue
        assert abs(ann) < 1.0, (
            f"{metrics_key}.annualized_return={ann} is triple-digit-CAGR-class -- "
            "the per-symphony series is still sourced from the 4-trigger post-mortem "
            "event sample treated as consecutive trading days (MAPERF-01). Expected a "
            "bounded value from the full daily shadow_history series."
        )


def test_ac3_observation_count_reflects_full_daily_span_not_trigger_count(
    client, pm_dir, monkeypatch
):
    """The per-symphony observation_count for a symphony with a full daily
    shadow_history span must reflect that FULL span (bounded by the requested
    `days` window), not just the handful of trigger days.

    MUST FAIL at 98901abf: observation_count == 4 (the trigger count), not
    anywhere close to the ~84-day daily span."""
    symphony_id = "sym_daily_span"
    trigger_dates = [_ET_DATES_A[5], _ET_DATES_A[25]]
    _seed_four_trigger_event_sample(pm_dir, symphony_id, trigger_dates[:2] + trigger_dates[:2])
    _seed_full_daily_shadow_history(symphony_id, _ET_DATES_A, daily_pct=0.02)

    resp = client.get(f"/api/performance?scope=symphony&symphony_id={symphony_id}&days=90")
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["observation_count"] > 10, (
        f"observation_count={body['observation_count']} looks like the trigger-day "
        f"count, not the ~{len(_ET_DATES_A)}-day daily shadow_history span (MAPERF-01)."
    )


# ===========================================================================
# AC-4 -- scope=symphony with no data must NOT fall back to portfolio numbers.
# ===========================================================================


def test_ac4_zero_data_symphony_scope_is_not_the_portfolio_series(client, pm_dir):
    """A symphony with ZERO post-mortem triggers and ZERO shadow_history rows
    must render an honest empty state on scope=symphony -- never the whole
    portfolio's non-empty series under its name (MAPERF-02).

    We seed a genuinely non-empty PORTFOLIO (two other symphonies with full
    shadow_history spans) so the unconditional `if not dates:` fallback has real
    non-empty data to leak, then request scope=symphony for a THIRD symphony
    that has no data of its own at all.

    MUST FAIL at 98901abf: both `if not dates:` fallbacks in the performance
    route are unconditional -- the empty symphony receives the portfolio's
    get_portfolio_bot_and_held_daily_returns() series."""
    # Portfolio-level data: two symphonies, full daily spans, so the aggregate
    # (and therefore the un-gated fallback) is genuinely non-empty.
    _seed_full_daily_shadow_history("sym_other_a", _ET_DATES_A, daily_pct=0.03)
    _seed_full_daily_shadow_history("sym_other_b", _ET_DATES_A, daily_pct=-0.01)

    portfolio_fallback = analytics.get_portfolio_bot_and_held_daily_returns()
    assert portfolio_fallback is not None, "fixture integrity: portfolio series must be non-empty"
    portfolio_dates = set(portfolio_fallback[0])
    assert portfolio_dates, "fixture integrity: portfolio series must carry dates"

    resp = client.get("/api/performance?scope=symphony&symphony_id=sym_totally_empty&days=90")
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["observation_count"] == 0, (
        f"a symphony with zero triggers and zero shadow_history rows must have "
        f"observation_count==0 (honest empty state), got {body['observation_count']} -- "
        "the unconditional fallback is leaking portfolio data under this symphony's scope"
    )
    assert set(body["dates"]) != portfolio_dates or not body["dates"], (
        "scope=symphony for a data-less symphony returned the PORTFOLIO's own date "
        "set -- the cross-scope fallback (MAPERF-02) is not scope-gated"
    )


def test_ac4_zero_data_symphony_scope_metrics_are_none_not_portfolio_metrics(client, pm_dir):
    """Companion to the dates check: the metrics for a data-less symphony must be
    the honest-empty shape (None), not the portfolio's real Sharpe/CAGR/etc."""
    _seed_full_daily_shadow_history("sym_other_c", _ET_DATES_A, daily_pct=0.04)
    _seed_full_daily_shadow_history("sym_other_d", _ET_DATES_A, daily_pct=0.06)

    portfolio_fallback = analytics.get_portfolio_bot_and_held_daily_returns()
    assert portfolio_fallback is not None, "fixture integrity: portfolio series must be non-empty"

    resp = client.get("/api/performance?scope=symphony&symphony_id=sym_still_empty&days=90")
    assert resp.status_code == 200
    body = resp.get_json()

    live_total_return = body["live_metrics"].get("total_return")
    assert live_total_return is None, (
        f"a data-less symphony's live_metrics.total_return must be None (honest "
        f"empty state); got {live_total_return} -- looks like the portfolio's real "
        "metrics leaked through the un-gated fallback"
    )


# ===========================================================================
# Self-guard -- the AC-3/AC-4 fix must NOT change the aggregate-scope path.
# Derived from a direct call to the SAME analytics function the route uses for
# scope=aggregate, never a hardcoded literal -- valid both before and after the
# fix (the plan's Scope Boundaries forbid touching the aggregate path).
# ===========================================================================


def test_selfguard_aggregate_scope_matches_direct_analytics_call(client, pm_dir):
    """scope=aggregate must return EXACTLY what
    analytics.get_portfolio_bot_and_held_daily_returns(days=N) reports -- pinning
    that AC-3/AC-4's per-symphony fix does not touch the (already-correct)
    aggregate path. This must PASS both before and after the GREEN fix."""
    _seed_full_daily_shadow_history("sym_agg_a", _ET_DATES_A, daily_pct=0.07)
    _seed_full_daily_shadow_history("sym_agg_b", _ET_DATES_A, daily_pct=-0.02)

    days = 90
    direct = analytics.get_portfolio_bot_and_held_daily_returns(days=days)
    assert direct is not None, "fixture integrity: aggregate series must be non-empty"
    expected_dates, expected_bot, expected_held = direct

    resp = client.get(f"/api/performance?scope=aggregate&days={days}")
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["dates"] == list(expected_dates), (
        "scope=aggregate dates no longer match a direct "
        "get_portfolio_bot_and_held_daily_returns() call -- the aggregate path must "
        "stay untouched by the AC-3/AC-4 per-symphony fix"
    )
    assert body["shadow_returns"] == pytest.approx(list(expected_bot)), (
        "scope=aggregate shadow_returns (bot) drifted from the direct analytics call"
    )
    assert body["live_returns"] == pytest.approx(list(expected_held)), (
        "scope=aggregate live_returns (held) drifted from the direct analytics call"
    )
