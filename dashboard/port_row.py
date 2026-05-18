"""
Dashboard context builders for dual-altitude port-level row rendering (AC-P2.9.*).

No I/O, no DB access, no math — pure context construction for Jinja templates.
All external values (account_id, triggered_reason) are HTML-escaped before rendering.
"""

from __future__ import annotations

import html


def _badge_for_row(row_altitude: str, exit_authority: str) -> dict:
    """
    Build the per-row AUTHORITATIVE/OBSERVED-ONLY badge context (AC-P2.9.3).

    A row is authoritative iff its altitude matches the active exit_authority.
    Badge sits in the LEFT column, not collocated with the rose staleness alarm (AC-P2.2.5, BC H6).
    """
    is_authoritative = row_altitude == exit_authority
    return {
        "is_authoritative": is_authoritative,
        "label": "AUTHORITATIVE" if is_authoritative else "OBSERVED-ONLY",
        "color": "indigo" if is_authoritative else "slate-500",
        "column": "left",
    }


def build_port_row_context(port_state: dict, exit_authority: str) -> dict:
    """
    Build template context for the pinned port-level row (AC-P2.9.2).

    Renders above per-symphony rows for each account.
    Both altitudes render regardless of exit_authority toggle (AC-P2.9.4).
    """
    raw_account_id = port_state.get("account_id", "")
    escaped_account_id = html.escape(str(raw_account_id))

    raw_reason = port_state.get("triggered_reason")
    escaped_reason = html.escape(str(raw_reason)) if raw_reason is not None else None

    return {
        "row_type": "port_level",
        "account_id": escaped_account_id,
        "account_id_display": escaped_account_id,
        "port_hwm": port_state.get("high_water_mark"),
        "port_current_return": port_state.get("current_return"),
        "port_armed": port_state.get("armed", False),
        "port_para_armed": port_state.get("para_armed", False),
        "port_triggered": port_state.get("triggered", False),
        "triggered_reason": escaped_reason,
        "last_target_reduction": port_state.get("last_target_reduction_json"),
        "last_selected_symphony_id": port_state.get("last_selected_symphony_id"),
        "exit_authority_badge": _badge_for_row("port_level", exit_authority),
    }


def build_per_symphony_row_context(symphony_state: dict, exit_authority: str) -> dict:
    """
    Build template context for a per-symphony row (AC-P2.9.1, AC-P2.9.3, AC-P2.9.5).

    OBSERVED-ONLY badge when EXIT_AUTHORITY=port_level (per spec; per-symphony state
    computes + displays but doesn't fire exits).

    Port-trigger glyph (indigo [P] pill) appears when port_trigger_id is present
    on this symphony's state (AC-P2.9.5). Tooltip includes port_trigger_id,
    target_reduction summary, and top-3 candidate scores.
    """
    ctx: dict = {
        "row_type": "per_symphony",
        "symphony_id": symphony_state.get("symphony_id"),
        "exit_authority_badge": _badge_for_row("per_symphony", exit_authority),
        "port_trigger_glyph": None,
    }

    trigger_id = symphony_state.get("port_trigger_id")
    if trigger_id:
        rationale = symphony_state.get("port_selection_rationale") or {}
        target_reduction = rationale.get("target_reduction")
        top3 = rationale.get("top3_scores")
        ctx["port_trigger_glyph"] = {
            "color": "indigo",
            "label": "[P]",
            "tooltip": {
                "port_trigger_id": trigger_id,
                "target_reduction": target_reduction,
                "top3_scores": top3,
                "candidate_scores": top3,
            },
        }

    return ctx
