"""
RED tests — feature-plans/mdd-window-truth.md route/render ACs (AC-1 render,
AC-2 render, AC-4, AC-5). DE-PERF-WINDOW-TRUTH-001.

Covers:
  AC-1 (render): app._compute_portfolio_strip's warm-cache branch must stop
    special-casing `_account_totals_cache["portfolio_mdd"]` (Composer's
    lifetime scalar) as the if_held comparison leg -- if_held must come from
    the (now window-genuine) analytics.get_portfolio_max_drawdown call
    regardless of cache warmth. Mirrors the established
    test_mdd_honest_framing.py pattern: mock app_module.analytics wholesale,
    hit the real "/" route, assert on rendered HTML.
  AC-2 (render): the Composer lifetime scalar remains available as its OWN
    clearly-labelled figure, naming its invested_since start where available
    -- never silently absorbed as a comparison leg.
  AC-4 (rendered disclosure, not a payload key): GET /performance's rendered
    page must have a render target the JS-driven coverage disclosure writes
    into that is NOT the pre-existing 30-observation stability banner (kept
    separate per the plan's Decision table).
  AC-5: GET /api/performance and GET /api/history/<days> gain honest
    actual_days/coverage_days/date_range fields; window_days keeps its
    "requested" meaning (not redefined) per the plan's exact wording.

-n0 only; no live API; database/analytics mocked or DB_FILE-overridden per the
established seams (analytics.DB_FILE, patch.object(app_module, "analytics")).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

_SHADOW_SCHEMA = """
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


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def clear_account_totals_cache():
    app_module._account_totals_cache.clear()
    yield
    app_module._account_totals_cache.clear()


@pytest.fixture
def mock_database():
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {}
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
        db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
        db_mock.read_fleet_alert.return_value = None
        db_mock.get_triggers.return_value = []
        db_mock.get_guard_alpha_by_symphony.return_value = {}
        yield db_mock


def _seed_shadow_db(path: Path, symphony_id: str, rows: list[dict]) -> str:
    db_file = str(path)
    conn = sqlite3.connect(db_file)
    conn.execute(_SHADOW_SCHEMA)
    for row in rows:
        trading_day = row.get("trading_day") or (
            date.today() - timedelta(days=row["days_ago"])
        ).isoformat()
        conn.execute(
            "INSERT INTO shadow_history (symphony_id, ts_utc, trading_day, current_return, "
            "shadow_return, is_post_trigger, position_epoch) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                symphony_id,
                trading_day + "T20:00:00Z",
                trading_day,
                row["current_return"],
                row["shadow_return"],
                row.get("is_post_trigger", 0),
                row.get("position_epoch", "EPOCH_A"),
            ),
        )
    conn.commit()
    conn.close()
    return db_file


def _analytics_mock_sufficient_history(
    *, mdd_if_held: float, mdd_dry_run: float, mdd_if_held_lifetime: float | None = None
) -> MagicMock:
    """A >=30-day shadow history (so the AC-4c sufficiency gate does NOT
    suppress the MDD row -- this file is testing AC-1/AC-2 render content,
    which the sufficiency gate would otherwise hide).

    Return-dict shape matches the CONFIRMED contract redesign
    (docs/audit/MDD-CONSUMER-ENUMERATION-2026-09-03.md): if_held/dry_run are
    the redefined comparable pair; if_held_lifetime is the Composer scalar
    under its own key (AC-2); n_obs is always an int.
    """
    dates = [f"2026-05-{d:02d}" for d in range(1, 31)]  # 30 days
    m = MagicMock()
    m.get_portfolio_today_change.return_value = {"if_held": 0.5, "dry_run": 0.4}
    m.get_portfolio_cumulative_return.return_value = {"if_held": 10.0, "dry_run": 9.5}
    m.get_portfolio_max_drawdown.return_value = {
        "if_held": mdd_if_held,
        "dry_run": mdd_dry_run,
        "if_held_lifetime": mdd_if_held_lifetime,
        "n_obs": 30,
    }
    m.get_symphony_today_change.return_value = {"if_held": 1.2, "dry_run": 0.9}
    m.get_symphony_cumulative_return.return_value = {"if_held": 12.0, "dry_run": 12.0}
    m.get_symphony_max_drawdown.return_value = {
        "if_held": mdd_if_held,
        "dry_run": mdd_dry_run,
        "if_held_lifetime": mdd_if_held_lifetime,
        "n_obs": 30,
    }
    m.get_portfolio_daily_returns_from_shadow.return_value = (dates, [0.01] * 30)
    m.get_portfolio_bot_and_held_daily_returns.return_value = None
    m.compute_portfolio_annualized_vol.return_value = 0.1
    m.get_history_with_cache_invalidation.return_value = {}
    m.compute_aggregate_returns.return_value = (dates, [0.01] * 30, [0.01] * 30)
    m._POST_MORTEMS_DIR = "/tmp/no-such-dir"
    return m


