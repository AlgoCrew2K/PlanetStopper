"""
RED — /api/strip/<window> LIVE route integration (NO analytics mock).

THE BUG (reviewer BLOCK + PM directive, 2026-06-04): the AC-3 windowed-strip tests in
test_hero_chart_and_windowed_strip.py mock the ENTIRE analytics module
(`patch.object(app_module, "analytics", analytics_mock)`), so they pass even though
`analytics.compute_windowed_portfolio_strip` is called at app.py:1771 but NEVER DEFINED in
analytics.py. Live, the missing function raises AttributeError, the route's
`except Exception` (app.py:1775) swallows it, and /api/strip/<window> returns HTTP 500 — the
time picker (the user's MANDATORY feature) is non-functional in production while the unit
suite is green. This is the exact tested-green-but-fails-live trap (feedback_verify_feature_works_live).

THE CONTRACT: /api/strip/<window> must return a valid 200 windowed strip for EVERY window
token — exercising the REAL analytics.compute_windowed_portfolio_strip (NOT a mock). The
database is the only thing mocked (a fixture bot_state, read-only); analytics is REAL so a
missing/unwired function is caught.

These FAIL today (the function is missing -> 500) and pass only when
compute_windowed_portfolio_strip is genuinely implemented + wired.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import analytics
import app as app_module

# All picker window tokens incl the NEW All-Time. Kept in sync with the route's
# _STRIP_WINDOW_TOKENS — if the route adds/removes a token this list must follow.
_WINDOW_TOKENS = ["30d", "60d", "90d", "125d", "ytd", "1y", "all"]


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _fixture_bot_state():
    """A minimal but realistic bot_state with two name-bearing symphonies (so the route
    builds a non-empty symphony list) plus a phantom non-name key (which must be ignored)."""
    return {
        "sym-a": {
            "name": "Alpha", "account": "ACC1", "current_value": 6000.0,
            "current_return": 1.5, "simple_return": 0.12, "net_deposits": 1000.0,
            "time_weighted_return": 0.12, "max_drawdown": 0.08,
            "armed": True, "tp_armed": False, "para_armed": False, "triggered": False,
        },
        "sym-b": {
            "name": "Bravo", "account": "ACC1", "current_value": 4000.0,
            "current_return": -0.5, "simple_return": 0.08, "net_deposits": 800.0,
            "time_weighted_return": 0.08, "max_drawdown": 0.05,
            "armed": False, "tp_armed": False, "para_armed": False, "triggered": False,
        },
        "last_market_close_snapshot": {"ts": "2026-06-04T20:00:00Z", "value": 10000.0},
    }


# ===========================================================================
# The function must EXIST in analytics (the literal missing-symbol the BLOCK named)
# ===========================================================================

class TestWindowedStripFunctionExists:
    def test_compute_windowed_portfolio_strip_is_defined(self):
        assert hasattr(analytics, "compute_windowed_portfolio_strip"), (
            "BLOCK: analytics.compute_windowed_portfolio_strip is not defined, but "
            "app.py:1771 calls it -> /api/strip/<window> 500s live. The AC-3 unit tests "
            "mock analytics so they never caught this."
        )
        assert callable(analytics.compute_windowed_portfolio_strip), (
            "analytics.compute_windowed_portfolio_strip must be callable."
        )

    def test_compute_windowed_symphony_guard_alpha_is_defined(self):
        assert hasattr(analytics, "compute_windowed_symphony_guard_alpha"), (
            "BLOCK: analytics.compute_windowed_symphony_guard_alpha is not defined — the "
            "per-card windowed guard alpha (AC-2/AC-3) cannot be computed live."
        )


# ===========================================================================
# The LIVE route must 200 for every window — analytics REAL, only DB mocked
# ===========================================================================

class TestWindowedStripRouteLive:
    @pytest.mark.parametrize("window", _WINDOW_TOKENS)
    def test_strip_route_returns_200_not_500_for_every_window(self, client, window):
        """Hit /api/strip/<window> with the REAL analytics module (only database mocked).
        A missing/unwired compute_windowed_portfolio_strip raises -> the route's
        except-block returns 500. This asserts 200 for every token."""
        with patch.object(app_module.database, "load_state", return_value=_fixture_bot_state()):
            resp = client.get(f"/api/strip/{window}")
        assert resp.status_code == 200, (
            f"LIVE FAIL: /api/strip/{window} returned {resp.status_code} (expected 200). "
            "The route calls the REAL analytics.compute_windowed_portfolio_strip — a 500 "
            "means the function is missing/unwired/raising. The unit tests mock analytics so "
            "they cannot catch this. Body: " + resp.get_data(as_text=True)[:300]
        )

    @pytest.mark.parametrize("window", _WINDOW_TOKENS)
    def test_strip_route_payload_is_a_valid_windowed_strip(self, client, window):
        """The 200 payload must be a real windowed strip (not an {'error': ...} body) and
        echo the resolved window so the label matches the value."""
        with patch.object(app_module.database, "load_state", return_value=_fixture_bot_state()):
            resp = client.get(f"/api/strip/{window}")
        assert resp.status_code == 200, (
            f"/api/strip/{window} did not 200: {resp.get_data(as_text=True)[:200]}"
        )
        body = resp.get_json()
        assert isinstance(body, dict), f"strip payload must be a dict; got {type(body)}"
        assert "error" not in body, (
            f"LIVE FAIL: /api/strip/{window} returned an error payload {body!r} — the windowed "
            "strip computation failed."
        )
        # Shape: the strip the route/JS consume (renderGuardAlpha + updateComparisonRows).
        for key in ("cumulative_return", "window"):
            assert key in body, (
                f"strip payload missing '{key}'; got keys {list(body.keys())}. The route must "
                "return a portfolio-strip-shaped dict that echoes the resolved window."
            )
        assert str(body["window"]).lower() == window, (
            f"strip must echo the resolved window '{window}'; got {body['window']!r} — the UI "
            "label must match the windowed value (F1)."
        )

    def test_unknown_window_token_is_404_not_500(self, client):
        """A genuinely unknown token is a 404 (client error), distinct from the 500 that a
        broken implementation produces — so the 200-vs-500 assertions above are meaningful."""
        with patch.object(app_module.database, "load_state", return_value=_fixture_bot_state()):
            resp = client.get("/api/strip/not-a-window")
        assert resp.status_code == 404, (
            f"an unknown window token must be 404 (not 500); got {resp.status_code}."
        )
