# Feature: Port-Level Math Mode
Status: ready (queued behind engine-correctness-remediation)
Created: 2026-05-15

## Summary

Add an operator-toggleable math mode that switches the engine between two semantics:
- **Per-symphony** (default; preserves current behavior): every math layer (HWM, volatility, PARA velocity, trailing stop, VWAP signals + bleed, MC gating, breakeven, current_return) operates per-symphony in isolation. This is the engine today.
- **Port-level** (new): the same math layers operate on per-account aggregated holdings — every symphony's current holdings under one account are unioned into a single "port" position; one port per account; multi-account = multi-port. Per-symphony exit semantics are preserved (one port-level signal fires N symphony-level exits inside that port).

The toggle lives in the existing settings modal as a labeled two-position switch — labels are literally "Port-level" / "Per-symphony" so the operator's reading is unambiguous. Default state: "Per-symphony" (no behavior change unless explicitly switched). Sequenced AFTER engine-correctness-remediation completes — the per-symphony math has 14 known correctness/methodology fixes landing first; port-level mode mirrors those fixes onto the aggregated input rather than re-implementing them.

## Acceptance Criteria

### P1.1 — Labeled settings toggle
- **AC-P1.1.1**: Settings modal includes a labeled two-position toggle ("Per-symphony" | "Port-level"). NOT an on/off binary, NOT a checkbox. Both labels visible at all times.
- **AC-P1.1.2**: Initial state on first deploy = "Per-symphony" (preserves all current behavior with zero operator action).
- **AC-P1.1.3**: Changing the toggle writes to `.env` `MATH_MODE` and persists across daemon restarts (same mechanism as the existing Composer/Alpaca/Discord/Anthropic key fields).
- **AC-P1.1.4**: Submitting the settings change does NOT auto-restart the daemon — the operator manually restarts via `.\restart.ps1` for the change to take effect. The settings UI surfaces a "Restart required for math-mode changes" notice when the toggle is changed.

### P1.2 — `MATH_MODE` env-var contract
- **AC-P1.2.1**: New `.env` variable `MATH_MODE` accepts two values: `per_symphony` (default) and `port_level`. String-valued, not boolean.
- **AC-P1.2.2**: Unrecognized value falls back to `per_symphony` with an ERROR-level log + dashboard banner ("MATH_MODE='<value>' invalid — falling back to per_symphony").
- **AC-P1.2.3**: `MATH_MODE` is named with a source comment in `.env.example` and read via `os.getenv("MATH_MODE", "per_symphony")` — no hardcoded mode string anywhere in production code.

### P1.3 — Port aggregator module
- **AC-P1.3.1**: New `port_aggregator.py` module exposes `aggregate_to_port(symphonies: list, account_id: str) -> dict` — takes the list of symphonies under one account and produces a single port-level holdings dict with: combined `current_holdings` (sum across symphonies; same-ticker positions merged), aggregated `current_value`, aggregated `current_return` (value-weighted), aggregated `simple_return` / `time_weighted_return` / `max_drawdown` / `net_deposits` (each value-weighted), `account_id` as the port identifier.
- **AC-P1.3.2**: Pure function — no I/O, no state mutation, no time-of-day awareness. Math layer compliance.
- **AC-P1.3.3**: Edge cases: account with zero symphonies (return empty/sentinel; engine treats as "no port to evaluate"); account with one symphony (the port is equivalent to that symphony, but flagged as port-mode for downstream telemetry).
- **AC-P1.3.4**: Allocation-weighted aggregation matches the convention `compute_vwap_signals` uses for symphony-level aggregation (consistency across the math layer).

