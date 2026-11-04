"""
The frozen (closed) `/api/state` branch reads the snapshot's own trading day.

The branch serves a stored close snapshot. Its per-symphony analytics reads (today change,
cumulative return, max drawdown) are made for the snapshot's `trading_day`, so a snapshot taken on
one day is never read against another day's rows.

Behaviour tested: analytics helpers are called with trading_day=snapshot["trading_day"].

Fixture provenance:
  tests/fixtures/dashboard/frozen_html/frozen_html_with_symphonies.json
  Hand-authored, schema-derived from alpha_bot_execution.py EOD snapshot writer
  and app.py:300-421 live-path enrichment pattern. PA-18.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------

_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "dashboard" / "frozen_html"


def _load(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Flask test-client fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def flask_client():
    """Flask test client with minimal stubs. No live DB; no live network."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with (
        patch.object(app_module, "analytics") as mock_analytics,
        patch.object(app_module, "schedule"),
    ):
        mock_analytics.get_portfolio_today_change.return_value = None
        mock_analytics.get_portfolio_cumulative_return.return_value = None
        mock_analytics.get_portfolio_max_drawdown.return_value = None
        mock_analytics.get_symphony_today_change.return_value = {"if_held": None, "dry_run": None}
        mock_analytics.get_symphony_cumulative_return.return_value = {
            "if_held": None,
            "dry_run": None,
        }
        mock_analytics.get_symphony_max_drawdown.return_value = {"if_held": None, "dry_run": None}
        with app_module.app.test_client() as client:
            yield client, app_module


def _make_db_mock(bot_state: dict) -> MagicMock:
    """Return a database mock configured for a frozen-path request."""
    mock_db = MagicMock()
    mock_db.load_state.return_value = bot_state
    mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
    mock_db.get_triggers.return_value = []
    mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
    mock_db.read_fleet_alert.return_value = None
    return mock_db


# ===========================================================================
# AC-H3: analytics helpers called with trading_day=snapshot["trading_day"]
# ===========================================================================


class TestAnalyticsCalledWithSnapshotTradingDay:
    """
    AC-H3: get_symphony_today_change / cumulative_return / max_drawdown must be called
    with trading_day=snapshot["trading_day"], not today's date.
    R14 contract: analytics reads shadow_history keyed on trading_day.
    """

    def test_analytics_helpers_called_with_snapshot_trading_day(self, flask_client, monkeypatch):
        """AC-H3: each analytics helper receives trading_day equal to snapshot.trading_day."""
        client, app_module = flask_client
        fx = _load("frozen_html_with_symphonies")
        snapshot_trading_day = fx["snapshot_input"]["trading_day"]  # "2026-05-16"

        bot_state = {
            "date": "2026-05-16",
            "last_market_close_snapshot": fx["snapshot_input"],
        }

        # Use the already-patched mock_analytics from the fixture
        # We need to capture calls, so re-patch analytics inside this test
        with (
            patch.object(app_module, "database", _make_db_mock(bot_state)),
            patch.object(app_module, "analytics") as mock_analytics,
        ):
            mock_analytics.get_symphony_today_change.return_value = {
                "if_held": None,
                "dry_run": None,
            }
            mock_analytics.get_symphony_cumulative_return.return_value = {
                "if_held": None,
                "dry_run": None,
            }
            mock_analytics.get_symphony_max_drawdown.return_value = {
                "if_held": None,
                "dry_run": None,
            }
            # on-the-fly portfolio_strip recompute also calls portfolio-level helpers;
            # stub them so jsonify doesn't 500 on MagicMock serialisation
            mock_analytics.get_portfolio_today_change.return_value = {
                "if_held": None,
                "dry_run": None,
            }
            mock_analytics.get_portfolio_cumulative_return.return_value = {
                "if_held": None,
                "dry_run": None,
            }
            mock_analytics.get_portfolio_max_drawdown.return_value = {
                "if_held": None,
                "dry_run": None,
            }

            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )
            resp = client.get("/api/state")

        assert resp.status_code == 200

        # Verify trading_day kwarg was snapshot's trading_day on every analytics call
        for mock_fn_name in (
            "get_symphony_today_change",
            "get_symphony_cumulative_return",
            "get_symphony_max_drawdown",
        ):
            mock_fn = getattr(mock_analytics, mock_fn_name)
            assert mock_fn.call_count > 0, (
                f"analytics.{mock_fn_name} must be called at least once for frozen rendering. "
                f"call_count={mock_fn.call_count}"
            )
            for c in mock_fn.call_args_list:
                actual_td = c.kwargs.get("trading_day")
                assert actual_td == snapshot_trading_day, (
                    f"analytics.{mock_fn_name} must be called with "
                    f"trading_day={snapshot_trading_day!r} (snapshot.trading_day), "
                    f"got trading_day={actual_td!r}. "
                    "R14 contract: analytics reads shadow_history keyed on trading_day."
                )
