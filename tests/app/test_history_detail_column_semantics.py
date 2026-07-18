"""RED tests -- Math Remediation R0, AC-6 (feature-plans/math-r0.md).

DE-MATH-AUDIT-001 / VERDICT.md ma-perf-findings.md MAPERF-06 (MEDIUM -- the
operator's "TP saved me 10%" sighting):

  The History "Detail" column carries TWO DIFFERENT semantics depending on
  which code path served it, rendered identically as a signed percent by
  static/history.js (no per-source distinction):
    - post-mortem path (analytics.py:1799): `detail = saved_pct_guard_alpha`
      -- guard-alpha percentage POINTS (how much the guard SAVED vs holding).
    - intraday backfill path (app.py:3067, `"detail": r[2]`): `r[2] = at_return`
      -- the raw EXIT-LEVEL return (how much the position was up/down AT the
      moment of exit) -- a completely different number under the same label.
  Before the 15:54 ET post-mortem write (i.e. all trading-day morning), the
  intraday path is what's live -- so "+3.20%" in the Detail column silently
  means "position was up 3.2% at exit" until 15:54, then flips to meaning
  "guard saved 3.2pp" once the post-mortem file lands. Same column, same
  format, two different quantities, no label change.

  AC-6: the History Detail column has ONE semantic -- saved-alpha (guard-alpha
  percentage points), both intraday and post-15:54. The intraday backfill path
  must emit `at_return - current_return` (the same-day guard-alpha-pp shape
  the post-mortem path already emits), not the raw at_return -- mirroring the
  existing shadow-subquery pattern already used elsewhere in this route
  (app.py:2619-2620 / the strip fallback).
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import analytics
import app as app_module
import database

# Same ET-day derivation the /api/history route under test uses (app.py:3099:
# `_today_et = datetime.now(_ET).date().isoformat()`). The bare
# datetime.date.today() built-in returns the machine's LOCAL calendar date,
# which drifts a full day ahead of the ET trading day on CI (UTC clock)
# between 00:00-04:00 UTC, silently seeding rows the day-filtered route can
# never find. NEVER the bare date.today() built-in in this file.
_ET = ZoneInfo("America/New_York")


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


@pytest.fixture
def pm_dir(tmp_path, monkeypatch):
    """Empty post-mortems dir -- no file for today, forcing the intraday
    backfill branch (the route's own guard: `if not stats.get("todays_exits")`)."""
    pm = tmp_path / "post_mortems"
    pm.mkdir()
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(pm))
    return pm


