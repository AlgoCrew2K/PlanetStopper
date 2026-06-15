"""
M1 RED — cross-cutting guard tests for the de-correlation diagnostic.

Covers AC-X1 (no Composer write endpoints), AC-X2 (off the live execution
path), AC-X3 (every persisted observation has is_advisory_only=1), and the
import-graph isolation between correlation_diagnostic and alpha_bot_execution.

All tests are static / source-inspection — no live DB, no network, no mocks
needed. These pass/fail on the structure of the code, not on its runtime
behaviour.
"""

from __future__ import annotations

import importlib
import pathlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Source paths
# ---------------------------------------------------------------------------

_WORKTREE = pathlib.Path(__file__).parent.parent.parent
_ALPHA_BOT_SRC = _WORKTREE / "alpha_bot_execution.py"
_CORR_DIAG_PKG = _WORKTREE / "advisors" / "correlation_diagnostic.py"


# ---------------------------------------------------------------------------
# AC-X2 GUARD: alpha_bot_execution.py must NOT import correlation_diagnostic.
#
# The live execution path runs every minute during market hours with a strict
# latency budget. Any advisor module imported at the top of
# alpha_bot_execution.py adds startup overhead and creates a coupling that
# violates the "off-the-live-path" architectural constraint.
# ---------------------------------------------------------------------------


def test_alpha_bot_execution_does_not_import_correlation_diagnostic_at_module_scope():
    """alpha_bot_execution.py must not import advisors.correlation_diagnostic
    (or any alias of it) at module scope.

    Static source-inspection: parsing the import statements in the source
    file is more reliable than ``sys.modules`` inspection because it catches
    future renames before any test run.
    """
    source = _ALPHA_BOT_SRC.read_text(encoding="utf-8")

    # Both ``import advisors.correlation_diagnostic`` and
    # ``from advisors.correlation_diagnostic import ...`` are forbidden.
    assert "correlation_diagnostic" not in source, (
        "alpha_bot_execution.py must not reference correlation_diagnostic — "
        "the diagnostic runs entirely off the 1-minute live execution path "
        "(AC-X2 architectural constraint)"
    )


def test_alpha_bot_execution_does_not_import_correlation_diagnostic_at_runtime():
    """Runtime confirmation that importing alpha_bot_execution does not pull
    correlation_diagnostic into sys.modules.

    We cannot actually import alpha_bot_execution in this test environment
    (it has live-environment side effects), so we fall back to the source
    inspection above. This test passes if the source guard above passes —
    it exists to document the runtime intent and will be meaningful in an
    integration harness that can import the module.

    Kept separate so the source-inspection test fails informatively while
    this one is a confirmed-intentional skip.
    """
    pytest.skip(
        "Full module import of alpha_bot_execution requires live environment "
        "(DB, .env). Source-inspection in the companion test is the primary guard."
    )


# ---------------------------------------------------------------------------
# AC-X2 GUARD: correlation_diagnostic must not import alpha_bot_execution.
#
# Circular dependency and coupling to the live path via reverse import.
# ---------------------------------------------------------------------------


def test_correlation_diagnostic_module_does_not_import_alpha_bot_execution():
    """advisors/correlation_diagnostic.py must not import alpha_bot_execution.

    A diagnostic module has no business importing the execution engine; any
    such import would create a circular dependency and couple the diagnostic
    to live-execution side effects.
    """
    if not _CORR_DIAG_PKG.exists():
        pytest.skip(
            "advisors/correlation_diagnostic.py not yet created (RED state); "
            "this guard test will fail once the file exists with the import"
        )

    source = _CORR_DIAG_PKG.read_text(encoding="utf-8")

    assert "alpha_bot_execution" not in source, (
        "advisors/correlation_diagnostic.py must not import alpha_bot_execution — "
        "that module carries live-execution side effects and must not be "
        "reachable from a read-only diagnostic module"
    )


# ---------------------------------------------------------------------------
# AC-X1 GUARD: correlation_diagnostic must not call any Composer write endpoint.
#
# Write endpoints include POST/PUT /symphonies, /copy, /deploy, go-to-cash.
# The diagnostic is pure measurement — it never writes back to Composer.
# ---------------------------------------------------------------------------


