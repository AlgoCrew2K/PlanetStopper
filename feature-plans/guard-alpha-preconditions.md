# Feature: Guard-Alpha Stop-Justification Preconditions
Status: ready
Created: 2026-07-23

## Summary
Per-symphony measurement of the Kaminski & Lo (J. Financial Markets 2014) stop-loss preconditions, answering "is a trailing-stop overlay theoretically justified on THIS symphony?" from data the system already collects. K&L prove a stop overlay always reduces expected return under random-walk dynamics and adds value under momentum, with sufficient condition ρ ≥ SR at the same sampling frequency (see docs/research/methodology-validation-2026-07.md, finding verified 16-1). The feature computes, per symphony at daily frequency: lag-1 autocorrelation ρ̂ of the if-held return series (with a confidence interval), the same-frequency (daily, non-annualized) Sharpe SR̂, and an honest verdict class — surfaced read-only on the dashboard and via one new GET API. Advisory display only: no engine behavior changes, no automatic enabling/disabling of Guard Alpha.

## Acceptance Criteria
- [ ] AC-1: A pure function in `analytics.py` (or a new `analytics`-adjacent module) computes, from a daily return series: lag-1 autocorrelation ρ̂, a 95% CI for ρ̂ (stderr ≈ 1/√N; method documented via constant + source comment), daily non-annualized Sharpe SR̂, and observation count N. No I/O in the math function.
- [ ] AC-2: **Unit comparability is enforced structurally**: ρ̂ and SR̂ are computed from the SAME series at the SAME frequency (daily), and the comparison function takes them as one paired input so a caller cannot pass an annualized Sharpe. A test asserts the annualized value would flip the verdict on a fixture (regression guard for the unit-mismatch class).
- [ ] AC-3: Verdict classes, exactly these five: `SUFFICIENT_MET` (ρ̂ − CI ≥ SR̂), `SUFFICIENT_LIKELY` (ρ̂ ≥ SR̂ but CI overlaps), `NOT_MET` (ρ̂ + CI < SR̂ and random-walk not rejected → overlay is expected-return drag per K&L), `NEGATIVE_EDGE` (SR̂ ≤ 0 → ρ ≥ SR is trivially satisfiable; verdict class is distinct and its display copy explains the stop-value question differs on a negative-edge strategy), `INSUFFICIENT_DATA` (N < N_MIN_OBS, named constant with source comment; proposed 40 [PM-ASSUMED], aligned with the MC minimum-raw-history floor).
- [ ] AC-4: Verdict copy states the condition is **sufficient, not necessary** ("sufficient condition met/not met"), and that NOT_MET is an **expected-return** statement only — variance/drawdown reduction survives under IID (the CRRA objective legitimately values it). Never renders "stop unjustified".
- [ ] AC-5 (amended 2026-07-23 per PM ruling on cycle recon findings 1+2 — see `.claude/tdd-handoff.md` for full citations): Primary sample = a new small extraction helper reading `ticks[-1]["return"]` for EVERY date in the replay's `history_data[sym_id]` dict (percent units per the `RETURN_PCT_TO_FRACTION` convention) — NOT `_collect_sim_returns` (autotuner.py:1449), which returns sparse trigger-day-only guard-alpha deltas (`triggered_return - eod_return`, autotuner.py:390-427), a different quantity from the if-held series this feature needs. Secondary/live corroboration = the RAW per-day `current_return` values from `shadow_history`, in trading-day order — NOT diffed (production-proven empirically: `current_return` is already a per-day return, not cumulative-since-open — see project memory `project_shadow_return_per_day_proven_empirically.md`) — never `shadow_return` (the frozen per-day exit value), EOD-row-only (last row per trading_day by `ts_utc` — never an intraday row, which would silently change the sampling frequency and invalidate the ρ-vs-SR comparison), restricted to the current epoch via the `_get_shadow_cumulative_trajectory` epoch-resolution pattern (analytics.py:612) — never stitch across epochs.
- [ ] AC-6: New read-only `GET /api/guard-alpha-preconditions` returns per-symphony `{rho, rho_ci, sharpe_daily, n_obs, verdict, sample_source}` for both samples where available; auth via the global hook; NOT in `_SETTINGS_WRITE_ALLOWLIST`; no `LIVE_EXECUTION` interaction; malformed/missing-data-safe (per-symphony honest degradation, never a 500).
- [ ] AC-7: Dashboard panel (Performance tab) renders the per-symphony verdict table with ρ̂ vs SR̂ and the honest empty state when no symphony has sufficient data. 401-guarded fetch in JS, following the `fetchGuardAlphaSummary()` pattern.
- [ ] AC-8: When the two samples (replay vs live shadow) disagree on verdict class, both are shown with their N — never silently prefer one.
- [ ] AC-9: All new numeric constants named with source comments (Kaminski-Lo citation: DOI 10.1016/j.finmar.2013.07.001 / SSRN 968338 — NOT the dead alo.mit.edu URL).

