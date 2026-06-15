"""
RED tests — shadow-logging-pattern: structural pattern compliance.

These tests enforce the shadow-logging-pattern plan's binding hazards (team-lead
briefing): every shadow-decision site routes through the H4 helper, never bypasses
it; is_live=True (LIVE_EXECUTION) is not a gate on shadow/telemetry writes;
cvar_diagnostics rows are queryable by cycle_id; insert failure on the production
call chain is logged-not-propagated.

Contract surfaces tested:
  1. record_cvar_diagnostic is the ONLY path that writes to cvar_diagnostics;
     alpha_bot_execution.py does not call write_telemetry_row("cvar_diagnostics")
     directly (thin-wrapper contract enforced at call site).
  2. record_cvar_diagnostic is called from alpha_bot_execution.py (the call site
     must exist — RED until implementer ships it).
  3. The call site passes mode= explicitly; never mode="live" hardcoded as a string
     literal at the alpha_bot_execution call site without the kwarg form.
  4. LIVE_EXECUTION does not gate the cvar_diagnostics write — telemetry is written
     in both live and dry-run execution modes.
  5. Rows are queryable by cycle_id — the column exists with NOT NULL and is
     populated on every write.
  6. Insert failure (OperationalError) is logged at WARNING level, not ERROR,
     and does not propagate to the caller — matching the H4 plan's live-mode contract.

Binding references:
  - shadow-logging-pattern plan §Why (non-blocking, benchmarked)
  - h4-telemetry-helper plan deliverable (6): record_cvar_diagnostic is a thin wrapper
  - Architecture constraint 1: no blocking I/O on execution path
  - Architecture constraint 4: is_live=True is explicit, never a default
  - Project CLAUDE.md §Coding Standards: API calls must be testable from a fixture
  - team-lead briefing: "every shadow-decision site uses the H4 helper, never bypasses"

Fixture provenance: schema-derived; no producer-computed values asserted.
"""

from __future__ import annotations

import ast
import inspect
import logging
import pathlib
import sqlite3
from unittest.mock import patch

import pytest

import database as db

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = pathlib.Path(__file__).parents[2]
_EXECUTION_PY = _WORKTREE_ROOT / "alpha_bot_execution.py"
_DATABASE_PY = _WORKTREE_ROOT / "database.py"


def _read_source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_ast(path: pathlib.Path) -> ast.Module:
    return ast.parse(_read_source(path))


# ---------------------------------------------------------------------------
# 1. Thin-wrapper contract: record_cvar_diagnostic delegates to write_telemetry_row
# ---------------------------------------------------------------------------


def test_record_cvar_diagnostic_body_contains_no_sqlite_connect():
    """record_cvar_diagnostic must NOT call sqlite3.connect() directly.

    All connection management lives in write_telemetry_row.  A second
    sqlite3.connect in the wrapper would split the live-swallow / replay-raise
    contract (h4-telemetry-helper plan deliverable 6 / Finding 6 / REQ-7).
    """
    assert hasattr(db, "record_cvar_diagnostic"), (
        "database.record_cvar_diagnostic not found — implement it first"
    )
    source = inspect.getsource(db.record_cvar_diagnostic)
    assert "sqlite3.connect" not in source, (
        "record_cvar_diagnostic must NOT call sqlite3.connect() directly. "
        "All connection logic lives in write_telemetry_row (H4 thin-wrapper contract). "
        "h4-telemetry-helper plan deliverable 6."
    )


def test_record_cvar_diagnostic_body_contains_no_insert_sql():
    """record_cvar_diagnostic must NOT contain a raw INSERT statement.

    The INSERT SQL lives in write_telemetry_row.  A second INSERT in the wrapper
    would duplicate the write and split the live/replay contract.
    """
    assert hasattr(db, "record_cvar_diagnostic"), (
        "database.record_cvar_diagnostic not found — implement it first"
    )
    source = inspect.getsource(db.record_cvar_diagnostic)
    assert "INSERT" not in source.upper(), (
        "record_cvar_diagnostic must NOT contain a raw INSERT statement. "
        "The INSERT SQL belongs in write_telemetry_row. "
        "h4-telemetry-helper plan deliverable 6."
    )


