"""
Tests for prism_scheduler council subprocess authentication isolation.

The council subprocess (`claude -p`) must NOT inherit ANTHROPIC_API_KEY from
the scheduler's environment: Claude Code's auth precedence puts ANTHROPIC_API_KEY
above CLAUDE_CODE_OAUTH_TOKEN, so passing the API key forces metered billing on
every nightly council run even when a subscription token is present.

Acceptance criteria (feat/prism-council-sub-auth):
  AC-1: ANTHROPIC_API_KEY is excluded from the subprocess env.
  AC-2: CLAUDE_CODE_OAUTH_TOKEN passes through to the subprocess env.
  AC-3: All other env vars (e.g. DB_PATH) are preserved — only the API key
        is stripped, nothing else.

Seam: patch `prism_scheduler.subprocess.run` and inspect the `env` kwarg that
`_run_prism` passes to it.  A mock returncode of 0 prevents a real subprocess.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

import prism_scheduler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proc(returncode: int = 0) -> MagicMock:
    """Return a fake CompletedProcess-like mock with the given returncode."""
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = returncode
    proc.stdout = ""
    proc.stderr = ""
    return proc


# ---------------------------------------------------------------------------
# AC-1 — RED today: os.environ.copy() passes ANTHROPIC_API_KEY through;
#         fix must pop it before handing env to subprocess.
# ---------------------------------------------------------------------------


def test_council_subprocess_excludes_api_key(monkeypatch):
    """ANTHROPIC_API_KEY must NOT appear in the env passed to the council subprocess.

    This test is genuinely RED against the current implementation: `_run_prism`
    calls `subprocess.run(..., env=os.environ.copy())` which copies the full
    environment, including any ANTHROPIC_API_KEY loaded by `_load_env` /
    inherited from the OS.  The fix must pop that key before the call so
    Claude Code falls back to CLAUDE_CODE_OAUTH_TOKEN (subscription auth).
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-should-not-reach-subprocess")

    with patch("prism_scheduler.subprocess.run", return_value=_make_proc(0)) as mock_run:
        prism_scheduler._run_prism("test-run-id-ac1")

    assert mock_run.called, "_run_prism did not call subprocess.run"
    captured_env = mock_run.call_args.kwargs.get("env") or mock_run.call_args[1].get("env")
    assert captured_env is not None, "subprocess.run was not called with an explicit env kwarg"
    assert "ANTHROPIC_API_KEY" not in captured_env, (
        "ANTHROPIC_API_KEY must be stripped from the council subprocess env so that "
        "claude -p falls back to CLAUDE_CODE_OAUTH_TOKEN (subscription) instead of "
        "billing against the metered API key"
    )


# ---------------------------------------------------------------------------
# AC-2 — CLAUDE_CODE_OAUTH_TOKEN must be preserved so the subprocess can auth.
# ---------------------------------------------------------------------------


def test_council_subprocess_passes_oauth_token(monkeypatch):
    """CLAUDE_CODE_OAUTH_TOKEN must reach the council subprocess env.

    If the token is stripped alongside ANTHROPIC_API_KEY, the subprocess has no
    auth mechanism and will fail.  This test ensures only the metered key is
    removed.
    """
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-test-token-abc123")
    # Ensure the API key is NOT in env for this test so we're isolating AC-2
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch("prism_scheduler.subprocess.run", return_value=_make_proc(0)) as mock_run:
        prism_scheduler._run_prism("test-run-id-ac2")

    assert mock_run.called, "_run_prism did not call subprocess.run"
    captured_env = mock_run.call_args.kwargs.get("env") or mock_run.call_args[1].get("env")
    assert captured_env is not None, "subprocess.run was not called with an explicit env kwarg"
    assert "CLAUDE_CODE_OAUTH_TOKEN" in captured_env, (
        "CLAUDE_CODE_OAUTH_TOKEN must be present in the council subprocess env "
        "so that claude -p can authenticate via the subscription"
    )
    assert captured_env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-test-token-abc123", (
        "CLAUDE_CODE_OAUTH_TOKEN value must be passed through unchanged"
    )


# ---------------------------------------------------------------------------
# AC-3 — All other env vars must be preserved (surgical removal, not a whitelist).
# ---------------------------------------------------------------------------


def test_council_subprocess_preserves_other_env(monkeypatch):
    """Env vars unrelated to Claude API auth must survive the subprocess env build.

    The fix must pop only ANTHROPIC_API_KEY.  Using an allowlist or over-broad
    deletion that strips DB_PATH, PATH, or other runtime vars would break the
    subprocess.
    """
    sentinel_value = "/tmp/test-alphabot-sentinel.db"
    monkeypatch.setenv("DB_PATH", sentinel_value)
    # API key present to exercise the strip path
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-be-stripped")

    with patch("prism_scheduler.subprocess.run", return_value=_make_proc(0)) as mock_run:
        prism_scheduler._run_prism("test-run-id-ac3")

    assert mock_run.called, "_run_prism did not call subprocess.run"
    captured_env = mock_run.call_args.kwargs.get("env") or mock_run.call_args[1].get("env")
    assert captured_env is not None, "subprocess.run was not called with an explicit env kwarg"
    assert "DB_PATH" in captured_env, (
        "DB_PATH must survive the env-build step — the fix must only remove "
        "ANTHROPIC_API_KEY, not wipe or allowlist-filter the entire environment"
    )
    assert captured_env["DB_PATH"] == sentinel_value, (
        "DB_PATH value must be passed through unchanged"
    )
    # Belt-and-suspenders: also confirm the API key is gone in this scenario
    assert "ANTHROPIC_API_KEY" not in captured_env, (
        "ANTHROPIC_API_KEY must still be stripped even when other vars are present"
    )
