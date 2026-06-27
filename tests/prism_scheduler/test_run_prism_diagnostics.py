"""
Tests for prism_scheduler council subprocess failure diagnostics (DE-PRISM-DIAG-001).

When the council subprocess exits non-zero, _run_prism must log a structured
diagnostic block (returncode + stderr tail + stdout tail) to sys.stderr so
the next real failure is diagnosable via journald.  The security invariant is
that NO credential value may appear in that output — redaction is the
load-bearing control.

All tests mock subprocess.run.  No real `claude -p`, no network, no LLM spend.
DB_PATH is isolated by conftest.py before collection.

ACs exercised:
  AC-1  — non-zero exit logs [prism_scheduler] prefix + returncode
  AC-2  — stderr tail logged; (stderr empty) marker when empty
  AC-3  — stdout tail logged; (stdout empty) marker; tail truncation with marker
  AC-4  — credential redaction: live values + token-shaped patterns (sk-ant-, sk-, oat_)
  AC-5  — success path (rc=0) byte-for-byte unchanged; no diagnostic emitted
  AC-6  — subprocess.run raises → SubprocessError: <Type> only, no message/path
  AC-7  — _run_prism never raises even if _redact_secrets itself throws
  AC-8  — _STDERR_LOG_CAP and _STDOUT_LOG_CAP named module-level constants exist
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

import prism_scheduler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REDACTED = "***REDACTED***"


def _make_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Return a fake CompletedProcess-like mock."""
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# AC-8 — Named constants must exist before any other test runs
# ---------------------------------------------------------------------------


def test_stderr_cap_constant_exists_and_is_positive_int():
    """_STDERR_LOG_CAP must be a positive int module-level constant (AC-8).

    The truncation cap is a design decision (4000 chars = room for a full Python
    traceback without flooding journald); asserting type + positivity ensures no
    accidental reset to 0 or None.
    """
    assert hasattr(prism_scheduler, "_STDERR_LOG_CAP"), (
        "_STDERR_LOG_CAP constant is missing from prism_scheduler"
    )
    cap = prism_scheduler._STDERR_LOG_CAP
    assert isinstance(cap, int), f"_STDERR_LOG_CAP must be int, got {type(cap)}"
    assert cap > 0, f"_STDERR_LOG_CAP must be positive, got {cap}"


def test_stdout_cap_constant_exists_and_is_positive_int():
    """_STDOUT_LOG_CAP must be a positive int module-level constant (AC-8)."""
    assert hasattr(prism_scheduler, "_STDOUT_LOG_CAP"), (
        "_STDOUT_LOG_CAP constant is missing from prism_scheduler"
    )
    cap = prism_scheduler._STDOUT_LOG_CAP
    assert isinstance(cap, int), f"_STDOUT_LOG_CAP must be int, got {type(cap)}"
    assert cap > 0, f"_STDOUT_LOG_CAP must be positive, got {cap}"


# ---------------------------------------------------------------------------
# _redact_secrets direct unit tests
# ---------------------------------------------------------------------------


