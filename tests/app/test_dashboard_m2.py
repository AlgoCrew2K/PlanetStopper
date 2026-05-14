"""
RED tests — M2 dashboard rework.

Covers six contracted changes to /api/state + table_partial.html + index.html:
  1. Portfolio 6-metric strip — TC/CR/MDD dry-run vs if-held in the JSON response
  2. Per-symphony clarity pass — improved column labels / tooltips in rendered HTML
  3. Per-symphony TC/CR/MDD vs if-held — new fields in rendered table rows
  4. HWM bug fix — sort key aligns with rendered value (both shadow_hwm)
  5. Freshness timestamp — data_as_of field in JSON + rendered in HTML
  6. Portfolio strip UI wiring — index.html DOM target + renderState() consumer

All tests use Flask test_client().  No live Composer/Alpaca calls are made.
The M1 analytics helpers (get_portfolio_*) are mocked to avoid coupling this
test suite to analytics correctness (that is tested in test_m1_helpers.py).

Fixture-derived assertions only — no hardcoded producer-computed values.
"""

from __future__ import annotations

import re
from datetime import datetime
from unittest.mock import MagicMock, patch

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
    return m


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

    def test_api_state_includes_portfolio_strip_key(
        self, client, mock_database, monkeypatch
    ):
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

    def test_portfolio_strip_has_today_change(
        self, client, mock_database, monkeypatch
    ):
        """portfolio_strip must contain today_change with if_held and dry_run keys."""
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        analytics_mock = _default_analytics_mock()
        monkeypatch.setattr(app_module, "analytics", analytics_mock)

        resp = client.get("/api/state")
        strip = resp.get_json()["portfolio_strip"]

        assert "today_change" in strip, (
            "portfolio_strip must have 'today_change'; "
            f"got keys: {list(strip.keys())}"
        )
        tc = strip["today_change"]
        assert "if_held" in tc, "today_change must have 'if_held' key"
        assert "dry_run" in tc, "today_change must have 'dry_run' key"
        assert isinstance(tc["if_held"], (int, float)), (
            "today_change.if_held must be numeric"
        )
        assert isinstance(tc["dry_run"], (int, float)), (
            "today_change.dry_run must be numeric"
        )

    def test_portfolio_strip_has_cumulative_return(
        self, client, mock_database, monkeypatch
    ):
        """portfolio_strip must contain cumulative_return with if_held and dry_run keys."""
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        strip = resp.get_json()["portfolio_strip"]

        assert "cumulative_return" in strip, (
            "portfolio_strip must have 'cumulative_return'; "
            f"got keys: {list(strip.keys())}"
        )
        cr = strip["cumulative_return"]
        assert "if_held" in cr and "dry_run" in cr

    def test_portfolio_strip_has_max_drawdown(
        self, client, mock_database, monkeypatch
    ):
        """portfolio_strip must contain max_drawdown with if_held and dry_run keys."""
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        strip = resp.get_json()["portfolio_strip"]

        assert "max_drawdown" in strip, (
            "portfolio_strip must have 'max_drawdown'; "
            f"got keys: {list(strip.keys())}"
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
        analytics_mock.get_portfolio_cumulative_return.return_value = {"if_held": 55.5, "dry_run": 54.4}
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

    def test_portfolio_strip_absent_when_state_empty(
        self, client, mock_database, monkeypatch
    ):
        """
        When bot_state is empty the route returns 'waiting' — strip is not relevant.
        This test pins the absence of an erroneous 'portfolio_strip' in a waiting response.
        """
        mock_database.load_state.return_value = {}
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})

        resp = client.get("/api/state")
        body = resp.get_json()
        assert body["status"] == "waiting"
        # strip must not appear in a non-active response
        assert "portfolio_strip" not in body, (
            "waiting response must not include portfolio_strip"
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
# 2. Per-symphony TC/CR/MDD in rendered HTML
# ---------------------------------------------------------------------------


class TestPerSymphonyMetricColumns:
    """
    table_partial.html must render TC / CR / MDD (dry-run vs if-held) as
    flat inline columns.  These are new data points per row that the implementer
    must thread through the template context.
    """

    def _get_rendered_html(self, client, mock_database, monkeypatch, state=None, analytics_mock=None):
        if state is None:
            state = _make_state_with_one_symphony()
        mock_database.load_state.return_value = state
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        if analytics_mock is None:
            analytics_mock = _default_analytics_mock()
        monkeypatch.setattr(app_module, "analytics", analytics_mock)
        resp = client.get("/api/state")
        assert resp.status_code == 200
        return resp.get_json().get("html", "")

    def test_rendered_html_contains_tc_dry_run_value(
        self, client, mock_database, monkeypatch
    ):
        """
        The per-symphony Today's Change dry-run value must appear in the rendered
        table row.  We inject a sentinel value via the analytics mock and assert
        the rendered HTML contains it.
        """
        analytics_mock = _default_analytics_mock()
        # Inject a sentinel that is visually distinctive in the HTML
        analytics_mock.get_symphony_today_change.return_value = {"if_held": 3.14, "dry_run": -2.71}
        html = self._get_rendered_html(
            client, mock_database, monkeypatch, analytics_mock=analytics_mock
        )
        assert "-2.71" in html or "-2.7" in html, (
            "rendered HTML must contain the dry_run today-change value from the M1 helper; "
            "failing here means per-symphony TC is not wired into the template — "
            "check that get_symphony_today_change output reaches table_partial.html"
        )

    def test_rendered_html_contains_tc_if_held_value(
        self, client, mock_database, monkeypatch
    ):
        """Per-symphony TC if-held must also appear in the rendered table row."""
        analytics_mock = _default_analytics_mock()
        analytics_mock.get_symphony_today_change.return_value = {"if_held": 9.81, "dry_run": 0.0}
        html = self._get_rendered_html(
            client, mock_database, monkeypatch, analytics_mock=analytics_mock
        )
        assert "9.81" in html, (
            "rendered HTML must contain the if_held today-change value; "
            "check that get_symphony_today_change if_held reaches table_partial.html"
        )

    def test_rendered_html_contains_cr_column_values(
        self, client, mock_database, monkeypatch
    ):
        """Per-symphony cumulative return (both sides) must appear in rendered HTML."""
        analytics_mock = _default_analytics_mock()
        analytics_mock.get_symphony_cumulative_return.return_value = {"if_held": 42.42, "dry_run": 42.42}
        html = self._get_rendered_html(
            client, mock_database, monkeypatch, analytics_mock=analytics_mock
        )
        assert "42.42" in html or "42.4" in html, (
            "rendered HTML must contain cumulative return value from M1 helper; "
            "check get_symphony_cumulative_return wiring to table_partial.html"
        )

    def test_rendered_html_contains_mdd_column_values(
        self, client, mock_database, monkeypatch
    ):
        """Per-symphony MDD (both sides) must appear in rendered HTML."""
        analytics_mock = _default_analytics_mock()
        analytics_mock.get_symphony_max_drawdown.return_value = {"if_held": 0.1234, "dry_run": 0.1234}
        html = self._get_rendered_html(
            client, mock_database, monkeypatch, analytics_mock=analytics_mock
        )
        # MDD is typically rendered as a percent: 0.1234 -> "12.34%" or as raw "0.12"
        assert "0.12" in html or "12.3" in html, (
            "rendered HTML must contain max-drawdown value from M1 helper; "
            "check get_symphony_max_drawdown wiring to table_partial.html"
        )

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


class TestColumnClarityPass:
    """
    table_partial.html must replace cryptic column headers with clearer labels
    and add informational hover-overs (title attributes).

    From the audit (table-audit.md §2):
      - "MC Prob" -> "Beat-SPY Prob" (or with tooltip)
      - "Stop Level" -> "Trailing Stop"
      - "Exit Return" -> "Return" or with tooltip clarifying pre/post trigger meaning
      - "High Water Mark" -> "Shadow Peak"
      - "If Held (Shadow)" -> "If Held"

    We assert the NEW labels and that tooltips (title=) are present.
    The old cryptic labels must be absent or augmented.
    """

    def _get_html(self, client, mock_database, monkeypatch):
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())
        resp = client.get("/api/state")
        return resp.get_json().get("html", "")

    def test_mc_prob_column_has_tooltip(self, client, mock_database, monkeypatch):
        """
        The MC Prob column header must have a title attribute (tooltip) explaining
        what the metric means.  Bare 'MC Prob' with no tooltip is a clarity failure.
        """
        html = self._get_html(client, mock_database, monkeypatch)
        # The header <th> for MC Prob (or Beat-SPY Prob) must include title=
        # We look for the column heading area containing either label + title attribute.
        pattern = re.compile(r'<th[^>]*title=["\'][^"\']+["\'][^>]*>[^<]*(MC Prob|Beat.SPY Prob)', re.IGNORECASE)
        alt_pattern = re.compile(r'(MC Prob|Beat.SPY Prob)[^<]*<', re.IGNORECASE)
        # At minimum, the heading text must be accompanied by a title somewhere nearby.
        # Check that the word "Monte Carlo" or "SPY" appears in a title attribute in the vicinity.
        title_with_mc = re.compile(r'title=["\'][^"\']*(?:Monte Carlo|SPY|beat)[^"\']*["\']', re.IGNORECASE)
        assert title_with_mc.search(html), (
            "MC Prob column must have a title tooltip containing 'Monte Carlo', 'SPY', or 'beat'; "
            "the audit found this column has no tooltip — add a <th title='...'> that explains the metric"
        )

    def test_stop_level_relabelled_to_trailing_stop(self, client, mock_database, monkeypatch):
        """
        Column label 'Stop Level' must be replaced with 'Trailing Stop'.
        The audit recommended this rename for clarity.
        """
        html = self._get_html(client, mock_database, monkeypatch)
        assert "Trailing Stop" in html, (
            "column header must read 'Trailing Stop', not 'Stop Level'; "
            "audit recommendation: rename to remove ambiguity about price vs return threshold"
        )
        # 'Stop Level' bare (without trailing stop) should be absent or accompanied by rename
        # We allow "Stop Level" only inside a title/tooltip attribute, not as a standalone header label.
        # Find standalone header <th> text that is just 'Stop Level'
        standalone_stop_level = re.compile(
            r'<th[^>]*>[^<]*Stop Level[^<]*</th>', re.IGNORECASE
        )
        assert not standalone_stop_level.search(html), (
            "'Stop Level' must not remain as a standalone column header label; "
            "it must be renamed to 'Trailing Stop'"
        )

    def test_high_water_mark_relabelled_to_shadow_peak(self, client, mock_database, monkeypatch):
        """
        Column label 'High Water Mark' must be replaced with 'Shadow Peak'.
        Audit recommendation: consistent with 'shadow'/'if-held' vocabulary.
        """
        html = self._get_html(client, mock_database, monkeypatch)
        assert "Shadow Peak" in html, (
            "column header must read 'Shadow Peak', not 'High Water Mark'; "
            "audit: rename for consistency with if-held/dry-run vocabulary"
        )

    def test_if_held_shadow_relabelled_to_if_held(self, client, mock_database, monkeypatch):
        """
        Column label 'If Held (Shadow)' must be simplified to 'If Held'.
        'Shadow' is internal jargon; the operator-facing vocabulary is 'if-held'.
        """
        html = self._get_html(client, mock_database, monkeypatch)
        assert "If Held" in html, (
            "column header must contain 'If Held'; "
            "check that 'If Held (Shadow)' was updated"
        )
        # The parenthetical '(Shadow)' must be removed or replaced
        assert "(Shadow)" not in html, (
            "'(Shadow)' must be removed from the column header; "
            "the operator-facing vocabulary is 'if-held', not 'shadow'"
        )

    def test_trailing_stop_column_has_tooltip(self, client, mock_database, monkeypatch):
        """
        The Trailing Stop column header must have a title tooltip explaining
        what 'trailing stop' means (return %, not price).
        """
        html = self._get_html(client, mock_database, monkeypatch)
        # Look for title containing 'trailing' or 'return' near the Trailing Stop header
        title_with_stop = re.compile(
            r'title=["\'][^"\']*(?:trailing|return|exit|threshold)[^"\']*["\']',
            re.IGNORECASE,
        )
        assert title_with_stop.search(html), (
            "Trailing Stop header must have a title tooltip explaining the metric; "
            "audit: ambiguous without context (price vs return level)"
        )

    def test_shadow_peak_column_has_tooltip(self, client, mock_database, monkeypatch):
        """
        The Shadow Peak column header must have a title tooltip explaining
        what the shadow peak represents.
        """
        html = self._get_html(client, mock_database, monkeypatch)
        title_with_peak = re.compile(
            r'title=["\'][^"\']*(?:shadow|if.held|peak|high.water|trailing)[^"\']*["\']',
            re.IGNORECASE,
        )
        assert title_with_peak.search(html), (
            "Shadow Peak header must have a title tooltip; "
            "audit: no tooltip on High Water Mark — add one on rename"
        )


