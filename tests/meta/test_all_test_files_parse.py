"""Recurrence guard — every tests/**/*.py must be parseable Python with no empty
Test* classes (AC-1 husk prevention).

WHY THIS FILE EXISTS
--------------------
AC-1 of the footprint-cap hardening cycle (fix/footprint-cap-hardening) consolidated
~14 scattered ``node --check`` test methods into a single parametrized module
(tests/js_syntax/test_js_syntax.py). Stripping those methods left 3 test files with
EMPTY class bodies, which are a SyntaxError in Python:

  - tests/app/test_guard_alpha_panel_ui.py          :: class TestIndexJsSyntaxValidity
  - tests/dashboard/test_dashboard_render_consistency.py :: class TestIndexJsParses
  - tests/dashboard/test_window_picker_wiring.py    :: class TestIndexJsParses

The cycle's 75 RED→GREEN tests never caught this because they targeted the new
consolidation module, not the donor files.  CI would fail on collection errors.

This module provides two complementary guards:

Guard 1 — PARSE (parametrized over every file)
    Calls ``compile(src, path, "exec")`` on each file.  A SyntaxError — including the
    "expected an indented block after class definition" produced by a completely empty
    class body — fails the test for that specific file.

Guard 2 — TEST-CLASS CONTENT (one aggregated test across all files)
    After parse succeeds, walks the AST of each file looking for class definitions
    whose name starts with ``Test`` and that contain zero ``test_``-prefixed methods
    in their direct body.  A ``pass``-only husk or a class left with only a docstring
    (but no test methods) would compile cleanly yet produce zero test coverage.  This
    guard catches those silently-empty test classes before they reach CI.

    Exceptions to the "must have test_ methods" rule:
    - ``__init__``, ``setUp``, ``tearDown``, helper methods (not prefixed ``test_``)
      are fine AS LONG AS at least one ``test_`` method is also present.
    - Pure base/mixin classes used only for inheritance are flagged by this guard.
      If a legitimate base class without test methods is added, add its name to the
      ``_BASE_CLASS_EXEMPTIONS`` set at the top of this file.

NO SUBPROCESS / NO HEAVY IMPORTS
---------------------------------
Pure stdlib (pathlib + ast + builtins).  Parse-time check only; the files are never
executed.  Fast enough to run on every CI push.

NO SKIP / XFAIL MARKERS IN THIS FILE
-------------------------------------
By construction.  A parse failure or empty test class is always a real defect;
skipping would defeat the purpose.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

_TESTS_ROOT = pathlib.Path(__file__).parent.parent


def _discover_test_files() -> list[pathlib.Path]:
    """Return every *.py under tests/, excluding __pycache__ and .claude paths.

    The rglob starts at the tests/ subtree, so repo-level .claude/worktrees/ is
    never visited.  The part-check operates on the path RELATIVE TO _TESTS_ROOT
    so that the worktree's own location under .claude/worktrees/ does not cause
    every file to be excluded.

    Defense-in-depth: if a future restructuring places a .claude dir inside tests/,
    those files will still be excluded because their relative parts contain '.claude'.
    """
    found: list[pathlib.Path] = []
    for path in sorted(_TESTS_ROOT.rglob("*.py")):
        # Check only the parts relative to _TESTS_ROOT, not the full absolute path.
        # This avoids false-exclusion when the worktree itself lives under .claude/.
        relative_parts = path.relative_to(_TESTS_ROOT).parts
        if any(part in {"__pycache__", ".claude"} for part in relative_parts):
            continue
        found.append(path)
    return found


_ALL_TEST_FILES = _discover_test_files()

# ---------------------------------------------------------------------------
# Base-class exemptions for Guard 2 (empty-Test*-class AST walk)
#
# Add class names here ONLY for legitimate base/mixin Test* classes that
# intentionally carry zero test_ methods (pure setup/helper bases).
# Using this exemption requires a code-review comment explaining why.
# ---------------------------------------------------------------------------

_BASE_CLASS_EXEMPTIONS: frozenset[str] = frozenset()

# Sanity: at collection time we must have found test files.
assert _ALL_TEST_FILES, (
    f"_discover_test_files() returned no files under {_TESTS_ROOT}. "
    "Check that the tests/ directory exists and contains *.py files."
)


# ---------------------------------------------------------------------------
# Parametrized parse guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "py_file",
    _ALL_TEST_FILES,
    # Use the path relative to tests/ as the test ID for readable failure output.
    ids=[str(f.relative_to(_TESTS_ROOT)) for f in _ALL_TEST_FILES],
)
def test_test_file_is_valid_python(py_file: pathlib.Path) -> None:
    """Every *.py file under tests/ must compile without SyntaxError.

    An empty class body (class Foo:\\n\\n) raises:
        SyntaxError: expected an indented block after class definition

    A missing method body, unterminated string, or any other parse-time error
    will also cause this test to FAIL — which is the desired behavior.

    The file is READ and COMPILED but never executed.  No imports are triggered,
    no fixtures are needed, no side effects occur.

    Tolerance: exactly zero parse failures accepted.
    """
    src = py_file.read_text(encoding="utf-8")
    try:
        compile(src, str(py_file), "exec")
    except SyntaxError as exc:
        pytest.fail(
            f"{py_file.relative_to(_TESTS_ROOT)}: SyntaxError at line {exc.lineno} — "
            f"{exc.msg}\n"
            f"  Hint: an empty class body (class Foo: with no methods) is a common "
            f"cause after a test consolidation removes the only method from a class. "
            f"Either delete the class entirely or add a 'pass' statement "
            f"(prefer deletion if the class is dead)."
        )


# ---------------------------------------------------------------------------
# Guard 2 — no Test* class with zero test_ methods (catches pass/docstring husks)
# ---------------------------------------------------------------------------


def _empty_test_classes(py_file: pathlib.Path) -> list[tuple[str, int]]:
    """Return (class_name, lineno) for every Test* class in py_file that has
    zero direct test_ methods in its body.

    Only examines classes parseable without error (Guard 1 covers parse failures).
    Only considers *direct* method definitions (FunctionDef/AsyncFunctionDef)
    whose name starts with 'test_' — does not recurse into nested classes.

    Exempted class names (in _BASE_CLASS_EXEMPTIONS) are skipped.
    """
    src = py_file.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(py_file))
    except SyntaxError:
        # Guard 1 (compile) will already catch this file; skip here.
        return []

    offenders: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not node.name.startswith("Test"):
            continue
        if node.name in _BASE_CLASS_EXEMPTIONS:
            continue
        # Count direct test_ methods in the class body (not nested).
        test_methods = [
            child
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name.startswith("test_")
        ]
        if not test_methods:
            offenders.append((node.name, node.lineno))
    return offenders


def test_no_empty_test_classes_across_all_test_files() -> None:
    """Every Test* class in tests/**/*.py must define at least one test_ method.

    A Test* class with zero test_ methods is a dead husk: it compiles, it
    collects (pytest sees it), but it contributes zero assertions.  It exists
    only as noise and a potential source of confusion.

    The original AC-1 defect that prompted this guard: three files were left
    with empty class bodies after their only test method was removed:

      tests/app/test_guard_alpha_panel_ui.py :: TestIndexJsSyntaxValidity
      tests/dashboard/test_dashboard_render_consistency.py :: TestIndexJsParses
      tests/dashboard/test_window_picker_wiring.py :: TestIndexJsParses

    Those husks produced SyntaxErrors (caught by Guard 1/compile).  This guard
    catches the softer variant: a class with a 'pass' body or a docstring-only
    body that compiles cleanly but has zero test_ methods.

    Tolerance: zero Test* classes with zero test_ methods.
    To exempt a legitimate base class, add its name to _BASE_CLASS_EXEMPTIONS.
    """
    all_violations: list[str] = []
    for py_file in _ALL_TEST_FILES:
        for class_name, lineno in _empty_test_classes(py_file):
            rel = py_file.relative_to(_TESTS_ROOT)
            all_violations.append(f"  {rel}:{lineno} :: {class_name}")

    assert not all_violations, (
        f"Found {len(all_violations)} Test* class(es) with zero test_ methods "
        f"(dead husks):\n"
        + "\n".join(all_violations)
        + "\n\nFix: either delete the class entirely (preferred when the class is dead) "
        "or add at least one test_ method.  To exempt a genuine base class, add "
        "its name to _BASE_CLASS_EXEMPTIONS in this file with a code-review comment."
    )
