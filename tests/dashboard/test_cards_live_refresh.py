"""
RED tests — cards-live: per-symphony live-refresh via /api/state.

CONTRACT:
  1. /api/state must include a 'symphonies' key in the response — a list of
     per-symphony dicts with fields needed to refresh card values.
  2. Each symphony dict in 'symphonies' must carry: id, status (one of
     armed/tp_armed/para_armed/triggered/standby), tc_bot, tc_held,
     cr_bot, cr_held, mdd_bot, mdd_held.
  3. The values in each symphony dict must be derived from the analytics
     _tc/_cr/_mdd enrichment already computed in the route (not re-computed).
  4. static/index.js must contain an updateCards function (or equivalent)
     that references document.querySelector with data-sym-id.
  5. Each value span targeted by updateCards must carry a data-field attribute
     in templates/index.html so the JS can address it without innerHTML replace.
  6. The 'symphonies' field must be additive — all existing /api/state keys
     must remain present alongside it.
  7. node --check static/index.js must pass (no JS parse errors).

Fixture provenance: bot_state values are constructed here; analytics helpers
are mocked to isolate routing logic from analytics correctness.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JS_PATH = pathlib.Path(__file__).parent.parent.parent / "static" / "index.js"
_INDEX_HTML_PATH = pathlib.Path(__file__).parent.parent.parent / "templates" / "index.html"


def _make_state_with_two_symphonies():
    """Minimal bot_state with two symphony entries covering both armed and standby."""
    return {
        "sym-armed": {
            "name": "Armed Symphony",
            "account": "ACC1",
            "armed": True,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,
            "current_return": 2.5,
            "current_value": 10000.0,
            "stop_trigger": -1.5,
            "mc_prob": 65.0,
        },
        "sym-standby": {
            "name": "Standby Symphony",
            "account": "ACC1",
            "armed": False,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,
            "current_return": 0.5,
            "current_value": 9500.0,
            "stop_trigger": -2.0,
            "mc_prob": 30.0,
        },
    }


def _make_state_with_triggered_symphony():
    """Bot state with a triggered symphony (guard_alpha path)."""
    return {
        "sym-trig": {
            "name": "Triggered Symphony",
            "account": "ACC1",
            "armed": False,
            "tp_armed": False,
            "para_armed": False,
            "triggered": True,
            "triggered_reason": "Trailing Stop",
            "current_return": -3.0,
            "current_value": 9700.0,
            "stop_trigger": -3.0,
            "mc_prob": 20.0,
        }
    }


def _default_analytics_mock():
    m = MagicMock()
    m.get_portfolio_today_change.return_value = {"if_held": 1.0, "dry_run": 0.8}
    m.get_portfolio_cumulative_return.return_value = {"if_held": 10.0, "dry_run": 9.5}
    m.get_portfolio_max_drawdown.return_value = {"if_held": 0.05, "dry_run": 0.05}
    m.get_symphony_today_change.return_value = {"if_held": 1.2, "dry_run": 0.9}
    m.get_symphony_cumulative_return.return_value = {"if_held": 15.0, "dry_run": 14.0}
    m.get_symphony_max_drawdown.return_value = {"if_held": 0.08, "dry_run": 0.07}
    m.get_portfolio_daily_returns_from_shadow.return_value = None
    m.get_history_with_cache_invalidation.return_value = {}
    m.compute_aggregate_returns.return_value = ([], [], [])
    m._POST_MORTEMS_DIR = "/tmp/no-such-dir"
    return m


# ---------------------------------------------------------------------------
# Fixtures
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
        db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
        db_mock.read_fleet_alert.return_value = None
        db_mock.get_triggers.return_value = []
        db_mock.get_guard_alpha_by_symphony.return_value = {}
        yield db_mock


# ---------------------------------------------------------------------------
# AC-1: /api/state response includes 'symphonies' key
# ---------------------------------------------------------------------------


class TestApiStateIncludesSymphonies:
    """
    /api/state must include a 'symphonies' key — a list of per-symphony dicts —
    when bot_state is non-empty.
    """

    def test_api_state_has_symphonies_key(self, client, mock_database, monkeypatch):
        """Response JSON must contain a 'symphonies' key when state is active."""
        mock_database.load_state.return_value = _make_state_with_two_symphonies()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "symphonies" in body, (
            "response must include 'symphonies' key for card refresh; "
            f"got keys: {list(body.keys())}"
        )

    def test_symphonies_is_a_list(self, client, mock_database, monkeypatch):
        """'symphonies' must be a list (not a dict) to enable JS iteration."""
        mock_database.load_state.return_value = _make_state_with_two_symphonies()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        body = resp.get_json()
        assert isinstance(body["symphonies"], list), (
            f"'symphonies' must be a list; got: {type(body['symphonies'])}"
        )

    def test_symphonies_count_matches_bot_state(self, client, mock_database, monkeypatch):
        """'symphonies' list length must equal the number of symphony entries in bot_state."""
        mock_database.load_state.return_value = _make_state_with_two_symphonies()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        body = resp.get_json()
        assert len(body["symphonies"]) == 2, f"expected 2 symphonies; got {len(body['symphonies'])}"


# ---------------------------------------------------------------------------
# AC-2: Each symphony dict has required fields
# ---------------------------------------------------------------------------


class TestSymphonyDictFields:
    """
    Each entry in 'symphonies' must carry id, status, tc_bot, tc_held,
    cr_bot, cr_held, mdd_bot, mdd_held.
    """

    _REQUIRED_FIELDS = (
        "id",
        "status",
        "tc_bot",
        "tc_held",
        "cr_bot",
        "cr_held",
        "mdd_bot",
        "mdd_held",
    )

    def _get_symphonies(self, client, mock_database, monkeypatch, state=None):
        mock_database.load_state.return_value = (
            state if state is not None else _make_state_with_two_symphonies()
        )
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())
        resp = client.get("/api/state")
        return resp.get_json()["symphonies"]

    def test_each_symphony_has_id(self, client, mock_database, monkeypatch):
        syms = self._get_symphonies(client, mock_database, monkeypatch)
        for sym in syms:
            assert "id" in sym, f"symphony dict missing 'id': {sym.keys()}"
            assert sym["id"], "symphony 'id' must be non-empty"

    def test_each_symphony_has_status(self, client, mock_database, monkeypatch):
        syms = self._get_symphonies(client, mock_database, monkeypatch)
        valid_statuses = {"armed", "tp_armed", "para_armed", "triggered", "standby"}
        for sym in syms:
            assert "status" in sym, f"symphony dict missing 'status': {sym.keys()}"
            assert sym["status"] in valid_statuses, (
                f"status '{sym['status']}' not in valid set {valid_statuses}"
            )

    def test_armed_symphony_has_status_armed(self, client, mock_database, monkeypatch):
        syms = self._get_symphonies(client, mock_database, monkeypatch)
        armed = next((s for s in syms if s["id"] == "sym-armed"), None)
        assert armed is not None, "sym-armed not found in symphonies"
        assert armed["status"] == "armed", (
            f"expected status='armed' for armed symphony; got '{armed['status']}'"
        )

    def test_standby_symphony_has_status_standby(self, client, mock_database, monkeypatch):
        syms = self._get_symphonies(client, mock_database, monkeypatch)
        standby = next((s for s in syms if s["id"] == "sym-standby"), None)
        assert standby is not None, "sym-standby not found in symphonies"
        assert standby["status"] == "standby", (
            f"expected status='standby' for standby symphony; got '{standby['status']}'"
        )

    def test_triggered_symphony_has_status_triggered(self, client, mock_database, monkeypatch):
        state = _make_state_with_triggered_symphony()
        syms = self._get_symphonies(client, mock_database, monkeypatch, state=state)
        trig = next((s for s in syms if s["id"] == "sym-trig"), None)
        assert trig is not None, "sym-trig not found in symphonies"
        assert trig["status"] == "triggered", f"expected status='triggered'; got '{trig['status']}'"

    def test_each_symphony_has_tc_fields(self, client, mock_database, monkeypatch):
        syms = self._get_symphonies(client, mock_database, monkeypatch)
        for sym in syms:
            for field in ("tc_bot", "tc_held"):
                assert field in sym, f"symphony dict missing '{field}': {sym.keys()}"

    def test_each_symphony_has_cr_fields(self, client, mock_database, monkeypatch):
        syms = self._get_symphonies(client, mock_database, monkeypatch)
        for sym in syms:
            for field in ("cr_bot", "cr_held"):
                assert field in sym, f"symphony dict missing '{field}': {sym.keys()}"

    def test_each_symphony_has_mdd_fields(self, client, mock_database, monkeypatch):
        syms = self._get_symphonies(client, mock_database, monkeypatch)
        for sym in syms:
            for field in ("mdd_bot", "mdd_held"):
                assert field in sym, f"symphony dict missing '{field}': {sym.keys()}"

    def test_tc_values_are_numeric_or_none(self, client, mock_database, monkeypatch):
        syms = self._get_symphonies(client, mock_database, monkeypatch)
        for sym in syms:
            for field in ("tc_bot", "tc_held", "cr_bot", "cr_held", "mdd_bot", "mdd_held"):
                val = sym[field]
                assert val is None or isinstance(val, (int, float)), (
                    f"symphony['{field}'] must be numeric or None; got {type(val)}: {val}"
                )


# ---------------------------------------------------------------------------
# AC-3: Values in symphonies derive from _tc/_cr/_mdd enrichment
# ---------------------------------------------------------------------------


class TestSymphonyValuesFromAnalytics:
    """
    tc_bot/tc_held/cr_bot/cr_held/mdd_bot/mdd_held must flow from the analytics
    helpers already called in the route (not re-computed or hardcoded).
    """

    def test_tc_bot_derives_from_analytics_mock(self, client, mock_database, monkeypatch):
        mock_database.load_state.return_value = _make_state_with_two_symphonies()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")

        analytics_mock = _default_analytics_mock()
        analytics_mock.get_symphony_today_change.return_value = {"if_held": 3.14, "dry_run": 2.71}
        monkeypatch.setattr(app_module, "analytics", analytics_mock)

        resp = client.get("/api/state")
        syms = resp.get_json()["symphonies"]
        for sym in syms:
            assert sym["tc_bot"] == pytest.approx(2.71, abs=1e-9), (
                f"tc_bot must equal analytics mock dry_run=2.71; got {sym['tc_bot']} for {sym['id']}"
            )
            assert sym["tc_held"] == pytest.approx(3.14, abs=1e-9), (
                f"tc_held must equal analytics mock if_held=3.14; got {sym['tc_held']} for {sym['id']}"
            )

    def test_cr_values_derive_from_analytics_mock(self, client, mock_database, monkeypatch):
        mock_database.load_state.return_value = _make_state_with_two_symphonies()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")

        analytics_mock = _default_analytics_mock()
        analytics_mock.get_symphony_cumulative_return.return_value = {
            "if_held": 42.0,
            "dry_run": 40.0,
        }
        monkeypatch.setattr(app_module, "analytics", analytics_mock)

        resp = client.get("/api/state")
        syms = resp.get_json()["symphonies"]
        for sym in syms:
            assert sym["cr_bot"] == pytest.approx(40.0, abs=1e-9), (
                f"cr_bot must equal mock dry_run=40.0; got {sym['cr_bot']}"
            )
            assert sym["cr_held"] == pytest.approx(42.0, abs=1e-9), (
                f"cr_held must equal mock if_held=42.0; got {sym['cr_held']}"
            )

    def test_mdd_values_derive_from_analytics_mock(self, client, mock_database, monkeypatch):
        mock_database.load_state.return_value = _make_state_with_two_symphonies()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")

        analytics_mock = _default_analytics_mock()
        analytics_mock.get_symphony_max_drawdown.return_value = {"if_held": 7.77, "dry_run": 8.88}
        monkeypatch.setattr(app_module, "analytics", analytics_mock)

        resp = client.get("/api/state")
        syms = resp.get_json()["symphonies"]
        for sym in syms:
            assert sym["mdd_bot"] == pytest.approx(8.88, abs=1e-9), (
                f"mdd_bot must equal mock dry_run=8.88; got {sym['mdd_bot']}"
            )
            assert sym["mdd_held"] == pytest.approx(7.77, abs=1e-9), (
                f"mdd_held must equal mock if_held=7.77; got {sym['mdd_held']}"
            )


# ---------------------------------------------------------------------------
# AC-4: static/index.js contains updateCards logic with data-sym-id selector
# ---------------------------------------------------------------------------


class TestIndexJsUpdateCards:
    """
    static/index.js must contain a function (updateCards or equivalent) that
    uses document.querySelector with '[data-sym-id="' to target card elements.
    """

    def _js_text(self):
        return _JS_PATH.read_text(encoding="utf-8")

    def test_js_contains_data_sym_id_selector(self):
        """JS must use data-sym-id attribute selector to find card DOM elements."""
        src = self._js_text()
        assert "data-sym-id" in src, (
            "static/index.js must reference 'data-sym-id' attribute selector "
            "to address individual symphony cards during live refresh"
        )

    def test_js_iterates_symphonies_from_data(self):
        """JS must read data.symphonies (or equivalent) to drive card updates."""
        src = self._js_text()
        # Accept any of: data.symphonies, data['symphonies'], (data||{}).symphonies
        assert re.search(r"data[.\[].*symphonies", src), (
            "static/index.js must read 'symphonies' from the /api/state response "
            "to update card values; pattern 'data.symphonies' or 'data[\"symphonies\"]' not found"
        )

    def test_js_updates_card_value_elements_by_data_field(self):
        """JS must target card value spans via a data-field attribute (not innerHTML replace)."""
        src = self._js_text()
        assert re.search(r"data-field", src), (
            "static/index.js must use data-field attribute to address individual value spans "
            "in card elements; innerHTML replacement is forbidden"
        )

    def test_js_does_not_replace_card_inner_html(self):
        """JS must not replace a card's innerHTML wholesale (layout-destroying pattern)."""
        src = self._js_text()
        # Flag: card.innerHTML = ... or cardEl.innerHTML = something
        # Acceptable: reading .innerHTML is fine (querySelector result unused for assignment)
        assign_pattern = re.findall(
            r"(?:sym.card|card|cardEl|symCard)\.innerHTML\s*=",
            src,
        )
        assert not assign_pattern, (
            f"static/index.js must not assign .innerHTML on card elements; "
            f"found: {assign_pattern}. Use targeted data-field span updates instead."
        )

    def test_js_does_not_inject_data_html_field(self):
        """
        JS must never inject the vestigial /api/state 'html' field into the DOM.
        (This is the broken pattern from the reverted prior cycle.)
        """
        src = self._js_text()
        # Check for container injection of the html field
        bad_patterns = re.findall(
            r"innerHTML\s*=\s*data(?:\.|(?:\[['\"]))\s*html",
            src,
        )
        assert not bad_patterns, (
            "static/index.js must not inject data.html into the DOM — "
            "this is the broken reverted pattern; found: " + str(bad_patterns)
        )


