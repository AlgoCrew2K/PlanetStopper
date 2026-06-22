"""RED tests for AC-6: pytest_configure rejects -n auto / -n >4 with loud SystemExit.

Guards the 67.8 GB Windows dev host against uncapped pytest-xdist fan-out that
caused two PC hard-reboots (Kernel-Power 41, ~238 GB committed).

The guard must:
  - Reject numprocesses == "auto" (any platform)
  - Reject numprocesses > 4 (integer) (any platform)
  - Accept numprocesses 0, 1, 2, 3, 4 (integer)
  - Accept a config without the numprocesses attribute (non-xdist run)

All tests are cross-platform because the guard runs on Linux CI too (we want CI
to be blocked from sneaking in an uncapped run, even though the crash risk is
lower on the cloud runner's cgroup-bounded environment).

Tests call tests.conftest.pytest_configure directly with a mock config object
to isolate the guard logic — they do NOT actually launch xdist workers,
spawn subprocesses, or trigger the DB_PATH/mem-cap side-effects of a real
pytest session start.
"""

from __future__ import annotations

import types

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(numprocesses=None, *, has_attr=True):
    """Return a minimal mock pytest Config object with config.option.numprocesses."""
    option = types.SimpleNamespace()
    if has_attr:
        option.numprocesses = numprocesses
    config = types.SimpleNamespace(option=option)
    return config


def _call_xdist_guard(config):
    """Call only the xdist worker-count guard portion of conftest.pytest_configure.

    We import conftest directly (not via pytest's plugin machinery) so the call
    does not trigger the DB_PATH or mem-cap side-effects.  The xdist guard is a
    self-contained block that reads config.option.numprocesses and raises SystemExit
    — we can exercise it in isolation without a full pytest session.

    NOTE: conftest.pytest_configure also calls _mem_cap.install_from_env() on
    Windows.  To isolate only the xdist guard, we pass a mock config and let the
    real function run — on non-Windows the mem-cap block is a no-op, and on Windows
    the cap is idempotent (already installed by the real conftest at session start).
    The DB_PATH block uses setdefault so it is a no-op if already set.
    """
    import tests.conftest as _conftest  # noqa: PLC0415

    _conftest.pytest_configure(config)


# ---------------------------------------------------------------------------
# Rejection tests — must raise SystemExit
# ---------------------------------------------------------------------------


def test_xdist_guard_rejects_n_auto():
    """pytest_configure must raise SystemExit when numprocesses == 'auto' (AC-6).

    '-n auto' instructs xdist to use all CPU cores.  On a 24-core host this fans
    out to 24 workers, each importing the full app stack — the combination has
    caused the 238 GB committed-memory host crash.  'auto' must be rejected loudly
    with no fallback or degradation.
    """
    config = _make_config(numprocesses="auto")

    with pytest.raises(SystemExit) as exc_info:
        _call_xdist_guard(config)

    msg = str(exc_info.value)
    assert "67.8" in msg or "auto" in msg or "ceiling" in msg.lower() or "reject" in msg.lower(), (
        f"SystemExit message must explain why '-n auto' is rejected "
        f"(cite the host ceiling or the rejected value); got: {msg!r}"
    )


def test_xdist_guard_rejects_n_5():
    """pytest_configure must raise SystemExit when numprocesses == 5 (> 4) (AC-6)."""
    config = _make_config(numprocesses=5)

    with pytest.raises(SystemExit):
        _call_xdist_guard(config)


def test_xdist_guard_rejects_n_8():
    """pytest_configure must raise SystemExit when numprocesses == 8 (> 4) (AC-6)."""
    config = _make_config(numprocesses=8)

    with pytest.raises(SystemExit):
        _call_xdist_guard(config)


def test_xdist_guard_rejects_n_large():
    """pytest_configure must raise SystemExit when numprocesses == 24 (all-cores scenario) (AC-6)."""
    config = _make_config(numprocesses=24)

    with pytest.raises(SystemExit):
        _call_xdist_guard(config)


# ---------------------------------------------------------------------------
# Acceptance tests — must NOT raise SystemExit
# ---------------------------------------------------------------------------


def test_xdist_guard_accepts_n_2():
    """pytest_configure must NOT raise when numprocesses == 2 (AC-6).

    The pyproject.toml addopts default is '-n 2' so this is the most common
    invocation on this host and must always pass.
    """
    config = _make_config(numprocesses=2)
    _call_xdist_guard(config)  # must not raise


def test_xdist_guard_accepts_n_4():
    """pytest_configure must NOT raise when numprocesses == 4 (the maximum allowed) (AC-6)."""
    config = _make_config(numprocesses=4)
    _call_xdist_guard(config)  # must not raise


def test_xdist_guard_accepts_n_0():
    """pytest_configure must NOT raise when numprocesses == 0 (no xdist parallelism) (AC-6)."""
    config = _make_config(numprocesses=0)
    _call_xdist_guard(config)  # must not raise


def test_xdist_guard_accepts_n_1():
    """pytest_configure must NOT raise when numprocesses == 1 (AC-6)."""
    config = _make_config(numprocesses=1)
    _call_xdist_guard(config)  # must not raise


def test_xdist_guard_accepts_missing_numprocesses_attribute():
    """pytest_configure must NOT raise when the config has no numprocesses attribute (AC-6).

    A config without numprocesses means xdist is not installed or not being used.
    The guard must treat missing attribute as 'no xdist' and pass through silently.
    """
    config = _make_config(has_attr=False)
    _call_xdist_guard(config)  # must not raise


def test_xdist_guard_accepts_numprocesses_none():
    """pytest_configure must NOT raise when numprocesses is None (xdist disabled) (AC-6).

    xdist sets numprocesses to None when the user doesn't pass -n, meaning the plugin
    is present but not activated for this run.
    """
    config = _make_config(numprocesses=None)
    _call_xdist_guard(config)  # must not raise
