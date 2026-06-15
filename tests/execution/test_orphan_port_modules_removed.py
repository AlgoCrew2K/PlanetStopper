"""
RED tests for Sprint 3 audit-fix Cycle B — orphan port-decision modules removal
(S3-AUDIT-005; auto-resolves S3-AUDIT-009).

Audit provenance:
  docs/audit/sprint-3-cross-cycle-audit.md S3-AUDIT-005 [MEDIUM] +
  S3-AUDIT-009 [LOW].

Removal targets (chain head → tail, all three orphans):
  engine/multi_cycle.py   — top of the dead chain; no production import.
  port_selector.py        — only engine/multi_cycle.py imports it.
  port_aggregator.py      — only port_selector.py imports it.

Negative-grep at audit time (a7a3d9d) confirms the chain is dead:
  no production file outside the chain imports engine.multi_cycle; no
  production file imports port_selector or port_aggregator at all. The
  chain therefore deletes as a unit.

Preserved (the SITE-D1 KEEP-DISPLAY surface must NOT regress):
  - `port_state` table in the state DB stays (additive-first per
    [[project_port_level_deprecation_directive]] "Schema rows stay").
  - /api/state response keeps its `port_state` field (read at
    app.get_api_state_dict).
  - dashboard.port_row stays — it consumes the table for display only.
  - tests/portmode/test_api_state_route_additive_fields.py and
    tests/portmode/test_port_state_schema.py exercise these display surfaces
    and must remain GREEN.

FAIL conditions (what makes this suite RED today):
  - The three orphan modules are physically present on disk and importable.
  - Production grep still finds zero imports — but the modules themselves
    exist; their import-failure tests therefore fail until deletion lands.

Pattern: mirrors tests/execution/test_port_engine_module_removal.py (SITE-C1).
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import subprocess

import pytest


# ---------------------------------------------------------------------------
# Common helpers — project-root resolution and AST scans
# ---------------------------------------------------------------------------


def _project_root() -> pathlib.Path:
    """The worktree root (tests/execution/ is two parents up from this file)."""
    return pathlib.Path(__file__).parent.parent.parent


def _production_py_files(root: pathlib.Path):
    """Yield .py files that are not under tests/, __pycache__/, or git worktree dirs.

    `.claude/` houses git worktrees (`.claude/worktrees/`) and audit worktrees
    (`.claude/audit-worktrees/`) from prior sessions whose stale .py snapshots
    must NOT be scanned as production code. Mirrors the scanner discipline
    established in test_port_engine_module_removal.py and the
    a7a3d9d scanner-fix commit.
    """
    for p in root.rglob("*.py"):
        parts = p.relative_to(root).parts
        if "tests" not in parts and "__pycache__" not in parts and ".claude" not in parts:
            yield p


def _import_module_names_in_file(filepath: pathlib.Path, module: str) -> list[str]:
    """Return the names imported via `from <module> import ...` in the file.

    Catches lazy in-function imports (the AST walks every node). Returns []
    on SyntaxError so the scan is robust to malformed files.
    """
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            names.extend(alias.name for alias in node.names)
    return names


def _file_has_bare_import(filepath: pathlib.Path, module: str) -> bool:
    """True iff the file has `import <module>` (a bare top-level import)."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module:
                    return True
    return False


# ---------------------------------------------------------------------------
# SITE — engine/multi_cycle.py removal (top of the dead chain)
# ---------------------------------------------------------------------------