### P1.4 — `port_state[account_id]` state model
- **AC-P1.4.1**: A new top-level dict `port_state` is added to `bot_state` (additive — does not remove `bot_state[symphony_id]`). When `MATH_MODE=port_level`, math reads/writes `port_state[account_id]`; when `per_symphony`, math reads/writes the existing per-symphony entries.
- **AC-P1.4.2**: `port_state[account_id]` carries the same gate-state fields as a symphony entry (`high_water_mark`, `safe_hwm`, `shadow_hwm`, `vwap_ticks`, `vwap_bleed_ticks`, `mc_history`, `mc_prob`, `armed`, `para_armed`, `breakeven_locked`, `triggered`, `triggered_reason`, `prev_return`, `current_return`, etc.) — identical schema, different identity (account_id vs symphony_id).
- **AC-P1.4.3**: Schema migration adds `port_state` columns to the state DB (additive-first, NULLable + DEFAULT). Existing per-symphony rows untouched.
- **AC-P1.4.4**: New-day reset (`database.py:140` semantics post-E1 fix) applies to `port_state` too — fresh `prev_return` baseline per port per day.

### P1.5 — Mode resolver in the cycle
- **AC-P1.5.1**: A new resolver function (`get_math_inputs(account_id, mode) -> list[dict]`) returns the input set the math layer iterates: in `per_symphony` mode, returns the list of symphonies under the account; in `port_level` mode, returns a single-element list `[port_dict]` produced by `port_aggregator`.
- **AC-P1.5.2**: `alpha_bot_execution.py`'s data phase calls the resolver at the top of each account loop. Downstream math is mode-agnostic — it iterates whatever the resolver returns. NO downstream layer checks `MATH_MODE` directly.
- **AC-P1.5.3**: The mode-resolver is called ONCE per account per cycle; result is reused within the cycle (no per-symphony re-aggregation).

### P1.6 — Exit fan-out semantics
- **AC-P1.6.1**: When `MATH_MODE=port_level` and a port-level exit signal fires, the engine emits N per-symphony exit decisions (one per symphony in that port), all sharing the same `port_trigger_id` for telemetry linkage. Each per-symphony exit is a normal "sell to cash" against that symphony's current holdings — no aggregated-trade placement.
- **AC-P1.6.2**: The `triggered=True` flag is set on `port_state[account_id]` AND mirrored to every `bot_state[symphony_id]` in the port (so the dashboard shows the right state for each symphony).
- **AC-P1.6.3**: If the operator flips the toggle BACK to per-symphony after a port-level exit fired, the per-symphony `triggered` flags remain (they are the truth) — but the port-level `triggered` may be re-evaluated on next cycle.

### P1.7 — Mode transition semantics
- **AC-P1.7.1**: Toggle takes effect at the NEXT cycle boundary after daemon restart. No mid-cycle re-keying.
- **AC-P1.7.2**: When switching FROM `per_symphony` TO `port_level`: `port_state[account_id]` is initialized fresh (no carryover from per-symphony state — the operator chose a new math mode, treat it as a fresh start for the port).
- **AC-P1.7.3**: When switching FROM `port_level` TO `per_symphony`: per-symphony state in `bot_state[symphony_id]` is preserved (it was being maintained in parallel; not stale). Port_state is retained in the DB for forensic / re-switch purposes but not consumed.
- **AC-P1.7.4**: Dashboard surfaces the active mode prominently (a labeled badge in the header banner, near the existing staleness badge).

### P1.8 — Telemetry linkage
- **AC-P1.8.1**: The H1 `exit_triggers` table gains an additive column `port_trigger_id TEXT` (NULLable; default NULL). Per-symphony exits fanned out from a port-level signal share the same `port_trigger_id` value. Per-symphony-mode exits leave it NULL.
- **AC-P1.8.2**: The `/api/triggers` endpoint surfaces `port_trigger_id` (does NOT exclude — operator-internal data, useful for forensics).
- **AC-P1.8.3**: Dashboard "Last trigger" sub-line shows a port-trigger indicator when applicable (e.g., `<reason> @ <hh:mm> (port)`).

