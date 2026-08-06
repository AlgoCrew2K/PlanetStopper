"""
RED tests — AC-2/AC-3 windowing coherence for GET /api/guard-alpha-summary.

THE BUG: the dashboard $-saved panel (fetchGuardAlphaSummary, static/index.js)
always shows the ALL-TIME cumulative Sigma saved_dollars, while the History
tab's "$ Saved" hero (GET /api/history/<days> -> analytics.get_history_summary)
shows a CALENDAR-DAY-WINDOWED sum of the SAME field from the SAME
post_mortem_*.json files. For a given time range the two surfaces can show
two different numbers with no way to reconcile them.

THE FIX (operator-locked, team-lead ruling): GET /api/guard-alpha-summary
gains an optional `?window=<token>` query param using the SAME token
vocabulary the dashboard's hero-strip picker already emits and
/api/strip/<window> already accepts (app.py's _STRIP_WINDOW_TOKENS =
{"30d","60d","90d","125d","ytd","1y","all"}), resolved via the SAME
analytics._window_cutoff_date the strip route already uses -- never a new
cutoff scheme. Omitting the param preserves today's all-time behavior
(regression guard for existing callers/tests of this route).

AC-2's PROOF: the windowed guard_alpha_summary sum for a given day-count
token must equal analytics.get_history_summary(days=N)["total_saved"] for
the SAME day-count, computed over the SAME shared fixture -- both routes
ultimately sum the identical `saved_dollars` field from the identical
post_mortem_*.json files through the identical is_valid_post_mortem_entry
gate, so this is provable byte-parity, not an approximation.

A COROLLARY BUG this proof depends on (bundled here since it blocks AC-2's
1Y byte-parity): History's own window picker (templates/history.html) maps
its 1Y/5Y buttons to LITERAL CALENDAR-day counts 252/1260 -- an artifact of
252 trading-days/year and 1260 = 252*5 -- while the app-wide `1y` token used
by /api/strip/<window> (and this fix) means 365 calendar days
(analytics._WINDOW_TRAILING_DAYS["1y"] == 365). Today "1Y" already means two
different spans depending which surface you're on. Fix: point History's own
buttons at the app-wide day-counts (1Y -> 365, 5Y -> 1825 = 365*5) instead
of adopting the dashboard side to History's 252/1260 -- then every surface
resolves each shared token to exactly ONE day-count, and the byte-parity
assertion below holds by construction, including at 1Y.

Dates in every fixture are computed relative to date.today() (team-lead /
prior-cycle ruling, see tests/analytics/test_post_mortem_validity_guard_
analytics.py's _recent_date_str) -- never hardcoded historical dates -- so
these tests stay correct regardless of which day the suite runs.

All dollar/count assertions are derived from the fixture dict built in this
file at test time, never hardcoded to a producer-computed value
(feedback_no_hardcoded_test_values).
"""

from __future__ import annotations

import datetime as _datetime_module
import json
import pathlib
import re
from datetime import UTC, date, timedelta

import pytest

import analytics as analytics_module
import app as app_module

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
_HISTORY_HTML = _PROJECT_ROOT / "templates" / "history.html"
_HISTORY_JS = _PROJECT_ROOT / "static" / "history.js"


def _recent_date_str(days_ago: int) -> str:
    """Same pattern as tests/analytics/test_post_mortem_validity_guard_analytics.py
    -- a dynamically-recent date so the fixture stays inside/outside the
    relevant window regardless of which day the suite runs."""
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _write_pm(base_dir: pathlib.Path, date_str: str, saved_dollars: float, symphony: str) -> None:
    payload = {
        "triggers": [
            {
                "symphony_name": symphony,
                "saved_dollars": saved_dollars,
                "saved_pct_guard_alpha": 1.0,
                "exit_reason": "Trailing Stop",
                "if_held_source": "shadow_history",
            }
        ]
    }
    (base_dir / f"post_mortem_{date_str}.json").write_text(json.dumps(payload), encoding="utf-8")


