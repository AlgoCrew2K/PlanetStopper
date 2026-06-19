# Feature: Market Prism Phase 4 — Unattended Scheduling + Graceful Fallback
Status: ready
Created: 2026-06-13

## Summary

Wires the proven Market Prism team to run blind, off-hours, daily, with a graceful fallback when a run errors — so the operator wakes up to a fresh overnight market read without anyone driving it. Because an Agent Team is a Claude Code construct and the Flask daemon cannot spawn one, scheduling is driven by a Claude Code session (not a daemon Python job). Two candidate mechanisms are evaluated after the Phase-3 proof run: Option A (daemon shells to a headless `claude` invocation) or Option B (OS-level / cloud scheduled Claude Code routine). Whichever mechanism is chosen, a fresh short-lived team per run avoids marathon-session compaction/husk fragility, and bounded retries prevent the persistent-429 infinite-loop crash.

## Acceptance Criteria

- [ ] AC-1: The Prism runs unattended on a daily off-hours cadence and writes a fresh `MARKET_PRISM` `advisor_observations` row + full `prism_audit_log` trail each run with a unique `run_id`.
- [ ] AC-2: A failed/partial run does NOT crash the daemon, does NOT leave a half-written `MARKET_PRISM` row; it records the failure (D-1: `type(exc).__name__` only) in the audit log and produces a clear `limited-inputs`/error-state report that the Overview tab degrades informatively on.
- [ ] AC-3: Two triggers in one window do not produce duplicate conflicting reports for the same logical day (idempotency / no double-run).
- [ ] AC-4: Each run's Opus spend is recorded (audit log or a run-summary entry) and observable by the PM.
- [ ] AC-5: A first batch of unattended runs (≥3) is spot-checked by the PM (nightly live-functional bar: Overview tab renders, audit trail is complete, no crashes) before declaring the feature trusted.

## Architecture

**Files potentially changed (mechanism-dependent — finalize after Phase 3):**

*Option A — daemon shells to headless `claude`:*
- `app.py` — the existing `run_scheduler()` 03:00 slot already calls `advisors/lens_pipeline.py`; extend or replace this slot to invoke a headless `claude` session running the Prism orchestration runbook; the daemon owns cadence, Claude owns the team lifecycle. Lazy import + subprocess, consistent with CC-2 boundary.

*Option B — OS-level / cloud scheduled Claude Code routine:*
- No daemon change; a new scheduled job (cron, cloud scheduler, or Windows Task Scheduler on the local box) invokes `claude` with the Prism runbook. The daemon's 03:00 slot can remain for the existing lens pipeline or be removed.

**Common to both options:**
- Idempotency guard: before writing a new `MARKET_PRISM` row, check if a row with today's date already exists in `advisor_observations` — if so, skip (no double-write).
- Bounded retry wrapper: any retry on transient API failure must be hard-bounded (fixed max attempts + exponential backoff cap). Recall: the `_fetch_with_backoff` infinite-loop was a PC-crash root cause.
- Per-run spend logging: synthesizer (or orchestration glue) records total Opus token usage in the audit log or a dedicated `prism_run_summary` entry.
- Deploy note: deploy target is the shared DO droplet `167.99.3.130` on `:8090` (future); if scheduling moves off the local box, Option B is the cleaner separation.

**Integration points:**
- `advisors/lens_pipeline.py` — Cycle-4 data layer (unchanged; runs before the team)
- Phase-2 agent files + orchestration runbook
- `database.py` `get_latest_market_prism_summary()` — Cycle-5 Overview tab reads the latest row
- `app.py` `run_scheduler()` — may be extended (Option A) or left unchanged (Option B)

## Design-System Mapping

N/A — backend feature, no UI surface. (All 10 are backend/infra; the Cycle-5 Market Prism Overview UI already shipped separately.)

## Edge Cases

