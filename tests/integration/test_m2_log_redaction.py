"""
RED integration tests — M2 CVaR diagnostic: Composer response payload injected
as M2 holdings input must not appear in log output on the unhappy path.

The M2 log-redaction contract (logging-redaction plan §Deliverables 3):
  When a Composer response payload is used to construct an M2 holdings input
  and a DB error fires on write, any log line that fires contains:
    - cycle_id  (the correlation handle)
    - the error class (e.g., OperationalError)
  And NOT:
    - any Composer response body field value (account_id, position_qty,
      webhook URL, bearer token, or any raw dollar/percent amount from the
      response body that was used to construct the holdings row)

These tests are INTEGRATION tests: they exercise the record_cvar_diagnostic
wrapper (the M2 write path) rather than write_telemetry_row directly.
The distinction matters: a future Phase-1 change could introduce a log line
in record_cvar_diagnostic itself, or its caller, that bypasses write_telemetry_row's
gate-7-clean log call.  These tests catch that regression.

Binding references:
  - feature-plans/decision-science/phase-1/logging-redaction/plan.md §Deliverables (3)
  - feature-plans/decision-science/phase-1/m2-cvar-diagnostic/plan.md
  - tests/fixtures/log-redaction-allowlist.txt

Fixture provenance:
  Synthetic Composer response-style payloads constructed to match the CLASS
  of data Composer returns.  No real credentials or account IDs.
  Patterns use regex to match secret classes — never literal values.

Hostile scenarios covered:
  C1. Composer account_id in cycle_id-derived context flows into an error
  C2. Composer position dollar value appears in cvar_5pct-adjacent exception
  C3. Composer bearer token appears in a stack-trace-style exception message
  C4. record_cvar_diagnostic caller logs the raw holdings dict on failure
  C5. The full cycle_id derived from a Composer account UUID leaks non-cycle parts
"""

from __future__ import annotations

import logging
import re
import sqlite3
from unittest.mock import patch

import pytest

import database as db

# ---------------------------------------------------------------------------
# Secret-class patterns (see test_h4_helper_log_redaction.py for rationale)
# ---------------------------------------------------------------------------

# Composer account IDs are UUIDs (8-4-4-4-12 hex format)
_COMPOSER_ACCOUNT_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

# Dollar amounts with cents that appear in Composer position values
_DOLLAR_AMOUNT_PATTERN = re.compile(r"\$[\d,]+\.\d{2}|\d{4,}\.\d{2}")

# Webhook URL pattern
_WEBHOOK_URL_PATTERN = re.compile(r"/api/webhooks/\d+/[A-Za-z0-9_-]{20,}")

# Bearer token
_BEARER_FRAGMENT_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}")


def _collect_all_log_text(records: list) -> str:
    """Concatenate all formatted log record messages."""
    return " | ".join(r.getMessage() for r in records)


