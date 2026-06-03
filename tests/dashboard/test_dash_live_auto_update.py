"""
RED tests for dash-live auto-update (AC-1 through AC-4).

The dashboard MUST always reflect current state on its own — a hard refresh
is NEVER required.  These tests drive the five required changes:

  AC-1  /api/state returns `html` (table_partial rendered HTML) that reflects
        current bot_state — exited symphonies show as exited, not stale 0.0%.

  AC-2  static/index.js passes `node --check` — a JS parse error kills the
        entire poll loop silently, making all server-side fixes useless.
        (Lesson from feedback_visual_gate_catches_js_break_unit_tests_miss.)

  AC-3  JS wiring: updateDashboard() in index.js references `data.html` and
        injects it into a named table container; index.html contains that
        container element.

  AC-4a Snapshot reset: on a NEW trading day, the closed/pre-market path MUST
        NOT serve yesterday's `last_market_close_snapshot` before session open.
        It must serve a clean reset (empty table or "waiting" state).

  AC-4b Held metrics guard: `tc_if_held` is blank/None pre-session; the
        lifetime metrics `cr_if_held` and `mdd_if_held` are UNTOUCHED so that
        Guard Alpha (`alpha = cr - cr_if_held`) remains a lifetime figure.

Fixture provenance: all inputs are hand-authored, schema-derived from
alpha_bot_execution.py state-writer patterns and app.py enrichment.  No
producer-computed values are hardcoded — assertions target shape/format/
presence and structural invariants only.

Market-calendar note: `market_calendar.is_trading_day` and `session_open` are
injected via monkeypatching — no live calendar I/O in any test run.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_WORKTREE = Path(__file__).resolve().parents[2]
_INDEX_JS = _WORKTREE / "static" / "index.js"
_INDEX_HTML = _WORKTREE / "templates" / "index.html"


# ---------------------------------------------------------------------------
# Flask client fixture — minimal stubs, no live DB or network
# ---------------------------------------------------------------------------

@pytest.fixture()
def flask_client():
    """Flask test client with the minimal stubs needed for /api/state."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with (
        patch.object(app_module, "analytics") as mock_analytics,
        patch.object(app_module, "schedule"),
    ):
        mock_analytics.get_portfolio_today_change.return_value = {"if_held": None, "dry_run": None}
        mock_analytics.get_portfolio_cumulative_return.return_value = {"if_held": None, "dry_run": None}
        mock_analytics.get_portfolio_max_drawdown.return_value = {"if_held": None, "dry_run": None}
        mock_analytics.get_symphony_today_change.return_value = {"if_held": None, "dry_run": None}
        mock_analytics.get_symphony_cumulative_return.return_value = {"if_held": None, "dry_run": None}
        mock_analytics.get_symphony_max_drawdown.return_value = {"if_held": None, "dry_run": None}
        mock_analytics.get_portfolio_daily_returns_from_shadow.return_value = None
        mock_analytics.get_history_with_cache_invalidation.return_value = None
        with app_module.app.test_client() as client:
            yield client, app_module


def _make_db_mock(bot_state: dict) -> MagicMock:
    """Return a database mock configured with sensible defaults."""
    m = MagicMock()
    m.load_state.return_value = bot_state
    m.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
    m.get_triggers.return_value = []
    m.normalize_name.side_effect = lambda n: (n or "").lower()
    m.read_fleet_alert.return_value = None
    m.get_guard_alpha_by_symphony.return_value = {}
    m.get_ro_connection.return_value = MagicMock()
    return m


# ===========================================================================
# AC-1: /api/state reflects exited symphonies in `html`
# ===========================================================================