class TestMultiCycleModuleRemoved:
    """engine.multi_cycle must not exist post-Cycle-B.

    The audit verified there is no production consumer; the file deletes
    without migration.
    """

    def test_multi_cycle_module_is_not_importable(self):
        """
        Importing engine.multi_cycle must raise ImportError / ModuleNotFoundError.
        FAILS today — the module is still present at engine/multi_cycle.py.
        """
        with pytest.raises((ImportError, ModuleNotFoundError)):
            importlib.import_module("engine.multi_cycle")

    def test_multi_cycle_file_does_not_exist(self):
        """
        The physical file engine/multi_cycle.py must be deleted (not just
        emptied or commented out).
        """
        candidate = _project_root() / "engine" / "multi_cycle.py"
        assert not candidate.exists(), (
            f"engine/multi_cycle.py still exists at {candidate}; "
            "S3-AUDIT-005 requires the file to be deleted (top of the dead chain)"
        )

    def test_multi_cycle_not_re_exported_from_engine_package(self):
        """
        engine/__init__.py must not re-export the deleted module's symbols
        (e.g. `from engine import multi_cycle_loop`). Confirms the deletion
        is genuine, not a name-rebinding in the package init.
        """
        try:
            engine_pkg = importlib.import_module("engine")
        except ImportError:
            pytest.skip("engine package itself not importable")
        for sym in ("multi_cycle", "multi_cycle_loop", "run_multi_cycle"):
            assert not hasattr(engine_pkg, sym), (
                f"engine package must not re-export {sym!r} after multi_cycle.py deletion"
            )


# ---------------------------------------------------------------------------
# SITE — port_selector.py removal (middle of the dead chain)
# ---------------------------------------------------------------------------


class TestPortSelectorModuleRemoved:
    """port_selector.py must not exist post-Cycle-B.

    Only engine/multi_cycle.py imported it; that module is also gone, so
    port_selector deletes without an additional migration.
    """

    def test_port_selector_module_is_not_importable(self):
        """Importing port_selector must raise ImportError / ModuleNotFoundError."""
        with pytest.raises((ImportError, ModuleNotFoundError)):
            importlib.import_module("port_selector")

    def test_port_selector_file_does_not_exist(self):
        """The physical file port_selector.py must be deleted."""
        candidate = _project_root() / "port_selector.py"
        assert not candidate.exists(), (
            f"port_selector.py still exists at {candidate}; S3-AUDIT-005 requires deletion"
        )

    def test_select_symphony_with_mc_gate_not_accessible(self):
        """
        The primary decision-math function `select_symphony_with_mc_gate`
        must not be reachable via any import path. The audit calls out this
        as the SITE-A2 selection routine — it must be gone with the module.
        """
        with pytest.raises((ImportError, ModuleNotFoundError, AttributeError)):
            from port_selector import select_symphony_with_mc_gate  # noqa: F401

    def test_composition_hash_from_port_selector_not_accessible(self):
        """
        port_selector.composition_hash must not be reachable. The canonical
        composition-hash for the `port_state` table is database.compute_composition_hash
        (database.py:1851); the port_selector copy is a duplicate that
        deletes with the module. This test does NOT assert that
        database.compute_composition_hash exists — that is the responsibility
        of database-layer tests; it only locks the orphan removal.
        """
        with pytest.raises((ImportError, ModuleNotFoundError, AttributeError)):
            from port_selector import composition_hash  # noqa: F401


# ---------------------------------------------------------------------------
# SITE — port_aggregator.py removal (tail of the dead chain)
# ---------------------------------------------------------------------------


class TestPortAggregatorModuleRemoved:
    """port_aggregator.py must not exist post-Cycle-B.

    Only port_selector imported it; both deletions land in the same cycle.
    The pure-math helper `_compute_max_drawdown_from_series` has no
    production consumer outside port_aggregator.py itself (verified by
    grep at audit time); it deletes with the module.
    """

    def test_port_aggregator_module_is_not_importable(self):
        """Importing port_aggregator must raise ImportError / ModuleNotFoundError."""
        with pytest.raises((ImportError, ModuleNotFoundError)):
            importlib.import_module("port_aggregator")

    def test_port_aggregator_file_does_not_exist(self):
        """The physical file port_aggregator.py must be deleted."""
        candidate = _project_root() / "port_aggregator.py"
        assert not candidate.exists(), (
            f"port_aggregator.py still exists at {candidate}; S3-AUDIT-005 requires deletion"
        )

    def test_aggregate_to_port_not_accessible(self):
        """
        `aggregate_to_port` was the SITE-A2 port-level aggregation routine;
        with the module gone it must not be importable.
        """
        with pytest.raises((ImportError, ModuleNotFoundError, AttributeError)):
            from port_aggregator import aggregate_to_port  # noqa: F401

    def test_compute_max_drawdown_from_series_not_accessible(self):
        """
        `_compute_max_drawdown_from_series` is internal to port_aggregator and
        has no production consumer outside the module. With the module gone,
        the helper must be unreachable.
        """
        with pytest.raises((ImportError, ModuleNotFoundError, AttributeError)):
            from port_aggregator import _compute_max_drawdown_from_series  # noqa: F401


