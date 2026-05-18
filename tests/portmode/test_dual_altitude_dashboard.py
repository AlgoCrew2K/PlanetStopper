"""
RED tests for dashboard dual-altitude rendering (AC-P2.9.*).

Tests: pinned port row above per-symphony rows, per-row authoritative badge,
port-trigger glyph, OBSERVED-ONLY badge, both altitudes render regardless of toggle.
"""

from __future__ import annotations

import pytest


# These imports WILL FAIL until implemented (RED intent)
from dashboard.port_row import (  # noqa: F401
    build_port_row_context,
    build_per_symphony_row_context,
)


def _make_port_state(triggered: bool = False, trigger_id: str | None = None) -> dict:
    return {
        "account_id": "acct-001",
        "high_water_mark": 105000.0,
        "current_return": 0.05,
        "armed": True,
        "para_armed": False,
        "triggered": triggered,
        "triggered_reason": "hwm_trailing_stop" if triggered else None,
        "last_target_reduction_json": None,
        "last_selected_symphony_id": None,
        "port_trigger_id": trigger_id,
    }


def _make_symphony_state(symphony_id: str, triggered: bool = False) -> dict:
    return {
        "symphony_id": symphony_id,
        "high_water_mark": 52000.0,
        "current_return": 0.04,
        "armed": False,
        "triggered": triggered,
        "port_trigger_id": None,
    }


class TestPinnedPortRow:
    """AC-P2.9.2: Pinned port row above per-symphony rows."""

    def test_port_row_context_has_required_columns(self):
        ctx = build_port_row_context(
            port_state=_make_port_state(),
            exit_authority="per_symphony",
        )
        required = {
            "account_id", "port_hwm", "port_current_return",
            "port_armed", "port_para_armed", "port_triggered",
            "triggered_reason", "last_target_reduction", "last_selected_symphony_id",
            "exit_authority_badge",
        }
        missing = required - ctx.keys()
        assert not missing, f"Port row context missing keys: {missing}"

    def test_port_row_is_pinned_type(self):
        ctx = build_port_row_context(
            port_state=_make_port_state(),
            exit_authority="per_symphony",
        )
        assert ctx.get("row_type") == "port_level", (
            "AC-P2.9.2: port row must be identified as row_type='port_level'"
        )


class TestPerRowAuthoritativeBadge:
    """AC-P2.9.3: Per-row AUTHORITATIVE vs OBSERVED-ONLY badge."""

    def test_port_row_authoritative_under_port_level(self):
        ctx = build_port_row_context(
            port_state=_make_port_state(),
            exit_authority="port_level",
        )
        assert ctx["exit_authority_badge"]["is_authoritative"] is True
        assert ctx["exit_authority_badge"]["label"] == "AUTHORITATIVE"
        assert ctx["exit_authority_badge"]["color"] == "indigo"

    def test_port_row_observed_only_under_per_symphony(self):
        ctx = build_port_row_context(
            port_state=_make_port_state(),
            exit_authority="per_symphony",
        )
        assert ctx["exit_authority_badge"]["is_authoritative"] is False
        assert ctx["exit_authority_badge"]["label"] == "OBSERVED-ONLY"
        assert ctx["exit_authority_badge"]["color"] == "slate-500"

    def test_per_symphony_row_authoritative_under_per_symphony(self):
        ctx = build_per_symphony_row_context(
            symphony_state=_make_symphony_state("sym-a"),
            exit_authority="per_symphony",
        )
        assert ctx["exit_authority_badge"]["is_authoritative"] is True
        assert ctx["exit_authority_badge"]["label"] == "AUTHORITATIVE"

    def test_per_symphony_row_observed_only_under_port_level(self):
        ctx = build_per_symphony_row_context(
            symphony_state=_make_symphony_state("sym-b"),
            exit_authority="port_level",
        )
        assert ctx["exit_authority_badge"]["is_authoritative"] is False
        assert ctx["exit_authority_badge"]["label"] == "OBSERVED-ONLY"

    def test_badge_not_collocated_with_staleness_alarm(self):
        """
        AC-P2.2.5, AC-P2.9.3: Active-authority badge must be in LEFT column,
        not collocated with rose-toned staleness alarm (panel BC H6).
        """
        ctx = build_port_row_context(
            port_state=_make_port_state(),
            exit_authority="port_level",
        )
        badge = ctx["exit_authority_badge"]
        assert badge.get("column") == "left" or badge.get("position") != "staleness_alarm_column", (
            "AC-P2.2.5 BC H6: authority badge must not be collocated with staleness alarm"
        )


