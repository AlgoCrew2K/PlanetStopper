"""
RED tests — H4 telemetry helper: log-redaction contract (overlay gate 7).

Contract under test (h4-telemetry-helper plan deliverable 5):
  On live-mode error, the log line contains:
    - table_name
    - the error type (class name)
    - cycle_id from row_dict  (correlation handle only — not financial data)
  The log line does NOT contain:
    - any other value from row_dict
    - raw Composer/Alpaca response body fragments
    - financial column values (cvar_5pct, cvar_5pct_stderr, cvar_n_tail, etc.)

Overlay gate 7 (quant-code-reviewer gate): no Composer/Alpaca response-body
echo in logs.  The row_dict may contain financial values; echoing those into
the log is the same violation class as echoing an API response body.

Binding references:
  - h4-telemetry-helper plan §Deliverables (5)
  - quant-code-reviewer overlay gate 7
  - live-vs-replay-safety-boundary plan risk R3 (no global state / side-effects)

Fixture provenance:
  tests/fixtures/math/telemetry_helper_write_basic.json
  — schema-derived; no producer-computed values asserted.
  Financial column values in test rows use distinguishable sentinel patterns
  (e.g., 0.031415926) so we can grep for them in log output without asserting
  the values are meaningful.
"""

from __future__ import annotations

import json
import logging
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
# Helpers
# ---------------------------------------------------------------------------

def _collect_all_log_text(records: list) -> str:
    """Concatenate all log record messages for inspection."""
    return " | ".join(r.getMessage() for r in records)


# ---------------------------------------------------------------------------
# 1. Permitted log fields: table_name and error type present
# ---------------------------------------------------------------------------


def test_live_error_log_contains_table_name(telemetry_fixture, caplog):
    """The log message on a live-mode error includes the table_name.

    This is the minimum required identifier for an operator to locate which
    table's write failed.
    """
    row = dict(telemetry_fixture["row_dict"])
    table_name = "nonexistent_table_gate7"

    with caplog.at_level(logging.WARNING, logger="database"):
        db.write_telemetry_row(table_name, row, mode="live")

    all_text = _collect_all_log_text(caplog.records)
    assert table_name in all_text, (
        f"Log must include table_name '{table_name}'; "
        f"full log text: {all_text!r}"
    )


def test_live_error_log_contains_error_type(telemetry_fixture, caplog):
    """The log message on a live-mode error includes the error type.

    'Error' is the common suffix across all sqlite3 exception classes;
    its presence confirms the error was characterised (not silently dropped).
    """
    row = dict(telemetry_fixture["row_dict"])

    with caplog.at_level(logging.WARNING, logger="database"):
        db.write_telemetry_row("nonexistent_table_gate7b", row, mode="live")

    all_text = _collect_all_log_text(caplog.records)
    assert "Error" in all_text or "error" in all_text, (
        f"Log must contain the error type name; "
        f"full log text: {all_text!r}"
    )


# ---------------------------------------------------------------------------
# 2. Forbidden log fields: financial column values must NOT appear
# ---------------------------------------------------------------------------