def _minimal_bot_state() -> dict:
    return {
        "sym-x": {
            "name": "Symphony X",
            "account": "ACC1",
            "armed": True,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,
            "current_return": 1.5,
            "current_value": 10000.0,
            "stop_trigger": -2.0,
            "mc_prob": 40.0,
            "simple_return": 0.12,
            "net_deposits": 1000.0,
            "time_weighted_return": 0.12,
            "max_drawdown": 0.9999,  # deliberately far from mdd_if_held/mdd_dry_run below
        }
    }


# ===========================================================================
# AC-1 (render) — the warm-cache lifetime-scalar override must be gone
# ===========================================================================


class TestAC1WarmCacheNoLongerOverridesIfHeld:
    def test_warm_cache_scalar_does_not_win_over_analytics_windowed_value(
        self, client, mock_database, monkeypatch
    ):
        """AC-1: today, when _account_totals_cache['portfolio_mdd'] is warm,
        app.py sets if_held = abs(cached scalar) UNCONDITIONALLY, ignoring
        analytics.get_portfolio_max_drawdown's if_held entirely. Post-fix,
        if_held must be the analytics (window-genuine) value regardless of
        cache warmth -- the whole POINT of AC-1 is that a lifetime Composer
        scalar must never be the comparison leg."""
        mock_database.load_state.return_value = _minimal_bot_state()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})

        analytics_mock = _analytics_mock_sufficient_history(
            mdd_if_held=10.5875, mdd_dry_run=10.3622
        )
        monkeypatch.setattr(app_module, "analytics", analytics_mock)

        # Warm cache: a DIFFERENT value than the analytics mock's if_held, so
        # the two are trivially distinguishable in the rendered output.
        stale_composer_scalar_pct = -173.2  # abs() would render 173.20 -- far from 10.59
        app_module._account_totals_cache["portfolio_mdd"] = stale_composer_scalar_pct
        app_module._account_totals_cache["portfolio_value"] = 20000.0

        resp = client.get("/")
        assert resp.status_code == 200, f"dashboard render failed: {resp.status_code}"
        html = resp.get_data(as_text=True)

        anchor = html.find('data-testid="comp-mdd-held-text"')
        assert anchor != -1, "rendered page must contain the Max DD held text span"
        snippet = html[anchor : anchor + 200]

        assert "173.20" not in snippet and "173.2" not in snippet, (
            f"AC-1 FAIL: the rendered Max DD Held figure still reflects the warm-"
            f"cache Composer lifetime scalar (abs({stale_composer_scalar_pct}) = "
            f"173.20) instead of the genuine windowed if_held (10.59) the "
            f"analytics mock supplied. Snippet: {snippet!r}. The app.py warm-"
            f"cache branch (`if _cached_mdd is not None: max_drawdown = "
            f"{{'if_held': abs(_cached_mdd), ...}}`) must be removed -- if_held "
            f"comes from analytics.get_portfolio_max_drawdown unconditionally."
        )
        assert "10.59" in snippet or "10.58" in snippet or "10.60" in snippet, (
            f"AC-1 FAIL: expected the genuine windowed if_held (~10.5875, "
            f"rendered to 2dp as 10.59/10.58/10.60 depending on rounding) in "
            f"the Held text; got snippet: {snippet!r}"
        )

    def test_cold_cache_still_renders_analytics_value(self, client, mock_database, monkeypatch):
        """Anti-regression: the cold-cache path (no special-casing today)
        must continue to render the analytics value after the warm-cache
        branch is removed."""
        mock_database.load_state.return_value = _minimal_bot_state()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        analytics_mock = _analytics_mock_sufficient_history(
            mdd_if_held=10.5875, mdd_dry_run=10.3622
        )
        monkeypatch.setattr(app_module, "analytics", analytics_mock)
        # cache left empty by the autouse fixture -> cold path

        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        anchor = html.find('data-testid="comp-mdd-held-text"')
        assert anchor != -1
        snippet = html[anchor : anchor + 200]
        assert "10.59" in snippet or "10.58" in snippet or "10.60" in snippet


# ===========================================================================
# AC-2 (render) — Composer lifetime scalar rendered as its OWN figure
# ===========================================================================


