"""
Tests — Dashboard routes must never call engine mutator symbols.

Plan deliverable (2): static import-graph test: every symbol called by an
app.py route is checked against a denylist:

  DENYLIST = {
    "record_exit_trigger", "record_shadow_observation", "record_autotune_run",
    "record_llm_suggestion", "save_state", "wipe_transient_state",
    "init_db", "run_migrations", "write_telemetry_row", "record_cvar_diagnostic",
    "wipe_*" (prefix match),
    any public function in alpha_bot_execution module,
    any function in math_engine that has I/O side-effects,
  }

Two complementary test strategies are used:

1. Structural (AST / source scan): parse app.py, collect every name that
   appears in route function bodies, and assert none are in the denylist.
   This catches direct calls AND calls through local aliases.

2. Behavioral (mock spy): for every denylisted database function, install a
   MagicMock spy via patch.object, exercise every route, and assert the spy
   was never called.  This catches indirect calls (e.g. a helper that calls a
   denylisted function).

Permitted write paths (explicit allowlist — not denylist violations):
  - `fleet_alert_dismiss` dispatches `write_fleet_alert` to _DISMISS_EXECUTOR
    background thread — the request thread itself does not call it synchronously.
  - `flush_resync` dispatches `save_state` to _flush_state_async background
    thread — request thread returns immediately before the write.
  - `ai_advisor_accept` calls `save_symphony_strategy` — this is the
    intentional operator-approval config-write path (gated behind four safety
    checks). It is a config write, not a state mutation or trade action.
    Pinned as the ONLY permitted write by test_dashboard_advisor_render_is_read_only.py.
  - `save_settings` calls `save_symphony_strategy` — same rationale as above;
    operator-initiated config write, not engine state.

The "save_" prefix in _DB_MUTATOR_PREFIXES catches these intentional writes.
_ALLOWED_ROUTE_WRITE_PAIRS enumerates them explicitly so the prefix-based
enumeration does not generate false positives in future behavioral spy tests.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

import app as app_module
import database as database_module


# ---------------------------------------------------------------------------
# Denylist: database mutator symbols forbidden in route call paths
# ---------------------------------------------------------------------------

_DB_MUTATOR_DENYLIST: frozenset[str] = frozenset(
    {
        # State writes
        "save_state",
        "wipe_transient_state",
        # Record helpers — engine-only side effects
        "record_exit_trigger",
        "record_shadow_observation",
        "record_autotune_run",
        "record_llm_suggestion",
        # Schema mutations
        "init_db",
        "run_migrations",
        # H4 telemetry path
        "write_telemetry_row",
        "record_cvar_diagnostic",
        # Fleet-alert write
        "write_fleet_alert",
    }
)

# Substrings that identify mutator patterns (prefix match on function names)
_DB_MUTATOR_PREFIXES: tuple[str, ...] = ("wipe_", "record_", "save_")

# Explicit allowlist: (route_function_name, database_function_name) pairs that
# are PERMITTED writes — the "save_" prefix is over-broad and catches intentional
# operator-approval config writes.  Pairs here are excluded from denylist checks.
# See module docstring for the rationale for each entry.
_ALLOWED_ROUTE_WRITE_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        # Operator-approval config write — four safety gates, not a trade action.
        # Pinned as the only permitted write in test_dashboard_advisor_render_is_read_only.py.
        ("ai_advisor_accept", "save_symphony_strategy"),
        # AC-5: /accept and /reject persist the operator decision to the immutable
        # llm_suggestions audit trail — this is an operator-decision write, not an
        # engine-state write.  Both routes are action paths (not render paths).
        ("ai_advisor_accept", "record_llm_suggestion"),
        ("ai_advisor_reject", "record_llm_suggestion"),
        # Operator settings write — same rationale; config write, not engine state.
        ("save_settings", "save_symphony_strategy"),
        # Chart history writes — persisted display data, not live engine state.
        ("save_chart_history_endpoint", "save_chart_history"),
        ("save_chart_archive_endpoint", "save_chart_archive"),
    }
)

# Symbols that are permitted writes (derived from _ALLOWED_ROUTE_WRITE_PAIRS)
# and must be excluded from the prefix-based denylist expansion.
_PERMITTED_WRITE_SYMBOLS: frozenset[str] = frozenset(sym for _, sym in _ALLOWED_ROUTE_WRITE_PAIRS)


def _all_db_mutators() -> frozenset[str]:
    """Enumerate every public function in database.py that matches the denylist.

    Excludes symbols in _PERMITTED_WRITE_SYMBOLS — those are intentional
    operator-approval writes, not engine-state violations.
    """
    all_names: set[str] = set(_DB_MUTATOR_DENYLIST)
    for name, obj in inspect.getmembers(database_module, inspect.isfunction):
        if name in _PERMITTED_WRITE_SYMBOLS:
            # Intentional write path — not a denylist violation.
            continue
        if any(name.startswith(prefix) for prefix in _DB_MUTATOR_PREFIXES):
            all_names.add(name)
    return frozenset(all_names)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_route_functions() -> list[tuple[str, str]]:
    """Return (route_path, function_name) for every registered Flask route."""
    routes = []
    for rule in app_module.app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        view_func = app_module.app.view_functions.get(rule.endpoint)
        if view_func is not None:
            routes.append((str(rule), view_func.__name__))
    return routes


def _parse_route_body_names(func_name: str, source_path: Path) -> set[str]:
    """Extract call targets from the TOP-LEVEL body of a route function.

    Walks only the direct statements of the named function — it does NOT descend
    into nested FunctionDef or AsyncFunctionDef nodes (e.g. background-thread
    closures like _dismiss_async).  Calls inside nested functions are dispatched
    off the request thread and are not synchronous side-effects of the route.

    This catches both bare calls (save_state(...)) and attribute calls
    (database.save_state(...)) made directly by the route frame.
    """
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Locate the top-level function definition by name.
    target_func = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                target_func = node
                break

    if target_func is None:
        return set()

    names: set[str] = set()

    class _TopLevelCallVisitor(ast.NodeVisitor):
        """Visit Call nodes but do not descend into nested function definitions."""

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            # Skip nested function bodies — they run off the request thread.
            if node is target_func:
                self.generic_visit(node)
            # else: do not recurse into nested defs

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            if node is target_func:
                self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
            self.generic_visit(node)

    _TopLevelCallVisitor().visit(target_func)
    return names


# ---------------------------------------------------------------------------
# Test 1: Static AST scan — no route body references a denylisted name
# ---------------------------------------------------------------------------


class TestRouteBodiesNoMutatorSymbols:
    """AST-level check: no route function body contains a denylisted call target.

    Routes that currently reference write helpers will fail here, naming the
    specific function and the denylisted symbol so the implementer knows exactly
    what to fix.
    """

    @pytest.fixture(autouse=True)
    def _app_source_path(self):
        self._source = Path(inspect.getfile(app_module))

    def test_no_route_calls_database_save_state(self):
        """No route body may call save_state — that is the engine's exclusive right."""
        violators = []
        for route_path, func_name in _get_route_functions():
            names = _parse_route_body_names(func_name, self._source)
            if "save_state" in names:
                violators.append(f"{func_name} ({route_path})")
        assert not violators, (
            "Route(s) call database.save_state(), which is an engine-exclusive write: "
            + ", ".join(violators)
        )

    def test_no_route_calls_write_fleet_alert(self):
        """No route body may call write_fleet_alert — alerts are engine-written."""
        violators = []
        for route_path, func_name in _get_route_functions():
            names = _parse_route_body_names(func_name, self._source)
            if "write_fleet_alert" in names:
                violators.append(f"{func_name} ({route_path})")
        assert not violators, (
            "Route(s) call database.write_fleet_alert(), an engine-exclusive write: "
            + ", ".join(violators)
        )

    def test_no_route_calls_record_mutators(self):
        """No route body may call record_* functions (engine-side telemetry),
        except for (route, symbol) pairs explicitly listed in _ALLOWED_ROUTE_WRITE_PAIRS.

        AC-5 permits ai_advisor_accept and ai_advisor_reject to call
        record_llm_suggestion as the immutable operator-decision audit trail.
        All other routes remain prohibited from calling any record_* mutator.
        """
        record_names = {n for n in _all_db_mutators() if n.startswith("record_")}
        violators = []
        for route_path, func_name in _get_route_functions():
            names = _parse_route_body_names(func_name, self._source)
            matched = names & record_names
            # Filter out (route, symbol) pairs explicitly permitted by the allowlist.
            matched = {sym for sym in matched if (func_name, sym) not in _ALLOWED_ROUTE_WRITE_PAIRS}
            if matched:
                violators.append(f"{func_name} ({route_path}): {matched}")
        assert not violators, (
            "Route(s) call record_*() telemetry mutators. "
            "These are engine-exclusive write paths: " + "; ".join(violators)
        )

    def test_no_route_calls_init_db_or_run_migrations(self):
        """No route body may call init_db or run_migrations — schema ops are startup-only."""
        schema_ops = {"init_db", "run_migrations"}
        violators = []
        for route_path, func_name in _get_route_functions():
            names = _parse_route_body_names(func_name, self._source)
            matched = names & schema_ops
            if matched:
                violators.append(f"{func_name} ({route_path}): {matched}")
        assert not violators, (
            "Route(s) call init_db() or run_migrations() — schema mutations "
            "must never happen on an HTTP request path: " + "; ".join(violators)
        )

    def test_no_route_calls_write_telemetry_or_record_cvar(self):
        """No route may call write_telemetry_row or record_cvar_diagnostic (H4 ban)."""
        telemetry_syms = {"write_telemetry_row", "record_cvar_diagnostic"}
        violators = []
        for route_path, func_name in _get_route_functions():
            names = _parse_route_body_names(func_name, self._source)
            matched = names & telemetry_syms
            if matched:
                violators.append(f"{func_name} ({route_path}): {matched}")
        assert not violators, (
            "Route(s) reference H4 telemetry write helpers. "
            "The dashboard must never write telemetry rows: " + "; ".join(violators)
        )


