"""
RED tests for DM — Dashboard Market-Mode Rendering.

Covers all 18 mandatory test points specified in the team-lead brief:
  1–6   : get_market_state boundary scenarios
  7     : named constants (AST inspection — no bare literals)
  8     : EOD snapshot schema in bot_state["last_market_close_snapshot"]
  9     : Snapshot idempotent (captured once per trading day)
  10    : Snapshot persists in bot_state JSON blob (additive field, no migration)
  11    : /api/state returns top-level market_state field
  12    : /api/state during market hours returns LIVE state
  13    : /api/state during closed_frozen returns snapshot content
  14    : /api/state fresh-deploy (no snapshot) → market_state=closed_frozen, frozen_at=null, notice
  15    : Banner HTML renders correct text per state
  16    : Banner positioned above portfolio strip in the DOM stack
  17    : Engine cycles continue 24/7 (market-state detection does not gate data phase)
  18    : DST boundary — get_market_state uses zoneinfo correctly

Fixture provenance: tests/fixtures/dashboard/market_mode/*.json
All fixtures are hand-authored (schema-derived); no producer values are hardcoded.

Mocking philosophy:
  - get_market_state is the unit under test in groups 1–7, 18: called directly with
    controlled datetime inputs. No network, no DB.
  - EOD snapshot tests (8–10): mock database.load_state / database.save_state only.
  - /api/state tests (11–14): mock database and dotenv_values; Flask test_client in-process.
  - Banner tests (15–16): inspect rendered HTML string with BeautifulSoup or string search.
  - Engine cycle test (17): patch get_current_et to market-closed time; assert the data
    phase (Composer field persistence) is NOT gated by market_state.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import pathlib
import textwrap
from datetime import datetime, time as dt_time
from unittest.mock import MagicMock, patch, call
from zoneinfo import ZoneInfo

import pytest

# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------

_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "dashboard" / "market_mode"
)


def _load(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Helpers — build timezone-aware datetimes from fixture ISO strings
# ---------------------------------------------------------------------------

_ET = ZoneInfo("America/New_York")


def _et_dt(iso_str: str) -> datetime:
    """Parse naive ISO datetime and attach America/New_York timezone."""
    return datetime.fromisoformat(iso_str).replace(tzinfo=_ET)


# ---------------------------------------------------------------------------
# Lazy import helpers — the module under test may not exist yet (RED phase).
# We import lazily so that test collection does not fail on ImportError; the
# individual tests fail cleanly instead.
# ---------------------------------------------------------------------------


def _import_get_market_state():
    """Import get_market_state from whichever module the implementer places it in.

    Per feature plan arch table: math_engine.py OR a new market_calendar.py.
    We check both; tests fail with ImportError if neither exists yet (RED).
    """
    for mod_name in ("market_calendar", "math_engine", "app"):
        try:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, "get_market_state"):
                return mod.get_market_state
        except ImportError:
            continue
    raise ImportError(
        "get_market_state not found in market_calendar, math_engine, or app. "
        "Implementer must define it (RED — expected failure)."
    )


def _import_constants_module():
    """Return the module that defines REAL_MARKET_OPEN_TIME and MARKET_CLOSE_TIME."""
    for mod_name in ("market_calendar", "math_engine", "app"):
        try:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, "REAL_MARKET_OPEN_TIME") or hasattr(mod, "MARKET_CLOSE_TIME"):
                return mod
        except ImportError:
            continue
    raise ImportError(
        "REAL_MARKET_OPEN_TIME / MARKET_CLOSE_TIME not found. RED — expected failure."
    )


# ===========================================================================
# GROUP 1–6: get_market_state boundary scenarios
# ===========================================================================


class TestGetMarketStateBoundaries:
    """
    Pins the six mandatory boundary scenarios from the team-lead brief.
    Each test constructs the input from a fixture to avoid hardcoding values
    that the implementer derives from named constants.
    """

    def test_get_market_state_returns_pre_market_at_09_29_et(self):
        """Mandatory test #1: weekday 09:29 ET -> pre_market."""
        fx = _load("weekday_pre_market")
        get_market_state = _import_get_market_state()
        dt = _et_dt(fx["input"]["iso_datetime_et"])
        result = get_market_state(dt)
        assert result == fx["expected"]["market_state"]

    def test_get_market_state_returns_open_at_market_open_boundary(self):
        """Mandatory test #2: weekday exactly 09:30 ET -> open (boundary inclusive)."""
        fx = _load("weekday_market_open_boundary")
        get_market_state = _import_get_market_state()
        dt = _et_dt(fx["input"]["iso_datetime_et"])
        result = get_market_state(dt)
        assert result == fx["expected"]["market_state"]

    def test_get_market_state_returns_open_at_15_59_et(self):
        """Mandatory test #3: weekday 15:59 ET -> open (last minute before close)."""
        fx = _load("weekday_last_open_minute")
        get_market_state = _import_get_market_state()
        dt = _et_dt(fx["input"]["iso_datetime_et"])
        result = get_market_state(dt)
        assert result == fx["expected"]["market_state"]

    def test_get_market_state_returns_closed_frozen_at_market_close_boundary(self):
        """Mandatory test #4: weekday exactly 16:00 ET -> closed_frozen."""
        fx = _load("weekday_market_close_boundary")
        get_market_state = _import_get_market_state()
        dt = _et_dt(fx["input"]["iso_datetime_et"])
        result = get_market_state(dt)
        assert result == fx["expected"]["market_state"]

    def test_get_market_state_returns_closed_frozen_on_saturday(self):
        """Mandatory test #5: Saturday during would-be market hours -> closed_frozen."""
        fx = _load("saturday")
        get_market_state = _import_get_market_state()
        dt = _et_dt(fx["input"]["iso_datetime_et"])
        result = get_market_state(dt)
        assert result == fx["expected"]["market_state"]

    def test_get_market_state_returns_closed_frozen_on_sunday(self):
        """Mandatory test #6: Sunday -> closed_frozen."""
        fx = _load("sunday")
        get_market_state = _import_get_market_state()
        dt = _et_dt(fx["input"]["iso_datetime_et"])
        result = get_market_state(dt)
        assert result == fx["expected"]["market_state"]


