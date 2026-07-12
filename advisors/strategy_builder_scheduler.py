"""Weekly Strategy Builder Scheduler — Component 4 (AC-18).

Standalone script (modelled on prism_scheduler.py) that runs the real builder
unattended for each of the four objectives and persists survivors via the
UNCHANGED downstream persist path (propose_strategies → insert_advisor_observation).

AC-18 requirements:
  - Named bounded-retry constant (MAX_ATTEMPTS — no magic numbers).
  - Same-ISO-week idempotency guard (_already_ran_this_week seam, patchable by tests).
  - D-1 never-raises: a builder failure does not crash the scheduler.
  - Persistence via the unchanged propose_strategies path.

Invoke:
  python -m advisors.strategy_builder_scheduler   # or directly as a script

Design constraints:
  - D-1 contract: reason strings carry ONLY type(exc).__name__ — no key/path/message leak.
  - Advisory-only: no LIVE_EXECUTION, no Composer write call, no _SETTINGS_WRITE_ALLOWLIST.
  - Off-execution-path: not imported from alpha_bot_execution.py.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants — no magic numbers
# ---------------------------------------------------------------------------

# Maximum attempts to run the builder for a given objective before giving up.
MAX_ATTEMPTS: int = 3

# ---------------------------------------------------------------------------
# Idempotency seam
# ---------------------------------------------------------------------------


def _already_ran_this_week() -> bool:
    """Return True if the builder already ran successfully this ISO week.

    Checks for any advisor_observations row with advisor_role='STRATEGY_BUILDER'
    created during the current ISO week (Monday 00:00 UTC → Sunday 23:59 UTC).

    This is a patchable seam: tests monkeypatch it to True (same-week no-op) or
    False (fresh run). Never raises — degradation defaults to False (run anyway).
    """
    try:
        import database  # noqa: PLC0415 - CC-2 lazy; no state-DB on execution path

        # ISO week bounds: Monday 00:00 UTC of the current week.
        now = datetime.now(tz=UTC)
        iso_year, iso_week, _ = now.isocalendar()

        # Fetch recent STRATEGY_BUILDER observations — we only need to check
        # whether any row exists from this ISO week.
        # AC-A1: the real signature is get_advisor_observations_for_role(advisor_role,
        # limit) (database.py:1133) — the prior get_advisor_observations_for_symphony(
        # symphony_id=..., advisor_role=..., limit=...) call raised TypeError (that
        # function takes only symphony_id), silently swallowed by the outer except
        # below, so the dedup guard always degraded to False.
        rows = database.get_advisor_observations_for_role("STRATEGY_BUILDER", limit=50)

        for row in rows:
            created_at = row.get("created_at") or row.get("timestamp") or ""
            if not created_at:
                continue
            try:
                # Parse ISO 8601 or SQLite datetime string.
                if isinstance(created_at, str):
                    # SQLite stores as "YYYY-MM-DD HH:MM:SS[.ffffff]"
                    created_at = created_at.replace(" ", "T")
                    if not created_at.endswith("Z") and "+" not in created_at:
                        created_at += "+00:00"
                    row_dt = datetime.fromisoformat(created_at)
                else:
                    row_dt = created_at
                if not row_dt.tzinfo:
                    row_dt = row_dt.replace(tzinfo=UTC)
                row_year, row_week, _ = row_dt.isocalendar()
                if row_year == iso_year and row_week == iso_week:
                    return True
            except Exception:
                continue

        return False

    except Exception as exc:
        # D-1: degrade to False (run anyway) on any DB error.
        logger.debug(
            "_already_ran_this_week: check failed (%s); defaulting to False", type(exc).__name__
        )
        return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_weekly_build() -> None:
    """Run the real builder for all four objectives; skip if already ran this week.

    Orchestration contract:
      1. Idempotency check: if _already_ran_this_week() → log + return (no-op).
      2. For each of the four objectives: call propose_strategies; on any failure
         log type(exc).__name__ only (D-1) and continue to the next objective.
      3. Never raises (D-1).
    """
    try:
        # CC-2 lazy imports — off-execution-path.
        import advisors.build_plan_generator as _bpg  # noqa: PLC0415
        from advisors.strategy_builder_engine import (  # noqa: PLC0415
            Objective,
            ScreenConfig,
            propose_strategies,
        )
    except Exception as exc:
        logger.error("strategy_builder_scheduler: import failed (%s)", type(exc).__name__)
        return

    # Idempotency guard: same-ISO-week is a no-op.
    if _already_ran_this_week():
        logger.info("strategy_builder_scheduler: already ran this week — skipping")
        return

    logger.info("strategy_builder_scheduler: starting weekly build for all objectives")

    for objective in Objective:
        # Objective-matched Atlas community injection (AC-13/EDGE-2 dual-mode).
        # load_atlas_candidates is D-1 (never-raises) and bill-protected (force_refresh=False
        # inside). Guard the call site too so any unexpected raise degrades to built-new-only.
        community_candidates: list = []
        try:
            community_candidates = _bpg.load_atlas_candidates(objective)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "strategy_builder_scheduler: Atlas load skipped for objective=%s (%s) "
                "— built-new only",
                objective.value,
                type(exc).__name__,
            )
            community_candidates = []

        attempt = 0
        while attempt < MAX_ATTEMPTS:
            attempt += 1
            try:
                logger.info(
                    "strategy_builder_scheduler: running objective=%s (attempt %d/%d)",
                    objective.value,
                    attempt,
                    MAX_ATTEMPTS,
                )
                propose_strategies(
                    objective=objective,
                    universe=[],  # self-source from C1 (Q2-A)
                    screen_config=ScreenConfig(),
                    live_returns=[],
                    community_candidates=community_candidates,
                )
                logger.info("strategy_builder_scheduler: objective=%s completed", objective.value)
                break  # success — no retry needed
            except Exception as exc:
                # D-1: log exception class name only — no key/path/message leak.
                logger.warning(
                    "strategy_builder_scheduler: objective=%s attempt %d failed (%s)",
                    objective.value,
                    attempt,
                    type(exc).__name__,
                )
                if attempt >= MAX_ATTEMPTS:
                    logger.error(
                        "strategy_builder_scheduler: objective=%s exhausted %d attempts",
                        objective.value,
                        MAX_ATTEMPTS,
                    )
                    # D-1: do NOT re-raise — continue to the next objective.

    logger.info("strategy_builder_scheduler: weekly build complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_weekly_build()
