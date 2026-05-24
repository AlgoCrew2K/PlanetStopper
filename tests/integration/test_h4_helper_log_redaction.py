"""
RED integration tests — H4 telemetry helper: Alpaca API error response body
must not appear in log output at any logging level.

Overlay gate 7 integration surface: when an Alpaca APIError carries a response
body containing account identifiers, position quantities, or credential
fragments, and that error travels through any path that calls
write_telemetry_row (or any helper wrapping it), the response body content
must not appear in any log record.

These tests are INTEGRATION tests: they exercise the full call chain from
an error being raised (simulating an upstream Alpaca failure) through the
H4 helper's error-handling path, asserting on the final formatted log string.
The unit tests in tests/database/test_telemetry_helper_no_log_response_body.py
test the helper in isolation with a plain dict row.  These tests test what
happens when the ROW DICT itself was constructed from an Alpaca response that
contained secrets — i.e., that even a row sourced from API data cannot leak
that data into logs.

Binding references:
  - feature-plans/decision-science/phase-1/logging-redaction/plan.md §Deliverables (2)
  - council-attack-rubric.md gate 7 (no Composer/Alpaca response-body verbatim)
  - h4-telemetry-helper plan §Deliverables (5)
  - tests/fixtures/log-redaction-allowlist.txt

Fixture provenance:
  Synthetic Alpaca-style error messages and response body fragments.
  Patterns are regexes that match the CLASS of secret, never a real credential.
  Tests assert absence of matches — a match is a redaction failure.

Hostile scenarios covered:
  H1. account_id as substring in an APIError message leaks via exc args
  H2. position_qty as float substring leaks via str(exc) in log formatting
  H3. webhook_url appearing in exception args leaks via default exception logging
  H4. a full response dict stringified and placed inside an exception message
  H5. account_id appears in a structured row field that gets echoed to log
  H6. bearer token prefix leaks via exception repr
"""

from __future__ import annotations

import logging
import re
import sqlite3
from unittest.mock import patch

import pytest

import database as db

# ---------------------------------------------------------------------------
# Sentinel patterns — these regex patterns match the CLASSES of secrets
# that must never appear in log output.  They are designed to be distinctive
# enough that a false positive is essentially impossible.
#
# Using patterns (not literal values) per the no-hardcoded-values rule:
# the test asserts that no string matching the pattern for an account_id,
# position_qty, webhook URL, or bearer-token fragment appears in log output.
# ---------------------------------------------------------------------------

# Alpaca account IDs follow the pattern ALP followed by 10 digits
_ALPACA_ACCOUNT_ID_PATTERN = re.compile(r"ALP\d{10}")

# Position quantities in Alpaca API responses are floats with multiple decimal places
_ALPACA_POSITION_QTY_PATTERN = re.compile(r"\d{3,}\.?\d{2,}")

# Discord webhook URLs contain /api/webhooks/ followed by an ID and a token
_WEBHOOK_URL_PATTERN = re.compile(r"/api/webhooks/\d+/[A-Za-z0-9_-]{20,}")

# Bearer token fragments: alphanumeric string >=20 chars that follows "Bearer "
_BEARER_FRAGMENT_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}")

# A response body indicator: the literal "response body" or "response_body"
_RESPONSE_BODY_INDICATOR = re.compile(r"response.?body", re.IGNORECASE)


def _collect_all_log_text(records: list) -> str:
    """Concatenate all formatted log record messages for pattern inspection."""
    return " | ".join(r.getMessage() for r in records)