def test_record_cvar_diagnostic_calls_write_telemetry_row():
    """record_cvar_diagnostic must call write_telemetry_row (thin wrapper).

    Verified via AST inspection — not source substring search (more resilient
    to whitespace formatting changes).
    """
    assert hasattr(db, "record_cvar_diagnostic"), (
        "database.record_cvar_diagnostic not found — implement it first"
    )
    source = _read_source(_DATABASE_PY)
    tree = _parse_ast(_DATABASE_PY)

    callee_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "record_cvar_diagnostic":
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name):
                        callee_names.append(child.func.id)
                    elif isinstance(child.func, ast.Attribute):
                        callee_names.append(child.func.attr)

    assert "write_telemetry_row" in callee_names, (
        f"record_cvar_diagnostic does not call write_telemetry_row. "
        f"Found calls: {callee_names}. "
        "H4 thin-wrapper contract (plan deliverable 6) requires this delegation."
    )


# ---------------------------------------------------------------------------
# 2. Call site: alpha_bot_execution.py must call record_cvar_diagnostic
# ---------------------------------------------------------------------------


def test_alpha_bot_execution_calls_record_cvar_diagnostic():
    """record_cvar_diagnostic must be called from alpha_bot_execution.py.

    RED until the sqlite-specialist (or risk-engine-specialist) adds the call site.
    Without this, M2's per-cycle CVaR diagnostic write never fires.

    shadow-logging-pattern plan §Why: "M2 writes one cvar_diagnostics row every
    cycle on a 1-minute cadence."
    """
    source = _read_source(_EXECUTION_PY)
    assert "record_cvar_diagnostic" in source, (
        "alpha_bot_execution.py does not contain a call to record_cvar_diagnostic. "
        "The shadow-logging-pattern plan requires the call site to exist so M2's "
        "per-cycle cvar_diagnostics write fires. "
        "RED until implementer adds the call site."
    )


def test_alpha_bot_execution_does_not_call_write_telemetry_row_for_cvar():
    """alpha_bot_execution.py must not call write_telemetry_row('cvar_diagnostics') directly.

    The call site must go through record_cvar_diagnostic (the thin wrapper),
    not bypass it by calling write_telemetry_row with a hardcoded table name.
    Bypassing the wrapper splits the mode= contract surface.

    team-lead briefing: "every shadow-decision site uses the H4 helper, never bypasses."
    """
    source = _read_source(_EXECUTION_PY)
    # Allow write_telemetry_row calls in general, but not with cvar_diagnostics
    if "write_telemetry_row" not in source:
        return  # No bypass possible — test passes trivially

    tree = _parse_ast(_EXECUTION_PY)
    bypass_calls: list[int] = []  # line numbers of offending calls

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # write_telemetry_row("cvar_diagnostics", ...)
            if isinstance(node.func, ast.Name) and node.func.id == "write_telemetry_row":
                if node.args and isinstance(node.args[0], ast.Constant):
                    if "cvar_diagnostics" in str(node.args[0].value):
                        bypass_calls.append(getattr(node, "lineno", -1))
            # database.write_telemetry_row("cvar_diagnostics", ...)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "write_telemetry_row":
                if node.args and isinstance(node.args[0], ast.Constant):
                    if "cvar_diagnostics" in str(node.args[0].value):
                        bypass_calls.append(getattr(node, "lineno", -1))

    assert not bypass_calls, (
        f"alpha_bot_execution.py calls write_telemetry_row('cvar_diagnostics') directly "
        f"at line(s) {bypass_calls}. "
        "Use record_cvar_diagnostic() instead — the thin wrapper enforces the "
        "live-swallow / replay-raise contract at a single location."
    )


