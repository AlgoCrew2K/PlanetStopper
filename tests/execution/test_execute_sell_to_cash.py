"""
Regression-pinning suite for ``alpha_bot_execution.execute_sell_to_cash``.

This is the money-mover: it POSTs to Composer's go-to-cash endpoint when Guard
Alpha triggers. This file pins its retry / backoff / rate-limit / secrets-hygiene
contract against the CURRENT in-code behaviour as of branch
``task-a1-execute-sell-to-cash`` (forked from main @ f9dca33).

Contract source of truth: the function body itself
(``alpha_bot_execution.py`` lines ~98-135). Backoff intervals, status-code
branches, and log-format are READ FROM CODE — not hardcoded aspirationally.
Tests are intentionally REGRESSION pins; they should pass against the current
implementation and FAIL only if a future change drifts from this contract.

Contract discrepancies between the brief and the actual code
-------------------------------------------------------------
- Brief (item 6) says ``requests.RequestException`` returns ``False`` flat.
  Code actually RETRIES on network exceptions with backoff ``[1, 2, 4, 10]``
  for attempts 0..3, then returns ``False`` on attempt 4. Tests pin the
  retry-with-backoff behaviour (regression spec).
- Brief implies 429 retries are capped. Code has NO cap on 429 — it
  ``continue``s without an ``attempt < len(backoff_intervals)`` guard. Five
  consecutive 429s exit the ``for`` loop and the final ``return False``
  fires. Tests pin this.
- Brief (item 4) says "4 consecutive 5xx → return False after 4 failures".
  Code actually loops ``range(5)`` (4 backoffs + 1 final un-slept attempt).
  Four backoffs ``[1, 2, 4, 10]`` are slept on attempts 0..3; on attempt 4
  the 5xx branch's ``attempt < len(backoff_intervals)`` is FALSE so it falls
  through to the generic "rejected" path and returns ``False``. So the
  function does FIVE HTTP calls for sustained 5xx, not four. Tests pin five.

All mocking is on ``requests.post`` and ``time.sleep`` (and ``builtins.print``
for secrets-hygiene assertions). No real network. No real sleeps. No
``--include-live`` markers — this suite is unconditional.
"""

import io
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import pytest
import requests

import alpha_bot_execution


# Backoff sequence is read from the function body (a named constant in scope).
# We import it indirectly by assertion against the live module's behaviour
# rather than redefining a literal — the test pins THE function's constant.
EXPECTED_BACKOFF_SEQUENCE = [1, 2, 4, 10]

# Per-call default sleep that fires after success (200/201/202). Pinned from
# the function body's ``time.sleep(0.5)`` on the success branch.
EXPECTED_POST_SUCCESS_SLEEP = 0.5

# Per-call default sleep that fires after a non-retriable rejection (4xx).
# Pinned from the function body's ``time.sleep(1.5)`` on the reject branch.
EXPECTED_POST_REJECT_SLEEP = 1.5

# Default ``Retry-After`` when the header is missing on a 429. Pinned from
# the function body's ``int(response.headers.get("Retry-After", 60))``.
EXPECTED_DEFAULT_RETRY_AFTER = 60


def _make_response(status_code: int, headers=None, text="MOCK_RESPONSE_BODY_DO_NOT_LOG"):
    """Build a MagicMock that quacks like a ``requests.Response``.

    The ``text`` default is a sentinel used by the secrets-hygiene tests:
    it must NEVER appear in captured stdout.
    """
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.text = text
    return resp


@pytest.fixture
def sym_and_account():
    """Arbitrary, shape-only inputs. The function under test never inspects
    them beyond URL formatting, so any non-empty strings work."""
    return ("sym-abc-123", "acct-xyz-789")


