# Feature: Portfolio Analytics Integrity — Remediation Cluster 6
Status: ready
Created: 2026-05-22

## Summary

Cluster 6 — the FINAL cluster of the AlphaBot v3 math-audit remediation. It sweeps up every remaining math-audit finding in the portfolio-aggregation and analytics surface not already remediated by Clusters 1–5: the max-drawdown `ZeroDivisionError`, the shadow-trajectory position-boundary handling, drawdown sign-convention inconsistencies, and any other portfolio/analytics integrity finding in the audit reports. Per the standing remediation rules, EVERY finding is fixed regardless of severity — severity sequences the work, it does not gate it. When this cluster merges, the entire math audit is remediated.

Audited at `main @ 53ef340`; branches from `main @ babd328` (post-Cluster-5). The full finding set + line numbers are in `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\docs\research\math-audit\` — primarily `risk-math__2026-05-21.md` and `invariant-coverage__2026-05-21.md`. Re-locate against current code.

## Acceptance Criteria

- [ ] AC-1 (finding enumeration): quant-test-writer reads every audit report in `docs/research/math-audit/` and enumerates EVERY finding touching the portfolio-aggregation / analytics / drawdown surface that is NOT already remediated by Clusters 1–5 (cross-reference the merged work). This enumerated set is the binding cluster scope. Any finding whose remediation needs a genuine design decision is escalated to team-lead; all others are remediated in this cycle. No finding is punted on severity.
- [ ] AC-2 (max-drawdown ZeroDivisionError): the max-drawdown computation no longer raises `ZeroDivisionError` — a zero/empty peak, an empty equity series, or any zero-denominator case returns a defined sentinel / `0.0` with the convention documented. A test drives each degenerate input.
- [ ] AC-3 (shadow-trajectory position boundary): the shadow-trajectory / comparison computation handles position-boundary transitions correctly — entry/exit days and the first and last data points. A test pins the boundary behavior.
- [ ] AC-4 (drawdown sign convention): drawdown sign is consistent across every producer and consumer (computation, persistence, reporting, dashboard). One documented convention; a test asserts every surface agrees.
- [ ] AC-5 (remaining enumerated findings): every other finding from AC-1's enumeration is remediated, each with a golden-fixture or property test.
- [ ] AC-6 (regression): full tree green; behavior shifts re-pinned with provenance; genuine whole-tree count + HEAD SHA quoted in every handoff.

## Architecture

| Finding | File / function | Change |
|---|---|---|
| max-drawdown ZeroDivisionError | the drawdown computation (port_aggregator.py / analytics / reporting.py) | guard the zero-denominator / empty cases |
| shadow-trajectory boundary | the shadow-trajectory / comparison computation | correct position-boundary handling |
| drawdown sign | every drawdown producer + consumer | one consistent documented convention |
| remaining enumerated findings | per AC-1 enumeration | per finding |

The team locates exact files against the audit reports + current code (the audit's line numbers are at `53ef340` and will have drifted).

## Edge Cases

- Empty equity / return series; a single data point.
- A portfolio with zero positions, or zero total value.
- A peak value of zero (the drawdown denominator).
- Position entry on the first day / exit on the last day (shadow-trajectory boundaries).
- All-positive or all-negative trajectories (drawdown sign).

## Security Considerations

Internal analytics / aggregation math. No new user input, no external calls, no auth surface. The dashboard is a read-only operator surface. `quant-code-reviewer`'s gates apply — especially Gate 6 (named constants) and the math-safety gate.

## Testing Strategy

- Golden-fixture / property tests for each remediated finding; expecteds independently computed, never producer-pinned.
- Degenerate-input tests for every guarded zero/empty case (AC-2).
- Sign convention: a test asserting every drawdown surface agrees on sign (AC-4).
- Full tree green; genuine whole-tree count + HEAD SHA in every handoff.

## Scope Boundaries

- **IN**: the portfolio-aggregation / analytics / drawdown surface — `port_aggregator.py`, `port_selector.py`, the analytics computations, drawdown math wherever it lives, and the reporting / persistence of those values. Every remaining math-audit finding in this surface.
- **OUT**: `math_engine` exit math (Clusters 1–2, merged); autotuner replay + statistics (Clusters 3–4, merged); `synthetic_history` (Cluster 5, merged). If an enumerated finding overlaps an already-merged cluster, note it as already-remediated — do not re-do it.

## Decisions

None pre-set. If AC-1's enumeration surfaces a finding whose remediation needs a design decision, it is assigned D7+ and escalated to team-lead.