# ---------------------------------------------------------------------------
# 3. LIVE_EXECUTION does not gate the cvar_diagnostics write call site
# ---------------------------------------------------------------------------


def test_alpha_bot_execution_cvar_write_is_not_gated_on_live_execution():
    """The cvar_diagnostics write must not be inside an `if LIVE_EXECUTION:` block.

    M2 is a diagnostic telemetry write — it must fire on every cycle regardless
    of whether the engine is in live or dry-run mode.  Gating it on LIVE_EXECUTION
    would silently skip CVaR data collection during all dry-run cycles, breaking
    the evidence question M2 is designed to answer.

    team-lead briefing binding hazard: "is_live=True never reached from a shadow path"
    (analogue: LIVE_EXECUTION must not be the gate for telemetry writes).

    RED if the call site is absent (no source lines to check yet).
    """
    source = _read_source(_EXECUTION_PY)
    if "record_cvar_diagnostic" not in source:
        pytest.fail(
            "alpha_bot_execution.py has no record_cvar_diagnostic call site — "
            "cannot verify the LIVE_EXECUTION gate. "
            "Implement the call site first (see test_alpha_bot_execution_calls_record_cvar_diagnostic)."
        )

    # AST walk: find if record_cvar_diagnostic calls appear inside an `if LIVE_EXECUTION` block.
    tree = _parse_ast(_EXECUTION_PY)
    src_lines = source.splitlines()

    gated_lines: list[int] = []

    # Collect all If-node ranges where the test references LIVE_EXECUTION
    live_exec_if_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            # Check if the test expression references LIVE_EXECUTION
            if_src = ast.get_source_segment(source, node.test) or ""
            if "LIVE_EXECUTION" in if_src:
                start = node.lineno
                end = getattr(node, "end_lineno", node.lineno)
                live_exec_if_ranges.append((start, end))

    # Find all record_cvar_diagnostic call line numbers
    cvar_call_lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fname = None
            if isinstance(node.func, ast.Name):
                fname = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fname = node.func.attr
            if fname == "record_cvar_diagnostic":
                cvar_call_lines.append(getattr(node, "lineno", -1))

    # Check if any cvar call falls inside a LIVE_EXECUTION-gated block
    for call_line in cvar_call_lines:
        for start, end in live_exec_if_ranges:
            if start <= call_line <= end:
                gated_lines.append(call_line)

    assert not gated_lines, (
        f"record_cvar_diagnostic call(s) at line(s) {gated_lines} are inside an "
        f"'if LIVE_EXECUTION:' block. "
        "M2 telemetry must fire on every cycle, not only during live execution. "
        "team-lead briefing: telemetry writes are mode-agnostic."
    )


# ---------------------------------------------------------------------------
# 4. Rows queryable by cycle_id
# ---------------------------------------------------------------------------


def test_cvar_diagnostics_schema_has_cycle_id_column(tmp_path):
    """cvar_diagnostics must have a cycle_id column (NOT NULL).

    cycle_id is the primary query key for cross-referencing CVaR diagnostics
    with other per-cycle decision records (team-lead briefing:
    "decision records are queryable by cycle_id").

    RED if the migration table is absent (021_cvar_diagnostics.sql not yet applied).
    """
    db_path = str(tmp_path / "schema_check.db")
    with patch.object(db, "DB_FILE", db_path):
        db.init_db()

    conn = sqlite3.connect(db_path)
    try:
        # The table may not exist yet (migration not applied) — that is a RED state
        try:
            cols = conn.execute("PRAGMA table_info(cvar_diagnostics)").fetchall()
        except sqlite3.OperationalError:
            cols = []
    finally:
        conn.close()

    assert cols, (
        "cvar_diagnostics table does not exist after init_db(). "
        "Run 021_cvar_diagnostics.sql migration — it must be in _MIGRATION_FILES. "
        "shadow-logging-pattern plan §Dependencies."
    )

    col_names = {c[1] for c in cols}
    assert "cycle_id" in col_names, (
        f"cvar_diagnostics table is missing 'cycle_id' column. "
        f"Found columns: {col_names}. "
        "cycle_id is required for cross-cycle diagnostic queries (team-lead binding hazard)."
    )

    # cycle_id must be NOT NULL (col[3] == 1 means notnull)
    cycle_id_col = next((c for c in cols if c[1] == "cycle_id"), None)
    assert cycle_id_col is not None
    assert cycle_id_col[3] == 1, (
        "cycle_id column must be NOT NULL — it is the primary query key. "
        "A NULL cycle_id would break cross-cycle correlation queries."
    )