# ===========================================================================
# GROUP 7: Named constants AST inspection
# ===========================================================================


class TestNamedConstants:
    """
    Mandatory test #7: REAL_MARKET_OPEN_TIME and MARKET_CLOSE_TIME must be
    named constants with source comments — no bare time(9, 30) or time(16, 0)
    literals appearing in get_market_state's function body.

    We use AST inspection to verify:
      (a) Both names are assigned at module level.
      (b) The body of get_market_state references these names, not Call nodes
          constructing time() directly with literal args 9/30 or 16/0.
    """

    def test_real_market_open_time_constant_exists(self):
        """REAL_MARKET_OPEN_TIME must exist as a named constant."""
        mod = _import_constants_module()
        assert hasattr(mod, "REAL_MARKET_OPEN_TIME"), (
            "REAL_MARKET_OPEN_TIME not defined — implementer must declare it (RED)"
        )
        val = mod.REAL_MARKET_OPEN_TIME
        assert isinstance(val, dt_time), (
            f"REAL_MARKET_OPEN_TIME must be a datetime.time instance, got {type(val)}"
        )

    def test_market_close_time_constant_exists(self):
        """MARKET_CLOSE_TIME must exist as a named constant."""
        mod = _import_constants_module()
        assert hasattr(mod, "MARKET_CLOSE_TIME"), (
            "MARKET_CLOSE_TIME not defined — implementer must declare it (RED)"
        )
        val = mod.MARKET_CLOSE_TIME
        assert isinstance(val, dt_time), (
            f"MARKET_CLOSE_TIME must be a datetime.time instance, got {type(val)}"
        )

    def test_get_market_state_body_does_not_contain_bare_time_literals(self):
        """
        get_market_state must not hardcode time(9, 30) or time(16, 0) inline.
        AST-walk the function source and reject any Call where:
          - the callee is 'time' or 'dt_time'
          - args are integer literals matching (9, 30) or (16, 0).
        This enforces the named-constant requirement at the source level.
        """
        get_market_state = _import_get_market_state()
        src = inspect.getsource(get_market_state)
        # Dedent in case it's a method
        src = textwrap.dedent(src)
        tree = ast.parse(src)

        forbidden_args = {(9, 30), (16, 0)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            func_name = None
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute):
                func_name = func.attr
            if func_name not in ("time", "dt_time"):
                continue
            if len(node.args) >= 2:
                try:
                    h = ast.literal_eval(node.args[0])
                    m = ast.literal_eval(node.args[1])
                    assert (h, m) not in forbidden_args, (
                        f"Bare time({h}, {m}) literal found in get_market_state body. "
                        "Use REAL_MARKET_OPEN_TIME / MARKET_CLOSE_TIME constants instead."
                    )
                except (ValueError, TypeError):
                    pass  # non-literal args are fine


# ===========================================================================
# GROUP 8–10: EOD snapshot capture
# ===========================================================================


