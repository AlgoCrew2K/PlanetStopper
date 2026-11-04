"""
RED tests — M2 dashboard rework.

Covers the contracted behaviours of /api/state and index.html:
  1. Portfolio 6-metric strip — TC/CR/MDD dry-run vs if-held in the JSON response
  2. Per-symphony analytics helpers — called once per symphony
  3. Freshness timestamp — data_as_of field in the JSON response
  4. Read-only contract — /api/state does not mutate bot_state
  5. Portfolio strip UI wiring — index.html DOM target + renderState() consumer
  6. Zero-metric fallback isolation — fallback dicts are never shared across symphonies

All tests use Flask test_client().  No live Composer/Alpaca calls are made.
The M1 analytics helpers (get_portfolio_*) are mocked to avoid coupling this
test suite to analytics correctness (that is tested in test_m1_helpers.py).

Fixture-derived assertions only — no hardcoded producer-computed values.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def mock_database():
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {}
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
        yield db_mock


def _make_state_with_one_symphony(sym_id="sym1", account="ACC1", **overrides):
    """Minimal bot_state with one symphony entry that passes the route's filtering."""
    entry = {
        "name": "My Symphony",
        "account": account,
        "mc_prob": 42.0,
        "triggered": False,
        "armed": False,
        "tp_armed": False,
        "para_armed": False,
        "breakeven_locked": False,
        "current_return": 1.5,
        "stop_trigger": -2.0,
        "shadow_hwm": 3.0,
        "high_water_mark": 3.0,
        "current_value": 10000.0,
    }
    entry.update(overrides)
    return {sym_id: entry}


def _default_analytics_mock():
    """Return a mock analytics module with sensible M1-helper defaults."""
    m = MagicMock()
    m.get_portfolio_today_change.return_value = {"if_held": 1.2, "dry_run": 0.9}
    m.get_portfolio_cumulative_return.return_value = {"if_held": 15.0, "dry_run": 14.5}
    m.get_portfolio_max_drawdown.return_value = {"if_held": 0.12, "dry_run": 0.12}
    m.get_symphony_today_change.return_value = {"if_held": 1.2, "dry_run": 0.9}
    m.get_symphony_cumulative_return.return_value = {"if_held": 15.0, "dry_run": 15.0}
    m.get_symphony_max_drawdown.return_value = {"if_held": 0.12, "dry_run": 0.12}
    # get_portfolio_daily_returns_from_shadow returns tuple[list[str], list[float]] | None.
    # None is the documented "insufficient history" path; _compute_portfolio_strip guards
    # `if _shadow_result is not None:` and skips vol calculation gracefully.  The m2 tests
    # cover today_change / cumulative_return / max_drawdown (M1 helpers), not vol.
    m.get_portfolio_daily_returns_from_shadow.return_value = None
    return m


# The newest recorded observation of the completed cycle: the instant the stamp must name.
_OBSERVATION_UTC = "2026-06-23T14:09:00+00:00"
_OBSERVATION_ET = "2026-06-23T10:09:00"
_OBSERVATION_STAMP = "2026-06-23 10:09 ET"


def _open_market_with_observation(monkeypatch):
    """Pin the live branch and record one engine observation; return the analytics mock.

    The stamp names the newest shadow_history row, not the request clock, so a test of it
    needs that row in the per-test database, which the mocked analytics module is pointed
    at. The market state is pinned so the branch does not depend on the host's time of day.
    """
    import database as real_database

    monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open")
    real_database.record_shadow_observation(
        symphony_id="sym1",
        account_id=None,
        cycle_id=None,
        ts_utc=_OBSERVATION_UTC,
        ts_et=_OBSERVATION_ET,
        trading_day=_OBSERVATION_ET[:10],
        current_return=1.5,
        shadow_return=1.5,
        is_post_trigger=0,
        trigger_id=None,
    )
    analytics_mock = _default_analytics_mock()
    analytics_mock._get_shadow_db_file.return_value = os.environ["DB_PATH"]
    return analytics_mock