# -----------------------------------------------------------------------------
# 1. Happy path — 200 on first call
# -----------------------------------------------------------------------------
class TestHappyPath:
    def test_http_200_first_call_returns_true_and_posts_once(self, sym_and_account):
        sym, acct = sym_and_account
        with (
            patch.object(alpha_bot_execution.requests, "post") as mock_post,
            patch.object(alpha_bot_execution.time, "sleep") as mock_sleep,
        ):
            mock_post.return_value = _make_response(200)
            result = alpha_bot_execution.execute_sell_to_cash(sym, acct)
        assert result is True
        assert mock_post.call_count == 1
        # Success branch sleeps once for the post-success cooldown.
        mock_sleep.assert_called_once_with(EXPECTED_POST_SUCCESS_SLEEP)

    @pytest.mark.parametrize("ok_status", [200, 201, 202])
    def test_2xx_family_treated_as_success(self, sym_and_account, ok_status):
        """Pins that 201 and 202 are treated as success, not only 200."""
        sym, acct = sym_and_account
        with (
            patch.object(alpha_bot_execution.requests, "post") as mock_post,
            patch.object(alpha_bot_execution.time, "sleep"),
        ):
            mock_post.return_value = _make_response(ok_status)
            assert alpha_bot_execution.execute_sell_to_cash(sym, acct) is True
            assert mock_post.call_count == 1

    def test_posts_to_composer_go_to_cash_url(self, sym_and_account):
        """URL shape regression: ``/deploy/accounts/{acct}/symphonies/{sym}/go-to-cash``."""
        sym, acct = sym_and_account
        with (
            patch.object(alpha_bot_execution.requests, "post") as mock_post,
            patch.object(alpha_bot_execution.time, "sleep"),
        ):
            mock_post.return_value = _make_response(200)
            alpha_bot_execution.execute_sell_to_cash(sym, acct)
        called_url = mock_post.call_args[0][0]
        # Format-only assertions — no hardcoded base URL or path literal beyond
        # the producer-owned segments under test.
        assert f"/accounts/{acct}/" in called_url
        assert f"/symphonies/{sym}/" in called_url
        assert called_url.endswith("/go-to-cash")


# -----------------------------------------------------------------------------
# 2. 429 with Retry-After header — sleeps N seconds, then retries
# -----------------------------------------------------------------------------
class Test429RateLimit:
    def test_429_with_retry_after_sleeps_header_value_then_retries_to_success(
        self, sym_and_account
    ):
        sym, acct = sym_and_account
        retry_after_value = 7  # arbitrary; verified to be the slept value
        with (
            patch.object(alpha_bot_execution.requests, "post") as mock_post,
            patch.object(alpha_bot_execution.time, "sleep") as mock_sleep,
        ):
            mock_post.side_effect = [
                _make_response(429, headers={"Retry-After": str(retry_after_value)}),
                _make_response(200),
            ]
            result = alpha_bot_execution.execute_sell_to_cash(sym, acct)
        assert result is True
        assert mock_post.call_count == 2
        # The Retry-After value must have been the argument to a sleep call.
        slept_values = [c.args[0] for c in mock_sleep.call_args_list]
        assert retry_after_value in slept_values, (
            f"Expected sleep({retry_after_value}); saw {slept_values}"
        )

    def test_429_without_retry_after_falls_back_to_60s(self, sym_and_account):
        sym, acct = sym_and_account
        with (
            patch.object(alpha_bot_execution.requests, "post") as mock_post,
            patch.object(alpha_bot_execution.time, "sleep") as mock_sleep,
        ):
            mock_post.side_effect = [
                _make_response(429, headers={}),  # no Retry-After
                _make_response(200),
            ]
            result = alpha_bot_execution.execute_sell_to_cash(sym, acct)
        assert result is True
        slept_values = [c.args[0] for c in mock_sleep.call_args_list]
        assert EXPECTED_DEFAULT_RETRY_AFTER in slept_values, (
            f"Expected sleep({EXPECTED_DEFAULT_RETRY_AFTER}) fallback; saw {slept_values}"
        )

    def test_persistent_429_exits_loop_and_returns_false(self, sym_and_account):
        """REGRESSION PIN: 429 has NO retry cap in current code.

        The 429 branch ``continue``s without ``attempt < len(backoff_intervals)``
        guarding it. After ``range(5)`` exhausts, the trailing
        ``return False`` fires. Five HTTP calls total."""
        sym, acct = sym_and_account
        with (
            patch.object(alpha_bot_execution.requests, "post") as mock_post,
            patch.object(alpha_bot_execution.time, "sleep"),
        ):
            mock_post.return_value = _make_response(429, headers={"Retry-After": "1"})
            result = alpha_bot_execution.execute_sell_to_cash(sym, acct)
        assert result is False
        # Loop body is range(len(backoff_intervals) + 1) == range(5).
        assert mock_post.call_count == len(EXPECTED_BACKOFF_SEQUENCE) + 1


