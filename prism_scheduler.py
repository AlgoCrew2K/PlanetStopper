"""
prism_scheduler.py — Market Prism Phase 4 nightly scheduler wrapper.

Invoked by Windows Task Scheduler daily at 03:00 local time via schedule_prism.ps1.
Implements Option B (OS-level scheduling, daemon-decoupled) per DE-PRISM-004.

Responsibilities:
  1. Load .env from the project root (credentials for Claude + DB)
  2. Idempotency guard: skip if today's MARKET_PRISM row already exists in the DB
  3. Invoke prism-synthesizer via headless `claude` subprocess
  4. Bounded retry: up to MAX_ATTEMPTS attempts with capped exponential backoff
  5. D-1 error contract: only type(exc).__name__ is logged — never raw messages or paths

Exit codes:
  0  — success (either today's row already existed, or subprocess completed successfully)
  1  — failure (all MAX_ATTEMPTS exhausted without a successful run)
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Named constants — no magic numbers
# ---------------------------------------------------------------------------

MAX_ATTEMPTS: int = 3           # Max subprocess invocations per run
BACKOFF_BASE_SECONDS: int = 30  # First retry wait
BACKOFF_CAP_SECONDS: int = 60   # Maximum wait between retries

# Project root: the directory containing this script
_PROJECT_ROOT: Path = Path(__file__).parent.resolve()

# ---------------------------------------------------------------------------
# Dependency wrappers (patchable in tests)
# ---------------------------------------------------------------------------


def _load_env() -> None:
    """Load .env from the project root into os.environ using python-dotenv."""
    try:
        from dotenv import load_dotenv  # noqa: PLC0415
        load_dotenv(dotenv_path=_PROJECT_ROOT / ".env", override=False)
    except Exception:  # noqa: BLE001
        # dotenv missing or unreadable — log type only (D-1), continue with current env
        pass


def _get_summary() -> dict | None:
    """Return the most recent MARKET_PRISM summary row, or None."""
    sys.path.insert(0, str(_PROJECT_ROOT))
    import database  # noqa: PLC0415
    return database.get_latest_market_prism_summary()


def _is_todays_row(row: dict | None) -> bool:
    """Return True if row's created_at date equals today UTC."""
    if row is None:
        return False
    created_at_str = row.get("created_at", "")
    try:
        # created_at is stored as "YYYY-MM-DD HH:MM:SS" (UTC)
        row_date = datetime.strptime(created_at_str[:10], "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        return row_date == today
    except Exception:  # noqa: BLE001
        return False


def _run_prism() -> bool:
    """
    Invoke prism-synthesizer via headless claude subprocess.

    Returns True on success (returncode == 0), False on failure.
    Raises no exceptions — all errors are logged as type-only (D-1).
    """
    cmd = [
        "claude",
        "-p",
        "--agent",
        "prism-synthesizer",
        "--dangerously-skip-permissions",
        "--model",
        "opus",
        "Run the Market Prism nightly run.",
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(_PROJECT_ROOT),
            env=os.environ.copy(),
        )
        return result.returncode == 0
    except Exception as exc:  # noqa: BLE001
        # D-1: log type only — never exc message or path
        print(f"[prism_scheduler] SubprocessError: {type(exc).__name__}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the Market Prism nightly scheduler. Exits 0 on success, 1 on failure."""
    _load_env()

    # --- Idempotency guard ---
    try:
        summary = _get_summary()
    except Exception as exc:  # noqa: BLE001
        print(f"[prism_scheduler] DB check failed: {type(exc).__name__}", file=sys.stderr)
        summary = None  # treat as no row — attempt the run

    if _is_todays_row(summary):
        run_id = summary.get("raw_response", {}).get("run_id", "unknown") if summary else "unknown"
        print(f"[prism_scheduler] Already ran today (run_id: {run_id}). Skipping.")
        sys.exit(0)

    # --- Bounded retry loop ---
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"[prism_scheduler] Attempt {attempt}/{MAX_ATTEMPTS}")
        success = _run_prism()
        if success:
            print("[prism_scheduler] Run completed successfully.")
            sys.exit(0)

        # Failure — log attempt and backoff before next try
        print(
            f"[prism_scheduler] Attempt {attempt} failed (SubprocessError). "
            f"{'Retrying...' if attempt < MAX_ATTEMPTS else 'Max attempts exhausted.'}",
            file=sys.stderr,
        )
        if attempt < MAX_ATTEMPTS:
            wait = min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_CAP_SECONDS)
            time.sleep(wait)

    # All attempts exhausted
    sys.exit(1)


if __name__ == "__main__":
    main()
