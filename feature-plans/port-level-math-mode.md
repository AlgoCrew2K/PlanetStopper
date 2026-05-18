# Feature: Port-Level Math Mode (Rewritten 2026-05-18 post panel validation)

Status: ready-for-revalidation
Created: 2026-05-15 (original); 2026-05-18 rewritten by spec-writer after Hex panel validation
Predecessor: docs/research/port-level/* on branch panel/port-level-validation (6 panel reports + convener synthesis). The original plan was found NOT viable as written; this rewrite captures the operator's clarified mental model and folds the panel's critical correctness BCs in.

## Summary

The engine maintains **two altitudes of risk state simultaneously**: per-symphony state (today's bot_state[symphony_id]) AND port-level state (port_state[account_id]). Both altitudes are computed every cycle and rendered on the dashboard regardless of any toggle. A single operator setting -- the **exit-authority toggle** -- decides which altitude DRIVES the exit decision; the non-authoritative altitude continues to compute and display but does not fire exits. In port-mode the port-level math emits a **sized asset-reduction target** (which tickers need exposure reduced and by how much), and an explicit **best-fit selection step** picks the ONE symphony whose current holdings most closely match that reduction profile; that symphony exits whole-portfolio-go-to-cash. If the port signal is still triggered the next cycle, another single symphony exits -- multi-cycle convergence, NOT fan-out. Per-account params replace per-symphony params in port-mode (one PARABOLIC_VELOCITY_THRESHOLD per account, shared across the account's symphonies).

## Why this rewrite (vs the original plan)

The panel validation (docs/research/port-level/2026-05-18-CONVENER-synthesis.md, 28 BCs + 40 PAs across 6 specialists) found the original plan misaligned with the operator's actual intent on two axes: (1) the original used a **mode switch** -- port OR per-symphony, not both -- whereas the operator wants **both altitudes always visible** with a separate exit-authority decision; (2) the original used **trade-level fan-out** -- one port signal triggers N parallel symphony exits -- whereas the operator wants **single-symphony best-fit selection** with multi-cycle convergence. The original also carried critical math errors (value-weighting path-dependent fields; breakeven_locked latching on a non-stationary port; wipe_transient_state clobber; bot_state misframed as relational). This rewrite addresses both the architectural realignment and the panel's correctness BCs.

## Acceptance Criteria

### P2.1 -- Dual-altitude state always computed

- **AC-P2.1.1**: Every cycle, the engine computes AND persists state at both altitudes: per-symphony state in bot_state[symphony_id] (existing model, schema-preserved), AND port-level state in port_state[account_id] (new typed table -- see P2.5). Computation happens regardless of the exit-authority toggle.
- **AC-P2.1.2**: The math layer iterates both altitudes via a compute_for_altitude(...) call shape parameterized by altitude. No layer below the resolver branches on the exit-authority toggle.
- **AC-P2.1.3**: First-cycle-after-deploy: an account with no prior port_state rows initializes them fresh that cycle. No retro-compute from history.
- **AC-P2.1.4**: A symphony with no enclosing port (lone symphony / unknown account_id grouping) still computes per-symphony state normally; port-level state for that case is the trivial single-symphony port -- flagged as port-mode for downstream telemetry consistency, no behavior difference.

### P2.2 -- Exit-authority toggle

- **AC-P2.2.1**: A new env var EXIT_AUTHORITY accepts two string values: per_symphony (DEFAULT -- preserves current behavior) and port_level. The settings modal exposes a labeled two-position control (both labels visible; not on/off).
- **AC-P2.2.2**: Defense-in-depth validation: unrecognized value falls back to per_symphony with an ERROR-level log + dashboard banner. The fallback posture is fail-DEGRADED for an unrecognized literal, but fail-STOP if port_level is requested AND port-level params for the account are missing (see P2.11).
- **AC-P2.2.3**: Read via os.getenv("EXIT_AUTHORITY", "per_symphony"). No hardcoded mode string anywhere in production code. .env.example carries the variable with a source comment.
- **AC-P2.2.4**: Toggle change writes to .env via set_key() (same mechanism as existing key fields). Does NOT auto-restart the daemon. Settings UI surfaces a sticky "Restart required for exit-authority change" notice -- amber inline beneath the toggle -- that REMAINS visible until /api/state.daemon_started_at exceeds the toggle-last-changed timestamp (positive restart-observed confirmation per panel BC H7).
- **AC-P2.2.5**: Active exit-authority is rendered on the dashboard header as a labeled badge in the LEFT column adjacent to the existing DRY-RUN / LIVE execution-context badge (NOT co-located with the rose-toned staleness alarm -- panel BC H6). Neutral indigo styling.
- **AC-P2.2.6**: The non-authoritative altitude's computed triggered value is NEVER acted upon. It is rendered + persisted for visibility but skipped by the order-placement code path.

### P2.3 -- Per-account parameters in port-mode

- **AC-P2.3.1**: In port-mode (EXIT_AUTHORITY=port_level), the parameters consumed by port-level math (the per-account altitude's PARABOLIC_VELOCITY_THRESHOLD, VWAP_CROSS_HWM_PCT) are looked up **per-account**, not per-symphony. One config set per account in port-mode.
- **AC-P2.3.2**: Mode-invariant params (TAKE_PROFIT_MC_PCT, VWAP_BLEED_MULTIPLIER, VWAP_BLEED_TICKS, MAX_PARABOLIC_SQUEEZE) remain per-symphony in both altitudes (panel finding M1 -- these have the same magnitude semantics regardless of altitude).
- **AC-P2.3.3**: Mode-specific params subset: PARABOLIC_VELOCITY_THRESHOLD, VWAP_CROSS_HWM_PCT. The autotuner search space is split accordingly (see P2.11). Per-symphony altitude continues to use per-symphony values for these params; only the port altitude swaps to per-account values.
- **AC-P2.3.4**: A symphony's per-symphony altitude lookup never falls back to per-account port values, and vice versa. The two altitudes are independent param spaces.

### P2.4 -- Port aggregator: math correctness

- **AC-P2.4.1**: New port_aggregator.py module. Pure function aggregate_to_port(symphonies: list, account_id: str, port_equity_series: list) -> dict. No I/O, no state mutation, no time-of-day awareness.
- **AC-P2.4.2** (CRITICAL -- panel BC C1): Per-field aggregation method is **explicit and method-specific**:
  - **Value-weightable (preserved)**: current_return, last_percent_change, last_dollar_change, value, cash. Aggregated as the existing allocation-weighted sum on common-denominator quantities.
  - **Path-dependent (recomputed from port-equity series, NEVER value-weighted)**: max_drawdown, simple_return, time_weighted_return. Computed via the port-equity series (see AC-P2.4.3). Goldberg-Mahmoud 2017 / Pospisil-Vecer 2010: drawdown is a path functional and value-weighted scalars across non-coincident extrema are mathematically meaningless.
  - **Flow (arithmetically summed)**: net_deposits. Summed across symphonies in the account; no weighting.
  - **Unavailable / dropped**: sharpe_ratio, annualized_rate_of_return. Either recomputed from the port-equity series if downstream consumers need them, or marked NULL with a documented unavailability reason. Defer the decision to test-writer + composer-alpaca-integration as part of the integration story.
- **AC-P2.4.3** (port-equity series construction): The port equity at time t is sum(symphony.value_at(t) for symphony in account), sampled at the engine's native 1-minute cadence within session AND at daily close across the trailing window required by each path-dependent computation (e.g., max_drawdown over the cycle since HWM; TWR over the lookback the operator currently uses for per-symphony). The series is derived from per-symphony historical data (already cached by synthetic_history.py); no new external fetches. Cache key includes a composition hash so composition changes invalidate the cache (panel BC H2).
- **AC-P2.4.4**: Edge cases: account with **zero symphonies** -> aggregator returns an empty sentinel; engine treats as "no port to evaluate." Account with **one symphony** -> the port is mathematically equivalent to that symphony, but flagged port-mode for telemetry consistency.
- **AC-P2.4.5**: Strike the original P1.3.4 false-precedent claim about compute_vwap_signals (panel BC H3). The aggregator does cross-symphony value-weighting on the value-weightable fields ONLY; the per-ticker within-symphony weighting in compute_vwap_signals is a separate convention and not cited.
- **AC-P2.4.6**: HWM topology change is documented (panel BC C2). The operator chose port-mode authoritative means per-symphony HWM is NOT a safety floor in port-mode -- operator accepts with eyes open. The per-symphony altitude continues to compute + display, but its triggered is non-authoritative when EXIT_AUTHORITY=port_level.

### P2.5 -- port_state storage

- **AC-P2.5.1** (panel BC C4): port_state is stored as a **dedicated typed table** (per R1 fleet_alert_state precedent), NOT as a JSON key inside the bot_state blob. Migration 010_port_state.sql creates port_state(account_id PRIMARY KEY, high_water_mark REAL, safe_hwm REAL, shadow_hwm REAL, vwap_ticks_json TEXT, vwap_bleed_ticks_json TEXT, mc_history_json TEXT, mc_prob REAL, armed INTEGER, para_armed INTEGER, port_breakeven_active INTEGER, triggered INTEGER, triggered_reason TEXT, prev_return REAL, current_return REAL, composition_hash TEXT, last_target_reduction_json TEXT, last_selected_symphony_id TEXT, updated_at TIMESTAMP, ...).
- **AC-P2.5.2**: New helpers in database.py: read_port_state(account_id), write_port_state(account_id, state), clear_port_state(account_id). RO-connection support for dashboard (R7 WAL compatibility preserved).
- **AC-P2.5.3** (panel BC C3 -- wipe_transient_state allowlist): database.py:159-185's blanket top-level-dict clobber on new-day reset is refactored to use a **reserved-key allowlist**. Reserved keys: date, last_execution_mode, last_market_close_snapshot, plus anything that is NOT a bot_state[symphony_id] shape. port_state is a separate table so the bot_state clobber does not reach it; but the daily-reset semantics still need to apply to port-state per-account.
- **AC-P2.5.4** (E1 baseline applies to ports -- see engine-correctness-remediation): On new-day reset for each account, port_state[account_id].prev_return = None so cycle-1 yields zero velocity and PARA-ARM cannot fire on the opening gap. Same sentinel mechanism as E1 fix.
- **AC-P2.5.5** (composition change reset -- panel BC H2 / PA-1): The resolver detects per-cycle changes to the set of symphony_ids in an account. On change, port_state[account_id] rebases: prev_return = None, mc_history = [], vwap_ticks = [], vwap_bleed_ticks = [], high_water_mark = current_value, port_breakeven_active = False, armed = False, para_armed = False. The composition_hash column stores a stable hash so detection is O(1).
- **AC-P2.5.6**: The breakeven concept in port_state is renamed port_breakeven_active and is **non-latching** (panel BC C5). Re-evaluated each cycle. The latching invariant from compute_breakeven_update (math_engine.py:229-231) applies ONLY to per-symphony state. The same loosening applies to armed and para_armed on port_state -- they are re-derivable per cycle from the current port composition; they are NOT lifetime-anchored to a port "open" event.

### P2.6 -- Port-level signal: binary trigger PLUS sized asset-reduction target

- **AC-P2.6.1**: When port-level math fires, the signal payload is BOTH a binary trigger AND a sized asset-reduction target. Schema:

```
{
  "triggered": bool,
  "triggered_reason": str,         # which trigger fired (TP / Trailing / VWAP-Breakdown / VWAP-Bleed)
  "fired_at_cycle": str,           # cycle id
  "target_reduction": [
    { "ticker": str,
      "amount_usd": float,         # dollar exposure to reduce
      "reason_for_this_ticker": str # which math signal nominated this ticker (HWM-drawdown / VWAP-cross / bleed / weight-of-portfolio)
    },
    ...
  ],
  "port_total_reduction_usd": float  # sum of target_reduction[].amount_usd
}
```

- **AC-P2.6.2** (target derivation): The per-ticker reduction amounts are derived from the math-layer signal that fired. Examples:
  - Port HWM trailing-stop trigger -> target = the per-ticker drawdown contributions: each ticker's (peak_exposure_usd - current_exposure_usd) clamped at zero.
  - VWAP-breakdown trigger -> target = the tickers that crossed VWAP below the threshold, sized by their current exposure.
  - VWAP-bleed trigger -> target = bleed-contributing tickers, sized by bleed contribution.
  - TP MC-gated trigger -> target = tickers proportional to gain contribution.
- **AC-P2.6.3**: When triggered=False, target_reduction is empty / NULL and not persisted in the trigger telemetry table.
- **AC-P2.6.4** (snapshot for audit): On every triggered port signal, port_state[account_id].last_target_reduction_json is updated with the latest payload. This becomes the input to the selection step in P2.7 AND the dashboard surfaces it (P2.9).
- **AC-P2.6.5**: When EXIT_AUTHORITY=per_symphony, the port-level signal continues to compute and is rendered for visibility but does NOT produce a target_reduction actioning step.

### P2.7 -- Best-fit single-symphony selection

- **AC-P2.7.1**: When EXIT_AUTHORITY=port_level AND port_state[account_id].triggered=True, the engine invokes a selection step that picks **ONE** symphony in the account whose current holdings best match the target_reduction profile. Selection happens BEFORE order placement.
- **AC-P2.7.2** (matching metric -- recommend, panel re-validation required): The default selection metric is **minimum sum of absolute deviations on per-ticker dollar exposure**:

```
score(symphony) = sum over tickers t of max(0, target_reduction[t] - symphony.exposure_usd[t])
                + sum over tickers t of max(0, symphony.exposure_usd[t] - target_reduction[t]) * OVER_SHOOT_PENALTY
```

The symphony with the LOWEST score (closest match -- exits the most of the target with the least over- and under-shoot) wins. OVER_SHOOT_PENALTY default = 1.0 (symmetric L1); operator may relax to <1.0 to bias toward over-shoot (exit a little more than needed) or >1.0 to bias toward under-shoot. This is a single-pick variant of the multi-item knapsack family (Martello-Toth 1990), simplified because the target is the constraint, not the optimum. Closest to the L1 nearest-neighbor formulation in transportation problems.
- **AC-P2.7.3** (alternative metric -- Euclidean on normalized weight vector): An alternative defined for the test-writer to validate against the L1 default during the panel re-validation cycle. Construction: normalize both target_reduction and symphony.current_holdings to weight vectors summing to 1; compute Euclidean distance. Lower = closer. The default ships as L1; Euclidean is the test-writer's adversarial alternative for selection robustness.
- **AC-P2.7.4** (tie-breakers): If two or more symphonies tie on score within an epsilon (suggest 1% of the smaller dollar amount), tie-break in this order: (1) largest symphony by value, (2) oldest position (longest holding duration), (3) lexicographic on symphony_id (deterministic last resort).
- **AC-P2.7.5** (no-good-match abort): If the WINNING symphony's score exceeds a configurable MIN_MATCH_QUALITY_THRESHOLD (i.e., even the best match is poor), the selection step ABORTS the exit with a WARN-level log + persistent dashboard banner. The port state's triggered flag stays True; the operator must inspect.
- **AC-P2.7.6**: The selected symphony's current_holdings is exited whole-portfolio-go-to-cash (existing exit semantics -- same machinery as a per-symphony triggered exit today). NOT a partial exit; NOT a target-shaped sell.
- **AC-P2.7.7** (telemetry snapshot): The selection rationale is recorded in exit_triggers.gate_state_json for the selected symphony's exit row: includes the full target_reduction payload, the scores for all candidate symphonies (top 3), and the chosen tie-breaker if any.

### P2.8 -- Multi-cycle convergence

- **AC-P2.8.1**: After one symphony exits via port-level selection, the next cycle the resolver detects a composition change (panel BC H2 -- that symphony's holdings drop out), rebases port_state[account_id] per P2.5.5, and recomputes port-level state from scratch against the remaining symphonies.
- **AC-P2.8.2**: If the recomputed port-level signal is still triggered, another single-symphony selection step fires. This repeats cycle-by-cycle until either (a) the signal clears or (b) all symphonies in the account have exited.
- **AC-P2.8.3** (convergence guarantee): For an account with N symphonies, port-level fan-out CANNOT exceed N selection events. Pinned by a RED test that fixtures an "everything-bleeds" path and asserts the engine fires at most N exits.
- **AC-P2.8.4** (cycle pacing): No "rapid-fire" within one cycle. The selection step yields at most ONE exit per cycle per account. Multi-cycle convergence is intentional -- gives the engine time to observe market reaction between exits.
- **AC-P2.8.5** (interaction with no-good-match abort): If P2.7.5's abort condition holds for one cycle, the next cycle re-evaluates -- the port composition may not have changed, but market data has, and a different ticker profile may arrive. The dashboard banner persists across cycles until the trigger clears or a good match emerges.
- **AC-P2.8.6** (interaction with per-symphony triggers): The per-symphony altitude continues to compute; if a per-symphony triggered=True arises while the port is in mid-convergence, that signal is observed and rendered but NOT acted upon (port is the exit-authority). On toggle BACK to per_symphony, the per-symphony triggered flags become authoritative.

### P2.9 -- Dashboard always shows both altitudes

- **AC-P2.9.1**: Per-symphony rows preserved exactly as today (one row per symphony, same columns, same styling).
- **AC-P2.9.2**: A new **pinned port-level row** is rendered ABOVE the per-symphony rows for each account in the table (panel BC H5). Columns: account_id, port HWM, port current_return, port armed / para_armed state, port triggered (with triggered_reason), the last target_reduction payload as an expandable cell, the last selected symphony_id if any, and an indicator for the active exit-authority on this row.
- **AC-P2.9.3** (active-authority indicator per row): Each row (port-level AND per-symphony) renders a small badge indicating whether THAT row is currently exit-authoritative. Two values: AUTHORITATIVE (indigo) or OBSERVED-ONLY (slate-500). Operator sees at-a-glance which altitude is firing.
- **AC-P2.9.4**: Both altitudes render REGARDLESS of the exit-authority toggle. Toggling does NOT add or remove rows; it only changes which rows carry AUTHORITATIVE vs OBSERVED-ONLY.
- **AC-P2.9.5** (selection rationale visibility): On the cycle a port-driven exit fires, the selected symphony's per-symphony row gains a port-trigger glyph + a tooltip with port_trigger_id, target_reduction summary, and top-3 candidate scores (panel BC H8 -- indigo glyph or [P] pill chip, NOT slate-500 text suffix).
- **AC-P2.9.6** (shadow / dry-run): The Shadow Perf strip (R15) aggregates by account_id in port-mode (panel M15). Per-symphony dry_run if-held remains as the forensic underlay; per-port aggregation is added.

### P2.10 -- Telemetry: exit_triggers schema

- **AC-P2.10.1** (panel BC H4): exit_triggers table grows a math_mode TEXT column (NULLable; backfills NULL for pre-existing rows). Set to port_level on port-driven exits, per_symphony on per-symphony-driven exits, NULL on pre-migration rows.
- **AC-P2.10.2**: exit_triggers gains port_trigger_id TEXT NULLable. For port-driven exits, this is a UUID generated at the cycle of the port trigger; the single selected symphony's exit row carries this id. Across multi-cycle convergence, EACH cycle's port-triggered exit has a DIFFERENT port_trigger_id (each cycle is a fresh trigger evaluation), but they all share the same account_id and can be reconstructed by query: SELECT * FROM exit_triggers WHERE account_id = ? AND math_mode = 'port_level' ORDER BY cycle_timestamp.
- **AC-P2.10.3**: gate_state_json for a port-driven exit row carries: the target_reduction payload at the moment of trigger, all candidate-symphony scores (top 3), the selected symphony, the tie-breaker used, and the abort-flag if P2.7.5 fired.
- **AC-P2.10.4**: /api/triggers surfaces all three new columns. Dashboard "Last trigger" sub-line shows the port-trigger glyph + tooltip per P2.9.5.
- **AC-P2.10.5** (partial index for query speed): Migration adds CREATE INDEX idx_exit_triggers_port_trigger_id ON exit_triggers(port_trigger_id) WHERE port_trigger_id IS NOT NULL (panel M4).

### P2.11 -- Autotuner: per-mode + per-account

- **AC-P2.11.1**: autotune_runs table (state DB -- panel M6 corrects original plan's "optimization DB" claim) gains a math_mode TEXT column. Migration 012. Backfills NULL -> per_symphony. New rows record the mode tuned under.
- **AC-P2.11.2** (panel M1 -- split): The search space is split:
  - **Mode-specific subset**: PARABOLIC_VELOCITY_THRESHOLD, VWAP_CROSS_HWM_PCT. Two separate studies per symphony / per account.
  - **Mode-invariant subset**: TAKE_PROFIT_MC_PCT, VWAP_BLEED_MULTIPLIER, VWAP_BLEED_TICKS, MAX_PARABOLIC_SQUEEZE. Single shared study (mode-blind).
  Avoids 4x redundant search work.
- **AC-P2.11.3** (panel BC C7 -- DSR T-correction): Bailey-de-Prado 2014 DSR Eq. 9 sqrt(T-1) term: in port-mode T = validation_calendar_days * N_symphonies (the per-symphony-day observation count is preserved even though the strategy aggregates) -- restored from the naive T = validation_calendar_days * 1 collapse. Documented in autotuner.py with citation.
- **AC-P2.11.4** (panel BC C8 -- frozen-eval fold): Port-mode extends the frozen-eval fold to **>=25 trading days** (relax TRAIN_RATIO mode-aware) so the OOS Sortino has enough port-day observations for the downside-deviation denominator to be stable. Mode-aware split parameter: documented as a project decision.
- **AC-P2.11.5** (lookup at engine startup, panel BC C6 -- fix original plan's contradiction): When the engine starts in EXIT_AUTHORITY=port_level mode for an account, it queries autotune_runs WHERE account_id=? AND math_mode='port_level'. **Fail-STOP** if no rows: refuse to enter port-level mode for that account; log an ERROR + persistent dashboard banner; engine falls back to per-symphony AUTHORITATIVE for that account specifically while continuing to compute port state in observed-only mode. The fallback does NOT use per-symphony params on the port altitude (which would be methodologically invalid per panel C6).
- **AC-P2.11.6** (Sortino documentation -- panel M2): Document that port-mode Sortino is a netted-downside-deviation proxy: the downside deviation is computed against the port-equity return series, NOT the per-symphony return series. This is a different measurement than per-symphony Sortino and PM ratifies the semantic change in DECISIONS.md.
- **AC-P2.11.7** (get_latest_autotune_run signature -- panel M3): New optional math_mode: str | None parameter. Legacy callers (NULL) unchanged; port-mode callers pass math_mode='port_level'.

### P2.12 -- Settings + restart UX

- **AC-P2.12.1**: Sticky "Restart required" notice per panel BC H7. Amber inline beneath the toggle. Sticks until restart-observed.
- **AC-P2.12.2**: /api/state adds three additive fields (panel M14): port_state (per-account dict), exit_authority (string), daemon_started_at (timestamp). Backward-compat preserved -- no existing field renamed or removed.
- **AC-P2.12.3** (panel M16 -- segmented control): Settings UI uses a segmented control pattern (not a single-toggle slider); placement is under the existing LIVE_EXECUTION segment in the Execution Mode section.
- **AC-P2.12.4** (panel M17 -- degraded-state badge): If the EXIT_AUTHORITY fallback fires per AC-P2.2.2, the active-mode badge renders with amber border + "PER-SYMPHONY-fallback" text so the operator sees the degraded posture.

## Architecture

| Surface | Files touched |
|---------|---------------|
| Engine cycle resolver | alpha_bot_execution.py -- dual-altitude compute loop; selection step gating on exit-authority |
| Port aggregator | NEW port_aggregator.py -- pure aggregation + port-equity series construction |
| Best-fit selector | NEW port_selector.py -- selection metric, tie-breakers, no-good-match abort |
| Math layer | math_engine.py -- confirm trigger functions are altitude-agnostic (consume state_dict, params); add target_reduction derivation helpers per trigger family |
| State schema | database.py -- port_state typed table, helpers, wipe_transient_state allowlist refactor |
| Telemetry | exit_triggers table -- math_mode + port_trigger_id + partial index (migration 011) |
| Tuning | autotuner.py -- per-mode sweeps; math_mode column on autotune_runs (migration 012); DSR T-correction; frozen-eval fold split; mode-invariant subset |
| Settings backend | app.py /api/settings POST -- EXIT_AUTHORITY field branch; /api/state additive fields |
| Settings UI | templates/index.html -- segmented control; sticky restart notice |
| Dashboard | templates/index.html / table_partial.html -- pinned port row per account; per-row authoritative badge; port-trigger glyph |
| Shadow Perf | analytics.py -- per-port aggregation by account_id |
| Reporting (open) | reporting.py -- EOD group-by port_trigger_id OR explicitly deferred (panel H9 -- operator picks) |
| AI Advisor (open) | ai_advisor.py -- port-mode context shape OR explicitly deferred (panel H9 -- operator picks) |
| Configuration | .env, .env.example -- EXIT_AUTHORITY=per_symphony (default) |

**Team composition**: Hex (per project CLAUDE.md standing pattern):
- quant-test-writer (lead -- math-layer + selector Toxic Pair primary)
- risk-engine-specialist (resolver, port_state writes, composition-change detection, wipe refactor, selector integration)
- flask-dashboard-specialist (settings UX, badge, pinned port row, sticky restart notice, glyph, segmented control)
- sqlite-specialist (migrations 010 / 011 / 012, helpers, lockstep SELECT/row-mapper updates, partial index)
- optuna-specialist (per-mode sweep, mode-invariant split, DSR T-correction, frozen-eval re-split, fail-stop posture)
- quant-code-reviewer (discipline gate; cross-cuts hard rules)

Math-layer changes always add quant-test-writer as the adversarial test author (project CLAUDE.md requirement).

## Edge Cases

- **Account with zero symphonies** -> resolver returns empty sentinel; no port-level math; no per-symphony math; engine continues to other accounts.
- **Account with one symphony** -> port = symphony; selection step is trivial (one candidate); fan-out degenerates to the existing per-symphony exit. Telemetry still records port_trigger_id for forensic completeness.
- **Composition change mid-day** (Composer rotates holdings) -> resolver detects via composition_hash delta, rebases port_state[account_id] per AC-P2.5.5. PARA reset, breakeven cleared, HWM reset to current value.
- **Composer rejects the go-to-cash on the selected symphony** -> exit fails, existing M2-class diagnostic procedure logs the rejection (per task #30 runbook). The port triggered flag remains True; next cycle re-evaluates. If the same symphony is re-selected and re-rejects, the operator banner per P2.7.5 should escalate (open question -- does Composer-rejection count as "no good match" for banner purposes?).
- **All symphonies in port already triggered (multi-cycle convergence near terminus)** -> if N=1 symphony remains and it ALSO has its own per-symphony triggered=True from observed-only state, the port selection still selects it (no other candidates). The per-symphony triggered becomes coincident -- single exit, both altitudes "agree."
- **Operator flips exit-authority MID-trading-day** -> restart-gated. Until the daemon restarts, the engine continues under the previous authority. Sticky notice persists until restart-observed.
- **Operator flips exit-authority AFTER a port-driven exit has fired this morning** -> per-symphony altitude's triggered=True flags persist for the symphony that exited (they are the truth). Port altitude's triggered=True may re-evaluate FALSE on next cycle if composition has changed (it has -- one symphony exited). Telemetry retains the historical row regardless.
- **Account with one symphony AND that symphony has zero current_holdings (fully in cash)** -> port-equity series degenerate; aggregator returns sentinel for path-dependent fields, value-weightable fields aggregate to 0. No trigger possible.
- **First cycle after a mode switch (per_symphony -> port_level)** -> fresh port_state[account_id] per AC-P2.1.3; prev_return=None per AC-P2.5.4; first cycle yields zero velocity; cannot PARA-ARM-on-open.
- **PARA velocity in port-mode** is per-port (= per-account), not cross-account (panel M9). Documented in Scope Boundaries.
- **MC seed in port-mode**: derive_cycle_mc_seed keyed on cycle_id + altitude_identity. Same seed across ports-in-same-cycle is intentional (panel M10 -- preserves cross-port comparability). Pinned in resolver comment.
- **MIN_MATCH_QUALITY_THRESHOLD never satisfied across many cycles** -> persistent dashboard banner per AC-P2.7.5; operator must manually inspect / override. The engine does NOT auto-degrade to per-symphony authority for that account in this case -- it WAITS for the operator. Document this in the runbook.

## Security Considerations

- No new external API surfaces. Port-level mode is pure math / state-layer / dashboard.
- Settings UI: the toggle is operator-only (same posture as existing settings fields). No per-user permissions.
- Telemetry: port_trigger_id is a UUID; account_id is operator-known; no PII risk.
- Jinja autoescape applies to all new dashboard cells (active-authority badge, port-trigger glyph, target_reduction expandable cell). XSS-safe -- no raw HTML interpolation.
- No Composer / Alpaca / Anthropic auth changes.

## Testing Strategy

- **TDD via real Hex team** (project hard requirement -- new codepaths everywhere). Shared worktree, one branch, SendMessage handoffs, autonomous Toxic Pair cycles with reviewers wrapped.
- **Golden fixtures for port aggregator** (tests/fixtures/port_aggregator/): captured synthetic per-symphony inputs; expected port output recorded; NO inline literals; assert per-field method (value-weighted vs path-recomputed vs sum). Math correctness: drawdown is recomputed from a fixtured port-equity series, NOT derived from per-symphony scalars.
- **Golden fixtures for port-equity series construction** (tests/fixtures/port_equity/): captured 1-minute and daily-close series; composition change scenarios; cache invalidation on composition hash change.
- **Golden fixtures for best-fit selector** (tests/fixtures/port_selector/): multiple target_reduction shapes vs candidate symphony holdings; expected winning symphony + score recorded; adversarial cases for tie-breaker order; no-good-match abort case.
- **Multi-cycle convergence test**: fixtured "everything-bleeds" path with N=4 symphonies; assert engine fires <=4 exits across multi-cycle replay; assert each cycle's port_trigger_id is distinct; assert composition-change reset fires between each.
- **Per-altitude isolation test**: assert that toggling EXIT_AUTHORITY does not change which rows are PERSISTED -- both altitudes always present -- only changes which is acted upon.
- **wipe_transient_state regression test** (panel C3): assert port_state survives new-day reset; assert latent bug for last_market_close_snapshot no longer present.
- **Composition-change reset test** (panel BC H2): assert prev_return=None, mc_history=[], vwap_ticks=[] on composition delta.
- **port_breakeven_active non-latching test** (panel C5): assert it can re-False after going True if conditions reverse.
- **DSR T-correction test** (panel C7): assert tuning replay computes T from the per-symphony-day observation count, not the per-port-day count.
- **Frozen-eval port-mode test** (panel C8): assert >=25 trading days when port-mode.
- **Mode-invariant split test** (panel M1): assert TAKE_PROFIT_MC_PCT study runs once per symphony (shared across modes); assert PARABOLIC_VELOCITY_THRESHOLD study runs twice (mode-specific).
- **Per-row authoritative badge test** (dashboard): assert each row's badge reflects the active exit-authority correctly under both toggle states.
- **Sticky restart notice test** (dashboard): assert notice persists until daemon_started_at > toggle_changed_at.
- **ZERO live calls in test tier** -- mock Composer/Alpaca; use captured symphony-stats-meta fixtures (panel M11 -- no new endpoints).
- **Live verification mandatory post-merge** (operator-led, per project hard rule): operator restarts daemon with EXIT_AUTHORITY=port_level set for one account; PM verifies (a) both altitudes render; (b) port-level signals fire; (c) best-fit selection picks ONE symphony and that symphony exits; (d) next cycle re-evaluates; (e) toggling back to per_symphony preserves per-symphony state and changes the active-authority badge; (f) sticky restart notice clears on restart-observed; (g) port-trigger glyph appears on selected symphony's exit row; (h) Discord EOD reporting aggregates correctly OR is explicitly deferred per H9.
- **No regression to per-symphony mode** (baseline preserved from engine-correctness-remediation merger).

## Decisions

| Decision | Rationale |
|----------|-----------|
| Sequenced AFTER engine-correctness-remediation | Per-symphony math has 14 known correctness/methodology fixes landing first. Port-level mode mirrors those fixes onto the aggregated input rather than re-implementing them. Engine-correctness-remediation is merged.md -- already complete. |
| Dual-altitude state always computed (operator clarification) | Operator's mental model: see both per-symphony AND port state simultaneously. No display toggle. Separate exit-authority decision. Reverses the original plan's mode-switch architecture. |
| Best-fit single-symphony selection (NOT fan-out) (operator clarification + panel BC) | Operator's mental model: ONE symphony exits per cycle when port signal fires. Multi-cycle convergence. Avoids panel BC H1 (fan-out fires constituents whose individual state contradicts the signal) and panel BC C2's information loss by treating port signal as a target profile + selection problem. |
| Per-account params in port-mode (operator clarification) | Single PARABOLIC_VELOCITY_THRESHOLD per account when in port-mode (NOT per-symphony per-mode). Simpler config surface; matches the altitude semantics. |
| Drop value-weighting for path-dependent fields (panel BC C1) | max_drawdown, simple_return, time_weighted_return are path-dependent or chain-linked. Goldberg-Mahmoud 2017 / Pospisil-Vecer 2010: value-weighting non-coincident extrema is mathematically meaningless. Recompute from port-equity series. net_deposits arithmetically sums. Only current_return, last_percent_change, last_dollar_change validly value-weight. |
| Per-symphony HWM not a safety floor in port-mode (panel BC C2 -- operator accepts with eyes open) | Operator chose port-mode authoritative means port altitude is in charge. Per-symphony HWM continues to compute + display, but its triggered=True is observed-only. The dual-altitude UX gives the operator visibility into the per-symphony signals they're forgoing. |
| port_state typed table (panel BC C4 -- option b recommended) | Mirrors R1 fleet_alert_state precedent; per-field SQL; per-account row; new helpers with RO support; R7 WAL compatibility preserved. Better than JSON-key-in-bot_state because port_state queries don't require parsing a blob. |
| wipe_transient_state allowlist (panel BC C3) | Refactor the database.py:159-185 blanket clobber to an allowlist of reserved top-level keys. Latent bug for last_market_close_snapshot exists today; port-level work is the forcing function. |
| port_breakeven_active non-latching (panel BC C5) | Breakeven latching is a position-lifetime invariant. Port composition is non-stationary. Strip the latching invariant; re-evaluate each cycle. Same loosening for port-state armed / para_armed. |
| Best-fit metric = L1 sum-of-absolute-deviations (default; Euclidean as adversarial alt) | Tractable; order-independent; intuitive (the symphony that exits the most of the target with the least over/under-shoot). Single-pick variant of multi-item knapsack (Martello-Toth 1990) but simpler -- target is the constraint not the optimum. Euclidean kept as adversarial alternative for test-writer to validate selection robustness. Panel re-validation should sanity-check this choice. |
| No-good-match abort (P2.7.5) | When best score exceeds threshold, the engine refuses to act -- operator review banner. Preserves correctness over throughput. |
| Multi-cycle convergence (NOT same-cycle iterative selection) | One exit per cycle gives the engine time to observe market reaction before the next selection. Reduces over-trading. Convergence guarantee: max N selections for N symphonies. |
| Fail-STOP if port-mode params missing (panel BC C6) | Fixes original plan's contradiction (P1.9.3 fallback to per-symphony params was methodologically invalid). Engine refuses to enter port-level authority for that account; falls back to per-symphony AUTHORITATIVE while continuing observed-only port compute. |
| math_mode column on exit_triggers (panel BC H4) | Forensic completeness. Operator toggles mode Mon/Tue/Wed; column lets reconstruction of which mode each trigger fired under. NULL backfill is safe (pre-migration rows). |
| Active-authority badge in LEFT column (panel BC H6) | Co-located with DRY-RUN / LIVE execution-context badges. Avoids alarm-blindness from co-location with rose-toned staleness badge. Neutral indigo styling. |
| Per-row authoritative indicator | New since the original plan. Each row carries an AUTHORITATIVE / OBSERVED-ONLY badge so the operator scans which altitude is firing for each symphony / port. |
| Mode-invariant param subset (panel M1) | TAKE_PROFIT_MC_PCT, VWAP_BLEED_MULTIPLIER, VWAP_BLEED_TICKS, MAX_PARABOLIC_SQUEEZE have same magnitude semantics regardless of altitude. Single shared study; avoids 4x redundant search work + DSR moment dilution. |
| DSR T-correction restored (panel BC C7) | T = validation_calendar_days * N_symphonies, not * 1. Selection-bias correction silently degraded under the original plan; this restores Bailey-de-Prado 2014 Eq. 9 correctness. |
| Frozen-eval >=25 trading days in port-mode (panel BC C8) | Per-symphony 4-5 * N gives ~300 obs; port 4-5 * 1 gives ~5 obs -- statistically unfit for purpose. Extend in port-mode only via mode-aware TRAIN_RATIO relaxation. Purged k-fold is the deferred alternative. |
| Re-validation by light panel before Gate-2 | This rewrite introduces best-fit selection (novel), per-account params, dual-altitude state. The novel selection algorithm in particular should be validated by quant-risk-researcher + optuna-specialist + risk-engine-specialist (math + methodology + engine surfaces) before Gate-2 dispatch. |

## Scope Boundaries

**IN:**
- Dual-altitude state model: per-symphony + per-account always computed and persisted.
- Exit-authority toggle: EXIT_AUTHORITY env var; settings UI; sticky restart notice; left-column badge; per-row authoritative indicator.
- port_aggregator.py with per-field aggregation method (value-weighted vs path-recomputed vs sum).
- Port-equity series construction (1-minute + daily-close; composition-hash cache key).
- port_selector.py with L1 selection metric, tie-breakers, no-good-match abort.
- port_state typed table (migration 010); helpers; wipe_transient_state allowlist refactor.
- Composition-change detection in resolver; rebase semantics.
- port_breakeven_active non-latching; armed / para_armed non-latching on port_state.
- Multi-cycle convergence semantics.
- exit_triggers schema: math_mode + port_trigger_id + partial index (migration 011).
- Autotuner per-mode + per-account: math_mode column (migration 012); mode-specific / mode-invariant split; DSR T-correction; frozen-eval fold extension in port-mode; fail-STOP startup posture.
- Dashboard: pinned port row; per-row authoritative badge; port-trigger glyph + tooltip; segmented control; per-port shadow aggregation.

**OUT:**
- Cross-account aggregation (one port per account; multi-account = multi-port; explicitly no super-port).
- Allocation-level overlay / position resizing (Path B from convener synthesis was rejected as likely-infeasible without Composer API gain -- exits-only platform).
- Mid-cycle exit-authority switching (restart-gated).
- Per-symphony stop-loss as a safety floor IN port-mode (operator accepts with eyes open -- panel BC C2; per-symphony altitude continues to compute + display but is non-authoritative).
- Composer allocation resizing (allocation-level overlay path explicitly out).
- Replacing the multi-trigger architecture (TP + Trailing + VWAP-Breakdown + VWAP-Bleed remain four parallel triggers in both altitudes).
- Changing EXECUTION_START_TIME action gate (applies identically in both altitudes; port-level math still gates at 10:30 ET for action phase).
- PARA velocity cross-account aggregation (PARA is per-port = per-account; explicitly NOT cross-account -- panel M9).
- Reporting + AI Advisor adaptation in port-mode -> DEFERRED to a follow-on plan (panel H9) UNLESS operator decides to fold in now (open question in re-validation).
- Hybrid OR-gate (Path C from convener synthesis was a panel-suggested alternative; operator's clarified mental model supersedes it -- dual-altitude state with exit-authority selection IS the operator's preferred shape).

## Dependencies

- **engine-correctness-remediation.merged.md complete** (it is -- merged on main). E1 (PARA-ARM-at-open fix), E2 (monotonicity), H1 (telemetry), H2 (priority resolution), H3 (MC seeding), O1-O5 (Optuna methodology), V1 (sweep), V2 (open-window gate), V3 (fleet circuit breaker), I1-I3 (investigations) are all live invariants this plan consumes.
- **Panel BCs from docs/research/port-level/2026-05-18-CONVENER-synthesis.md folded in** (28 BCs + 40 PAs). Critical BCs C1-C8 addressed in P2.4 / P2.5 / P2.6 / P2.7 / P2.11. Operator-accept-with-eyes-open: BC C2 (per-symphony HWM not a safety floor in port-mode). High BCs H1-H9 addressed in P2.6 / P2.7 / P2.5 / P2.10 / P2.9 / P2.2. Medium amendments M1-M18 folded across relevant ACs.
- **Re-validation by light panel before Gate-2**: this rewrite is substantively different from what the original panel validated. The novel best-fit selection algorithm in P2.7, the dual-altitude state model in P2.1, and the per-account param shape in P2.3 are new. Recommended re-validation panel: quant-risk-researcher (selection metric + literature grounding for L1-distance-on-target-profile), optuna-specialist (per-account params + DSR / frozen-eval still hold), risk-engine-specialist (composition-change semantics + non-latching breakeven still hold). Light scope -- not a full Hex re-panel.
- **After this plan ships**: V1-equivalent sweep RE-RUN per account in port-mode to produce port-level parameter recommendations (per-account, not per-symphony).
- **V3 (fleet-correlation circuit breaker)** semantics adapt automatically -- fleet in port-mode = number of ports, not symphonies. Already handled by V3's existing abstraction.
