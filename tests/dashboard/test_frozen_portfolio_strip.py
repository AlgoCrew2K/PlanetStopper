"""
RED tests for R13 deferred LOW — frozen portfolio_strip all-None when market closed.

Bug: alpha_bot_execution.py EOD capture block stores portfolio_strip with three
hard-coded None values (today_change, cumulative_return, max_drawdown). The comment
says "the live analytics path in app.py computes these" but the /api/state
frozen-branch (app.py:258) does snapshot.get("portfolio_strip") directly —
it never recomputes from analytics. Result: dashboard shows '---' for all
portfolio-level stats after market close.

Root cause: BOTH Case A and Case B.
  Case A — EOD post-mortem captures portfolio_strip as all-None (no analytics call).
  Case B — /api/state frozen-branch returns the None values verbatim (no recomputation).

Fix path: Case A only (correct approach).
  At EOD snapshot capture, call analytics.get_portfolio_today_change / cumulative_return /
  max_drawdown with the live symphony_data_cache and bot_state, and store results in
  portfolio_strip. Case B is already wired correctly (line 258 passes through the snapshot
  value); once Case A stores real data, the frozen-branch renders correctly.

Acceptance criteria:
  AC-PS.1  : /api/state closed_frozen + snapshot with populated portfolio_strip →
             response.portfolio_strip has non-None dict values.
  AC-PS.2  : /api/state closed_frozen + snapshot with portfolio_strip=None →
             response.portfolio_strip is None (no crash, no fabrication).
  AC-PS.3  : /api/state closed_frozen + no snapshot → existing "No closing snapshot"
             notice preserved; no regression.
  AC-PS.4  : /api/state market open → live path portfolio_strip unaffected (no regression).
  AC-PS.5  : EOD capture site — after capture, last_market_close_snapshot.portfolio_strip
             is populated with the analytics return values (not all-None).

Fixture provenance:
  tests/fixtures/dashboard/frozen_portfolio_strip/ — schema-derived from
  alpha_bot_execution.py EOD snapshot block and analytics.py portfolio functions.
  PA-18: provenance comment inline below.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Fixture dir
# ---------------------------------------------------------------------------

_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "dashboard" / "frozen_portfolio_strip"
)


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
        mock_analytics.get_portfolio_today_change.return_value = {"if_held": 1.5, "dry_run": 1.3}
        mock_analytics.get_portfolio_cumulative_return.return_value = {
            "if_held": 12.0,
            "dry_run": 11.5,
        }
        mock_analytics.get_portfolio_max_drawdown.return_value = {"if_held": -5.2, "dry_run": -4.9}
        mock_analytics.get_symphony_today_change.return_value = {"if_held": 1.5, "dry_run": 1.3}
        mock_analytics.get_symphony_cumulative_return.return_value = {
            "if_held": 12.0,
            "dry_run": 11.5,
        }
        mock_analytics.get_symphony_max_drawdown.return_value = {"if_held": -5.2, "dry_run": -4.9}
        with app_module.app.test_client() as client:
            yield client, app_module


# ===========================================================================
# AC-PS.1: closed_frozen + snapshot with populated portfolio_strip → non-None values
# ===========================================================================


class TestFrozenPortfolioStripPopulated:
    """
    Core regression: frozen path must return real portfolio_strip values,
    not all-None when a snapshot with data is available.

    These tests use the 'all_none' fixture — the actual buggy state captured from
    the live DB — to confirm the fix routes through the snapshot's portfolio_strip.
    After the fix (EOD capture calls analytics), new snapshots will have real values
    and the frozen path will surface them correctly.
    """

    def test_frozen_portfolio_strip_passthrough_today_change(self, flask_client, monkeypatch):
        """
        AC-PS.1: /api/state frozen path passes through snapshot.portfolio_strip verbatim.
        Uses 'populated' fixture to confirm non-None values survive the route.
        """
        client, app_module = flask_client
        fx = _load("frozen_portfolio_strip_populated")

        bot_state = {
            "date": "2026-05-16",
            "last_market_close_snapshot": fx["snapshot_input"],
        }

        with patch.object(app_module, "database") as mock_db:
            mock_db.load_state.return_value = bot_state
            mock_db.get_shadow_divergence.return_value = {
                "by_symphony": {},
                "portfolio_today": None,
            }
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )

            resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()
        ps = body.get("portfolio_strip")

        assert ps is not None, (
            "/api/state.portfolio_strip must not be None when snapshot has populated data."
        )
        assert isinstance(ps, dict), f"portfolio_strip must be a dict, got {type(ps).__name__!r}"
        assert ps.get("today_change") is not None, (
            f"portfolio_strip.today_change must not be None when snapshot.portfolio_strip "
            f"has real values. Got: {ps!r}. "
            "Frozen route must pass through snapshot values; EOD capture must store real analytics."
        )

    def test_frozen_portfolio_strip_passthrough_cumulative_return(self, flask_client, monkeypatch):
        """AC-PS.1: cumulative_return passes through from snapshot to /api/state."""
        client, app_module = flask_client
        fx = _load("frozen_portfolio_strip_populated")

        bot_state = {
            "date": "2026-05-16",
            "last_market_close_snapshot": fx["snapshot_input"],
        }

        with patch.object(app_module, "database") as mock_db:
            mock_db.load_state.return_value = bot_state
            mock_db.get_shadow_divergence.return_value = {
                "by_symphony": {},
                "portfolio_today": None,
            }
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )

            resp = client.get("/api/state")

        body = resp.get_json()
        ps = body.get("portfolio_strip")
        assert ps is not None and ps.get("cumulative_return") is not None, (
            f"portfolio_strip.cumulative_return must not be None when snapshot is populated. "
            f"Got portfolio_strip={ps!r}"
        )

    def test_frozen_portfolio_strip_passthrough_max_drawdown(self, flask_client, monkeypatch):
        """AC-PS.1: max_drawdown passes through from snapshot to /api/state."""
        client, app_module = flask_client
        fx = _load("frozen_portfolio_strip_populated")

        bot_state = {
            "date": "2026-05-16",
            "last_market_close_snapshot": fx["snapshot_input"],
        }

        with patch.object(app_module, "database") as mock_db:
            mock_db.load_state.return_value = bot_state
            mock_db.get_shadow_divergence.return_value = {
                "by_symphony": {},
                "portfolio_today": None,
            }
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )

            resp = client.get("/api/state")

        body = resp.get_json()
        ps = body.get("portfolio_strip")
        assert ps is not None and ps.get("max_drawdown") is not None, (
            f"portfolio_strip.max_drawdown must not be None when snapshot is populated. "
            f"Got portfolio_strip={ps!r}"
        )


# ===========================================================================
# AC-PS.2 (revised): snapshot with portfolio_strip=None → recompute from
#                    accounts_map; no crash; response is not all-None
# ===========================================================================


class TestFrozenPortfolioStripNullRecomputed:
    """
    AC-PS.2 (revised for on-the-fly recompute design):
    When snapshot.portfolio_strip is None (legacy snapshot captured under the
    old all-None code), /api/state must still produce real values by recomputing
    from accounts_map via analytics. The old "no fabrication" assertion was written
    for the pass-through design; it is superseded by AC-OF.2 (recompute is
    authoritative). No crash is still required.
    """

    def test_frozen_portfolio_strip_none_in_snapshot_recomputes_from_accounts_map(
        self, flask_client, monkeypatch
    ):
        """AC-PS.2: snapshot with portfolio_strip=None + accounts_map populated →
        response.portfolio_strip is non-None (recomputed, not passed through)."""
        client, app_module = flask_client
        fx = _load("frozen_portfolio_strip_populated")

        snapshot = dict(fx["snapshot_input"])
        snapshot["portfolio_strip"] = None

        bot_state = {
            "date": "2026-05-16",
            "last_market_close_snapshot": snapshot,
        }

        with patch.object(app_module, "database") as mock_db:
            mock_db.load_state.return_value = bot_state
            mock_db.get_shadow_divergence.return_value = {
                "by_symphony": {},
                "portfolio_today": None,
            }
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )

            resp = client.get("/api/state")

        assert resp.status_code == 200, (
            f"Frozen path must not crash when snapshot.portfolio_strip is None. "
            f"Status: {resp.status_code}"
        )
        body = resp.get_json()
        ps = body.get("portfolio_strip")
        assert ps is not None, (
            f"When snapshot.portfolio_strip is None but accounts_map is populated, "
            f"response.portfolio_strip must be recomputed (non-None). Got: {ps!r}. "
            "Recompute is authoritative — captured None must not pass through."
        )
        assert isinstance(ps, dict), (
            f"portfolio_strip must be a dict after recompute, got {type(ps).__name__!r}"
        )


# ===========================================================================
# AC-PS.3: no snapshot → existing notice preserved (no regression)
# ===========================================================================


class TestNoSnapshotNoticePreserved:
    """
    AC-PS.3: Fresh deploy (no snapshot) — existing "No closing snapshot yet" notice
    must still be returned. This is the pre-existing behavior from AC-DM.3.4.
    """

    def test_no_snapshot_notice_preserved(self, flask_client, monkeypatch):
        """AC-PS.3: closed_frozen + no snapshot → notice field present, no crash."""
        client, app_module = flask_client

        with patch.object(app_module, "database") as mock_db:
            mock_db.load_state.return_value = {}
            mock_db.get_shadow_divergence.return_value = {
                "by_symphony": {},
                "portfolio_today": None,
            }
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )

            resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()
        notice = body.get("notice")
        assert notice is not None and "snapshot" in notice.lower(), (
            f"AC-DM.3.4 notice must still be present on fresh deploy. Got: {notice!r}"
        )


# ===========================================================================
# AC-PS.4: live path (market open) → portfolio_strip unaffected (no regression)
# ===========================================================================


class TestOpenMarketPortfolioStripNoRegression:
    """
    AC-PS.4: market_state='open' must still compute portfolio_strip via analytics
    (live path). The fix must not touch the live branch.
    """

    def test_open_market_portfolio_strip_is_computed(self, flask_client, monkeypatch):
        """AC-PS.4: open market → portfolio_strip present and not all-None."""
        client, app_module = flask_client

        live_state = {
            "date": "2026-05-16",
            "sym-live": {
                "name": "Live Symphony",
                "account": "ACC-INDIVIDUAL",
                "current_return": 3.1,
                "armed": True,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
            },
        }

        with (
            patch.object(app_module, "database") as mock_db,
            patch.object(app_module, "dotenv_values", return_value={}),
            patch.object(app_module, "render_template", return_value=""),
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
        ps = body.get("portfolio_strip")
        assert ps is not None, "Live path portfolio_strip must not be None. No regression allowed."
        assert body.get("frozen_at") is None, "frozen_at must be null when market is open"


# ===========================================================================
# AC-PS.5: EOD capture site populates portfolio_strip (engine-side test)
# ===========================================================================


class TestEodCapturePopulatesPortfolioStrip:
    """
    AC-PS.5: After the EOD post-mortem snapshot capture fires, the stored
    last_market_close_snapshot.portfolio_strip must contain real values from
    analytics, not hard-coded Nones.

    Tests verify:
    (a) alpha_bot_execution imports analytics (the module-level dependency exists).
    (b) The analytics portfolio functions return non-None values for the fixture
        data — confirming the capture path can produce real data.
    (c) The computed portfolio_strip differs from the all-None buggy state.

    Fixture provenance:
      PA-18 — schema-derived from alpha_bot_execution.py EOD capture block
      and analytics.py _value_weighted_portfolio signature.
      eod_symphonies_flat matches fetch_symphony_stats() return shape:
      'id', 'last_percent_change', 'value' (current_value) keys.
    """

    def test_alpha_bot_execution_imports_analytics(self):
        """
        AC-PS.5 precondition: alpha_bot_execution must import analytics.
        Without this import, the EOD analytics calls would NameError.
        """
        import alpha_bot_execution as abe_module

        assert hasattr(abe_module, "analytics"), (
            "alpha_bot_execution must import analytics for EOD portfolio_strip computation. "
            "Add 'import analytics' to alpha_bot_execution.py."
        )

    def test_eod_analytics_returns_non_none_for_valued_symphonies(self):
        """
        AC-PS.5: analytics portfolio functions return non-None values when
        symphonies have positive value — confirming capture can store real data.

        Fixture: eod_symphonies_flat from frozen_portfolio_strip_populated.json.

        Fixture provenance:
          PA-18 — schema-derived from alpha_bot_execution.py and analytics.py.
          Symphonies have 'value' > 0 and 'last_percent_change' set.
        """
        import analytics as analytics_module

        fx = _load("frozen_portfolio_strip_populated")
        symphonies_flat = fx["eod_symphonies_flat"]
        trading_day = "2026-05-16"

        # Build bot_state with current_value > 0 so _value_weighted_portfolio
        # includes these symphonies in the aggregate.
        bot_state: dict = {"date": trading_day}
        for sym in symphonies_flat:
            bot_state[sym["id"]] = {
                "name": sym.get("name", sym["id"]),
                "account": sym.get("account", "ACC-TEST"),
                "current_value": sym.get("value", 10000.0),
                "current_return": sym.get("last_percent_change", 0.0) * 100,
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
            }

        with (
            patch.object(analytics_module, "get_symphony_today_change") as mock_tc,
            patch.object(analytics_module, "get_symphony_cumulative_return") as mock_cr,
            patch.object(analytics_module, "get_symphony_max_drawdown") as mock_mdd,
        ):
            mock_tc.return_value = {"if_held": 1.5, "dry_run": 1.3}
            mock_cr.return_value = {"if_held": 12.0, "dry_run": 11.5}
            mock_mdd.return_value = {"if_held": -5.2, "dry_run": -4.9}

            computed_strip = {
                "today_change": analytics_module.get_portfolio_today_change(
                    symphonies_flat, bot_state, trading_day=trading_day
                ),
                "cumulative_return": analytics_module.get_portfolio_cumulative_return(
                    symphonies_flat, bot_state, trading_day=trading_day
                ),
                "max_drawdown": analytics_module.get_portfolio_max_drawdown(
                    symphonies_flat, bot_state, trading_day=trading_day
                ),
            }

        assert computed_strip["today_change"] is not None, (
            "analytics.get_portfolio_today_change must return non-None when symphonies "
            "have value > 0. today_change was None — check _value_weighted_portfolio logic."
        )
        assert computed_strip["cumulative_return"] is not None, (
            "analytics.get_portfolio_cumulative_return must return non-None when symphonies "
            "have value > 0."
        )
        assert computed_strip["max_drawdown"] is not None, (
            "analytics.get_portfolio_max_drawdown must return non-None when symphonies "
            "have value > 0."
        )

        buggy_strip = {"today_change": None, "cumulative_return": None, "max_drawdown": None}
        assert computed_strip != buggy_strip, (
            "Computed portfolio_strip must differ from all-None buggy strip. "
            "Fix: replace hard-coded Nones with analytics calls in EOD capture block."
        )