# -----------------------------------------------------------------------------
# 3. 5xx retry / backoff
# -----------------------------------------------------------------------------
class Test5xxRetryBackoff:
    def test_sustained_500_returns_false_after_full_backoff_sequence(self, sym_and_account):
        """REGRESSION PIN: sustained 500 → 5 POSTs total, 4 backoff sleeps in
        the exact sequence ``[1, 2, 4, 10]``. On the 5th attempt the 5xx
        branch is skipped (``attempt < 4`` is False), so it falls through to
        the rejection path (which sleeps 1.5 and returns False)."""
        sym, acct = sym_and_account
        with (
            patch.object(alpha_bot_execution.requests, "post") as mock_post,
            patch.object(alpha_bot_execution.time, "sleep") as mock_sleep,
        ):
            mock_post.return_value = _make_response(500)
            result = alpha_bot_execution.execute_sell_to_cash(sym, acct)

        assert result is False
        # 4 backoff attempts + 1 terminal attempt = 5 HTTP calls.
        assert mock_post.call_count == len(EXPECTED_BACKOFF_SEQUENCE) + 1
        # Extract the sleep sequence; the 5xx branch only slept the backoffs,
        # the terminal attempt sleeps the post-reject cooldown (1.5).
        slept = [c.args[0] for c in mock_sleep.call_args_list]
        # The exact backoff prefix must appear in order at the head of the
        # sleep sequence.
        assert slept[: len(EXPECTED_BACKOFF_SEQUENCE)] == EXPECTED_BACKOFF_SEQUENCE, (
            f"Backoff regression: expected prefix {EXPECTED_BACKOFF_SEQUENCE}; got {slept}"
        )
        # Terminal "rejected" sleep follows the backoff prefix.
        assert slept[-1] == EXPECTED_POST_REJECT_SLEEP

    @pytest.mark.parametrize("server_status", [500, 502, 503, 504])
    def test_5xx_family_triggers_retry(self, sym_and_account, server_status):
        """Pins that the 5xx branch uses ``status_code >= 500`` (no whitelist).
        Mixed sequences should recover when the next call succeeds."""
        sym, acct = sym_and_account
        with (
            patch.object(alpha_bot_execution.requests, "post") as mock_post,
            patch.object(alpha_bot_execution.time, "sleep") as mock_sleep,
        ):
            mock_post.side_effect = [
                _make_response(server_status),
                _make_response(200),
            ]
            result = alpha_bot_execution.execute_sell_to_cash(sym, acct)
        assert result is True
        assert mock_post.call_count == 2
        # First backoff is interval[0] == 1, then post-success sleep == 0.5.
        slept = [c.args[0] for c in mock_sleep.call_args_list]
        assert slept[0] == EXPECTED_BACKOFF_SEQUENCE[0]
        assert EXPECTED_POST_SUCCESS_SLEEP in slept

    def test_500_500_then_200_returns_true_with_backoff_1_then_2(self, sym_and_account):
        """Brief item 5: 500, 500, 200 → True; backoff [1, 2] honored."""
        sym, acct = sym_and_account
        with (
            patch.object(alpha_bot_execution.requests, "post") as mock_post,
            patch.object(alpha_bot_execution.time, "sleep") as mock_sleep,
        ):
            mock_post.side_effect = [
                _make_response(500),
                _make_response(500),
                _make_response(200),
            ]
            result = alpha_bot_execution.execute_sell_to_cash(sym, acct)

        assert result is True
        assert mock_post.call_count == 3
        slept = [c.args[0] for c in mock_sleep.call_args_list]
        # Backoffs [1, 2] must appear in order BEFORE the post-success sleep.
        assert slept[:2] == EXPECTED_BACKOFF_SEQUENCE[:2], (
            f"Expected backoff prefix {EXPECTED_BACKOFF_SEQUENCE[:2]}; got {slept}"
        )
        assert slept[-1] == EXPECTED_POST_SUCCESS_SLEEP