class TestAC2LifetimeScalarRenderedSeparately:
    """[Updated, DE-PERF-WINDOW-TRUTH-001, AC-2 AMENDED per team-lead ruling
    2026-09-03]: the separate lifetime figure is labelled GENERICALLY --
    "Lifetime Max Drawdown · since inception" -- NOT with the actual
    invested_since date (not persisted anywhere reachable without violating
    AC-7's alpha_bot_execution.py freeze; see feature-plans/mdd-window-
    truth.md's amended AC-2 + Decisions table)."""

    _RULED_LABEL_FRAGMENTS = ("Lifetime Max Drawdown", "since inception")

    def test_page_renders_a_lifetime_figure_distinct_from_the_vs_row(
        self, client, mock_database, monkeypatch
    ):
        mock_database.load_state.return_value = _minimal_bot_state()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        analytics_mock = _analytics_mock_sufficient_history(
            mdd_if_held=10.5875, mdd_dry_run=10.3622, mdd_if_held_lifetime=99.99
        )
        monkeypatch.setattr(app_module, "analytics", analytics_mock)

        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert 'data-testid="mdd-lifetime-scalar"' in html, (
            "AC-2 FAIL: the page must render a SEPARATE, clearly-labelled "
            "Composer lifetime max_drawdown figure (data-testid="
            "'mdd-lifetime-scalar') -- distinct from the Bot-vs-Held vs-row "
            "comparison legs, which no longer read this field. Not found in "
            "rendered HTML."
        )
        # It must not live INSIDE the vs-row comparison block (which would
        # re-introduce it as a de-facto third comparison leg).
        vs_row_anchor = html.find('data-testid="comp-mdd-bot-text"')
        lifetime_anchor = html.find('data-testid="mdd-lifetime-scalar"')
        next_vs_row = html.find('data-testid="vs-row"', vs_row_anchor + 1)
        mdd_vs_row_end = next_vs_row if next_vs_row != -1 else vs_row_anchor + 1500
        assert not (vs_row_anchor <= lifetime_anchor < mdd_vs_row_end), (
            "AC-2 FAIL: the lifetime-scalar figure must NOT be rendered inside "
            "the Max-DD vs-row comparison block -- it is a separate, clearly-"
            "labelled figure, not a third comparison leg."
        )

    def test_lifetime_figure_uses_the_ruled_generic_label_not_a_date(
        self, client, mock_database, monkeypatch
    ):
        """AC-2's team-lead-amended label text: 'Lifetime Max Drawdown ·
        since inception', with NO invested_since date rendered this cycle
        (per feature-plans/mdd-window-truth.md's Decisions table -- fetching
        invested_since would need either a new Composer call, out of scope,
        or touching alpha_bot_execution.py, forbidden by AC-7)."""
        mock_database.load_state.return_value = _minimal_bot_state()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        analytics_mock = _analytics_mock_sufficient_history(
            mdd_if_held=10.5875, mdd_dry_run=10.3622, mdd_if_held_lifetime=99.99
        )
        monkeypatch.setattr(app_module, "analytics", analytics_mock)

        resp = client.get("/")
        html = resp.get_data(as_text=True)
        anchor = html.find('data-testid="mdd-lifetime-scalar"')
        assert anchor != -1
        snippet = html[max(0, anchor - 250) : anchor + 250]
        for fragment in self._RULED_LABEL_FRAGMENTS:
            assert fragment in snippet, (
                f"AC-2 FAIL: the ruled label fragment {fragment!r} was not "
                f"found near the lifetime-scalar figure. Ruled copy: "
                f"'Lifetime Max Drawdown · since inception'. Snippet: {snippet!r}"
            )
        # invested_since is explicitly NOT persisted/rendered this cycle.
        assert "invested_since" not in html.lower().replace(" ", "_"), (
            "AC-2 explicitly rules OUT rendering the real invested_since date "
            "this cycle (not persisted anywhere reachable without violating "
            "AC-7) -- a literal 'invested_since' reference suggests an "
            "unapproved scope expansion, not the ruled generic label."
        )


# ===========================================================================
# AC-5 — /api/performance and /api/history/<days> gain honest coverage fields
# ===========================================================================


