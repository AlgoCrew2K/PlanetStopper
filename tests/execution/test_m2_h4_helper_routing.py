"""
RED tests — M2 cvar_diagnostics write is routed exclusively through the H4 helper.

Plan deliverable (4): assert that any call to write cvar_diagnostics from
compute_portfolio_cvar's call-chain flows only through database.record_cvar_diagnostic
(which itself delegates to write_telemetry_row).  A direct conn.execute("INSERT INTO
cvar_diagnostics ...") outside the helper is a grep-fail.

Two complementary checks:
  1. Source scan: alpha_bot_execution.py must not contain a bare INSERT INTO
     cvar_diagnostics SQL string outside of the H4 helper delegation chain.
  2. Source scan: math_engine.py must not contain any cvar_diagnostics INSERT
     — that is a database layer concern.
  3. Behavioural (RED gate): when M2 is called, database.record_cvar_diagnostic
     is the only write path (mock it, assert it is called).

Binding refs:
  - live-vs-replay-safety-boundary plan deliverable (4)
  - H4 plan risk R4: no second connection path
  - database.py:record_cvar_diagnostic (thin wrapper over write_telemetry_row)
  - Plan risk R1: a caller that bypasses record_cvar_diagnostic loses the
    live-swallow / replay-raise contract
"""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import patch, MagicMock

import pytest

import math_engine
import database as db

_WORKTREE_ROOT = pathlib.Path(__file__).parents[2]
_MATH_ENGINE_PATH = _WORKTREE_ROOT / "math_engine.py"
_ALPHA_BOT_PATH = _WORKTREE_ROOT / "alpha_bot_execution.py"


# ---------------------------------------------------------------------------
# 1. math_engine.py has no raw cvar_diagnostics INSERT
# ---------------------------------------------------------------------------


def test_math_engine_has_no_raw_cvar_diagnostics_insert():
    """Source guard: math_engine.py must not contain INSERT INTO cvar_diagnostics.

    All cvar_diagnostics writes must go through database.record_cvar_diagnostic.
    An inline INSERT in math_engine would bypass the H4 helper's live-swallow /
    replay-raise contract (plan deliverable 4 / H4 risk R4).
    """
    source = _MATH_ENGINE_PATH.read_text(encoding="utf-8")
    # Case-insensitive check for the INSERT INTO cvar_diagnostics pattern
    assert "cvar_diagnostics" not in source.lower().replace(
        "cvarassessment", ""
    ).replace("cvar_pct", "").replace("cvar_n_tail", ""), (
        "math_engine.py contains a cvar_diagnostics reference that looks like "
        "a direct write. All cvar_diagnostics writes must go through "
        "database.record_cvar_diagnostic. "
        "math_engine is a pure-math module — it must not own any DB writes."
    )


def test_math_engine_has_no_sqlite3_import_at_module_level():
    """math_engine.py must not import sqlite3 at module level.

    A module-level sqlite3 import in math_engine would enable direct DB writes
    from math functions — a category error.  math_engine is a pure-math module;
    all persistence belongs in database.py.
    """
    source = _MATH_ENGINE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    module_sqlite_imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "sqlite3" in alias.name:
                    module_sqlite_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if "sqlite3" in (node.module or ""):
                module_sqlite_imports.append(node.module or "")

    assert not module_sqlite_imports, (
        "math_engine.py has a top-level sqlite3 import: "
        + str(module_sqlite_imports)
        + ". math_engine must remain pure-math; all DB writes belong in database.py."
    )


# ---------------------------------------------------------------------------
# 2. alpha_bot_execution.py has no raw cvar_diagnostics INSERT outside helper
# ---------------------------------------------------------------------------