# -----------------------------------------------------------------------------
# 4. Network exception (requests.RequestException)
# -----------------------------------------------------------------------------
class TestNetworkException:
    def test_persistent_network_exception_returns_false_without_crashing(self, sym_and_account):
        """Brief item 6: every attempt raises ``requests.RequestException`` →
        ``False``, no crash. REGRESSION PIN (vs. brief): the function actually
        RETRIES with backoff before giving up — 5 attempts total."""
        sym, acct = sym_and_account
        with (
            patch.object(alpha_bot_execution.requests, "post") as mock_post,
            patch.object(alpha_bot_execution.time, "sleep") as mock_sleep,
        ):
            mock_post.side_effect = requests.RequestException("boom")
            # Must not raise.
            result = alpha_bot_execution.execute_sell_to_cash(sym, acct)
        assert result is False
        assert mock_post.call_count == len(EXPECTED_BACKOFF_SEQUENCE) + 1
        slept = [c.args[0] for c in mock_sleep.call_args_list]
        # The exception branch sleeps the backoff intervals on attempts 0..3
        # (4 sleeps total — no terminal cooldown on the 5th, the exception
        # branch's else returns False directly).
        assert slept == EXPECTED_BACKOFF_SEQUENCE, (
            f"Network-exception backoff regression: expected {EXPECTED_BACKOFF_SEQUENCE}; got {slept}"
        )

    def test_transient_network_exception_then_success_returns_true(self, sym_and_account):
        sym, acct = sym_and_account
        with (
            patch.object(alpha_bot_execution.requests, "post") as mock_post,
            patch.object(alpha_bot_execution.time, "sleep"),
        ):
            mock_post.side_effect = [
                requests.RequestException("transient"),
                _make_response(200),
            ]
            result = alpha_bot_execution.execute_sell_to_cash(sym, acct)
        assert result is True
        assert mock_post.call_count == 2


# -----------------------------------------------------------------------------
# 5. Non-retriable 4xx — immediate False
# -----------------------------------------------------------------------------
class TestNonRetriable4xx:
    @pytest.mark.parametrize("client_status", [400, 401, 403, 404, 415])
    def test_4xx_returns_false_after_single_call(self, sym_and_account, client_status):
        """REGRESSION PIN: non-retriable 4xx fall through both the 429 branch
        and the 5xx branch and hit the generic "rejected" return. One POST,
        one post-reject cooldown sleep, return False."""
        sym, acct = sym_and_account
        with (
            patch.object(alpha_bot_execution.requests, "post") as mock_post,
            patch.object(alpha_bot_execution.time, "sleep") as mock_sleep,
        ):
            mock_post.return_value = _make_response(client_status)
            result = alpha_bot_execution.execute_sell_to_cash(sym, acct)
        assert result is False
        assert mock_post.call_count == 1
        # Exactly one sleep — the post-reject cooldown.
        mock_sleep.assert_called_once_with(EXPECTED_POST_REJECT_SLEEP)


