"""
RED tests for Sprint 3 port-settings cleanup — AX-1, AX-2, AX-3.

AX-1 (Option B): save_settings still writes EXIT_AUTHORITY to .env when the
    settings UI posts it; the engine no longer reads the key for decisions (the
    import was removed in Wave 2). The toggle is a visible no-op — UI unchanged,
    env write preserved, engine ignores.

AX-2 (KEEP): get_exit_authority_badge_context and build_restart_notice_context
    are display-only helpers that remain importable and callable. No regression
    allowed.

AX-3 (REMOVE): rebase_port_state_on_composition_change and
    new_day_reset_port_state served the port-level dispatch path (SITE-A2/A3)
    that was removed in Wave 2. Both helpers must be absent from database after
    this cycle.

Test impact (per manifest §6):
    - TestCompositionChangeReset + TestNewDayResetSentinel deleted from
      test_port_state_schema.py (they import the removed helpers).
    - This file replaces them with assertion-of-absence tests.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import logging
import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# AX-3: rebase_port_state_on_composition_change must be REMOVED
# ---------------------------------------------------------------------------


class TestRebasePortStateOnCompositionChangeRemoved:
    """AX-3: rebase_port_state_on_composition_change no longer exported from database.

    Callers of this helper were exclusively the port-level dispatch block
    (SITE-A2) — removed in Wave 2. Keeping a dead helper with live tests would
    give false confidence that the removal is complete.
    """

    def test_rebase_helper_absent_from_database_module(self):
        """database must not expose rebase_port_state_on_composition_change."""
        import database

        importlib.reload(database)
        assert not hasattr(database, "rebase_port_state_on_composition_change"), (
            "AX-3: rebase_port_state_on_composition_change was a SITE-A2-only helper; "
            "it must be removed from database after the port dispatch block is gone"
        )

    def test_rebase_helper_not_importable_by_name(self):
        """Importing the removed helper by name must raise ImportError or AttributeError."""
        import database

        with pytest.raises((ImportError, AttributeError)):
            # This line must fail — the function must not be reachable
            _ = database.rebase_port_state_on_composition_change


# ---------------------------------------------------------------------------
# AX-3: new_day_reset_port_state must be REMOVED
# ---------------------------------------------------------------------------


class TestNewDayResetPortStateRemoved:
    """AX-3: new_day_reset_port_state no longer exported from database.

    grep confirms zero live callers in alpha_bot_execution.py and app.py after
    SITE-A2/A3 removal. Retaining a zero-caller function with tests is dead
    weight and a maintenance hazard.
    """

    def test_new_day_reset_absent_from_database_module(self):
        """database must not expose new_day_reset_port_state."""
        import database

        importlib.reload(database)
        assert not hasattr(database, "new_day_reset_port_state"), (
            "AX-3: new_day_reset_port_state has no live callers after port dispatch removal; "
            "it must be removed from database"
        )

    def test_new_day_reset_not_importable_by_name(self):
        """Importing the removed helper by name must raise ImportError or AttributeError."""
        import database

        with pytest.raises((ImportError, AttributeError)):
            _ = database.new_day_reset_port_state


# ---------------------------------------------------------------------------
# AX-3: display-path helpers must NOT be removed
# ---------------------------------------------------------------------------


class TestPortStateDisplayHelpersPreserved:
    """AX-3: read_port_state and get_all_port_states are display-path helpers; must stay."""

    def test_read_port_state_still_importable(self):
        import database

        assert hasattr(database, "read_port_state"), (
            "AX-3: read_port_state supports dashboard display (SITE-D4); must not be removed"
        )
        assert callable(database.read_port_state)

    def test_get_all_port_states_still_importable(self):
        import database

        assert hasattr(database, "get_all_port_states"), (
            "AX-3: get_all_port_states supports dashboard display (SITE-D4); must not be removed"
        )
        assert callable(database.get_all_port_states)

    def test_write_port_state_still_importable(self):
        """write_port_state may still be needed by display writes; preserved until grep confirms dead."""
        import database

        assert hasattr(database, "write_port_state"), (
            "AX-3: write_port_state retained pending final grep — do not remove until confirmed dead"
        )

    def test_clear_port_state_still_importable(self):
        import database

        assert hasattr(database, "clear_port_state"), (
            "AX-3: clear_port_state retained pending final grep — do not remove until confirmed dead"
        )


# ---------------------------------------------------------------------------
# AX-1 (Option B): save_settings writes EXIT_AUTHORITY to .env
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ax1_client():
    """Flask test client with isolated temp DB and .env for AX-1 route tests."""
    fd_db, db_path = tempfile.mkstemp(suffix=".db", prefix="test_ax1_")
    fd_env, env_path = tempfile.mkstemp(suffix=".env", prefix="test_ax1_")
    os.close(fd_db)
    os.close(fd_env)

    saved_db = os.environ.get("DB_PATH")
    saved_ea = os.environ.get("EXIT_AUTHORITY")

    os.environ["DB_PATH"] = db_path
    os.environ["EXIT_AUTHORITY"] = "per_symphony"

    import database

    _old_level = logging.getLogger().level
    logging.getLogger().setLevel(logging.CRITICAL)
    try:
        database.init_db()
    finally:
        logging.getLogger().setLevel(_old_level)

    import app as app_module

    app_module.ENV_FILE_PATH = env_path
    app_module.app.config["TESTING"] = True

    with open(env_path, "w") as fh:
        fh.write("EXIT_AUTHORITY=per_symphony\n")

    with app_module.app.test_client() as client:
        yield client, env_path

    os.environ.pop("DB_PATH", None)
    os.environ.pop("EXIT_AUTHORITY", None)
    if saved_db is not None:
        os.environ["DB_PATH"] = saved_db
    if saved_ea is not None:
        os.environ["EXIT_AUTHORITY"] = saved_ea
    try:
        os.unlink(db_path)
        os.unlink(env_path)
    except OSError:
        pass


class TestAX1SaveSettingsWritesExitAuthority:
    """AX-1 (Option B): save_settings must continue to write EXIT_AUTHORITY to .env.

    The PM chose Option B: leave UI and env-write untouched. The toggle is a
    visible no-op — the engine no longer reads it for decisions, but the setting
    persists for operator awareness.
    """

    def test_post_exit_authority_returns_success(self, ax1_client):
        client, _ = ax1_client
        resp = client.post(
            "/api/settings",
            data=json.dumps({"globals": {"EXIT_AUTHORITY": "per_symphony"}}),
            content_type="application/json",
        )
        data = json.loads(resp.data)
        assert data.get("status") == "success", (
            "AX-1: POST /api/settings with EXIT_AUTHORITY must return status=success "
            "(Option B: write preserved)"
        )

    def test_exit_authority_written_to_env_file(self, ax1_client):
        """The env key must appear in the .env file after a POST."""
        client, env_path = ax1_client
        client.post(
            "/api/settings",
            data=json.dumps({"globals": {"EXIT_AUTHORITY": "per_symphony"}}),
            content_type="application/json",
        )
        with open(env_path) as fh:
            content = fh.read()
        assert "EXIT_AUTHORITY" in content, (
            "AX-1 Option B: EXIT_AUTHORITY must be written to .env on save_settings POST"
        )

    def test_exit_authority_changed_at_recorded_on_post(self, ax1_client):
        """AC-P2.2.4: toggle change must stamp _exit_authority_changed_at in .env."""
        client, env_path = ax1_client
        client.post(
            "/api/settings",
            data=json.dumps({"globals": {"EXIT_AUTHORITY": "per_symphony"}}),
            content_type="application/json",
        )
        with open(env_path) as fh:
            content = fh.read()
        assert "_exit_authority_changed_at" in content or "EXIT_AUTHORITY_CHANGED_AT" in content, (
            "AC-P2.2.4: _exit_authority_changed_at must be stamped when EXIT_AUTHORITY is posted"
        )

    def test_engine_does_not_consume_exit_authority_on_execution_path(self):
        """AX-1 engine contract: alpha_bot_execution.py must not import get_exit_authority.

        Wave 2 removed the engine consumption. This test guards against accidental
        re-introduction by statically scanning the source file for the import.
        """
        worktree_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        source_path = os.path.join(worktree_root, "alpha_bot_execution.py")
        with open(source_path, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=source_path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                # ImportFrom: check module + names
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    names = [alias.name for alias in node.names]
                    if "exit_authority" in module and "get_exit_authority" in names:
                        pytest.fail(
                            "AX-1: alpha_bot_execution.py must not import get_exit_authority "
                            f"(found at line {node.lineno}); engine no longer consumes the toggle"
                        )
                # Regular import: flag any import of get_exit_authority
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "get_exit_authority" in alias.name:
                            pytest.fail(
                                "AX-1: alpha_bot_execution.py must not import get_exit_authority"
                            )

    def test_engine_does_not_call_get_exit_authority_at_runtime(self):
        """AX-1 complementary check: get_exit_authority call absent from execution source."""
        worktree_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        source_path = os.path.join(worktree_root, "alpha_bot_execution.py")
        with open(source_path, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=source_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # Direct call: get_exit_authority(...)
                if isinstance(func, ast.Name) and func.id == "get_exit_authority":
                    pytest.fail(
                        "AX-1: alpha_bot_execution.py must not call get_exit_authority() "
                        f"(found at line {node.lineno})"
                    )
                # Attribute call: module.get_exit_authority(...)
                if isinstance(func, ast.Attribute) and func.attr == "get_exit_authority":
                    pytest.fail(
                        "AX-1: alpha_bot_execution.py must not call get_exit_authority() "
                        f"(found at line {node.lineno})"
                    )


# ---------------------------------------------------------------------------
# AX-2: badge helpers remain importable and correct
# ---------------------------------------------------------------------------


class TestAX2BadgeHelpersPreserved:
    """AX-2: display helpers in engine/exit_authority.py must survive this cycle.

    PM Decision: AX-1 Option B means the badge still has data to show. Both
    helpers are KEEP-DISPLAY and must not be removed by this cycle.
    """

    def test_get_exit_authority_badge_context_importable(self):
        from engine.exit_authority import get_exit_authority_badge_context

        assert callable(get_exit_authority_badge_context), (
            "AX-2: get_exit_authority_badge_context must remain importable (KEEP-DISPLAY)"
        )

    def test_build_restart_notice_context_importable(self):
        from engine.exit_authority import build_restart_notice_context

        assert callable(build_restart_notice_context), (
            "AX-2: build_restart_notice_context must remain importable (KEEP-DISPLAY)"
        )

    def test_badge_context_returns_required_keys_for_per_symphony(self, monkeypatch):
        """Badge context must include is_degraded, label, color, authority."""
        monkeypatch.setenv("EXIT_AUTHORITY", "per_symphony")
        from engine.exit_authority import get_exit_authority_badge_context

        ctx = get_exit_authority_badge_context()
        for key in ("is_degraded", "label", "color", "authority"):
            assert key in ctx, f"AX-2: get_exit_authority_badge_context must return key '{key}'"

    def test_badge_context_per_symphony_not_degraded(self, monkeypatch):
        """per_symphony is a recognized value — badge must not be degraded."""
        monkeypatch.setenv("EXIT_AUTHORITY", "per_symphony")
        from engine.exit_authority import get_exit_authority_badge_context

        ctx = get_exit_authority_badge_context()
        assert ctx["is_degraded"] is False, (
            "AX-2: per_symphony is valid; badge must not show degraded state"
        )

    def test_badge_context_unrecognized_value_is_degraded(self, monkeypatch):
        """Unrecognized EXIT_AUTHORITY value must produce a degraded badge (AC-P2.12.4)."""
        monkeypatch.setenv("EXIT_AUTHORITY", "garbage_value")
        from engine.exit_authority import get_exit_authority_badge_context

        ctx = get_exit_authority_badge_context()
        assert ctx["is_degraded"] is True, (
            "AX-2 AC-P2.12.4: unrecognized EXIT_AUTHORITY must produce degraded badge"
        )

    def test_restart_notice_required_when_toggle_changed_after_daemon_start(self):
        """Toggle changed after daemon start -> restart_required=True."""
        from engine.exit_authority import build_restart_notice_context

        ctx = build_restart_notice_context(
            toggle_changed_at="2024-11-01T10:05:00Z",
            daemon_started_at="2024-11-01T09:00:00Z",
        )
        assert ctx["restart_required"] is True, (
            "AX-2: restart notice must fire when toggle changed after daemon start"
        )

    def test_restart_notice_clears_after_restart(self):
        """Daemon restarted after toggle -> restart_required=False."""
        from engine.exit_authority import build_restart_notice_context

        ctx = build_restart_notice_context(
            toggle_changed_at="2024-11-01T10:05:00Z",
            daemon_started_at="2024-11-01T10:10:00Z",
        )
        assert ctx["restart_required"] is False, (
            "AX-2: restart notice must clear when daemon restarted after toggle change"
        )

    def test_decision_path_functions_still_absent_from_execution_engine(self):
        """AX-2 scope guard: decision-path functions (get_exit_authority, is_authoritative,
        validate_exit_authority) are not called from alpha_bot_execution.py.

        The badge helpers are display-only in app.py; the engine must not call them.
        """
        worktree_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        source_path = os.path.join(worktree_root, "alpha_bot_execution.py")
        with open(source_path, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=source_path)
        decision_fns = {"get_exit_authority", "is_authoritative", "validate_exit_authority"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name in decision_fns:
                    pytest.fail(
                        f"AX-2: alpha_bot_execution.py must not call {name}() — "
                        "engine no longer uses EXIT_AUTHORITY for decisions"
                    )


# ---------------------------------------------------------------------------
# Regression: settings page renders without exception
# ---------------------------------------------------------------------------


class TestSettingsPageNoRegression:
    """AX-1 UI: settings GET still works after this cycle's changes."""

    def test_get_settings_returns_200(self, ax1_client):
        client, _ = ax1_client
        resp = client.get("/api/settings")
        assert resp.status_code == 200, (
            "AX-1: GET /api/settings must return 200 — no UI regression from this cycle"
        )

    def test_get_settings_includes_exit_authority(self, ax1_client):
        """EXIT_AUTHORITY must still appear in GET /api/settings globals (display field)."""
        client, _ = ax1_client
        resp = client.get("/api/settings")
        data = json.loads(resp.data)
        assert "EXIT_AUTHORITY" in data.get("globals", {}), (
            "AX-1: GET /api/settings must include EXIT_AUTHORITY in globals "
            "(Option B: UI toggle remains visible)"
        )

    def test_get_settings_exit_authority_not_masked(self, ax1_client):
        """EXIT_AUTHORITY is not a secret; it must not be masked in the GET response."""
        client, _ = ax1_client
        resp = client.get("/api/settings")
        data = json.loads(resp.data)
        ea_val = data.get("globals", {}).get("EXIT_AUTHORITY", "")
        assert ea_val != "", (
            "AX-1: EXIT_AUTHORITY must not be masked (returned as empty string) in settings response"
        )
