"""RED — BL-6 (audit #118 M3, docs/audit/TWO-WEEK-REVIEW-2026-08-04.md §4/§6):
analytics.get_symphony_today_change's trading_day-omitted fallback resolves
to a UTC calendar date instead of the ET trading-day convention every other
part of this codebase uses.

THE BUG: analytics.get_symphony_today_change (analytics.py:513-552) falls
back to ``datetime.now(UTC).strftime("%Y-%m-%d")`` (:543) ONLY when a caller
omits ``trading_day``. Both current production call sites (app.py's
_compute_portfolio_strip loop and the frozen/EOD state-render loop) pass an
explicit ET-derived ``trading_day``, so this branch is dead code today — but
a future caller that forgets to pass it would, after ~20:00 ET, query
TOMORROW's UTC date against ET-stamped shadow_history rows and silently get
no match (dry_run=None).

THE FIX (AC-4): the fallback computes the ET calendar date — matching the
write-side convention already used at alpha_bot_execution.py (`current_et.
strftime("%Y-%m-%d")`) and every explicit-trading_day call site — instead of
datetime.now(UTC).

AC-5 (verified at consumer-discovery time, current line numbers — shifted
from the plan's historical app.py:1502/:2676 citation): THREE real
production call sites, all passing an explicit trading_day —
app.py:1274 (_safe_analytics(analytics.get_symphony_today_change, _sym_dict,
_s, trading_day=_dash_today, ...)), app.py:2169
(analytics.get_symphony_today_change(_sym_dict, _sym, trading_day=
_snap_trading_day, ...)), app.py:2675 (analytics.get_symphony_today_change(
sym_dict, s, trading_day=_today_et, ...)). This fix does not change any of
their behavior — it only corrects the DEFENSIVE fallback branch none of them
currently reach.

Edge case (plan): the fallback is dead code on all current production call
sites — AC-6's test below exercises the fallback branch DIRECTLY (calls the
function without trading_day), never relying on discovering a live call site
that omits it.

No freezegun in this repo (verified: not installed). Clock control via the
standard no-freezegun technique: monkeypatch the stdlib ``datetime`` module's
``datetime`` class — analytics.py's fallback does a function-local
``from datetime import datetime as _dt``, a fresh attribute lookup on the
datetime module each call, so patching ``datetime.datetime`` is picked up.
"""

from __future__ import annotations

import datetime as _datetime_module
import os
from datetime import UTC
from zoneinfo import ZoneInfo

import pytest

import analytics
import database

_ET = ZoneInfo("America/New_York")

# 2026-08-21T01:00:00 UTC == 2026-08-20T21:00:00 ET (EDT, UTC-4 in August) —
# the exact UTC-vs-ET date-divergence window: it's still "today" (08-20) in
# ET but already "tomorrow" (08-21) in UTC.
_FROZEN_UTC_INSTANT = _datetime_module.datetime(2026, 8, 21, 1, 0, 0, tzinfo=UTC)
_EXPECTED_ET_DATE = "2026-08-20"
_WRONG_UTC_DATE = "2026-08-21"