# ---------------------------------------------------------------------------
# 4. HWM bug fix — sort key must match rendered value (both shadow_hwm)
# ---------------------------------------------------------------------------


class TestHwmBugFix:
    """
    The audit found: column renders sym.shadow_hwm but sort uses high_water_mark.
    After fix: sort must use shadow_hwm (consistent with rendered value).

    We assert via HTML inspection that the sort onclick/param for the HWM column
    uses 'shadow_hwm' (not 'high_water_mark'), and that the rendered cell shows
    shadow_hwm (not high_water_mark).
    """

    def test_hwm_sort_key_is_shadow_hwm_not_high_water_mark(
        self, client, mock_database, monkeypatch
    ):
        """
        The HWM / Shadow Peak column's sort trigger must use 'shadow_hwm' as the
        sortCol value.  Before the fix it used 'high_water_mark', which was
        inconsistent with the rendered value (shadow_hwm).
        """
        state = _make_state_with_one_symphony(
            shadow_hwm=7.77,
            high_water_mark=5.55,  # deliberately different from shadow_hwm
        )
        mock_database.load_state.return_value = state
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        html = resp.get_json().get("html", "")

        # The setSort() call for the HWM/Shadow Peak column must use 'shadow_hwm'
        assert "setSort('shadow_hwm')" in html or 'setSort("shadow_hwm")' in html, (
            "HWM/Shadow Peak column sort must use 'shadow_hwm' as the sort key; "
            "bug: current code uses 'high_water_mark' — the cell renders shadow_hwm "
            "but sorting used the wrong field"
        )
        # The old broken key must be absent as a sort trigger
        assert "setSort('high_water_mark')" not in html and 'setSort("high_water_mark")' not in html, (
            "'high_water_mark' must not be the sort key for the Shadow Peak column "
            "after the HWM bug fix"
        )

    def test_hwm_column_renders_shadow_hwm_value(
        self, client, mock_database, monkeypatch
    ):
        """
        The rendered HWM cell must display shadow_hwm, not high_water_mark.
        These differ in our test fixture: shadow_hwm=7.77, high_water_mark=5.55.
        Only 7.77 must appear in the HWM column; 5.55 must not.
        """
        state = _make_state_with_one_symphony(
            shadow_hwm=7.77,
            high_water_mark=5.55,
        )
        mock_database.load_state.return_value = state
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        html = resp.get_json().get("html", "")

        assert "7.77" in html, (
            "rendered HWM column must show shadow_hwm=7.77; "
            "if 7.77 is absent, the cell is not rendering shadow_hwm"
        )

    def test_app_sort_by_shadow_hwm_uses_shadow_hwm_field(
        self, client, mock_database, monkeypatch
    ):
        """
        GET /api/state?sortCol=shadow_hwm must sort by shadow_hwm (the correct
        post-fix key), not by the old high_water_mark key.  We verify by injecting
        two symphonies whose shadow_hwm ordering differs from high_water_mark ordering.

        sym_a: shadow_hwm=10, high_water_mark=1
        sym_b: shadow_hwm=1,  high_water_mark=10

        Sorted desc by shadow_hwm: sym_a first.
        Sorted desc by high_water_mark: sym_b first.
        The route must use shadow_hwm ordering.
        """
        state = {
            "sym_a": {
                "name": "Alpha",
                "account": "ACC1",
                "shadow_hwm": 10.0,
                "high_water_mark": 1.0,
                "current_return": 0.0,
                "current_value": 5000.0,
                "triggered": False,
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "breakeven_locked": False,
                "stop_trigger": -2.0,
                "mc_prob": 50.0,
            },
            "sym_b": {
                "name": "Beta",
                "account": "ACC1",
                "shadow_hwm": 1.0,
                "high_water_mark": 10.0,
                "current_return": 0.0,
                "current_value": 5000.0,
                "triggered": False,
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "breakeven_locked": False,
                "stop_trigger": -2.0,
                "mc_prob": 50.0,
            },
        }
        mock_database.load_state.return_value = state
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        captured_render_args = {}

        def capturing_render(template, **ctx):
            captured_render_args.update(ctx)
            return ""

        monkeypatch.setattr(app_module, "render_template", capturing_render)

        client.get("/api/state?sortCol=shadow_hwm&sortDir=desc")

        accounts_map = captured_render_args.get("accounts_map", {})
        acc_syms = list(accounts_map.values())[0] if accounts_map else []

        assert len(acc_syms) == 2, (
            f"expected 2 symphonies in accounts_map; got {len(acc_syms)}"
        )
        # After desc sort by shadow_hwm: Alpha (10) before Beta (1)
        first_name = acc_syms[0].get("name")
        assert first_name == "Alpha", (
            f"desc sort by shadow_hwm must put Alpha (shadow_hwm=10) first; "
            f"got '{first_name}' — the route is still sorting by high_water_mark"
        )