# ---------------------------------------------------------------------------
# AC-5: index.html value spans have data-field attributes for JS targeting
# ---------------------------------------------------------------------------


class TestIndexHtmlDataFieldHooks:
    """
    The value spans inside .sym-card that JS needs to refresh must carry
    data-field attributes, so updateCards can address them without innerHTML.
    """

    def _html_text(self):
        return _INDEX_HTML_PATH.read_text(encoding="utf-8")

    def test_dual_value_bot_span_has_data_field(self):
        """The bot today-change span in .dual-value-headline must have data-field='tc-bot'."""
        html = self._html_text()
        # Look for a span inside dual-value-headline that carries data-field="tc-bot"
        assert re.search(r'data-field=["\']tc-bot["\']', html), (
            "templates/index.html: the bot today-change span inside .dual-value-headline "
            "must have data-field='tc-bot' so JS can update it in-place"
        )

    def test_dual_value_held_span_has_data_field(self):
        """The if-held today-change span in .dual-value-headline must have data-field='tc-held'."""
        html = self._html_text()
        assert re.search(r'data-field=["\']tc-held["\']', html), (
            "templates/index.html: the if-held today-change span must have data-field='tc-held'"
        )

    def test_footer_cr_bot_span_has_data_field(self):
        """The cumulative-return bot value span in .card-footer-grid must have data-field='cr-bot'."""
        html = self._html_text()
        assert re.search(r'data-field=["\']cr-bot["\']', html), (
            "templates/index.html: cumulative-return bot span must have data-field='cr-bot'"
        )

    def test_footer_cr_held_span_has_data_field(self):
        """The cumulative-return held value span must have data-field='cr-held'."""
        html = self._html_text()
        assert re.search(r'data-field=["\']cr-held["\']', html), (
            "templates/index.html: cumulative-return held span must have data-field='cr-held'"
        )

    def test_footer_mdd_bot_span_has_data_field(self):
        """The max-drawdown bot value span must have data-field='mdd-bot'."""
        html = self._html_text()
        assert re.search(r'data-field=["\']mdd-bot["\']', html), (
            "templates/index.html: max-drawdown bot span must have data-field='mdd-bot'"
        )

    def test_footer_mdd_held_span_has_data_field(self):
        """The max-drawdown held value span must have data-field='mdd-held'."""
        html = self._html_text()
        assert re.search(r'data-field=["\']mdd-held["\']', html), (
            "templates/index.html: max-drawdown held span must have data-field='mdd-held'"
        )


