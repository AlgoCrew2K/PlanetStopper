"""
RED integration tests — Composer/Alpaca client wrappers: response body fields
must not appear in stdout, stderr, or any log record at any level.

Overlay gate 7 integration surface (logging-redaction plan §Deliverables 4):
  For any Composer or Alpaca client wrapper in alpha_bot_execution.py,
  response-body fields (account_id pattern, position_qty pattern, webhook
  URL pattern) must NEVER appear in stdout/stderr on the error path.

Binding references:
  - feature-plans/decision-science/phase-1/logging-redaction/plan.md §Deliverables (4)
  - council-attack-rubric.md gate 7 (no Composer/Alpaca response-body verbatim)
  - tests/fixtures/log-redaction-allowlist.txt

Fixture provenance:
  Mock HTTP responses constructed to simulate Composer and Alpaca API response
  shapes.  Account IDs, position quantities, and webhook URLs in these fixtures
  match the CLASS of data returned by the real API, not real credentials.
  Patterns are regexes — tests assert absence of class matches, not specific values.

Hostile scenarios covered:
  S1. fetch_symphony_stats prints account_id in its error path
  S2. fetch_symphony_stats prints raw exception message (which may include
      request headers with bearer token)
  S3. execute_sell_to_cash prints account_id via str(e) on RequestException
  S4. execute_sell_to_cash prints response.text / response.json() on 4xx/5xx
  S5. fetch_alpaca_history prints response body on JSON parse failure
  S6. get_composer_headers return value (containing bearer token) does not
      appear in print output on error

Note on print vs logging:
  The existing client wrappers use print() rather than logging.  Gate 7 applies
  to ALL operator-visible output — stdout and stderr are operator-visible.
  These tests capture stdout via capsys; any secret-class match is a violation.
  The redaction requirement applies equally to print() and logging.warning() etc.
"""

from __future__ import annotations

import re
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
import requests

import alpha_bot_execution as abe

# ---------------------------------------------------------------------------
# Secret-class patterns
# ---------------------------------------------------------------------------

# Composer/Alpaca account IDs: UUID format or ALP followed by digits
_ACCOUNT_ID_PATTERN = re.compile(
    r"ALP\d{8,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

# Position dollar values: amounts with fractional cents, 4+ digits before decimal
_POSITION_QTY_PATTERN = re.compile(r"\d{4,}\.\d{2,}")

# Discord webhook URL pattern
_WEBHOOK_URL_PATTERN = re.compile(r"/api/webhooks/\d+/[A-Za-z0-9_-]{20,}")

# Bearer token: "Bearer " followed by long alphanumeric string
_BEARER_FRAGMENT_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}")

# Any API key / secret value: long alphanumeric strings that look like API tokens
_API_KEY_PATTERN = re.compile(r"[A-Za-z0-9]{32,}")


def _capture_stdout_stderr_and_logs(caplog, func, *args, **kwargs):
    """Run func(*args, **kwargs), capture stdout+stderr+logs, return (stdout, stderr, log_text)."""
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = StringIO()
    sys.stderr = StringIO()
    try:
        import logging
        func(*args, **kwargs)
        stdout_text = sys.stdout.getvalue()
        stderr_text = sys.stderr.getvalue()
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    log_text = " | ".join(r.getMessage() for r in caplog.records)
    return stdout_text, stderr_text, log_text


def _all_output(stdout: str, stderr: str, log_text: str) -> str:
    """Concatenate all operator-visible output for pattern inspection."""
    return f"{stdout} | {stderr} | {log_text}"


# ---------------------------------------------------------------------------
# S1: fetch_symphony_stats error path must not print account_id
# ---------------------------------------------------------------------------