class TestAC5PerformanceRouteCoverageFields:
    def test_api_performance_response_carries_actual_days_and_coverage_days(
        self, client, tmp_path, monkeypatch
    ):
        sym_id = "sym-perf-coverage"
        rows = [{"days_ago": d, "current_return": 0.1 * (d % 3), "shadow_return": 0.1 * (d % 3)}
                for d in range(20, -1, -1)]  # 21 real trading days
        db_file = _seed_shadow_db(tmp_path / "perf_shadow.db", sym_id, rows)
        monkeypatch.setattr(app_module.analytics, "DB_FILE", db_file)

        resp = client.get("/api/performance", query_string={"scope": "aggregate", "days": "60"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()

        assert body["window_days"] == 60, (
            "AC-5: window_days must keep its EXISTING 'requested window' "
            f"meaning (the plan explicitly says do not redefine it); got "
            f"{body['window_days']!r}"
        )
        assert "actual_days" in body, (
            "AC-5 FAIL: /api/performance response is missing 'actual_days' -- "
            "the honest count of trading days actually available (mirrors the "
            "already-computed observation_count)."
        )
        assert body["actual_days"] == body["observation_count"], (
            "actual_days must equal the already-computed observation_count "
            f"({body['observation_count']}); got {body['actual_days']}"
        )
        assert "coverage_days" in body, (
            "AC-5 FAIL: /api/performance response is missing 'coverage_days' "
            "-- mirrors database.get_exit_turnover_stats' established "
            "'coverage_days = min(window, actual_days)' honesty pattern."
        )
        assert body["coverage_days"] == min(60, body["actual_days"]), (
            f"coverage_days must equal min(requested, actual) = "
            f"min(60, {body['actual_days']}); got {body['coverage_days']}"
        )
        assert "date_range" in body, (
            "AC-5 FAIL: /api/performance response is missing 'date_range' -- "
            "the real earliest/latest date actually covered."
        )

    def test_api_performance_oversized_window_reports_honest_coverage_shortfall(
        self, client, tmp_path, monkeypatch
    ):
        """AC-5's core scenario -- the operator's exact complaint: requesting a
        60d window when only ~21 days of history exist must be VISIBLE in the
        response's own numbers (coverage_days < requested), not silently
        collapsed to 'all the data there is' with no signal."""
        sym_id = "sym-perf-shortfall"
        rows = [{"days_ago": d, "current_return": 0.05, "shadow_return": 0.05}
                for d in range(20, -1, -1)]
        db_file = _seed_shadow_db(tmp_path / "perf_shadow2.db", sym_id, rows)
        monkeypatch.setattr(app_module.analytics, "DB_FILE", db_file)

        resp = client.get("/api/performance", query_string={"scope": "aggregate", "days": "60"})
        body = resp.get_json()
        assert body["coverage_days"] < 60, (
            f"AC-5 FAIL: requested a 60d window over ~21 days of real history; "
            f"coverage_days ({body.get('coverage_days')}) must honestly report "
            f"the shortfall (< 60), not silently claim full coverage."
        )

    def test_api_history_response_carries_actual_days_and_date_range(self, client, tmp_path):
        """GET /api/history/<days>: window_days keeps its existing (requested)
        meaning; actual_days/coverage_days/date_range are additive. No
        post_mortem files exist in this isolated tmp base_dir -> actual_days
        must honestly be 0, not fabricated."""
        with patch.object(
            app_module.analytics, "_POST_MORTEMS_DIR", str(tmp_path / "no_post_mortems")
        ):
            resp = client.get("/api/history/60")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["window_days"] == 60
        assert "actual_days" in body, (
            "AC-5 FAIL: /api/history/<days> response is missing 'actual_days'"
        )
        assert "coverage_days" in body, (
            "AC-5 FAIL: /api/history/<days> response is missing 'coverage_days'"
        )
        assert "date_range" in body, (
            "AC-5 FAIL: /api/history/<days> response is missing 'date_range'"
        )
        assert body["actual_days"] == 0, (
            f"an empty post-mortem directory must honestly report actual_days=0, "
            f"not a fabricated non-zero value; got {body['actual_days']}"
        )


# ===========================================================================
# AC-4 — rendered coverage disclosure target exists, distinct from the
# pre-existing 30-observation stability banner (Decision table: keep separate)
# ===========================================================================


class TestAC4RenderedDisclosureTargetIsDistinctFromStabilityBanner:
    def test_performance_page_has_obs_caption_and_separate_stability_banner(self, client):
        resp = client.get("/performance")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert 'data-testid="obs-caption"' in html, (
            "the JS-populated coverage-disclosure caption element must exist "
            "on the page (renderObsCount's render target)"
        )
        assert 'data-testid="insufficient-banner"' in html, (
            "the pre-existing 30-observation STABILITY banner must remain "
            "(Decision table: 'Keep the existing 30-observation stability "
            "banner SEPARATE — it answers a different question than coverage "
            "disclosure; conflating them is what let this defect hide')."
        )
        # They must be genuinely distinct elements, not the same one relabeled.
        obs_idx = html.find('data-testid="obs-caption"')
        banner_idx = html.find('data-testid="insufficient-banner"')
        assert obs_idx != banner_idx and abs(obs_idx - banner_idx) > 10, (
            "obs-caption and insufficient-banner must be two distinct DOM "
            "elements, not the same element serving both purposes"
        )
