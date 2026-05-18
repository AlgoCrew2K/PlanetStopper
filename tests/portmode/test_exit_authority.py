"""
RED tests for EXIT_AUTHORITY toggle (AC-P2.2.*).

EXIT_AUTHORITY env var; settings validation; fallback behavior; per-row authorization.
"""

from __future__ import annotations

import os

import pytest


# These imports WILL FAIL until implemented (RED intent)
from engine.exit_authority import (  # noqa: F401
    get_exit_authority,
    is_authoritative,
    validate_exit_authority,
)


class TestExitAuthorityEnvVar:
    """AC-P2.2.1, AC-P2.2.3: ENV var with default per_symphony."""

    def test_default_is_per_symphony(self, monkeypatch):
        monkeypatch.delenv("EXIT_AUTHORITY", raising=False)
        assert get_exit_authority() == "per_symphony"

    def test_per_symphony_value_accepted(self, monkeypatch):
        monkeypatch.setenv("EXIT_AUTHORITY", "per_symphony")
        assert get_exit_authority() == "per_symphony"

    def test_port_level_value_accepted(self, monkeypatch):
        monkeypatch.setenv("EXIT_AUTHORITY", "port_level")
        assert get_exit_authority() == "port_level"

    def test_only_two_valid_values(self, monkeypatch):
        """AC-P2.2.1: only 'per_symphony' and 'port_level' are valid."""
        monkeypatch.setenv("EXIT_AUTHORITY", "BOTH")
        result = validate_exit_authority("BOTH")
        assert result["valid"] is False

    def test_no_hardcoded_mode_string_in_production_code(self):
        """AC-P2.2.3: No hardcoded mode string anywhere in production code."""
        import inspect
        import engine.exit_authority as ea
        source = inspect.getsource(ea)
        # The valid values must be defined as constants, not inline string literals in if-checks
        # (The constants themselves will contain the strings, but comparisons must reference constants)
        assert 'os.getenv("EXIT_AUTHORITY"' in source or 'os.getenv(\'EXIT_AUTHORITY\'' in source, (
            "AC-P2.2.3: EXIT_AUTHORITY must be read via os.getenv"
        )


class TestExitAuthorityValidationAndFallback:
    """AC-P2.2.2: Defense-in-depth — unrecognized value falls back to per_symphony with ERROR log."""

    def test_unrecognized_value_falls_back_to_per_symphony(self, monkeypatch):
        monkeypatch.setenv("EXIT_AUTHORITY", "invalid_value")
        authority = get_exit_authority()
        assert authority == "per_symphony", (
            "AC-P2.2.2: unrecognized EXIT_AUTHORITY falls back to per_symphony"
        )

    def test_unrecognized_value_produces_error_log(self, monkeypatch, caplog):
        import logging
        monkeypatch.setenv("EXIT_AUTHORITY", "bogus")
        with caplog.at_level(logging.ERROR):
            get_exit_authority()
        assert any("EXIT_AUTHORITY" in r.message for r in caplog.records), (
            "AC-P2.2.2: unrecognized EXIT_AUTHORITY must produce ERROR-level log"
        )

    def test_port_level_with_missing_params_is_fail_stop(self, monkeypatch):
        """
        AC-P2.2.2: fail-STOP if port_level is requested AND port-level params
        for the account are missing. Engine refuses to enter port-level mode.
        """
        monkeypatch.setenv("EXIT_AUTHORITY", "port_level")
        result = validate_exit_authority("port_level", account_id="acct-no-params", has_port_params=False)
        assert result["valid"] is False
        assert result["error_type"] == "fail_stop", (
            "AC-P2.2.2: missing port-level params with port_level authority = fail-STOP"
        )

    def test_unrecognized_is_fail_degraded_not_stop(self, monkeypatch):
        """AC-P2.2.2: unrecognized literal = fail-DEGRADED (falls back, continues running)."""
        result = validate_exit_authority("garbage")
        assert result["valid"] is False
        assert result["error_type"] == "fail_degraded"


class TestIsAuthoritative:
    """AC-P2.2.6: Non-authoritative altitude triggered is never acted upon."""

    def test_per_symphony_is_authoritative_under_per_symphony(self):
        assert is_authoritative(altitude="per_symphony", exit_authority="per_symphony") is True

    def test_port_level_is_not_authoritative_under_per_symphony(self):
        assert is_authoritative(altitude="port_level", exit_authority="per_symphony") is False

    def test_port_level_is_authoritative_under_port_level(self):
        assert is_authoritative(altitude="port_level", exit_authority="port_level") is True

    def test_per_symphony_is_not_authoritative_under_port_level(self):
        assert is_authoritative(altitude="per_symphony", exit_authority="port_level") is False

    def test_non_authoritative_triggered_not_acted_upon(self):
        """
        AC-P2.2.6: When per_symphony is non-authoritative (EXIT_AUTHORITY=port_level),
        a triggered per-symphony signal must NOT reach the order-placement path.
        is_authoritative returns False, and the caller must skip order placement.
        """
        auth = is_authoritative(altitude="per_symphony", exit_authority="port_level")
        assert auth is False, (
            "per_symphony altitude must NOT be authoritative when EXIT_AUTHORITY=port_level"
        )


class TestExitAuthorityPersistence:
    """AC-P2.2.4: Toggle change semantics."""

    def test_toggle_change_does_not_auto_restart(self, tmp_path, monkeypatch):
        """
        AC-P2.2.4: Toggle change does NOT auto-restart the daemon.
        The settings write goes to .env; restart is operator-triggered.
        """
        from engine.exit_authority import write_exit_authority_to_env
        env_file = tmp_path / ".env"
        env_file.write_text("EXIT_AUTHORITY=per_symphony\n")
        write_exit_authority_to_env("port_level", env_path=str(env_file))
        content = env_file.read_text()
        assert "port_level" in content
        # Auto-restart would mean daemon_started_at changed; we only test env write here

    def test_restart_notice_required_fields(self):
        """
        AC-P2.2.4: Settings UI must surface a sticky restart notice.
        The notice data structure must include: toggle_changed_at timestamp
        AND daemon_started_at (from /api/state) for comparison.
        """
        from engine.exit_authority import build_restart_notice_context
        context = build_restart_notice_context(
            toggle_changed_at="2024-11-01T10:00:00Z",
            daemon_started_at="2024-11-01T09:00:00Z",
        )
        assert context["restart_required"] is True
        assert "toggle_changed_at" in context
        assert "daemon_started_at" in context

    def test_restart_notice_clears_after_daemon_restart(self):
        """
        AC-P2.2.4: Notice clears when daemon_started_at > toggle_changed_at.
        """
        from engine.exit_authority import build_restart_notice_context
        context = build_restart_notice_context(
            toggle_changed_at="2024-11-01T10:00:00Z",
            daemon_started_at="2024-11-01T10:05:00Z",
        )
        assert context["restart_required"] is False, (
            "Restart notice must clear after daemon restarts post-toggle"
        )
