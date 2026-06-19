# Calibration Sweep (PARA + VWAP) — consolidated V1/W2

**Status:** ready
**Consolidates:** `engine-correctness-remediation.merged.md` §V1 (canonical) + `vwap-remediation.merged.md` §W2 (superseded into V1). One sweep cycle.
**Team:** Pent — quant-test-writer (LEAD/RED) + optuna-specialist (GREEN, owns autotuner sweep) + risk-engine-specialist (math) + quant-code-reviewer + doc-gen. Math-layer → quant-test-writer mandatory.

## Summary
The PARA + VWAP trigger constants (`PARABOLIC_VELOCITY_THRESHOLD`, `VWAP_CROSS_HWM_PCT`, `VWAP_BREAK_CONFIRM_TICKS`, `VWAP_BLEED_ARM_MIN`/`MAX`) are hand-set / aggressively tuned. The Tier-3 calibration-methodology fixes (O1/O2/O3/O5) and the E1 PARA-velocity fix have shipped. The existing Optuna walk-forward V1 calibration sweep (`run_calibration_sweep`) already covers **two** of these — `PARABOLIC_VELOCITY_THRESHOLD` + `VWAP_CROSS_HWM_PCT` — and deliberately leaves the bleed-arm/break params HAND-SET per V1's documented methodology review (sweeping them is a separate future operator-gated decision — see AC-1). **This cycle does NOT expand the search space.** It produces an **advisory per-symphony recommendation report** from the existing 2-param sweep's study output — current vs proposed values + expected trigger-frequency impact from historical replay + sensitivity bands — plus the minimal sweep robustness/hygiene additions the ACs require (AC-4 insufficient-history skip, AC-6 study-name hygiene, AC-5/AC-7 advisory flags). **Rollout is per-symphony, operator-gated — NO fleet-wide flip, NO auto-apply.** The sweep + report are off the live execution path (advisory tooling).