# ---------------------------------------------------------------------------
# 1. Portfolio 6-metric strip — JSON contract
# ---------------------------------------------------------------------------


class TestPortfolioStripJson:
    """
    /api/state must return portfolio-level TC/CR/MDD in the JSON body when
    bot_state is non-empty and a Composer symphony list is available.

    The route must call the M1 helpers and surface their results under
    'portfolio_strip' (or similar agreed key) so the client-side JS can
    populate the strip without re-computing analytics.
    """

    def test_api_state_includes_portfolio_strip_key(self, client, mock_database, monkeypatch):
        """Response JSON must contain a 'portfolio_strip' key when state is active."""
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "portfolio_strip" in body, (
            "response must include 'portfolio_strip' key for the 6-metric band; "
            f"got keys: {list(body.keys())}"
        )

    def test_portfolio_strip_has_today_change(self, client, mock_database, monkeypatch):
        """portfolio_strip must contain today_change with if_held and dry_run keys."""
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        analytics_mock = _default_analytics_mock()
        monkeypatch.setattr(app_module, "analytics", analytics_mock)

        resp = client.get("/api/state")
        strip = resp.get_json()["portfolio_strip"]

        assert "today_change" in strip, (
            f"portfolio_strip must have 'today_change'; got keys: {list(strip.keys())}"
        )
        tc = strip["today_change"]
        assert "if_held" in tc, "today_change must have 'if_held' key"
        assert "dry_run" in tc, "today_change must have 'dry_run' key"
        assert isinstance(tc["if_held"], (int, float)), "today_change.if_held must be numeric"
        assert isinstance(tc["dry_run"], (int, float)), "today_change.dry_run must be numeric"

    def test_portfolio_strip_has_cumulative_return(self, client, mock_database, monkeypatch):
        """portfolio_strip must contain cumulative_return with if_held and dry_run keys."""
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        strip = resp.get_json()["portfolio_strip"]

        assert "cumulative_return" in strip, (
            f"portfolio_strip must have 'cumulative_return'; got keys: {list(strip.keys())}"
        )
        cr = strip["cumulative_return"]
        assert "if_held" in cr and "dry_run" in cr

    def test_portfolio_strip_has_max_drawdown(self, client, mock_database, monkeypatch):
        """portfolio_strip must contain max_drawdown with if_held and dry_run keys."""
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        strip = resp.get_json()["portfolio_strip"]

        assert "max_drawdown" in strip, (
            f"portfolio_strip must have 'max_drawdown'; got keys: {list(strip.keys())}"
        )
        mdd = strip["max_drawdown"]
        assert "if_held" in mdd and "dry_run" in mdd

    def test_portfolio_strip_values_derive_from_m1_helpers(
        self, client, mock_database, monkeypatch
    ):
        """
        The values in portfolio_strip must come from the M1 helpers, not from
        a re-implementation.  We inject controlled mock return values and assert
        they round-trip into the response — ensuring the route calls the helpers
        and uses their output rather than computing independently.
        """
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")

        analytics_mock = _default_analytics_mock()
        analytics_mock.get_portfolio_today_change.return_value = {"if_held": 7.77, "dry_run": 6.66}
        analytics_mock.get_portfolio_cumulative_return.return_value = {
            "if_held": 55.5,
            "dry_run": 54.4,
        }
        analytics_mock.get_portfolio_max_drawdown.return_value = {"if_held": 0.33, "dry_run": 0.33}
        monkeypatch.setattr(app_module, "analytics", analytics_mock)

        resp = client.get("/api/state")
        strip = resp.get_json()["portfolio_strip"]

        # Values must flow through from mock return — not hardcoded or recomputed.
        # We compare structure only: each value must be the mock's exact return value.
        assert strip["today_change"]["if_held"] == pytest.approx(7.77, abs=1e-9), (
            "portfolio_strip.today_change.if_held must equal M1 helper output; "
            "failing here means the route is not using get_portfolio_today_change"
        )
        assert strip["today_change"]["dry_run"] == pytest.approx(6.66, abs=1e-9)
        assert strip["cumulative_return"]["if_held"] == pytest.approx(55.5, abs=1e-9)
        assert strip["max_drawdown"]["if_held"] == pytest.approx(0.33, abs=1e-9)

    def test_portfolio_strip_absent_when_state_empty(self, client, mock_database, monkeypatch):
        """
        When bot_state is empty the route returns 'waiting' — strip is not relevant.
        This test pins the absence of an erroneous 'portfolio_strip' in a waiting response.
        """
        mock_database.load_state.return_value = {}
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})

        resp = client.get("/api/state")
        body = resp.get_json()
        assert body["status"] == "waiting"
        # The waiting response keeps the strip key: the page reads it. With no state it
        # states no more than the value-weighted floor over zero symphonies, marked as such,
        # and the dash of an unknown moment; it fabricates no account figure.
        assert "portfolio_strip" in body, "waiting response must carry the portfolio_strip key"
        strip = body["portfolio_strip"]
        assert isinstance(strip, dict)
        assert strip.get("basis") == "value_weighted", (
            "the waiting strip must carry the honest value-weighted floor marker"
        )
        assert strip.get("data_as_of") == app_module._NO_STAMP
        assert not strip.get("account_value"), "no account value exists without state"
        assert all(v is None for v in (strip.get("cumulative_return") or {}).values()), (
            f"waiting response must not fabricate a cumulative return: "
            f"{strip.get('cumulative_return')!r}"
        )

    def test_portfolio_m1_helpers_called_with_symphonies_list(
        self, client, mock_database, monkeypatch
    ):
        """
        The route must pass a symphonies list to the M1 helpers, not an empty list.
        The Composer symphony list (with last_percent_change, value fields) is
        required for value-weighted aggregation.  Passing an empty list silently
        returns 0/0 without error — a bug masquerading as a feature.

        We assert: get_portfolio_today_change was called, and its first positional
        argument is a non-empty list.
        """
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")

        analytics_mock = _default_analytics_mock()
        monkeypatch.setattr(app_module, "analytics", analytics_mock)

        client.get("/api/state")

        assert analytics_mock.get_portfolio_today_change.called, (
            "get_portfolio_today_change must be called by /api/state"
        )
        call_args = analytics_mock.get_portfolio_today_change.call_args
        # First positional arg is the symphonies list.
        symphonies_arg = call_args.args[0] if call_args.args else call_args.kwargs.get("symphonies")
        assert symphonies_arg is not None, (
            "get_portfolio_today_change must receive a symphonies argument"
        )
        assert isinstance(symphonies_arg, list), (
            f"symphonies argument must be a list; got {type(symphonies_arg)}"
        )
        assert len(symphonies_arg) > 0, (
            "symphonies list passed to M1 helpers must be non-empty when bot_state has symphonies; "
            "an empty list silently produces 0/0 aggregates — check that the route "
            "populates the symphonies list from bot_state or Composer data"
        )


