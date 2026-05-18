"""
RED tests for dashboard route guard — AC-DRG.1-3.

Context:
  vars_locked_count loop in dashboard() iterated state.items() and only
  skipped "date". bot_state contains other non-symphony top-level keys
  (data_as_of: str, last_market_close_snapshot: dict, etc.). When sym_data
  is a non-dict, sym_data.get("name", "") raises AttributeError → 500.

Acceptance criteria:
  AC-DRG.1: dashboard() returns 200 when state contains mixed dict + non-dict
             top-level keys (data_as_of: str, last_market_close_snapshot: dict).
  AC-DRG.2: vars_locked_count still correctly counts only symphony entries.
  AC-DRG.3: non-dict entries are silently skipped (no exception propagates).
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture()
def flask_client():
    import app as app_module
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client, app_module


class TestDashboardRouteGuard:
    """AC-DRG.1 + AC-DRG.3: mixed state keys must not cause 500."""

    def test_returns_200_with_non_dict_state_values(self, flask_client):
        """AC-DRG.1: non-dict top-level values in state do not crash the route."""
        client, app_module = flask_client

        mixed_state = {
            "date": "2026-05-18",
            "data_as_of": "16:00 ET",
            "last_market_close_snapshot": {"trading_day": "2026-05-16"},
            "sym-alpha": {
                "name": "Alpha Momentum",
                "account": "ACC-1",
                "current_return": 2.0,
            },
        }

        mock_db = MagicMock()
        mock_db.load_state.return_value = mixed_state
        mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
        mock_db.get_symphony_strategy.return_value = None

        with patch.object(app_module, "database", mock_db):
            resp = client.get("/")

        assert resp.status_code == 200, (
            f"dashboard() must return 200 when state contains non-dict top-level values. "
            f"Got {resp.status_code}. Non-dict entries must be silently skipped."
        )

    def test_no_exception_on_string_state_value(self, flask_client):
        """AC-DRG.3: string value (e.g. data_as_of) does not propagate AttributeError."""
        client, app_module = flask_client

        state_with_string = {
            "date": "2026-05-18",
            "data_as_of": "16:00 ET",
        }

        mock_db = MagicMock()
        mock_db.load_state.return_value = state_with_string
        mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
        mock_db.get_symphony_strategy.return_value = None

        with patch.object(app_module, "database", mock_db):
            resp = client.get("/")

        assert resp.status_code == 200, (
            f"String state value must not raise AttributeError. Got {resp.status_code}."
        )


class TestVarsLockedCountAccuracy:
    """AC-DRG.2: count is correct despite mixed state."""

    def test_counts_only_symphony_entries(self, flask_client):
        """AC-DRG.2: vars_locked_count counts locked_vars from symphony entries only."""
        client, app_module = flask_client

        mixed_state = {
            "date": "2026-05-18",
            "data_as_of": "16:00 ET",
            "last_market_close_snapshot": {"trading_day": "2026-05-16"},
            "sym-alpha": {"name": "Alpha Momentum", "account": "ACC-1"},
            "sym-beta": {"name": "Beta Defensive", "account": "ACC-2"},
        }

        mock_db = MagicMock()
        mock_db.load_state.return_value = mixed_state
        mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
        mock_db.get_symphony_strategy.side_effect = lambda name: (
            {"locked_vars": ["TRIGGER_THRESHOLD_PCT", "MC_FLOOR"]} if "alpha" in name
            else None
        )

        with patch.object(app_module, "database", mock_db):
            resp = client.get("/")

        assert resp.status_code == 200
        # Verify get_symphony_strategy was NOT called for non-dict entries
        call_args = [c.args[0] for c in mock_db.get_symphony_strategy.call_args_list]
        assert all(isinstance(a, str) for a in call_args), (
            "get_symphony_strategy must only be called with string names from symphony dicts."
        )
        # Non-symphony keys must not produce strategy lookups for non-dict values
        assert len(call_args) == 2, (
            f"Expected 2 strategy lookups (one per symphony), got {len(call_args)}: {call_args}. "
            "Non-dict state values must be skipped."
        )