### P1.9 — Retunes required (parameters mode-specific)
- **AC-P1.9.1**: After P1 ships, `autotuner.py` is updated to run sweeps PER MODE — port-level tuned parameters (e.g. `PARABOLIC_VELOCITY_THRESHOLD`, `VWAP_CROSS_HWM_PCT`) are distinct from per-symphony tuned values, since the signal magnitudes differ on aggregated holdings.
- **AC-P1.9.2**: The optimization DB grows a `math_mode` column on `autotune_runs` (NULLable, defaults to `per_symphony` for backfill). New rows record the mode they were tuned under.
- **AC-P1.9.3**: A symphony's "best params" lookup at engine startup queries by current `MATH_MODE`. Falls back to per_symphony params with a WARN-level log + dashboard notice if port-level params don't exist yet.

## Architecture

| Surface | Files touched |
|---------|---------------|
| Settings UI | `templates/index.html` (settings modal section) — labeled toggle with both options |
| Settings backend | `app.py` `/api/settings` POST handler — `MATH_MODE` field branch |
| Engine mode resolver | `alpha_bot_execution.py` — resolver call at top of data phase; mode-aware iteration |
| Port aggregation | NEW `port_aggregator.py` — pure function module |
| State schema | `database.py` — port_state read/write helpers; new schema migration |
| Telemetry linkage | H1's `exit_triggers` table — additive `port_trigger_id` column (migration 007 or later) |
| Tuning | `autotuner.py` — per-mode sweeps + `math_mode` column on `autotune_runs` |
| Dashboard | `templates/index.html` — active-mode badge in header |
| Configuration | `.env` — `MATH_MODE=per_symphony` (default) |

**Team composition**: Hex
- `quant-test-writer` (lead)
- `risk-engine-specialist` (cycle resolver + port_state writes + state schema)
- `flask-dashboard-specialist` (settings toggle + active-mode badge)
- `sqlite-specialist` (port_state schema + port_trigger_id migration)
- `optuna-specialist` (per-mode sweep + `math_mode` column)
- `quant-code-reviewer` (discipline gate)

## Edge Cases

- Account with **zero** symphonies in port-level mode → resolver returns empty list, no math runs for that account; engine continues with other accounts.
- Account with **one** symphony in port-level mode → port aggregation produces a port equivalent to the single symphony (with port-mode flag); math runs once on the port, exit fanout fires that one symphony if triggered.
- **Composer/Alpaca fetch failure for a port-level account** → preserve prior `port_state[account_id]` values (no clobber-with-None). Same posture as the engine-correctness-remediation BCC pattern.
- **Mode toggle changed BUT no daemon restart** → the running daemon continues in the previous mode until restart. UI notice makes this explicit.
- **Mode toggle changed and daemon restarted mid-trading-day** → mode takes effect at next cycle. The port_state for the day is fresh (no historical accumulation from morning if we switch at 11:30 ET). PARA-ARM-at-open velocity bug behavior is mode-aware — see dependency on E1's fix.
- **Multi-account portfolios where some symphonies have unknown account_id** → per current behavior, treated as "Unknown Account" group; the same applies to port-level mode (one port per unique account_id including "Unknown").
- **Per-mode tuned parameters not yet swept** → fall back to per_symphony params with WARN-level log + dashboard notice. Operator triggers V1-equivalent sweep manually for the new mode.

## Security Considerations