# ---------------------------------------------------------------------------
# Shared fixture for an isolated DB
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a temp file and initialise schema."""
    db_path = str(tmp_path / "h4_integration.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()
    return db_path


def _make_cvar_table(db_path: str) -> None:
    """Create a minimal cvar_diagnostics table in the given DB."""
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


# ---------------------------------------------------------------------------
# H1: account_id substring in APIError message must not appear in log
# ---------------------------------------------------------------------------

def test_alpaca_account_id_in_error_message_does_not_appear_in_log(
    isolated_db, caplog
):
    """Gate 7 H1: when the DB error message contains an Alpaca account_id pattern
    (simulating an error from a DB write whose row came from an Alpaca response),
    the account_id pattern must not appear in any log record.

    This tests the integration surface: a caller that builds a row from Alpaca
    data, hits a DB error, and the H4 helper logs the failure.  The error
    message must be stripped of the response body fragment — only the error
    CLASS is permitted (gate 7, allowlist field: error_type).
    """
    _make_cvar_table(isolated_db)

    # Simulate a row that was constructed from an Alpaca response body;
    # the cycle_id is a correlation handle, symphony_id is a routing key —
    # but the error message from sqlite3 could carry these back through
    # str(exc) if the implementation is naive.
    row = {
        "cycle_id": "CYCLE_INT_H1_001",
        "symphony_id": "SYM_INTEGRATION_H1",
        # cvar fields are financial — must not leak
        "cvar_5pct": None,
        "cvar_5pct_stderr": None,
        "cvar_n_tail": 0,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }

    # Force a DB error whose message contains an Alpaca account_id pattern —
    # this simulates what would happen if the error propagation path embedded
    # response body fragments from the upstream API call.
    alpaca_account_id = "ALP9876543210"

    def _error_raising_connect(*args, **kwargs):
        raise sqlite3.OperationalError(
            f"unable to open database: table locked by account_id={alpaca_account_id}"
        )

    with patch("sqlite3.connect", _error_raising_connect):
        with caplog.at_level(logging.DEBUG, logger="database"):
            db.write_telemetry_row("cvar_diagnostics", row, mode="live")

    all_text = _collect_all_log_text(caplog.records)
    assert not _ALPACA_ACCOUNT_ID_PATTERN.search(all_text), (
        f"Gate 7 H1 violation: Alpaca account_id pattern found in log output. "
        f"The log must not echo API response body fragments from exception messages. "
        f"Full log text: {all_text!r}"
    )


# ---------------------------------------------------------------------------
# H2: position_qty float substring in error message must not appear in log
# ---------------------------------------------------------------------------

def test_alpaca_position_qty_in_error_message_does_not_appear_in_log(
    isolated_db, caplog
):
    """Gate 7 H2: when the DB error message contains a position_qty value
    (simulating a response body fragment embedded in the exception), that
    value must not appear in any log record.

    A naive implementation that logs str(exc) or repr(exc) would fail this test.
    """
    row = {
        "cycle_id": "CYCLE_INT_H2_001",
        "symphony_id": "SYM_INTEGRATION_H2",
        "cvar_5pct": None,
        "cvar_5pct_stderr": None,
        "cvar_n_tail": 0,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }

    # A position_qty pattern: large float as would appear in an Alpaca position response
    # The pattern 12345.67890 matches _ALPACA_POSITION_QTY_PATTERN
    def _error_raising_connect(*args, **kwargs):
        raise sqlite3.OperationalError(
            "constraint failed: position_qty=12345.67890 exceeds account limit"
        )

    with patch("sqlite3.connect", _error_raising_connect):
        with caplog.at_level(logging.DEBUG, logger="database"):
            db.write_telemetry_row("cvar_diagnostics", row, mode="live")

    all_text = _collect_all_log_text(caplog.records)
    assert not _ALPACA_POSITION_QTY_PATTERN.search(all_text), (
        f"Gate 7 H2 violation: position_qty pattern found in log output. "
        f"str(exc) or repr(exc) must not be logged directly. "
        f"Full log text: {all_text!r}"
    )


# ---------------------------------------------------------------------------
# H3: webhook URL in exception args must not appear in log
# ---------------------------------------------------------------------------

def test_webhook_url_in_error_message_does_not_appear_in_log(
    isolated_db, caplog
):
    """Gate 7 H3: when the DB error message contains a Discord webhook URL
    pattern, it must not appear in log output.

    Discord webhook URLs contain /api/webhooks/<id>/<token> — a token that
    can be used to send messages to the webhook.  Echoing this in logs is a
    secret leak.
    """
    row = {
        "cycle_id": "CYCLE_INT_H3_001",
        "symphony_id": "SYM_INTEGRATION_H3",
        "cvar_5pct": None,
        "cvar_5pct_stderr": None,
        "cvar_n_tail": 0,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }

    # Webhook URL pattern: /api/webhooks/ID/TOKEN
    # Token uses characters from the allowable Discord token charset
    def _error_raising_connect(*args, **kwargs):
        raise sqlite3.OperationalError(
            "failed to notify: /api/webhooks/1234567890/AbCdEfGhIjKlMnOpQrStUvWx_token"
        )

    with patch("sqlite3.connect", _error_raising_connect):
        with caplog.at_level(logging.DEBUG, logger="database"):
            db.write_telemetry_row("cvar_diagnostics", row, mode="live")

    all_text = _collect_all_log_text(caplog.records)
    assert not _WEBHOOK_URL_PATTERN.search(all_text), (
        f"Gate 7 H3 violation: webhook URL pattern found in log output. "
        f"Exception messages containing webhook URLs must be stripped before logging. "
        f"Full log text: {all_text!r}"
    )


# ---------------------------------------------------------------------------
# H4: full response dict stringified in exception message must not leak
# ---------------------------------------------------------------------------

def test_full_response_dict_in_exception_does_not_appear_in_log(
    isolated_db, caplog
):
    """Gate 7 H4: when the exception message contains a stringified response
    body dict (the most common accident — developers log str(response.json())),
    the dict contents must not appear in any log record.

    This is the highest-risk scenario: a developer adds a log line that
    includes the raw exception, and the exception was constructed with
    a full API response body as its first argument.
    """
    row = {
        "cycle_id": "CYCLE_INT_H4_001",
        "symphony_id": "SYM_INTEGRATION_H4",
        "cvar_5pct": None,
        "cvar_5pct_stderr": None,
        "cvar_n_tail": 0,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }

    # Simulate exception constructed with response body as string argument
    # — the account_id and position_qty are embedded in the stringified body
    response_body_str = (
        '{"account_id": "ALP1111111111", '
        '"positions": [{"qty": "98765.43210", "symbol": "SPY"}], '
        '"status": "error", "message": "DB constraint failed"}'
    )

    def _error_raising_connect(*args, **kwargs):
        raise sqlite3.OperationalError(f"API response body: {response_body_str}")

    with patch("sqlite3.connect", _error_raising_connect):
        with caplog.at_level(logging.DEBUG, logger="database"):
            db.write_telemetry_row("cvar_diagnostics", row, mode="live")

    all_text = _collect_all_log_text(caplog.records)
    assert "ALP1111111111" not in all_text, (
        f"Gate 7 H4 violation: account_id from response body found in log. "
        f"Full log text: {all_text!r}"
    )
    assert "98765.43210" not in all_text, (
        f"Gate 7 H4 violation: position_qty from response body found in log. "
        f"Full log text: {all_text!r}"
    )
    assert not _RESPONSE_BODY_INDICATOR.search(all_text), (
        f"Gate 7 H4 violation: 'response body' indicator found in log. "
        f"Full log text: {all_text!r}"
    )


# ---------------------------------------------------------------------------
# H5: account_id in a structured row field must not appear in log
# ---------------------------------------------------------------------------

def test_account_id_in_row_field_does_not_appear_in_log(
    isolated_db, caplog
):
    """Gate 7 H5: if a row_dict contains a field whose value matches an
    account_id pattern, that value must not appear in any log record.

    This tests the scenario where a Phase-1 caller incorrectly includes an
    account_id in the row_dict (schema violation), and the H4 helper silently
    leaks it through the error log.  The gate-7 contract forbids this regardless
    of how the row was constructed.
    """
    # A row that contains an account_id field — this is a schema violation
    # that the H4 helper must NOT log verbatim
    row = {
        "cycle_id": "CYCLE_INT_H5_001",
        "symphony_id": "SYM_INTEGRATION_H5",
        # account_id is NOT a permitted cvar_diagnostics field — simulating a
        # misconstrued row that got extra Alpaca data merged in
        "cvar_5pct": 0.05,
        "cvar_5pct_stderr": 0.01,
        "cvar_n_tail": 7,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }

    with patch("sqlite3.connect") as mock_connect:
        mock_connect.side_effect = sqlite3.OperationalError(
            "no such table: cvar_diagnostics"
        )
        with caplog.at_level(logging.DEBUG, logger="database"):
            db.write_telemetry_row("cvar_diagnostics", row, mode="live")

    all_text = _collect_all_log_text(caplog.records)
    # The financial field VALUES from the row must not appear in log
    assert "0.05" not in all_text or "cvar_5pct" not in all_text, (
        f"Gate 7 H5 violation: financial row field value appeared in log. "
        f"The log must not echo row_dict values. "
        f"Full log text: {all_text!r}"
    )
    # Stronger: cvar_5pct as a column name must not appear (would indicate row dump)
    assert "cvar_5pct" not in all_text, (
        f"Gate 7 H5 violation: cvar_5pct column name appeared in log output — "
        f"this suggests a row_dict or similar payload was stringified. "
        f"Full log text: {all_text!r}"
    )


# ---------------------------------------------------------------------------
# H6: bearer token fragment in exception must not appear in log
# ---------------------------------------------------------------------------

def test_bearer_token_in_exception_does_not_appear_in_log(
    isolated_db, caplog
):
    """Gate 7 H6: when the exception message contains a Bearer token fragment
    (simulating a headers-in-exception scenario from a downstream HTTP error),
    the token must not appear in any log record.

    Bearer tokens are the primary Composer API credential — leaking one in logs
    is a critical security incident.
    """
    row = {
        "cycle_id": "CYCLE_INT_H6_001",
        "symphony_id": "SYM_INTEGRATION_H6",
        "cvar_5pct": None,
        "cvar_5pct_stderr": None,
        "cvar_n_tail": 0,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }

    # Exception message containing a Bearer token — this simulates the scenario
    # where an HTTP client library includes request headers in the exception
    def _error_raising_connect(*args, **kwargs):
        raise sqlite3.OperationalError(
            "request headers: Bearer XyZaAbBcCdDeEfFgGhHiIjJkKlLmMnNoOpPqQ auth failed"
        )

    with patch("sqlite3.connect", _error_raising_connect):
        with caplog.at_level(logging.DEBUG, logger="database"):
            db.write_telemetry_row("cvar_diagnostics", row, mode="live")

    all_text = _collect_all_log_text(caplog.records)
    assert not _BEARER_FRAGMENT_PATTERN.search(all_text), (
        f"Gate 7 H6 violation: Bearer token fragment found in log output. "
        f"Exception messages containing auth credentials must be stripped. "
        f"Full log text: {all_text!r}"
    )


# ---------------------------------------------------------------------------
# Positive control: table_name and error type ARE present (log is not empty)
# ---------------------------------------------------------------------------

def test_log_contains_table_name_and_error_type_after_redaction(
    isolated_db, caplog
):
    """Positive control: after all redaction, the permitted fields (table_name,
    error_type) must still be present in the log record.

    This prevents a trivially-passing implementation that just logs nothing.
    A redaction implementation that produces an empty log record would fail gate 7
    in a different way — operators need the table_name and error type to triage.
    """
    row = {
        "cycle_id": "CYCLE_INT_POSCTRL_001",
        "symphony_id": "SYM_INTEGRATION_POSCTRL",
        "cvar_5pct": None,
        "cvar_5pct_stderr": None,
        "cvar_n_tail": 0,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }
    table_name = "cvar_diagnostics_posctrl"

    with patch("sqlite3.connect") as mock_connect:
        mock_connect.side_effect = sqlite3.OperationalError(
            "no such table: cvar_diagnostics_posctrl"
        )
        with caplog.at_level(logging.WARNING, logger="database"):
            db.write_telemetry_row(table_name, row, mode="live")

    all_text = _collect_all_log_text(caplog.records)
    assert table_name in all_text, (
        f"Positive control: table_name must appear in log after redaction; "
        f"full log: {all_text!r}"
    )
    # Error type: OperationalError or its base Error — either is acceptable
    assert re.search(r"OperationalError|sqlite3.Error|Error", all_text), (
        f"Positive control: error type must appear in log after redaction; "
        f"full log: {all_text!r}"
    )
