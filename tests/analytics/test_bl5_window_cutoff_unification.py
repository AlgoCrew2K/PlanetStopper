"""RED — BL-5 (audit #118 D3, docs/audit/TWO-WEEK-REVIEW-2026-08-04.md §4/§6):
window-cutoff asymmetry between analytics.get_history_summary and
analytics._window_cutoff_date.

THE BUG:
  analytics._window_cutoff_date (analytics.py:1720-1744) resolves a window
  token to a UTC-DATE cutoff (`_dt.now(UTC).date()`), and callers include a
  file at date_str >= cutoff (INCLUSIVE of the boundary day — see app.py:3377
  `date_str < _cutoff_iso` to exclude).

  analytics.get_history_summary (analytics.py:1987+) instead derives its own
  window with NAIVE LOCAL `_dt.now()` (no UTC) and compares FULL DATETIMES
  (`start_date <= file_date <= end_date`, both carrying a time-of-day
  component) rather than pure dates. Because `end_date` carries "now"'s
  time-of-day but `file_date` is always parsed at midnight
  (`datetime.strptime(date_part, "%Y-%m-%d")`), a file dated EXACTLY at the
  N-days-ago boundary can be excluded under the old arithmetic whenever "now"
  is any time after midnight — while `_window_cutoff_date`-based callers
  (``/api/guard-alpha-summary``, ``/api/strip/<window>``) always include it.

THE FIX (AC-1): get_history_summary re-routes its window boundary through
  analytics._window_cutoff_date — one cutoff function, one timezone
  convention, shared by both surfaces.

AC-2's byte-parity proof at the exact boundary lives in
tests/app/test_guard_alpha_summary_windowed.py (extends
TestByteParityWithHistorySummary, the existing DE-GAS-COHERENCE-001 pattern,
to the boundary case specifically) — this file covers AC-1 (the structural
routing check) plus a direct analytics-level boundary reproduction and the
zero-in-window-files edge case, both faster/simpler without going through the
Flask app.

Consumer-discovery (house lesson, feedback_consumer_suite_discovery_before_
sufficiency): grepped every existing test referencing get_history_summary
(tests/analytics/test_history_daily_drilldown.py,
tests/analytics/test_post_mortem_validity_guard_analytics.py,
tests/app/test_guard_alpha_summary_windowed.py,
tests/app/test_history_today_fallback_date_filter.py,
tests/app/test_history_name_map_f821.py, tests/ui/test_cycle_2_fix_live_data.py,
tests/app/test_live_dashboard_metrics.py, tests/app/test_dashboard_no_order_path.py)
— every fixture date offset used is far from any window boundary (1/2/3/20/
200/400 days-ago, or a 3650-day window so wide no boundary can matter). None
encode the pre-fix boundary behavior as expected; no reconciliation required.

No freezegun in this repo (verified: not installed, no test uses it). Clock
control uses the standard no-freezegun technique: monkeypatch the STDLIB
``datetime`` module's ``datetime`` class itself. Both
``_window_cutoff_date`` and ``get_history_summary`` do a function-LOCAL
``from datetime import datetime as _dt`` — that import is a fresh attribute
lookup on the already-imported ``datetime`` module each call, so patching
``datetime.datetime`` (the real stdlib module attribute) is picked up by
every subsequent local import, without freezegun.
"""

from __future__ import annotations

import datetime as _datetime_module
import json
from datetime import UTC

import pytest

import analytics

# A fixed instant with a NON-midnight time-of-day component (noon UTC) — the
# exact condition that exposes the pre-fix bug: get_history_summary's old
# `end_date - timedelta(days=N)` carries this time-of-day, so a file parsed
# at midnight on the boundary date falls (start_date > file_date) and is
# wrongly excluded, while _window_cutoff_date's pure-date arithmetic includes
# it.
_FROZEN_UTC = _datetime_module.datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