# -----------------------------------------------------------------------------
# 6. Secrets hygiene (cycle #27 fix) — response.text never echoed; status is
# -----------------------------------------------------------------------------
class TestSecretsHygiene:
    SENTINEL_BODY = "SECRET_BODY_FROM_COMPOSER_DO_NOT_LEAK_42"

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 415, 500, 502, 503, 504])
    def test_response_text_never_appears_in_stdout(self, sym_and_account, status_code):
        """response.text must NEVER appear in captured stdout for any error
        branch. Pinned per cycle #27 secrets-hygiene fix."""
        sym, acct = sym_and_account
        buf = io.StringIO()
        with (
            patch.object(alpha_bot_execution.requests, "post") as mock_post,
            patch.object(alpha_bot_execution.time, "sleep"),
        ):
            mock_post.return_value = _make_response(status_code, text=self.SENTINEL_BODY)
            with redirect_stdout(buf):
                alpha_bot_execution.execute_sell_to_cash(sym, acct)
        captured = buf.getvalue()
        assert self.SENTINEL_BODY not in captured, (
            f"Secrets leak: response.text ({self.SENTINEL_BODY!r}) appeared in "
            f"stdout for status {status_code}. Captured: {captured!r}"
        )

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 415, 500, 502, 503, 504, 429])
    def test_status_code_does_appear_in_stdout(self, sym_and_account, status_code):
        """The status code itself must appear in stdout — operators need it
        for triage. This is the inverse of the secrets-hygiene assertion."""
        sym, acct = sym_and_account
        buf = io.StringIO()
        with (
            patch.object(alpha_bot_execution.requests, "post") as mock_post,
            patch.object(alpha_bot_execution.time, "sleep"),
        ):
            mock_post.return_value = _make_response(
                status_code, headers={"Retry-After": "1"} if status_code == 429 else None
            )
            with redirect_stdout(buf):
                alpha_bot_execution.execute_sell_to_cash(sym, acct)
        captured = buf.getvalue()
        assert str(status_code) in captured, (
            f"Status code {status_code} missing from stdout. Captured: {captured!r}"
        )

    def test_response_text_not_echoed_on_network_exception(self, sym_and_account):
        """Gate 7: on RequestException, only the exception CLASS NAME is surfaced.

        The exception message text is NOT printed — it may contain the full
        request URL (which includes account_id in the path) or auth headers
        with bearer tokens.  Gate-7 redaction (logging-redaction plan §S3)
        requires stripping str(e) entirely; only type(e).__name__ is permitted.

        Positive pin: 'RequestException' (the class name) DOES appear so
        operators can identify the failure class.
        Negative pin: the sentinel message text does NOT appear — confirms
        gate-7 redaction is applied and over-surfacing is prevented.
        """
        sym, acct = sym_and_account
        buf = io.StringIO()
        sentinel = "EXC_MSG_NOT_A_BODY"
        with (
            patch.object(alpha_bot_execution.requests, "post") as mock_post,
            patch.object(alpha_bot_execution.time, "sleep"),
        ):
            mock_post.side_effect = requests.RequestException(sentinel)
            with redirect_stdout(buf):
                result = alpha_bot_execution.execute_sell_to_cash(sym, acct)
        assert result is False
        captured = buf.getvalue()
        # Positive pin: exception class name must appear for operator triage.
        assert "RequestException" in captured, (
            f"Exception class name missing from stdout; operators need it. Captured: {captured!r}"
        )
        # Negative pin (gate 7): exception message text must NOT appear —
        # it may carry account_id via URL or bearer token via auth header.
        assert sentinel not in captured, (
            f"Gate 7 violation: exception message text found in stdout. "
            f"Only type(e).__name__ is permitted, not str(e). "
            f"Captured: {captured!r}"
        )