class TestApiStateHtmlReflectsExitedSymphony:
    """
    AC-1: when bot_state marks a symphony as triggered/exited, the rendered
    `html` value in /api/state must NOT show the pre-exit 0.0% value as the
    only status — it must convey the exit.

    Root cause: updateDashboard() currently ignores `data.html`; this test
    pins the SERVER side of the contract — the response must include `html`
    that reflects exited state.  The client wiring tests (AC-3) pin the JS
    side.
    """

    def test_api_state_includes_html_key_for_live_exited_symphony(
        self, flask_client, monkeypatch
    ):
        """
        AC-1: live path (market open) — response includes 'html' key,
        and it is a non-empty string.  A wrong implementation that strips
        'html' from the response would fail here.
        """
        client, app_module = flask_client
        bot_state = {
            "sym_exited": {
                "name": "ExitedSym",
                "account": "ACC1",
                "triggered": True,
                "triggered_reason": "Trailing Stop",
                "current_return": -5.3,
            }
        }
        with patch.object(app_module, "database", _make_db_mock(bot_state)):
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "open", raising=False
            )
            monkeypatch.setattr(app_module, "_dotenv_module",
                                MagicMock(dotenv_values=lambda *_a, **_k: {}))
            resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()

        assert "html" in body, (
            "/api/state live-path response must include 'html' key. "
            f"Keys present: {list(body.keys())}. "
            "updateDashboard() needs data.html to inject table rows on each poll."
        )
        html_val = body["html"]
        assert isinstance(html_val, str) and html_val.strip(), (
            f"/api/state.html must be a non-empty string; got {html_val!r}. "
            "An empty html prevents the table from ever updating after page load."
        )

    def test_api_state_html_contains_symphony_row_for_triggered_symphony(
        self, flask_client, monkeypatch
    ):
        """
        AC-1: rendered HTML must contain a row for the triggered symphony.
        The JS injection replaces the entire table so every active symphony
        must appear — if a triggered symphony is absent the operator cannot
        see the exit without a hard refresh.

        The bot_state uses a fully-formed symphony dict (all required template
        fields populated) so the render_template call succeeds and we get
        proper HTML.  A test that fails only because render_template raises is
        testing infrastructure, not the AC.
        """
        client, app_module = flask_client
        sym_id = "sym_exited_abc"
        # Fully-formed symphony dict matching the fields table_partial.html requires.
        # Fields derived from schema inspection of table_partial.html template vars.
        bot_state = {
            sym_id: {
                "name": "ExitedSym",
                "account": "ACC1",
                "triggered": True,
                "triggered_reason": "Trailing Stop",
                "current_return": -5.3,
                "mc_prob": 8.2,
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "stop_trigger": -6.0,
                "shadow_hwm": 1.2,
                "above_tp_count": 0,
                "below_stop_count": 0,
                "breakeven_locked": False,
                "triggered_at_return": -5.3,
                "triggered_at_stop": -6.0,
                "triggered_at_hwm": 1.2,
                "triggered_at_time": "10:05",
                "current_value": 50000.0,
                "_tc": {"if_held": None, "dry_run": None},
                "_cr": {"if_held": None, "dry_run": None},
                "_mdd": {"if_held": None, "dry_run": None},
            }
        }
        with patch.object(app_module, "database", _make_db_mock(bot_state)):
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "open", raising=False
            )
            monkeypatch.setattr(app_module, "_dotenv_module",
                                MagicMock(dotenv_values=lambda *_a, **_k: {}))
            resp = client.get("/api/state")

        body = resp.get_json()
        html = body.get("html", "")

        assert isinstance(html, str) and html.strip(), (
            "html must be a non-empty string to contain symphony rows. "
            f"Got: {html!r}"
        )
        # The fallback sentinel is the only non-empty html a broken render returns.
        assert html != "<table><tbody></tbody></table>", (
            "html is the render_template fallback sentinel — template raised an "
            "exception during render.  The symphony id cannot appear in a fallback. "
            "Check that the bot_state dict has all fields required by table_partial.html."
        )
        assert sym_id in html, (
            f"Rendered HTML must contain symphony id {sym_id!r} "
            f"so the triggered row is visible after the next poll. "
            f"HTML (first 400 chars): {html[:400]!r}"
        )


# ===========================================================================
# AC-2: node --check passes on static/index.js
# ===========================================================================

