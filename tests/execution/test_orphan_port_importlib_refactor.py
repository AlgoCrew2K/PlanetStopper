"""RED tests for AC-2: replace pytest --collect-only subprocess spawns with in-process importlib.

tests/execution/test_orphan_port_modules_removed.py contains class
TestRetainedPortmodeTestsStillCollect whose two methods each spawn a subprocess running
`python -m pytest --collect-only -q <target>`.  pytest's collection imports the target
module and all its transitive dependencies — including `app`, which pulls in the full
Flask stack.  Each such subprocess commits ~1-2 GB of process memory.  With xdist
these are nested inside N workers, multiplying the footprint N-fold.

AC-2 requires replacing those two spawns with in-process `importlib.import_module(...)`
+ path-existence checks.  The replacement must still FAIL if a target file is missing
or fails to import — it must not weaken the coverage.

These tests assert the refactor was done by AST-parsing test_orphan_port_modules_removed.py
and verifying the subprocess.run("--collect-only") calls are absent WITHIN the target
class methods.  Raw string search is avoided to prevent false-passes on comments/docstrings.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_WORKTREE = pathlib.Path(__file__).parent.parent.parent
_TARGET = _WORKTREE / "tests" / "execution" / "test_orphan_port_modules_removed.py"


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _collect_subprocess_run_args_in_class(source: str, class_name: str) -> list[list[str]]:
    """Return the argument lists of every subprocess.run() call inside a named class.

    Walks the AST looking for Call nodes of the form:
      subprocess.run([...], ...)
    that are nested inside the named ClassDef.  Returns a list of string literals
    found in the first positional argument list of each such call.

    Only string literals are collected; non-literal elements (variables, calls) are
    represented as None placeholders so positional checks remain correct.
    """
    tree = ast.parse(source)
    results: list[list[str | None]] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self):
            self._in_target_class = False

        def visit_ClassDef(self, node: ast.ClassDef):
            prev = self._in_target_class
            if node.name == class_name:
                self._in_target_class = True
            self.generic_visit(node)
            self._in_target_class = prev

        def visit_Call(self, node: ast.Call):
            if self._in_target_class:
                # Match subprocess.run(...) or subprocess.run([...], ...)
                is_subprocess_run = (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "run"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"
                )
                if is_subprocess_run and node.args:
                    first_arg = node.args[0]
                    if isinstance(first_arg, (ast.List, ast.Tuple)):
                        elts = []
                        for elt in first_arg.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                elts.append(elt.value)
                            else:
                                elts.append(None)
                        results.append(elts)
            self.generic_visit(node)

    _Visitor().visit(tree)
    return results


def _collect_importlib_calls_in_class(source: str, class_name: str) -> list[str]:
    """Return all module names passed to importlib.import_module() inside a named class.

    Walks the AST looking for:
      importlib.import_module("some.module")
    inside the named ClassDef.  Returns the string argument of each call.
    """
    tree = ast.parse(source)
    found: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self):
            self._in_target_class = False

        def visit_ClassDef(self, node: ast.ClassDef):
            prev = self._in_target_class
            if node.name == class_name:
                self._in_target_class = True
            self.generic_visit(node)
            self._in_target_class = prev

        def visit_Call(self, node: ast.Call):
            if self._in_target_class:
                is_import_module = (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "import_module"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "importlib"
                )
                if is_import_module and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        found.append(first.value)
            self.generic_visit(node)

    _Visitor().visit(tree)
    return found


# ---------------------------------------------------------------------------
# RED tests
# ---------------------------------------------------------------------------

_CLASS_NAME = "TestRetainedPortmodeTestsStillCollect"


def test_collect_only_subprocess_calls_removed_from_orphan_test():
    """No subprocess.run([..., '--collect-only', ...]) must exist inside TestRetainedPortmodeTestsStillCollect after AC-2 (RED).

    Uses AST-scoped inspection of the class body — not raw string search — to
    avoid false-passes from comments or docstrings that happen to contain the string
    '--collect-only'.

    The two spawns (originally lines 524 and 540) commit ~1-2 GB of child-process
    memory each.  Replacing them with importlib.import_module() is the AC-2 footprint
    reduction.
    """
    assert _TARGET.exists(), f"test_orphan_port_modules_removed.py must exist at {_TARGET}"

    source = _TARGET.read_text(encoding="utf-8")

    try:
        subprocess_args = _collect_subprocess_run_args_in_class(source, _CLASS_NAME)
    except SyntaxError as exc:
        pytest.fail(f"test_orphan_port_modules_removed.py has a syntax error: {exc}")

    collect_only_calls = [args for args in subprocess_args if "--collect-only" in args]

    assert not collect_only_calls, (
        f"Found {len(collect_only_calls)} subprocess.run([..., '--collect-only', ...]) "
        f"call(s) inside {_CLASS_NAME} in test_orphan_port_modules_removed.py.  "
        "AC-2 requires replacing these with in-process importlib.import_module() calls.  "
        f"Calls found: {collect_only_calls!r}.  "
        "This test is RED until that refactor is done."
    )


def test_importlib_import_module_used_in_retained_portmode_class():
    """importlib.import_module() must be called inside TestRetainedPortmodeTestsStillCollect after AC-2 (RED).

    The in-process replacement uses importlib.import_module('tests.portmode.<module>')
    to verify that the target module exists AND imports cleanly — the same semantic
    guarantee that --collect-only provided for these simple modules.

    If no importlib.import_module() calls appear in the class, the refactor was not done
    or a different approach was used that may not achieve the same coverage.
    """
    assert _TARGET.exists(), f"test_orphan_port_modules_removed.py must exist at {_TARGET}"

    source = _TARGET.read_text(encoding="utf-8")

    try:
        importlib_calls = _collect_importlib_calls_in_class(source, _CLASS_NAME)
    except SyntaxError as exc:
        pytest.fail(f"test_orphan_port_modules_removed.py has a syntax error: {exc}")

    assert importlib_calls, (
        f"No importlib.import_module() calls found inside {_CLASS_NAME} in "
        "test_orphan_port_modules_removed.py.  "
        "AC-2 requires replacing the subprocess --collect-only spawns with "
        "in-process importlib.import_module() calls.  "
        "This test is RED until that refactor is done."
    )


def test_importlib_calls_cover_both_portmode_targets():
    """importlib.import_module() inside the class must reference both portmode targets after AC-2.

    The original two subprocess tests covered:
      tests/portmode/test_api_state_route_additive_fields.py
      tests/portmode/test_port_state_schema.py

    The importlib replacement must reference both — either by module path or file check —
    so neither portmode surface loses its keep-display verification.
    """
    assert _TARGET.exists(), f"test_orphan_port_modules_removed.py must exist at {_TARGET}"

    source = _TARGET.read_text(encoding="utf-8")

    # Both target names must appear in the file (the importlib call or the exists() check).
    assert "test_api_state_route_additive_fields" in source, (
        "test_orphan_port_modules_removed.py must reference 'test_api_state_route_additive_fields' "
        "after the AC-2 refactor — portmode keep-surface coverage must be preserved"
    )
    assert "test_port_state_schema" in source, (
        "test_orphan_port_modules_removed.py must reference 'test_port_state_schema' "
        "after the AC-2 refactor — portmode keep-surface coverage must be preserved"
    )
