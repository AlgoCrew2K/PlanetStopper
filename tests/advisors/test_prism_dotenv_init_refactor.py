"""RED tests for AC-3: replace _init_db_at subprocess spawn with in-process DB init.

tests/advisors/test_prism_dotenv_hardening.py lines 88-103 define _init_db_at(),
a helper that runs `python -c "import database; database.init_db()"` in a subprocess
to initialise a fresh SQLite file for the dotenv CLI tests.  The justification in the
original comment was "avoid importing database into the test process with the wrong
DB_PATH."  But the conftest.py autouse _isolate_db fixture already handles DB_PATH
isolation per-test via monkeypatch.setenv — the subprocess is unnecessary overhead.

AC-3 requires:
  - Replace the subprocess in _init_db_at with a direct in-process call using
    monkeypatch.setenv("DB_PATH", str(db_path)) then database.init_db().
  - The ESSENTIAL subprocess tests (test_cli_honors_dotenv_db_path_when_not_in_shell_env,
    test_cli_uses_find_dotenv_to_discover_dotenv_in_ancestor_dir, etc.) which need a
    fresh os.environ via subprocess.run are UNCHANGED.

These tests are RED until AC-3 is implemented.  They inspect the source of
test_prism_dotenv_hardening.py to verify the structural change.
"""

from __future__ import annotations

import ast
import pathlib
import textwrap

import pytest

_WORKTREE = pathlib.Path(__file__).parent.parent.parent
_TARGET = _WORKTREE / "tests" / "advisors" / "test_prism_dotenv_hardening.py"


def _extract_function_source(source: str, fn_name: str) -> str | None:
    """Return the source text of a top-level function in a Python module, or None if not found.

    Uses AST to find the function's line span, then slices the original source
    lines for an exact text match (no decompilation).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
            start = node.lineno - 1  # ast lineno is 1-based
            end = node.end_lineno  # inclusive 1-based
            return "\n".join(lines[start:end])
    return None


def test_init_db_at_no_longer_spawns_subprocess():
    """_init_db_at in test_prism_dotenv_hardening.py must NOT call subprocess.run after AC-3 (RED).

    The original helper spawns `python -c "import database; database.init_db()"` to
    avoid importing database with the wrong DB_PATH.  After AC-3 this subprocess is
    replaced by an in-process call.  Removing the subprocess spawn is the whole point
    of AC-3 — the footprint reduction comes from eliminating the child Python process
    startup cost and its ~30+ MB committed memory.
    """
    assert _TARGET.exists(), f"test_prism_dotenv_hardening.py must exist at {_TARGET}"

    source = _TARGET.read_text(encoding="utf-8")
    fn_source = _extract_function_source(source, "_init_db_at")

    assert fn_source is not None, (
        "_init_db_at function not found in test_prism_dotenv_hardening.py — "
        "it must still exist as a helper function (just without the subprocess call)"
    )

    assert "subprocess.run" not in fn_source, (
        "Found 'subprocess.run' inside _init_db_at in test_prism_dotenv_hardening.py.  "
        "AC-3 requires replacing this subprocess spawn with an in-process "
        "database.init_db() call.  "
        "This test is RED until that refactor is done."
    )


def test_init_db_at_uses_in_process_init_db():
    """_init_db_at must call database.init_db() directly (in-process) after AC-3 (RED).

    The replacement for the subprocess spawn is a direct `import database; database.init_db()`
    call inside the _init_db_at helper, with DB_PATH set via monkeypatch or os.environ
    before the call.  This preserves the behavior (the temp DB is initialised) while
    eliminating the subprocess overhead.
    """
    assert _TARGET.exists(), f"test_prism_dotenv_hardening.py must exist at {_TARGET}"

    source = _TARGET.read_text(encoding="utf-8")
    fn_source = _extract_function_source(source, "_init_db_at")

    assert fn_source is not None, "_init_db_at not found in test_prism_dotenv_hardening.py"

    has_init_db = "init_db" in fn_source and "database" in fn_source
    assert has_init_db, (
        "database.init_db() not found inside _init_db_at in test_prism_dotenv_hardening.py.  "
        "AC-3 requires replacing the subprocess helper with a direct in-process init_db() call.  "
        "This test is RED until that refactor is done."
    )


def test_essential_dotenv_subprocess_tests_preserved():
    """The essential dotenv-discovery subprocess tests must remain UNCHANGED after AC-3 (AC-3).

    AC-3 removes the subprocess from _init_db_at only.  The actual dotenv-discovery tests
    (test_cli_honors_dotenv_db_path_when_not_in_shell_env, etc.) rely on subprocess.run to
    give the CLI a fresh os.environ without DB_PATH.  Those are NOT refactored.

    This test verifies that subprocess.run still appears in the file (in the actual test
    bodies, not _init_db_at) — if it disappears entirely, the essential subprocess tests
    were accidentally deleted.
    """
    assert _TARGET.exists(), f"test_prism_dotenv_hardening.py must exist at {_TARGET}"

    source = _TARGET.read_text(encoding="utf-8")

    # After AC-3, subprocess.run must still appear OUTSIDE _init_db_at (in the test bodies).
    fn_source = _extract_function_source(source, "_init_db_at") or ""

    # Count subprocess.run occurrences total vs inside _init_db_at.
    total_subprocess_run = source.count("subprocess.run")
    in_init_db_at = fn_source.count("subprocess.run")
    outside_init_db_at = total_subprocess_run - in_init_db_at

    assert outside_init_db_at > 0, (
        "subprocess.run must still appear in test bodies outside _init_db_at — "
        "the essential dotenv-discovery subprocess tests must be preserved.  "
        "If subprocess.run is completely absent after AC-3, the refactor accidentally "
        "removed the wrong code."
    )


def test_prism_dotenv_hardening_file_is_valid_python():
    """test_prism_dotenv_hardening.py must be syntactically valid Python after AC-3 (AC-3).

    A syntax error introduced during the refactor would silently drop all dotenv
    hardening tests from the suite (pytest reports ERROR not FAIL during collection).
    """
    assert _TARGET.exists(), f"test_prism_dotenv_hardening.py must exist at {_TARGET}"

    source = _TARGET.read_text(encoding="utf-8")
    try:
        ast.parse(source)
    except SyntaxError as exc:
        pytest.fail(f"test_prism_dotenv_hardening.py has a syntax error after AC-3 refactor: {exc}")