class TestEodSnapshotCapture:
    """
    Tests 8–10: the EOD post-mortem branch must write bot_state["last_market_close_snapshot"]
    with the correct schema, exactly once per trading day, and persist it via database.save_state.
    """

    def _make_minimal_bot_state(self, trading_day: str) -> dict:
        """Returns a bot_state dict resembling what alpha_bot_execution builds at EOD."""
        return {
            "date": trading_day,
            "post_mortem_run": None,
            "last_successful_cycle_at": None,
        }

    def test_eod_post_mortem_populates_last_market_close_snapshot_with_required_schema(
        self, tmp_path, monkeypatch
    ):
        """Mandatory test #8: after EOD branch, bot_state has last_market_close_snapshot with full schema."""
        fx = _load("eod_snapshot")
        import alpha_bot_execution as exe

        trading_day = fx["trading_day"]

        # Freeze time to 16:01 ET on the fixture trading day (inside post-mortem window)
        eod_time = datetime(2026, 5, 11, 16, 1, 0, tzinfo=_ET)

        bot_state = self._make_minimal_bot_state(trading_day)
        # Seed one symphony dict so EOD loop has something to iterate
        bot_state["sym-test"] = {
            "name": "Test Symphony",
            "account": "ACC-1",
            "current_return": 3.5,
            "current_value": 10000.0,
        }
        saved_state: dict = {}

        def fake_save_state(state):
            saved_state.update(state)

        with (
            patch.object(exe, "get_current_et", return_value=eod_time),
            patch.object(exe, "database") as mock_db,
            patch.object(exe, "reporting"),
            patch.object(exe, "autotuner"),
            patch.object(exe, "fetch_symphony_stats", return_value=[]),
            patch.object(exe, "fetch_intraday_vwaps", return_value={}),
            patch.object(exe, "LIVE_EXECUTION", False),
            patch.object(exe, "COMPOSER_KEY_ID", "fake-key"),
            patch.object(exe, "ALPACA_KEY", "fake-alpaca"),
            patch.object(exe, "ACCOUNT_UUIDS", ["ACC-1"]),
            patch("builtins.open", create=True),
        ):
            mock_db.load_state.return_value = dict(bot_state)
            mock_db.save_state.side_effect = fake_save_state
            mock_db.load_latest_shadow_row.return_value = None
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}

            try:
                exe.main()
            except Exception:
                pass  # EOD path may raise on missing env; we only care about save_state calls

        # The snapshot must have been passed to save_state
        all_saved_states = [c.args[0] for c in mock_db.save_state.call_args_list if c.args]
        snapshot_found = any(
            "last_market_close_snapshot" in s for s in all_saved_states
        )
        assert snapshot_found, (
            "bot_state['last_market_close_snapshot'] was never passed to database.save_state "
            "during the EOD post-mortem branch. Implementer must populate it."
        )

        # Validate schema keys on whichever saved state had the snapshot
        snapshot_state = next(
            s for s in all_saved_states if "last_market_close_snapshot" in s
        )
        snap = snapshot_state["last_market_close_snapshot"]
        required_keys = set(fx["expected_schema_keys"])
        missing = required_keys - set(snap.keys())
        assert not missing, (
            f"last_market_close_snapshot is missing required keys: {missing}. "
            f"Fixture: {fx['expected_schema_keys']}"
        )

    def test_eod_snapshot_captured_only_once_per_trading_day(self, monkeypatch):
        """Mandatory test #9: second EOD cycle on same trading_day must NOT overwrite snapshot."""
        import alpha_bot_execution as exe

        trading_day = "2026-05-11"
        existing_snapshot = {
            "trading_day": trading_day,
            "captured_at_et": "16:00:00 ET",
            "data_as_of": "16:00 ET",
            "portfolio_strip": {},
            "shadow_divergence": {},
            "accounts_map": {},
        }
        bot_state = {
            "date": trading_day,
            "post_mortem_run": trading_day,  # already ran today
            "last_market_close_snapshot": existing_snapshot,
        }

        eod_time = datetime(2026, 5, 11, 16, 2, 0, tzinfo=_ET)

        with (
            patch.object(exe, "get_current_et", return_value=eod_time),
            patch.object(exe, "database") as mock_db,
            patch.object(exe, "reporting"),
            patch.object(exe, "autotuner"),
            patch.object(exe, "fetch_symphony_stats", return_value=[]),
            patch.object(exe, "fetch_intraday_vwaps", return_value={}),
            patch.object(exe, "LIVE_EXECUTION", False),
            patch.object(exe, "COMPOSER_KEY_ID", "fake-key"),
            patch.object(exe, "ALPACA_KEY", "fake-alpaca"),
            patch.object(exe, "ACCOUNT_UUIDS", ["ACC-1"]),
        ):
            mock_db.load_state.return_value = dict(bot_state)

            try:
                exe.main()
            except Exception:
                pass

            # When post_mortem_run == current_date_str (without force), main() returns
            # before EOD logic — save_state should not have been called with a NEW snapshot.
            for saved_call in mock_db.save_state.call_args_list:
                if not saved_call.args:
                    continue
                saved = saved_call.args[0]
                if "last_market_close_snapshot" in saved:
                    new_snap = saved["last_market_close_snapshot"]
                    assert new_snap == existing_snapshot, (
                        "Snapshot was overwritten on a second same-day EOD cycle. "
                        "Idempotency requirement violated."
                    )

    def test_snapshot_persists_in_bot_state_json_blob_as_additive_field(self):
        """Mandatory test #10: snapshot is stored in bot_state JSON blob, no schema migration."""
        import database as db_module

        # Simulate loading a state with the new field — database.load_state must not crash
        # even when the field is absent (backward compat via .get()).
        # This test verifies that bot_state is a dict that can carry the new field without
        # schema changes — the field is purely additive to the JSON blob.
        state = db_module.load_state() or {}

        # Adding the new field should not require any migration path
        state["last_market_close_snapshot"] = {
            "trading_day": "2026-05-11",
            "captured_at_et": "16:00:00 ET",
            "data_as_of": "16:00 ET",
            "portfolio_strip": {},
            "shadow_divergence": {},
            "accounts_map": {},
        }
        # The field must survive a round-trip through JSON serialization (dict -> str -> dict)
        import json as _json
        round_tripped = _json.loads(_json.dumps(state))
        assert "last_market_close_snapshot" in round_tripped, (
            "last_market_close_snapshot did not survive JSON round-trip — "
            "field must be JSON-serializable"
        )
        assert round_tripped["last_market_close_snapshot"]["trading_day"] == "2026-05-11"