class TestRedactSecrets:
    """Direct unit tests for the _redact_secrets(text, secret_values) helper.

    The helper is the load-bearing security control for AC-4.  These tests
    probe its contract independent of _run_prism so failures pinpoint the
    helper vs the caller.
    """

    def test_empty_string_returns_empty(self):
        """Empty input must yield empty output regardless of secret_values."""
        assert prism_scheduler._redact_secrets("", ["s3cr3t"]) == ""

    def test_no_secrets_no_patterns_passthrough(self):
        """Benign text with no matching secrets or token shapes is unchanged."""
        text = "Connecting to database at localhost:5432"
        result = prism_scheduler._redact_secrets(text, [])
        assert result == text

    def test_replaces_live_value_at_start(self):
        """Secret at the start of the string is replaced."""
        secret = "super_secret_value_xyz"
        text = f"{secret} followed by normal text"
        result = prism_scheduler._redact_secrets(text, [secret])
        assert secret not in result
        assert _REDACTED in result

    def test_replaces_live_value_in_middle(self):
        """Secret embedded mid-string is replaced."""
        secret = "super_secret_value_xyz"
        text = f"prefix {secret} suffix"
        result = prism_scheduler._redact_secrets(text, [secret])
        assert secret not in result
        assert _REDACTED in result

    def test_replaces_live_value_at_end(self):
        """Secret at the end of the string is replaced."""
        secret = "super_secret_value_xyz"
        text = f"normal text ends with {secret}"
        result = prism_scheduler._redact_secrets(text, [secret])
        assert secret not in result
        assert _REDACTED in result

    def test_replaces_all_occurrences_of_live_value(self):
        """All occurrences of the secret in the text must be replaced (global replace)."""
        secret = "my_token_value"
        text = f"token={secret} and again token={secret} and once more {secret}"
        result = prism_scheduler._redact_secrets(text, [secret])
        assert secret not in result
        # Three occurrences → three REDACTED markers
        assert result.count(_REDACTED) >= 3

    def test_sk_ant_pattern_redacted_even_without_live_value(self):
        """sk-ant-... token shape is redacted by the shape-regex even without a live value match.

        Tolerance: the pattern requires at least 8 alphanumeric/underscore/dash chars
        after sk-ant-.  We use 10 to give the implementation minimal wiggle room.
        """
        token = (
            "sk-ant-api03-AAAAAAAAAA"  # 10 chars after sk-ant-api03-, well over the 8-char minimum
        )
        text = f"Authorization: Bearer {token}"
        result = prism_scheduler._redact_secrets(text, [])  # no explicit secret value
        assert token not in result, (
            f"sk-ant-... token shape must be redacted by regex; got: {result!r}"
        )
        assert _REDACTED in result

    def test_sk_pattern_redacted_by_shape_regex(self):
        """sk-... token shape (16+ alphanum after sk-) is redacted by the shape-regex."""
        token = "sk-ABCDEFGHIJKLMNOPQRST"  # 20 chars after sk-, over the 16-char minimum
        text = f"credential={token}"
        result = prism_scheduler._redact_secrets(text, [])
        assert token not in result, f"sk-... token shape must be redacted; got: {result!r}"
        assert _REDACTED in result

    def test_oat_pattern_redacted_by_shape_regex(self):
        """oat_... token shape (8+ alphanum after oat_) is redacted by the shape-regex."""
        token = "oat_ABCDEFGHIJ"  # 10 chars after oat_, over the 8-char minimum
        text = f"CLAUDE_CODE_OAUTH_TOKEN={token}"
        result = prism_scheduler._redact_secrets(text, [])
        assert token not in result, f"oat_... token shape must be redacted; got: {result!r}"
        assert _REDACTED in result

    def test_oat_pattern_too_short_not_redacted_by_regex(self):
        """oat_ABC (< 8 chars after oat_) must NOT be redacted by the shape-regex.

        This proves the minimum-length requirements are respected, so the regex
        doesn't over-eagerly censor benign short strings like 'oat_hi'.
        Tolerance: exactly 3 chars after oat_ — safely below the 8-char floor.
        """
        token = "oat_ABC"  # 3 chars after oat_ — below the 8-char minimum
        text = f"some text mentioning {token} here"
        result = prism_scheduler._redact_secrets(text, [])
        # The token should survive because it's too short for the regex
        assert token in result, (
            f"oat_... with fewer than 8 chars should NOT be redacted by regex; got: {result!r}"
        )

    def test_empty_value_in_secret_values_list_skipped(self):
        """Empty string in secret_values must not corrupt the text or raise.

        An empty string would match everywhere via str.replace("", ...) and
        produce malformed output.  The helper must filter it out.
        """
        text = "normal log line with useful info"
        result = prism_scheduler._redact_secrets(text, [""])
        # Text must be recognizable; no explosion of REDACTED markers
        assert "normal log line" in result or _REDACTED in result
        # The specific check: empty value must not cause every character to be wrapped
        assert len(result) < len(text) * 3, (
            "Empty secret value caused runaway string expansion — must be filtered"
        )

    def test_multiple_distinct_secrets_all_replaced(self):
        """Multiple distinct secret values in secret_values are all replaced."""
        s1 = "first_secret_aaaa"
        s2 = "second_secret_bbbb"
        text = f"saw {s1} then {s2} in output"
        result = prism_scheduler._redact_secrets(text, [s1, s2])
        assert s1 not in result
        assert s2 not in result
        assert result.count(_REDACTED) >= 2


# ---------------------------------------------------------------------------
# AC-1 — Non-zero exit logs [prism_scheduler] prefix + returncode
# ---------------------------------------------------------------------------


def test_nonzero_exit_logs_returncode_and_prism_prefix(capsys, monkeypatch):
    """rc != 0 must produce a [prism_scheduler] diagnostic with the integer returncode.

    The prefix and the returncode number are the minimum identifiers for a
    journald operator to locate and triage the entry.
    """
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch(
        "prism_scheduler.subprocess.run", return_value=_make_proc(2, stderr="Traceback boom")
    ):
        result = prism_scheduler._run_prism("run-id-ac1")

    assert result is False
    captured = capsys.readouterr()
    assert "[prism_scheduler]" in captured.err, (
        "Non-zero exit must emit a [prism_scheduler] prefixed log line"
    )
    assert (
        "returncode=2" in captured.err or "returncode: 2" in captured.err or "2" in captured.err
    ), "The integer returncode must appear in the diagnostic log"


# ---------------------------------------------------------------------------
# AC-2 — stderr tail logged; (stderr empty) marker when empty
# ---------------------------------------------------------------------------


