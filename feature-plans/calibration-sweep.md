# Calibration Sweep (PARA + VWAP) — consolidated V1/W2

**Status:** ready
**Consolidates:** `engine-correctness-remediation.merged.md` §V1 (canonical) + `vwap-remediation.merged.md` §W2 (superseded into V1). One sweep cycle.
**Team:** Pent — quant-test-writer (LEAD/RED) + optuna-specialist (GREEN, owns autotuner sweep) + risk-engine-specialist (math) + quant-code-reviewer + doc-gen. Math-layer → quant-test-writer mandatory.

## Summary
The PARA + VWAP trigger constants (`PARABOLIC_VELOCITY_THRESHOLD`, `VWAP_CROSS_HWM_PCT`, `VWAP_BREAK_CONFIRM_TICKS`, `VWAP_BLEED_ARM_MIN`/`MAX`) are hand-set / aggressively tuned. The Tier-3 calibration-methodology fixes (O1/O2/O3/O5) and the E1 PARA-velocity fix have shipped, so a principled Optuna walk-forward sweep over these constants is now unblocked. This cycle EXPANDS the existing autotuner search space to include them and produces an **advisory per-symphony recommendation report** — current vs proposed values + expected trigger-frequency impact from historical replay + sensitivity bands. **Rollout is per-symphony, operator-gated — NO fleet-wide flip, NO auto-apply.** The sweep + report are off the live execution path (advisory tooling).

## Acceptance Criteria
- **AC-1** — The Optuna walk-forward search space (`autotuner.py`) is expanded to include, as tunable params with sane bounds: `PARABOLIC_VELOCITY_THRESHOLD`, `VWAP_CROSS_HWM_PCT`, `VWAP_BREAK_CONFIRM_TICKS`, `VWAP_BLEED_ARM_MIN` (bound `[-5.0, -1.0]`), `VWAP_BLEED_ARM_MAX` (bound `[-1.0, -0.1]`). The existing walk-forward objective (CRRA-EU `_haircut_select` + CPCV + PBO gate) is reused unchanged — this cycle only widens the search space.
- **AC-2** — New `scripts/vwap-calibration-report.py` reads a completed walk-forward study (optimization DB) and emits `docs/research/dashboard/vwap-calibration-report.md`: per symphony → current value, proposed value, expected trigger-frequency delta from historical replay, sensitivity band. Human-readable; deterministic from the study.
- **AC-3** — The report is ADVISORY ONLY. The sweep/report MUST NOT mutate live constants, MUST NOT write to `bot_state`, MUST NOT touch the execution path. No fleet-wide application. (Per-symphony deploy is a future operator-gated step, out of this cycle's scope.)
- **AC-4** — A symphony with insufficient history (<125 trading days) is skipped cleanly with a logged warning; the sweep does not crash.
- **AC-5** — Multiple-testing / overfit correction on the best-trial selection: the EXISTING PBO gate (`compute_pbo`, walk-forward Option A) is the chosen correction (DSR was removed in favor of PBO — do NOT re-add deflated-Sharpe). The report notes the PBO veto status per symphony so an inflated "best" is not surfaced as a recommendation.
- **AC-6** — Study-name hygiene: sweep studies use a fresh `<timestamp>__<symphony>__calsweep` name; never reuse a study name.
- **AC-7** — A symphony whose retune flips trigger frequency >2× vs current is flagged in the report for explicit operator review before any deploy.

## [PM-ASSUMED] methodology resolutions (operator may correct via PR/plan)
1. **Bleed-arm clamp tunability:** W2 §AC-2.1 says sweep `VWAP_BLEED_ARM_MIN`/`MAX`; V1 §AC-V1.1 cites a recommendation to "leave clamp endpoints hand-set." RESOLUTION: sweep MIN/MAX as tunable (W2's explicit AC) — the report is advisory + operator-gated, so surfacing a recommendation is safe even if the operator chooses to keep them hand-set. Flagged for operator.
2. **Multiple-testing correction:** AC-2.5 (W2) asked for deflated-Sharpe (Bailey/López de Prado). The walk-forward overhaul shipped PBO (Option A) and explicitly REMOVED the DSR machinery. RESOLUTION: PBO is the multiple-testing guard (AC-5); do not re-introduce DSR.
3. **`VWAP_BREAK_CONFIRM_TICKS` (open Q whether to tune):** include it as tunable per the AC list; it's advisory.

## Architecture
- `autotuner.py` — extend the Optuna param-suggestion block (the `trial.suggest_*` search space) with the 5 constants; thread them into the replay/objective the same way existing params flow. Reuse `_haircut_select`, CPCV folds, the PBO gate. No new objective.
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
- IN: search-space expansion + the advisory report script + tests + docs.
- OUT: applying any recommended constant (per-symphony operator-gated deploy is a FUTURE cycle); fleet-wide flip; changing the live execution path; re-adding DSR; the H1 trigger-attribution telemetry (already shipped) and post-deploy verification (future, needs a real deploy).