def test_fetch_symphony_stats_error_does_not_print_account_id(capsys, caplog):
    """Gate 7 S1: the fetch_symphony_stats error path must not print the
    account_id parameter to stdout or stderr.

    Currently fetch_symphony_stats calls:
        print(f"Error fetching account {account_id}: HTTP {response.status_code}")

    This leaks the account_id in plain text to stdout.  Gate 7 forbids any
    operator-visible output that contains an account identifier.

    The account_id supplied here matches the Alpaca account ID class pattern
    (ALP + digits).  Any match on the pattern in captured output is a violation.
    """
    test_account_id = "ALP9988776655"  # Alpaca-format account ID — must NOT appear in output

    # Mock a 403 response — triggers the error print path in fetch_symphony_stats
    mock_response = MagicMock()
    mock_response.status_code = 403

    import logging
    with patch("requests.get", return_value=mock_response):
        with caplog.at_level(logging.DEBUG):
            abe.fetch_symphony_stats(test_account_id)

    captured = capsys.readouterr()
    all_output = _all_output(captured.out, captured.err, " | ".join(r.getMessage() for r in caplog.records))

    assert test_account_id not in all_output, (
        f"Gate 7 S1 violation: account_id '{test_account_id}' found in operator output. "
        f"fetch_symphony_stats must not echo account_id on error. "
        f"Full output: {all_output!r}"
    )
    assert not _ACCOUNT_ID_PATTERN.search(all_output), (
        f"Gate 7 S1 violation: account_id pattern found in operator output. "
        f"Full output: {all_output!r}"
    )


# ---------------------------------------------------------------------------
# S2: fetch_symphony_stats RequestException must not print exception message
#     when exception contains a bearer token
# ---------------------------------------------------------------------------

def test_fetch_symphony_stats_exception_with_bearer_does_not_print_token(capsys, caplog):
    """Gate 7 S2: when a RequestException message contains a Bearer token
    (e.g., from request headers echoed in an HTTP client error), the token
    must not appear in stdout/stderr.

    Currently fetch_symphony_stats calls:
        print(f"Exception fetching account {account_id}: {e}")

    Printing str(e) where e's args include authorization headers is a
    bearer-token leak.
    """
    test_account_id = "ACCT_BEARER_TEST"
    exception_with_token = requests.RequestException(
        "Request failed with auth=Bearer AbCdEfGhIjKlMnOpQrStUvWxYz1234567890"
    )

    import logging
    with patch("requests.get", side_effect=exception_with_token):
        with caplog.at_level(logging.DEBUG):
            abe.fetch_symphony_stats(test_account_id)

    captured = capsys.readouterr()
    all_output = _all_output(captured.out, captured.err, " | ".join(r.getMessage() for r in caplog.records))

    assert not _BEARER_FRAGMENT_PATTERN.search(all_output), (
        f"Gate 7 S2 violation: Bearer token fragment found in operator output. "
        f"Exception messages with auth credentials must be stripped. "
        f"Full output: {all_output!r}"
    )


# ---------------------------------------------------------------------------
# S3: execute_sell_to_cash must not print account_id via str(e) on exception
# ---------------------------------------------------------------------------

def test_execute_sell_to_cash_exception_does_not_print_account_id(capsys, caplog):
    """Gate 7 S3: execute_sell_to_cash must not print the account_id in its
    exception handler output.

    The current implementation constructs the URL with account_id embedded
    and on RequestException prints:
        print(f"     !!! [API CRASH]: {str(e)}")

    If str(e) contains the URL (which includes account_id), this leaks it.
    Additionally the Composer URL pattern contains the account_id directly
    in the path, so any URL echo is an account_id leak.
    """
    test_account_id = "ALP5544332211"  # Alpaca-format
    test_symphony_id = "SYM_SELLTEST_001"

    # Exception whose str() contains the account_id (via URL in the request)
    def _exception_with_url(*args, **kwargs):
        raise requests.RequestException(
            f"Connection failed for URL https://api.composer.trade/api/v0.1/"
            f"deploy/accounts/{test_account_id}/symphonies/{test_symphony_id}/go-to-cash"
        )

    import logging
    with patch("requests.post", side_effect=_exception_with_url):
        with patch("time.sleep"):  # prevent actual sleeps in retry loop
            with caplog.at_level(logging.DEBUG):
                abe.execute_sell_to_cash(test_symphony_id, test_account_id)

    captured = capsys.readouterr()
    all_output = _all_output(captured.out, captured.err, " | ".join(r.getMessage() for r in caplog.records))

    assert test_account_id not in all_output, (
        f"Gate 7 S3 violation: account_id '{test_account_id}' found in operator output. "
        f"execute_sell_to_cash must not echo account_id via str(exception). "
        f"Full output: {all_output!r}"
    )
    assert not _ACCOUNT_ID_PATTERN.search(all_output), (
        f"Gate 7 S3 violation: account_id pattern found in operator output. "
        f"Full output: {all_output!r}"
    )


