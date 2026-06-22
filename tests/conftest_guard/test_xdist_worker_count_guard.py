"""RED tests for AC-6: pytest_configure rejects -n auto / -n >4 with loud SystemExit.

Guards the 67.8 GB Windows dev host against uncapped pytest-xdist fan-out that
caused two PC hard-reboots (Kernel-Power 41, ~238 GB committed).

The guard must:
  - Reject numprocesses == "auto" (any platform) with SystemExit
  - Reject numprocesses > 4 (integer) (any platform) with SystemExit
  - Accept numprocesses 0, 1, 2, 3, 4 (integer)
  - Accept a config without the numprocesses attribute (non-xdist run)
  - Accept numprocesses == None (xdist installed but -n not passed)

Implementation requirement (for the implementer — this is the seam these tests exercise):
  tests/conftest.py must extract the guard into a top-level helper:

    def _assert_safe_worker_count(numprocesses) -> None:
        '''Reject unsafe xdist worker counts before install_from_env().'''
        if numprocesses is None:
            return
        if numprocesses == "auto" or (isinstance(numprocesses, int) and numprocesses > 4):
            raise SystemExit(...)

  Then pytest_configure calls it EARLY, before ALPHABOT_MAX_JOBS/OPTUNA_N_JOBS setdefaults
  and before install_from_env().  This lets these tests call _assert_safe_worker_count
  directly without triggering the cap-install or env side-effects.

These tests call _assert_safe_worker_count directly (not pytest_configure) to isolate
the guard logic.  The helper must be exported from tests.conftest so it is importable.

All tests are cross-platform because the guard must run on Linux CI too.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Import the helper (RED until the seam is extracted)
# ---------------------------------------------------------------------------

def _get_guard():
    """Import _assert_safe_worker_count from tests.conftest.

    Returns the callable if it exists, or raises ImportError with a clear message
    so the test fails with a descriptive error rather than AttributeError.
    """
    import tests.conftest as _conftest  # noqa: PLC0415

    if not hasattr(_conftest, "_assert_safe_worker_count"):
        raise ImportError(
            "tests.conftest does not export '_assert_safe_worker_count'.  "
            "AC-6 requires extracting the worker-count guard into a top-level helper "
            "so tests can call it directly without triggering cap-install side-effects.  "
            "Add: def _assert_safe_worker_count(numprocesses) -> None: ..."
        )
    return _conftest._assert_safe_worker_count


# ---------------------------------------------------------------------------
# Seam-existence test (RED until extracted)
# ---------------------------------------------------------------------------


def test_xdist_guard_helper_exported_from_conftest():
    """tests.conftest must export _assert_safe_worker_count as a top-level callable (AC-6, RED).

    This test is RED until the implementer extracts the worker-count guard from
    pytest_configure into a standalone helper.  The seam lets guard tests run
    without triggering cap-install or env side-effects.
    """
    import tests.conftest as _conftest  # noqa: PLC0415

    assert hasattr(_conftest, "_assert_safe_worker_count"), (
        "tests.conftest does not export '_assert_safe_worker_count'.  "
        "AC-6 requires extracting the xdist worker-count guard into a top-level helper "
        "called EARLY in pytest_configure (before install_from_env() and the env setdefaults).  "
        "This test is RED until that extraction is done."
    )
    assert callable(_conftest._assert_safe_worker_count), (
        "_assert_safe_worker_count must be callable"
    )


# ---------------------------------------------------------------------------
# Rejection tests — must raise SystemExit
# ---------------------------------------------------------------------------


def test_xdist_guard_rejects_n_auto():
    """_assert_safe_worker_count must raise SystemExit when numprocesses == 'auto' (AC-6).

    '-n auto' instructs xdist to use all CPU cores.  On a 24-core host this fans
    out to 24 workers, each importing the full app stack — the combination has
    caused the 238 GB committed-memory host crash.  'auto' must be rejected loudly.
    """
    guard = _get_guard()

    with pytest.raises(SystemExit) as exc_info:
        guard("auto")

    msg = str(exc_info.value)
    assert any(kw in msg for kw in ("67.8", "auto", "ceiling", "reject", "safe")), (
        f"SystemExit message must explain why '-n auto' is rejected "
        f"(cite the host ceiling or the rejected value); got: {msg!r}"
    )


def test_xdist_guard_rejects_n_5():
    """_assert_safe_worker_count must raise SystemExit when numprocesses == 5 (> 4) (AC-6)."""
    guard = _get_guard()

    with pytest.raises(SystemExit):
        guard(5)


def test_xdist_guard_rejects_n_8():
    """_assert_safe_worker_count must raise SystemExit when numprocesses == 8 (> 4) (AC-6)."""
    guard = _get_guard()

    with pytest.raises(SystemExit):
        guard(8)


def test_xdist_guard_rejects_n_large():
    """_assert_safe_worker_count must raise SystemExit when numprocesses == 24 (AC-6)."""
    guard = _get_guard()

    with pytest.raises(SystemExit):
        guard(24)


# ---------------------------------------------------------------------------
# Acceptance tests — must NOT raise SystemExit
# ---------------------------------------------------------------------------


def test_xdist_guard_accepts_n_2():
    """_assert_safe_worker_count must NOT raise when numprocesses == 2 (AC-6).

    The pyproject.toml addopts default is '-n 2' so this is the most common
    invocation on this host and must always pass.
    """
    guard = _get_guard()
    guard(2)  # must not raise


def test_xdist_guard_accepts_n_4():
    """_assert_safe_worker_count must NOT raise when numprocesses == 4 (the maximum allowed) (AC-6)."""
    guard = _get_guard()
    guard(4)  # must not raise


def test_xdist_guard_accepts_n_0():
    """_assert_safe_worker_count must NOT raise when numprocesses == 0 (AC-6)."""
    guard = _get_guard()
    guard(0)  # must not raise


def test_xdist_guard_accepts_n_1():
    """_assert_safe_worker_count must NOT raise when numprocesses == 1 (AC-6)."""
    guard = _get_guard()
    guard(1)  # must not raise


def test_xdist_guard_accepts_none():
    """_assert_safe_worker_count must NOT raise when numprocesses is None (AC-6).

    xdist sets numprocesses to None when the user doesn't pass -n, meaning the plugin
    is present but not activated for this run.
    """
    guard = _get_guard()
    guard(None)  # must not raise


def test_xdist_guard_integrated_into_pytest_configure():
    """pytest_configure must call _assert_safe_worker_count (AC-6 integration).

    Verifies the seam is actually wired into pytest_configure by AST-inspecting
    conftest.py and asserting _assert_safe_worker_count appears as a call inside
    pytest_configure's function body.
    """
    import ast  # noqa: PLC0415
    import pathlib  # noqa: PLC0415

    worktree = pathlib.Path(__file__).parent.parent.parent
    conftest_path = worktree / "tests" / "conftest.py"
    assert conftest_path.exists(), f"tests/conftest.py not found at {conftest_path}"

    source = conftest_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find pytest_configure function body and look for _assert_safe_worker_count call.
    found_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "pytest_configure":
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    func = child.func
                    if isinstance(func, ast.Name) and func.id == "_assert_safe_worker_count":
                        found_call = True
                        break
            break

    assert found_call, (
        "_assert_safe_worker_count is not called inside pytest_configure in tests/conftest.py.  "
        "AC-6 requires wiring the helper into pytest_configure so the guard runs on every "
        "pytest session start."
    )