class _FrozenDatetime(_datetime_module.datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return _FROZEN_UTC.astimezone(tz)
        return _FROZEN_UTC.replace(tzinfo=None)


@pytest.fixture
def frozen_clock(monkeypatch):
    """Freezes datetime.now()/datetime.now(UTC) to the SAME instant
    (_FROZEN_UTC) for both the naive-local and UTC-aware call shapes used by
    get_history_summary and _window_cutoff_date respectively."""
    monkeypatch.setattr(_datetime_module, "datetime", _FrozenDatetime)
    return _FROZEN_UTC


def _write_pm(base_dir, date_str: str, saved_dollars: float = 10.0) -> None:
    payload = {
        "triggers": [
            {
                "if_held_source": "shadow_history",
                "symphony_id": "sym-bl5",
                "exit_reason": "Trailing Stop",
                "saved_pct_guard_alpha": 1.0,
                "saved_dollars": saved_dollars,
            }
        ]
    }
    (base_dir / f"post_mortem_{date_str}.json").write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# AC-1: get_history_summary derives its cutoff via the shared helper
# ---------------------------------------------------------------------------


class TestGetHistorySummaryRoutesThroughSharedCutoff:
    def test_calls_window_cutoff_date_with_the_day_count(self, tmp_path, monkeypatch):
        """Structural proof (not a fixed-line grep, to dodge the OC-ordering
        rfind fragility class from the BL-2/BL-4 sweep lesson): a spy on the
        REAL analytics._window_cutoff_date confirms get_history_summary
        actually calls it with the `days` int, rather than merely existing
        somewhere nearby in the module. Passes trivially post-fix; fails RED
        today because get_history_summary never calls this function at all.
        """
        calls: list[object] = []
        real_cutoff = analytics._window_cutoff_date

        def _spy(window):
            calls.append(window)
            return real_cutoff(window)

        monkeypatch.setattr(analytics, "_window_cutoff_date", _spy)

        analytics.get_history_summary(days=30, base_dir=str(tmp_path))

        assert 30 in calls, (
            f"get_history_summary(days=30) must resolve its window boundary via "
            f"analytics._window_cutoff_date(30) — the same helper "
            f"/api/guard-alpha-summary and /api/strip/<window> already use; "
            f"spy recorded calls={calls}"
        )


# ---------------------------------------------------------------------------
# Direct analytics-level boundary reproduction (a faster sibling of the
# cross-route Flask proof in test_guard_alpha_summary_windowed.py)
# ---------------------------------------------------------------------------


class TestBoundaryDatedFileMatchesWindowCutoffDate:
    def test_file_at_the_cutoff_day_is_included_same_as_window_cutoff_date(
        self, tmp_path, frozen_clock
    ):
        """At a frozen clock with a non-midnight time-of-day (noon UTC), a
        post-mortem file dated EXACTLY at the 30-day cutoff boundary
        (2026-08-20 - 30d = 2026-07-21) must be INCLUDED by get_history_summary
        — matching _window_cutoff_date's own inclusive-of-boundary convention
        (app.py:3377's `date_str < _cutoff_iso` excludes only STRICTLY older
        dates).

        RED today: get_history_summary's old `start_date <= file_date <=
        end_date` full-datetime compare puts start_date at 2026-07-21 12:00:00
        (carrying frozen_clock's noon time-of-day) against file_date parsed at
        2026-07-21 00:00:00 (midnight) — start_date > file_date, so the file is
        wrongly EXCLUDED (total_saved stays 0.0) despite being exactly on the
        30-day boundary that _window_cutoff_date(30) itself includes.
        """
        cutoff = analytics._window_cutoff_date(30)
        assert cutoff is not None, "fixture integrity: _window_cutoff_date(30) must resolve"
        boundary_date_str = cutoff.isoformat()

        _write_pm(tmp_path, boundary_date_str, saved_dollars=42.42)

        result = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

        assert result["total_saved"] == pytest.approx(42.42, abs=0.01), (
            f"a post-mortem file dated exactly at the 30-day cutoff boundary "
            f"({boundary_date_str}) must be included in get_history_summary's "
            f"total — got total_saved={result['total_saved']}, the boundary file "
            "was wrongly excluded"
        )
        assert result["trigger_count"] == 1

    def test_file_one_day_older_than_the_cutoff_is_excluded_on_both(self, tmp_path, frozen_clock):
        """Regression guard for the fix's other direction: a file dated ONE
        DAY OLDER than the cutoff must stay excluded post-fix too (AC-3 — this
        is a boundary-condition fix, not a widening of the window)."""
        cutoff = analytics._window_cutoff_date(30)
        too_old = (cutoff - _datetime_module.timedelta(days=1)).isoformat()

        _write_pm(tmp_path, too_old, saved_dollars=999.0)

        result = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

        assert result["total_saved"] == pytest.approx(0.0, abs=1e-9), (
            f"a file one day older than the 30-day cutoff ({too_old}) must stay "
            f"excluded; got total_saved={result['total_saved']}"
        )
        assert result["trigger_count"] == 0


class TestZeroInWindowFilesHonestEmptyState:
    def test_all_files_outside_window_yields_honest_empty_total(self, tmp_path, frozen_clock):
        """Edge case (plan): a window with ZERO in-window files must produce
        the same honest empty state get_history_summary already returns for a
        directory with no files at all — no crash, no fabricated total."""
        cutoff = analytics._window_cutoff_date(30)
        far_outside = (cutoff - _datetime_module.timedelta(days=100)).isoformat()
        _write_pm(tmp_path, far_outside, saved_dollars=7.0)

        result = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

        assert result["total_saved"] == pytest.approx(0.0, abs=1e-9)
        assert result["trigger_count"] == 0