# ---------------------------------------------------------------------------
# 5. Freshness timestamp — data_as_of in JSON + HTML
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
    - The rendered HTML also contains the freshness indicator
    """

    def test_api_state_includes_data_as_of_key(
        self, client, mock_database, monkeypatch
    ):
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

    def test_data_as_of_is_hhmm_string(
        self, client, mock_database, monkeypatch
    ):
        """
        data_as_of must be a string in HH:MM format (wall clock, ET implied).
        We assert format only — not an exact value (wall clock is non-deterministic).
        """
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        data_as_of = resp.get_json()["data_as_of"]

        assert isinstance(data_as_of, str), (
            f"data_as_of must be a string; got {type(data_as_of)}"
        )
        # Accept HH:MM or H:MM with optional seconds and/or 'ET' suffix
        hhmm_pattern = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?(\s*ET)?$")
        assert hhmm_pattern.match(data_as_of.strip()), (
            f"data_as_of must match HH:MM format; got '{data_as_of}' — "
            "format should read like '09:45 ET' or '14:32'"
        )

    def test_data_as_of_reflects_current_time(
        self, client, mock_database, monkeypatch
    ):
        """
        data_as_of must be close to the current wall-clock time.
        We verify the hour matches (within a 1-minute window boundary tolerance).

        This guards against using a stale cached timestamp — the staleness bug
        was precisely that a cached time appeared as the freshness indicator.
        """
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        before = datetime.now()
        resp = client.get("/api/state")
        after = datetime.now()

        data_as_of = resp.get_json()["data_as_of"]
        # Parse just HH:MM from the value
        time_part = data_as_of.strip().split(" ")[0]  # strip 'ET' suffix if present
        parsed = datetime.strptime(time_part, "%H:%M")

        # The hour must match what it was when the request ran.
        # We allow the hour to be either before.hour or after.hour (straddle boundary).
        valid_hours = {before.hour, after.hour}
        assert parsed.hour in valid_hours, (
            f"data_as_of hour={parsed.hour} does not match current hour (one of {valid_hours}); "
            "the timestamp must be generated fresh per request — not a stale cached value"
        )

    def test_rendered_html_contains_freshness_indicator(
        self, client, mock_database, monkeypatch
    ):
        """
        The rendered table_partial.html or index.html must contain a visible
        freshness indicator — the 'data as of HH:MM ET' text must appear so the
        operator can see when the data was last fetched.
        """
        mock_database.load_state.return_value = _make_state_with_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        html = resp.get_json().get("html", "")

        # The HTML must contain a time string in HH:MM format and an 'ET' or
        # 'data as of' indicator — exact wording flexible, presence mandatory.
        has_et = "ET" in html
        has_data_as_of = re.compile(r"data.as.of", re.IGNORECASE).search(html) is not None
        has_time_pattern = re.compile(r"\d{1,2}:\d{2}").search(html) is not None

        assert (has_et or has_data_as_of) and has_time_pattern, (
            "rendered HTML must contain a freshness indicator (time + 'ET' or 'data as of'); "
            "MANDATORY — prior staleness incident: operator must always see data freshness"
        )

    def test_data_as_of_absent_when_state_waiting(
        self, client, mock_database, monkeypatch
    ):
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

    def test_api_state_does_not_call_database_save(
        self, client, mock_database, monkeypatch
    ):
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
        assert not mock_database.save_state.called if hasattr(mock_database, "save_state") else True, (
            "GET /api/state must not call database.save_state"
        )
        # save_symphony_strategy is a settings route concern, not dashboard
        assert not mock_database.save_symphony_strategy.called, (
            "GET /api/state must not call database.save_symphony_strategy"
        )


# ---------------------------------------------------------------------------
# 7. Portfolio strip UI wiring — index.html DOM target + renderState() consumer
# (Reviewer BLOCK finding: portfolio_strip returned in JSON but never consumed)
# ---------------------------------------------------------------------------

_INDEX_HTML_PATH = (
    __import__("pathlib").Path(__file__).parent.parent.parent
    / "templates"
    / "index.html"
)


class TestPortfolioStripUiWiring:
    """
    index.html must contain:
      (a) a DOM element that is the render target for the portfolio strip
      (b) JS in renderState() that reads data.portfolio_strip and populates it

    Without both, portfolio_strip is dead JSON — computed and returned by
    /api/state but never shown to the operator.

    These are static analysis tests against the template file — no Flask
    test_client needed.  They will RED immediately against the current
    index.html which has no portfolio strip DOM or JS consumer.
    """

    def test_index_html_has_portfolio_strip_dom_element(self):
        """
        index.html must contain a DOM element that serves as the render target
        for the portfolio strip.  Acceptable identifiers: id='portfolio-strip',
        id='portfolioStrip', or a class containing 'portfolio-strip'.
        """
        html = _INDEX_HTML_PATH.read_text(encoding="utf-8")

        has_dom_target = (
            "portfolio-strip" in html
            or "portfolioStrip" in html
            or "portfolio_strip" in html
        )
        assert has_dom_target, (
            "index.html must contain a DOM render target for the portfolio strip "
            "(e.g. id='portfolio-strip'); "
            "reviewer BLOCK: /api/state returns portfolio_strip JSON but renderState() "
            "never reads it — the 6-metric band is invisible to the operator"
        )

    def test_index_html_render_state_reads_portfolio_strip(self):
        """
        The renderState() function in index.html must reference data.portfolio_strip
        (or equivalent) to populate the strip DOM element on each poll cycle.

        Without this the strip element, even if present in the HTML, will always
        show its initial empty/placeholder state.
        """
        html = _INDEX_HTML_PATH.read_text(encoding="utf-8")

        # renderState must contain a reference to portfolio_strip from the API response
        assert "portfolio_strip" in html, (
            "renderState() in index.html must read data.portfolio_strip from the "
            "/api/state response and populate the strip DOM element; "
            "currently the key is returned but never consumed — strip is dead JSON"
        )

    def test_index_html_strip_shows_today_change(self):
        """
        The portfolio strip JS must reference today_change (TC) from portfolio_strip.
        All three metrics (TC/CR/MDD) must be individually consumed.
        """
        html = _INDEX_HTML_PATH.read_text(encoding="utf-8")

        assert "today_change" in html, (
            "index.html must reference portfolio_strip.today_change to render the "
            "Today's Change metric in the portfolio strip; "
            "check renderState() or the strip DOM element's population logic"
        )

    def test_index_html_strip_shows_cumulative_return(self):
        """The portfolio strip JS must reference cumulative_return from portfolio_strip."""
        html = _INDEX_HTML_PATH.read_text(encoding="utf-8")

        assert "cumulative_return" in html, (
            "index.html must reference portfolio_strip.cumulative_return; "
            "check renderState() strip population"
        )

    def test_index_html_strip_shows_max_drawdown(self):
        """The portfolio strip JS must reference max_drawdown from portfolio_strip."""
        html = _INDEX_HTML_PATH.read_text(encoding="utf-8")

        assert "max_drawdown" in html, (
            "index.html must reference portfolio_strip.max_drawdown; "
            "check renderState() strip population"
        )

    def test_index_html_strip_dom_positioned_between_header_and_table(self):
        """
        Per A/C: the strip must appear between the header banner and the symphony
        table (not inside either).  We assert the DOM target appears after </header>
        and before the accounts-container div.
        """
        html = _INDEX_HTML_PATH.read_text(encoding="utf-8")

        header_end = html.find("</header>")
        accounts_start = html.find('id="accounts-container"')

        # Find any portfolio strip DOM anchor in the document
        strip_pos = -1
        for candidate in ("portfolio-strip", "portfolioStrip", "portfolio_strip"):
            pos = html.find(candidate)
            if pos != -1:
                strip_pos = pos
                break

        assert strip_pos != -1, (
            "index.html must contain a portfolio strip DOM element; "
            "none of 'portfolio-strip', 'portfolioStrip', 'portfolio_strip' found"
        )
        assert header_end != -1 and accounts_start != -1, (
            "index.html structure unexpected: </header> or accounts-container not found"
        )
        assert header_end < strip_pos < accounts_start, (
            f"portfolio strip DOM element (pos={strip_pos}) must appear after "
            f"</header> (pos={header_end}) and before accounts-container "
            f"(pos={accounts_start}); A/C requires the strip between header and table"
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

        captured_render_args = {}

        def capturing_render(template, **ctx):
            captured_render_args.update(ctx)
            return ""

        monkeypatch.setattr(app_module, "render_template", capturing_render)
        client.get("/api/state")

        accounts_map = captured_render_args.get("accounts_map", {})
        syms = list(accounts_map.values())[0] if accounts_map else []
        assert len(syms) == 2, f"expected 2 syms in accounts_map; got {len(syms)}"

        tc_dicts = [s["_tc"] for s in syms if "_tc" in s]
        assert len(tc_dicts) == 2, (
            f"both syms must have _tc set via fallback; got {len(tc_dicts)}"
        )
        # The two fallback dicts must be independent objects, not the same reference.
        assert tc_dicts[0] is not tc_dicts[1], (
            "_tc fallback dicts for sym1 and sym2 must be distinct objects; "
            "currently _zero_metric is a single shared dict — mutating one corrupts the other; "
            "fix: use a fresh dict literal {'if_held': 0.0, 'dry_run': 0.0} in each except branch"
        )