class TestIndexJsNodeCheck:
    """
    AC-2: `node --check static/index.js` must exit 0.

    A JS parse error kills the entire 30-second poll loop without any visible
    error on the dashboard — every server-side fix becomes dead code.
    (Lesson: feedback_visual_gate_catches_js_break_unit_tests_miss)

    This test is RED when index.js has a syntax error; it turns GREEN when
    the JS is valid (which it currently is or will be after the implementer's
    changes).  The test is included here because the implementer MUST NOT
    break JS syntax while wiring in the data.html injection.
    """

    def test_index_js_is_syntactically_valid_per_node_check(self):
        """
        `node --check` must exit 0.  Any syntax error in index.js silently
        disables the entire poll loop; this test catches it before it reaches
        production.
        """
        result = subprocess.run(
            ["node", "--check", str(_INDEX_JS)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"`node --check {_INDEX_JS.name}` failed with exit code "
            f"{result.returncode}.\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}\n"
            "A JS syntax error silently breaks the 30s poll loop — the "
            "operator never sees live state updates without a hard refresh."
        )


# ===========================================================================
# AC-3: JS wiring — updateDashboard injects data.html; index.html has container
# ===========================================================================

class TestJsWiringDataHtmlInjection:
    """
    AC-3: static/index.js must reference `data.html` and inject it into a
    named DOM container; templates/index.html must contain that container.

    These are AST/grep-level checks since there is no DOM runner available.
    A wrong implementation that still calls updateDashboard() but ignores
    data.html would fail here.

    The exact container id/selector and the injection pattern (innerHTML or
    morphdom) are left to the implementer — we assert the structural
    invariants that make the contract verifiable.
    """

    def test_index_js_reads_data_html_property(self):
        """
        updateDashboard() or a renderer it calls must access `data.html`
        (or `data['html']`).  Without this read the table never updates.
        """
        js_text = _INDEX_JS.read_text(encoding="utf-8")
        # Match data.html or data['html'] or data["html"]
        pattern = re.compile(r'data\.html\b|data\[[\'""]html[\'""]]\s*')
        assert pattern.search(js_text), (
            "static/index.js must reference data.html (the table partial HTML "
            "from /api/state).  updateDashboard() currently ignores it — "
            "wiring it in is the core of this fix.  "
            "Expected to find `data.html` or `data['html']` in index.js."
        )

    def test_index_js_injects_html_into_a_container(self):
        """
        The wiring must actually SET innerHTML (or call morphdom / insertAdjacentHTML)
        on a container element, not merely read data.html.  A read with no write
        is a no-op.
        """
        js_text = _INDEX_JS.read_text(encoding="utf-8")
        # Look for assignment-to-innerHTML on any el or for morphdom/insertAdjacentHTML
        injection_pattern = re.compile(
            r'\.innerHTML\s*=|morphdom\s*\(|insertAdjacentHTML\s*\('
        )
        # Must appear AFTER the data.html reference, but for simplicity we just
        # assert both patterns exist anywhere in the file.
        data_html_pattern = re.compile(r'data\.html\b|data\[[\'""]html[\'""]]\s*')

        has_data_html = bool(data_html_pattern.search(js_text))
        has_injection = bool(injection_pattern.search(js_text))

        assert has_data_html and has_injection, (
            "static/index.js must both (a) read data.html and (b) inject it into "
            "a container via .innerHTML= or morphdom() or insertAdjacentHTML(). "
            f"data.html reference found: {has_data_html}. "
            f"Injection mechanism found: {has_injection}. "
            "Without an actual DOM write the table stays static after page load."
        )

    def test_index_html_contains_table_container_element(self):
        """
        templates/index.html must contain a named container element (an element
        with an id that the JS targets to inject the table partial).

        We require an element with id=`sym-table-container` or
        `data-testid=\\"sym-table-container\\"` — the implementer picks the
        exact id; this test ensures it exists so the querySelector in JS
        can find it.
        """
        html_text = _INDEX_HTML.read_text(encoding="utf-8")
        # Accept id="sym-table-container" or id='sym-table-container'
        container_pattern = re.compile(
            r'id=["\']sym-table-container["\']'
            r'|data-testid=["\']sym-table-container["\']'
        )
        assert container_pattern.search(html_text), (
            'templates/index.html must contain a container element with '
            'id="sym-table-container" (or data-testid="sym-table-container") '
            "so the JS poll can inject the freshly-rendered table_partial HTML. "
            "Without this container the querySelector in index.js returns null "
            "and the table never updates on poll."
        )

    def test_sym_table_container_is_not_hidden_with_display_none(self):
        """
        BLOCK-1 (flask-dashboard-specialist review): the sym-table-container element
        must NOT have `style="display:none"` (or any inline display:none variant).

        If the container is hidden, the JS injects the table HTML correctly but the
        operator never sees it — identical trap to the JS parse-error lesson
        (feedback_visual_gate_catches_js_break_unit_tests_miss): the mechanism works
        at the server level while the operator sees nothing.

        The JS renderer does NOT unconditionally remove display:none after injection
        (verified in index.js), so the HTML must not apply the hide in the first place.
        """
        html_text = _INDEX_HTML.read_text(encoding="utf-8")

        # Find the sym-table-container element line(s) and check for display:none
        # We look for the container tag itself to scope the assertion correctly.
        container_re = re.compile(
            r'id=["\']sym-table-container["\'][^>]*>|'
            r'<[^>]+id=["\']sym-table-container["\'][^>]*>'
        )
        match = container_re.search(html_text)
        assert match, (
            'sym-table-container element not found in index.html — '
            'cannot check visibility. Ensure the element exists first.'
        )

        tag_html = match.group(0)
        # Check for display:none in any of its common forms
        hidden_pattern = re.compile(r'display\s*:\s*none', re.IGNORECASE)
        assert not hidden_pattern.search(tag_html), (
            'sym-table-container has `display:none` inline style. '
            'The JS injects data.html into this container on every poll, '
            'but the operator never sees it because the container is hidden. '
            f'Tag HTML: {tag_html!r}. '
            'Remove the display:none from the element in index.html, OR '
            'add `tableContainer.style.display = "";` after the innerHTML assignment '
            'in index.js to reveal it on first injection.'
        )


# ===========================================================================
# AC-4a: Snapshot reset — new trading day must not serve yesterday's snapshot
# ===========================================================================

class TestSnapshotResetOnNewTradingDay:
    """
    AC-4a: on a NEW trading day, the pre-market path must NOT serve the
    previous day's `last_market_close_snapshot`.

    Root cause: the current app.py closed/pre_market branch checks whether
    `last_market_close_snapshot` exists but does NOT check whether the snapshot
    belongs to yesterday vs today.  A pre-market request on day D+1 would
    serve day D's frozen table — the operator sees stale data until session open.

    After the fix: pre-market on a NEW trading day returns a clean reset
    (status=waiting or an empty html) rather than yesterday's snapshot.
    """

    def _make_snapshot_for_date(self, snapshot_date: str) -> dict:
        """Return a minimal last_market_close_snapshot dated `snapshot_date`."""
        return {
            "captured_at_et": f"{snapshot_date}T16:01:00",
            "data_as_of": "16:00 ET",
            "trading_day": snapshot_date,
            "accounts_map": {
                "ACC1": [
                    {
                        "id": "sym_stale",
                        "name": "StaleHolding",
                        "current_return": 2.5,
                        "triggered": False,
                    }
                ]
            },
            "account_labels": {"ACC1": "Individual"},
            "shadow_divergence": {},
        }

    def test_pre_market_new_trading_day_does_not_serve_previous_day_snapshot(
        self, flask_client, monkeypatch
    ):
        """
        AC-4a: pre_market on a NEW trading day (is_trading_day=True for today,
        snapshot.trading_day = yesterday) must return status=waiting — a clean
        reset, NOT yesterday's frozen table.

        The A/C says: "pre-market of a new day shows a clean reset (not
        yesterday's frozen close)."  Serving stale snapshot data with only a
        notice field does NOT satisfy this requirement — the operator sees
        yesterday's table regardless of whether a notice field is present in
        the JSON.

        BLOCK-2 (flask-dashboard-specialist): a prior test iteration accepted
        notice-only as sufficient.  This tighter assertion requires status=waiting
        so the operator-visible table is clean.
        """
        client, app_module = flask_client

        # Snapshot is from YESTERDAY (2026-06-02); today is 2026-06-03.
        snapshot_yesterday = self._make_snapshot_for_date("2026-06-02")
        bot_state = {
            "date": "2026-06-02",
            "last_market_close_snapshot": snapshot_yesterday,
        }

        with patch.object(app_module, "database", _make_db_mock(bot_state)):
            # Inject: today IS a trading day, time is pre-market (before 09:30)
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "pre_market", raising=False
            )
            monkeypatch.setattr(
                app_module.market_calendar, "is_trading_day",
                lambda d: True,
                raising=False,
            )
            monkeypatch.setattr(
                app_module.market_calendar, "session_open",
                lambda d: __import__("datetime").time(9, 30),
                raising=False,
            )
            monkeypatch.setattr(app_module, "_dotenv_module",
                                MagicMock(dotenv_values=lambda *_a, **_k: {}))
            resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()

        status = body.get("status", "")
        assert status == "waiting", (
            "On pre_market of a NEW trading day (snapshot.trading_day='2026-06-02', "
            "today='2026-06-03'), the response must be status='waiting' — a clean "
            "reset. The operator must see a clean slate, not yesterday's frozen table. "
            f"Got status={status!r}. "
            "A notice field in the JSON is insufficient — it is not surfaced in the "
            "operator UI; the operator still sees yesterday's symphony table rows. "
            "The implementation must skip the stale snapshot entirely and fall through "
            "to the waiting path when pre_market + new trading day is detected."
        )

    def test_pre_market_same_trading_day_may_serve_snapshot(
        self, flask_client, monkeypatch
    ):
        """
        Regression guard: pre-market on the SAME day (snapshot.trading_day ==
        today) is still valid to serve.  The reset must ONLY fire when the
        snapshot belongs to a different (prior) trading day.
        """
        client, app_module = flask_client
        import datetime

        today_str = datetime.date.today().isoformat()
        snapshot_today = self._make_snapshot_for_date(today_str)
        bot_state = {
            "date": today_str,
            "last_market_close_snapshot": snapshot_today,
        }

        with patch.object(app_module, "database", _make_db_mock(bot_state)):
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "pre_market", raising=False
            )
            monkeypatch.setattr(
                app_module.market_calendar, "is_trading_day",
                lambda d: True,
                raising=False,
            )
            monkeypatch.setattr(app_module, "_dotenv_module",
                                MagicMock(dotenv_values=lambda *_a, **_k: {}))
            resp = client.get("/api/state")

        # Must not crash — same-day snapshot serving is fine.
        assert resp.status_code == 200, (
            f"pre_market + same-day snapshot must return 200, got {resp.status_code}"
        )