# ---------------------------------------------------------------------------
# Production callers sweep — no remaining imports anywhere outside tests/
# ---------------------------------------------------------------------------


class TestNoProductionImportsOfOrphanModules:
    """
    AST-based scan: no production .py file (excluding tests/, __pycache__/,
    and .claude/* worktree snapshots) may import any of the three orphan
    modules — either via `from <mod> import ...` or bare `import <mod>`.

    Today: passes for engine.multi_cycle and port_aggregator (they are
    imported only by other orphan-chain modules, which are not "production"
    in the sense of being a live entry point — but the scanner sees them as
    production files until they are deleted). To make this test concretely
    RED, the scanner must catch port_selector's import inside multi_cycle
    AND port_aggregator's import inside port_selector. Both files are still
    on disk and will be flagged. After deletion the scanner finds zero
    violations.
    """

    @pytest.fixture(scope="class")
    def project_root(self):
        return _project_root()

    def test_no_production_file_imports_engine_multi_cycle(self, project_root):
        """No production file may import engine.multi_cycle (top of chain).

        Both `from engine.multi_cycle import ...` and bare
        `import engine.multi_cycle` patterns are caught.
        """
        violations: list[str] = []
        for py_file in _production_py_files(project_root):
            names = _import_module_names_in_file(py_file, "engine.multi_cycle")
            if names:
                rel = py_file.relative_to(project_root)
                violations.append(f"{rel}: from engine.multi_cycle import {names}")
            if _file_has_bare_import(py_file, "engine.multi_cycle"):
                rel = py_file.relative_to(project_root)
                violations.append(f"{rel}: import engine.multi_cycle")
        assert not violations, (
            "Production files still import engine.multi_cycle (must be DELETED "
            "in S3-AUDIT-005 fix):\n" + "\n".join(violations)
        )

    def test_no_production_file_imports_port_selector(self, project_root):
        """No production file may import port_selector."""
        violations: list[str] = []
        for py_file in _production_py_files(project_root):
            names = _import_module_names_in_file(py_file, "port_selector")
            if names:
                rel = py_file.relative_to(project_root)
                violations.append(f"{rel}: from port_selector import {names}")
            if _file_has_bare_import(py_file, "port_selector"):
                rel = py_file.relative_to(project_root)
                violations.append(f"{rel}: import port_selector")
        assert not violations, (
            "Production files still import port_selector (must be DELETED "
            "in S3-AUDIT-005 fix):\n" + "\n".join(violations)
        )

    def test_no_production_file_imports_port_aggregator(self, project_root):
        """No production file may import port_aggregator."""
        violations: list[str] = []
        for py_file in _production_py_files(project_root):
            names = _import_module_names_in_file(py_file, "port_aggregator")
            if names:
                rel = py_file.relative_to(project_root)
                violations.append(f"{rel}: from port_aggregator import {names}")
            if _file_has_bare_import(py_file, "port_aggregator"):
                rel = py_file.relative_to(project_root)
                violations.append(f"{rel}: import port_aggregator")
        assert not violations, (
            "Production files still import port_aggregator (must be DELETED "
            "in S3-AUDIT-005 fix):\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# Test-suite hygiene — no surviving test file may import the orphan modules
# ---------------------------------------------------------------------------


class TestNoSurvivingTestImportsOrphanModules:
    """
    Any .py file under tests/ that still imports one of the deleted modules
    will fail collection at the next pytest run (ImportError at import time).
    To prevent that, this scan asserts that NO test file (after the cycle
    completes) imports the orphan modules.

    Cycle-B impl-fixB must therefore either delete the offending test files
    or migrate them off the orphan dependency. The brief authorizes either
    approach (audit S3-AUDIT-005 fix-scope step 3: "Delete or migrate tests
    under tests/portmode/ that exercise removed surfaces"); this guard catches
    incomplete cleanup regardless of strategy.

    Exclusions:
      - This test file itself (it intentionally references the modules in
        string literals + import-failure assertions). Filtered by filename.
      - tests/execution/test_port_dispatch_removal.py contains the four
        deletion-tripwire xfails (strict=False) that intentionally try to
        import the modules. Those imports live inside xfail-decorated tests
        and are EXPECTED to raise after deletion — the file is excluded
        from the scan by filename.
    """

    EXCLUDED_TEST_FILES = {
        "test_orphan_port_modules_removed.py",  # this file
        "test_port_dispatch_removal.py",  # houses xfail tripwires
    }

    @pytest.fixture(scope="class")
    def project_root(self):
        return _project_root()

    def _test_py_files(self, root: pathlib.Path):
        for p in (root / "tests").rglob("*.py"):
            if p.name in self.EXCLUDED_TEST_FILES:
                continue
            if "__pycache__" in p.relative_to(root).parts:
                continue
            yield p

    def test_no_test_file_imports_engine_multi_cycle(self, project_root):
        violations: list[str] = []
        for py_file in self._test_py_files(project_root):
            names = _import_module_names_in_file(py_file, "engine.multi_cycle")
            if names or _file_has_bare_import(py_file, "engine.multi_cycle"):
                rel = py_file.relative_to(project_root)
                violations.append(str(rel))
        assert not violations, (
            "Test files still import engine.multi_cycle (will ImportError at "
            "collection after deletion):\n" + "\n".join(violations)
        )

    def test_no_test_file_imports_port_selector(self, project_root):
        violations: list[str] = []
        for py_file in self._test_py_files(project_root):
            names = _import_module_names_in_file(py_file, "port_selector")
            if names or _file_has_bare_import(py_file, "port_selector"):
                rel = py_file.relative_to(project_root)
                violations.append(str(rel))
        assert not violations, (
            "Test files still import port_selector (will ImportError at "
            "collection after deletion):\n" + "\n".join(violations)
        )

    def test_no_test_file_imports_port_aggregator(self, project_root):
        violations: list[str] = []
        for py_file in self._test_py_files(project_root):
            names = _import_module_names_in_file(py_file, "port_aggregator")
            if names or _file_has_bare_import(py_file, "port_aggregator"):
                rel = py_file.relative_to(project_root)
                violations.append(str(rel))
        assert not violations, (
            "Test files still import port_aggregator (will ImportError at "
            "collection after deletion):\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# Display-surface invariants — SITE-D1 KEEP-DISPLAY must NOT regress
# ---------------------------------------------------------------------------


class TestPortStateDisplaySurfaceUnchanged:
    """
    Per [[project_port_level_deprecation_directive]] "Schema rows stay
    (additive-first), code paths removed". The display-surface invariants:

      1. `port_state` table column exists in the state DB (migrations
         010/011/012 retained per Sprint-3 directive).
      2. /api/state response includes a `port_state` field.
      3. dashboard.port_row module is still importable as a display helper.

    These tests are GREEN today (the display surface is intact) and must
    STAY GREEN after Cycle B. They exist here as anti-regression sentinels
    in case impl-fixB over-deletes.
    """

    def test_port_state_table_exists_in_fresh_state_db(self, tmp_path, monkeypatch):
        """
        A fresh state DB initialised via database.init_db() must contain
        the `port_state` table — schema rows stay even though the decision
        code is gone.
        """
        import logging
        import os
        import sqlite3

        db_path = tmp_path / "state.db"
        monkeypatch.setenv("DB_PATH", str(db_path))

        import database

        # init_db is chatty; silence logging during the test to keep stdout clean
        _prior = logging.getLogger().level
        logging.getLogger().setLevel(logging.CRITICAL)
        try:
            database.init_db()
        finally:
            logging.getLogger().setLevel(_prior)

        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='port_state'")
            row = cur.fetchone()
        assert row is not None, (
            "port_state table must exist in a fresh state DB per "
            "[[project_port_level_deprecation_directive]] — schema rows stay; "
            "the orphan-module deletion in Cycle B must NOT drop the table."
        )

    def test_api_state_response_includes_port_state_field(self, tmp_path, monkeypatch):
        """
        app.get_api_state_dict() must include a `port_state` key in its
        returned dict — SITE-D1 KEEP-DISPLAY contract.

        We mock at the boundary (DB only) and assert dict-shape, not values
        (per feedback_no_hardcoded_test_values).
        """
        import logging
        import os

        db_path = tmp_path / "state.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        # Ensure EXIT_AUTHORITY is set so get_api_state_dict can render the
        # exit_authority badge alongside port_state — both are display surfaces.
        monkeypatch.setenv("EXIT_AUTHORITY", "per_symphony")

        import database

        _prior = logging.getLogger().level
        logging.getLogger().setLevel(logging.CRITICAL)
        try:
            database.init_db()
        finally:
            logging.getLogger().setLevel(_prior)

        import app as app_module

        result = app_module.get_api_state_dict()
        assert "port_state" in result, (
            "get_api_state_dict() must include 'port_state' in its return "
            "dict — SITE-D1 KEEP-DISPLAY; orphan-module deletion must NOT "
            "affect this display-only surface."
        )

    def test_dashboard_port_row_module_still_importable(self):
        """
        dashboard.port_row is a display-only module that renders port-state
        rows. It must remain importable after Cycle B (its only ties to
        port_aggregator/port_selector were the orphan-chain decision math,
        which it does NOT import — verified by AST scan in
        test_port_engine_module_removal.py::test_dashboard_port_row_does_not_import_from_exit_authority).
        """
        try:
            mod = importlib.import_module("dashboard.port_row")
        except (ImportError, ModuleNotFoundError) as exc:
            pytest.fail(
                f"dashboard.port_row must remain importable after Cycle B "
                f"(display-only KEEP); got: {exc!r}"
            )
        assert mod is not None  # silence linters; the import is the assertion


# ---------------------------------------------------------------------------
# Retained portmode-test suites — must still pass after Cycle B
# ---------------------------------------------------------------------------


class TestRetainedPortmodeTestsStillCollect:
    """
    Two tests under tests/portmode/ exercise the surviving display surfaces
    and must remain in the suite + collectable post-Cycle-B:

      tests/portmode/test_api_state_route_additive_fields.py
      tests/portmode/test_port_state_schema.py

    A fast collection-only check confirms they exist AND import cleanly
    (i.e. impl-fixB did not accidentally over-delete or break a transitive
    import while removing the orphan modules).
    """

    def test_test_api_state_route_additive_fields_collects(self):
        target = _project_root() / "tests" / "portmode" / "test_api_state_route_additive_fields.py"
        assert target.exists(), f"{target} must remain in the suite (Sprint-3-manifest §6 KEEP)"
        result = subprocess.run(
            ["python", "-m", "pytest", "--collect-only", "-q", str(target)],
            cwd=str(_project_root()),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            "tests/portmode/test_api_state_route_additive_fields.py must "
            "collect cleanly after Cycle B (KEEP-DISPLAY surface).\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_test_port_state_schema_collects(self):
        target = _project_root() / "tests" / "portmode" / "test_port_state_schema.py"
        assert target.exists(), f"{target} must remain in the suite (Sprint-3-manifest §6 KEEP)"
        result = subprocess.run(
            ["python", "-m", "pytest", "--collect-only", "-q", str(target)],
            cwd=str(_project_root()),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            "tests/portmode/test_port_state_schema.py must collect cleanly "
            "after Cycle B (KEEP-DISPLAY surface).\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
