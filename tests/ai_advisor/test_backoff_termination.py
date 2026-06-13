"""
Regression tests for ai_advisor._fetch_with_backoff infinite-loop / OOM bug.

RED contract (before fix): the 429 branch spun forever once delay collapsed
to 0.0, leaking connection objects until ~100 GB was committed.

GREEN contract (after fix): every path terminates within _FETCH_MAX_ATTEMPTS
calls to requests.get regardless of the server's response code.

Safety: all tests patch requests.get — no live network I/O ever.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, call, patch

import pytest
import requests as requests_lib


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resp(status_code: int) -> MagicMock:
    """Return a mock requests.Response with the given status_code."""
    resp = MagicMock(name=f"MockResponse_{status_code}")
    resp.status_code = status_code
    return resp


# ---------------------------------------------------------------------------
# 429 infinite-loop regression
# ---------------------------------------------------------------------------


def test_backoff_429_terminates_within_max_attempts():
    """_fetch_with_backoff MUST return after at most _FETCH_MAX_ATTEMPTS calls
    when the server replies 429 every time.

    Before the fix: delay collapsed to 0.0 and the loop span forever, leaking
    memory until the process was killed.  After the fix: the function returns
    the 429 response once the attempt ceiling or budget is reached.

    Asserts:
      (a) the call count is <= _FETCH_MAX_ATTEMPTS (bounded, not infinite),
      (b) the function returns a Response (or raises — but NOT hangs),
      (c) the returned status_code is 429 (caller is responsible for raise_for_status).
    """
    import ai_advisor

    call_count = 0

    def always_429(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _make_resp(429)

    # Patch time.sleep to avoid any real waits during this test — the test
    # verifies termination logic, not timing.
    with patch("ai_advisor.requests.get", side_effect=always_429), \
         patch("ai_advisor.time.sleep"):
        resp = ai_advisor._fetch_with_backoff("https://test.example/")

    assert call_count <= ai_advisor._FETCH_MAX_ATTEMPTS, (
        f"requests.get was called {call_count} times; must not exceed "
        f"_FETCH_MAX_ATTEMPTS ({ai_advisor._FETCH_MAX_ATTEMPTS}). "
        "Infinite-loop regression: delay collapsed to 0.0 after budget was "
        "spent, making can_retry True forever."
    )
    assert resp.status_code == 429, (
        "After exhausting retries the 429 response must be returned to the "
        "caller (caller runs raise_for_status() — that is the existing contract)."
    )


def test_backoff_429_call_count_is_strictly_positive():
    """Sanity check: at least one GET attempt must be made."""
    import ai_advisor

    call_count = 0

    def always_429(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _make_resp(429)

    with patch("ai_advisor.requests.get", side_effect=always_429), \
         patch("ai_advisor.time.sleep"):
        ai_advisor._fetch_with_backoff("https://test.example/")

    assert call_count >= 1, "At least one GET attempt must be made."


def test_backoff_connection_error_terminates_within_max_attempts():
    """_fetch_with_backoff MUST raise after at most _FETCH_MAX_ATTEMPTS calls
    when requests.get raises ConnectionError every time.

    Before the fix: the ``total_waited + delay > 8.0`` guard became
    ``8.0 > 8.0 = False`` when delay collapsed to 0.0 — so the function
    never raised and spun forever.

    After the fix: the attempt ceiling or ``delay > 0.0`` gate causes an
    early raise before the spin can begin.

    Asserts:
      (a) the call count is <= _FETCH_MAX_ATTEMPTS,
      (b) the function raises requests.exceptions.ConnectionError (re-raised
          from the last attempt — callers wrap in try/except in each producer).
    """
    import ai_advisor

    call_count = 0

    def always_conn_error(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise requests_lib.exceptions.ConnectionError("simulated connection refused")

    with patch("ai_advisor.requests.get", side_effect=always_conn_error), \
         patch("ai_advisor.time.sleep"):
        with pytest.raises(requests_lib.exceptions.ConnectionError):
            ai_advisor._fetch_with_backoff("https://test.example/")

    assert call_count <= ai_advisor._FETCH_MAX_ATTEMPTS, (
        f"requests.get was called {call_count} times on persistent ConnectionError; "
        f"must not exceed _FETCH_MAX_ATTEMPTS ({ai_advisor._FETCH_MAX_ATTEMPTS}). "
        "Infinite-loop regression: delay==0.0 after budget spent made 'not can_retry' "
        "False forever, preventing the raise."
    )


def test_backoff_timeout_error_terminates_within_max_attempts():
    """Same termination guarantee for requests.exceptions.Timeout."""
    import ai_advisor

    call_count = 0

    def always_timeout(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise requests_lib.exceptions.Timeout("simulated read timeout")

    with patch("ai_advisor.requests.get", side_effect=always_timeout), \
         patch("ai_advisor.time.sleep"):
        with pytest.raises(requests_lib.exceptions.Timeout):
            ai_advisor._fetch_with_backoff("https://test.example/")

    assert call_count <= ai_advisor._FETCH_MAX_ATTEMPTS, (
        f"requests.get was called {call_count} times on persistent Timeout; "
        f"must not exceed _FETCH_MAX_ATTEMPTS ({ai_advisor._FETCH_MAX_ATTEMPTS})."
    )


# ---------------------------------------------------------------------------
# Success / non-retry paths — preserved behaviour
# ---------------------------------------------------------------------------


def test_backoff_success_on_first_attempt_makes_exactly_one_call():
    """A 200 response on the first attempt: no retry, exactly one GET call."""
    import ai_advisor

    with patch("ai_advisor.requests.get", return_value=_make_resp(200)) as mock_get, \
         patch("ai_advisor.time.sleep") as mock_sleep:
        resp = ai_advisor._fetch_with_backoff("https://test.example/")

    mock_get.assert_called_once()
    mock_sleep.assert_not_called()
    assert resp.status_code == 200


def test_backoff_non_429_error_code_is_returned_immediately():
    """A non-429 non-200 response (e.g. 500) is returned immediately, no retry."""
    import ai_advisor

    with patch("ai_advisor.requests.get", return_value=_make_resp(500)) as mock_get, \
         patch("ai_advisor.time.sleep") as mock_sleep:
        resp = ai_advisor._fetch_with_backoff("https://test.example/")

    mock_get.assert_called_once()
    mock_sleep.assert_not_called()
    assert resp.status_code == 500


def test_backoff_429_then_200_retries_and_succeeds():
    """Server returns 429 once then 200: exactly two GET calls, one sleep."""
    import ai_advisor

    responses = iter([_make_resp(429), _make_resp(200)])

    with patch("ai_advisor.requests.get", side_effect=lambda *a, **kw: next(responses)) as mock_get, \
         patch("ai_advisor.time.sleep") as mock_sleep:
        resp = ai_advisor._fetch_with_backoff("https://test.example/")

    assert mock_get.call_count == 2, (
        f"Expected 2 GET calls (429 then 200), got {mock_get.call_count}"
    )
    mock_sleep.assert_called_once()
    assert resp.status_code == 200


def test_backoff_headers_and_params_forwarded():
    """headers and params keyword arguments are forwarded to requests.get."""
    import ai_advisor

    with patch("ai_advisor.requests.get", return_value=_make_resp(200)) as mock_get, \
         patch("ai_advisor.time.sleep"):
        ai_advisor._fetch_with_backoff(
            "https://test.example/",
            headers={"X-Test": "value"},
            params={"key": "val"},
        )

    mock_get.assert_called_once()
    _, kwargs = mock_get.call_args
    assert kwargs.get("headers") == {"X-Test": "value"}
    assert kwargs.get("params") == {"key": "val"}
    assert kwargs.get("timeout") == ai_advisor._FETCH_TIMEOUT_S, (
        "explicit timeout must always be passed to requests.get"
    )


# ---------------------------------------------------------------------------
# Constant contract
# ---------------------------------------------------------------------------


def test_fetch_max_attempts_constant_exists_and_is_positive_int():
    """_FETCH_MAX_ATTEMPTS must be a positive int named constant (no magic number)."""
    import ai_advisor

    assert hasattr(ai_advisor, "_FETCH_MAX_ATTEMPTS"), (
        "_FETCH_MAX_ATTEMPTS constant is missing from ai_advisor.py"
    )
    assert isinstance(ai_advisor._FETCH_MAX_ATTEMPTS, int), (
        "_FETCH_MAX_ATTEMPTS must be an int"
    )
    assert ai_advisor._FETCH_MAX_ATTEMPTS > 0, (
        "_FETCH_MAX_ATTEMPTS must be positive"
    )


def test_fetch_max_backoff_total_wait_constant_exists():
    """_FETCH_MAX_BACKOFF_TOTAL_WAIT_S must still be present (pre-existing contract)."""
    import ai_advisor

    assert hasattr(ai_advisor, "_FETCH_MAX_BACKOFF_TOTAL_WAIT_S")
    assert ai_advisor._FETCH_MAX_BACKOFF_TOTAL_WAIT_S == 8.0