# Three tiers relative to the shared window tokens under test:
#   RECENT (20 days ago)  -- inside every window: 30d/60d/90d/125d/1y/all
#   MID    (200 days ago) -- outside 125d, inside 1y (365) and all
#   FAR    (400 days ago) -- outside 1y (365), inside only "all"
_DAYS_AGO_RECENT = 20
_DAYS_AGO_MID = 200
_DAYS_AGO_FAR = 400

_AMOUNT_RECENT = 111.11
_AMOUNT_MID = 222.22
_AMOUNT_FAR = 333.33


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def three_tier_pm_dir(tmp_path, monkeypatch):
    """Builds the shared fixture described in the module docstring and patches
    analytics._POST_MORTEMS_DIR so both the route and a direct
    get_history_summary() call read the SAME files."""
    pm_dir = tmp_path / "post_mortems"
    pm_dir.mkdir()

    date_recent = _recent_date_str(_DAYS_AGO_RECENT)
    date_mid = _recent_date_str(_DAYS_AGO_MID)
    date_far = _recent_date_str(_DAYS_AGO_FAR)

    _write_pm(pm_dir, date_recent, _AMOUNT_RECENT, "Sym-Recent")
    _write_pm(pm_dir, date_mid, _AMOUNT_MID, "Sym-Mid")
    _write_pm(pm_dir, date_far, _AMOUNT_FAR, "Sym-Far")

    monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(pm_dir))

    return {
        "dir": pm_dir,
        "date_recent": date_recent,
        "date_mid": date_mid,
        "date_far": date_far,
    }


# ---------------------------------------------------------------------------
# AC-2: windowed sum matches the fixture-derived expectation per token
# ---------------------------------------------------------------------------


class TestWindowedSumMatchesFixture:
    def test_30d_window_includes_only_recent(self, client, three_tier_pm_dir):
        resp = client.get("/api/guard-alpha-summary?window=30d")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] == pytest.approx(_AMOUNT_RECENT, abs=0.01), (
            f"window=30d must include only the {_DAYS_AGO_RECENT}-day-old fixture file "
            f"(the {_DAYS_AGO_MID}- and {_DAYS_AGO_FAR}-day-old files are both older than "
            f"a 30-day cutoff); got {data['cumulative_saved_dollars']}"
        )

    def test_1y_window_includes_recent_and_mid_not_far(self, client, three_tier_pm_dir):
        """1y == 365 calendar days (analytics._WINDOW_TRAILING_DAYS["1y"]) --
        the 200-day-old file is inside, the 400-day-old file is outside."""
        resp = client.get("/api/guard-alpha-summary?window=1y")
        assert resp.status_code == 200
        data = resp.get_json()
        expected = _AMOUNT_RECENT + _AMOUNT_MID
        assert data["cumulative_saved_dollars"] == pytest.approx(expected, abs=0.01), (
            f"window=1y must include the 20- and 200-day-old files but exclude the "
            f"400-day-old file (365-day cutoff); got {data['cumulative_saved_dollars']}, "
            f"expected {expected}"
        )

    def test_all_window_includes_every_file(self, client, three_tier_pm_dir):
        resp = client.get("/api/guard-alpha-summary?window=all")
        assert resp.status_code == 200
        data = resp.get_json()
        expected = _AMOUNT_RECENT + _AMOUNT_MID + _AMOUNT_FAR
        assert data["cumulative_saved_dollars"] == pytest.approx(expected, abs=0.01)

    def test_omitted_window_param_preserves_all_time_default(self, client, three_tier_pm_dir):
        """Regression guard: existing callers of this route (Discord aggregation
        context, tests/app/test_guard_alpha_summary_route.py) never pass
        `window` and must keep getting the all-time sum."""
        resp_default = client.get("/api/guard-alpha-summary")
        resp_all = client.get("/api/guard-alpha-summary?window=all")
        assert resp_default.status_code == 200
        assert resp_all.status_code == 200
        assert resp_default.get_json()["cumulative_saved_dollars"] == pytest.approx(
            resp_all.get_json()["cumulative_saved_dollars"], abs=0.01
        ), "omitting `window` must behave identically to window=all"

    def test_unrecognized_window_token_degrades_to_all_not_404(self, client, three_tier_pm_dir):
        """Permissive fallback -- matches analytics._window_cutoff_date's own
        documented behavior ('Unknown tokens resolve to None (lifetime)').
        This is a read-only advisory route; a garbage query string should
        never break the whole panel."""
        resp = client.get("/api/guard-alpha-summary?window=not-a-real-token")
        assert resp.status_code == 200, (
            "an unrecognized window token must NOT 404/500 -- read-only advisory route"
        )
        resp_all = client.get("/api/guard-alpha-summary?window=all")
        assert resp.get_json()["cumulative_saved_dollars"] == pytest.approx(
            resp_all.get_json()["cumulative_saved_dollars"], abs=0.01
        ), "an unrecognized token must degrade to the lifetime/all-time sum"