def test_alpha_bot_execution_has_no_raw_cvar_diagnostics_insert():
    """Source guard: alpha_bot_execution.py must not contain a raw INSERT INTO
    cvar_diagnostics SQL string.

    The H4 helper (record_cvar_diagnostic) is the one authorised write path.
    A direct INSERT in alpha_bot_execution would bypass the live-swallow /
    replay-raise contract — plan deliverable (4) grep-fail.
    """
    if not _ALPHA_BOT_PATH.exists():
        pytest.skip("alpha_bot_execution.py not found")

    source = _ALPHA_BOT_PATH.read_text(encoding="utf-8")

    # Look for INSERT ... cvar_diagnostics patterns (SQL strings)
    import re
    raw_insert_pattern = re.compile(
        r"INSERT\s+INTO\s+cvar_diagnostics", re.IGNORECASE
    )
    matches = raw_insert_pattern.findall(source)

    assert not matches, (
        f"alpha_bot_execution.py contains {len(matches)} raw INSERT INTO "
        f"cvar_diagnostics statement(s). All cvar_diagnostics writes must flow "
        "through database.record_cvar_diagnostic. A direct INSERT bypasses the "
        "live-swallow / replay-raise contract (H4 risk R4)."
    )


# ---------------------------------------------------------------------------
# 3. Behavioural: M2 write path calls record_cvar_diagnostic (RED gate)
# ---------------------------------------------------------------------------


def test_compute_portfolio_cvar_write_calls_record_cvar_diagnostic(tmp_path, monkeypatch):
    """When compute_portfolio_cvar is called with write=True (or equivalent),
    database.record_cvar_diagnostic is invoked — not a raw sqlite3 call.

    This is RED until M2 lands (compute_portfolio_cvar does not exist yet).
    The test will also fail if M2 bypasses record_cvar_diagnostic.

    We do not assert how many times or with which exact arguments (those are
    producer-computed, PA-18) — we assert the delegation happened.
    """
    if not hasattr(math_engine, "compute_portfolio_cvar"):
        pytest.fail(
            "compute_portfolio_cvar not found in math_engine. "
            "Plan deliverable (4) is RED — implement M2 to go GREEN."
        )

    db_path = str(tmp_path / "h4_routing_test.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()

    # Build minimal inputs from the known fixture shape
    holdings = [
        {"ticker": "AAA", "allocation": 0.6, "last_percent_change": 0.0},
        {"ticker": "BBB", "allocation": 0.4, "last_percent_change": 0.0},
    ]

    def _date_key(i):
        base = 1 + i
        m = 1 + (base - 1) // 28
        d = 1 + (base - 1) % 28
        return f"2024-{m:02d}-{d:02d}"

    history = {}
    for i in range(50):
        sign = 1 if i % 2 == 0 else -1
        history[_date_key(i)] = {
            "SPY": {"daily_ret": sign * 0.005},
            "AAA": {"daily_ret": sign * 0.008},
            "BBB": {"daily_ret": sign * 0.004},
        }

    with patch.object(db, "record_cvar_diagnostic") as mock_record:
        mock_record.return_value = None

        math_engine.compute_portfolio_cvar(
            cycle_id="20240115_0930",
            holdings=holdings,
            historical_data=history,
            spy_today_return=0.0,
            simulation_paths=200,
            neighbor_k=10,
            mode="live",  # plan: mode is always explicit, no default
        )

    assert mock_record.called, (
        "compute_portfolio_cvar must call database.record_cvar_diagnostic to "
        "persist the cvar row. It did not call it — either M2 is not writing at "
        "all, or it is writing via a direct INSERT (bypassing the H4 helper). "
        "Both are defects (plan deliverable 4)."
    )

    # The mode= keyword arg must have been passed to record_cvar_diagnostic
    call_kwargs = mock_record.call_args.kwargs if mock_record.call_args else {}
    call_positionals = mock_record.call_args.args if mock_record.call_args else ()
    all_args_str = str(call_kwargs) + str(call_positionals)

    assert "mode" in call_kwargs or "live" in all_args_str or "replay" in all_args_str, (
        "record_cvar_diagnostic must be called with an explicit mode= argument. "
        "Plan risk R4: mode default disallowed — every call site states live or replay explicitly. "
        f"Call args seen: args={call_positionals!r}, kwargs={call_kwargs!r}"
    )