def test_nonzero_exit_stderr_content_logged(capsys, monkeypatch):
    """Captured stderr text must appear in the diagnostic log on non-zero exit."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    sentinel = "SENTINEL_STDERR_CONTENT_SHOULD_APPEAR"
    with patch("prism_scheduler.subprocess.run", return_value=_make_proc(1, stderr=sentinel)):
        prism_scheduler._run_prism("run-id-stderr-content")

    captured = capsys.readouterr()
    assert sentinel in captured.err, (
        "The subprocess stderr content must appear in the diagnostic log"
    )


def test_nonzero_exit_empty_stderr_logs_marker(capsys, monkeypatch):
    """Empty subprocess stderr on non-zero exit must produce a (stderr empty) marker.

    The absence of output is itself diagnostic (e.g. a silent OOM); logging
    nothing would be indistinguishable from a missing diagnostic block.
    """
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch(
        "prism_scheduler.subprocess.run", return_value=_make_proc(1, stderr="", stdout="something")
    ):
        prism_scheduler._run_prism("run-id-empty-stderr")

    captured = capsys.readouterr()
    assert "(stderr empty)" in captured.err, (
        "Empty subprocess stderr must produce a '(stderr empty)' marker in the log"
    )


# ---------------------------------------------------------------------------
# AC-3 — stdout tail logged; (stdout empty) marker; tail truncation with marker
# ---------------------------------------------------------------------------


def test_nonzero_exit_empty_stdout_logs_marker(capsys, monkeypatch):
    """Empty subprocess stdout on non-zero exit must produce a (stdout empty) marker."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch(
        "prism_scheduler.subprocess.run", return_value=_make_proc(1, stderr="some error", stdout="")
    ):
        prism_scheduler._run_prism("run-id-empty-stdout")

    captured = capsys.readouterr()
    assert "(stdout empty)" in captured.err, (
        "Empty subprocess stdout must produce a '(stdout empty)' marker in the log"
    )


def test_nonzero_exit_logs_stdout_tail_with_truncation_marker(capsys, monkeypatch):
    """Stdout longer than _STDOUT_LOG_CAP must be tail-truncated with a marker.

    The tail (not the head) is logged because error payloads land at the end.
    The truncation marker must appear so the operator knows output was cut.
    Derived from the module constant so the test stays valid if the cap is retuned.
    """
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    cap = prism_scheduler._STDOUT_LOG_CAP
    # Build a string distinctly longer than the cap — head chars 'H', tail chars 'T'
    head = "H" * cap  # these should NOT appear in the log
    tail = "T" * 500  # these should appear (they're at the end)
    long_stdout = head + tail

    with patch(
        "prism_scheduler.subprocess.run",
        return_value=_make_proc(1, stderr="err", stdout=long_stdout),
    ):
        prism_scheduler._run_prism("run-id-stdout-trunc")

    captured = capsys.readouterr()
    # The tail sentinel chars must be present
    assert "T" * 20 in captured.err, "The tail of the stdout must appear in the diagnostic log"
    # A truncation marker must appear (exact wording is implementation detail;
    # assert 'truncat' covers '[truncated, showing last N]' and similar)
    assert "truncat" in captured.err.lower() or "…" in captured.err or "..." in captured.err, (
        "A truncation marker must appear when stdout exceeds _STDOUT_LOG_CAP"
    )


def test_nonzero_exit_stderr_long_logs_tail_truncation_marker(capsys, monkeypatch):
    """Stderr longer than _STDERR_LOG_CAP must be tail-truncated with a marker.

    Derived from the module constant so the test stays valid if the cap is retuned.
    """
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    cap = prism_scheduler._STDERR_LOG_CAP
    head = "B" * cap  # these should NOT appear
    tail = "Z" * 1000  # these should appear (tail end)
    long_stderr = head + tail

    with patch(
        "prism_scheduler.subprocess.run", return_value=_make_proc(1, stderr=long_stderr, stdout="")
    ):
        prism_scheduler._run_prism("run-id-stderr-trunc")

    captured = capsys.readouterr()
    assert "Z" * 20 in captured.err, "The tail of the stderr must appear in the diagnostic log"
    assert "truncat" in captured.err.lower() or "…" in captured.err or "..." in captured.err, (
        "A truncation marker must appear when stderr exceeds _STDERR_LOG_CAP"
    )


