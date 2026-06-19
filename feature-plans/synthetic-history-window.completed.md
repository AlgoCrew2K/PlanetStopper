# Feature: Synthetic History Window — Remediation Cluster 5
Status: ready
Created: 2026-05-22

## Summary

Cluster 5 of the AlphaBot v3 math-audit remediation. `synthetic_history.py` is the live Alpaca historical fetcher that feeds the autotuner's walk-forward replay — so any shortfall or misalignment in the data it produces propagates directly into the parameters selected for real-money deployment. This cluster fixes the fetch-window shortfall (the calendar window does not reliably yield the trading days the autotuner + MC warmup require), the hardcoded UTC-4 offset (wrong for ~5 months/year — US Eastern is UTC-5 in standard time), and a bare `except`. It also triages and resolves a 4-test orphaned RED suite in `tests/synthetic_history/` that has been failing since before Cluster 3 and was never implemented.

Audited at `main @ 53ef340`; branches from `main @ 4524124` (post-Cluster-4). The exact findings + line numbers are in `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\docs\research\math-audit\` — re-locate against current code.

## Acceptance Criteria

- [ ] AC-1 (fetch-window shortfall): the historical fetch window is sized from a TRADING-DAY requirement, not a fixed calendar-day count. It must guarantee at least the autotuner's walk-forward trading-day count (125) + the MC warmup floor (`MC_MIN_HISTORY_DAYS + MC_VOL_WINDOW_DAYS - 1` eligible days) + a holiday/margin buffer. The window is computed via `market_calendar` (count back N trading days), not a `180`-day literal. Test: across a window spanning multiple market holidays, the fetched set still yields ≥ the required trading days.
- [ ] AC-2 (UTC-4 DST bug): the hardcoded UTC-4 offset is replaced with a DST-aware zone (`zoneinfo` `America/New_York`). Session boundaries / bar timestamps resolve correctly in both EDT (UTC-4, summer) and EST (UTC-5, winter). Test: a standard-time date and a daylight-time date both resolve to the correct offset.
- [ ] AC-3 (bare except): `synthetic_history.py`'s bare `except` → a specific exception set + a logged WARNING naming the failure and the affected symbol/date.
- [ ] AC-4 (orphaned RED triage — "Ruling 1b"): the 4 pre-existing RED tests — `tests/synthetic_history/test_insufficient_mc_replay_safe.py:98/:204/:220` and `tests/synthetic_history/test_calibration_alignment.py:351` — are each triaged against the post-Cluster-2/3 reality. Per test, exactly one of: (a) the asserted behavior is genuinely correct and missing → implement to GREEN; or (b) the asserted behavior is superseded by Cluster 2's MC None-sentinel contract / Cluster 3's AC-5 replay handling → the test is deleted with a one-line rationale. No test is left RED. See Decision D6.
- [ ] AC-5 (regression): every changed layer ships a golden-fixture or property test; the full tree is green with ZERO unexplained failures (the 4 orphaned reds resolved to GREEN or deleted); behavior shifts re-pinned with provenance; genuine full-tree count + HEAD SHA quoted.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| D6 — orphaned-RED triage policy | Per-test, evidence-based: implement-to-GREEN vs delete-with-rationale, decided against the MERGED Cluster 2/3 decisions. | The reference is Cluster 2 decision D2 (MC-insufficient history → bypass the MC gate, fail-safe, out-of-band `None` sentinel) and Cluster 3 AC-5 (replay insufficient-MC handling matches the production fail-safe). A test asserting behavior that CONTRADICTS those merged decisions is obsolete → delete, rationale in the commit. A test asserting correct behavior those decisions did not cover → implement to GREEN. quant-test-writer + risk-engine-specialist make each call. `test_calibration_alignment.py:351` is a calibration-accumulator structural test — triage whether it shares the Ruling-1b root cause or is separately stale. |

## Architecture

| Finding | File / function | Change |
|---|---|---|
| fetch-window shortfall | `synthetic_history.py` fetch-window calculation | size from a trading-day count via `market_calendar` |
| UTC-4 DST bug | `synthetic_history.py` timezone handling | `zoneinfo` `America/New_York` |
| bare except | `synthetic_history.py` | specific exception set + WARNING log |
| orphaned RED suite | `tests/synthetic_history/test_insufficient_mc_replay_safe.py`, `test_calibration_alignment.py` | triage per D6 |

## Edge Cases

- A window whose span crosses a DST transition (the fetch covers both EDT and EST days).
- A window spanning an unusual holiday cluster (Thanksgiving week, year-end).
- Alpaca returning fewer bars than requested (partial data) — the bare-except replacement must surface this, not swallow it.
- The MC warmup floor interacting with the replay window — the fetched history must cover BOTH (warmup precedes the 125-day replay).

## Security Considerations

Internal data-fetch change. `synthetic_history` hits the Alpaca market-data API (read-only, no orders). No new user input, no auth surface change. The bare-except fix is itself a safety improvement — a swallowed fetch error currently produces silently-incomplete history that the autotuner then optimizes against. `quant-code-reviewer`'s gates apply.

## Testing Strategy

- Golden-fixture / property tests for the trading-day window sizing (holiday-spanning), the DST offset (a winter date + a summer date), the exception handling.
- The orphaned-RED triage: each of the 4 tests ends GREEN or deleted-with-rationale; the full tree has zero unexplained failures.
- `market_calendar` is the source of truth for trading-day counts — tests assert against it, not hardcoded day counts.
- Full tree green; genuine whole-tree count + HEAD SHA quoted in every handoff.

## Scope Boundaries

- **IN**: `synthetic_history.py` — fetch-window sizing, timezone handling, exception handling; the 4-test orphaned RED suite in `tests/synthetic_history/`.
- **OUT**: the autotuner statistics / replay (Clusters 3-4, merged); `math_engine` MC math (Cluster 2, merged); portfolio / analytics (Cluster 6). If the timezone or fetch-window work uncovers a genuine Alpaca-API contract question, the team escalates for a `composer-alpaca-integration` / `alpaca-api-researcher` consult rather than expanding scope.
