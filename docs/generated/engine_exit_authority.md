# engine/exit_authority

> Display helpers for the exit-authority badge and restart-notice context in the dashboard. No math, no DB writes, no side effects.

**Source:** `engine/exit_authority.py`
**Last updated:** 2026-05-27

## Overview

Sprint 3 SITE-C1 removed the decision-path functions (`get_exit_authority`, `validate_exit_authority`, `is_authoritative`, `write_exit_authority_to_env`) from this module. Only two display helpers remain. See `docs/audit/sprint-3-port-removal-manifest.md §3` for the removal record.

The module reads `EXIT_AUTHORITY` from the environment (values: `"per_symphony"` or `"port_level"`). The authority value is display-only; no per-minute dispatch decision is derived from it.

## API Reference

### `get_exit_authority_badge_context() → dict`

Builds the active-authority badge context for the dashboard header.

Reads `EXIT_AUTHORITY` from the environment (default: `"per_symphony"`).

**Returns:** `dict` with keys:
| Key | Type | Description |
|-----|------|-------------|
| `is_degraded` | `bool` | `True` for unrecognized authority values |
| `label` | `str` | `"PER-SYMPHONY"`, `"PORT-LEVEL"`, or `"PER-SYMPHONY-fallback"` |
| `color` | `str` | `"slate"`, `"indigo"`, or `"amber"` |
| `border_style` | `str` | Same as `color` |
| `authority` | `str` | `"per_symphony"` or `"port_level"` |

**Behavior:**
- `per_symphony` → neutral (slate) styling, `is_degraded=False`
- `port_level` → indigo styling, `is_degraded=False`
- Unrecognized value → amber styling, `is_degraded=True`, label `"PER-SYMPHONY-fallback"`, authority `"per_symphony"`

---

### `build_restart_notice_context(toggle_changed_at: str | None, daemon_started_at: str) → dict`

Builds the restart-notice context dict for the settings UI.

Restart is required when `daemon_started_at <= toggle_changed_at`. Clears when `daemon_started_at` strictly exceeds `toggle_changed_at`. Equal timestamps still require restart (must EXCEED, not merely equal). Comparison is lexicographic on ISO 8601 UTC strings.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `toggle_changed_at` | `str \| None` | ISO 8601 timestamp of last authority toggle; `None` = never changed |
| `daemon_started_at` | `str` | ISO 8601 timestamp of daemon startup |

**Returns:** `dict` with keys:
| Key | Type | Description |
|-----|------|-------------|
| `restart_required` | `bool` | `True` when a restart is needed to activate the toggle |
| `toggle_changed_at` | `str \| None` | Passed through |
| `daemon_started_at` | `str` | Passed through |

## Internal Dependencies

- `os` — reads `EXIT_AUTHORITY` environment variable
- `logging` — module-level logger (no active logging in public functions)