def test_nonzero_exit_short_output_logged_in_full(capsys, monkeypatch):
    """Output shorter than the cap must be logged in full (tail of a short string = the string)."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    short_stderr = "Error: model not found"
    short_stdout = '{"error": "quota_exceeded"}'

    with patch(
        "prism_scheduler.subprocess.run",
        return_value=_make_proc(1, stderr=short_stderr, stdout=short_stdout),
    ):
        prism_scheduler._run_prism("run-id-short-output")

    captured = capsys.readouterr()
    assert short_stderr in captured.err
    assert short_stdout in captured.err


# ---------------------------------------------------------------------------
# AC-4 — Credential redaction: live values + token-shaped patterns
# ---------------------------------------------------------------------------


def test_redacts_live_oauth_token_in_stderr(capsys, monkeypatch):
    """The live CLAUDE_CODE_OAUTH_TOKEN value embedded in stderr must be redacted.

    The test plants the token via monkeypatch.setenv (so _run_prism sees it in
    _council_env) and embeds the same value in the mocked stderr.  If redaction
    works, the literal value must not appear anywhere in captured output.
    """
    live_token = "oat_LIVE_TOKEN_AAAAAAAAAA"
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", live_token)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    stderr_with_token = f"Authentication failed with token: {live_token}"
    with patch(
        "prism_scheduler.subprocess.run", return_value=_make_proc(1, stderr=stderr_with_token)
    ):
        prism_scheduler._run_prism("run-id-oauth-redact")

    captured = capsys.readouterr()
    full_output = captured.out + captured.err
    assert live_token not in full_output, (
        "Live CLAUDE_CODE_OAUTH_TOKEN value must not appear in any log output; "
        "found in captured output."
    )
    assert _REDACTED in full_output, (
        "***REDACTED*** must be present in the log when a token value was replaced"
    )


def test_redacts_api_key_value_in_stdout(capsys, monkeypatch):
    """The ANTHROPIC_API_KEY value embedded in stdout must be redacted.

    Even though _run_prism strips ANTHROPIC_API_KEY from the subprocess env,
    the key may still appear in the subprocess output (e.g. an error message
    that echoes the env it tried to use).  The redaction covers it anyway.
    """
    api_key = "sk-ant-live-BBBBBBBBBBBBBB"
    monkeypatch.setenv("ANTHROPIC_API_KEY", api_key)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    stdout_with_key = f"Startup: ANTHROPIC_API_KEY={api_key} was detected"
    with patch(
        "prism_scheduler.subprocess.run",
        return_value=_make_proc(1, stdout=stdout_with_key, stderr="fail"),
    ):
        prism_scheduler._run_prism("run-id-apikey-redact")

    captured = capsys.readouterr()
    full_output = captured.out + captured.err
    assert api_key not in full_output, (
        "ANTHROPIC_API_KEY value must not appear in any log output; found in captured output."
    )
    assert _REDACTED in full_output


def test_redacts_sk_ant_pattern_in_stderr(capsys, monkeypatch):
    """sk-ant-... token shape in stderr is redacted by shape-regex even without a live env match."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    token_in_output = "sk-ant-api03-XXXXXXXXXXXXXXXX"  # 16 chars — well over the 8-char minimum
    stderr_with_token = "Error: invalid token sk-ant-api03-XXXXXXXXXXXXXXXX in config"
    with patch(
        "prism_scheduler.subprocess.run", return_value=_make_proc(1, stderr=stderr_with_token)
    ):
        prism_scheduler._run_prism("run-id-sk-ant-regex")

    captured = capsys.readouterr()
    assert token_in_output not in captured.err, (
        "sk-ant-... token shape must be redacted by shape-regex"
    )
    assert _REDACTED in captured.err


def test_redacts_oat_pattern_in_stdout(capsys, monkeypatch):
    """oat_... token shape in stdout is redacted by shape-regex."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    token_in_output = "oat_YYYYYYYYYY"  # 10 chars after oat_
    stdout_with_token = f"council stdout: CLAUDE_CODE_OAUTH_TOKEN={token_in_output}"
    with patch(
        "prism_scheduler.subprocess.run",
        return_value=_make_proc(1, stdout=stdout_with_token, stderr="err"),
    ):
        prism_scheduler._run_prism("run-id-oat-regex")

    captured = capsys.readouterr()
    assert token_in_output not in captured.err, (
        "oat_... token shape must be redacted by shape-regex"
    )
    assert _REDACTED in captured.err


def test_redacts_sk_pattern_in_stderr(capsys, monkeypatch):
    """sk-... token shape (16+ chars) in stderr is redacted by shape-regex."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    token_in_output = "sk-ZZZZZZZZZZZZZZZZ"  # 16 chars — exactly at the minimum
    stderr_with_token = f"key='{token_in_output}' rejected"
    with patch(
        "prism_scheduler.subprocess.run", return_value=_make_proc(1, stderr=stderr_with_token)
    ):
        prism_scheduler._run_prism("run-id-sk-regex")

    captured = capsys.readouterr()
    assert token_in_output not in captured.err, (
        "sk-... token shape (16+ chars) must be redacted by shape-regex"
    )
    assert _REDACTED in captured.err


