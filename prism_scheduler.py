"""
prism_scheduler.py — Market Prism Phase 4 nightly scheduler wrapper.

Invoked by Windows Task Scheduler daily at 03:00 local time via schedule_prism.ps1.
Implements Option B (OS-level scheduling, daemon-decoupled) per DE-PRISM-004.

Responsibilities:
  1. Load .env from the project root (credentials for Claude + DB)
  2. Idempotency guard: skip if today's MARKET_PRISM row already exists in the DB
  3. Spawn all 6 Prism agents (prism-synthesizer + 5 analysts) via headless vanilla `claude -p` subprocess
  4. Bounded retry: up to MAX_ATTEMPTS attempts with capped exponential backoff
  5. D-1 error contract: only type(exc).__name__ is logged — never raw messages or paths

Exit codes:
  0  — success (either today's row already existed, or subprocess completed successfully)
  1  — failure (all MAX_ATTEMPTS exhausted without a successful run)
"""

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Named constants — no magic numbers
# ---------------------------------------------------------------------------

MAX_ATTEMPTS: int = 3           # Max subprocess invocations per run
BACKOFF_BASE_SECONDS: int = 30  # First retry wait
BACKOFF_CAP_SECONDS: int = 60   # Maximum wait between retries
# A full 6-agent Opus council (synthesizer + 5 analysts, multi-round Q&A +
# conditional debate) realistically costs $5-10 per run. 15.0 is a
# runaway-prevention ceiling, not a target.
MAX_BUDGET_USD: float = 15.0

# Prompt passed to the vanilla-primary headless claude session. The primary
# spawns ALL 6 agents itself — prism-synthesizer has no Agent/spawn tool and
# cannot spawn teammates. prism-synthesizer only coordinates the pre-spawned
# analysts via SendMessage. The completion guard prevents the session from
# returning before the MARKET_PRISM row is written.
PRISM_RUN_PROMPT: str = (
    "You are running the Market Prism nightly council. "
    "Spawn all 6 agents: prism-synthesizer (team lead), "
    "prism-technicals-analyst, prism-sentiment-analyst, "
    "prism-derivatives-analyst, prism-macro-analyst, "
    "prism-fundamentals-analyst. "
    "prism-synthesizer coordinates the analyst agents: it messages each one "
    "via SendMessage for their reads, runs Q&A and conditional debate "
    "(up to 3 rounds), integrates all outputs into ONE MARKET_PRISM "
    "observation, and writes it to the DB via prism_audit_write. "
    "Do NOT return your final answer until prism-synthesizer confirms the "
    "MARKET_PRISM row has been written to the DB. "
    "This is fully unattended — complete the entire run without waiting for "
    "any user input."
)

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
        # dotenv missing or unreadable — silent fallback (no logging, to avoid
        # echoing env paths; stricter than D-1), continue with current env
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


def _persist_spend(run_id: str, stdout: str) -> None:
    """Parse subprocess JSON stdout for total_cost_usd and persist to prism_audit_log.

    Reads total_cost_usd (CC 2.1.181+ envelope) with a tolerant fallback to the
    legacy cost_usd key so older/local CC builds still log.

    Non-fatal — a parse or DB failure is logged as type-only (D-1) and swallowed.
    """
    try:
        parsed = json.loads(stdout)
        cost = parsed.get("total_cost_usd") or parsed.get("cost_usd")
        if cost is not None:
            sys.path.insert(0, str(_PROJECT_ROOT))
            import database  # noqa: PLC0415
            database.insert_prism_audit_entry(
                run_id=run_id,
                agent_role="LAUNCHER",
                phase="spend_log",
                content=json.dumps({"total_cost_usd": cost}),
            )
    except Exception as exc:  # noqa: BLE001
        # D-1: log type only — never exc message or path
        print(f"[prism_scheduler] SpendLogError: {type(exc).__name__}", file=sys.stderr)


def _run_prism(run_id: str = "unknown") -> bool:
    """
    Invoke the 6-agent Prism council via a vanilla headless `claude -p` subprocess.

    The primary spawns all 6 agents (prism-synthesizer + 5 analysts); prism-synthesizer
    coordinates the pre-spawned analysts via SendMessage, integrates outputs, and writes
    the MARKET_PRISM row. Returns True on success (returncode == 0), False on failure.
    Raises no exceptions — all errors are logged as type-only (D-1).
    """
    cmd = [
        "claude",
        "-p",
        "--dangerously-skip-permissions",
        "--model",
        "claude-opus-4-8",
        "--max-budget-usd",
        str(MAX_BUDGET_USD),
        "--output-format",
        "json",
        PRISM_RUN_PROMPT,
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(_PROJECT_ROOT),
            env=os.environ.copy(),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            _persist_spend(run_id, result.stdout)
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

    run_id = str(uuid.uuid4())

    # --- Bounded retry loop ---
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"[prism_scheduler] Attempt {attempt}/{MAX_ATTEMPTS}")
        success = _run_prism(run_id=run_id)
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