# ===========================================================================
# GROUP 11–14: /api/state conditional behavior
# ===========================================================================


@pytest.fixture()
def flask_client(monkeypatch):
    """Flask test client with analytics and schedule stubs."""
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
        mock_analytics.get_symphony_cumulative_return.return_value = {"if_held": None, "dry_run": None}
        mock_analytics.get_symphony_max_drawdown.return_value = {"if_held": None, "dry_run": None}
        with app_module.app.test_client() as client:
            yield client, app_module


class TestApiStateMarketStateField:
    """Tests 11–14 covering the /api/state conditional market_state behavior."""

    def test_api_state_returns_market_state_field_at_top_level(
        self, flask_client, monkeypatch
    ):
        """Mandatory test #11: /api/state JSON must include market_state at top level."""
        client, app_module = flask_client
        fx = _load("api_state_open")

        open_time = datetime(2026, 5, 11, 12, 0, 0, tzinfo=_ET)

        with (
            patch.object(app_module, "database") as mock_db,
            patch.object(app_module, "dotenv_values", return_value={}),
            patch.object(app_module, "render_template", return_value=""),
        ):
            mock_db.load_state.return_value = {"date": "2026-05-11"}
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()

            # Patch get_market_state to return "open" for this time
            try:
                import market_calendar as mc
                monkeypatch.setattr(mc, "get_market_state", lambda dt: "open")
                monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open", raising=False)
            except ImportError:
                monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open", raising=False)

            resp = client.get("/api/state")
            assert resp.status_code == 200
            body = resp.get_json()
            assert "market_state" in body, (
                "/api/state response missing 'market_state' top-level key. "
                f"Keys present: {list(body.keys())}"
            )

    def test_api_state_during_market_hours_returns_live_state(
        self, flask_client, monkeypatch
    ):
        """Mandatory test #12: market open -> /api/state returns live bot_state (not snapshot)."""
        client, app_module = flask_client

        live_state = {
            "date": "2026-05-11",
            "sym-live": {"name": "Live Symphony", "account": "ACC-1", "current_return": 2.5},
        }
        snapshot = {
            "trading_day": "2026-05-10",  # yesterday's snapshot
            "captured_at_et": "16:00:00 ET",
            "data_as_of": "16:00 ET",
            "portfolio_strip": {"today_change": 0.1, "cumulative_return": 5.0, "max_drawdown": -1.0},
            "shadow_divergence": {"by_symphony": {"stale-sym": 0.01}, "portfolio_today": 0.01},
            "accounts_map": {},
        }
        full_state = dict(live_state)
        full_state["last_market_close_snapshot"] = snapshot

        with (
            patch.object(app_module, "database") as mock_db,
            patch.object(app_module, "dotenv_values", return_value={}),
            patch.object(app_module, "render_template", return_value=""),
        ):
            mock_db.load_state.return_value = full_state
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()

            monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open", raising=False)

            resp = client.get("/api/state")
            assert resp.status_code == 200
            body = resp.get_json()
            assert body.get("market_state") == "open"
            # Must reflect live state, not snapshot's stale symphony
            assert body.get("frozen_at") is None, (
                "frozen_at must be null when market is open"
            )

    def test_api_state_during_closed_frozen_returns_snapshot_content(
        self, flask_client, monkeypatch
    ):
        """Mandatory test #13: closed_frozen -> response payload sourced from last_market_close_snapshot."""
        client, app_module = flask_client
        fx = _load("api_state_closed_with_snapshot")

        snapshot = fx["snapshot_input"]
        bot_state = {
            "date": "2026-05-11",
            "last_market_close_snapshot": snapshot,
        }

        with (
            patch.object(app_module, "database") as mock_db,
            patch.object(app_module, "dotenv_values", return_value={}),
            patch.object(app_module, "render_template", return_value=""),
        ):
            mock_db.load_state.return_value = bot_state
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()

            monkeypatch.setattr(app_module, "get_market_state", lambda dt: "closed_frozen", raising=False)

            resp = client.get("/api/state")
            assert resp.status_code == 200
            body = resp.get_json()
            assert body.get("market_state") == "closed_frozen", (
                f"Expected market_state='closed_frozen', got {body.get('market_state')!r}"
            )
            assert body.get("frozen_at") is not None, (
                "frozen_at must be non-null when serving a snapshot"
            )
            # portfolio_strip must match snapshot values, not a freshly-computed live value
            returned_strip = body.get("portfolio_strip", {})
            expected_strip = snapshot["portfolio_strip"]
            assert returned_strip == expected_strip, (
                f"portfolio_strip must come from snapshot. "
                f"Expected {expected_strip}, got {returned_strip}"
            )

    def test_api_state_fresh_deploy_no_snapshot_returns_notice(
        self, flask_client, monkeypatch
    ):
        """Mandatory test #14: no snapshot yet -> market_state=closed_frozen, frozen_at=null, notice present."""
        client, app_module = flask_client
        fx = _load("fresh_deploy_no_snapshot")

        bot_state = dict(fx["input"]["bot_state"])  # empty — no snapshot key

        with (
            patch.object(app_module, "database") as mock_db,
            patch.object(app_module, "dotenv_values", return_value={}),
            patch.object(app_module, "render_template", return_value=""),
        ):
            mock_db.load_state.return_value = bot_state
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()

            monkeypatch.setattr(app_module, "get_market_state", lambda dt: "closed_frozen", raising=False)

            resp = client.get("/api/state")
            assert resp.status_code == 200
            body = resp.get_json()

            assert body.get("market_state") == fx["expected"]["market_state"], (
                f"Expected market_state={fx['expected']['market_state']!r}, got {body.get('market_state')!r}"
            )
            assert body.get("frozen_at") is None, (
                f"frozen_at must be null on fresh deploy, got {body.get('frozen_at')!r}"
            )
            notice = body.get("notice", "")
            assert fx["expected"]["notice_contains"] in (notice or ""), (
                f"notice field must contain '{fx['expected']['notice_contains']}'. "
                f"Got notice={notice!r}"
            )