def test_no_secret_value_appears_in_any_log_line(capsys, monkeypatch):
    """Adversarial sweep: embed BOTH live credential values in BOTH stdout and stderr.

    Reads the entirety of captured output (both .out and .err) and asserts that
    neither secret value appears as a substring anywhere.  This is the most
    comprehensive security regression guard — it proves the full redaction path
    works end-to-end from env lookup through to the final print() call.
    """
    oauth_token = "oat_SWEEPTOKEN_AAAAAAAAAA"  # 18 chars after oat_
    api_key = "sk-ant-sweep-BBBBBBBBBBB"  # well over 8 chars

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", oauth_token)
    monkeypatch.setenv("ANTHROPIC_API_KEY", api_key)

    # Embed both secrets in BOTH channels — worst-case scenario
    poisoned_stderr = (
        f"Attempt to authenticate with CLAUDE_CODE_OAUTH_TOKEN={oauth_token}\n"
        f"Also tried ANTHROPIC_API_KEY={api_key}"
    )
    poisoned_stdout = (
        f'{{"error": "auth_failed", "token": "{oauth_token}", "api_key": "{api_key}"}}'
    )

    with patch(
        "prism_scheduler.subprocess.run",
        return_value=_make_proc(1, stderr=poisoned_stderr, stdout=poisoned_stdout),
    ):
        prism_scheduler._run_prism("run-id-sweep-test")

    captured = capsys.readouterr()
    full_output = captured.out + captured.err

    assert oauth_token not in full_output, (
        "Live CLAUDE_CODE_OAUTH_TOKEN must not appear anywhere in captured output"
    )
    assert api_key not in full_output, (
        "ANTHROPIC_API_KEY value must not appear anywhere in captured output"
    )
    # At least two REDACTED markers (one per secret in stderr, possibly more)
    assert full_output.count(_REDACTED) >= 2, (
        f"Expected at least 2 ***REDACTED*** markers (one per secret value); "
        f"got {full_output.count(_REDACTED)}"
    )


# ---------------------------------------------------------------------------
# AC-5 — Success path (rc=0) byte-for-byte unchanged
# ---------------------------------------------------------------------------


def test_success_path_calls_persist_spend_and_returns_true(capsys, monkeypatch):
    """On rc=0, _persist_spend is called once with correct args and True is returned.

    No [prism_scheduler] diagnostic line must be emitted — the success path must
    be byte-for-byte unchanged so the only new behavior is in the failure branch.
    """
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    run_id = "run-id-success-ac5"
    stdout_payload = '{"total_cost_usd": 7.42}'

    with (
        patch(
            "prism_scheduler.subprocess.run", return_value=_make_proc(0, stdout=stdout_payload)
        ) as mock_run,
        patch("prism_scheduler._persist_spend") as mock_spend,
    ):
        result = prism_scheduler._run_prism(run_id)

    assert result is True, "rc=0 must return True"
    mock_spend.assert_called_once_with(run_id, stdout_payload)

    captured = capsys.readouterr()
    # No diagnostic block on success — the prefix must not appear
    assert "[prism_scheduler]" not in captured.err or "Attempt" not in captured.err, (
        "Success path must not emit a diagnostic block — check that only failure "
        "paths trigger the new logging"
    )


def test_success_path_emits_no_diagnostic_block(capsys, monkeypatch):
    """Stronger check: [prism_scheduler] diagnostic prefix must be absent on rc=0.

    Separate from the persist-spend check so a failure is unambiguous.
    """
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with (
        patch("prism_scheduler.subprocess.run", return_value=_make_proc(0, stdout="{}")),
        patch("prism_scheduler._persist_spend"),
    ):
        prism_scheduler._run_prism("run-id-no-diag")

    captured = capsys.readouterr()
    # The diagnostic block is keyed by returncode= in the output
    assert "returncode=" not in captured.err and "returncode:" not in captured.err, (
        "returncode diagnostic must NOT appear on success path"
    )


# ---------------------------------------------------------------------------
# AC-6 — subprocess.run raises → SubprocessError: <Type> only, no message/path
# ---------------------------------------------------------------------------


def test_subprocess_raises_logs_type_only_and_returns_false(capsys, monkeypatch):
    """subprocess.run raising must produce SubprocessError: FileNotFoundError only.

    The message and path in the exception must NOT appear — D-1 contract means
    type-only.  This ensures a path like '/usr/local/bin/claude' never leaks
    into the ops log.
    """
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    sensitive_path = "/usr/local/bin/claude this path must not appear in logs"

    with patch("prism_scheduler.subprocess.run", side_effect=FileNotFoundError(sensitive_path)):
        result = prism_scheduler._run_prism("run-id-exc-path")

    assert result is False, "subprocess.run raising must return False"

    captured = capsys.readouterr()
    assert "SubprocessError: FileNotFoundError" in captured.err, (
        "Expected 'SubprocessError: FileNotFoundError' in stderr on subprocess raise"
    )
    assert sensitive_path not in captured.err, (
        "The exception message/path must NOT appear in the log — D-1 type-only"
    )
    # The diagnostic block (returncode=) must also not appear (no result.stderr/stdout exists)
    assert "returncode=" not in captured.err and "returncode:" not in captured.err, (
        "The failure-diagnostic block must not appear when subprocess.run raises "
        "(there is no result object to inspect)"
    )