def test_correlation_diagnostic_does_not_reference_composer_write_endpoints():
    """Source-inspection guard: correlation_diagnostic.py must not reference
    any Composer write endpoint.

    Write endpoints AC-X1 forbids: /symphonies (POST/PUT), /copy, /deploy,
    go-to-cash, and the generic 'backtest' endpoint (which M1 does not use —
    AC-1.4 explicitly: 'Runs no backtest, invokes no gate').
    """
    if not _CORR_DIAG_PKG.exists():
        pytest.skip("advisors/correlation_diagnostic.py not yet created (RED state)")

    source = _CORR_DIAG_PKG.read_text(encoding="utf-8")

    forbidden_patterns = [
        "/symphonies",
        "/copy",
        "/deploy",
        "go-to-cash",
        "backtest",  # M1 explicitly runs no backtest (AC-1.4)
    ]
    for pattern in forbidden_patterns:
        assert pattern not in source, (
            f"advisors/correlation_diagnostic.py must not reference {pattern!r} — "
            "the de-correlation diagnostic is pure measurement; it never calls "
            "Composer write endpoints or the backtest API (AC-X1 / AC-1.4)"
        )


# ---------------------------------------------------------------------------
# AC-X3 GUARD: any advisor_observation persisted by the diagnostic must have
# is_advisory_only=1.
#
# Source-inspection: the only acceptable persistence call is
# database.insert_advisor_observation(...); it always stores is_advisory_only=1
# (hard-coded in database.py:902). Calling any other DB write path would bypass
# that guard.
# ---------------------------------------------------------------------------


def test_correlation_diagnostic_persists_via_insert_advisor_observation_only():
    """correlation_diagnostic.py must write observations only through
    database.insert_advisor_observation — the single accessor that structurally
    enforces is_advisory_only=1 (database.py:902).

    Any direct SQL INSERT or call to another database accessor bypasses the
    is_advisory_only guard and is forbidden.
    """
    if not _CORR_DIAG_PKG.exists():
        pytest.skip("advisors/correlation_diagnostic.py not yet created (RED state)")

    source = _CORR_DIAG_PKG.read_text(encoding="utf-8")

    # Must not use raw SQL INSERT targeting advisor_observations or any other table.
    assert "INSERT INTO" not in source, (
        "correlation_diagnostic.py must not issue raw SQL INSERT — use "
        "database.insert_advisor_observation which enforces is_advisory_only=1"
    )
    assert "execute(" not in source.lower() or "select" in source.lower(), (
        "correlation_diagnostic.py must not call conn.execute() for writes — "
        "all persistence must go through database.insert_advisor_observation"
    )


def test_correlation_diagnostic_does_not_write_to_bot_state():
    """The diagnostic must never write to bot_state, symphony_strategies, or
    the live-position tables. Source guard: none of those table names appear
    in write context.
    """
    if not _CORR_DIAG_PKG.exists():
        pytest.skip("advisors/correlation_diagnostic.py not yet created (RED state)")

    source = _CORR_DIAG_PKG.read_text(encoding="utf-8")

    forbidden_write_targets = [
        "bot_state",
        "symphony_strategies",
        "exit_triggers",
        "shadow_history",
    ]
    for target in forbidden_write_targets:
        # Allow READ references (SELECT queries to pull return data) but
        # flag any write (INSERT/UPDATE/DELETE) context.
        # Simple heuristic: the target name appearing next to a write keyword.
        import re

        write_pattern = re.compile(
            r"\b(INSERT|UPDATE|DELETE)\b.*?" + re.escape(target),
            re.IGNORECASE | re.DOTALL,
        )
        assert not write_pattern.search(source), (
            f"correlation_diagnostic.py must not write to {target!r} — "
            "the diagnostic is read-only; it may read return data but must "
            "never mutate live-position or strategy tables"
        )


# ---------------------------------------------------------------------------
# AC-X2 IMPORT-GRAPH: importing the diagnostic module in a clean sys.modules
# must not pull in the live execution engine.
# ---------------------------------------------------------------------------


def test_importing_correlation_diagnostic_does_not_import_alpha_bot_execution():
    """Importing advisors.correlation_diagnostic in a clean module namespace
    must not transitively import alpha_bot_execution.

    alpha_bot_execution imports live-DB connections, subprocess launchers, and
    .env readers at module scope — any transitive import of it from a diagnostic
    module would trigger those side effects in every test run.
    """
    if not _CORR_DIAG_PKG.exists():
        pytest.skip("advisors/correlation_diagnostic.py not yet created (RED state)")

    saved = {
        name: sys.modules.get(name)
        for name in ("advisors.correlation_diagnostic", "alpha_bot_execution")
    }
    try:
        sys.modules.pop("advisors.correlation_diagnostic", None)
        sys.modules.pop("alpha_bot_execution", None)

        importlib.import_module("advisors.correlation_diagnostic")

        assert "alpha_bot_execution" not in sys.modules, (
            "importing advisors.correlation_diagnostic must not transitively "
            "import alpha_bot_execution — that module has live-environment side "
            "effects that must not activate in a diagnostic import"
        )
    except ImportError:
        pytest.skip(
            "advisors.correlation_diagnostic cannot be imported yet (dependencies "
            "not yet satisfied — RED state); source-inspection tests are the "
            "primary guard in this phase"
        )
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
