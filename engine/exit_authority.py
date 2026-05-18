"""
EXIT_AUTHORITY resolver (AC-P2.2.*)

Reads, validates, and communicates the exit-authority toggle.
No math. No DB writes. No side effects except env file write.

Valid values: "per_symphony" (default) | "port_level"
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Named constants — no hardcoded inline strings in comparisons (AC-P2.2.3)
EXIT_AUTHORITY_PER_SYMPHONY = "per_symphony"
EXIT_AUTHORITY_PORT_LEVEL = "port_level"
_VALID_AUTHORITIES = frozenset({EXIT_AUTHORITY_PER_SYMPHONY, EXIT_AUTHORITY_PORT_LEVEL})

_ENV_KEY = "EXIT_AUTHORITY"


def get_exit_authority() -> str:
    """
    Read EXIT_AUTHORITY from environment with default per_symphony (AC-P2.2.3).

    Unrecognized values fall back to per_symphony with ERROR-level log (AC-P2.2.2
    fail-DEGRADED posture). Never raises.
    """
    raw = os.getenv("EXIT_AUTHORITY", EXIT_AUTHORITY_PER_SYMPHONY)
    if raw not in _VALID_AUTHORITIES:
        logger.error(
            "Unrecognized EXIT_AUTHORITY=%r; falling back to %r (fail-DEGRADED)",
            raw,
            EXIT_AUTHORITY_PER_SYMPHONY,
        )
        return EXIT_AUTHORITY_PER_SYMPHONY
    return raw


def validate_exit_authority(
    value: str,
    account_id: str | None = None,
    has_port_params: bool = True,
) -> dict:
    """
    Validate an EXIT_AUTHORITY value and return a result dict.

    Returns {"valid": bool, "error_type": str | None, "authority": str}

    Error types (AC-P2.2.2):
      - "fail_degraded": unrecognized literal — falls back to per_symphony
      - "fail_stop": port_level requested but account has no port-level params
    """
    if value not in _VALID_AUTHORITIES:
        return {
            "valid": False,
            "error_type": "fail_degraded",
            "authority": EXIT_AUTHORITY_PER_SYMPHONY,
            "message": f"Unrecognized EXIT_AUTHORITY={value!r}; falling back to per_symphony",
        }

    if value == EXIT_AUTHORITY_PORT_LEVEL and not has_port_params:
        return {
            "valid": False,
            "error_type": "fail_stop",
            "authority": EXIT_AUTHORITY_PER_SYMPHONY,
            "message": (
                f"EXIT_AUTHORITY=port_level requested for account {account_id!r} "
                "but no port-level params found — refusing to enter port-level mode (fail-STOP)"
            ),
        }

    return {"valid": True, "error_type": None, "authority": value, "message": None}


def is_authoritative(altitude: str, exit_authority: str) -> bool:
    """
    Return True iff the given altitude is the exit-authoritative altitude (AC-P2.2.6).

    Per-symphony is authoritative iff EXIT_AUTHORITY=per_symphony.
    Port-level is authoritative iff EXIT_AUTHORITY=port_level.
    Non-authoritative altitude's triggered=True is NEVER acted upon.
    """
    return altitude == exit_authority


def write_exit_authority_to_env(value: str, env_path: str = ".env") -> None:
    """
    Write EXIT_AUTHORITY=<value> to the .env file (AC-P2.2.4).

    Uses a simple line-replace so existing keys are updated in-place.
    Does NOT restart the daemon — restart is operator-triggered.
    """
    try:
        try:
            with open(env_path, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []

        key_found = False
        new_lines = []
        for line in lines:
            if line.startswith(f"{_ENV_KEY}=") or line.startswith(f"{_ENV_KEY} ="):
                new_lines.append(f"{_ENV_KEY}={value}\n")
                key_found = True
            else:
                new_lines.append(line)

        if not key_found:
            new_lines.append(f"{_ENV_KEY}={value}\n")

        with open(env_path, "w") as f:
            f.writelines(new_lines)
    except OSError as exc:
        logger.error("Failed to write EXIT_AUTHORITY to %s: %s", env_path, exc)


def get_exit_authority_badge_context() -> dict:
    """
    Build the active-authority badge context for the dashboard header (AC-P2.2.5, AC-P2.12.4).

    Reads EXIT_AUTHORITY from the environment. Unrecognized values produce a degraded
    badge with amber border + 'PER-SYMPHONY-fallback' text (AC-P2.12.4).
    Normal per_symphony: neutral styling. Normal port_level: indigo styling.
    Badge is always in the LEFT column adjacent to the DRY-RUN badge (BC H6).
    """
    raw = os.getenv("EXIT_AUTHORITY", EXIT_AUTHORITY_PER_SYMPHONY)
    if raw not in _VALID_AUTHORITIES:
        return {
            "is_degraded": True,
            "label": "PER-SYMPHONY-fallback",
            "color": "amber",
            "border_style": "amber",
            "authority": EXIT_AUTHORITY_PER_SYMPHONY,
        }
    if raw == EXIT_AUTHORITY_PORT_LEVEL:
        return {
            "is_degraded": False,
            "label": "PORT-LEVEL",
            "color": "indigo",
            "border_style": "indigo",
            "authority": EXIT_AUTHORITY_PORT_LEVEL,
        }
    return {
        "is_degraded": False,
        "label": "PER-SYMPHONY",
        "color": "slate",
        "border_style": "slate",
        "authority": EXIT_AUTHORITY_PER_SYMPHONY,
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