# ---------------------------------------------------------------------------
# AC-2 proof: byte-parity with History's own windowed aggregate
# ---------------------------------------------------------------------------


class TestByteParityWithHistorySummary:
    """The literal AC-2 proof: for the SAME day-count, the windowed dashboard
    route and analytics.get_history_summary (History's own producer) must
    agree to the cent, computed from ONE shared fixture -- never hardcoded."""

    def test_30d_matches_get_history_summary_days_30(self, client, three_tier_pm_dir):
        route_sum = client.get("/api/guard-alpha-summary?window=30d").get_json()[
            "cumulative_saved_dollars"
        ]
        history_sum = analytics_module.get_history_summary(
            days=30, base_dir=str(three_tier_pm_dir["dir"])
        )["total_saved"]
        assert route_sum == pytest.approx(history_sum, abs=0.01), (
            f"dashboard window=30d ({route_sum}) must equal History's own "
            f"get_history_summary(days=30) ({history_sum}) for the identical fixture -- "
            "same source files, same validity gate, same cutoff arithmetic"
        )

    def test_1y_matches_get_history_summary_days_365(self, client, three_tier_pm_dir):
        """The corollary-bug proof: 1y must resolve to 365 on BOTH sides (not
        252, History's pre-fix day-count for its own "1Y" button) for this
        byte-parity to hold at the 1-year token."""
        route_sum = client.get("/api/guard-alpha-summary?window=1y").get_json()[
            "cumulative_saved_dollars"
        ]
        history_sum = analytics_module.get_history_summary(
            days=365, base_dir=str(three_tier_pm_dir["dir"])
        )["total_saved"]
        assert route_sum == pytest.approx(history_sum, abs=0.01), (
            f"dashboard window=1y ({route_sum}) must equal get_history_summary(days=365) "
            f"({history_sum}) -- both sides must resolve the shared '1Y' token to the "
            "SAME 365-day-count"
        )


# ---------------------------------------------------------------------------
# BL-5 (audit #118 D3): byte-parity extended to the exact boundary day.
#
# The proofs above (test_30d_matches_get_history_summary_days_30 etc.) use
# fixture offsets far from any cutoff boundary (20/200/400 days-ago) — they
# hold both before and after BL-5's fix and don't exercise the specific gap
# BL-5 closes. This class pins a file dated EXACTLY at the N-day cutoff under
# a frozen clock with a non-midnight time-of-day, the one condition where the
# two surfaces' pre-fix arithmetic disagrees. See
# tests/analytics/test_bl5_window_cutoff_unification.py for the full bug
# writeup and the direct analytics-level (non-Flask) reproduction.
# ---------------------------------------------------------------------------


class _FrozenDatetime(_datetime_module.datetime):
    _frozen: _datetime_module.datetime

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return cls._frozen.astimezone(tz)
        return cls._frozen.replace(tzinfo=None)


