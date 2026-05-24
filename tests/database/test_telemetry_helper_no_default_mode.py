"""
RED tests — H4 telemetry helper: mode= is a required keyword argument.

Contract under test (h4-telemetry-helper plan deliverable 3):
  write_telemetry_row(table_name, row_dict, *, mode: Literal["live","replay"])

The `mode` parameter MUST have no default.  Every call site must state
"live" or "replay" explicitly — this is the analogue of project rule 4
(is_live=True explicit, never a default) applied to the telemetry helper.

Additional hostile invariants (team-lead RED brief):
  - Truthy-non-bool passed as mode value → TypeError or ValueError
  - Invalid string mode value → TypeError or ValueError
  - mode passed positionally (circumventing the keyword-only enforcement) → TypeError

Binding references:
  - h4-telemetry-helper plan §Deliverables (3)
  - Project CLAUDE.md architecture constraint 4: is_live=True explicit, never a default
  - H4 plan risk callout R4: "mode default value disallowed — every call site states
    live or replay explicitly"
  - live-vs-replay-safety-boundary plan risk callout R4: global RNG bleed / mode default

Fixture provenance:
  tests/fixtures/math/telemetry_helper_write_basic.json
  — schema-derived; no producer-computed values asserted.
"""

from __future__ import annotations

import inspect
import json
import pathlib

import pytest

import database as db

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parents[1]
    / "fixtures"
    / "math"
    / "telemetry_helper_write_basic.json"
)


@pytest.fixture(scope="module")
def telemetry_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. mode= omitted entirely → TypeError at the Python call site
# ---------------------------------------------------------------------------


def test_mode_omitted_raises_type_error(telemetry_fixture):
    """Calling write_telemetry_row without mode= must raise TypeError.

    The `mode` parameter is keyword-only with no default.  Python raises
    TypeError ("missing required keyword argument") before the function body
    executes — this is the structural enforcement that every call site is
    forced to state a mode explicitly.
    """
    row = dict(telemetry_fixture["row_dict"])
    table = telemetry_fixture["table_name"]

    with pytest.raises(TypeError):
        db.write_telemetry_row(table, row)  # mode= absent


def test_mode_omitted_error_message_names_mode(telemetry_fixture):
    """The TypeError message must name 'mode' so the caller diagnoses the issue.

    Python's standard message is "missing required keyword argument: 'mode'".
    This pins that the parameter name is 'mode', not an alias.
    """
    row = dict(telemetry_fixture["row_dict"])
    table = telemetry_fixture["table_name"]

    with pytest.raises(TypeError, match="mode"):
        db.write_telemetry_row(table, row)


# ---------------------------------------------------------------------------
# 2. mode passed positionally → TypeError (keyword-only enforcement)
# ---------------------------------------------------------------------------


def test_mode_passed_positionally_raises_type_error(telemetry_fixture):
    """mode= must be keyword-only (after *).

    Passing it as a positional argument must raise TypeError because the
    function signature enforces keyword-only via the `*` separator.
    """
    row = dict(telemetry_fixture["row_dict"])
    table = telemetry_fixture["table_name"]

    with pytest.raises(TypeError):
        # Three positional args — any extra positional raises TypeError if mode is kw-only
        db.write_telemetry_row(table, row, "live")


# ---------------------------------------------------------------------------
# 3. Invalid mode value → TypeError or ValueError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_mode", [
    True,       # truthy bool — should not be accepted (mode is a Literal, not bool)
    False,      # falsy bool
    1,          # truthy int
    0,          # falsy int
    "Live",     # wrong casing
    "LIVE",     # wrong casing
    "Replay",   # wrong casing
    "",         # empty string
    "live ",    # trailing whitespace
    " replay",  # leading whitespace
    None,       # None
])
def test_invalid_mode_value_raises(telemetry_fixture, bad_mode):
    """Hostile: non-literal mode values must be rejected at the call boundary.

    The helper must validate that mode is exactly "live" or "replay" —
    accepting a truthy non-bool (e.g., True, 1) would silently route a
    replay-context caller into live mode, swallowing failures that should
    fail loud.  That is the R4 risk in the h4-telemetry-helper plan.
    """
    row = dict(telemetry_fixture["row_dict"])
    table = telemetry_fixture["table_name"]

    with pytest.raises((TypeError, ValueError)):
        db.write_telemetry_row(table, row, mode=bad_mode)