- **No new external API surfaces** — port-level mode is a pure math/state-layer change.
- **Settings UI**: the toggle is operator-only (same posture as existing settings fields); no per-user permissions exist.
- **Telemetry**: `port_trigger_id` is operator-internal; no PII risk (it's a hash/UUID of the cycle + account).
- **No XSS**: the active-mode badge renders a string from a controlled set (`per_symphony` / `port_level`); Jinja autoescape applies.
- **No Composer/Alpaca/Anthropic auth changes**.

## Testing Strategy

- **TDD via real Hex team** (project hard requirement; this is new codepath).
- **Golden fixtures for port aggregation** (`tests/fixtures/port_aggregator/`) — captured from synthetic per-symphony inputs, expected port output recorded; no inline literals.
- **Golden fixtures for mode transitions** — fresh port_state on switch in; per-symphony state preservation on switch out.
- **Per-mode parameter retune fixtures** — autotuner output records `math_mode` column; engine startup picks the right params per mode.
- **ZERO live calls in test tier** — mock Composer/Alpaca; use captured `symphony-stats-meta` fixtures.
- **Live verification mandatory post-merge**: operator restarts daemon with `MATH_MODE=port_level` set for one account; PM verifies (a) port-level signals fire, (b) per-symphony exits fan out with shared `port_trigger_id`, (c) toggling back to `per_symphony` preserves per-symphony state, (d) dashboard active-mode badge reflects the current mode.
- **No regression** to per-symphony mode (1346+ baseline at engine-correctness-remediation completion).

## Decisions

| Decision | Rationale |
|----------|-----------|
| Sequenced AFTER engine-correctness-remediation | Per-symphony math has 14 known correctness/methodology fixes landing first. Implementing port-level mode against a broken per-symphony reference would mirror the same bugs. The remediation plan must complete; then this plan implements port-level as a parallel branch consuming the corrected per-symphony math primitives. |
| Labeled toggle ("Port-level" / "Per-symphony") not on/off | Operator confirmed — the labels make the operator's reading unambiguous. Boolean would be ambiguous (which is "on"?). |
| `port_state[account_id]` additive (NOT replacing `bot_state[symphony_id]`) | Mode-switch must be reversible without losing state. Both state models live in parallel; mode decides which the math engages with. |
| Toggle takes effect at next cycle boundary after daemon restart | Mid-cycle re-keying is complex + error-prone. Restart is the operator's explicit "commit" point — matches the existing pattern (restart for `.py` config changes, hot-reload for templates only). |
| Per-symphony exit fan-out preserved (not aggregated trade) | Operator confirmed — exits are per-symphony semantically even when signal is port-level. Maintains symphony-level traceability and isolates the failure mode of a single bad fanout. |
| Per-mode parameter retune required (P1.9) | Aggregated holdings have different signal magnitudes than per-symphony. The same `PARABOLIC_VELOCITY_THRESHOLD` value cannot be assumed valid for both modes. |
| Telemetry linkage via `port_trigger_id` (not separate table) | Additive column on existing `exit_triggers` keeps the schema simple; N per-symphony rows share one `port_trigger_id` for fanout linkage. Query: `SELECT * FROM exit_triggers WHERE port_trigger_id = ?` reconstructs the fanout. |

## Scope Boundaries

**IN:**
- The 9 acceptance-criteria groups above.
- `port_aggregator.py` module + tests.
- `port_state` schema additions + migration.
- `MATH_MODE` env var + settings UI toggle.
- Mode resolver in `alpha_bot_execution.py` + downstream mode-agnostic math iteration.
- Exit fan-out logic + `port_trigger_id` telemetry linkage.
- Per-mode autotuner support + `math_mode` column on `autotune_runs`.
- Dashboard active-mode badge.

**OUT:**
- Cross-account aggregation (multi-account → multi-port; one port per account only).
- Aggregated trade placement (exits remain per-symphony).
- Mid-cycle mode switching (restart-gated).
- Re-tuning the engine-correctness-remediation V1 results for port mode (that's P1.9's deferred retune work; it depends on this plan having shipped).
- Replacing the multi-trigger architecture (TP + Trailing + VWAP-Breakdown + VWAP-Bleed remain four parallel triggers in both modes).
- Changing the `EXECUTION_START_TIME` action gate (applies identically in both modes; port-level math still gates at 10:30 for action phase).

## Dependencies

- **Engine-correctness-remediation plan must complete first**: E1 (PARA-ARM fix), E2 (monotonicity), H1 (telemetry), H2 (priority resolution), H3 (MC seeding), O1-O5 (Optuna methodology), V1 (sweep), V2 (open-window gate), V3 (fleet circuit breaker), I1-I3 (investigations).
- After this plan ships, the V1-equivalent sweep is RE-RUN per mode to produce port-level parameter recommendations.
- V3 (fleet-correlation circuit breaker)'s semantics adapt automatically — "fleet" in port-level mode = number of ports, not symphonies.