# ===========================================================================
# AC-4b: Held metrics guard — tc_if_held blank pre-session; cr/mdd untouched
# ===========================================================================

class TestHeldMetricsPreSession:
    """
    AC-4b: `tc_if_held` (today's held change = Composer todays_percent_change)
    must be blank/None pre-session (before session_open on a trading day; all
    day on a non-trading day).

    `cr_if_held` (lifetime cumulative) and `mdd_if_held` (lifetime max-dd)
    must be UNTOUCHED — Guard Alpha (`alpha = cr - cr_if_held`) is a LIFETIME
    metric and must not lose its history at market open.

    Implementation note: `tc_if_held` derives from
    `_account_totals_cache["portfolio_tc"]` which is the Composer
    todays_percent_change.  Pre-session, Composer has not yet computed today's
    change — the cache should not have a stale intraday value from yesterday.
    `_build_meta()` must emit None / 0.0 for tc_if_held in that case, not a
    stale prior-day value.
    """

    def test_tc_if_held_is_none_or_zero_pre_session(
        self, flask_client, monkeypatch
    ):
        """
        AC-4b: meta.portfolio.tc_if_held must be absent, None, or 0.0
        when the market is pre_market (Composer has no valid today-change yet).

        A wrong implementation that caches yesterday's todays_percent_change
        and returns it pre-session would fail this assertion.
        """
        client, app_module = flask_client

        bot_state = {}  # empty — no live data pre-session

        with patch.object(app_module, "database", _make_db_mock(bot_state)):
            # Explicitly clear the account-totals cache so no stale tc leaks through
            original_cache = dict(app_module._account_totals_cache)
            app_module._account_totals_cache.clear()

            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "pre_market", raising=False
            )
            monkeypatch.setattr(app_module, "_dotenv_module",
                                MagicMock(dotenv_values=lambda *_a, **_k: {}))
            resp = client.get("/api/state")

            # Restore cache after the request
            app_module._account_totals_cache.update(original_cache)

        assert resp.status_code == 200
        body = resp.get_json()
        meta = body.get("meta") or {}
        portfolio = meta.get("portfolio") or {}

        tc_if_held = portfolio.get("tc_if_held")
        # Acceptable pre-session values: absent, None, or 0.0
        # (0.0 is the _build_meta default when no cache entry exists — correct)
        assert tc_if_held is None or tc_if_held == 0.0, (
            f"meta.portfolio.tc_if_held must be None or 0.0 pre-session "
            f"(Composer has not yet computed today's change). "
            f"Got {tc_if_held!r}. "
            "A stale yesterday tc_if_held here would mislead the operator about "
            "today's held return before the session begins."
        )

    def test_cr_if_held_is_present_and_not_zeroed_when_history_exists(
        self, flask_client, monkeypatch
    ):
        """
        AC-4b regression guard: cr_if_held must NOT be wiped to 0 or None
        by the pre-session reset.  Guard Alpha uses cr_if_held as a lifetime
        baseline — zeroing it would show a false positive Alpha at open.
        """
        client, app_module = flask_client

        # Inject a portfolio_cr into the cache (simulating history from prior days)
        import app as app_module_direct
        original_cache = dict(app_module_direct._account_totals_cache)
        app_module_direct._account_totals_cache["portfolio_cr"] = 12.5

        bot_state = {}

        try:
            with patch.object(app_module, "database", _make_db_mock(bot_state)):
                monkeypatch.setattr(
                    app_module, "get_market_state", lambda dt: "pre_market", raising=False
                )
                monkeypatch.setattr(app_module, "_dotenv_module",
                                    MagicMock(dotenv_values=lambda *_a, **_k: {}))
                resp = client.get("/api/state")
        finally:
            # Always restore the cache
            app_module_direct._account_totals_cache.clear()
            app_module_direct._account_totals_cache.update(original_cache)

        assert resp.status_code == 200
        body = resp.get_json()
        meta = body.get("meta") or {}
        portfolio = meta.get("portfolio") or {}

        cr_if_held = portfolio.get("cr_if_held")
        # When portfolio_cr cache has a value, cr_if_held must reflect it
        # (must be non-zero, since we injected 12.5).  A wrong implementation
        # that zeros cr_if_held pre-session would fail here.
        assert cr_if_held is not None, (
            "meta.portfolio.cr_if_held must not be None when portfolio_cr cache "
            "has a value — Guard Alpha (cr - cr_if_held) is a lifetime metric. "
            f"Got cr_if_held={cr_if_held!r}"
        )
        # 0.0 default is only acceptable when NO cache entry exists; we injected 12.5.
        assert cr_if_held != 0.0, (
            "meta.portfolio.cr_if_held must not be zeroed to 0.0 when the "
            "portfolio_cr cache holds a non-zero lifetime return. "
            f"Got cr_if_held={cr_if_held!r} with portfolio_cr=12.5 in cache. "
            "Zeroing cr_if_held pre-session would fabricate a false Guard Alpha."
        )

    def test_mdd_if_held_is_present_when_cache_has_portfolio_mdd(
        self, flask_client, monkeypatch
    ):
        """
        AC-4b regression guard: mdd_if_held must NOT be zeroed by the
        pre-session reset.  It is a lifetime max-drawdown figure.
        """
        client, app_module = flask_client

        import app as app_module_direct
        original_cache = dict(app_module_direct._account_totals_cache)
        # portfolio_mdd from Composer is stored as negative (convention from API)
        app_module_direct._account_totals_cache["portfolio_mdd"] = -8.2

        bot_state = {}

        try:
            with patch.object(app_module, "database", _make_db_mock(bot_state)):
                monkeypatch.setattr(
                    app_module, "get_market_state", lambda dt: "pre_market", raising=False
                )
                monkeypatch.setattr(app_module, "_dotenv_module",
                                    MagicMock(dotenv_values=lambda *_a, **_k: {}))
                resp = client.get("/api/state")
        finally:
            app_module_direct._account_totals_cache.clear()
            app_module_direct._account_totals_cache.update(original_cache)

        assert resp.status_code == 200
        body = resp.get_json()
        meta = body.get("meta") or {}
        portfolio = meta.get("portfolio") or {}

        mdd_if_held = portfolio.get("mdd_if_held")
        assert mdd_if_held is not None, (
            "meta.portfolio.mdd_if_held must not be None when portfolio_mdd "
            "cache has a value — it is a lifetime metric that must persist "
            f"across pre-session resets.  Got {mdd_if_held!r}."
        )
        assert mdd_if_held != 0.0, (
            "meta.portfolio.mdd_if_held must not be zeroed pre-session when "
            f"the cache holds a non-zero lifetime MDD.  Got {mdd_if_held!r}."
        )
