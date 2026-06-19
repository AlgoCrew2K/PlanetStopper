"""
RED tests for frozen /api/state html rendering.

Bug: /api/state frozen branch (market_state: closed_frozen / pre_market) does NOT
include the "html" key.  JS at index.html:941-952 guards `if (data.html) morphdom(...)`.
When absent the container stays empty → symphony table is BLANK on frozen view.
Pre-existing since DM merged (commit 0092fa2, 2026-05-16).

Acceptance criteria tested:
  AC-H1 : frozen branch response includes "html" key (non-null, non-empty string).
  AC-H2 : rendered HTML contains <tr data-symphony-id> per snapshot symphony.
  AC-H3 : analytics helpers called with trading_day=snapshot["trading_day"].
  AC-H4 : data-symphony-id attribute present on each <tr> (R15 + R14 contract).
  AC-H5 : sortCol / sortDir query params honored (same logic as live branch).
  AC-H6 : no snapshot → html may be absent OR an empty table; response is not 500.
  AC-H7 : live (open) branch unchanged — html key still present (no regression).
  AC-H8 : pre_market path includes html key (same as closed_frozen).

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
# AC-H1: frozen response includes "html" key
# ===========================================================================


class TestFrozenResponseIncludesHtmlKey:
    """
    Core regression: frozen branch must include "html" in its JSON response.
    JS at index.html:941-952 guards `if (data.html) morphdom(...)` — when absent
    the symphony table stays blank.
    """

    def test_closed_frozen_with_snapshot_includes_html_key(self, flask_client, monkeypatch):
        """AC-H1: closed_frozen + snapshot → response.html is non-null, non-empty string."""
        client, app_module = flask_client
        fx = _load("frozen_html_with_symphonies")

        bot_state = {
            "date": "2026-05-16",
            "last_market_close_snapshot": fx["snapshot_input"],
        }

        with patch.object(app_module, "database", _make_db_mock(bot_state)):
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )
            resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()

        assert "html" in body, (
            "/api/state frozen response must include 'html' key. "
            f"Keys present: {list(body.keys())}. "
            "JS at index.html:941-952 guards `if (data.html) morphdom(...)` — "
            "when absent the symphony table is blank."
        )
        html_val = body["html"]
        assert html_val is not None and html_val != "", (
            f"/api/state.html must be a non-empty string, got {html_val!r}"
        )
        assert isinstance(html_val, str), (
            f"/api/state.html must be a string, got {type(html_val).__name__!r}"
        )

    def test_html_key_is_absent_or_safe_when_no_snapshot(self, flask_client, monkeypatch):
        """AC-H6: no snapshot → response is not 500; html may be absent or empty-table string."""
        client, app_module = flask_client

        with patch.object(app_module, "database", _make_db_mock({})):
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )
            resp = client.get("/api/state")

        # Must NOT be a server error
        assert resp.status_code == 200, (
            f"No-snapshot frozen path must not 500. Got {resp.status_code}."
        )
        body = resp.get_json()
        # If html is present, it must be a string (not None crashes morphdom guard)
        if "html" in body and body["html"] is not None:
            assert isinstance(body["html"], str), (
                f"html key when present must be a string, got {type(body['html']).__name__!r}"
            )


# ===========================================================================
# AC-H2 + AC-H4: rendered HTML contains <tr data-symphony-id> per symphony
# ===========================================================================


class TestRenderedHtmlContainsSymphonyRows:
    """
    AC-H2: HTML contains one <tr> per symphony from snapshot.
    AC-H4: each <tr> has data-symphony-id attribute (R15 hover-highlight contract).
    """

    def test_html_contains_tr_per_snapshot_symphony(self, flask_client, monkeypatch):
        """AC-H2 + AC-H4: rendered HTML has <tr data-symphony-id> for each snapshot symphony."""
        client, app_module = flask_client
        fx = _load("frozen_html_with_symphonies")

        bot_state = {
            "date": "2026-05-16",
            "last_market_close_snapshot": fx["snapshot_input"],
        }

        with patch.object(app_module, "database", _make_db_mock(bot_state)):
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )
            resp = client.get("/api/state")

        body = resp.get_json()
        html = body.get("html", "")

        assert isinstance(html, str) and html, (
            "html must be a non-empty string to contain symphony rows."
        )

        for sym_id in fx["expected"]["symphony_ids_in_html"]:
            assert f'data-symphony-id="{sym_id}"' in html, (
                f'Rendered HTML must contain <tr data-symphony-id="{sym_id}"> '
                f"for snapshot symphony {sym_id!r}. "
                f"HTML (first 500 chars): {html[:500]!r}"
            )


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


# ===========================================================================
# AC-H5: sortCol / sortDir query params honored
# ===========================================================================


class TestFrozenSortingHonored:
    """
    AC-H5: sortCol / sortDir query params must be passed through to table rendering,
    same as the live branch.
    """

    def test_sort_params_do_not_crash_frozen_path(self, flask_client, monkeypatch):
        """AC-H5: sortCol=status&sortDir=desc must return 200 with html key."""
        client, app_module = flask_client
        fx = _load("frozen_html_with_symphonies")

        bot_state = {
            "date": "2026-05-16",
            "last_market_close_snapshot": fx["snapshot_input"],
        }

        with patch.object(app_module, "database", _make_db_mock(bot_state)):
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )
            resp = client.get("/api/state?sortCol=status&sortDir=desc")

        assert resp.status_code == 200
        body = resp.get_json()
        assert "html" in body, (
            "html key must be present even when sort params are supplied. "
            f"Keys: {list(body.keys())}"
        )

    def test_default_sort_produces_html(self, flask_client, monkeypatch):
        """AC-H5: default sort (no params) must produce html key."""
        client, app_module = flask_client
        fx = _load("frozen_html_with_symphonies")

        bot_state = {
            "date": "2026-05-16",
            "last_market_close_snapshot": fx["snapshot_input"],
        }

        with patch.object(app_module, "database", _make_db_mock(bot_state)):
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )
            resp = client.get("/api/state")

        body = resp.get_json()
        assert body.get("html"), "Default-sort frozen path must return non-empty html."


# ===========================================================================
# AC-H7: live (open) branch unchanged — html key still present
# ===========================================================================


class TestLiveBranchUnchanged:
    """
    AC-H7: The live (open) path must still include html. No regression.
    """

    def test_open_market_html_key_still_present(self, flask_client, monkeypatch):
        """AC-H7: market_state='open' → html key present (live path not regressed)."""
        client, app_module = flask_client

        live_state = {
            "date": "2026-05-16",
            "sym-live": {
                "name": "Live Symphony",
                "account": "ACC-INDIVIDUAL",
                "current_return": 3.1,
                "current_value": 40000.0,
                "mc_prob": 60.0,
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
                "stop_trigger": -4.0,
                "shadow_hwm": 3.5,
                "breakeven_locked": False,
            },
        }

        with (
            patch.object(app_module, "database") as mock_db,
            patch.object(app_module, "dotenv_values", return_value={}),
        ):
            mock_db.load_state.return_value = dict(live_state)
            mock_db.get_shadow_divergence.return_value = {
                "by_symphony": {},
                "portfolio_today": None,
            }
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open", raising=False)

            resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()
        assert "html" in body, (
            "Live (open) path must still include html key. "
            f"Keys: {list(body.keys())}. Regression introduced by frozen-html fix."
        )
        assert body["html"] is not None and body["html"] != "", (
            f"Live path html must be non-empty. Got {body['html']!r}"
        )


# ===========================================================================
# AC-H8: pre_market path includes html key
# ===========================================================================


class TestPreMarketIncludesHtmlKey:
    """
    AC-H8: pre_market path shares the closed_frozen branch — must also return html.
    """

    def test_pre_market_with_snapshot_includes_html(self, flask_client, monkeypatch):
        """AC-H8: pre_market + snapshot → html key present in response."""
        client, app_module = flask_client
        fx = _load("frozen_html_with_symphonies")

        bot_state = {
            "date": "2026-05-16",
            "last_market_close_snapshot": fx["snapshot_input"],
        }

        with patch.object(app_module, "database", _make_db_mock(bot_state)):
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "pre_market", raising=False
            )
            resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()
        assert "html" in body, (
            f"pre_market frozen path must include html key. Keys: {list(body.keys())}"
        )
        assert isinstance(body["html"], str) and body["html"], (
            f"pre_market html must be a non-empty string, got {body['html']!r}"
        )


# ===========================================================================
# AC-H9: symphonies missing shadow_hwm must not crash frozen render
# ===========================================================================


class TestFrozenRenderWithMissingShadowHwm:
    """
    AC-H9: A snapshot symphony with shadow_hwm absent must render without a
    Jinja UndefinedError. Two guards must both be load-bearing:
      1. app.py _FROZEN_SYM_DEFAULTS setdefault("shadow_hwm", 0.0) normalises the dict.
      2. table_partial.html `| default(0)` prevents Undefined reaching | round(2).
    This is the exact field whose absence caused the operator-reported blank-table
    regression (commit 0092fa2). A test that passes with both guards removed is
    worthless — this test pins the behaviour the fix introduced.
    """

    def test_symphony_missing_shadow_hwm_renders_without_crash(self, flask_client, monkeypatch):
        """AC-H9: snapshot with shadow_hwm absent → 200, html key, symphony row present."""
        client, app_module = flask_client
        fx = _load("frozen_html_missing_shadow_hwm")

        bot_state = {
            "date": "2026-05-16",
            "last_market_close_snapshot": fx["snapshot_input"],
        }

        with patch.object(app_module, "database", _make_db_mock(bot_state)):
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )
            resp = client.get("/api/state")

        assert resp.status_code == 200, (
            f"Frozen render with missing shadow_hwm must not 500. "
            f"Got {resp.status_code}. "
            "Likely cause: _FROZEN_SYM_DEFAULTS setdefault removed or | default(0) guard removed."
        )
        body = resp.get_json()
        assert "html" in body, (
            "html key must be present even when snapshot symphonies lack shadow_hwm. "
            f"Keys: {list(body.keys())}"
        )
        html = body["html"]
        assert isinstance(html, str) and html, f"html must be a non-empty string, got {html!r}"
        for sym_id in fx["expected"]["symphony_ids_in_html"]:
            assert f'data-symphony-id="{sym_id}"' in html, (
                f'Rendered HTML must contain <tr data-symphony-id="{sym_id}"> '
                f"even when shadow_hwm is absent from the snapshot. "
                f"HTML (first 500 chars): {html[:500]!r}"
            )
