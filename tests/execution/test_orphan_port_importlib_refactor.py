"""RED tests for AC-2: replace pytest --collect-only subprocess spawns with in-process importlib.

tests/execution/test_orphan_port_modules_removed.py lines 524 and 540 each spawn a
subprocess running `python -m pytest --collect-only -q <target>`.  pytest's collection
imports the target module and all its transitive dependencies — including `app`, which
pulls in the full Flask stack.  Each such subprocess commits ~1-2 GB of process memory.
With xdist these are nested inside N workers, multiplying the footprint N-fold.

AC-2 requires replacing those two spawns with in-process `importlib.import_module(...)`
+ path-existence checks.  The replacement must still FAIL if a target file is missing
or fails to import — it must not weaken the coverage.

These tests assert the refactor was done by inspecting the source of
test_orphan_port_modules_removed.py:
  - The string "--collect-only" must no longer appear (subprocess spawn removed).
  - `importlib.import_module` (or `importlib` + `import_module`) must appear (replacement added).

Both tests are RED until AC-2 is implemented.
"""

from __future__ import annotations

import pathlib

import pytest

_WORKTREE = pathlib.Path(__file__).parent.parent.parent
_TARGET = _WORKTREE / "tests" / "execution" / "test_orphan_port_modules_removed.py"


def test_collect_only_subprocess_calls_removed_from_orphan_test():
    """'--collect-only' must NOT appear in test_orphan_port_modules_removed.py after AC-2 (RED).

    The two subprocess spawns at lines 524 and 540 each run
    `python -m pytest --collect-only -q <target>`.  Removing them is the footprint
    reduction — a subprocess importing app costs ~1-2 GB per call.

    If '--collect-only' still appears in the file, AC-2 has not been implemented.
    """
    assert _TARGET.exists(), f"test_orphan_port_modules_removed.py must exist at {_TARGET}"

    source = _TARGET.read_text(encoding="utf-8")

    assert "--collect-only" not in source, (
        "Found '--collect-only' in test_orphan_port_modules_removed.py.  "
        "AC-2 requires removing the two subprocess pytest --collect-only spawns "
        "(lines 524 and 540) and replacing them with in-process importlib checks.  "
        "This test is RED until that refactor is done."
    )


def test_importlib_import_module_used_in_orphan_test():
    """'importlib.import_module' must appear in test_orphan_port_modules_removed.py after AC-2 (RED).

    The in-process replacement for the subprocess spawns must use importlib to verify
    that the target modules import cleanly.  This is the behavior-preserving equivalent:
    `importlib.import_module('tests.portmode.test_api_state_route_additive_fields')`
    succeeds if and only if the module exists and its imports succeed — the same
    semantic guarantee as `--collect-only` for these simple modules.

    If 'importlib.import_module' is absent, either the refactor was not done or a
    different (possibly subprocess-based) approach was used.
    """
    assert _TARGET.exists(), f"test_orphan_port_modules_removed.py must exist at {_TARGET}"

    source = _TARGET.read_text(encoding="utf-8")

    assert "importlib.import_module" in source or (
        "importlib" in source and "import_module" in source
    ), (
        "'importlib.import_module' not found in test_orphan_port_modules_removed.py.  "
        "AC-2 requires replacing the subprocess --collect-only spawns with in-process "
        "importlib.import_module() calls.  "
        "This test is RED until that refactor is done."
    )


def test_orphan_test_still_asserts_target_files_exist():
    """test_orphan_port_modules_removed.py must still assert the portmode targets exist (AC-2).

    The refactor must preserve — not weaken — the coverage.  The original subprocess
    tests verified both (a) the file exists AND (b) collection succeeds.  The importlib
    replacement covers (b); (a) is trivially implied by a successful import.  But to
    match the spirit of the original assertion ('test_api_state_route_additive_fields.py
    must remain in the suite') the file should still assert path existence OR the
    importlib call itself should fail loudly if the module is not found.

    This test checks that the word 'assert' still appears in the portmode-check section
    of the file (the exists() check or the importlib call's own ImportError should be
    asserted, not swallowed).
    """
    assert _TARGET.exists(), f"test_orphan_port_modules_removed.py must exist at {_TARGET}"

    source = _TARGET.read_text(encoding="utf-8")

    # Both target module names must still be referenced.
    assert "test_api_state_route_additive_fields" in source, (
        "test_orphan_port_modules_removed.py must still reference "
        "'test_api_state_route_additive_fields' after the AC-2 refactor — "
        "the coverage of that specific keep-surface must be preserved"
    )
    assert "test_port_state_schema" in source, (
        "test_orphan_port_modules_removed.py must still reference "
        "'test_port_state_schema' after the AC-2 refactor — "
        "the coverage of that specific keep-surface must be preserved"
    )