@pytest.fixture
def isolated_db_with_cvar(tmp_path, monkeypatch):
    """Isolated DB with cvar_diagnostics table for M2 integration tests."""
    db_path = str(tmp_path / "m2_integration.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()
    conn = sqlite3.connect(db_path)
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
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# C1: Composer account_id embedded in a DB error does not appear in log
# ---------------------------------------------------------------------------

def test_composer_account_id_in_db_error_does_not_appear_in_m2_log(
    isolated_db_with_cvar, caplog
):
    """M2 gate 7 C1: when a DB error message contains a Composer account UUID
    (simulating a DB error that was propagated with context from the Composer
    response), the UUID must not appear in the M2 log output.

    record_cvar_diagnostic routes through write_telemetry_row.  Any log line
    emitted by either must be gate-7 clean — no UUID from the API response body.
    """
    # Composer account IDs are UUIDs — use a recognisable test UUID
    composer_account_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def _error_raising_connect(*args, **kwargs):
        raise sqlite3.OperationalError(
            f"INSERT failed: account_id={composer_account_uuid} not found in accounts"
        )

    with patch("sqlite3.connect", _error_raising_connect):
        with caplog.at_level(logging.DEBUG, logger="database"):
            db.record_cvar_diagnostic(
                cycle_id="CYCLE_M2_C1_001",
                symphony_id="SYM_M2_C1",
                cvar_5pct=None,
                cvar_5pct_stderr=None,
                cvar_n_tail=0,
                cvar_5pct_long=None,
                cvar_n_tail_long=None,
                mode="live",
            )

    all_text = _collect_all_log_text(caplog.records)
    assert not _COMPOSER_ACCOUNT_UUID_PATTERN.search(all_text), (
        f"M2 gate 7 C1 violation: Composer account UUID found in log output. "
        f"Full log text: {all_text!r}"
    )


# ---------------------------------------------------------------------------
# C2: Composer position dollar value in exception must not appear in M2 log
# ---------------------------------------------------------------------------

def test_composer_position_dollar_value_in_error_does_not_appear_in_m2_log(
    isolated_db_with_cvar, caplog
):
    """M2 gate 7 C2: a dollar amount from a Composer position response
    embedded in a DB error message must not appear in log output.

    Composer responses include position values like 'current_value: 12345.67'.
    If such a value is embedded in a DB exception (e.g., via a constraint check
    error) and that exception flows through record_cvar_diagnostic, the value
    must be stripped before logging.
    """
    def _error_raising_connect(*args, **kwargs):
        raise sqlite3.OperationalError(
            "constraint failed: current_value=98765.43 exceeds position limit"
        )

    with patch("sqlite3.connect", _error_raising_connect):
        with caplog.at_level(logging.DEBUG, logger="database"):
            db.record_cvar_diagnostic(
                cycle_id="CYCLE_M2_C2_001",
                symphony_id="SYM_M2_C2",
                cvar_5pct=None,
                cvar_5pct_stderr=None,
                cvar_n_tail=0,
                cvar_5pct_long=None,
                cvar_n_tail_long=None,
                mode="live",
            )

    all_text = _collect_all_log_text(caplog.records)
    assert not _DOLLAR_AMOUNT_PATTERN.search(all_text), (
        f"M2 gate 7 C2 violation: dollar amount pattern found in log output. "
        f"Composer position values in exception messages must be stripped. "
        f"Full log text: {all_text!r}"
    )


# ---------------------------------------------------------------------------
# C3: bearer token in exception message does not leak through M2 log path
# ---------------------------------------------------------------------------

def test_bearer_token_in_db_error_does_not_appear_in_m2_log(
    isolated_db_with_cvar, caplog
):
    """M2 gate 7 C3: a Composer bearer token embedded in a DB error message
    must not appear in any M2 log record.

    Composer uses Bearer tokens for authentication.  If a helper function
    accidentally includes the request authorization header in a DB exception
    message (e.g., via a chained exception), the token must be stripped.
    """
    def _error_raising_connect(*args, **kwargs):
        raise sqlite3.OperationalError(
            "auth context: Bearer EyJhBcDeFgHiJkLmNoPqRstuVwXYZabc123 rejected"
        )

    with patch("sqlite3.connect", _error_raising_connect):
        with caplog.at_level(logging.DEBUG, logger="database"):
            db.record_cvar_diagnostic(
                cycle_id="CYCLE_M2_C3_001",
                symphony_id="SYM_M2_C3",
                cvar_5pct=None,
                cvar_5pct_stderr=None,
                cvar_n_tail=0,
                cvar_5pct_long=None,
                cvar_n_tail_long=None,
                mode="live",
            )

    all_text = _collect_all_log_text(caplog.records)
    assert not _BEARER_FRAGMENT_PATTERN.search(all_text), (
        f"M2 gate 7 C3 violation: Bearer token fragment found in M2 log output. "
        f"Full log text: {all_text!r}"
    )


# ---------------------------------------------------------------------------
# C4: full cvar_5pct value must not appear in log from record_cvar_diagnostic
# ---------------------------------------------------------------------------

def test_cvar_5pct_value_does_not_appear_in_m2_error_log(
    isolated_db_with_cvar, caplog
):
    """M2 gate 7 C4: the cvar_5pct value passed to record_cvar_diagnostic
    must not appear in any log record emitted on the error path.

    This is the same assertion as the H4 unit test but from the M2 interface —
    record_cvar_diagnostic is the caller; it must not log the cvar_5pct value
    either directly or by passing it as part of a row repr to write_telemetry_row.

    Uses a distinctive sentinel value (pi fragment) so any match is unambiguous.
    """
    distinctive_cvar = 0.031415926  # pi/100 sentinel — must NOT appear in log

    with patch("sqlite3.connect") as mock_connect:
        mock_connect.side_effect = sqlite3.OperationalError(
            "no such table: cvar_diagnostics"
        )
        with caplog.at_level(logging.DEBUG, logger="database"):
            db.record_cvar_diagnostic(
                cycle_id="CYCLE_M2_C4_001",
                symphony_id="SYM_M2_C4",
                cvar_5pct=distinctive_cvar,
                cvar_5pct_stderr=0.001,
                cvar_n_tail=7,
                cvar_5pct_long=None,
                cvar_n_tail_long=None,
                mode="live",
            )

    all_text = _collect_all_log_text(caplog.records)
    assert "0.031415926" not in all_text, (
        f"M2 gate 7 C4 violation: cvar_5pct sentinel value found in log output. "
        f"Full log text: {all_text!r}"
    )


# ---------------------------------------------------------------------------
# C5: cycle_id IS present (permitted field) — positive control
# ---------------------------------------------------------------------------

def test_cycle_id_present_in_m2_error_log(
    isolated_db_with_cvar, caplog
):
    """Positive control C5: cycle_id must appear in the M2 error log record.

    The allowlist permits cycle_id as the correlation handle.  An implementation
    that produces an empty log record or omits cycle_id fails this test.
    Operators need the cycle_id to correlate log entries with DB records.
    """
    cycle_id = "CYCLE_M2_POSCTRL_001"

    with patch("sqlite3.connect") as mock_connect:
        mock_connect.side_effect = sqlite3.OperationalError(
            "no such table: cvar_diagnostics"
        )
        with caplog.at_level(logging.WARNING, logger="database"):
            db.record_cvar_diagnostic(
                cycle_id=cycle_id,
                symphony_id="SYM_M2_POSCTRL",
                cvar_5pct=None,
                cvar_5pct_stderr=None,
                cvar_n_tail=0,
                cvar_5pct_long=None,
                cvar_n_tail_long=None,
                mode="live",
            )

    all_text = _collect_all_log_text(caplog.records)
    assert cycle_id in all_text, (
        f"Positive control C5: cycle_id must appear in M2 error log. "
        f"Full log text: {all_text!r}"
    )
    # Error type must also be present
    assert re.search(r"OperationalError|Error", all_text), (
        f"Positive control C5: error type must appear in M2 error log. "
        f"Full log text: {all_text!r}"
    )


# ---------------------------------------------------------------------------
# C6: replay mode raises and emits no log — same contract as H4 unit test
#     but verified at the record_cvar_diagnostic interface level
# ---------------------------------------------------------------------------

def test_m2_replay_mode_raises_and_emits_no_warning(
    isolated_db_with_cvar, caplog
):
    """M2 gate 7 C6: replay mode must raise the DB error without logging.

    This mirrors the H4 unit test but asserts the contract at the
    record_cvar_diagnostic level — the M2 interface.  If record_cvar_diagnostic
    adds its own log before delegating to write_telemetry_row, this test fails.
    """
    with patch("sqlite3.connect") as mock_connect:
        mock_connect.side_effect = sqlite3.OperationalError(
            "no such table: cvar_diagnostics"
        )
        with caplog.at_level(logging.DEBUG, logger="database"):
            with pytest.raises(sqlite3.OperationalError):
                db.record_cvar_diagnostic(
                    cycle_id="CYCLE_M2_C6_001",
                    symphony_id="SYM_M2_C6",
                    cvar_5pct=None,
                    cvar_5pct_stderr=None,
                    cvar_n_tail=0,
                    cvar_5pct_long=None,
                    cvar_n_tail_long=None,
                    mode="replay",
                )

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warning_records) == 0, (
        f"M2 gate 7 C6: replay mode must not log before raising. "
        f"Found {len(warning_records)} warning(s): "
        f"{[r.getMessage() for r in warning_records]}"
    )