# ---------------------------------------------------------------------------
# S4: execute_sell_to_cash 4xx response must not echo response body
# ---------------------------------------------------------------------------

def test_execute_sell_to_cash_4xx_response_body_does_not_appear_in_output(capsys, caplog):
    """Gate 7 S4: a 4xx response from Composer must not cause the response
    body (which may contain account IDs, positions, or access tokens) to appear
    in operator output.

    A naive implementation might call response.text or response.json() and
    print it for debugging — this is the most common accidental leak pattern.
    """
    test_account_id = "ALP1122334455"
    test_symphony_id = "SYM_4XX_TEST"

    # Simulate a 401 response with a body containing account data
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = (
        '{"error": "Unauthorized", "account_id": "ALP1122334455", '
        '"detail": "Token invalid for this account"}'
    )
    mock_response.json.return_value = {
        "error": "Unauthorized",
        "account_id": "ALP1122334455",
        "detail": "Token invalid for this account",
    }

    import logging
    with patch("requests.post", return_value=mock_response):
        with patch("time.sleep"):
            with caplog.at_level(logging.DEBUG):
                abe.execute_sell_to_cash(test_symphony_id, test_account_id)

    captured = capsys.readouterr()
    all_output = _all_output(captured.out, captured.err, " | ".join(r.getMessage() for r in caplog.records))

    # The response body account_id must not appear
    assert "ALP1122334455" not in all_output, (
        f"Gate 7 S4 violation: account_id from response body found in operator output. "
        f"4xx responses must not be echoed verbatim. "
        f"Full output: {all_output!r}"
    )
    # The response body text itself must not appear verbatim
    assert '"account_id"' not in all_output, (
        f"Gate 7 S4 violation: response body JSON key 'account_id' found in output — "
        f"suggests response.text or str(response.json()) was printed. "
        f"Full output: {all_output!r}"
    )


# ---------------------------------------------------------------------------
# S5: fetch_alpaca_history JSON parse failure must not print response body
# ---------------------------------------------------------------------------