# ---------------------------------------------------------------------------
# 4. Signature introspection: mode has no default
# ---------------------------------------------------------------------------


def test_write_telemetry_row_exists():
    """write_telemetry_row must be importable from database."""
    assert hasattr(db, "write_telemetry_row"), (
        "database.write_telemetry_row does not exist. "
        "H4 plan deliverable (1) requires this function."
    )
    assert callable(db.write_telemetry_row), (
        "database.write_telemetry_row is not callable."
    )


def test_mode_parameter_has_no_default():
    """Signature inspection: the mode parameter must have no default value.

    This is the structural enforcement of "mode is required" — separate from
    the runtime TypeError test above, this catches an implementation that
    adds a default later via a refactor.
    """
    sig = inspect.signature(db.write_telemetry_row)
    assert "mode" in sig.parameters, (
        "write_telemetry_row has no 'mode' parameter. "
        "H4 plan requires: write_telemetry_row(table_name, row_dict, *, mode)"
    )
    param = sig.parameters["mode"]
    assert param.default is inspect.Parameter.empty, (
        f"mode parameter must have no default; found default={param.default!r}. "
        "H4 plan risk R4: mode default disallowed — every call site must be explicit."
    )


def test_mode_parameter_is_keyword_only():
    """mode must be keyword-only (kind == KEYWORD_ONLY).

    The `*` separator in the signature is what enforces this structurally.
    """
    sig = inspect.signature(db.write_telemetry_row)
    assert "mode" in sig.parameters, "mode parameter not found — see test_mode_parameter_has_no_default"
    param = sig.parameters["mode"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
        f"mode must be KEYWORD_ONLY; found kind={param.kind!r}. "
        "H4 plan: 'mode: Literal[\"live\",\"replay\"]' — after the * separator."
    )


def test_function_accepts_valid_live_mode(telemetry_fixture, tmp_path, monkeypatch):
    """Sanity: write_telemetry_row(table, row, mode='live') does not raise TypeError.

    Confirms the signature is callable with the expected keyword form before
    any DB setup — isolates signature shape from persistence behaviour.
    Uses a tmp_path DB with the correct table so the success path can run.
    """
    db_path = str(tmp_path / "sig_test.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()

    # Create the target table
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cvar_diagnostics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT NOT NULL,
                ts_utc TEXT NOT NULL DEFAULT (datetime('now')),
                symphony_id TEXT NOT NULL,
                cvar_5pct REAL DEFAULT NULL,
                cvar_5pct_stderr REAL DEFAULT NULL,
                cvar_n_tail INTEGER DEFAULT NULL,
                cvar_5pct_long REAL DEFAULT NULL,
                cvar_n_tail_long INTEGER DEFAULT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    row = {**telemetry_fixture["row_dict"], "cycle_id": "CYCLE_SIG_TEST_LIVE"}
    # Must not raise TypeError — only a valid call is checked here
    db.write_telemetry_row(
        telemetry_fixture["table_name"],
        row,
        mode="live",
    )


def test_function_accepts_valid_replay_mode(telemetry_fixture, tmp_path, monkeypatch):
    """Sanity: write_telemetry_row(table, row, mode='replay') does not raise TypeError."""
    db_path = str(tmp_path / "sig_test_replay.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()

    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cvar_diagnostics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT NOT NULL,
                ts_utc TEXT NOT NULL DEFAULT (datetime('now')),
                symphony_id TEXT NOT NULL,
                cvar_5pct REAL DEFAULT NULL,
                cvar_5pct_stderr REAL DEFAULT NULL,
                cvar_n_tail INTEGER DEFAULT NULL,
                cvar_5pct_long REAL DEFAULT NULL,
                cvar_n_tail_long INTEGER DEFAULT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    row = {**telemetry_fixture["row_dict"], "cycle_id": "CYCLE_SIG_TEST_REPLAY"}
    db.write_telemetry_row(
        telemetry_fixture["table_name"],
        row,
        mode="replay",
    )
