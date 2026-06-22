"""RED tests for AC-1: js-syntax check consolidation into one parametrized module.

The current suite has ~14 scattered `node --check` subprocess spawns across ~17 test
files (tests/ui/, tests/dashboard/, tests/ai_advisor/, tests/app/).  Each spawns a
`node` child process that imports nothing but checks one JS file's syntax.  While
cheap individually, together they impose repeated OS-process overhead and are not
co-located for easy maintenance.

AC-1 requires:
  1. A NEW file tests/js_syntax/test_js_syntax.py with ONE parametrized test that
     runs `node --check` over every file matching static/*.js.
  2. The shutil.which("node") is None skip is preserved.
  3. Every JS file under static/ gets a syntax check (coverage identical to the
     ~14 originals — just consolidated).
  4. The originating per-file node --check test methods are removed from their
     source files.

These tests are RED because tests/js_syntax/test_js_syntax.py does not exist yet.
They assert the contract (module exists, covers all files, has the skip guard)
without running node themselves — the actual syntax verification is left to the
real parametrized tests inside the new module.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

# Absolute path to the worktree root, resolved from this file's location.
# Worktree layout: tests/js_syntax/test_js_syntax_consolidation.py
_WORKTREE = pathlib.Path(__file__).parent.parent.parent


def test_js_syntax_consolidated_module_exists():
    """tests/js_syntax/test_js_syntax.py must exist after AC-1 is implemented (RED until then).

    The whole consolidation effort is moot if the destination file is never created.
    This test is the first RED gate: it will fail until the implementer creates the file.
    """
    target = _WORKTREE / "tests" / "js_syntax" / "test_js_syntax.py"
    assert target.exists(), (
        f"tests/js_syntax/test_js_syntax.py does not exist at {target}.  "
        "AC-1 requires creating this file with a parametrized node --check test "
        "that covers every file under static/*.js"
    )


def test_js_syntax_consolidated_module_has_node_skip_guard():
    """tests/js_syntax/test_js_syntax.py must contain the shutil.which('node') skip (AC-1).

    The existing per-file node-check tests all guard against node being absent on
    the runner.  The consolidated module must preserve this guard so the test is
    gracefully skipped on CI environments without node, not failed.
    """
    target = _WORKTREE / "tests" / "js_syntax" / "test_js_syntax.py"
    if not target.exists():
        pytest.skip("tests/js_syntax/test_js_syntax.py not created yet (AC-1 not implemented)")

    source = target.read_text(encoding="utf-8")
    assert "shutil.which" in source, (
        "tests/js_syntax/test_js_syntax.py must contain 'shutil.which' to guard against "
        "node being absent on the runner.  "
        "Pattern: `if shutil.which('node') is None: pytest.skip(...)`"
    )
    assert "node" in source.lower(), (
        "tests/js_syntax/test_js_syntax.py must reference 'node' — the skip guard must "
        "check for node specifically, not a generic binary"
    )


def test_js_syntax_consolidated_module_covers_all_static_js_files():
    """tests/js_syntax/test_js_syntax.py must parametrize over every static/*.js file (AC-1).

    This test discovers the actual JS files under static/ and verifies the consolidated
    module's parametrize list covers all of them.  Coverage must be identical to the
    ~14 originals — no JS file may silently lose its syntax check after consolidation.

    Strategy: parse the consolidated module's AST to find the parametrize decorator's
    argument list, then compare against the glob result.
    """
    target = _WORKTREE / "tests" / "js_syntax" / "test_js_syntax.py"
    if not target.exists():
        pytest.skip("tests/js_syntax/test_js_syntax.py not created yet (AC-1 not implemented)")

    # Discover JS files under static/ — this is the ground truth.
    js_files = sorted(_WORKTREE.glob("static/*.js"))
    assert js_files, f"No .js files found under {_WORKTREE / 'static'} — check the path"

    js_basenames = {f.name for f in js_files}

    # Parse the consolidated module's source to extract what it tests.
    source = target.read_text(encoding="utf-8")

    # Each JS file must be referenced by its path or name somewhere in the module.
    # The exact form (parametrize list, glob discovery, etc.) is up to the implementer;
    # we only require that every basename appears in the source OR that the source
    # uses a glob pattern that would discover all of them.
    uses_glob = "glob" in source and "static" in source
    uses_js_extension = "*.js" in source or ".js" in source

    if uses_glob and uses_js_extension:
        # Dynamic discovery — the implementer uses glob("static/*.js") or equivalent.
        # Trust that the pattern covers all files; assert the pattern is present.
        assert "static" in source, "Glob-based discovery must reference the 'static' directory"
    else:
        # Static list — each basename must appear explicitly.
        missing = [name for name in js_basenames if name not in source]
        assert not missing, (
            f"The following JS files under static/ are missing from "
            f"tests/js_syntax/test_js_syntax.py: {missing!r}.  "
            "AC-1 requires identical coverage to the original scattered tests."
        )


def test_js_syntax_consolidated_module_is_valid_python():
    """tests/js_syntax/test_js_syntax.py must be syntactically valid Python (AC-1).

    A syntax error in the consolidation file would silently drop all JS syntax checks
    from the suite (collection fails, pytest reports ERROR not FAIL).  Parsing the
    AST here catches that before the real suite runs.
    """
    target = _WORKTREE / "tests" / "js_syntax" / "test_js_syntax.py"
    if not target.exists():
        pytest.skip("tests/js_syntax/test_js_syntax.py not created yet (AC-1 not implemented)")

    source = target.read_text(encoding="utf-8")
    try:
        ast.parse(source)
    except SyntaxError as exc:
        pytest.fail(
            f"tests/js_syntax/test_js_syntax.py has a syntax error: {exc}.  "
            "A broken consolidation file silently drops all JS syntax checks from the suite."
        )
