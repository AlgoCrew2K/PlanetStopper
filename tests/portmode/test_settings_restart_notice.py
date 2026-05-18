"""
RED tests for EXIT_AUTHORITY settings UX and restart notice (AC-P2.12.*).

Tests: sticky restart notice logic, /api/state additive fields, segmented control
semantics, degraded-state badge.
"""

from __future__ import annotations

import pytest


# These imports WILL FAIL until implemented (RED intent)
from engine.exit_authority import (  # noqa: F401
    build_restart_notice_context,
    write_exit_authority_to_env,
)


class TestStickyRestartNotice:
    """AC-P2.12.1, AC-P2.2.4: Sticky restart notice logic."""

    def test_notice_required_when_toggle_changed_after_daemon_start(self):
        """Toggle changed AFTER daemon start -> restart required."""
        ctx = build_restart_notice_context(
            toggle_changed_at="2024-11-01T10:05:00Z",
            daemon_started_at="2024-11-01T09:00:00Z",
        )
        assert ctx["restart_required"] is True

    def test_notice_clears_when_daemon_restarted_after_toggle(self):
        """Daemon restarted AFTER toggle -> no restart required."""
        ctx = build_restart_notice_context(
            toggle_changed_at="2024-11-01T10:05:00Z",
            daemon_started_at="2024-11-01T10:10:00Z",
        )
        assert ctx["restart_required"] is False

    def test_notice_not_required_when_no_toggle_change(self):
        """If toggle_changed_at is None (never changed), no notice needed."""
        ctx = build_restart_notice_context(
            toggle_changed_at=None,
            daemon_started_at="2024-11-01T09:00:00Z",
        )
        assert ctx["restart_required"] is False

    def test_notice_context_includes_both_timestamps(self):
        ctx = build_restart_notice_context(
            toggle_changed_at="2024-11-01T10:05:00Z",
            daemon_started_at="2024-11-01T09:00:00Z",
        )
        assert "toggle_changed_at" in ctx
        assert "daemon_started_at" in ctx

    def test_notice_uses_positive_restart_confirmation(self):
        """
        AC-P2.2.4: Panel BC H7 — positive restart-observed confirmation.
        Notice stays visible until daemon_started_at EXCEEDS toggle_changed_at.
        Equal timestamps (same second) still requires restart (not yet confirmed).
        """
        ctx = build_restart_notice_context(
            toggle_changed_at="2024-11-01T10:05:00Z",
            daemon_started_at="2024-11-01T10:05:00Z",
        )
        assert ctx["restart_required"] is True, (
            "AC-P2.2.4 BC H7: daemon_started_at must EXCEED (not merely equal) toggle_changed_at"
        )


class TestApiStateAdditiveFields:
    """AC-P2.12.2: /api/state adds three additive fields (no existing field renamed/removed)."""

    def test_api_state_includes_port_state_field(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test_apistate.db"))
        monkeypatch.setenv("EXIT_AUTHORITY", "per_symphony")
        import database
        database.init_db()

        from app import get_api_state_dict
        state = get_api_state_dict()
        assert "port_state" in state, "AC-P2.12.2: /api/state must include 'port_state' field"

    def test_api_state_includes_exit_authority_field(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test_apistate2.db"))
        monkeypatch.setenv("EXIT_AUTHORITY", "per_symphony")
        import database
        database.init_db()

        from app import get_api_state_dict
        state = get_api_state_dict()
        assert "exit_authority" in state, "AC-P2.12.2: /api/state must include 'exit_authority' field"

    def test_api_state_includes_daemon_started_at_field(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test_apistate3.db"))
        import database
        database.init_db()

        from app import get_api_state_dict
        state = get_api_state_dict()
        assert "daemon_started_at" in state, "AC-P2.12.2: /api/state must include 'daemon_started_at' field"

    def test_api_state_preserves_existing_fields(self, tmp_path, monkeypatch):
        """AC-P2.12.2: No existing field renamed or removed."""
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test_apistate4.db"))
        import database
        database.init_db()

        from app import get_api_state_dict
        state = get_api_state_dict()
        # These fields must still exist (backward compat)
        existing_fields = ["bot_state", "is_locked"]
        for field in existing_fields:
            assert field in state, (
                f"AC-P2.12.2: existing field '{field}' must not be removed from /api/state"
            )


class TestDegradedStateBadge:
    """AC-P2.12.4: Degraded-state badge when EXIT_AUTHORITY fallback fires."""

    def test_degraded_badge_when_unrecognized_authority(self, monkeypatch):
        """
        AC-P2.12.4: If EXIT_AUTHORITY fallback fires (unrecognized value),
        active-mode badge renders with amber border + 'PER-SYMPHONY-fallback' text.
        """
        monkeypatch.setenv("EXIT_AUTHORITY", "invalid_mode")

        from engine.exit_authority import get_exit_authority_badge_context
        ctx = get_exit_authority_badge_context()
        assert ctx["is_degraded"] is True
        assert "PER-SYMPHONY-fallback" in ctx["label"] or "fallback" in ctx["label"].lower(), (
            "AC-P2.12.4: degraded badge must indicate per-symphony fallback"
        )
        assert ctx.get("border_style") == "amber" or ctx.get("color") == "amber"

    def test_normal_badge_when_per_symphony(self, monkeypatch):
        monkeypatch.setenv("EXIT_AUTHORITY", "per_symphony")
        from engine.exit_authority import get_exit_authority_badge_context
        ctx = get_exit_authority_badge_context()
        assert ctx["is_degraded"] is False

    def test_normal_badge_when_port_level(self, monkeypatch):
        monkeypatch.setenv("EXIT_AUTHORITY", "port_level")
        from engine.exit_authority import get_exit_authority_badge_context
        ctx = get_exit_authority_badge_context()
        assert ctx["is_degraded"] is False
        assert ctx.get("color") == "indigo" or "port" in ctx["label"].lower()


# AC-P2.3.* per-account params tests live in test_per_account_params.py (dedicated file).
# Removed duplicate class here to prevent DB_PATH monkeypatch leakage across test files.
