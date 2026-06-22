"""Recurrence guard — every tests/**/*.py must be parseable Python (AC-1 husk prevention).

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

This guard fires BEFORE any refactor can ship to CI: if a class body is accidentally
left empty (or any other ParseError is introduced into a test file), this parametrized
test reports exactly which file broke.

WHAT IS ASSERTED
----------------
For every ``*.py`` file under ``tests/`` (excluding ``__pycache__`` dirs and any path
segment under ``.claude``): read the source and call ``compile(src, path, "exec")``.
A SyntaxError — including the "expected an indented block after class definition"
error produced by an empty class body — causes the test to FAIL with the file path
and error message.

NO SUBPROCESS / NO HEAVY IMPORTS
---------------------------------
Pure stdlib (pathlib + builtins).  Compile-time check only; the files are never
executed.  Fast enough to run on every CI push.

NO SKIP / XFAIL MARKERS IN THIS FILE
-------------------------------------
By construction.  A parse failure is always a real defect; skipping it would defeat
the purpose.
"""

from __future__ import annotations

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
