"""
RED tests for on-the-fly portfolio_strip recompute in /api/state frozen branch.

Context:
  R13's earlier fix (06c966a) was CAPTURE-SIDE: alpha_bot_execution.py EOD
  post-mortem now calls analytics at capture time. But:
    1. The existing in-DB snapshot was captured under the old all-None code.
       New capture-side code only fires at next EOD (16:00 ET tomorrow).
    2. Even after that fires, the fix is one-shot: any future capture glitch
       (network error, analytics exception) will produce all-None again and
       the operator sees '---' with no self-healing.

  Fix: /api/state frozen branch must recompute portfolio_strip on-the-fly
  from snapshot.accounts_map using analytics, regardless of the captured
  portfolio_strip value. Recompute is AUTHORITATIVE — not a fallback.

  Same pattern as the frozen-html fix (R2 state reconstruction):
  reconstruct at /api/state time, don't trust the captured value.

Acceptance criteria:
  AC-OF.1 : closed_frozen + snapshot with portfolio_strip all-None + accounts_map
            populated → response.portfolio_strip has non-None values (recomputed
            from accounts_map via analytics).
  AC-OF.2 : closed_frozen + snapshot with portfolio_strip already populated →
            response.portfolio_strip values come from on-the-fly analytics call,
            NOT from the captured snapshot.portfolio_strip (recompute is authoritative).
  AC-OF.3 : analytics.get_portfolio_today_change / cumulative_return / max_drawdown
            each called with trading_day=snapshot["trading_day"] (R14 contract).
  AC-OF.4 : closed_frozen + no snapshot → existing "No closing snapshot" notice
            preserved; no crash; no portfolio_strip key (or None).
  AC-OF.5 : live branch (market open) → portfolio_strip still computed from live
            analytics path; frozen on-the-fly logic not invoked (no regression).

Fixture provenance:
  tests/fixtures/dashboard/frozen_portfolio_strip/frozen_strip_onfly_all_none.json
  Hand-authored. Schema-derived from frozen_portfolio_strip_all_none.json (live
  capture from alphabot_state.db on 2026-05-18) and app.py accounts_map shape.
  PA-18.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture loader
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
        mock_analytics.get_portfolio_today_change.return_value = {"if_held": 2.1, "dry_run": 1.9}
        mock_analytics.get_portfolio_cumulative_return.return_value = {
            "if_held": 14.3,
            "dry_run": 13.8,
        }
        mock_analytics.get_portfolio_max_drawdown.return_value = {"if_held": -6.1, "dry_run": -5.7}
        mock_analytics.get_symphony_today_change.return_value = {"if_held": None, "dry_run": None}
        mock_analytics.get_symphony_cumulative_return.return_value = {
            "if_held": None,
            "dry_run": None,
        }
        mock_analytics.get_symphony_max_drawdown.return_value = {"if_held": None, "dry_run": None}
        with app_module.app.test_client() as client:
            yield client, app_module


def _make_db_mock(bot_state: dict) -> MagicMock:
    mock_db = MagicMock()
    mock_db.load_state.return_value = bot_state
    mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
    mock_db.get_triggers.return_value = []
    mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
    mock_db.read_fleet_alert.return_value = None
    return mock_db


# ===========================================================================
# AC-OF.1: all-None captured portfolio_strip → on-the-fly recompute returns
#          non-None values
# ===========================================================================


class TestOnFlyRecomputeWhenCapturedAllNone:
    """
    Core regression: even when the captured snapshot.portfolio_strip is all-None
    (the current live state), /api/state must call analytics and surface real values.
    Recompute is authoritative — not a fallback that only triggers on None.
    """

    def test_all_none_captured_strip_today_change_recomputed(self, flask_client, monkeypatch):
        """AC-OF.1: portfolio_strip.today_change is non-None when accounts_map populated."""
        client, app_module = flask_client
        fx = _load("frozen_strip_onfly_all_none")

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
        ps = body.get("portfolio_strip")

        assert ps is not None, (
            "/api/state.portfolio_strip must not be None when accounts_map is populated. "
            f"Keys: {list(body.keys())}. "
            "Frozen branch must recompute portfolio_strip on-the-fly from accounts_map."
        )
        assert ps.get("today_change") is not None, (
            "portfolio_strip.today_change must be non-None after on-the-fly recompute. "
            f"Got portfolio_strip={ps!r}. "
            "Captured all-None value must NOT be passed through — call analytics at /api/state time."
        )

    def test_all_none_captured_strip_cumulative_return_recomputed(self, flask_client, monkeypatch):
        """AC-OF.1: portfolio_strip.cumulative_return is non-None after recompute."""
        client, app_module = flask_client
        fx = _load("frozen_strip_onfly_all_none")

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
        ps = body.get("portfolio_strip")
        assert ps is not None and ps.get("cumulative_return") is not None, (
            "portfolio_strip.cumulative_return must be non-None after on-the-fly recompute. "
            f"Got portfolio_strip={ps!r}."
        )

    def test_all_none_captured_strip_max_drawdown_recomputed(self, flask_client, monkeypatch):
        """AC-OF.1: portfolio_strip.max_drawdown is non-None after recompute."""
        client, app_module = flask_client
        fx = _load("frozen_strip_onfly_all_none")

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
        ps = body.get("portfolio_strip")
        assert ps is not None and ps.get("max_drawdown") is not None, (
            "portfolio_strip.max_drawdown must be non-None after on-the-fly recompute. "
            f"Got portfolio_strip={ps!r}."
        )


# ===========================================================================
# AC-OF.2: recompute is authoritative — ignores pre-populated captured value
# ===========================================================================


class TestOnFlyRecomputeIsAuthoritative:
    """
    AC-OF.2: Even when the captured snapshot.portfolio_strip has non-None values,
    /api/state must call analytics and use the freshly-computed values — NOT the
    captured snapshot values. This makes the fix resilient to future capture glitches.

    The analytics mock returns sentinel values distinct from any snapshot fixture value,
    so if the test sees the mock's return value the recompute happened; if it sees the
    fixture's pre-captured value the pass-through path is still in use.
    """

    def test_recompute_overrides_pre_populated_today_change(self, flask_client, monkeypatch):
        """AC-OF.2: response.portfolio_strip.today_change comes from analytics, not snapshot."""
        client, app_module = flask_client
        fx = _load("frozen_strip_onfly_all_none")

        # Give the snapshot a pre-populated portfolio_strip with a distinct sentinel value.
        snapshot = dict(fx["snapshot_input"])
        snapshot["portfolio_strip"] = {
            "today_change": {"if_held": 99.9, "dry_run": 88.8},
            "cumulative_return": {"if_held": 77.7, "dry_run": 66.6},
            "max_drawdown": {"if_held": -55.5, "dry_run": -44.4},
        }

        bot_state = {
            "date": "2026-05-16",
            "last_market_close_snapshot": snapshot,
        }

        # analytics mock returns a different sentinel
        with (
            patch.object(app_module, "database", _make_db_mock(bot_state)),
            patch.object(app_module, "analytics") as mock_analytics,
        ):
            analytics_sentinel = {"if_held": 2.1, "dry_run": 1.9}
            mock_analytics.get_portfolio_today_change.return_value = analytics_sentinel
            mock_analytics.get_portfolio_cumulative_return.return_value = {
                "if_held": 14.3,
                "dry_run": 13.8,
            }
            mock_analytics.get_portfolio_max_drawdown.return_value = {
                "if_held": -6.1,
                "dry_run": -5.7,
            }
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

            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )
            resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()
        ps = body.get("portfolio_strip")

        assert ps is not None, (
            "portfolio_strip must not be None even when snapshot has pre-populated values."
        )
        assert ps.get("today_change") == analytics_sentinel, (
            f"portfolio_strip.today_change must equal analytics mock return value "
            f"{analytics_sentinel!r}, not the snapshot's pre-captured value. "
            f"Got: {ps.get('today_change')!r}. "
            "Recompute must be authoritative — snapshot.portfolio_strip must be ignored."
        )

    def test_recompute_calls_analytics_not_pass_through(self, flask_client, monkeypatch):
        """AC-OF.2: analytics.get_portfolio_today_change must be called on frozen path."""
        client, app_module = flask_client
        fx = _load("frozen_strip_onfly_all_none")

        bot_state = {
            "date": "2026-05-16",
            "last_market_close_snapshot": fx["snapshot_input"],
        }

        with (
            patch.object(app_module, "database", _make_db_mock(bot_state)),
            patch.object(app_module, "analytics") as mock_analytics,
        ):
            mock_analytics.get_portfolio_today_change.return_value = {
                "if_held": 2.1,
                "dry_run": 1.9,
            }
            mock_analytics.get_portfolio_cumulative_return.return_value = {
                "if_held": 14.3,
                "dry_run": 13.8,
            }
            mock_analytics.get_portfolio_max_drawdown.return_value = {
                "if_held": -6.1,
                "dry_run": -5.7,
            }
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

            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )
            resp = client.get("/api/state")

        assert resp.status_code == 200

        assert mock_analytics.get_portfolio_today_change.call_count >= 1, (
            "analytics.get_portfolio_today_change must be called on frozen path. "
            f"call_count={mock_analytics.get_portfolio_today_change.call_count}. "
            "Recompute is authoritative — analytics must be called, not snapshot passed through."
        )
        assert mock_analytics.get_portfolio_cumulative_return.call_count >= 1, (
            "analytics.get_portfolio_cumulative_return must be called on frozen path. "
            f"call_count={mock_analytics.get_portfolio_cumulative_return.call_count}."
        )
        assert mock_analytics.get_portfolio_max_drawdown.call_count >= 1, (
            "analytics.get_portfolio_max_drawdown must be called on frozen path. "
            f"call_count={mock_analytics.get_portfolio_max_drawdown.call_count}."
        )


# ===========================================================================
# AC-OF.3: analytics called with trading_day=snapshot["trading_day"] (R14)
# ===========================================================================


class TestAnalyticsCalledWithSnapshotTradingDay:
    """
    AC-OF.3: All three portfolio analytics helpers must be called with
    trading_day=snapshot["trading_day"], NOT today's date.
    R14 contract: analytics reads shadow_history keyed on trading_day.
    """

    def test_portfolio_analytics_called_with_snapshot_trading_day(self, flask_client, monkeypatch):
        """AC-OF.3: each portfolio analytics helper called with snapshot.trading_day."""
        client, app_module = flask_client
        fx = _load("frozen_strip_onfly_all_none")
        snapshot_trading_day = fx["snapshot_input"]["trading_day"]  # "2026-05-16"

        bot_state = {
            "date": "2026-05-16",
            "last_market_close_snapshot": fx["snapshot_input"],
        }

        with (
            patch.object(app_module, "database", _make_db_mock(bot_state)),
            patch.object(app_module, "analytics") as mock_analytics,
        ):
            mock_analytics.get_portfolio_today_change.return_value = {
                "if_held": 2.1,
                "dry_run": 1.9,
            }
            mock_analytics.get_portfolio_cumulative_return.return_value = {
                "if_held": 14.3,
                "dry_run": 13.8,
            }
            mock_analytics.get_portfolio_max_drawdown.return_value = {
                "if_held": -6.1,
                "dry_run": -5.7,
            }
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

            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )
            resp = client.get("/api/state")

        assert resp.status_code == 200

        for fn_name in (
            "get_portfolio_today_change",
            "get_portfolio_cumulative_return",
            "get_portfolio_max_drawdown",
        ):
            mock_fn = getattr(mock_analytics, fn_name)
            assert mock_fn.call_count >= 1, (
                f"analytics.{fn_name} must be called on frozen path. "
                f"call_count={mock_fn.call_count}"
            )
            for c in mock_fn.call_args_list:
                actual_td = c.kwargs.get("trading_day")
                assert actual_td == snapshot_trading_day, (
                    f"analytics.{fn_name} must be called with "
                    f"trading_day={snapshot_trading_day!r} (snapshot.trading_day). "
                    f"Got trading_day={actual_td!r}. "
                    "R14 contract: analytics reads shadow_history keyed on trading_day, "
                    "not today's date."
                )


# ===========================================================================
# AC-OF.4: no snapshot → notice preserved, no crash
# ===========================================================================


class TestNoSnapshotNoRegression:
    """
    AC-OF.4: Fresh deploy with no snapshot — existing "No closing snapshot yet"
    notice must still be returned. portfolio_strip key must be absent or None.
    """

    def test_no_snapshot_notice_preserved_and_no_crash(self, flask_client, monkeypatch):
        """AC-OF.4: closed_frozen + no snapshot → notice present, 200, no crash."""
        client, app_module = flask_client

        with patch.object(app_module, "database", _make_db_mock({})):
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )
            resp = client.get("/api/state")

        assert resp.status_code == 200, (
            f"No-snapshot frozen path must return 200. Got {resp.status_code}."
        )
        body = resp.get_json()
        notice = body.get("notice")
        assert notice is not None and "snapshot" in notice.lower(), (
            f"AC-DM.3.4 'No closing snapshot' notice must still be present. "
            f"Got notice={notice!r}. On-the-fly recompute must not remove this notice."
        )


# ===========================================================================
# AC-OF.5: live branch (market open) → no regression
# ===========================================================================


class TestLiveBranchNoRegression:
    """
    AC-OF.5: market_state='open' must still compute portfolio_strip via the
    live analytics path. The frozen on-the-fly logic must not touch the live branch.
    """

    def test_open_market_portfolio_strip_still_computed(self, flask_client, monkeypatch):
        """AC-OF.5: market open → portfolio_strip non-None, frozen_at is None."""
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
            patch.object(app_module, "render_template", return_value="<table></table>"),
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
        assert body.get("portfolio_strip") is not None, (
            "Live (open) path portfolio_strip must not be None. "
            "On-the-fly frozen recompute must not have regressed the live path."
        )
        assert body.get("frozen_at") is None, "frozen_at must be None on live (open) path."