@pytest.fixture
def frozen_noon_utc(monkeypatch):
    """Freezes datetime.now()/datetime.now(UTC) to the same instant (noon UTC
    on a fixed date) for both the route's UTC-aware _window_cutoff_date call
    and get_history_summary's naive-local `datetime.now()` call — the
    standard no-freezegun technique (this repo has no freezegun dependency):
    both functions do a function-LOCAL `from datetime import datetime as
    _dt`, which is a fresh attribute lookup on the datetime module at call
    time, so patching datetime.datetime itself is picked up by both.
    """
    frozen = _datetime_module.datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    _FrozenDatetime._frozen = frozen
    monkeypatch.setattr(_datetime_module, "datetime", _FrozenDatetime)
    return frozen


@pytest.fixture
def boundary_pm_dir(tmp_path, monkeypatch):
    """A single post_mortem file dated exactly at the 30-day cutoff boundary
    (computed from analytics._window_cutoff_date(30) under the frozen clock
    the frozen_noon_utc fixture installs — this fixture assumes it already
    ran, per pytest's declared-first-wins ordering when both are requested).
    """
    pm_dir = tmp_path / "post_mortems"
    pm_dir.mkdir()

    cutoff = analytics_module._window_cutoff_date(30)
    boundary_date = cutoff.isoformat()

    _write_pm(pm_dir, boundary_date, 55.55, "Sym-Boundary")

    monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(pm_dir))
    return {"dir": pm_dir, "date": boundary_date}


class TestBoundaryDatedFileByteParity:
    def test_30d_boundary_file_included_identically_on_both_surfaces(
        self, client, frozen_noon_utc, boundary_pm_dir
    ):
        """AC-2's boundary proof: a file dated exactly at the 30-day cutoff
        must be included/excluded IDENTICALLY by /api/guard-alpha-summary?
        window=30d and /api/history/30 — closing the exact gap the
        non-boundary byte-parity tests above don't exercise.

        RED today: get_history_summary's naive-local full-datetime compare
        excludes the boundary file at this frozen (non-midnight) clock, while
        /api/guard-alpha-summary already includes it via _window_cutoff_date
        — the two surfaces disagree on the identical file.
        """
        route_resp = client.get("/api/guard-alpha-summary?window=30d")
        assert route_resp.status_code == 200
        route_sum = route_resp.get_json()["cumulative_saved_dollars"]

        history_sum = analytics_module.get_history_summary(
            days=30, base_dir=str(boundary_pm_dir["dir"])
        )["total_saved"]

        assert route_sum == pytest.approx(55.55, abs=0.01), (
            f"fixture integrity: /api/guard-alpha-summary?window=30d must include "
            f"the boundary-dated file ({boundary_pm_dir['date']}); got {route_sum}"
        )
        assert history_sum == pytest.approx(route_sum, abs=0.01), (
            f"/api/history/30 (get_history_summary, {history_sum}) disagrees with "
            f"/api/guard-alpha-summary?window=30d ({route_sum}) on whether the "
            f"boundary-dated file ({boundary_pm_dir['date']}) is in-window — both "
            "surfaces must resolve the SAME cutoff for the SAME day-count"
        )


# ---------------------------------------------------------------------------
# AC-3: response discloses the actual in-window date range
# ---------------------------------------------------------------------------