# ---------------------------------------------------------------------------
# 2. Per-symphony TC/CR/MDD helpers
# ---------------------------------------------------------------------------


class TestPerSymphonyMetricColumns:
    """
    The per-symphony TC / CR / MDD (dry-run vs if-held) figures are computed once for
    each symphony in the state.
    """

    def test_per_symphony_m1_helpers_called_once_per_symphony(
        self, client, mock_database, monkeypatch
    ):
        """
        get_symphony_today_change must be called once per symphony in bot_state.
        Using two symphonies: expect exactly 2 calls.
        """
        state = {
            "sym1": {
                "name": "Alpha",
                "account": "ACC1",
                "current_return": 1.0,
                "current_value": 5000.0,
                "triggered": False,
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "breakeven_locked": False,
                "stop_trigger": -2.0,
                "shadow_hwm": 2.0,
                "high_water_mark": 2.0,
                "mc_prob": 30.0,
            },
            "sym2": {
                "name": "Beta",
                "account": "ACC1",
                "current_return": -0.5,
                "current_value": 5000.0,
                "triggered": False,
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "breakeven_locked": False,
                "stop_trigger": -3.0,
                "shadow_hwm": 1.0,
                "high_water_mark": 1.0,
                "mc_prob": 60.0,
            },
        }
        mock_database.load_state.return_value = state
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})

        analytics_mock = _default_analytics_mock()
        monkeypatch.setattr(app_module, "analytics", analytics_mock)

        client.get("/api/state")

        call_count = analytics_mock.get_symphony_today_change.call_count
        assert call_count == 2, (
            f"get_symphony_today_change must be called once per symphony (2); "
            f"got {call_count} calls — check the per-row loop in /api/state or template context"
        )