def test_fetch_alpaca_history_json_parse_error_does_not_print_response_body(capsys, caplog):
    """Gate 7 S5: when fetch_alpaca_history receives a response whose JSON
    cannot be parsed, the raw response content must not appear in output.

    The current implementation prints:
        print(f"Error parsing Alpaca response JSON: HTTP {status} - {e}")

    If the ValueError (JSONDecodeError) includes the raw response text in its
    message (as Python's json module sometimes does for truncated inputs),
    this path leaks the response body.
    """
    # Simulate a response that parses as HTTP 200 but has a body containing
    # account data that is mangled JSON — json.loads will raise ValueError
    # and the ValueError message may include the raw body fragment
    account_id_in_body = "ALP7766554433"
    raw_body_with_account = (
        f'{{"bars": {{"SPY": []}}, "account_id": "{account_id_in_body}", '
        f'"position_qty": "99999.99"'
        # Intentionally missing closing brace — malformed JSON
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.side_effect = ValueError(
        f"Expecting '}}' delimiter: line 1 column 200 (char 199) in: {raw_body_with_account}"
    )

    import logging
    with patch("requests.get", return_value=mock_response):
        with caplog.at_level(logging.DEBUG):
            # fetch_alpaca_history requires tickers and a date string
            abe.fetch_alpaca_history(["SPY"], "2026-05-24")

    captured = capsys.readouterr()
    all_output = _all_output(captured.out, captured.err, " | ".join(r.getMessage() for r in caplog.records))

    assert account_id_in_body not in all_output, (
        f"Gate 7 S5 violation: account_id from Alpaca response body found in output. "
        f"JSON parse error message must not echo the response body. "
        f"Full output: {all_output!r}"
    )
    assert "99999.99" not in all_output, (
        f"Gate 7 S5 violation: position_qty from Alpaca response body found in output. "
        f"Full output: {all_output!r}"
    )


# ---------------------------------------------------------------------------
# S6: get_composer_headers bearer token must not appear in any error output
# ---------------------------------------------------------------------------

def test_get_composer_headers_bearer_token_does_not_leak_on_request_exception(capsys, caplog):
    """Gate 7 S6: the bearer token in get_composer_headers() must not appear
    in operator output when a requests exception carries the request headers.

    Some HTTP client implementations include request headers (including
    Authorization: Bearer <token>) in the exception message.  If the
    exception str() is printed, the token leaks.

    This test injects a fake COMPOSER_SECRET and asserts that even if an
    exception is constructed with the authorization header, the secret value
    does not appear in operator output.
    """
    fake_secret = "FAKESECRET_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"  # distinctive, != real secret
    fake_key = "FAKEKEY_ABCDEF"

    # Inject fake credentials via the module globals
    original_secret = abe.COMPOSER_SECRET
    original_key = abe.COMPOSER_KEY_ID
    abe.COMPOSER_SECRET = fake_secret
    abe.COMPOSER_KEY_ID = fake_key

    exception_with_headers = requests.RequestException(
        f"Connection failed: Authorization header was Bearer {fake_secret}"
    )

    import logging
    try:
        with patch("requests.get", side_effect=exception_with_headers):
            with caplog.at_level(logging.DEBUG):
                abe.fetch_symphony_stats("ACCT_S6_TEST")
    finally:
        abe.COMPOSER_SECRET = original_secret
        abe.COMPOSER_KEY_ID = original_key

    captured = capsys.readouterr()
    all_output = _all_output(captured.out, captured.err, " | ".join(r.getMessage() for r in caplog.records))

    assert fake_secret not in all_output, (
        f"Gate 7 S6 violation: COMPOSER_SECRET value found in operator output. "
        f"Bearer tokens must never appear in any operator-visible output. "
        f"Full output: {all_output!r}"
    )
    assert not _BEARER_FRAGMENT_PATTERN.search(all_output), (
        f"Gate 7 S6 violation: Bearer token pattern found in operator output. "
        f"Full output: {all_output!r}"
    )


# ---------------------------------------------------------------------------
# S7: fetch_symphony_stats Composer JSON parse error must not print exception
#     message (same gap as S5 for Alpaca, now closed for Composer path)
# ---------------------------------------------------------------------------

def test_fetch_symphony_stats_json_parse_error_does_not_print_exception_message(capsys, caplog):
    """Gate 7 S7 (REVISE): when fetch_symphony_stats receives a 200 response
    whose JSON cannot be parsed, the ValueError (JSONDecodeError) message must
    not appear in operator output.

    alpha_bot_execution.py:140 currently prints:
        print(f"Error parsing Composer response JSON: HTTP {response.status_code} - {e}")

    Python's json.JSONDecodeError.__str__() includes a fragment of the input
    document.  If the Composer response body contained account_id, position
    values, or bearer token fragments, those appear in the exception string
    and thus in stdout.

    This test was identified as a gap in the REVISE pass (S5 covered only the
    Alpaca JSON parse path at line 312; S7 covers the Composer path at line 140).
    """
    account_id_in_body = "a1b2c3d4-e5f6-7890-abcd-ef9988776655"  # Composer UUID format
    raw_body_with_account = (
        f'{{"symphonies": [], "account_id": "{account_id_in_body}", '
        f'"balance": "54321.99"'
        # intentionally malformed — missing closing brace
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.side_effect = ValueError(
        f"Expecting '}}' delimiter: line 1 column 180 (char 179) in: {raw_body_with_account}"
    )

    import logging
    with patch("requests.get", return_value=mock_response):
        with caplog.at_level(logging.DEBUG):
            abe.fetch_symphony_stats("ACCT_S7_TEST")

    captured = capsys.readouterr()
    all_output = _all_output(captured.out, captured.err, " | ".join(r.getMessage() for r in caplog.records))

    assert account_id_in_body not in all_output, (
        f"Gate 7 S7 violation: Composer account UUID from response body found in output. "
        f"fetch_symphony_stats JSON parse error must not echo the exception message. "
        f"Full output: {all_output!r}"
    )
    assert "54321.99" not in all_output, (
        f"Gate 7 S7 violation: balance value from Composer response body found in output. "
        f"Full output: {all_output!r}"
    )