- **Transient API failure:** retry with hard-bounded backoff (finite max attempts). Log each attempt as `type(exc).__name__`. After max retries, produce `limited-inputs` report — never hang.
- **All lenses unavailable:** produce `verdict="limited-inputs"` report; Overview tab degrades informatively. This is not a crash condition.
- **Double-trigger in one window:** idempotency guard (check for today's row before writing) prevents duplicate conflicting reports. [PM-ASSUMED] "Same logical day" = UTC date of `created_at`.
- **Daemon crash while run is in flight:** the `MARKET_PRISM` row is written atomically by the synthesizer at the end; a mid-run daemon crash leaves no half-written row. The next scheduled run starts fresh.
- **Marathon session compaction / husk fragility:** each run spins up a fresh short-lived team per the locked design. No persistent session state.
- **Local box vs. DO droplet timezone:** off-hours time (e.g. 03:00) must account for host timezone (US Central) vs. US market schedule. [PM-ASSUMED] Finalize timezone handling after Phase-3.
- **Headless `claude` authentication (Option A):** the `ANTHROPIC_API_KEY` must be available in the subprocess environment. [PM-ASSUMED] Loaded from the daemon's `.env` via the existing env-loading pattern.
- **Spend runaway:** Opus multi-agent runs are accepted but bounded by the ≤3-round debate cap and the finite number of analysts (5 + synthesizer). Per-run spend logging makes overruns observable.

## Security Considerations

- **Bounded retries (hard rule):** the persistent-429 infinite-loop was a PC-crash root cause. Any retry logic MUST use a finite `max_attempts` constant and exponential backoff with a cap. Never a `while True` retry.
- **API key handling:** `ANTHROPIC_API_KEY` must not be logged or stored in the audit trail. The daemon's existing `.env` pattern applies. For Option A (subprocess), key is passed via environment, not CLI arg.
- **D-1 contract:** all error paths surface `type(exc).__name__` only — no raw exception strings in the `MARKET_PRISM` row, the audit log, the Overview tab, Discord, or any notification.
- **Authz / advisory-only:** off-execution-path; never touches `LIVE_EXECUTION`. The scheduled run writes to `advisor_observations` and `prism_audit_log` only.
- **Idempotency as a safety property:** the double-run guard prevents overwriting a clean report with a failed-run degenerate report if two triggers fire (e.g. a restart + scheduled trigger overlap).
- **No daemon blocking:** the scheduled mechanism (whichever option) must not block the Flask daemon's main thread or the :00 execution path. Option A uses subprocess (non-blocking from daemon perspective); Option B is fully decoupled.

## Testing Strategy

**For any new Python scheduling/integration glue (new codepath → Toxic Pair TDD):**
- `tests/ai_advisor/test_prism_scheduling.py` — idempotency guard: inserting a second run for the same date returns early without a new row; bounded retry: a mocked API that always returns 429 exhausts `max_attempts` and produces `limited-inputs` without hanging; Option-A subprocess invocation: subprocess is called with the correct env and runbook args (mock subprocess.run, assert args).

**Fixture provenance:** mock API failures via `unittest.mock`; mock existing rows via the `_isolate_db` fixture. No hardcoded response content — assert shape/verdict/entry presence.

**Run protocol:** `DB_PATH` set via `tests/conftest.py`; targeted run: `pytest tests/ai_advisor -n0 -o addopts= -p no:xdist`. No real Opus calls in unit tests.

**Live functional verification (PM):** spot-check ≥3 unattended runs — confirm Overview tab renders, audit trail is complete, no daemon crashes, Opus spend is logged (AC-5). This is the nightly live-functional bar per project memory.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Mechanism (Option A vs B) decided after Phase 3 | What proved reliable in the observed run informs the choice; do not commit to a mechanism before proof |
| Fresh short-lived team per run | Locked design: avoids marathon-session compaction/husk fragility; clean state each nightly run |
| Bounded retries hard rule | The persistent-429 infinite-loop was a PC-crash root cause; any retry must be finite |
| Idempotency guard before writing `MARKET_PRISM` row | Prevents duplicate conflicting reports on double-trigger |
| Per-run Opus spend logging | Makes runaway spend observable before it becomes a problem |

## Scope Boundaries

- **IN**: scheduling mechanism (Option A or B, decided post-Phase-3); idempotency guard; bounded-retry wrapper; per-run spend logging; Toxic Pair TDD for any new Python glue; doc-gen updates; PM spot-check of ≥3 unattended runs
- **OUT**: Phase-3 operator sign-off (prerequisite); Epic B lens data enrichment; changes to the Overview tab template; deploy to DO droplet (future); changes to `LIVE_EXECUTION` or the core execution path

**Dependencies:** Phase 3 operator sign-off. Deploy target (future) = shared DO droplet `167.99.3.130` on `:8090` — relevant if scheduling moves off the local box.

**Hard rules:** bounded retries only (finite `max_attempts` + exponential backoff cap); off-execution-path, advisory-only; nightly live-functional verification by PM is the acceptance bar before "trusted."

---

## Sub-task shipped: DISABLE_DAEMON_LENS_PIPELINE env guard (2026-06-19)

**Status: SHIPPED** on branch `feat/prism-nightly-producer-gate` (commits 7c38075 → 3de3a31 → 5bbc030). Pending PM end-gate + PR to origin.

**What shipped:** A 4-line env guard at the top of `_run_lens_pipeline()` (`app.py:686–688`) that silences the daemon's 03:00 lens-pipeline slot when `DISABLE_DAEMON_LENS_PIPELINE` is set to any non-empty value. 4 tests GREEN (`tests/app/test_lens_pipeline_gate.py`).

**Why:** Option B (`prism_scheduler.py`) is the confirmed scheduling mechanism. With the council as the sole nightly producer on the DO droplet, the daemon's 03:00 slot would write a competing `MARKET_PRISM` row on the same night — no idempotency guard exists between the two paths. The env guard lets the operator silence the daemon slot without touching the scheduler registration.

**Remaining open items (PM handles deployment):**
- Set `DISABLE_DAEMON_LENS_PIPELINE=1` on the droplet **before** registering the council systemd timer (see DE-PRISM-GATE-001 safe transition order).
- Register the council systemd timer (or equivalent cron) on the droplet.
- PM spot-check ≥3 unattended runs (AC-5).
- Confirm AC-1 (fresh `MARKET_PRISM` row + full audit trail per run), AC-3 (no double-row per night), AC-4 (Opus spend logged).

---

## Sub-task shipped: Council subprocess ANTHROPIC_API_KEY exclusion (2026-06-19)

**Status: SHIPPED** on branch `feat/prism-council-sub-auth` (RED: d85aa94, GREEN: pending).

**What shipped:** In `_run_prism()`, the subprocess env build changed from `env=os.environ.copy()` to `env={k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}`. The council subprocess now falls back to `CLAUDE_CODE_OAUTH_TOKEN` (subscription) instead of billing against the metered API key. 3 new tests in `tests/prism_scheduler/test_council_sub_auth.py` (AC-1: key excluded; AC-2: OAuth token passes through; AC-3: other vars preserved).

**Why:** Claude Code auth precedence puts `ANTHROPIC_API_KEY` above `CLAUDE_CODE_OAUTH_TOKEN`. Without the pop, nightly council runs were billed against the metered key even when a subscription token was present. The on-demand dashboard advisor (Flask HTTP routes) is unaffected — it calls the Anthropic SDK directly, not via a `claude -p` subprocess.

**Remaining open items (PM handles deployment) — unchanged from previous sub-task:**
- Set `DISABLE_DAEMON_LENS_PIPELINE=1` on the droplet before registering the council systemd timer (DE-PRISM-GATE-001 safe transition order).
- Register the council systemd timer.
- PM spot-check ≥3 unattended runs (AC-5).
- Confirm AC-1, AC-3, AC-4 on real droplet runs.
