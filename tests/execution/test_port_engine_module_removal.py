"""
RED tests for port-engine-module-removal (Sprint 3, Stream A — SITE-C1).

Asserts the POST-removal contract for SITE-C1 per
docs/audit/sprint-3-port-removal-manifest.md §3.

Removal targets:
  engine/dual_altitude.py  — ENTIRE module deleted.
  engine/exit_authority.py — Decision-path functions removed:
      get_exit_authority, validate_exit_authority, is_authoritative,
      write_exit_authority_to_env.

Preserved (AX-2 option b — display helpers stay):
  engine/exit_authority.py — get_exit_authority_badge_context,
      build_restart_notice_context.

Invariants that MUST NOT change:
  - app.py get_api_state_dict must not import get_exit_authority from
    engine.exit_authority (since that function is deleted); it must source
    exit_authority by another means (e.g. direct os.getenv) or omit the field.
  - tests/app/conftest.py and tests/dashboard/conftest.py comment text may
    reference get_exit_authority but do not import it — they are fine.
  - tests/portmode/test_settings_restart_notice.py imports the two KEPT display
    helpers; those imports must still resolve GREEN.
  - tests/portmode/test_dual_altitude_dashboard.py imports dashboard.port_row —
    that module is NOT removed; it stays as a display-only module. However the
    test is retained here as a coverage note: dashboard.port_row must NOT import
    from engine.exit_authority (the is_authoritative local variable in port_row.py
    is a plain equality comparison, not an import).
  - Symphony-level math, per-symphony execution queue, dashboard KEEP-DISPLAY
    surfaces — all untouched.

FAIL conditions (i.e. what makes these RED):
  - engine.dual_altitude is currently importable → ImportError tests will pass
    once the module is removed (currently they fail because the module exists).
  - engine.exit_authority still exports get_exit_authority etc. → AttributeError
    tests will pass once those symbols are gone.
  - app.py still imports get_exit_authority lazily inside get_api_state_dict →
    the AST probe will fail until that import is removed.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import types

import pytest

# ---------------------------------------------------------------------------
# SITE-C1-A: engine/dual_altitude.py — entire module removed
# ---------------------------------------------------------------------------


class TestDualAltitudeModuleRemoved:
    """engine.dual_altitude must not exist after SITE-C1 removal."""

    def test_dual_altitude_module_is_not_importable(self):
        """
        After removal, importing engine.dual_altitude must raise ImportError
        (or ModuleNotFoundError which is a subclass). Currently FAILS because
        the module still exists.
        """
        with pytest.raises((ImportError, ModuleNotFoundError)):
            importlib.import_module("engine.dual_altitude")

    def test_compute_for_altitude_not_accessible(self):
        """
        compute_for_altitude must not be accessible via any engine.* path after
        the module is deleted.
        """
        try:
            engine_pkg = importlib.import_module("engine")
        except ImportError:
            pytest.skip("engine package itself not importable — skip sentinel test")

        assert not hasattr(engine_pkg, "compute_for_altitude"), (
            "compute_for_altitude must not be re-exported from engine/__init__.py"
        )

    def test_initialize_port_state_if_absent_not_accessible(self):
        """
        initialize_port_state_if_absent must not be accessible after the module
        is deleted.
        """
        try:
            engine_pkg = importlib.import_module("engine")
        except ImportError:
            pytest.skip("engine package itself not importable")

        assert not hasattr(engine_pkg, "initialize_port_state_if_absent"), (
            "initialize_port_state_if_absent must not be re-exported from engine/__init__.py"
        )

    def test_dual_altitude_file_does_not_exist(self):
        """
        The physical file engine/dual_altitude.py must not exist after removal.
        Confirms the implementer deleted the file, not just commented it out.
        """
        # The worktree is the source tree — path is relative to this file's location
        engine_dir = pathlib.Path(__file__).parent.parent.parent / "engine"
        candidate = engine_dir / "dual_altitude.py"
        assert not candidate.exists(), (
            f"engine/dual_altitude.py still exists at {candidate}; "
            "SITE-C1 requires the file to be deleted"
        )


# ---------------------------------------------------------------------------
# SITE-C1-B: engine/exit_authority.py — decision-path functions removed
# ---------------------------------------------------------------------------


class TestExitAuthorityDecisionFunctionsRemoved:
    """
    get_exit_authority, validate_exit_authority, is_authoritative, and
    write_exit_authority_to_env must not be present in engine.exit_authority
    after SITE-C1 removal.
    """

    @pytest.fixture(scope="class")
    def exit_authority_module(self):
        """
        Load engine.exit_authority.  If it is not importable at all the tests
        are vacuously green (the whole module was deleted) — but AX-2 says the
        display helpers stay, so the module MUST still exist.  Fail explicitly
        if the module is gone.
        """
        try:
            return importlib.import_module("engine.exit_authority")
        except (ImportError, ModuleNotFoundError):
            pytest.fail(
                "engine.exit_authority is not importable at all — the display "
                "helpers (get_exit_authority_badge_context, build_restart_notice_context) "
                "must survive per AX-2 option (b). The entire module must not be deleted."
            )

    def test_get_exit_authority_not_present(self, exit_authority_module):
        """get_exit_authority is a decision-path function — must be removed."""
        assert not hasattr(exit_authority_module, "get_exit_authority"), (
            "engine.exit_authority.get_exit_authority must not exist after SITE-C1 removal"
        )

    def test_validate_exit_authority_not_present(self, exit_authority_module):
        """validate_exit_authority is a decision-path function — must be removed."""
        assert not hasattr(exit_authority_module, "validate_exit_authority"), (
            "engine.exit_authority.validate_exit_authority must not exist after SITE-C1 removal"
        )

    def test_is_authoritative_not_present(self, exit_authority_module):
        """is_authoritative is a decision-path function — must be removed."""
        assert not hasattr(exit_authority_module, "is_authoritative"), (
            "engine.exit_authority.is_authoritative must not exist after SITE-C1 removal"
        )

    def test_write_exit_authority_to_env_not_present(self, exit_authority_module):
        """
        write_exit_authority_to_env writes the decision toggle to .env —
        dead code once the toggle has no effect.  Must be removed.
        """
        assert not hasattr(exit_authority_module, "write_exit_authority_to_env"), (
            "engine.exit_authority.write_exit_authority_to_env must not exist "
            "after SITE-C1 removal"
        )

    def test_exit_authority_constants_per_symphony_removed(self, exit_authority_module):
        """
        EXIT_AUTHORITY_PER_SYMPHONY and EXIT_AUTHORITY_PORT_LEVEL are module-level
        constants that name the toggle values.  With the toggle removed they are
        dead module-level names.  Both must be absent.
        """
        assert not hasattr(exit_authority_module, "EXIT_AUTHORITY_PER_SYMPHONY"), (
            "EXIT_AUTHORITY_PER_SYMPHONY constant must be removed with the decision-path functions"
        )
        assert not hasattr(exit_authority_module, "EXIT_AUTHORITY_PORT_LEVEL"), (
            "EXIT_AUTHORITY_PORT_LEVEL constant must be removed with the decision-path functions"
        )

    def test_valid_authorities_set_removed(self, exit_authority_module):
        """
        _VALID_AUTHORITIES was the validation set for the toggle values.
        With the decision functions removed it is dead — must not persist.
        """
        assert not hasattr(exit_authority_module, "_VALID_AUTHORITIES"), (
            "_VALID_AUTHORITIES set must be removed with the decision-path functions"
        )


# ---------------------------------------------------------------------------
# SITE-C1-C: AX-2 display helpers preserved (invariant — must NOT be removed)
# ---------------------------------------------------------------------------


class TestExitAuthorityDisplayHelpersPreserved:
    """
    AX-2 option (b): get_exit_authority_badge_context and
    build_restart_notice_context are display-only; they stay.
    These tests will PASS once the decision-path functions are removed
    but the display helpers remain.  They will FAIL NOW only if the module
    itself is broken — so they are currently GREEN (the helpers exist) and
    will stay GREEN after removal.  They exist here to lock the invariant.
    """

    @pytest.fixture(scope="class")
    def ea_module(self):
        return importlib.import_module("engine.exit_authority")

    def test_get_exit_authority_badge_context_still_callable(self, ea_module):
        """get_exit_authority_badge_context is a KEEP-DISPLAY function — must survive."""
        assert callable(getattr(ea_module, "get_exit_authority_badge_context", None)), (
            "engine.exit_authority.get_exit_authority_badge_context must still be callable "
            "after SITE-C1 — it is a display helper (AX-2 option b)"
        )

    def test_build_restart_notice_context_still_callable(self, ea_module):
        """build_restart_notice_context is a KEEP-DISPLAY function — must survive."""
        assert callable(getattr(ea_module, "build_restart_notice_context", None)), (
            "engine.exit_authority.build_restart_notice_context must still be callable "
            "after SITE-C1 — it is a display helper (AX-2 option b)"
        )

    def test_badge_context_returns_dict_with_is_degraded(self, monkeypatch):
        """
        Post-removal smoke-test: badge context for a valid env-var value returns
        a dict with an is_degraded key.  No producer value is hardcoded — only
        the dict-shape contract is asserted.
        """
        monkeypatch.setenv("EXIT_AUTHORITY", "per_symphony")
        from engine.exit_authority import get_exit_authority_badge_context
        ctx = get_exit_authority_badge_context()
        assert isinstance(ctx, dict), "get_exit_authority_badge_context must return a dict"
        assert "is_degraded" in ctx, "returned dict must contain 'is_degraded' key"

    def test_restart_notice_context_returns_dict_with_restart_required(self):
        """
        Post-removal smoke-test: restart notice context returns a dict with
        restart_required key.  Value is derived purely from the input arguments.
        """
        from engine.exit_authority import build_restart_notice_context
        ctx = build_restart_notice_context(
            toggle_changed_at=None,
            daemon_started_at="2024-01-01T09:00:00Z",
        )
        assert isinstance(ctx, dict), "build_restart_notice_context must return a dict"
        assert "restart_required" in ctx, "returned dict must contain 'restart_required' key"
        # toggle_changed_at=None -> no restart needed — value derived from documented spec
        assert ctx["restart_required"] is False, (
            "toggle_changed_at=None must yield restart_required=False "
            "(spec: toggle never changed, no restart needed)"
        )


# ---------------------------------------------------------------------------
# SITE-C1-D: app.py no longer imports get_exit_authority (AST probe)
# ---------------------------------------------------------------------------


class TestAppNoGetExitAuthorityImport:
    """
    After SITE-C1, app.py must not import get_exit_authority from
    engine.exit_authority anywhere — that function no longer exists.

    Scans app.py's AST for ImportFrom nodes targeting engine.exit_authority
    that name get_exit_authority.  Finds lazy (inside-function) imports too.
    """

    @pytest.fixture(scope="class")
    def app_ast(self):
        app_path = pathlib.Path(__file__).parent.parent.parent / "app.py"
        return ast.parse(app_path.read_text(encoding="utf-8"))

    def _collect_import_from_names(
        self, tree: ast.AST, module: str
    ) -> list[str]:
        """Return all names imported from `module` anywhere in the AST."""
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == module:
                names.extend(alias.name for alias in node.names)
        return names

    def test_app_does_not_import_get_exit_authority(self, app_ast):
        """
        app.py must have no ``from engine.exit_authority import get_exit_authority``
        statement (including lazy imports inside functions like get_api_state_dict).
        Currently FAILS because line ~654 contains exactly that import.
        """
        imported = self._collect_import_from_names(app_ast, "engine.exit_authority")
        assert "get_exit_authority" not in imported, (
            "app.py must not import get_exit_authority from engine.exit_authority — "
            "that function is removed in SITE-C1; app must source exit_authority "
            "via os.getenv directly or an equivalent mechanism"
        )

    def test_app_does_not_import_is_authoritative(self, app_ast):
        """
        is_authoritative is removed in SITE-C1; no app.py import must reference it.
        """
        imported = self._collect_import_from_names(app_ast, "engine.exit_authority")
        assert "is_authoritative" not in imported, (
            "app.py must not import is_authoritative from engine.exit_authority "
            "(function removed in SITE-C1)"
        )

    def test_app_does_not_import_from_dual_altitude(self, app_ast):
        """
        engine.dual_altitude is deleted in SITE-C1; app.py must have zero
        imports from it.
        """
        imported = self._collect_import_from_names(app_ast, "engine.dual_altitude")
        assert not imported, (
            f"app.py must not import from engine.dual_altitude (module deleted in SITE-C1); "
            f"found imports: {imported}"
        )

    def test_get_api_state_dict_still_returns_exit_authority_key(self):
        """
        Regression guard: even after removing the get_exit_authority import,
        get_api_state_dict must still include 'exit_authority' in its return
        dict — display consumers depend on it (SITE-D2 KEEP-DISPLAY).

        Exercises the live function via an in-process call with a minimal mock
        so as not to need a running DB.  Network and DB are mocked; only the
        return shape is asserted.
        """
        import os
        import tempfile
        import logging
        from unittest.mock import patch, MagicMock

        fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_getapistate_c1_")
        os.close(fd)
        original_db = os.environ.get("DB_PATH")
        os.environ["DB_PATH"] = db_path
        os.environ.setdefault("EXIT_AUTHORITY", "per_symphony")

        try:
            import database
            _old = logging.getLogger().level
            logging.getLogger().setLevel(logging.CRITICAL)
            try:
                database.init_db()
            finally:
                logging.getLogger().setLevel(_old)

            import app as app_module
            result = app_module.get_api_state_dict()
            assert "exit_authority" in result, (
                "get_api_state_dict must still include 'exit_authority' in its "
                "return dict after SITE-C1 — display consumers depend on it (SITE-D2)"
            )
        finally:
            os.environ.pop("DB_PATH", None)
            if original_db is not None:
                os.environ["DB_PATH"] = original_db
            try:
                os.unlink(db_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# SITE-C1-E: production code sweep — no remaining imports of removed symbols
# ---------------------------------------------------------------------------


class TestNoRemainingProductionImports:
    """
    No production .py file (outside tests/) may import from engine.dual_altitude
    or import the removed decision-path names from engine.exit_authority.

    AST-based, so it catches lazy in-function imports too.
    """

    @pytest.fixture(scope="class")
    def project_root(self):
        return pathlib.Path(__file__).parent.parent.parent

    def _production_py_files(self, root: pathlib.Path):
        """Yield .py files that are not under tests/, __pycache__/, or git worktree dirs.

        `.claude/` houses git worktrees (`.claude/worktrees/`) and audit worktrees
        (`.claude/audit-worktrees/`) from prior sessions whose stale .py snapshots
        must NOT be scanned as production code. See project memory
        `project_blast_radius_scanner_worktree_interaction`.
        """
        for p in root.rglob("*.py"):
            parts = p.relative_to(root).parts
            if (
                "tests" not in parts
                and "__pycache__" not in parts
                and ".claude" not in parts
            ):
                yield p

    def _import_from_names_in_file(
        self, filepath: pathlib.Path, module: str
    ) -> list[str]:
        try:
            tree = ast.parse(filepath.read_text(encoding="utf-8"))
        except SyntaxError:
            return []
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == module:
                names.extend(alias.name for alias in node.names)
        return names

    def test_no_production_file_imports_from_dual_altitude(self, project_root):
        """
        No production file may import from engine.dual_altitude after the module
        is deleted.
        """
        violations: list[str] = []
        for py_file in self._production_py_files(project_root):
            names = self._import_from_names_in_file(py_file, "engine.dual_altitude")
            if names:
                rel = py_file.relative_to(project_root)
                violations.append(f"{rel}: imports {names}")
        assert not violations, (
            "Production files still import from engine.dual_altitude (DELETED in SITE-C1):\n"
            + "\n".join(violations)
        )

    def test_no_production_file_imports_removed_exit_authority_names(self, project_root):
        """
        No production file may import the removed decision-path names from
        engine.exit_authority: get_exit_authority, validate_exit_authority,
        is_authoritative, write_exit_authority_to_env.
        """
        _removed = {
            "get_exit_authority",
            "validate_exit_authority",
            "is_authoritative",
            "write_exit_authority_to_env",
        }
        violations: list[str] = []
        for py_file in self._production_py_files(project_root):
            names = self._import_from_names_in_file(py_file, "engine.exit_authority")
            bad = set(names) & _removed
            if bad:
                rel = py_file.relative_to(project_root)
                violations.append(f"{rel}: imports {sorted(bad)}")
        assert not violations, (
            "Production files still import removed symbols from engine.exit_authority:\n"
            + "\n".join(violations)
        )

    def test_dashboard_port_row_does_not_import_from_exit_authority(self, project_root):
        """
        dashboard/port_row.py uses ``is_authoritative`` only as a local variable
        name (``is_authoritative = row_altitude == exit_authority``).  It must NOT
        import is_authoritative from engine.exit_authority — that would be a broken
        import after SITE-C1.  This test confirms the local-variable pattern is
        retained and no import is present.
        """
        port_row = project_root / "dashboard" / "port_row.py"
        if not port_row.exists():
            pytest.skip("dashboard/port_row.py does not exist — skip import check")
        names = self._import_from_names_in_file(port_row, "engine.exit_authority")
        assert not names, (
            "dashboard/port_row.py must not import from engine.exit_authority; "
            f"found: {names}.  The is_authoritative local variable in _badge_for_row "
            "is a plain equality comparison, not an import."
        )