## Architecture
- **Math**: new pure functions (module: `analytics.py` or `guard_preconditions.py` — implementer's call, but off the execution path either way): `compute_persistence_stats(daily_returns) -> PersistenceStats`, `classify_stop_justification(stats) -> Verdict`. No engine imports into the hot path; the 1-minute cycle never calls this.
- **Data**: replay sample via a new all-days if-held extraction helper reading `ticks[-1]["return"]` from the replay's `history_data` (NOT `_collect_sim_returns`, autotuner.py:1449 — recon resolved, PM-ruled 2026-07-23, see amended AC-5); live sample via `shadow_history` reads (migration 008), raw per-day `current_return`, EOD-row-only, current-epoch filter mirroring `analytics._get_shadow_cumulative_trajectory` (analytics.py:612).
- **API**: one GET route in `app.py` following `guard_alpha_summary()` (`app.py:2172`) — read-only SQLite, honest empty state, global auth hook.
- **UI**: Performance tab section in `templates/performance.html` + fetch/render in the corresponding static JS. No new template files.
- **Dependencies**: none new (stdlib/numpy already present).
- **Regime-switching condition (K&L's μ₂ < rf test): OUT** — deferred; requires regime-model estimation that would dominate the cycle. Noted in Scope Boundaries.

## Design-System Mapping
No design system declared. Follow house dashboard conventions: existing panel/table classes used by the Performance tab and the `dollar-saved-panel` pattern; semantic CSS classes for verdict chips (reuse the chip modifier pattern from `templates/ai_advisor.html` — canonical class names, no inline styles, no raw colors in JS).

## Edge Cases
- Retention-pruned shadow_history → small N (a live retune once had 29 usable days) → `INSUFFICIENT_DATA`, honest count shown.
- Epoch boundary inside the window → current-epoch-only; if the epoch is younger than N_MIN_OBS → `INSUFFICIENT_DATA`.
- Flat/zero-variance series → SR̂ undefined → guarded (no div-by-zero), maps to `INSUFFICIENT_DATA` with reason.
- SR̂ ≤ 0 → `NEGATIVE_EDGE` (must not fall through to `SUFFICIENT_MET`).
- NaN/missing days in either series → dropped pairwise with count reported; never forward-filled.
- Symphony present in replay but absent from shadow_history (never triggered / new) → replay-only row, `sample_source` honest.
- Multiple intraday `shadow_history` rows per trading day → EOD-only selection (last row by `ts_utc`); using any non-EOD row (or all rows) would silently change the sampling frequency, invalidating the ρ-vs-SR comparison (same failure class as AC-2, data-side instead of stat-side).
- All symphonies insufficient → panel-level empty state, not an empty 200 that renders as blank.

## Security Considerations
- Read-only surface: no new write paths, no settings mutation, not in `_SETTINGS_WRITE_ALLOWLIST`; CSRF not applicable (GET), auth via global hook (401 JSON for XHR).
- Response contains derived statistics only — no credentials, no raw API bodies, no file paths.
- Symphony names in JSON/HTML are escaped (`| e`, no `| safe`); no user-supplied input reaches the route (no query params beyond none — the route takes no parameters [PM-ASSUMED]).
- Oversized-data DoS: bounded by symphony count (~tens); no pagination needed.

## Testing Strategy
- **Golden-fixture tests (mandatory — math layer)**: fixture return series with known ρ and SR (constructed AR(1) with specified coefficient; IID control; negative-edge control) asserting exact ρ̂/SR̂/verdict outputs; the annualization-flip regression test (AC-2).
- **Unit**: verdict-class boundary tests (CI straddling each threshold), N_MIN gate, epoch filter, NaN handling, shadow_return-vs-current_return field-selection test (assert the query reads `current_return` — the field-semantics gotcha class).
- **Route tests**: auth-disabled fixture, empty-DB honest state, malformed rows, per-symphony degradation, schema of response.
- **JS**: extend the parametrized `tests/js_syntax` module (no new per-file node --check).
- **Behavioral (PM live gate, post-review)**: rendered Performance tab on the droplet against real shadow_history; screenshot read by PM per house rule.
- Responsive: not required beyond existing tab behavior.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Daily frequency for the K&L test | Exits are driven by daily-scale dynamics; shadow_history and the replay are daily series; K&L requires ρ and SR at one common frequency |
| ρ ≥ SR treated as sufficient-only, verdicts worded accordingly | Verifier caution in the validation report: reading it as necessary overstates the theory |
| Reuse autotuner replay series as primary sample | N≈250 vs ~90-day pruned shadow window → ρ̂ stderr ~±0.06 vs ±0.11; no new data infrastructure |
| Regime-switching (μ₂ < rf) test deferred | Requires regime-model estimation; separate cycle if wanted |
| Advisory display only — no auto-disable of Guard Alpha | Engine behavior changes are out of scope; operator decides what to do with verdicts |

## Scope Boundaries
- **IN**: persistence math + verdict classification, one GET API, Performance-tab panel, golden fixtures.
- **OUT**: regime-switching estimation; any change to exit decisions or engine codepaths; automatic per-symphony Guard-Alpha enable/disable; annualized-metric displays; Discord reporting of verdicts; historical multi-epoch stitching.