def test_live_error_log_does_not_echo_cvar_5pct(caplog, tmp_path, monkeypatch):
    """Gate 7: cvar_5pct value must not appear in any log record.

    Uses a row with a distinctive cvar_5pct value (pi-fragment) so any
    substring match in logs is unambiguous.
    """
    db_path = str(tmp_path / "gate7a.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()

    row = {
        "cycle_id": "CYCLE_GATE7_001",
        "symphony_id": "SYM_GATE7",
        "cvar_5pct": 0.031415926,   # recognisable sentinel — must NOT appear in log
        "cvar_5pct_stderr": 0.001,
        "cvar_n_tail": 7,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }

    with caplog.at_level(logging.DEBUG, logger="database"):
        db.write_telemetry_row("nonexistent_gate7a", row, mode="live")

    all_text = _collect_all_log_text(caplog.records)
    assert "0.031415926" not in all_text, (
        f"Gate 7 violation: cvar_5pct value found in log output. "
        f"The log must not echo financial column values. "
        f"Full log text: {all_text!r}"
    )


def test_live_error_log_does_not_echo_cvar_5pct_stderr(caplog, tmp_path, monkeypatch):
    """Gate 7: cvar_5pct_stderr value must not appear in any log record."""
    db_path = str(tmp_path / "gate7b.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()

    row = {
        "cycle_id": "CYCLE_GATE7_002",
        "symphony_id": "SYM_GATE7",
        "cvar_5pct": 0.01,
        "cvar_5pct_stderr": 0.027182818,  # e-fragment — must NOT appear in log
        "cvar_n_tail": 7,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }

    with caplog.at_level(logging.DEBUG, logger="database"):
        db.write_telemetry_row("nonexistent_gate7b", row, mode="live")

    all_text = _collect_all_log_text(caplog.records)
    assert "0.027182818" not in all_text, (
        f"Gate 7 violation: cvar_5pct_stderr value found in log output. "
        f"Full log text: {all_text!r}"
    )


def test_live_error_log_does_not_echo_symphony_id(caplog, tmp_path, monkeypatch):
    """Gate 7: symphony_id must not appear in the error log.

    The symphony_id is a routing identifier, not a correlation handle for
    telemetry writes.  Only cycle_id is the permitted identifier.  Echoing
    symphony_id widens the log surface unnecessarily.
    """
    db_path = str(tmp_path / "gate7c.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()

    distinctive_symphony = "SYM_GATE7_DISTINCTIVE_ID_XQ9"
    row = {
        "cycle_id": "CYCLE_GATE7_003",
        "symphony_id": distinctive_symphony,
        "cvar_5pct": None,
        "cvar_5pct_stderr": None,
        "cvar_n_tail": 0,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }

    with caplog.at_level(logging.DEBUG, logger="database"):
        db.write_telemetry_row("nonexistent_gate7c", row, mode="live")

    all_text = _collect_all_log_text(caplog.records)
    assert distinctive_symphony not in all_text, (
        f"Gate 7: symphony_id must not appear in error log. "
        f"Only cycle_id is the permitted correlation handle. "
        f"Full log text: {all_text!r}"
    )


def test_live_error_log_does_not_dump_entire_row_dict(caplog, tmp_path, monkeypatch):
    """Gate 7: the entire row_dict must not be stringified into the log.

    A naive implementation might log `str(row_dict)` — that would expose all
    financial values.  We detect this by checking that the full dict repr
    does not appear as a substring of any log record.
    """
    db_path = str(tmp_path / "gate7d.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()

    row = {
        "cycle_id": "CYCLE_GATE7_004",
        "symphony_id": "SYM_GATE7D",
        "cvar_5pct": 0.0424242,
        "cvar_5pct_stderr": 0.0131313,
        "cvar_n_tail": 8,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }
    # repr key that would only appear if the entire dict was stringified
    row_repr_fragment = "cvar_5pct_stderr"

    with caplog.at_level(logging.DEBUG, logger="database"):
        db.write_telemetry_row("nonexistent_gate7d", row, mode="live")

    all_text = _collect_all_log_text(caplog.records)
    assert row_repr_fragment not in all_text, (
        f"Gate 7: log appears to contain a stringified row_dict (found "
        f"'{row_repr_fragment}' in log). The log must not echo the row payload. "
        f"Full log text: {all_text!r}"
    )


# ---------------------------------------------------------------------------
# 3. No log output in replay mode (failure raises, does not log-then-raise)
# ---------------------------------------------------------------------------


def test_replay_mode_error_emits_no_warning_log(
    telemetry_fixture, caplog, tmp_path, monkeypatch
):
    """Replay mode: on DB error, no WARNING is logged before the exception.

    If the helper logs-then-raises, the log would contain financial payload
    under gate 7 scrutiny AND would reveal implementation detail about the
    exception path.  The replay contract is: raise directly, no log.
    """
    import sqlite3

    db_path = str(tmp_path / "gate7e.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()

    row = dict(telemetry_fixture["row_dict"])

    with caplog.at_level(logging.DEBUG, logger="database"):
        with pytest.raises(sqlite3.Error):
            db.write_telemetry_row("nonexistent_gate7e", row, mode="replay")

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warning_records) == 0, (
        f"Replay mode must not log before raising. "
        f"Found {len(warning_records)} warning(s): "
        f"{[r.getMessage() for r in warning_records]}"
    )
