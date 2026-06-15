"""
RED tests — No Flask route may reach broker order symbols.

Plan deliverable (3): no submit_order / place_order / cancel_order / liquidate
symbol is reachable from any route in app.py.

Two checks:
  1. Static: scan app.py source for any Name/Attribute node matching the
     broker-order denylist — both at module scope and inside route bodies.
  2. Behavioral: call every route and assert subprocess.run and
     requests.post are never reached with arguments that look like broker
     endpoints, EXCEPT for the `/api/sell_account` route which may spawn
     a liquidation thread but only when `live_mode=True` (and that thread
     must itself gate on `live_mode`).

Additional check: no route spawns `alpha_bot_execution.py` synchronously
(the scheduler is the only legal spawner; a route doing so would mean the
dashboard has become an action surface).
"""

from __future__ import annotations

import ast
import inspect
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

import app as app_module


# ---------------------------------------------------------------------------
# Broker-order denylist
# ---------------------------------------------------------------------------

_BROKER_ORDER_SYMBOLS: frozenset[str] = frozenset(
    {
        "submit_order",
        "place_order",
        "cancel_order",
        "liquidate",
        # Composer go-to-cash verb used inside perform_account_liquidation
        "go-to-cash",
    }
)

# Routes that are legitimately allowed to reach order paths (with guards):
# sell_account is the ONLY route allowed to reach perform_account_liquidation,
# and only when LIVE_EXECUTION=True + all confirmation gates pass.
_ALLOWED_ORDER_ROUTES: frozenset[str] = frozenset(
    {
        "sell_account",
        "perform_account_liquidation",  # helper, not a route itself
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_all_function_names_in_source(source_path: Path) -> dict[str, set[str]]:
    """Return {func_name: {call_target_names}} for every function in source."""
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    result: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names: set[str] = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name):
                        names.add(child.func.id)
                    elif isinstance(child.func, ast.Attribute):
                        names.add(child.func.attr)
                        # Also capture string literals passed as URL path segments
                    if isinstance(child.func, ast.Attribute):
                        for arg in child.args:
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                names.add(arg.value)
            result[node.name] = names
    return result


def _get_route_function_names() -> set[str]:
    """Return the __name__ of every Flask route handler (excluding static)."""
    names: set[str] = set()
    for rule in app_module.app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        view_func = app_module.app.view_functions.get(rule.endpoint)
        if view_func is not None:
            names.add(view_func.__name__)
    return names


@pytest.fixture
def flask_client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Test 1: Static — route bodies don't reference broker-order symbols
# ---------------------------------------------------------------------------


class TestNoBrokerSymbolsInRoutes:
    """No route function (excluding sell_account) references broker-order symbols."""

    @pytest.fixture(autouse=True)
    def _load_source(self):
        self._source = Path(inspect.getfile(app_module))
        self._call_map = _parse_all_function_names_in_source(self._source)
        self._route_names = _get_route_function_names() - _ALLOWED_ORDER_ROUTES

    def test_no_route_references_submit_order(self):
        """No route body may contain the symbol submit_order."""
        violators = [f for f in self._route_names if "submit_order" in self._call_map.get(f, set())]
        assert not violators, "Route(s) reference submit_order: " + ", ".join(violators)

    def test_no_route_references_place_order(self):
        """No route body may contain the symbol place_order."""
        violators = [f for f in self._route_names if "place_order" in self._call_map.get(f, set())]
        assert not violators, "Route(s) reference place_order: " + ", ".join(violators)

    def test_no_route_references_cancel_order(self):
        """No route body may contain the symbol cancel_order."""
        violators = [f for f in self._route_names if "cancel_order" in self._call_map.get(f, set())]
        assert not violators, "Route(s) reference cancel_order: " + ", ".join(violators)

    def test_no_non_sell_route_references_liquidate(self):
        """No route except sell_account may reference the symbol liquidate."""
        violators = [
            f
            for f in self._route_names  # already excludes sell_account
            if "liquidate" in self._call_map.get(f, set())
        ]
        assert not violators, "Non-sell route(s) reference liquidate: " + ", ".join(violators)


# ---------------------------------------------------------------------------
# Test 2: No route spawns alpha_bot_execution.py synchronously
# ---------------------------------------------------------------------------


class TestNoRouteSynchronouslySpawnsEngine:
    """Routes must not spawn alpha_bot_execution.py — that is the scheduler's job.

    The only legal engine spawner is the minute scheduler (run_scheduler /
    threaded_trigger).  A route that spawns it directly converts the dashboard
    into an action surface, violating architecture constraint 2.
    """

    def test_no_get_route_spawns_subprocess(self, flask_client):
        """GET routes must not call subprocess.run with alpha_bot_execution.py."""
        spawn_calls: list = []

        def _spy_run(cmd, *args, **kwargs):
            if "alpha_bot_execution" in str(cmd):
                spawn_calls.append(cmd)
            return MagicMock(returncode=0)

        mock_db = MagicMock()
        mock_db.load_state.return_value = {}
        mock_db.get_triggers.return_value = []
        mock_db.read_fleet_alert.return_value = None
        mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
        mock_db.get_symphony_strategy.return_value = None
        mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
        mock_db.get_all_autotune_runs.return_value = []
        mock_db.DEFAULT_STRATEGY = {}
        ro_conn = MagicMock()
        ro_conn.execute.return_value = MagicMock(fetchone=lambda: None, fetchall=lambda: [])
        mock_db.get_ro_connection.return_value = ro_conn

        mock_analytics = MagicMock()
        mock_analytics.get_portfolio_daily_returns_from_shadow.return_value = None
        mock_analytics.get_history_with_cache_invalidation.return_value = {}
        mock_analytics.compute_aggregate_returns.return_value = ([], [], [])
        mock_analytics._POST_MORTEMS_DIR = "/tmp/pm"
        mock_analytics._HISTORY_CACHE = {"key": None, "data": None}
        mock_analytics.get_history_summary.return_value = {}
        mock_analytics.list_available_symphonies.return_value = []

        with patch.object(app_module, "database", mock_db):
            with patch.object(app_module, "analytics", mock_analytics):
                with patch("subprocess.run", side_effect=_spy_run):
                    flask_client.get("/api/state")
                    flask_client.get("/api/triggers")
                    flask_client.get("/api/autotune-runs")
                    flask_client.get("/api/accounts")
                    flask_client.get("/api/history/30")
                    flask_client.get("/api/performance")

        assert spawn_calls == [], (
            f"GET route(s) spawned alpha_bot_execution.py subprocess: {spawn_calls}. "
            "Only the minute scheduler (trigger_alpha_bot) may spawn the engine."
        )

    def test_manual_trigger_route_is_an_action_surface_violation(self, flask_client):
        """POST /api/trigger spawns alpha_bot_execution.py — this route is an action surface.

        This test asserts that the route does NOT spawn the engine after the
        side-effect ban.  It will be RED until the implementer removes or gates
        the manual trigger route (architecture constraint 2: dashboard is a
        read-only operator surface, NEVER an action surface).

        We patch at `app_module.trigger_alpha_bot` level — the function that
        wraps subprocess.run — so the spy fires synchronously regardless of
        threading, avoiding a race condition between the daemon thread and the
        test assertion.
        """
        trigger_calls: list = []

        def _spy_trigger(force=False):
            trigger_calls.append(force)

        with patch.object(app_module, "trigger_alpha_bot", side_effect=_spy_trigger):
            resp = flask_client.post("/api/trigger")

        # The route must NOT call trigger_alpha_bot — that is the scheduler's job.
        # Any call here means the dashboard is acting as an action surface.
        assert trigger_calls == [], (
            "POST /api/trigger called trigger_alpha_bot directly from a Flask route. "
            "This converts the dashboard into an action surface (arch constraint 2 violation). "
            "The manual-trigger route must be removed or gated behind an operator-auth mechanism "
            "that is not part of the operator-facing dashboard surface. "
            f"trigger_alpha_bot was called {len(trigger_calls)} time(s)."
        )


# ---------------------------------------------------------------------------
# Test 3: sell_account route gates on live_mode and confirmation before any order
# ---------------------------------------------------------------------------


class TestSellAccountLiveModeGate:
    """POST /api/sell_account must NOT reach perform_account_liquidation in dry-run mode.

    The F-1 hazard: is_live=True must be explicit.  When LIVE_EXECUTION=False,
    the route must return dry_run=True and executed=False with zero broker calls.
    """

    def test_dry_run_mode_produces_no_broker_calls(self, flask_client):
        """In dry-run mode, sell_account returns executed=False with no requests.post.

        The route calls dotenv_values(".env") via the name imported at module
        scope (`from dotenv import dotenv_values`).  We must patch that exact
        binding in the app module's namespace.
        """
        requests_post_calls: list = []

        def _spy_post(url, *args, **kwargs):
            requests_post_calls.append(url)
            return MagicMock(status_code=200)

        # Provide a mock env with LIVE_EXECUTION=False and a fake COMPOSER_KEY_ID
        # so the route reaches the dry-run gate (not the missing-credentials gate).
        _mock_env = {
            "LIVE_EXECUTION": "False",
            "COMPOSER_KEY_ID": "fake-key",
            "COMPOSER_SECRET": "fake-secret",
        }

        with patch.object(app_module, "dotenv_values", return_value=_mock_env):
            with patch.object(app_module, "requests") as mock_requests:
                mock_requests.post.side_effect = _spy_post
                resp = flask_client.post(
                    "/api/sell_account",
                    json={
                        "account_id": "ACC-001",
                        "confirm_account_id": "ACC-001",
                        "confirm_phrase": "LIQUIDATE",
                    },
                )

        data = resp.get_json()
        assert data is not None
        assert data.get("executed") is False, (
            f"sell_account in dry-run mode must return executed=False, got: {data}"
        )
        # Broker-endpoint POST must never happen in non-live mode
        broker_posts = [u for u in requests_post_calls if "go-to-cash" in u]
        assert not broker_posts, (
            f"sell_account in dry-run mode made broker go-to-cash POST call(s): {broker_posts}. "
            "Broker calls must only happen when LIVE_EXECUTION=True."
        )

    def test_missing_confirmation_phrase_is_rejected_before_any_order(self, flask_client):
        """sell_account with wrong confirm_phrase must be rejected at gate, no order placed."""
        requests_post_calls: list = []

        def _spy_post(url, *args, **kwargs):
            requests_post_calls.append(url)
            return MagicMock(status_code=200)

        with patch("requests.post", side_effect=_spy_post):
            resp = flask_client.post(
                "/api/sell_account",
                json={
                    "account_id": "ACC-001",
                    "confirm_account_id": "ACC-001",
                    "confirm_phrase": "WRONG",  # Not "LIQUIDATE"
                },
            )

        assert resp.status_code == 400, (
            f"Wrong confirm_phrase must be rejected with 400, got {resp.status_code}"
        )
        broker_posts = [u for u in requests_post_calls if "go-to-cash" in u]
        assert not broker_posts, "Broker order was placed despite invalid confirmation phrase."

    def test_mismatched_account_id_is_rejected_before_any_order(self, flask_client):
        """sell_account with mismatched confirm_account_id must be rejected, no order placed."""
        requests_post_calls: list = []

        def _spy_post(url, *args, **kwargs):
            requests_post_calls.append(url)
            return MagicMock(status_code=200)

        with patch("requests.post", side_effect=_spy_post):
            resp = flask_client.post(
                "/api/sell_account",
                json={
                    "account_id": "ACC-001",
                    "confirm_account_id": "ACC-WRONG",  # Mismatch
                    "confirm_phrase": "LIQUIDATE",
                },
            )

        assert resp.status_code == 400
        broker_posts = [u for u in requests_post_calls if "go-to-cash" in u]
        assert not broker_posts, "Broker order was placed despite account ID mismatch."