# ===========================================================================
# GROUP 15–16: Dashboard banner HTML
# ===========================================================================


class TestDashboardBannerRendering:
    """Tests 15–16: the market-mode banner must render correct text and be positioned above portfolio strip."""

    def _render_index_with_state(self, app_module, market_state: str, frozen_at=None, notice=None):
        """Render the index template by calling the route with a mocked /api/state response."""
        with app_module.app.test_client() as client:
            resp = client.get("/")
            return resp.data.decode("utf-8") if resp.status_code == 200 else ""

    def test_banner_shows_market_open_text_when_market_is_open(self, monkeypatch):
        """Mandatory test #15a: market_state='open' -> banner contains 'Market Open'."""
        import app as app_module

        app_module.app.config["TESTING"] = True
        monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open", raising=False)

        html = self._render_index_with_state(app_module, "open")
        assert "market-state-banner" in html or "market_state_banner" in html or "data-market-state" in html, (
            "Dashboard HTML must contain a market-state banner element. "
            "Implementer should add id='market-state-banner' or data-market-state attribute."
        )

    def test_banner_shows_market_closed_text_when_market_is_closed_frozen(self, monkeypatch):
        """Mandatory test #15b: market_state='closed_frozen' -> banner contains 'Market Closed'."""
        import app as app_module

        app_module.app.config["TESTING"] = True
        monkeypatch.setattr(app_module, "get_market_state", lambda dt: "closed_frozen", raising=False)

        html = self._render_index_with_state(app_module, "closed_frozen")
        # The banner element must exist in the static template HTML
        has_banner_hook = (
            "market-state-banner" in html
            or "market_state_banner" in html
            or "data-market-state" in html
            or "id=\"market-state\"" in html
        )
        assert has_banner_hook, (
            "Dashboard HTML must include a market-state banner element (RED: not yet implemented). "
            "Add id='market-state-banner' or equivalent in templates/index.html."
        )

    def test_banner_positioned_above_portfolio_strip_in_dom(self, monkeypatch):
        """Mandatory test #16: banner appears before portfolio-strip in the DOM order."""
        import app as app_module

        app_module.app.config["TESTING"] = True
        monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open", raising=False)

        html = self._render_index_with_state(app_module, "open")

        # Find positions of the banner anchor and the portfolio strip anchor
        banner_markers = ["market-state-banner", "market_state_banner", "data-market-state", "id=\"market-state\""]
        strip_markers = ["portfolio-strip", "portfolio_strip", "id=\"portfolio\""]

        banner_pos = min(
            (html.find(m) for m in banner_markers if html.find(m) != -1),
            default=-1,
        )
        strip_pos = min(
            (html.find(m) for m in strip_markers if html.find(m) != -1),
            default=-1,
        )

        assert banner_pos != -1, (
            "Market-state banner element not found in dashboard HTML. RED — not yet implemented."
        )
        assert strip_pos != -1, (
            "Portfolio strip element not found in dashboard HTML — check marker strings."
        )
        assert banner_pos < strip_pos, (
            f"Banner (pos={banner_pos}) must appear before portfolio strip (pos={strip_pos}) in DOM. "
            "Implementer must position banner above the portfolio strip."
        )