# ---------------------------------------------------------------------------
# AC-6: 'symphonies' is additive — existing /api/state keys remain present
# ---------------------------------------------------------------------------


class TestSymphoniesIsAdditive:
    """
    Adding 'symphonies' to /api/state must not remove or rename any existing keys.
    The known required existing keys: status, bot_state, state, meta, portfolio_strip,
    market_state, live_mode, html.
    """

    _EXISTING_KEYS = (
        "status",
        "bot_state",
        "state",
        "meta",
        "portfolio_strip",
        "market_state",
        "live_mode",
        "html",
    )

    def test_existing_keys_still_present(self, client, mock_database, monkeypatch):
        mock_database.load_state.return_value = _make_state_with_two_symphonies()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        body = resp.get_json()
        for key in self._EXISTING_KEYS:
            assert key in body, (
                f"existing key '{key}' must still be present after adding 'symphonies'; "
                f"got keys: {list(body.keys())}"
            )


# ---------------------------------------------------------------------------
# AC-7: node --check passes on static/index.js
# ---------------------------------------------------------------------------


class TestJsParseCheck:
    """
    `node --check static/index.js` must exit 0 (no syntax errors).
    This is the gate that caught the prior cycle's broken JS.
    """

    def test_index_js_parses_cleanly(self):
        """node --check must succeed for static/index.js."""
        result = subprocess.run(
            ["node", "--check", str(_JS_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"node --check static/index.js failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