class TestWindowedDateRangeDisclosure:
    def test_response_echoes_the_resolved_window_token(self, client, three_tier_pm_dir):
        resp = client.get("/api/guard-alpha-summary?window=30d")
        data = resp.get_json()
        assert data.get("window") == "30d", (
            f"response must echo the resolved window token (mirrors /api/strip/<window>'s "
            f"own echo-back pattern) so the UI label can never silently mismatch the "
            f"value; got {data.get('window')!r}"
        )

    def test_default_omitted_window_echoes_all(self, client, three_tier_pm_dir):
        resp = client.get("/api/guard-alpha-summary")
        assert resp.get_json().get("window") == "all"

    def test_windowed_date_range_reflects_only_in_window_files(self, client, three_tier_pm_dir):
        """AC-3: date_range must bracket the WINDOWED subset's actual file
        dates -- not the cutoff bound, and not the all-time earliest/latest.
        With window=30d only the recent-tier file qualifies, so earliest ==
        latest == that file's date."""
        resp = client.get("/api/guard-alpha-summary?window=30d")
        data = resp.get_json()
        dr = data["date_range"]
        assert dr["earliest"] == three_tier_pm_dir["date_recent"], (
            f"window=30d date_range.earliest must be the in-window file's own date "
            f"({three_tier_pm_dir['date_recent']}), not the all-time earliest "
            f"({three_tier_pm_dir['date_far']}); got {dr['earliest']}"
        )
        assert dr["latest"] == three_tier_pm_dir["date_recent"]

    def test_all_window_date_range_spans_full_history(self, client, three_tier_pm_dir):
        resp = client.get("/api/guard-alpha-summary?window=all")
        data = resp.get_json()
        dr = data["date_range"]
        assert dr["earliest"] == three_tier_pm_dir["date_far"]
        assert dr["latest"] == three_tier_pm_dir["date_recent"]


# ---------------------------------------------------------------------------
# Corollary fix: History's own 1Y/5Y buttons must resolve to the app-wide
# day-counts (365/1825), not the legacy 252/1260 trading-day-shaped values.
# ---------------------------------------------------------------------------


class TestHistoryWindowPickerAppWideDayCounts:
    def test_history_html_1y_button_is_365_not_252(self):
        html = _HISTORY_HTML.read_text(encoding="utf-8")
        assert 'data-window="252"' not in html, (
            "History's 1Y button must no longer use the legacy trading-day-shaped "
            "252 -- it must match the app-wide _WINDOW_TRAILING_DAYS['1y']==365 so "
            "the dashboard $-saved panel and History agree at the '1Y' token"
        )
        assert re.search(r'data-window="365"[^>]*>\s*1Y', html) or re.search(
            r"historySelectWindow\(this,\s*'365'\)", html
        ), "History's 1Y button must carry data-window/historySelectWindow value '365'"

    def test_history_html_5y_button_is_1825_not_1260(self):
        html = _HISTORY_HTML.read_text(encoding="utf-8")
        assert 'data-window="1260"' not in html, (
            "History's 5Y button must no longer use the legacy 1260 (252*5) -- it "
            "must use the calendar-day-consistent 1825 (365*5)"
        )
        assert re.search(r'data-window="1825"[^>]*>\s*5Y', html) or re.search(
            r"historySelectWindow\(this,\s*'1825'\)", html
        ), "History's 5Y button must carry data-window/historySelectWindow value '1825'"

    def test_history_js_window_days_default_updated(self):
        """windowDays()'s bare-int fallback (`currentWindow = '90'`) is
        unaffected by this fix, but the function must not hardcode 252/1260
        anywhere as a special-cased ytd-adjacent branch (it doesn't today --
        this guards against a partial fix that only touches the HTML)."""
        js = _HISTORY_JS.read_text(encoding="utf-8")
        assert "252" not in js, (
            "static/history.js must not carry a residual 252 special-case once the "
            "1Y button itself is updated to 365"
        )
        assert "1260" not in js, (
            "static/history.js must not carry a residual 1260 special-case once the "
            "5Y button itself is updated to 1825"
        )


# ---------------------------------------------------------------------------
# Sufficiency-review finding (gas-review, Revise phase): the windowed-empty
# fallback dishonestly diverts to the day-1/no-post-mortems-ever branch.
# ---------------------------------------------------------------------------


@pytest.fixture
def out_of_window_pm_dir(tmp_path, monkeypatch):
    """One post-mortem file dated 60 days ago (outside a 30d window) with a
    valid $500 trigger -- proves post-mortem HISTORY EXISTS, just not inside
    the requested window."""
    pm_dir = tmp_path / "post_mortems"
    pm_dir.mkdir()
    date_60d_ago = _recent_date_str(60)
    _write_pm(pm_dir, date_60d_ago, 500.0, "Sym-OutOfWindow")
    monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(pm_dir))
    return {"dir": pm_dir, "date": date_60d_ago}


