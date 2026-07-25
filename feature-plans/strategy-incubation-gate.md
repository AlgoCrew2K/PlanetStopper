# Feature: Strategy Incubation Gate
Status: ready
Created: 2026-07-25

## Summary
The third and final graduated gate from the methodology-validation report (docs/research/methodology-validation-2026-07.md, recommendation 3): Strategy Builder survivors (built-new and atlas-suggested candidates that pass the FDR/PBO/SPY gates) no longer surface as recommendations immediately. Each survivor enters a forward-incubation ledger and is tracked on real forward market data (paper, zero capital, zero execution) for a minimum window; only candidates meeting forward criteria are PROMOTED to recommendation status. Besides matching documented allocator practice (≥6-month OOS incubation; retail-pragmatic window here), this is the structural mitigation for LLM parametric lookahead bias: forward data post-dates the generating model's training cutoff **by construction**, so promotion is the first memorization-free signal in the pipeline. Advisory-only end to end; zero live-execution surface.

## Acceptance Criteria
- [ ] AC-1: New additive migration 037 `strategy_incubation` (NULLable + DEFAULT, never destructive): candidate_hash (tree-structural SHA-256, the community_strats convention), tree_json, objective, provenance (built-new/atlas-suggested), admitted_at, backtest_mdd_pct, status (`INCUBATING`/`PROMOTED`/`FAILED`/`EXPIRED`), status_reason, promoted_at. Companion table `incubation_daily` (candidate_hash, trading_day, forward_return_pct, spy_return_pct) additive, one row per candidate per trading day. Migration count assertions per house rule (order-vs-neighbors, never is-last pins).
- [ ] AC-2: Admission: when the Strategy Builder pipeline (route or weekly scheduler) produces gate-survivors, each is REGISTERED into the ledger as `INCUBATING` instead of surfacing as a recommendation; registration is idempotent by candidate_hash (a re-generated identical tree does not reset an existing incubation clock). Cap: `MAX_INCUBATING = 20` [PM-ASSUMED] — beyond the cap, lowest-priority candidates (by gate-time oos_alpha) are not admitted, logged honestly.
- [ ] AC-3: Daily incubation tick (off-hours, off-execution-path, CC-2 lazy import, D-1 never-raises): for each `INCUBATING` candidate, fetch forward returns via ONE `composer_backtest_client.run_backtest` call per candidate per tick, plus one shared SPY call per tick (not per candidate). **AMENDED (recon finding c, 2026-07-24):** `run_backtest` has no date-window parameter — every call returns the full-history `daily_returns` series (ISO-date-keyed). The tick extracts every date key strictly after that candidate's last recorded `trading_day` in `incubation_daily` (filtered through `market_calendar.is_trading_day`) and appends one row per new trading day; this also backfills a multi-day daemon-down gap in the same single call (see Edge Cases). Bill-bounded: ≤ MAX_INCUBATING+1 Composer calls per day, batched in one tick (unchanged — one call per candidate regardless of how many new days it yields). A failed fetch degrades that candidate's day honestly (row absent; no fabrication); N consecutive fetch failures (`INCUBATION_MAX_FETCH_FAILURES = 5` [PM-ASSUMED]) or a Composer 422 tree-invalid → status `EXPIRED` with reason.
- [ ] AC-4: Promotion evaluation at each tick for candidates with ≥ `INCUBATION_WINDOW_TRADING_DAYS = 63` [PM-ASSUMED, ~3 months; report cites ≥6-month allocator practice — window is an operator knob, named constant] of forward rows: PROMOTED iff forward cumulative alpha vs SPY over the window ≥ 0 AND forward max-drawdown ≤ `INCUBATION_MDD_BREACH_MULT = 1.5` [PM-ASSUMED] × its stored backtest MDD. Early-fail any time: forward MDD breaches the multiplier → `FAILED` immediately (no need to wait the full window). All thresholds named constants with source comments; NONE tunable by Optuna or the advisor allowlist.
- [ ] AC-5: Surfacing: the Strategy Builder tab renders survivors with an honest status badge — "Incubating — day N of 63", "Promoted — recommended", "Failed incubation (reason)", "Expired (reason)" — and ONLY `PROMOTED` candidates render in the recommended/actionable styling. **AMENDED (recon finding + PM ruling, 2026-07-24):** `advisor_observations` rows are append-only/immutable (no update/delete accessor) — a status frozen into `raw_response` at persist time would always read "INCUBATING" and could never reflect a later promotion/failure, making the badge lie. The persisted advisory observation instead gains ONLY an additive, immutable `candidate_hash` join key (same additive-blob precedent as the shipped `run_id`/`invocation_source` fields). Status is computed LIVE at API/render time by joining `candidate_hash` against the incubation ledger (`GET /api/incubation` / `get_incubation_overview`) — never stored as a frozen status string. Existing consumers parse old rows unchanged (the new key is additive/ignorable).
- [ ] AC-6: Honest empty/degraded states everywhere: no incubatees → explicit empty state; ledger read failure → panel degrades per-section, never 500; a candidate with missing days shows its true row count ("day N of 63 · M days observed").
- [ ] AC-7: Read-only `GET /api/incubation` (per-candidate status + forward stats + day counts), global auth hook, not in `_SETTINGS_WRITE_ALLOWLIST`, no `LIVE_EXECUTION` interaction, strict-JSON-safe (NaN/Infinity sanitized at the boundary — the shipped `_json_safe_float` pattern).
- [ ] AC-8: The lookahead-bias rationale is documented at the promotion-decision code site and in DECISIONS (forward data post-dates the generating model's cutoff by construction — the first memorization-free signal; cite the validation report's parametric-lookahead finding).
- [ ] AC-9: Zero changes to live exit-decision codepaths (`alpha_bot_execution.py` / `math_engine.py` decision functions byte-untouched; blast-radius guard per the shipped pattern). The 1-minute engine cycle never touches incubation code.
- [ ] AC-10: All timestamps/day-keying use the existing ET trading-day conventions (`market_calendar.is_trading_day`, `synthetic_history.utc_to_eastern`); no naive-datetime or weekday-proxy logic.