def test_subprocess_raises_os_error_logs_type_only(capsys, monkeypatch):
    """OSError (e.g. EACCES) raise path also logs type-only."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch(
        "prism_scheduler.subprocess.run", side_effect=OSError("Permission denied: secret path")
    ):
        result = prism_scheduler._run_prism("run-id-oserr")

    assert result is False
    captured = capsys.readouterr()
    assert "SubprocessError: OSError" in captured.err
    assert "Permission denied" not in captured.err, (
        "Exception message must not appear in the log (D-1 type-only)"
    )


# ---------------------------------------------------------------------------
# AC-7 — _run_prism never raises even when _redact_secrets throws
# ---------------------------------------------------------------------------


def test_never_raises_when_redact_secrets_throws(capsys, monkeypatch):
    """If _redact_secrets raises, _run_prism still returns False and does not propagate.

    The diagnostic-suppressed fallback must fire and report the exception type
    so the operator knows the diagnostic was unavailable (vs the council actually
    writing clean output).
    """
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def _redact_raises(text: str, secret_values: list) -> str:
        raise RuntimeError("simulated redaction failure")

    with (
        patch(
            "prism_scheduler.subprocess.run",
            return_value=_make_proc(1, stderr="council output", stdout=""),
        ),
        patch("prism_scheduler._redact_secrets", side_effect=_redact_raises),
    ):
        # Must not raise
        result = prism_scheduler._run_prism("run-id-never-raises")

    assert result is False, "_run_prism must return False on non-zero rc even when redaction fails"

    captured = capsys.readouterr()
    assert "diagnostic suppressed" in captured.err.lower(), (
        "When _redact_secrets throws, the log must contain 'diagnostic suppressed' "
        f"to indicate the diagnostic was unavailable; got: {captured.err!r}"
    )
    assert "RuntimeError" in captured.err, (
        "The suppressed exception type must be logged so the operator can triage"
    )


def test_never_raises_on_formatting_error(capsys, monkeypatch):
    """Even if the whole diagnostic block assembly throws, _run_prism returns False without raising.

    Simulates a broader failure in the diagnostic path (not just _redact_secrets).
    """
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch(
        "prism_scheduler.subprocess.run", return_value=_make_proc(1, stderr="err", stdout="")
    ):
        # Patch print to raise on the FIRST call (the diagnostic print)
        original_print = __builtins__["print"] if isinstance(__builtins__, dict) else print
        call_count = {"n": 0}

        def _print_raises(*args, **kwargs):
            file = kwargs.get("file")
            import sys as _sys

            if file is _sys.stderr:
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise ValueError("simulated print failure")
            original_print(*args, **kwargs)

        with patch("builtins.print", side_effect=_print_raises):
            # Must not raise — the outer except in _run_prism must catch this
            try:
                result = prism_scheduler._run_prism("run-id-print-raises")
            except Exception as exc:
                pytest.fail(
                    f"_run_prism raised {type(exc).__name__} when internal formatting failed — "
                    "must never raise (D-1 contract)"
                )

    # result may be True or False depending on implementation; key invariant is no raise


# ---------------------------------------------------------------------------
# AC-4 (extended) — _council_env credentials beyond OAUTH+API_KEY must be redacted
#
# _run_prism builds _council_env = os.environ.copy() then pops only
# ANTHROPIC_API_KEY.  All other .env credentials (COMPOSER_SECRET, ALPACA_SECRET,
# DISCORD_WEBHOOK_URL, etc.) remain in _council_env and are passed verbatim to
# the council subprocess.  If those values appear in subprocess stderr/stdout,
# they must NOT reach the logs.  None of their formats match the three shape
# regexes, so secret_values must cover all credential-keyed env vars in
# _council_env — not just CLAUDE_CODE_OAUTH_TOKEN and ANTHROPIC_API_KEY.
#
# Reviewer Finding 1 (council-review, commit 6cfb935): BLOCKER — these three
# credential classes are structurally absent from secret_values and would appear
# unredacted in journald on a real Composer/Alpaca/Discord auth failure.
# ---------------------------------------------------------------------------


def test_does_not_leak_composer_secret_from_council_env(capsys, monkeypatch):
    """COMPOSER_SECRET from _council_env appearing in subprocess stderr must be redacted.

    _council_env = os.environ.copy() passes ALL .env credentials to the subprocess.
    Only CLAUDE_CODE_OAUTH_TOKEN + ANTHROPIC_API_KEY are in the original secret_values;
    COMPOSER_SECRET has no matching shape regex and will survive unredacted unless
    secret_values is extended to sweep the full _council_env for credential-keyed vars.
    """
    composer_secret = "COMPOSER_SECRET_VALUE_XYZ_PLAINTEXT_9876"
    monkeypatch.setenv("COMPOSER_SECRET", composer_secret)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    stderr_with_creds = (
        f"Authorization failed: COMPOSER_SECRET={composer_secret} was rejected by Composer API"
    )
    with patch(
        "prism_scheduler.subprocess.run",
        return_value=_make_proc(1, stderr=stderr_with_creds),
    ):
        prism_scheduler._run_prism("run-id-composer-secret-leak")

    captured = capsys.readouterr()
    assert composer_secret not in captured.out + captured.err, (
        "COMPOSER_SECRET from _council_env must not appear in any log output — "
        "secret_values must cover all credential-keyed env vars in _council_env, "
        "not only CLAUDE_CODE_OAUTH_TOKEN and ANTHROPIC_API_KEY"
    )
    assert _REDACTED in captured.out + captured.err, (
        "***REDACTED*** must appear in the log when a COMPOSER_SECRET value was replaced"
    )


def test_does_not_leak_alpaca_secret_from_council_env(capsys, monkeypatch):
    """ALPACA_SECRET_KEY from _council_env appearing in subprocess stdout must be redacted.

    ALPACA_SECRET_KEY is present in _council_env (inherited from os.environ after
    _load_env).  An Alpaca 401 error in subprocess output would echo this value.
    It matches no shape regex (no sk-ant-/sk-/oat_ prefix).
    """
    alpaca_secret = "alpaca_secret_key_ABCDEFGHIJ_plaintext_live"
    monkeypatch.setenv("ALPACA_SECRET_KEY", alpaca_secret)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    stdout_with_secret = f'{{"error": "unauthorized", "key_used": "{alpaca_secret}", "code": 401}}'
    with patch(
        "prism_scheduler.subprocess.run",
        return_value=_make_proc(1, stdout=stdout_with_secret, stderr="council error"),
    ):
        prism_scheduler._run_prism("run-id-alpaca-secret-leak")

    captured = capsys.readouterr()
    assert alpaca_secret not in captured.out + captured.err, (
        "ALPACA_SECRET_KEY from _council_env must not appear in any log output"
    )
    assert _REDACTED in captured.out + captured.err


def test_does_not_leak_discord_webhook_from_council_env(capsys, monkeypatch):
    """DISCORD_WEBHOOK_URL from _council_env appearing in subprocess stderr must be redacted.

    A Discord webhook URL is a bearer-equivalent secret — possession = ability to
    post to the channel.  It has no token shape matching any of the three regexes
    (no sk-ant-/sk-/oat_ prefix) and would appear unredacted in journald unless
    secret_values covers it.
    """
    discord_webhook = "https://discord.com/api/webhooks/111222333/abcdefghijklmnop_secret_token"
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", discord_webhook)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    stderr_with_webhook = (
        f"Discord notification failed: POST {discord_webhook} returned 401 Unauthorized"
    )
    with patch(
        "prism_scheduler.subprocess.run",
        return_value=_make_proc(1, stderr=stderr_with_webhook),
    ):
        prism_scheduler._run_prism("run-id-discord-webhook-leak")

    captured = capsys.readouterr()
    assert discord_webhook not in captured.out + captured.err, (
        "DISCORD_WEBHOOK_URL from _council_env must not appear in any log output"
    )
    assert _REDACTED in captured.out + captured.err


def test_full_env_credential_sweep_no_value_leaks(capsys, monkeypatch):
    """Master sweep: ALL .env credential classes present in _council_env must be redacted.

    Plants five distinct credential values across five env vars (two named explicitly
    in the original secret_values, three that are absent but present in _council_env).
    Embeds all five in mocked stderr AND stdout.  Reads the entirety of capsys output
    and asserts none of the five values appears as a substring anywhere.

    This is the regression guard for the full expanded-secret_values fix.
    """
    oauth_token = "oat_SWEEPTOKEN_MASTER_AAAAAAA"  # matches oat_ shape regex too
    api_key = "sk-ant-sweep-master-BBBBBBBB"  # matches sk-ant- shape regex too
    composer_secret = "COMPOSER_MASTER_SECRET_99999_plaintext"
    alpaca_secret = "alpaca_master_secret_ZZZZZZZZZ_live"
    discord_webhook = "https://discord.com/api/webhooks/999888777/master_sweep_secret_token_xyz"

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", oauth_token)
    monkeypatch.setenv("ANTHROPIC_API_KEY", api_key)
    monkeypatch.setenv("COMPOSER_SECRET", composer_secret)
    monkeypatch.setenv("ALPACA_SECRET_KEY", alpaca_secret)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", discord_webhook)

    poisoned_stderr = (
        f"oauth={oauth_token} api_key={api_key} composer={composer_secret} alpaca={alpaca_secret}"
    )
    poisoned_stdout = (
        f'{{"discord": "{discord_webhook}", "composer": "{composer_secret}", '
        f'"alpaca": "{alpaca_secret}"}}'
    )

    with patch(
        "prism_scheduler.subprocess.run",
        return_value=_make_proc(1, stderr=poisoned_stderr, stdout=poisoned_stdout),
    ):
        prism_scheduler._run_prism("run-id-full-env-sweep")

    captured = capsys.readouterr()
    full_output = captured.out + captured.err

    secrets_that_must_not_appear = {
        "oauth_token": oauth_token,
        "api_key": api_key,
        "composer_secret": composer_secret,
        "alpaca_secret": alpaca_secret,
        "discord_webhook": discord_webhook,
    }
    for name, value in secrets_that_must_not_appear.items():
        assert value not in full_output, (
            f"{name} ({value[:20]}…) must not appear anywhere in captured log output — "
            "secret_values must sweep ALL credential-keyed env vars in _council_env"
        )

    assert _REDACTED in full_output, (
        "***REDACTED*** must appear at least once when credentials were redacted"
    )


# ---------------------------------------------------------------------------
# Over-redaction guard — short/common marker-keyed values must NOT be redacted
#
# A credential-marker sweep (keys containing SECRET/KEY/TOKEN/…) that lacks a
# minimum-value-length floor will pull in vars like `LOG_LEVEL=1`, `X_KEY=yes`,
# `SOME_TOKEN=true`, etc.  Redacting "1", "yes", or "true" everywhere destroys
# diagnostic value — every returncode, integer, and common word in the log gets
# censored.  The fix must skip values shorter than a minimum floor (e.g. 8 chars).
# ---------------------------------------------------------------------------


def test_short_secret_marker_keyed_value_not_redacted(capsys, monkeypatch):
    """An env var with a credential-marker key but a SHORT value must NOT be redacted.

    A credential-marker sweep without a minimum-value-length floor pulls in vars
    like LOG_LEVEL_KEY=info42 (6 chars).  Redacting 'info42' everywhere corrupts
    the diagnostic.  The fix must skip values below a named min-length constant
    (e.g. _MIN_SWEEP_SECRET_LEN = 8).

    The distinctive value 'info42' is embedded in the mocked stderr WITHOUT the
    key name.  An implementation without the floor replaces it with ***REDACTED***,
    failing the assertion — that is the intended RED signal to drive the fix.
    """
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    # 6-char value — distinctive enough not to appear accidentally in the diagnostic
    # prefix text, and safely below any reasonable min-length floor (e.g. 8 chars).
    # The key contains "KEY" so a naive marker sweep includes this value.
    short_value = "info42"
    monkeypatch.setenv("LOG_LEVEL_KEY", short_value)

    # Embed the value in stderr WITHOUT the key name — the only path the assertion
    # can pass is if the value itself survives unredacted.
    # No floor:    "severity=info42" → "severity=***REDACTED***" → assertion FAILS.
    # With floor:  "severity=info42" unchanged               → assertion PASSES.
    stderr_with_short_value = f"Process exited: severity={short_value} returncode=1"
    with patch(
        "prism_scheduler.subprocess.run",
        return_value=_make_proc(1, stderr=stderr_with_short_value),
    ):
        prism_scheduler._run_prism("run-id-over-redact-guard")

    captured = capsys.readouterr()
    full_output = captured.out + captured.err

    # "info42" must survive in the log.  If swept into secret_values without a
    # min-length floor it is replaced everywhere and the assertion fails.
    assert short_value in full_output, (
        f"Short ({len(short_value)}-char) marker-keyed value {short_value!r} must NOT "
        "be replaced by ***REDACTED*** — the min-length floor (_MIN_SWEEP_SECRET_LEN) "
        "must exclude values shorter than 8 chars from secret_values to prevent "
        "corrupting diagnostic output (common config values, short strings)"
    )


def test_benign_non_marker_env_var_never_redacted(capsys, monkeypatch):
    """Env vars with no credential-marker in their key must never be redacted.

    Only vars whose key contains a marker (SECRET/KEY/TOKEN/WEBHOOK/PASSWORD/URI)
    are candidates for redaction.  A long benign value in an innocent key (e.g.
    PYTHONPATH, HOME, TERM, PATH fragment) must pass through unmolested.
    """
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    # Benign env var — long value, no credential marker in key
    benign_value = "very_long_benign_value_abcdefghijklmnop_definitely_not_a_secret"
    monkeypatch.setenv("PYTHONPATH_EXTRA", benign_value)

    stderr_with_benign = f"Subprocess stderr mentioning PYTHONPATH_EXTRA={benign_value}"
    with patch(
        "prism_scheduler.subprocess.run",
        return_value=_make_proc(1, stderr=stderr_with_benign),
    ):
        prism_scheduler._run_prism("run-id-benign-no-redact")

    captured = capsys.readouterr()
    # The benign value must not be redacted — only marker-keyed vars are in scope
    assert benign_value in captured.err, (
        f"Benign non-marker-keyed value must appear unredacted in the log; "
        f"got: {captured.err[:200]!r}"
    )
