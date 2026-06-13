# Market Prism — Phase 4: Unattended Scheduling + Graceful Fallback

**Epic:** [Market Prism](market-prism-overview.md) · **Status:** 🔴 blocked by Phase 3
(operator must sign off on the observed proof run first).

## Goal

Wire the proven Prism team to run **blind, off-hours, daily**, with a graceful fallback when a
run errors — so the operator wakes up to a fresh overnight market read without anyone driving
it.

## Execution constraint (the crux)

An Agent Team is a Claude Code construct — the Flask daemon CANNOT spawn one. So scheduling is
NOT a daemon Python job. The Prism run must be driven by a **Claude Code session**. Two
candidate mechanisms (decide AFTER the Phase-3 proof, based on what proved reliable):

- **Option A — daemon shells to headless `claude`:** the existing daemon scheduler (it already
  has a 03:00 `schedule.every().day` slot from Cycle 4) shells out to a headless `claude` run
  that executes the Prism orchestration runbook, then exits. Daemon owns the cadence; Claude
  owns the team.
- **Option B — scheduled cron / cloud routine:** an OS-level / cloud scheduled Claude Code
  routine independent of the daemon. Avoids coupling the team lifecycle to the daemon process.

Either way: a **fresh short-lived team per run** (avoids marathon-session compaction/husk
fragility).

## Acceptance criteria

1. The Prism runs unattended on a daily off-hours cadence and writes a fresh `MARKET_PRISM`
   row + full audit trail each run, with a unique `run_id`.
2. **Graceful fallback:** a failed/partial run does NOT crash the daemon, does NOT leave a
   half-written `MARKET_PRISM` row; it records the failure (D-1 type-only) in the audit log and
   leaves a clear `limited-inputs`/error-state report. The Overview tab degrades informatively.
3. Idempotency / no double-run: two triggers in one window don't produce duplicate conflicting
   reports for the same logical day.
4. Spend is bounded/observable: each run's Opus spend is recorded (audit log or a run summary).
5. A first few unattended runs are spot-checked by the PM (nightly live-functional bar) before
   declaring it trustworthy.

## Open questions (decide after Phase 3)

- Exact off-hours time (Cycle-4 uses 03:00) and timezone handling (US Central host, US market).
- Where the headless `claude` invocation authenticates / which model budget it uses.
- Retry policy on transient API failures (bounded — recall the `_fetch_with_backoff`
  infinite-loop crash; any retry must be hard-bounded).

## Hard rules

- Bounded retries only (the persistent-429 infinite-loop was a PC-crash root cause).
- Off-execution-path, advisory-only.
- Nightly live-functional verification by the PM is the acceptance bar before "trusted".

## Dependencies

Phase 3 operator sign-off. Deploy target (future) = shared DO droplet `167.99.3.130` on
`:8090` — relevant if scheduling moves off the local box.