## Architecture
- **DB**: migration `migrations/037_strategy_incubation.sql` + accessors in `database.py` (register_incubation_candidate, get_incubating, append_incubation_day, set_incubation_status, get_incubation_overview) — parameterized, `get_ro_connection()` for reads (sleeves convention), sentinel-safe.
- **Engine**: new `advisors/incubation.py` — admission adapter, the daily tick (`run_incubation_tick()`), promotion evaluation. D-1 contract throughout; `type(exc).__name__` error surfacing. **AMENDED (recon finding b, 2026-07-24):** both the route (app.py:3813) and the weekly scheduler funnel through the SAME production call site — `strategy_builder_engine.propose_strategies()` Step 5's survivor-persist loop (`advisors/strategy_builder_engine.py:1065-1085`, which calls `_persist_survivor`). The admission adapter is wired at exactly ONE seam there, per screened survivor — not separately at the route and the scheduler — removing double-admission risk. `candidate_hash` is computed from `info.tree` reusing `community_strats`' tree-structural SHA-256 convention (`_composition_hash`, `advisors/community_strats.py:87-113`) — implementer's choice to import the private name directly or add a one-line public alias in `community_strats`; either satisfies reuse-don't-fork, and a hash-equality property test pins the two never diverging. `CandidateInfo.candidate_id` (`plan["plan_id"]`) is NOT this hash and must not be used as the ledger key.
- **Scheduling**: the daily tick invoked from the daemon's existing off-hours daily slot (same CC-2 pattern as the 03:00 lens pipeline — lazy import from app.py's scheduler; NOT a new OS timer). **CONFIRMED (recon finding a, 2026-07-24):** `app.py:931-940 run_scheduler()` registers `schedule.every().day.at("HH:MM").do(fn)` jobs; 03:00 is already `_run_lens_pipeline`. The incubation tick registers at **03:30**, same thin-wrapper-spawns-daemon-thread pattern as `_run_lens_pipeline`/`_lens_pipeline_worker` (app.py:905-928).
- **Composer usage**: exclusively via the existing `composer_backtest_client` (1 req/s, fee/slippage params as shipped — forward segments are therefore net-of-cost, consistent with the gate).
- **UI**: Strategy Builder tab badge rendering in `static/ai_advisor.js` + template; `GET /api/incubation` in app.py.
- **Reuse, don't invent**: candidate_hash = community_strats' tree-structural SHA convention; forward-alpha math mirrors backtest_gate_engine's SPY-baseline shape; no new statistical machinery.

## Design-System Mapping
No design system declared. House conventions: BEM-style status chips (the prism/verdict chip pattern), `data-testid` attributes, textContent-only DOM building, no new timers (fold into existing SPA refresh), CSS vars only.

## Edge Cases
- Candidate tree becomes invalid at Composer mid-incubation (422) → `EXPIRED`, reason recorded, honest badge.
- Market holidays/weekends → no tick rows; day counting uses trading days only.
- Duplicate candidate (same hash) re-proposed while INCUBATING/PROMOTED/FAILED → idempotent: no clock reset; FAILED hashes are not re-admitted for `INCUBATION_REFRACTORY_DAYS = 90` [PM-ASSUMED].
- Cap overflow (>20 survivors) → honest not-admitted logging; never silent.
- Daemon down for days → tick catches up from last recorded trading_day (window is calendar-anchored to admitted_at, not tick-count-anchored). **AMENDED (recon finding c):** this falls out of the AC-3 full-history-call + date-diff-extraction algorithm at zero extra Composer calls — a single `run_backtest` response already spans the entire gap; the tick simply inserts every date key after the last recorded row.
- Composer response missing days (its own data gaps) → absent rows, true-count display, no interpolation.
- SPY fetch fails but candidate fetches succeed → candidate rows recorded, promotion evaluation deferred (needs the benchmark; never promote benchmark-blind).
- Migration on droplet with live daemon → additive-only guarantees safe restart-applies (house pattern).