## Acceptance Criteria
- **AC-1** — The Optuna walk-forward V1 calibration sweep ALREADY EXISTS on origin/main (`autotuner.py:2893` `run_calibration_sweep`, with the `tests/calibration/v1/` suite) and deliberately covers TWO params: `PARABOLIC_VELOCITY_THRESHOLD` + `VWAP_CROSS_HWM_PCT`. The bleed-arm/break params (`VWAP_BREAK_CONFIRM_TICKS`, `VWAP_BLEED_ARM_MIN`, `VWAP_BLEED_ARM_MAX`) are intentionally LEFT HAND-SET per V1's documented methodology recommendation (AC-V1.1: "recommendation says leave clamp endpoints hand-set"). **This cycle does NOT expand the search space.** AC-1 = assert the EXISTING 2-param search space is present + correct (PARA + VWAP_CROSS_HWM_PCT in `OPTUNA_SEARCH_SPACE_KEYS`, with their existing bound constants); the existing CRRA-EU/CPCV/PBO objective is unchanged. (PLAN CORRECTION 2026-06-19: the original AC-1 here wrongly mandated a 5-param expansion — that pulled W2's superseded AC-2.1 over V1's shipped methodology decision. Corrected to respect the shipped 2-param design.)
- **AC-2** — New `scripts/vwap-calibration-report.py` reads a completed walk-forward study (optimization DB) and emits `docs/research/dashboard/vwap-calibration-report.md`: per symphony → current value, proposed value, expected trigger-frequency delta from historical replay, sensitivity band. Human-readable; deterministic from the study.
- **AC-3** — The report is ADVISORY ONLY. The sweep/report MUST NOT mutate live constants, MUST NOT write to `bot_state`, MUST NOT touch the execution path. No fleet-wide application. (Per-symphony deploy is a future operator-gated step, out of this cycle's scope.)
- **AC-4** — A symphony with insufficient history (<125 trading days) is skipped cleanly with a logged warning; the sweep does not crash.
- **AC-5** — Multiple-testing / overfit correction on the best-trial selection: the EXISTING PBO gate (`compute_pbo`, walk-forward Option A) is the chosen correction (DSR was removed in favor of PBO — do NOT re-add deflated-Sharpe). The report notes the PBO veto status per symphony so an inflated "best" is not surfaced as a recommendation.
- **AC-6** — Study-name hygiene: sweep studies use a fresh `<timestamp>__<symphony>__calsweep` name; never reuse a study name.
- **AC-7** — A symphony whose retune flips trigger frequency >2× vs current is flagged in the report for explicit operator review before any deploy.

## [PM-ASSUMED] methodology resolutions (operator may correct via PR/plan)
1. **Bleed-arm clamp tunability:** W2 §AC-2.1 says sweep `VWAP_BLEED_ARM_MIN`/`MAX`; V1 §AC-V1.1 cites a recommendation to "leave clamp endpoints hand-set." RESOLUTION (CORRECTED 2026-06-19): the SHIPPED V1 sweep already followed V1's recommendation — bleed-arm/break params are HAND-SET, sweep is 2-param. Respect that documented methodology decision: this cycle does NOT sweep the bleed-arm params. W2's AC-2.1 (sweep them) is SUPERSEDED by V1's review + ship; sweeping them is a SEPARATE future operator-gated methodology decision, NOT this cycle.
2. **Multiple-testing correction:** AC-2.5 (W2) asked for deflated-Sharpe (Bailey/López de Prado). The walk-forward overhaul shipped PBO (Option A) and explicitly REMOVED the DSR machinery. RESOLUTION: PBO is the multiple-testing guard (AC-5); do not re-introduce DSR.
3. **`VWAP_BREAK_CONFIRM_TICKS` (open Q whether to tune):** include it as tunable per the AC list; it's advisory.

## Architecture
- `autotuner.py` — **search space + objective UNCHANGED** (the V1 2-param sweep `run_calibration_sweep`:2893 already exists + is tested; do NOT add `VWAP_BREAK_CONFIRM_TICKS` / `VWAP_BLEED_ARM_MIN` / `VWAP_BLEED_ARM_MAX` to `OPTUNA_SEARCH_SPACE_KEYS` or to the sweep objective). **Minimal ADDITIVE changes ONLY** for the sweep robustness/hygiene ACs: AC-4 (skip <125-day symphonies cleanly + warn), AC-6 (`<ts>__<sym>__calsweep` study-name), and emitting the AC-5 PBO-veto / AC-7 >2×-flag advisory fields the report consumes. **[PM-ASSUMED 2026-06-19]** the original "autotuner.py UNCHANGED" line meant "no search-space change", not "zero edits" — these minimal robustness/hygiene additions are in-cycle; the contentious bleed-arm/break SWEEP expansion stays OUT (AC-1).
- `scripts/vwap-calibration-report.py` — NEW. Reads the optimization DB study (read-only) + replays trigger frequency from existing historical fixtures (no new external API calls — offline, existing pattern). Emits the markdown report.
- `math_engine.py` constants (`VWAP_BLEED_ARM_MIN/MAX`:785-786, `VWAP_BREAK_CONFIRM_TICKS`:820, + `PARABOLIC_VELOCITY_THRESHOLD`, `VWAP_CROSS_HWM_PCT` — locate exact lines) are READ for current values; NOT mutated by this cycle.

## Edge Cases
- Symphony <125 trading days → skip + warn (AC-4). Empty/failed study → report says "no recommendation" for that symphony, no crash. PBO-vetoed best trial → report flags it, does not recommend. Param at a search-space boundary → note in the report (may need a wider bound next sweep).

## Security Considerations
- No new external API calls (offline sweep against existing fixtures). No credentials. Report writes only to `docs/research/dashboard/`. Advisory — cannot place a trade or change live config.

## Testing Strategy
- RED first (quant-test-writer): search-space expansion includes the 5 params with correct bounds; objective/replay still runs; report generation from a fixture study (deterministic shape: per-symphony current/proposed/delta/band); insufficient-history skip; advisory-only invariant (no writes to bot_state / no execution-path import); PBO-veto surfaced; >2× flag. No hardcoded producer values — assert shape/presence, derive expected from the fixture study.
- Opt-in live tier (`test_live_*`) for one real walk-forward sweep on one symphony (gated, `--include-live`).

## Scope Boundaries
- IN: the advisory report script (`scripts/vwap-calibration-report.py`) + its tests + the generated report doc; the minimal additive autotuner sweep robustness/hygiene for AC-4 (insufficient-history skip) + AC-6 (study-name `__calsweep`) + the AC-5/AC-7 advisory row fields. The 2-param V1 sweep **search space + objective are UNCHANGED** — NO search-space expansion.
- OUT: search-space expansion / sweeping the bleed-arm/break params (superseded by V1's methodology review — a FUTURE operator-gated decision); applying any recommended constant (per-symphony operator-gated deploy is a FUTURE cycle); fleet-wide flip; changing the live execution path; re-adding DSR; H1 telemetry (shipped) + post-deploy verification (future).
