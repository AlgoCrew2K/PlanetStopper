# Feature: VWAP Remediation
Status: ready
Created: 2026-05-15

## Summary

Remediate findings from the VWAP code audit (`docs/research/dashboard/vwap-audit.md`) and the methodology review (returned as conversation transcript on 2026-05-15). The VWAP exit system is **mathematically correct and behaved as designed** during today's 11-simultaneous-trigger event — every formula matches spec, every constant has provenance, `triggered=True` is set in exactly one site (`alpha_bot_execution.py:936`) gated behind the action-phase exit branch, no data-phase leakage. **No code bug.** The audits did, however, identify aggressive tuning of the VWAP-Breakdown gate, missing trigger-attribution observability, fleet-correlation over-fire risk inherent to the multi-trigger architecture, and one open velocity-calc question (`prev_return=0` on new-day reset). This plan delivers five remediation workstreams gated workstream-by-workstream by the operator.

## Acceptance Criteria

### Workstream 1 — Trigger Attribution Telemetry  [PRIORITY: HIGHEST — diagnostic foundation]

- **AC-1.1**: Every exit-trigger fire is recorded with: UTC + ET timestamp, `symphony_id`, `account_id`, `triggered_reason` (TP / Trailing Stop / VWAP Breakdown / VWAP Bleed Cut), `at_return`, and a JSON blob of the relevant gate-state at fire time (`high_water_mark`, `vwap_ticks`, `vwap_bleed_ticks`, `mc_prob`, `symphony_vol`, the computed gate values that crossed their thresholds).
- **AC-1.2**: Telemetry persists across daemon restarts (state DB table, append-only).
- **AC-1.3**: Dashboard surfaces, per-symphony: "Last trigger" cell with timestamp + reason (or "—" if no triggers today). New compact aggregate view: trigger distribution counts per reason over the day (and optionally week/month).
- **AC-1.4**: Read-only HTTP endpoint (e.g. `GET /api/triggers?since=<iso_ts>&symphony_id=<id>`) returns historical triggers as JSON for programmatic/operator export.
- **AC-1.5**: Telemetry write must be **non-blocking on the execution path** (project hard constraint #1) — same-transaction or fire-and-forget; cannot fail the cycle.
- **AC-1.6**: Retention: 90 days by default (configurable via `.env` var, e.g. `TRIGGER_TELEMETRY_RETENTION_DAYS=90`); rotation handled cleanly without manual intervention.

### Workstream 2 — Calibration Sweep  [PRIORITY: HIGH — tuning, depends on W1 for verification]

- **AC-2.1**: Optuna search space expanded to include three currently-aggressive constants:
  - `VWAP_CROSS_HWM_PCT` (currently `1.0`)
  - `VWAP_BREAK_CONFIRM_TICKS` (currently `3`)
  - `VWAP_BLEED_ARM_MIN` / `VWAP_BLEED_ARM_MAX` (currently `[-3.0, -0.5]`) — sweep MIN and MAX independently within sane ranges (proposed: MIN in [-5.0, -1.0], MAX in [-1.0, -0.1]).
- **AC-2.2**: Walk-forward sweep produces a **per-symphony recommendation report** (`docs/research/dashboard/vwap-calibration-report.md` or CSV): for each symphony, current vs proposed values + expected impact on trigger frequency from historical replay + sensitivity bands.
- **AC-2.3**: Rollout is **per-symphony, operator-gated** — the operator approves each symphony's new constants before they ship; no fleet-wide flip.
- **AC-2.4**: Post-deploy verification: confirm via Workstream 1's telemetry that the post-tune trigger frequency aligns with the sweep's predicted distribution (operational verification, not just unit-test).
- **AC-2.5**: Deflated-Sharpe / multiple-testing correction (Bailey/López de Prado) applied to the trial best-Sharpe selection so the reported "best" is not inflated by 500 trials of search.

### Workstream 3 — Open-Window Time Gate  [PRIORITY: MEDIUM — surgical sensitivity fix]

- **AC-3.1**: New `.env` constant `VWAP_OPEN_WINDOW_GRACE_MINUTES` (default `15`). VWAP-Breakdown and VWAP-Bleed-Cut triggers do NOT fire in the first N minutes after the action-phase gate (`EXECUTION_START_TIME` + N).
- **AC-3.2**: Take-Profit and Trailing Stop triggers ARE unaffected — they continue to fire during the open-window grace period.
- **AC-3.3**: Constant is named with source comment per project standard.
- **AC-3.4**: RED tests pin: VWAP-Breakdown does NOT fire during the grace window even when all otherwise-required conditions hold; TP DOES fire during the grace window; grace ends correctly at the configured N minutes; the grace window respects `EXECUTION_START_TIME` even when that value is itself changed.

### Workstream 4 — Fleet-Decorrelation Circuit Breaker  [PRIORITY: LOWER — defensive observability, depends on W1]

- **AC-4.1**: When **>N%** of active symphonies (N configurable, default `50%`) fire the **same** `triggered_reason` within an **M-minute window** (default `3` minutes), the engine writes a `fleet_correlation_alert` field to `bot_state` and surfaces a high-visibility dashboard banner: `"Fleet-correlated trigger event detected: <count> symphonies fired <reason> within <M> minutes — review before acting"`.
- **AC-4.2**: The circuit breaker is **OBSERVATIONAL ONLY** — it does NOT block triggers, it does NOT alter exit decisions, it does NOT pause the engine. It surfaces a signal for the operator. (Today's 11-simultaneous-trigger event was a real signal per the audit; the engine must not second-guess legitimate fleet-correlated signals.)
- **AC-4.3**: Banner clears automatically after 30 minutes of no further fleet-correlated triggers; operator can also dismiss manually.
- **AC-4.4**: Detection algorithm runs on the trigger-attribution table written by Workstream 1.

### Workstream 5 — PARA-ARM-at-Open Investigation + Conditional Fix  [PRIORITY: PHASED — investigate first]

- **AC-5.1 (Phase A — investigation, read-only worker)**: A dedicated audit determines whether the new-day reset that sets `prev_return = 0.0` in `database.py:140` causes unintended PARA-ARM behavior on the first cycle each day. Output: a verdict — "intended (no fix)" / "real defect (fix)" / "marginal (tunable)". Cite file:line + behavior trace.
- **AC-5.2 (Phase B — conditional fix, only if Phase A returns "defect" or "marginal")**: Default proposal — on new-day reset, instead of `prev_return = 0.0`, defer the first velocity calculation until a second observation is available (effectively zero velocity at open until the engine has two data points). Concrete alternative: persist yesterday's terminal `current_return` and use it for the first velocity calc. The Phase A worker proposes the specific approach.
- **AC-5.3 (RED tests if Phase B fires)**: With a fixture replay simulating a symphony opening +2.0% on a new trading day, assert PARA-ARM does NOT fire on cycle 1; assert it CAN fire on cycle 2 if velocity remains high.

## Architecture

### Workstream 1 — Telemetry

- **`database.py`**: new table `exit_triggers` with columns: `id INTEGER PRIMARY KEY AUTOINCREMENT`, `ts_utc TEXT NOT NULL`, `ts_et TEXT NOT NULL`, `symphony_id TEXT NOT NULL`, `account_id TEXT`, `triggered_reason TEXT NOT NULL`, `at_return REAL`, `gate_state_json TEXT`, `cycle_id TEXT`. Index on (`ts_utc DESC`), (`symphony_id`, `ts_utc DESC`).
- **`alpha_bot_execution.py:936`** (the single `triggered=True` set site): on every trigger fire, write one row to `exit_triggers` via a new helper `database.record_trigger(...)`.
- **`app.py`**: surface `last_trigger` per symphony in `/api/state`; new route `GET /api/triggers?since=<iso_ts>&symphony_id=<id>&reason=<r>` (read-only).
- **`templates/table_partial.html`**: add a "Last Trigger" column showing `<reason> @ <hh:mm>` or `—`.
- **`templates/index.html`**: small aggregate widget — trigger-distribution-by-reason for today.
- **Team**: quant-test-writer (lead) + risk-engine-specialist (engine write site) + sqlite-specialist (schema + migration) + flask-dashboard-specialist (route + template) + quant-code-reviewer. Pent for this workstream.

### Workstream 2 — Calibration Sweep

- **`autotuner.py`**: extend the search-space dictionary to include the three constants; add an opt-in flag that produces the per-symphony comparison report; integrate the deflated-Sharpe correction per Bailey/López de Prado at trial-selection time.
- **`math_engine.py`**: verify the constants are consumed from passed-in params and not hardcoded — refactor any hardcoded references.
- **New file**: `scripts/vwap-calibration-report.py` — generates the human-readable recommendation report from a completed walk-forward study.
- **Team**: quant-test-writer + optuna-specialist (implementer) + risk-engine-specialist (math consumer review) + quant-code-reviewer. Quad.

### Workstream 3 — Open-Window Gate

- **`math_engine.py`**: new pure helper `compute_vwap_open_window_gate(current_et: time, action_gate_et: time, grace_minutes: int) -> bool` — returns True if the cycle is inside the post-action-gate grace window.
- **`alpha_bot_execution.py`**: in the VWAP exit-decision branch, short-circuit the VWAP-trigger fire when the gate returns True; emit a structured log line ("VWAP grace window — trigger suppressed").
- **`.env`**: new var `VWAP_OPEN_WINDOW_GRACE_MINUTES=15`.
- **Team**: quant-test-writer + risk-engine-specialist + quant-code-reviewer. Trio (no UI surface, no Composer/Alpaca surface).

### Workstream 4 — Fleet Circuit Breaker

- **`alpha_bot_execution.py`**: after the action-phase exit-decisions write, query the `exit_triggers` table for fleet correlation; set `bot_state["fleet_correlation_alert"]` (object with reason, count, window_start, window_end) or clear it.
- **`app.py /api/state`**: surface the `fleet_correlation_alert` at the top level of the JSON response.
- **`templates/index.html`**: render a sticky banner above the portfolio strip when the alert is set; dismiss button writes a short-lived suppression to `bot_state`.
- **Team**: quant-test-writer + flask-dashboard-specialist (implementer; banner + route) + risk-engine-specialist (detection algorithm) + quant-code-reviewer + ux-expert (banner visual verification). Pent. **Depends on Workstream 1** being merged.

### Workstream 5 — PARA-ARM Deep-Dive

- **Phase A**: solo background worker (risk-engine-specialist, opus, read-only) produces verdict + fix proposal. Output: `docs/research/dashboard/para-arm-open-audit.md`.
- **Phase B (conditional)**: TDD team — quant-test-writer + risk-engine-specialist + quant-code-reviewer. Trio.

## Edge Cases

- **W1 telemetry**: simultaneous triggers across multiple symphonies in the same cycle — write order is deterministic (lowest symphony_id first) so the table is reconstructible; concurrent daemon access (pidfile prevents two daemons; single-process is the contract).
- **W1 retention rotation**: never delete during a cycle write (deadlock risk); rotate at end-of-day or via a scheduled separate process.
- **W2 sweep**: a symphony with insufficient history (<125 trading days) — skip cleanly with a logged warning; do not crash the sweep.
- **W3 grace window**: `EXECUTION_START_TIME` change at runtime — the gate computes grace window dynamically off the current `EXECUTION_START_TIME` each cycle; no cache.
- **W3 daylight-saving boundary**: ET conversions must use `zoneinfo` correctly across DST transitions.
- **W4 fleet detection**: if Workstream 1's table is empty (engine just started, no history), the detection algorithm returns no-alert cleanly.
- **W4 banner dismissal**: dismissal is per-event (identified by `window_start` timestamp) so a NEW fleet event re-raises the banner.
- **W5 Phase A**: read-only investigation must not modify state; treat the audit as informational input to Phase B's scope decision.

## Security Considerations

- **Telemetry table**: no PII; symphony names + IDs are non-sensitive trading identifiers. Retention bounded (90 days default).
- **New routes**: `/api/triggers` is read-only; no mutation; rate-limit consideration is low (operator-only dashboard, internal LAN — not internet-exposed).
- **No XSS surfaces** in the new template renders — all data-driven strings (`triggered_reason`, timestamps) come from controlled sources (the engine's own writes); Jinja autoescape default applies.
- **No new external API calls** introduced by W1/W3/W4. W2's Optuna runs offline against historical Alpaca/Composer fixtures (existing pattern).
- **Composer/Alpaca auth boundary unchanged** — these workstreams don't touch the API client layer.

## Testing Strategy

- **TDD per workstream**, real Agent Teams (no solo-implementer approximations) per project hard requirement.
- **Adversarial RED first** — for math-layer workstreams (W3, W5), `quant-test-writer` is the adversarial author per project standard.
- **Fixtures** — use existing captured fixtures (`tests/fixtures/composer/symphony_stats_meta.json`, etc.); for W1 a synthetic state-DB snapshot from a known-cycle is captured for replay.
- **Zero live calls in the test tier** — every workstream's RED must mock external I/O. (C2.2 lesson — a prior cycle leaked live calls and blew the suite to 10 min.)
- **Live verification after merge** — operator-led, post-restart, on real Composer data. The PM does NOT declare cycle-complete on test pass alone — actual live verification of the user-visible behavior is required per the M2-class lesson.
- **Cross-workstream regression** — each workstream's PR must keep the 1346+ baseline (counting up as each workstream lands).

## Scope Boundaries

- **IN**: the five workstreams above, in the priority order specified.
- **OUT**:
  - Rewriting the VWAP math itself (audits confirmed correctness).
  - Replacing the multi-trigger composition with single-rule logic.
  - Adding new exit-trigger types.
  - Changing `EXECUTION_START_TIME` default.
  - Cross-symphony aggregation changes (e.g., portfolio-level VWAP signals).
  - Implementing the math-engine-wide remediation surfacing from the parallel panel (separate `/scaffold` after the panel reports).

## Decisions

| Decision | Rationale |
|----------|-----------|
| Telemetry storage in the state SQLite DB (new `exit_triggers` table) | Aligns with existing two-DB pattern; queryable via existing tooling; ACID guarantees. Append-only JSON log was the alternative — rejected for queryability and rotation friction. |
| Workstream 4 (circuit breaker) is OBSERVATIONAL only, not BLOCKING | The audit confirmed today's 11 triggers were a REAL fleet-correlation signal. The engine must not second-guess real signals; operator decides whether to act. The breaker exists only to surface, not gate. |
| Workstream 5 phased: investigation first, conditional fix second | The auditor flagged PARA-ARM-at-open as an open question, not a confirmed defect. Fix scope is entirely dependent on the verdict, so the investigation worker runs first. |
| Workstream 4 depends on Workstream 1 | The fleet-correlation detector reads the `exit_triggers` table. W4 cannot ship before W1. |
| Sequence: W1 → W2 → W3 → W5 → W4 | W1 telemetry is the diagnostic foundation enabling W2's verification and W4's detection. W2 reduces the trigger rate via tuning (high impact). W3 is a surgical sensitivity fix. W5 unblocks an open question. W4 is a defensive observability layer that benefits from the prior workstreams' impact being visible first. |
| W2 includes deflated-Sharpe correction | Methodology review flagged multiple-testing inflation (500 trials × N symphonies); without correction the reported "best" trial is biased upward (López de Prado / Bailey). |
| Retention default 90 days | Balances long-enough history for trend analysis vs. SQLite file growth. Operator can adjust via `.env`. |