## Security Considerations
- Read-only API surface; no new write endpoints; admission/tick are internal producers only. No `LIVE_EXECUTION`, not in `_SETTINGS_WRITE_ALLOWLIST`.
- tree_json stored server-side only; the API returns derived stats + status, never raw trees (no tree exfil via the dashboard; the SPA already has its own tree display path with its own controls).
- Composer calls bounded (cap + 1 req/s + daily batch) — bill protection; no credential logging (redaction rule).
- Status/reason strings are static tokens, never `str(exc)` echoes (the C5 sanitized-error precedent).

## Testing Strategy
- **Golden fixtures (math layer rule)**: promotion evaluation over constructed forward series (pass, alpha-fail, MDD-early-fail, benchmark-missing-deferral); fixture-derived assertions, no producer literals.
- **Migration tests**: 037 applies on a 036 DB; order-vs-neighbor assertions; old-DB compat.
- **Idempotency/cap/refractory tests**; tick catch-up after gap; 422→EXPIRED; fetch-failure escalation to EXPIRED at the named threshold.
- **Seam-mocked Composer** throughout (fixture-first house rule; the live-API structural guard applies — no unmocked Atlas/Composer in tests).
- **Route tests**: schema, auth, strict-JSON (NaN guard), degraded states. JS via the parametrized syntax module.
- **Suite scope**: advisors/ + database/ + app/ + ui/ + dashboard/ + reporting/ sweeps locally (the outside-touch-set lesson); CI full tree.
- **PM live gate**: harness with fresh droplet snapshot — admission of a real recent survivor batch (if any exist in advisor_observations), ledger rows visible, badges render, API strict-JSON; the first REAL tick observed post-deploy is the watch item (mirrors the two shipped watch patterns).

## Decisions
| Decision | Rationale |
|----------|-----------|
| Window = 63 trading days [PM-ASSUMED, operator knob] | Report documents ≥6-month allocator practice; 3 months is the retail-pragmatic floor that still delivers a memorization-free signal; named constant, trivially raised |
| Promotion = forward alpha vs SPY ≥ 0 AND MDD ≤ 1.5× backtest [PM-ASSUMED] | Reuses the gate's own SPY-baseline worldview on genuinely forward data; MDD breach early-fail catches blowups without waiting |
| Forward data via daily Composer backtest windowed from admission | The only existing evaluator of arbitrary trees; bounded (≤21 calls/day); net-of-cost by the client's shipped fee params; building a local tree interpreter would be a new engine (rejected scope) |
| Incubation thresholds never tunable | Same rationale as the friction constant — the pipeline must not optimize its own gate |
| Idempotent admission by tree hash, no clock resets | Prevents re-generation from laundering a candidate past its own history |

## PM Rulings (recon amendments, 2026-07-24)
All five ga3-tw recon findings (a-e) plus the AC-5 badge-honesty finding were RULED ACCEPTED by the PM. Nothing below lives only in chat — this is the frozen record, reproduced verbatim in `.claude/tdd-handoff.md`:
- (a) 03:30 daemon slot via the exact lens-pipeline CC-2 wrapper pattern — approved, no standalone systemd timer.
- (b) Single wiring point at `propose_strategies` Step 5 — approved. `candidate_hash` reuses `community_strats._composition_hash` (import directly or add a one-line public alias — implementer's choice); a hash-equality property test is mandatory.
- (c) AC-3 amended: no windowed Composer API exists — one full-history `run_backtest` call per candidate per tick + one shared SPY call, new-rows-since-last-recorded-trading-day extraction. Catch-up-after-gap is free; bill-bound unchanged (≤ MAX_INCUBATING+1 calls/tick, pinned by a mock call-count test).
- (d) Migration 037 + `tests/database/test_037_strategy_incubation.py` naming convention — approved.
- (e) Day-zero admissible survivors from pre-existing `advisor_observations` rows — moot by Scope Boundaries (backfilling forward data for pre-feature survivors is explicitly OUT); the PM live gate probes real post-deploy admission instead.
- AC-5 amended: persist ONLY the immutable `candidate_hash` join key into `raw_response`; status is computed LIVE at render/API time via the ledger join — never a frozen status string.

## Scope Boundaries
- **IN**: ledger + daily tick + promotion logic + badges + API + migration 037.
- **OUT**: any change to which candidates the FDR/PBO/SPY gates pass; live execution of promoted strategies (that is the future live-execution epic); backfilling forward data for pre-feature survivors; a local tree evaluator; notification/Discord surfaces for promotions (follow-on candidate); changes to the K-L or friction features.