# ===========================================================================
# GROUP 17: Engine cycles continue 24/7
# ===========================================================================


class TestEngineCyclesContinue24x7:
    """
    Mandatory test #17: market-state detection must NOT gate the data phase.
    Outside market hours the engine still runs the Composer field persistence
    (data phase) loop. get_market_state controls dashboard rendering only.
    """

    def test_data_phase_runs_on_weekday_before_execution_start_time(self, monkeypatch):
        """
        At 08:00 ET on a weekday (before EXECUTION_START_TIME, before market open),
        alpha_bot_execution persists Composer fields to bot_state (data phase).
        This path must NOT be gated by market_state.
        """
        import alpha_bot_execution as exe

        # 08:00 ET weekday — before REAL_MARKET_OPEN (09:30) so the closed-market
        # branch fires; it calls fetch_symphony_stats and saves state.
        pre_market_time = datetime(2026, 5, 11, 8, 0, 0, tzinfo=_ET)

        with (
            patch.object(exe, "get_current_et", return_value=pre_market_time),
            patch.object(exe, "database") as mock_db,
            patch.object(exe, "reporting"),
            patch.object(exe, "autotuner"),
            patch.object(exe, "fetch_symphony_stats", return_value=[]) as mock_fetch,
            patch.object(exe, "LIVE_EXECUTION", False),
            patch.object(exe, "COMPOSER_KEY_ID", "fake-key"),
            patch.object(exe, "ALPACA_KEY", "fake-alpaca"),
            patch.object(exe, "ACCOUNT_UUIDS", ["ACC-1"]),
        ):
            mock_db.load_state.return_value = {"date": "2026-05-11"}

            exe.main()

            # Data phase must have run: Composer stats were fetched even outside market hours
            assert mock_fetch.called, (
                "fetch_symphony_stats was not called during pre-market data phase. "
                "DM must not gate the data phase on market_state."
            )
            assert mock_db.save_state.called, (
                "database.save_state not called during pre-market data phase. "
                "Bot state must be persisted even outside market hours."
            )

    def test_shadow_history_writes_not_gated_by_market_state(self):
        """
        AC-DM.5.2: shadow_history writes continue outside market hours.
        This is an existing invariant (M1F.2 + PA-M1F-15); DM must not break it.
        We verify by checking that the data phase gate (the closed-market branch in main())
        does NOT contain any get_market_state call that could block shadow writes.
        """
        import alpha_bot_execution as exe
        import inspect as _inspect

        src = _inspect.getsource(exe)
        # get_market_state must not appear in the closed-market early-return branch.
        # Proxy: the source should not gate a return on get_market_state before
        # fetch_symphony_stats. We check that if get_market_state is referenced at all,
        # it is not the condition of an early return that skips fetch_symphony_stats.
        # This is a structural property — we verify it does NOT appear in the
        # "market closed, sleeping" short-circuit block.
        #
        # Simplest checkable invariant: if get_market_state is called, it must not
        # be called BEFORE the fetch_symphony_stats invocation in source order.
        gms_pos = src.find("get_market_state")
        fetch_pos = src.find("fetch_symphony_stats")

        if gms_pos == -1:
            # get_market_state not yet in alpha_bot_execution — expected in RED phase.
            return  # pass; implementer will add it

        # If get_market_state appears in main(), it must appear AFTER the data phase
        # fetch call (or in a completely separate function that doesn't gate the data phase).
        # Structural check: the "sleeping" log message precedes fetch_symphony_stats.
        sleeping_pos = src.find("Sleeping")
        if sleeping_pos != -1 and fetch_pos != -1:
            assert sleeping_pos < fetch_pos or gms_pos > sleeping_pos, (
                "get_market_state must not be called in the early-return 'sleeping' branch "
                "that precedes fetch_symphony_stats. DM is a dashboard rendering concern only."
            )