# ---------------------------------------------------------------------------
# 3. Clarity pass — column labels and tooltips in rendered HTML
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 4. HWM bug fix — sort key must match rendered value (both shadow_hwm)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 5. Freshness timestamp — data_as_of in JSON
# ---------------------------------------------------------------------------


class TestFreshnessTimestamp:
    """
    /api/state must include a 'data_as_of' field containing the time the
    response was generated, formatted as HH:MM ET (or similar).

    This is a MANDATORY feature following a prior staleness incident where
    a stale value appeared live.

    Contract:
    - JSON response includes 'data_as_of' key
    - Value is a string in HH:MM format (24h or 12h, ET timezone label optional)
    """

    def test_api_state_includes_data_as_of_key(self, client, mock_database, monkeypatch):
        """Active /api/state response must include 'data_as_of' key."""
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "data_as_of" in body, (
            "response must include 'data_as_of' key; "
            "MANDATORY — a prior staleness incident had stale values reading as live"
        )

    def test_data_as_of_is_hhmm_string(self, client, mock_database, monkeypatch):
        """
        data_as_of must be a string naming the data's ET date and time.
        We assert format only here; the value is pinned by the next test.
        """
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _open_market_with_observation(monkeypatch))

        resp = client.get("/api/state")
        data_as_of = resp.get_json()["data_as_of"]

        assert isinstance(data_as_of, str), f"data_as_of must be a string; got {type(data_as_of)}"
        # The stamp names the ET date and time of the data: 'YYYY-MM-DD HH:MM ET'.
        stamp_pattern = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})? ET$")
        assert stamp_pattern.match(data_as_of.strip()), (
            f"data_as_of must match 'YYYY-MM-DD HH:MM ET'; got '{data_as_of}'"
        )

    def test_data_as_of_names_the_data_moment_not_the_request_clock(
        self, client, mock_database, monkeypatch
    ):
        """
        data_as_of must name the newest recorded observation, never the clock of the
        request. The observation is far from now, so a stamp built from the request time
        cannot equal it, and a stamp cached across requests cannot follow a new row.
        """
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _open_market_with_observation(monkeypatch))

        resp = client.get("/api/state")

        assert resp.get_json()["data_as_of"] == _OBSERVATION_STAMP, (
            "the stamp must name the newest observation's ET date and time"
        )

    def test_data_as_of_absent_when_state_waiting(self, client, mock_database, monkeypatch):
        """
        When the route returns 'waiting' (empty state), data_as_of need not be
        present — or if present, it must not be a stale/wrong value.
        This is a non-crashing contract pin.
        """
        mock_database.load_state.return_value = {}
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})

        resp = client.get("/api/state")
        body = resp.get_json()
        assert body["status"] == "waiting"
        # If data_as_of is present it must still be a valid time string
        if "data_as_of" in body:
            hhmm_pattern = re.compile(r"^\d{1,2}:\d{2}")
            assert hhmm_pattern.match(body["data_as_of"].strip()), (
                "data_as_of in waiting response must still be a valid HH:MM string"
            )


# ---------------------------------------------------------------------------
# 6. Read-only contract — /api/state must not mutate bot_state
# ---------------------------------------------------------------------------