class _FrozenDatetime(_datetime_module.datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return _FROZEN_UTC_INSTANT.astimezone(tz)
        return _FROZEN_UTC_INSTANT.replace(tzinfo=None)


@pytest.fixture
def frozen_clock_diverging_utc_et(monkeypatch):
    """Sanity-checks the fixture's own premise (the chosen instant really
    does diverge between UTC and ET) before installing the frozen clock."""
    assert _FROZEN_UTC_INSTANT.astimezone(_ET).strftime("%Y-%m-%d") == _EXPECTED_ET_DATE
    assert _FROZEN_UTC_INSTANT.strftime("%Y-%m-%d") == _WRONG_UTC_DATE
    monkeypatch.setattr(_datetime_module, "datetime", _FrozenDatetime)


class TestFallbackBranchUsesETNotUTC:
    def test_omitted_trading_day_resolves_shadow_row_via_et_date(
        self, _isolate_db, frozen_clock_diverging_utc_et
    ):
        """AC-6: calling get_symphony_today_change with trading_day OMITTED at
        a UTC/ET-diverging clock must resolve the shadow_history lookup by
        the ET date, not tomorrow's UTC date.

        RED today: the fallback computes datetime.now(UTC) -> "2026-08-21",
        finds no shadow_history row for that (wrong) day, and returns
        dry_run=None despite a real row existing for the correct ET day.
        """
        symphony_id = "sym-bl6-fallback"
        db_path = os.environ["DB_PATH"]

        # The CORRECT row — dated at the ET calendar day the fallback should
        # resolve to.
        database.record_shadow_observation(
            symphony_id=symphony_id,
            account_id="acct-1",
            cycle_id="cyc-et",
            ts_utc=f"{_FROZEN_UTC_INSTANT.isoformat()}",
            ts_et="2026-08-20T21:00:00",
            trading_day=_EXPECTED_ET_DATE,
            current_return=17.5,
            shadow_return=17.5,
            is_post_trigger=0,
            trigger_id=None,
        )
        # A DIFFERENT row dated at the WRONG (UTC) day — its presence with a
        # different value proves the fallback isn't accidentally landing on
        # this row by coincidence, and that a real row exists there too (so
        # a None result below can't be blamed on "no row for that day
        # either").
        database.record_shadow_observation(
            symphony_id=symphony_id,
            account_id="acct-1",
            cycle_id="cyc-utc-poison",
            ts_utc=f"{_FROZEN_UTC_INSTANT.isoformat()}",
            ts_et="2026-08-20T21:00:01",
            trading_day=_WRONG_UTC_DATE,
            current_return=-999.0,
            shadow_return=-999.0,
            is_post_trigger=0,
            trigger_id=None,
        )

        sym_dict = {"id": symphony_id, "last_percent_change": 0.0}
        # trading_day intentionally omitted from both the kwarg and sym_dict —
        # exercises the fallback branch directly, per the plan's edge case.
        result = analytics.get_symphony_today_change(
            sym_dict, bot_state_entry=None, trading_day=None, db_path=db_path
        )

        assert result["dry_run"] == pytest.approx(17.5), (
            f"get_symphony_today_change's trading_day-omitted fallback must resolve "
            f"the ET calendar date ({_EXPECTED_ET_DATE}), not tomorrow's UTC date "
            f"({_WRONG_UTC_DATE}) — got dry_run={result['dry_run']!r} (None means it "
            f"queried the wrong day and found no row there; -999.0 would mean it "
            f"queried the WRONG day's row)"
        )


class TestExplicitTradingDaySitesUnaffected:
    """AC-5 regression guard: source-scan confirming the real production call
    sites still pass trading_day explicitly (their behavior is untouched by
    this fix — only the never-reached fallback branch changes). Two call
    shapes exist in app.py: a direct ``analytics.get_symphony_today_change(
    ..., trading_day=...)`` call, and one indirect call where the function is
    passed BY REFERENCE as ``_safe_analytics(analytics.get_symphony_today_
    change, ..., trading_day=...)`` — that site's trading_day kwarg lives on
    the enclosing ``_safe_analytics`` call, not on a call to the target
    function itself (there is no such call node in that case)."""

    def test_production_call_sites_pass_trading_day_explicitly(self):
        import ast
        import pathlib

        src_path = pathlib.Path(__file__).parent.parent.parent / "app.py"
        tree = ast.parse(src_path.read_text(encoding="utf-8"))

        def _is_target_attr(node: ast.expr) -> bool:
            return isinstance(node, ast.Attribute) and node.attr == "get_symphony_today_change"

        # Shape 1: analytics.get_symphony_today_change(..., trading_day=...)
        direct_calls_missing_trading_day = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and _is_target_attr(node.func)
            and "trading_day" not in {kw.arg for kw in node.keywords}
        ]
        direct_call_count = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _is_target_attr(node.func)
        )

        # Shape 2: _safe_analytics(analytics.get_symphony_today_change, ...,
        # trading_day=...) — the target is a bare reference in node.args.
        safe_analytics_calls_missing_trading_day = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_safe_analytics"
            and any(_is_target_attr(arg) for arg in node.args)
            and "trading_day" not in {kw.arg for kw in node.keywords}
        ]
        safe_analytics_call_count = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_safe_analytics"
            and any(_is_target_attr(arg) for arg in node.args)
        )

        assert not direct_calls_missing_trading_day, (
            f"app.py call site(s) at lines {direct_calls_missing_trading_day} call "
            "get_symphony_today_change without an explicit trading_day kwarg — "
            "AC-5 requires all production call sites to keep passing it explicitly"
        )
        assert not safe_analytics_calls_missing_trading_day, (
            f"app.py _safe_analytics(...get_symphony_today_change...) call site(s) at "
            f"lines {safe_analytics_calls_missing_trading_day} omit trading_day"
        )
        # Non-vacuity: the scan must actually have found production call
        # sites to check — an empty scan would pass both asserts above
        # trivially without proving anything (feedback: never write an
        # assertion-free/vacuous test).
        assert direct_call_count + safe_analytics_call_count >= 2, (
            f"expected at least 2 real get_symphony_today_change call sites in app.py "
            f"(direct={direct_call_count}, via _safe_analytics={safe_analytics_call_count}) "
            "— the AST scan found too few to be exercising anything real"
        )