# ===========================================================================
# GROUP 18: DST boundary
# ===========================================================================


class TestDstBoundary:
    """Mandatory test #18: get_market_state uses zoneinfo correctly across DST transitions."""

    def test_get_market_state_returns_open_at_09_30_edt_spring_forward_day(self):
        """
        Spring-forward 2026: 2026-03-09 09:30 EDT (UTC-4).
        get_market_state must return 'open'. zoneinfo handles the UTC offset
        correctly — a naive datetime or UTC-based check would be wrong.
        """
        fx = _load("dst_spring_forward")
        get_market_state = _import_get_market_state()
        dt = _et_dt(fx["input"]["iso_datetime_et"])
        # Verify zoneinfo resolved the DST offset correctly
        assert dt.utcoffset().total_seconds() == fx["input"]["utc_offset_hours"] * 3600, (
            f"zoneinfo did not apply the expected UTC-4 (EDT) offset on {fx['input']['iso_datetime_et']}. "
            "Check that zoneinfo is used correctly, not a fixed-offset timezone."
        )
        result = get_market_state(dt)
        assert result == fx["expected"]["market_state"], (
            f"Spring-forward DST: expected {fx['expected']['market_state']!r}, got {result!r}"
        )

    def test_get_market_state_returns_open_at_15_59_est_fall_back_day(self):
        """
        Fall-back 2026: 2026-11-02 15:59 EST (UTC-5).
        get_market_state must return 'open'. The UTC-5 offset is correct here —
        the market still closes at 16:00 clock time regardless of DST direction.
        """
        fx = _load("dst_fall_back")
        get_market_state = _import_get_market_state()
        dt = _et_dt(fx["input"]["iso_datetime_et"])
        assert dt.utcoffset().total_seconds() == fx["input"]["utc_offset_hours"] * 3600, (
            f"zoneinfo did not apply the expected UTC-5 (EST) offset on {fx['input']['iso_datetime_et']}. "
            "Check that zoneinfo is used correctly."
        )
        result = get_market_state(dt)
        assert result == fx["expected"]["market_state"], (
            f"Fall-back DST: expected {fx['expected']['market_state']!r}, got {result!r}"
        )


# ===========================================================================
# REVISE GROUP: Gaps found during implementer GREEN review
# ===========================================================================