class TestReadOnlyContract:
    """
    The dashboard route is read-only.  Threading M1 helper data into the
    response must not write back to bot_state or the database.
    """

    def test_api_state_does_not_call_database_save(self, client, mock_database, monkeypatch):
        """
        GET /api/state must not call any database write methods.
        Dashboard is read-only per architecture constraints.
        """
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        client.get("/api/state")

        # No write method should have been called
        assert (
            not mock_database.save_state.called if hasattr(mock_database, "save_state") else True
        ), "GET /api/state must not call database.save_state"
        # save_symphony_strategy is a settings route concern, not dashboard
        assert not mock_database.save_symphony_strategy.called, (
            "GET /api/state must not call database.save_symphony_strategy"
        )


# ---------------------------------------------------------------------------
# 7. Portfolio strip UI wiring — index.html DOM target + renderState() consumer
# (Reviewer BLOCK finding: portfolio_strip returned in JSON but never consumed)
# ---------------------------------------------------------------------------

_INDEX_HTML_PATH = (
    __import__("pathlib").Path(__file__).parent.parent.parent / "templates" / "index.html"
)
_INDEX_JS_PATH = __import__("pathlib").Path(__file__).parent.parent.parent / "static" / "index.js"


class TestPortfolioStripUiWiring:
    """
    Studio design contract: portfolio summary is rendered as the hero-section
    (data-testid="hero-section") with vs-rows for Today / Cumulative / Max DD.
    The portfolio_strip JSON from /api/state is consumed by static/index.js
    (not inline JS in index.html — the Studio design moved JS to static files).

    These tests assert the Studio design contract, not the old Tailwind
    portfolio-strip DOM element that was replaced in the V3 redesign.
    """

    def test_index_html_has_hero_section_as_portfolio_summary(self):
        """
        index.html must contain data-testid="hero-section" — the Studio portfolio
        summary panel that replaced the old id="portfolio-strip" element.
        """
        html = _INDEX_HTML_PATH.read_text(encoding="utf-8")

        assert 'data-testid="hero-section"' in html, (
            'index.html must contain data-testid="hero-section" — the Studio hero '
            "panel is the portfolio summary surface; the old id='portfolio-strip' "
            "was replaced by the hero comparison section in the V3 Studio redesign"
        )

    def test_index_js_renders_portfolio_strip_data(self):
        """
        static/index.js must reference portfolio_strip from the /api/state response
        to populate the hero comparison rows on each poll cycle.
        The Studio design moved JS from inline <script> in index.html to static/index.js.
        """
        js = _INDEX_JS_PATH.read_text(encoding="utf-8")

        assert "portfolio_strip" in js, (
            "static/index.js must read data.portfolio_strip from the /api/state response "
            "and use it to update the hero comparison rows; "
            "reviewer BLOCK: /api/state returns portfolio_strip JSON and the hero section "
            "must consume it on each poll"
        )

    def test_index_js_strip_reads_today_change(self):
        """
        static/index.js must reference today_change from portfolio_strip to update
        the Today comparison row in the hero section.
        """
        js = _INDEX_JS_PATH.read_text(encoding="utf-8")

        assert "today_change" in js, (
            "static/index.js must reference portfolio_strip.today_change to update "
            "the Today row in the hero comparison panel"
        )

    def test_index_js_strip_reads_cumulative_return(self):
        """static/index.js must reference cumulative_return from portfolio_strip."""
        js = _INDEX_JS_PATH.read_text(encoding="utf-8")

        assert "cumulative_return" in js, (
            "static/index.js must reference portfolio_strip.cumulative_return; "
            "check the updateComparisonRows / renderState function"
        )

    def test_index_js_strip_reads_max_drawdown(self):
        """static/index.js must reference max_drawdown from portfolio_strip."""
        js = _INDEX_JS_PATH.read_text(encoding="utf-8")

        assert "max_drawdown" in js, (
            "static/index.js must reference portfolio_strip.max_drawdown; "
            "check the updateComparisonRows / renderState function"
        )

    def test_index_html_hero_section_positioned_after_header(self):
        """
        Per A/C: the portfolio summary (hero-section) must appear after the
        page header chrome in the DOM.  The Studio design includes the chrome
        via {% include '_chrome.html' %} and then renders the hero-section.
        We assert hero-section appears after the chrome include directive.
        """
        html = _INDEX_HTML_PATH.read_text(encoding="utf-8")

        chrome_include_pos = html.find("_chrome.html")
        hero_pos = html.find('data-testid="hero-section"')

        assert hero_pos != -1, 'data-testid="hero-section" not found in index.html'
        assert chrome_include_pos != -1, (
            "index.html must include '_chrome.html' for the page navigation chrome"
        )
        assert chrome_include_pos < hero_pos, (
            f"hero-section (pos={hero_pos}) must appear after chrome include (pos={chrome_include_pos}); "
            "the portfolio summary belongs in the page body, after the navigation chrome"
        )


