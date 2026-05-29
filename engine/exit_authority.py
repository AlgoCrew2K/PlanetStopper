"""
EXIT_AUTHORITY display helpers (AX-2 display-only surface).

Decision-path functions (get_exit_authority, validate_exit_authority,
is_authoritative, write_exit_authority_to_env) were removed in Sprint 3
SITE-C1 — see docs/audit/sprint-3-port-removal-manifest.md §3.

Only the dashboard badge and restart-notice helpers remain.
No math. No DB writes. No side effects.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_ENV_KEY = "EXIT_AUTHORITY"


def get_exit_authority_badge_context() -> dict:
    """
    Build the active-authority badge context for the dashboard header (AC-P2.2.5, AC-P2.12.4).

    Reads EXIT_AUTHORITY from the environment. Unrecognized values produce a degraded
    badge with amber border + 'PER-SYMPHONY-fallback' text (AC-P2.12.4).
    Normal per_symphony: neutral styling. Normal port_level: indigo styling.
    Badge is always in the LEFT column adjacent to the DRY-RUN badge (BC H6).
    """
    raw = os.getenv(_ENV_KEY, "per_symphony")
    if raw not in ("per_symphony", "port_level"):
        return {
            "is_degraded": True,
            "label": "PER-SYMPHONY-fallback",
            "color": "amber",
            "border_style": "amber",
            "authority": "per_symphony",
        }
    if raw == "port_level":
        return {
            "is_degraded": False,
            "label": "PORT-LEVEL",
            "color": "indigo",
            "border_style": "indigo",
            "authority": "port_level",
        }
    return {
        "is_degraded": False,
        "label": "PER-SYMPHONY",
        "color": "slate",
        "border_style": "slate",
        "authority": "per_symphony",
    }


def build_restart_notice_context(
    toggle_changed_at: str | None,
    daemon_started_at: str,
) -> dict:
    """
    Build the restart-notice context dict for the settings UI (AC-P2.2.4).

    Restart is required when daemon_started_at <= toggle_changed_at.
    Clears (restart_required=False) when daemon_started_at EXCEEDS toggle_changed_at
    (positive restart-observed confirmation per panel BC H7).
    Equal timestamps still require restart — must EXCEED, not merely equal.

    toggle_changed_at=None means the toggle was never changed; no restart needed.
    Both non-None values are ISO 8601 strings; lexicographic comparison is valid
    for UTC timestamps.
    """
    if toggle_changed_at is None:
        restart_required = False
    else:
        restart_required = daemon_started_at <= toggle_changed_at
    return {
        "restart_required": restart_required,
        "toggle_changed_at": toggle_changed_at,
        "daemon_started_at": daemon_started_at,
    }