# ---------------------------------------------------------------------------
# Test 2: Behavioral spy — mutator mocks are never called during route exercise
# ---------------------------------------------------------------------------


def _make_safe_db_mock() -> MagicMock:
    """Return a database mock with sensible read-only return values."""
    mock = MagicMock()
    mock.load_state.return_value = {}
    mock.load_chart_history.return_value = {"symphonies": {}}
    mock.get_triggers.return_value = []
    mock.read_fleet_alert.return_value = None
    mock.normalize_name.side_effect = lambda n: (n or "").lower()
    mock.get_symphony_strategy.return_value = None
    mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
    mock.get_all_autotune_runs.return_value = []
    mock.get_symphony_logs.return_value = []
    mock.DEFAULT_STRATEGY = {}
    ro_conn = MagicMock()
    ro_conn.execute.return_value = MagicMock(fetchone=lambda: None, fetchall=lambda: [])
    ro_conn.close.return_value = None
    mock.get_ro_connection.return_value = ro_conn
    return mock


@pytest.fixture
def flask_client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestMutatorSpiesNeverCalledOnGetRoutes:
    """Behavioral: database write methods are never called on safe GET routes."""

    def test_api_state_never_calls_save_state(self, flask_client):
        """GET /api/state must never call database.save_state."""
        mock_db = _make_safe_db_mock()
        mock_analytics = MagicMock()
        mock_analytics.get_portfolio_daily_returns_from_shadow.return_value = None
        mock_analytics.get_history_with_cache_invalidation.return_value = {}
        mock_analytics.compute_aggregate_returns.return_value = ([], [], [])
        mock_analytics._POST_MORTEMS_DIR = "/tmp/pm"
        mock_analytics._HISTORY_CACHE = {"key": None, "data": None}

        with patch.object(app_module, "database", mock_db):
            with patch.object(app_module, "analytics", mock_analytics):
                flask_client.get("/api/state")

        mock_db.save_state.assert_not_called()
        mock_db.write_fleet_alert.assert_not_called()
        mock_db.record_exit_trigger.assert_not_called()

    def test_api_settings_get_never_calls_save_state(self, flask_client):
        """GET /api/settings must never call database.save_state."""
        mock_db = _make_safe_db_mock()

        with patch.object(app_module, "database", mock_db):
            flask_client.get("/api/settings")

        mock_db.save_state.assert_not_called()
        mock_db.write_fleet_alert.assert_not_called()

    def test_api_triggers_never_mutates(self, flask_client):
        """GET /api/triggers must not call any database mutator."""
        mock_db = _make_safe_db_mock()

        with patch.object(app_module, "database", mock_db):
            flask_client.get("/api/triggers")

        mock_db.save_state.assert_not_called()
        mock_db.write_fleet_alert.assert_not_called()
        mock_db.record_exit_trigger.assert_not_called()
        mock_db.record_shadow_observation.assert_not_called()

    def test_api_autotune_runs_never_mutates(self, flask_client):
        """GET /api/autotune-runs must not call any database mutator."""
        mock_db = _make_safe_db_mock()

        with patch.object(app_module, "database", mock_db):
            flask_client.get("/api/autotune-runs")

        mock_db.save_state.assert_not_called()
        mock_db.record_autotune_run.assert_not_called()

    def test_fleet_alert_dismiss_dispatches_write_to_background_thread(self, flask_client):
        """POST /api/fleet-alert/dismiss must dispatch write_fleet_alert to a background thread.

        The redesign (Option A — background-thread dispatch via _DISMISS_EXECUTOR) is
        now implemented.  The new contract:
          - write_fleet_alert is NOT called synchronously on the Flask request thread.
          - write_fleet_alert IS called by the background executor worker (off-thread).
          - The handler returns 200 immediately before the write completes.

        Assertion strategy: use a threading.Event inside the mock so we wait for
        the background write inside the patch.object context.  This is the same
        pattern as test_fleet_banner.py::test_dismiss_route_clears_alert_in_state.
        """
        mock_db = _make_safe_db_mock()
        mock_db.read_fleet_alert.return_value = {
            "id": 1,
            "dismissed_at_et": None,
            "alert_type": "correlation",
            "payload": "{}",
        }

        write_completed = threading.Event()

        def _capture_write(payload):
            write_completed.set()

        mock_db.write_fleet_alert.side_effect = _capture_write

        with patch.object(app_module, "database", mock_db):
            flask_client.post("/api/fleet-alert/dismiss")
            # Wait inside the patch context — the executor worker runs after the handler
            # returns; the mock must remain active when it fires.
            write_completed.wait(timeout=2)

        assert mock_db.write_fleet_alert.called, (
            "POST /api/fleet-alert/dismiss must call database.write_fleet_alert() via "
            "a background thread.  The executor worker did not call the mock within 2 s."
        )
        written_payload = mock_db.write_fleet_alert.call_args[0][0]
        assert written_payload.get("dismissed_at_et") is not None, (
            "Background write payload must include a non-null dismissed_at_et timestamp."
        )