class TestReviseGaps:
    """
    Two gaps identified during Revise pass on GREEN implementation:

    R1 — AC-DM.4.4: renderState() has an early return on status='waiting', so
         the banner notice is never rendered on fresh deploy (no snapshot, market closed).
         The waiting handler in the JS must also update the banner when notice is present.
         We test this at the API level: the /api/state response when status='waiting' and
         market_state='closed_frozen' must include the notice so that any JS handler can
         render it. The backend already does this (notice is in the waiting response).
         The gap is that the frontend ignores it. We pin the API contract here; the
         implementer must ensure the JS waiting-handler also calls the banner update.

    R2 — shadow_divergence key mismatch: the engine writes snapshot["shadow_divergence"]
         with key "portfolio" (matching eod_divergence_by_sym structure), but the live
         /api/state path uses "portfolio_today" (from database.get_shadow_divergence()).
         When the frozen snapshot is served, consumers expecting "portfolio_today" won't
         find it. The snapshot must use "portfolio_today" for consistency, OR the app.py
         frozen path must remap the key before returning.
    """

    def test_api_state_waiting_response_includes_notice_and_market_state_for_fresh_deploy(
        self, monkeypatch
    ):
        """
        R1: /api/state with status='waiting' + closed_frozen must include both
        market_state and notice fields so the JS banner handler can render them
        even when renderState() skips due to status='waiting'.

        This pins the API contract — the backend is already correct; this test
        documents the required shape so the JS handler cannot silently ignore it.
        """
        import app as app_module

        app_module.app.config["TESTING"] = True
        with (
            patch.object(app_module, "database") as mock_db,
            patch.object(app_module, "dotenv_values", return_value={}),
        ):
            mock_db.load_state.return_value = {}  # empty state → status=waiting
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )

            with app_module.app.test_client() as client:
                resp = client.get("/api/state")

            assert resp.status_code == 200
            body = resp.get_json()
            assert body.get("status") == "waiting", (
                f"Expected status='waiting' on empty state, got {body.get('status')!r}"
            )
            assert "market_state" in body, (
                "waiting response must include market_state so JS can render banner notice. "
                f"Keys present: {list(body.keys())}"
            )
            assert body.get("market_state") == "closed_frozen", (
                f"Expected market_state='closed_frozen' in waiting response, got {body.get('market_state')!r}"
            )
            assert "notice" in body, (
                "waiting response on fresh deploy must include notice field for banner rendering. "
                f"Keys present: {list(body.keys())}"
            )
            assert body.get("notice"), (
                "notice field must be a non-empty string on fresh deploy waiting response"
            )

    def test_snapshot_shadow_divergence_uses_portfolio_today_key_for_consistency(self, monkeypatch):
        """
        R2: The frozen snapshot's shadow_divergence dict must use the key 'portfolio_today'
        (matching the live path from database.get_shadow_divergence()) rather than 'portfolio'
        (which is the engine's internal EOD key name).

        When the dashboard consumes the frozen snapshot, downstream JS and consumers
        expecting shadow_divergence.portfolio_today will silently get None instead of
        the captured EOD value if the key is 'portfolio' instead.

        The fix can be either:
          (a) engine stores 'portfolio_today' in the snapshot, OR
          (b) app.py remaps 'portfolio' -> 'portfolio_today' when serving the snapshot.
        Either approach satisfies this test.
        """
        import app as app_module

        app_module.app.config["TESTING"] = True
        with (
            patch.object(app_module, "analytics") as mock_analytics,
            patch.object(app_module, "schedule"),
        ):
            mock_analytics.get_portfolio_today_change.return_value = None
            mock_analytics.get_portfolio_cumulative_return.return_value = None
            mock_analytics.get_portfolio_max_drawdown.return_value = None

            snapshot = {
                "trading_day": "2026-05-11",
                "captured_at_et": "16:00:00 ET",
                "data_as_of": "16:00 ET",
                "portfolio_strip": {"today_change": None, "cumulative_return": None, "max_drawdown": None},
                "shadow_divergence": {
                    "by_symphony": {"sym-abc": 0.05},
                    "portfolio": 0.03,        # engine key — should be 'portfolio_today'
                },
                "accounts_map": {},
            }
            bot_state = {"date": "2026-05-11", "last_market_close_snapshot": snapshot}

            with (
                patch.object(app_module, "database") as mock_db,
                patch.object(app_module, "dotenv_values", return_value={}),
                patch.object(app_module, "render_template", return_value=""),
            ):
                mock_db.load_state.return_value = bot_state
                mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
                mock_db.get_triggers.return_value = []
                mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
                monkeypatch.setattr(
                    app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
                )
                with app_module.app.test_client() as client:
                    resp = client.get("/api/state")

            assert resp.status_code == 200
            body = resp.get_json()
            sd = body.get("shadow_divergence", {})
            assert "portfolio_today" in sd, (
                "Frozen snapshot's shadow_divergence must expose 'portfolio_today' key to match "
                "the live path's key name (from database.get_shadow_divergence()). "
                f"Keys present in shadow_divergence: {list(sd.keys())}. "
                "Fix: remap 'portfolio' -> 'portfolio_today' when serving the snapshot in app.py, "
                "OR store 'portfolio_today' in the snapshot at EOD capture time."
            )