class TestWindowedEmptyResultStaysHonest:
    """THE BUG (gas-review, empirically proved): guard_alpha_summary's
    `if dates: / else:` fallback gates on the WINDOW-FILTERED `dates` list
    (post-AC-2), not on "do any post-mortem files exist at all". When files
    exist but NONE fall inside the requested window (e.g. a 60-day-old $500
    save, ?window=30d), every file hits the AC-2 cutoff `continue` BEFORE its
    date is appended to `dates` -- so `dates` ends up empty and the route
    wrongly takes the day-1/no-post-mortems-yet branch: source flips to
    "exit_triggers_intraday" and (with zero of today's exit_triggers rows,
    the common case) basis_label becomes "no guard events yet" -- dishonestly
    implying NO guard event has EVER happened, when in fact one just happened
    outside this window. analytics.get_history_summary(days=30) on the
    IDENTICAL fixture honestly returns total_saved=0.0 with no such false
    claim -- this is the cycle's own "same range same number" + honesty
    purpose, and the THIRD-STATE edge case (files-exist-but-none-in-window,
    distinct from both "day-1, zero files anywhere" and "files exist, some
    in-window") that the approved plan specified but GREEN missed.
    """

    def test_source_is_not_the_intraday_fallback(self, client, out_of_window_pm_dir):
        resp = client.get("/api/guard-alpha-summary?window=30d")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["source"] != "exit_triggers_intraday", (
            f"post-mortem files EXIST (just outside the 30d window) -- the route "
            f"must not divert to the day-1 exit_triggers_intraday fallback, which "
            f"is reserved for 'no post-mortem history exists at all'. Got source="
            f"{data['source']!r}"
        )

    def test_basis_label_does_not_claim_no_guard_events_ever(self, client, out_of_window_pm_dir):
        resp = client.get("/api/guard-alpha-summary?window=30d")
        data = resp.get_json()
        assert data["basis_label"] != "no guard events yet", (
            f"a real guard event exists (60 days ago) -- the basis_label must "
            f"never claim 'no guard events yet', which dishonestly implies none "
            f"has ever happened. Got basis_label={data['basis_label']!r}"
        )

    def test_windowed_empty_result_is_an_honest_zero_from_post_mortem_source(
        self, client, out_of_window_pm_dir
    ):
        resp = client.get("/api/guard-alpha-summary?window=30d")
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] == pytest.approx(0.0, abs=1e-9), (
            f"nothing falls inside the 30d window -- the honest sum is 0.0 (matching "
            f"analytics.get_history_summary(days=30) on the identical fixture), not "
            f"a fabricated non-zero and not a diversion to a different data source; "
            f"got {data['cumulative_saved_dollars']}"
        )
        assert data["source"] == "post_mortem_eod", (
            f"the post-mortem-file source produced the (honestly empty) window-scoped "
            f"result -- source must say so, not silently swap to a different "
            f"data-source label; got {data['source']!r}"
        )
        assert data["basis_label"] and isinstance(data["basis_label"], str), (
            "an honest window-scoped basis_label must still be a non-empty string "
            "(never blank just because the window is empty)"
        )

    def test_windowed_empty_matches_history_summary_honest_zero(self, client, out_of_window_pm_dir):
        """The literal byte-parity proof extended to the zero case: History's
        own producer, on the IDENTICAL fixture, honestly returns 0.0 -- the
        windowed route must match, not divert to a different code path
        that produces a different (dishonest) answer."""
        route_sum = client.get("/api/guard-alpha-summary?window=30d").get_json()[
            "cumulative_saved_dollars"
        ]
        history_sum = analytics_module.get_history_summary(
            days=30, base_dir=str(out_of_window_pm_dir["dir"])
        )["total_saved"]
        assert route_sum == pytest.approx(history_sum, abs=1e-9) == 0.0, (
            f"windowed route ({route_sum}) must match History's honest zero "
            f"({history_sum}) for the identical out-of-window fixture"
        )