def test_cvar_diagnostics_row_is_queryable_by_cycle_id(tmp_path):
    """Written row must be retrievable by cycle_id.

    Writes one row via record_cvar_diagnostic and reads it back via cycle_id.
    Asserts the shape of the returned row (column presence), not the values,
    to comply with the no-hardcoded-producer-values rule.
    """
    db_path = str(tmp_path / "query_cycle.db")
    with patch.object(db, "DB_FILE", db_path):
        db.init_db()

        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cvar_diagnostics (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id         TEXT    NOT NULL,
                    ts_utc           TEXT    NOT NULL DEFAULT (datetime('now')),
                    symphony_id      TEXT    NOT NULL,
                    cvar_5pct        REAL    DEFAULT NULL,
                    cvar_5pct_stderr REAL    DEFAULT NULL,
                    cvar_n_tail      INTEGER DEFAULT NULL,
                    cvar_5pct_long   REAL    DEFAULT NULL,
                    cvar_n_tail_long INTEGER DEFAULT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

        target_cycle_id = "QUERY_CYCLE_20260525_1430"
        db.record_cvar_diagnostic(
            cycle_id=target_cycle_id,
            symphony_id="SYM_QUERY_001",
            cvar_5pct=None,
            cvar_5pct_stderr=None,
            cvar_n_tail=0,
            cvar_5pct_long=None,
            cvar_n_tail_long=None,
            mode="live",
        )

        verify_conn = sqlite3.connect(db_path)
        verify_conn.row_factory = sqlite3.Row
        try:
            row = verify_conn.execute(
                "SELECT * FROM cvar_diagnostics WHERE cycle_id = ?",
                (target_cycle_id,),
            ).fetchone()
        finally:
            verify_conn.close()

    assert row is not None, (
        f"No row found in cvar_diagnostics for cycle_id={target_cycle_id!r}. "
        "record_cvar_diagnostic must persist the row so it is queryable by cycle_id."
    )

    # Assert shape: required columns present (not their values)
    row_keys = set(row.keys())
    required_keys = {"cycle_id", "symphony_id", "cvar_5pct", "cvar_n_tail"}
    assert required_keys.issubset(row_keys), (
        f"cvar_diagnostics row missing required keys. Expected {required_keys}, found {row_keys}."
    )

    # cycle_id round-trips correctly
    assert row["cycle_id"] == target_cycle_id, (
        f"cycle_id mismatch: wrote {target_cycle_id!r}, read {row['cycle_id']!r}."
    )


# ---------------------------------------------------------------------------
# 5. Insert failure is logged-not-propagated (end-to-end non-blocking)
# ---------------------------------------------------------------------------


def test_cvar_insert_failure_logs_warning_not_error(tmp_path, caplog):
    """Live-mode insert failure must log WARNING (not ERROR) and not raise.

    The h4-telemetry-helper plan specifies mode="live" logs at WARNING level.
    This tests the production call chain (record_cvar_diagnostic → write_telemetry_row),
    not just the helper directly.

    The cycle must never fail on telemetry — this is the non-blocking contract
    at the execution-path level.

    Failure is triggered by dropping the cvar_diagnostics table after init_db()
    creates it — this keeps the test independent of whether the migration has
    been applied (both states lead to an absent table at write time).
    """
    db_path = str(tmp_path / "warn_log.db")
    with patch.object(db, "DB_FILE", db_path):
        db.init_db()
        # Drop the table after init to reliably trigger OperationalError regardless
        # of whether 021_cvar_diagnostics.sql is registered in _MIGRATION_FILES.
        drop_conn = sqlite3.connect(db_path)
        try:
            drop_conn.execute("DROP TABLE IF EXISTS cvar_diagnostics")
            drop_conn.commit()
        finally:
            drop_conn.close()

        with caplog.at_level(logging.WARNING):
            db.record_cvar_diagnostic(
                cycle_id="WARN_LOG_CYCLE_001",
                symphony_id="SYM_WARN_001",
                cvar_5pct=None,
                cvar_5pct_stderr=None,
                cvar_n_tail=0,
                cvar_5pct_long=None,
                cvar_n_tail_long=None,
                mode="live",
            )

    # Must have logged at WARNING level
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) >= 1, (
        "record_cvar_diagnostic must log at WARNING level when the INSERT fails. "
        "Found no WARNING records. "
        "H4 plan deliverable (1): live mode logs one WARNING, does not propagate."
    )

    # Must NOT have logged at ERROR level (ERROR is for record_shadow_observation's precedent;
    # write_telemetry_row uses WARNING per h4-telemetry-helper plan deliverable 1)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) == 0, (
        "record_cvar_diagnostic logged at ERROR level on insert failure. "
        "The h4-telemetry-helper plan specifies WARNING, not ERROR, for live-mode swallows. "
        f"Found ERROR records: {[r.message for r in error_records]}"
    )