class TestBothAltitudesRenderRegardlessOfToggle:
    """AC-P2.9.4: Both altitudes render regardless of exit-authority toggle."""

    def test_port_row_renders_when_per_symphony_authoritative(self):
        """Even when EXIT_AUTHORITY=per_symphony, port row must be rendered."""
        ctx = build_port_row_context(
            port_state=_make_port_state(triggered=True),
            exit_authority="per_symphony",
        )
        # Port row context is built (not None, not empty)
        assert ctx is not None
        assert ctx.get("account_id") == "acct-001"

    def test_per_symphony_rows_render_when_port_level_authoritative(self):
        """Even when EXIT_AUTHORITY=port_level, per-symphony rows must be rendered."""
        ctx = build_per_symphony_row_context(
            symphony_state=_make_symphony_state("sym-c", triggered=True),
            exit_authority="port_level",
        )
        assert ctx is not None
        assert ctx.get("symphony_id") == "sym-c"

    def test_toggling_does_not_remove_rows_just_changes_badge(self):
        """Toggling exit-authority changes badges, NOT row visibility."""
        ctx_per_sym = build_port_row_context(
            port_state=_make_port_state(), exit_authority="per_symphony"
        )
        ctx_port = build_port_row_context(
            port_state=_make_port_state(), exit_authority="port_level"
        )
        # Both contexts are valid; only the badge differs
        assert ctx_per_sym["account_id"] == ctx_port["account_id"]
        assert ctx_per_sym["exit_authority_badge"]["is_authoritative"] is False
        assert ctx_port["exit_authority_badge"]["is_authoritative"] is True


class TestPortTriggerGlyph:
    """AC-P2.9.5: Port-trigger glyph on selected symphony's per-symphony row."""

    def test_port_trigger_glyph_present_on_selected_symphony(self):
        """
        AC-P2.9.5: On port-driven exit cycle, selected symphony's row gets
        port-trigger glyph (indigo [P] pill, not slate-500 text suffix).
        """
        trigger_id = "port-trig-uuid-001"
        ctx = build_per_symphony_row_context(
            symphony_state={
                **_make_symphony_state("sym-selected", triggered=False),
                "port_trigger_id": trigger_id,
                "port_selection_rationale": {
                    "target_reduction": [{"ticker": "SPY", "amount_usd": 5000.0}],
                    "top3_scores": [
                        {"symphony_id": "sym-selected", "score": 100.0},
                    ],
                },
            },
            exit_authority="port_level",
        )
        assert ctx.get("port_trigger_glyph") is not None, (
            "AC-P2.9.5: selected symphony row must have port_trigger_glyph"
        )
        glyph = ctx["port_trigger_glyph"]
        assert glyph.get("color") == "indigo", (
            "AC-P2.9.5: port-trigger glyph must be indigo (not slate-500 text)"
        )
        assert glyph.get("tooltip") is not None, (
            "AC-P2.9.5: port-trigger glyph must have tooltip with rationale"
        )

    def test_no_port_trigger_glyph_on_non_selected_symphony(self):
        """Non-selected symphony rows must NOT show the port-trigger glyph."""
        ctx = build_per_symphony_row_context(
            symphony_state=_make_symphony_state("sym-not-selected"),
            exit_authority="port_level",
        )
        assert ctx.get("port_trigger_glyph") is None, (
            "AC-P2.9.5: non-selected symphony must not show port-trigger glyph"
        )

    def test_port_trigger_glyph_tooltip_includes_required_data(self):
        """AC-P2.9.5: Tooltip includes port_trigger_id, target_reduction summary, top-3 scores."""
        trigger_id = "port-trig-uuid-002"
        ctx = build_per_symphony_row_context(
            symphony_state={
                **_make_symphony_state("sym-s2"),
                "port_trigger_id": trigger_id,
                "port_selection_rationale": {
                    "target_reduction": [
                        {"ticker": "SPY", "amount_usd": 8000.0, "reason_for_this_ticker": "hwm_drawdown"}
                    ],
                    "top3_scores": [
                        {"symphony_id": "sym-s2", "score": 50.0},
                        {"symphony_id": "sym-other", "score": 200.0},
                    ],
                },
            },
            exit_authority="port_level",
        )
        tooltip = ctx["port_trigger_glyph"]["tooltip"]
        assert trigger_id in str(tooltip) or tooltip.get("port_trigger_id") == trigger_id
        assert "target_reduction" in tooltip or "SPY" in str(tooltip)
        top3 = tooltip.get("top3_scores") or tooltip.get("candidate_scores")
        assert top3 is not None and len(top3) >= 1


class TestXSSProtection:
    """Security: all new dashboard cells must be XSS-safe (AC spec security section)."""

    def test_port_row_account_id_is_escaped(self):
        """account_id from port_state must be escaped before rendering."""
        ctx = build_port_row_context(
            port_state={**_make_port_state(), "account_id": "<script>xss</script>"},
            exit_authority="per_symphony",
        )
        rendered_id = ctx.get("account_id_display") or ctx.get("account_id")
        assert "<script>" not in str(rendered_id), (
            "account_id must not contain unescaped HTML in dashboard context"
        )