def test_ac6_intraday_detail_is_guard_alpha_not_raw_exit_level_return(client, pm_dir):
    """The intraday backfill's Detail value must be `at_return - current_return`
    (same-day guard-alpha pp -- the SAME semantic the post-mortem path already
    emits via `saved_pct_guard_alpha`), not the raw at_return.

    Fixture: at_return (exit-level return, what the position was up when the
    guard exited) and current_return (today's if-held return, sourced from the
    LATEST shadow_history row for the same symphony) are deliberately
    DIFFERENT, so the two candidate semantics are numerically distinguishable.

    MUST FAIL at 98901abf: app.py:3067 emits `"detail": r[2]` where r[2] is
    the raw exit_triggers.at_return column, unchanged."""
    symphony_id = "sym_intraday_detail"
    today = datetime.now(_ET).date().isoformat()
    at_return = 3.2
    current_return = 0.9  # deliberately different from at_return

    database.save_state({symphony_id: {"name": "Intraday Detail Sym"}})
    database.record_shadow_observation(
        symphony_id=symphony_id,
        account_id="acct-1",
        cycle_id="cyc-1",
        ts_utc=f"{today}T19:55:00Z",
        ts_et=f"{today}T15:55:00",
        trading_day=today,
        current_return=current_return,
        shadow_return=current_return,
        is_post_trigger=0,
        trigger_id=None,
    )
    database.record_exit_trigger(
        symphony_id=symphony_id,
        account_id="acct-1",
        triggered_reason="trailing_stop",
        at_return=at_return,
        cycle_id="cyc-2",
        ts_utc=f"{today}T15:54:00Z",
        ts_et=f"{today}T11:54:00",
    )

    resp = client.get("/api/history/3650")
    assert resp.status_code == 200
    exits = resp.get_json()["todays_exits"]
    assert exits, "fixture integrity: expected exactly one intraday backfilled exit row"

    row = next((e for e in exits if e.get("symphony_id") == symphony_id), None)
    assert row is not None, f"fixture integrity: no row for {symphony_id} in {exits}"

    expected_guard_alpha_pp = at_return - current_return
    assert row["detail"] != pytest.approx(at_return), (
        f"detail={row['detail']} still equals the raw at_return ({at_return}) -- the "
        "intraday path is still emitting exit-level return, not guard-alpha pp"
    )
    assert row["detail"] == pytest.approx(expected_guard_alpha_pp), (
        f"detail={row['detail']} does not equal at_return-current_return "
        f"({expected_guard_alpha_pp} = {at_return} - {current_return}) -- AC-6 requires "
        "the intraday path to emit the SAME guard-alpha-pp semantic as the post-mortem "
        "path (analytics.py:1799 saved_pct_guard_alpha), not the raw exit-level return"
    )


def test_ac6_intraday_detail_semantic_matches_postmortem_semantic_shape(client, pm_dir):
    """Cross-path consistency: the post-mortem path's `saved_pct_guard_alpha`
    field IS `at_return - current_return`-shaped (guard-alpha pp) by
    construction (reporting.py's Stage-1 math). This test pins that the
    INTRADAY path's detail value, for the identical (at_return,
    current_return) pair, produces the SAME number a post-mortem file would
    have recorded for that trigger -- proving one semantic across both
    surfaces, not just "not the raw value" in isolation.

    MUST FAIL at 98901abf for the same reason as the test above."""
    symphony_id = "sym_cross_path_consistency"
    today = datetime.now(_ET).date().isoformat()
    at_return = -1.8
    current_return = -4.5

    database.save_state({symphony_id: {"name": "Cross Path Sym"}})
    database.record_shadow_observation(
        symphony_id=symphony_id,
        account_id="acct-1",
        cycle_id="cyc-1",
        ts_utc=f"{today}T19:55:00Z",
        ts_et=f"{today}T15:55:00",
        trading_day=today,
        current_return=current_return,
        shadow_return=current_return,
        is_post_trigger=0,
        trigger_id=None,
    )
    database.record_exit_trigger(
        symphony_id=symphony_id,
        account_id="acct-1",
        triggered_reason="vwap_bleed",
        at_return=at_return,
        cycle_id="cyc-2",
        ts_utc=f"{today}T14:10:00Z",
        ts_et=f"{today}T10:10:00",
    )

    resp = client.get("/api/history/3650")
    assert resp.status_code == 200
    exits = resp.get_json()["todays_exits"]
    row = next((e for e in exits if e.get("symphony_id") == symphony_id), None)
    assert row is not None, f"fixture integrity: no row for {symphony_id} in {exits}"

    # The post-mortem-path semantic, reproduced exactly (reporting.py's
    # saved_pct_guard_alpha == at_return - current_return, the same-day
    # guard-alpha pp -- see reporting.py:91-94's saved math).
    postmortem_equivalent_semantic = at_return - current_return
    assert row["detail"] == pytest.approx(postmortem_equivalent_semantic), (
        f"intraday detail={row['detail']} does not match the guard-alpha-pp value "
        f"a post-mortem file would have recorded for this same trigger "
        f"({postmortem_equivalent_semantic}) -- the Detail column must carry ONE "
        "semantic across both the intraday and post-mortem paths (MAPERF-06)"
    )