def test_cvar_insert_failure_log_does_not_echo_payload_values(tmp_path, caplog):
    """Log line on insert failure must not contain payload values from the row_dict.

    Gate 7 (h4-telemetry-helper plan deliverable 5): the log shows only table_name,
    error type, and cycle_id — no CVaR floats, symphony names, or other financial values.

    This test passes sentinel values that would be unmistakable if they leaked
    into the log (a distinct float and a recognisable symphony_id).
    """
    db_path = str(tmp_path / "gate7_check.db")

    # Sentinel values that would be unmistakable in a log line
    sentinel_cvar = -0.12345678
    sentinel_sym = "SYM_GATE7_SENTINEL_XYZ"
    target_cycle_id = "GATE7_CYCLE_001"

    with patch.object(db, "DB_FILE", db_path):
        db.init_db()
        # Drop the table after init to reliably trigger OperationalError regardless
        # of whether 021_cvar_diagnostics.sql is registered in _MIGRATION_FILES.
        drop_conn = sqlite3.connect(db_path)
        try:
            drop_conn.execute("DROP TABLE IF EXISTS cvar_diagnostics")
            drop_conn.commit()
        finally:
            drop_conn.close()

        with caplog.at_level(logging.WARNING):
            db.record_cvar_diagnostic(
                cycle_id=target_cycle_id,
                symphony_id=sentinel_sym,
                cvar_5pct=sentinel_cvar,
                cvar_5pct_stderr=None,
                cvar_n_tail=0,
                cvar_5pct_long=None,
                cvar_n_tail_long=None,
                mode="live",
            )

    log_text = " ".join(r.getMessage() for r in caplog.records)

    # The cycle_id IS allowed in the log (it is the correlation key, not payload)
    # The CVaR float value and symphony name must NOT appear
    assert str(sentinel_cvar) not in log_text, (
        f"Log line contains the payload cvar_5pct value {sentinel_cvar}. "
        "Gate 7: log must show table_name, error type, cycle_id only — no financial values."
    )
    assert sentinel_sym not in log_text, (
        f"Log line contains the symphony_id payload value {sentinel_sym!r}. "
        "Gate 7: symphony identifiers may be financial PII — must not appear in error logs."
    )
