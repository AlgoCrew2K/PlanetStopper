"""
RED tests for Amendment B1 (Critical): MC-gate-before-commit in port-mode selection path.

Amendment B1: Selected-symphony exit currently sets triggered=True and BYPASSES the
per-constituent MC sanity gate. ADD: MC-gate-before-commit step in the selection path.
If the selected symphony's MC sanity gate would have blocked an exit at per-symphony
altitude, suppress the port-mode exit AND log + emit telemetry.

These tests target the resolver/selection integration in alpha_bot_execution.py
(or wherever the selection -> MC-check -> commit flow lives).

The import targets will FAIL until the implementation adds the MC gate step (RED intent).
"""

from __future__ import annotations

import pytest


# These imports WILL FAIL until implemented (RED intent)
from port_selector import select_symphony_with_mc_gate  # noqa: F401


def _make_candidate(symphony_id: str, exposure_usd: dict, mc_would_block: bool) -> dict:
    return {
        "symphony_id": symphony_id,
        "exposure_usd": exposure_usd,
        "total_value": sum(exposure_usd.values()),
        "position_open_date": "2024-01-01",
        "mc_sanity_gate_would_block": mc_would_block,
    }


class TestMCSanityGateBeforeCommit:
    """
    Amendment B1: MC gate must fire BEFORE the port-mode exit is committed.
    If the selected symphony's MC sanity gate would have blocked a per-symphony exit,
    the port-mode exit must be SUPPRESSED (not committed).
    """

    def test_mc_gate_suppresses_exit_when_would_block(self):
        """
        When the selected symphony's MC sanity gate would block an exit,
        select_symphony_with_mc_gate returns suppressed=True and does NOT commit the exit.
        """
        result = select_symphony_with_mc_gate(
            target_reduction=[{"ticker": "SPY", "amount_usd": 5000.0}],
            candidates=[
                _make_candidate("sym-blocked", {"SPY": 5000.0}, mc_would_block=True),
            ],
            over_shoot_penalty=1.0,
        )
        assert result["suppressed"] is True, (
            "Amendment B1: port-mode exit must be suppressed when MC gate would block"
        )
        assert result["selected_symphony_id"] is None, (
            "Amendment B1: no symphony is committed when MC gate suppresses"
        )

    def test_mc_gate_suppressed_includes_telemetry_reason(self):
        """Amendment B1: suppression must log + emit telemetry (result carries reason)."""
        result = select_symphony_with_mc_gate(
            target_reduction=[{"ticker": "SPY", "amount_usd": 5000.0}],
            candidates=[
                _make_candidate("sym-blocked", {"SPY": 5000.0}, mc_would_block=True),
            ],
            over_shoot_penalty=1.0,
        )
        assert result["suppressed"] is True
        assert result.get("suppression_reason") == "mc_sanity_gate_would_block", (
            "Amendment B1: suppression_reason must be set for telemetry"
        )
        assert result.get("suppressed_symphony_id") == "sym-blocked", (
            "Amendment B1: telemetry must record which symphony was suppressed"
        )

    def test_mc_gate_allows_exit_when_gate_passes(self):
        """When MC gate would NOT block, the exit proceeds normally."""
        result = select_symphony_with_mc_gate(
            target_reduction=[{"ticker": "SPY", "amount_usd": 5000.0}],
            candidates=[
                _make_candidate("sym-ok", {"SPY": 5000.0}, mc_would_block=False),
            ],
            over_shoot_penalty=1.0,
        )
        assert result["suppressed"] is False
        assert result["selected_symphony_id"] == "sym-ok"

    def test_mc_gate_fallback_to_second_candidate_when_first_blocked(self):
        """
        If the BEST-MATCH symphony is MC-blocked, the selection does NOT fall back
        to the second-best — the exit is suppressed entirely (Amendment B1 spec:
        suppress AND log, not fall back to next candidate).
        """
        result = select_symphony_with_mc_gate(
            target_reduction=[{"ticker": "SPY", "amount_usd": 5000.0}],
            candidates=[
                _make_candidate("sym-best-blocked", {"SPY": 5000.0}, mc_would_block=True),
                _make_candidate("sym-second", {"SPY": 4000.0}, mc_would_block=False),
            ],
            over_shoot_penalty=1.0,
        )
        # Amendment B1: suppress the exit entirely, do NOT silently pick second candidate
        assert result["suppressed"] is True
        assert result["selected_symphony_id"] is None

    def test_mc_gate_check_fires_after_selection(self):
        """MC gate fires on the SELECTED symphony (best-fit winner), not all candidates."""
        # sym-second is selected (best score). sym-best is not selected but MC-blocked.
        result = select_symphony_with_mc_gate(
            target_reduction=[{"ticker": "SPY", "amount_usd": 5000.0}],
            candidates=[
                _make_candidate("sym-poor-fit-mc-blocked", {"SPY": 1000.0}, mc_would_block=True),
                _make_candidate("sym-best-fit-mc-ok", {"SPY": 5000.0}, mc_would_block=False),
            ],
            over_shoot_penalty=1.0,
        )
        # sym-best-fit-mc-ok has the better L1 score (exact match); MC gate passes
        assert result["suppressed"] is False
        assert result["selected_symphony_id"] == "sym-best-fit-mc-ok"