# ---------------------------------------------------------------------------
# 8. Zero-metric fallback isolation — shared mutable dict must not corrupt state
# (Reviewer advisory, encoded as a RED pin)
# ---------------------------------------------------------------------------


class TestZeroMetricFallbackIsolation:
    """
    app.py uses a single _zero_metric dict object for all exception-path
    fallbacks.  If multiple sym["_tc"]/_cr/_mdd entries point to the same
    dict and any code mutates one, all are corrupted.

    The fix: each fallback must be a fresh dict literal, not a shared reference.
    These tests pin that the fallback dicts for different symphonies are
    independent objects (not the same identity).
    """

    def test_zero_metric_fallback_dicts_are_not_shared_across_symphonies(
        self, client, mock_database, monkeypatch
    ):
        """
        When analytics helpers raise (triggering the fallback path) for two
        symphonies, the resulting _tc dicts must be distinct objects.
        If they share identity, mutating one would corrupt the other.
        """
        state = {
            "sym1": {
                "name": "Alpha",
                "account": "ACC1",
                "current_return": None,  # will cause TypeError in helper
                "current_value": 5000.0,
                "triggered": False,
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "breakeven_locked": False,
                "stop_trigger": -2.0,
                "shadow_hwm": 2.0,
                "high_water_mark": 2.0,
                "mc_prob": 50.0,
            },
            "sym2": {
                "name": "Beta",
                "account": "ACC1",
                "current_return": None,
                "current_value": 5000.0,
                "triggered": False,
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "breakeven_locked": False,
                "stop_trigger": -2.0,
                "shadow_hwm": 1.0,
                "high_water_mark": 1.0,
                "mc_prob": 30.0,
            },
        }
        mock_database.load_state.return_value = state
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})

        analytics_mock = _default_analytics_mock()
        # Force the TC helper to raise so the fallback path is taken for both syms
        analytics_mock.get_symphony_today_change.side_effect = KeyError("last_percent_change")
        monkeypatch.setattr(app_module, "analytics", analytics_mock)

        # Record the body dict the route hands to jsonify, then let jsonify answer as usual.
        served = []
        real_jsonify = app_module.jsonify

        def recording_jsonify(*args, **kwargs):
            served.append(args[0] if args else kwargs)
            return real_jsonify(*args, **kwargs)

        monkeypatch.setattr(app_module, "jsonify", recording_jsonify)
        client.get("/api/state")

        assert served, "the route never built a body"
        entries = [served[-1]["bot_state"][sym_id] for sym_id in ("sym1", "sym2")]

        tc_dicts = [entry["_tc"] for entry in entries if "_tc" in entry]
        assert len(tc_dicts) == 2, f"both syms must have _tc set via fallback; got {len(tc_dicts)}"
        # The two fallback dicts must be independent objects, not the same reference.
        assert tc_dicts[0] is not tc_dicts[1], (
            "_tc fallback dicts for sym1 and sym2 must be distinct objects; "
            "a shared fallback dict would let a mutation of one corrupt the other"
        )
