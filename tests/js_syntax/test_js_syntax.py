"""Consolidated JS syntax checks — one parametrized module for all static/*.js files.

Replaces ~14 scattered `node --check` subprocess calls across ~19 test files (AC-1).
Coverage is identical: every JS file under static/ gets a `node --check` assertion.

Skip condition: shutil.which("node") is None — graceful skip on runners without node.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

# Discover JS files relative to the worktree root (two levels up from this file).
_WORKTREE = pathlib.Path(__file__).parent.parent.parent
_JS_FILES = sorted(_WORKTREE.glob("static/*.js"))


@pytest.mark.parametrize("js_file", _JS_FILES, ids=[f.name for f in _JS_FILES])
def test_js_syntax(js_file: pathlib.Path) -> None:
    """Run `node --check <file>` to assert the JS file is syntactically valid (AC-1).

    node --check parses the file and exits non-zero if there is a syntax error.
    It does NOT execute the file.  No imports, no DOM, no network.

    Skipped if node is not installed on this runner.
    """
    if shutil.which("node") is None:
        pytest.skip("node not installed — skipping JS syntax check")

    result = subprocess.run(
        ["node", "--check", str(js_file)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"node --check failed for {js_file.name}:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
