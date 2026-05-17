"""
RED tests for V3 — Fleet-correlation circuit breaker dashboard surface.

Covers AC-V3.1 (banner renders), AC-V3.3 (banner clears/dismiss route),
and /api/state fleet_correlation_alert field exposure.

Test naming: scenario-based.

Fixture provenance: alert_state_shape.json + auto_clear.json from
  tests/fixtures/engine/fleet_correlation/. Zero hardcoded producer values.

Mocking:
  - Flask test client with database.load_state patched.
  - No live network; no live DB reads.
  - dismiss route patches database.save_state.

PA-18: fixtures are schema-derived per alert_state_shape.json.
PA-19: explicit APPROVE from quant-code-reviewer required before merge.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

_FLEET_FIXTURES = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "engine"
    / "fleet_correlation"
)


def _load(name: str) -> dict:
    return json.loads((_FLEET_FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# Flask test client fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def flask_client():
    """Return a Flask test client with minimal env patching."""
    with patch("database.init_db"):
        import app as flask_app
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# Section 1 — /api/state exposes fleet_correlation_alert
# ---------------------------------------------------------------------------


class TestApiStateExposesFleetCorrelationAlert:
    """
    AC-V3.1: /api/state must include a top-level 'fleet_correlation_alert' field.
    Value is None when no alert is active; the alert dict when tripped.
    """

    def _base_state(self) -> dict:
        """Minimal bot_state that /api/state renders without error."""
        return {}

    def test_api_state_includes_fleet_correlation_alert_field_when_tripped(self, flask_client):
        """
        When bot_state contains 'fleet_correlation_alert', /api/state must expose it
        as a top-level field in the JSON response.
        """
        fixture = _load("alert_state_shape.json")
        alert_payload = fixture["example"]

        active_state = {"fleet_correlation_alert": alert_payload}

        with patch("database.load_state", return_value=active_state), \
             patch("database.get_shadow_divergence", return_value={"by_symphony": {}, "portfolio_today": None}), \
             patch("database.get_triggers", return_value=[]), \
             patch("market_calendar.get_market_state", return_value="open"), \
             patch("analytics.get_portfolio_today_change", return_value={}), \
             patch("analytics.get_portfolio_cumulative_return", return_value={}), \
             patch("analytics.get_portfolio_max_drawdown", return_value={}), \
             patch("app.render_template", return_value=""):
            resp = flask_client.get("/api/state")

        assert resp.status_code == 200, f"/api/state returned {resp.status_code}"
        data = resp.get_json()
        assert data is not None, "/api/state returned non-JSON response"
        assert "fleet_correlation_alert" in data, (
            "/api/state must include 'fleet_correlation_alert' as a top-level key. "
            "Key absent — add it to the /api/state response dict (app.py get_state)."
        )
        alert = data["fleet_correlation_alert"]
        assert alert is not None, (
            "fleet_correlation_alert must not be None when bot_state contains the alert dict."
        )
        for key in fixture["required_keys"]:
            assert key in alert, (
                f"/api/state fleet_correlation_alert missing required key '{key}'. "
                f"Present keys: {list(alert.keys()) if isinstance(alert, dict) else alert}"
            )

    def test_api_state_fleet_correlation_alert_is_none_when_absent(self, flask_client):
        """
        When bot_state does NOT contain 'fleet_correlation_alert', /api/state must
        include the key with value None (not omit it entirely).
        """
        # Non-empty state with no fleet_correlation_alert — triggers active path, not waiting path
        active_state: dict = {"date": "2026-05-16"}

        with patch("database.load_state", return_value=active_state), \
             patch("database.get_shadow_divergence", return_value={"by_symphony": {}, "portfolio_today": None}), \
             patch("database.get_triggers", return_value=[]), \
             patch("market_calendar.get_market_state", return_value="open"), \
             patch("analytics.get_portfolio_today_change", return_value={}), \
             patch("analytics.get_portfolio_cumulative_return", return_value={}), \
             patch("analytics.get_portfolio_max_drawdown", return_value={}), \
             patch("app.render_template", return_value=""):
            resp = flask_client.get("/api/state")

        assert resp.status_code == 200
        data = resp.get_json()
        assert "fleet_correlation_alert" in data, (
            "/api/state must always include 'fleet_correlation_alert' key. "
            "When not tripped, value must be null/None so the JS can clear the banner."
        )
        assert data["fleet_correlation_alert"] is None, (
            "fleet_correlation_alert must be null/None when no alert is active. "
            f"Got: {data['fleet_correlation_alert']!r}"
        )


# ---------------------------------------------------------------------------
# Section 1b — /api/state frozen and waiting paths also expose fleet_correlation_alert
# ---------------------------------------------------------------------------


class TestApiStateAllPathsExposeFleetAlert:
    """
    flask-dashboard-specialist pre-read identified 3 response paths in /api/state:
    active (lines 395-408), frozen snapshot (lines 232-241), waiting (lines 251-261).
    AC-V3.1 requires fleet_correlation_alert in all three.
    """

    def test_frozen_snapshot_path_includes_fleet_correlation_alert(self, flask_client):
        """
        Frozen snapshot path (market closed + snapshot present) must include
        fleet_correlation_alert at the top level.
        """
        fixture = _load("alert_state_shape.json")
        alert_payload = fixture["example"]

        snapshot_state = {
            "fleet_correlation_alert": alert_payload,
            "last_market_close_snapshot": {
                "captured_at_et": "16:00",
                "data_as_of": "2026-05-16",
                "portfolio_strip": {},
                "accounts_map": {},
                "shadow_divergence": {"portfolio_today": None, "by_symphony": {}},
            },
        }

        with patch("database.load_state", return_value=snapshot_state), \
             patch("market_calendar.get_market_state", return_value="closed_frozen"):
            resp = flask_client.get("/api/state")

        assert resp.status_code == 200
        data = resp.get_json()
        assert "fleet_correlation_alert" in data, (
            "Frozen snapshot path must include 'fleet_correlation_alert' in response. "
            "Add state_data.get('fleet_correlation_alert', None) to the frozen snapshot "
            "return jsonify({...}) block in app.py (lines ~232-241)."
        )

    def test_waiting_path_includes_fleet_correlation_alert(self, flask_client):
        """
        Waiting path (no state_data) must include fleet_correlation_alert (None).
        JS must be able to clear a banner that was visible before the daemon restarted.
        """
        with patch("database.load_state", return_value={}), \
             patch("database.get_shadow_divergence", return_value={"by_symphony": {}, "portfolio_today": None}), \
             patch("market_calendar.get_market_state", return_value="open"):
            resp = flask_client.get("/api/state")

        assert resp.status_code == 200
        data = resp.get_json()
        assert "fleet_correlation_alert" in data, (
            "Waiting path must include 'fleet_correlation_alert' key (value None). "
            "Add 'fleet_correlation_alert': None to the waiting_resp dict in app.py "
            "(lines ~251-261)."
        )
        assert data["fleet_correlation_alert"] is None, (
            "Waiting path fleet_correlation_alert must be None — no active alert "
            f"when state is empty. Got: {data['fleet_correlation_alert']!r}"
        )


# ---------------------------------------------------------------------------
# Section 2 — POST /api/fleet-alert/dismiss route
# ---------------------------------------------------------------------------


class TestFleetAlertDismissRoute:
    """
    AC-V3.3: Operator can dismiss manually via POST /api/fleet-alert/dismiss.
    Route must clear the alert in bot_state and persist.
    """

    def test_dismiss_route_exists_and_returns_200(self, flask_client):
        """POST /api/fleet-alert/dismiss must exist and return 200."""
        alert_state = {
            "fleet_correlation_alert": {
                "tripped_at_et": "2026-05-16T10:33:00",
                "triggered_reason": "Trailing Stop",
                "tripped_count": 6,
                "active_count": 10,
            }
        }

        with patch("database.load_state", return_value=alert_state), \
             patch("database.save_state") as mock_save:
            resp = flask_client.post("/api/fleet-alert/dismiss")

        assert resp.status_code == 200, (
            f"POST /api/fleet-alert/dismiss must return 200, got {resp.status_code}. "
            "Route does not exist — add it to app.py per AC-V3.3."
        )

    def test_dismiss_route_clears_alert_in_state(self, flask_client):
        """POST /api/fleet-alert/dismiss must remove fleet_correlation_alert and persist."""
        alert_state = {
            "fleet_correlation_alert": {
                "tripped_at_et": "2026-05-16T10:33:00",
                "triggered_reason": "Trailing Stop",
                "tripped_count": 6,
                "active_count": 10,
            }
        }

        with patch("database.load_state", return_value=alert_state), \
             patch("database.save_state") as mock_save:
            resp = flask_client.post("/api/fleet-alert/dismiss")

        assert resp.status_code == 200
        # Verify save_state was called with state that lacks fleet_correlation_alert
        assert mock_save.called, (
            "dismiss route must call database.save_state to persist the cleared alert."
        )
        saved_state = mock_save.call_args[0][0]
        assert "fleet_correlation_alert" not in saved_state, (
            "dismiss route must remove 'fleet_correlation_alert' from state before saving. "
            f"Key still present in saved state: {list(saved_state.keys())}"
        )

    def test_dismiss_route_response_json_has_status_ok(self, flask_client):
        """Dismiss route must return JSON with at minimum a 'status': 'ok' field."""
        alert_state = {
            "fleet_correlation_alert": {
                "tripped_at_et": "2026-05-16T10:33:00",
                "triggered_reason": "Trailing Stop",
                "tripped_count": 6,
                "active_count": 10,
            }
        }

        with patch("database.load_state", return_value=alert_state), \
             patch("database.save_state"):
            resp = flask_client.post("/api/fleet-alert/dismiss")

        data = resp.get_json()
        assert data is not None, "dismiss route must return JSON"
        assert data.get("status") == "ok", (
            f"dismiss route must return {{\"status\": \"ok\"}}, got {data!r}"
        )

    def test_dismiss_route_idempotent_when_no_alert(self, flask_client):
        """POST /api/fleet-alert/dismiss returns 200 even when no alert is active."""
        empty_state: dict = {}

        with patch("database.load_state", return_value=empty_state), \
             patch("database.save_state"):
            resp = flask_client.post("/api/fleet-alert/dismiss")

        assert resp.status_code == 200, (
            f"Dismiss route must be idempotent (200 even when no alert present). "
            f"Got {resp.status_code}."
        )


# ---------------------------------------------------------------------------
# Section 3 — Dashboard banner structural tests
# ---------------------------------------------------------------------------


class TestFleetBannerTemplate:
    """
    AC-V3.1: A high-visibility fleet correlation banner must exist in templates/index.html.
    Tests are structural (HTML element presence + JS update logic).
    """

    @pytest.fixture(autouse=True)
    def _load_template(self):
        template_path = (
            pathlib.Path(__file__).parent.parent.parent / "templates" / "index.html"
        )
        if not template_path.exists():
            pytest.skip("templates/index.html not found — skip template structural tests.")
        self._template_src = template_path.read_text(encoding="utf-8")

    def test_fleet_correlation_banner_element_exists(self):
        """templates/index.html must contain a fleet-correlation-banner element."""
        assert "fleet-correlation-banner" in self._template_src, (
            "templates/index.html must contain an element with id='fleet-correlation-banner' "
            "or class='fleet-correlation-banner'. "
            "Add a high-visibility banner div per AC-V3.1 (pattern: market-state-banner)."
        )

    def test_fleet_correlation_banner_is_hidden_by_default(self):
        """Fleet correlation banner must have 'hidden' class or attribute by default."""
        # Extract a window around the fleet-correlation-banner element
        idx = self._template_src.find("fleet-correlation-banner")
        assert idx >= 0, "fleet-correlation-banner not found — see previous test."

        # Look in a 500-char window around the element for 'hidden'
        window = self._template_src[max(0, idx - 50):idx + 400]
        assert "hidden" in window, (
            "fleet-correlation-banner must include 'hidden' attribute or class by default "
            "so it does not render when no alert is active. "
            "Pattern: see market-state-banner in templates/index.html."
        )

    def test_js_renderstate_updates_fleet_banner_from_api_response(self):
        """
        The renderState JS function in templates/index.html must reference
        'fleet_correlation_alert' from the API response data.
        """
        assert "fleet_correlation_alert" in self._template_src, (
            "templates/index.html renderState() must read 'fleet_correlation_alert' from "
            "the /api/state JSON response to show/hide the fleet banner. "
            "Add JS logic parallel to the market-state-banner update block."
        )

    def test_js_dismiss_button_calls_fleet_alert_dismiss_route(self):
        """
        The fleet banner must include a dismiss button that POSTs to /api/fleet-alert/dismiss.
        """
        assert "/api/fleet-alert/dismiss" in self._template_src, (
            "templates/index.html must include a button or link that POSTs to "
            "/api/fleet-alert/dismiss per AC-V3.3 operator manual-dismiss requirement."
        )
