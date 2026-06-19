# Feature: Autotuner Replay Parity — Remediation Cluster 3
Status: ready
Created: 2026-05-21

## Summary

Cluster 3 of the AlphaBot v3 math-audit remediation. The autotuner's walk-forward simulation (`run_simulation` / `_collect_sim_returns`) is the objective function every deployed parameter set is selected against — but it re-implements the exit logic with inline literals instead of calling the production `math_engine` functions, so it optimizes a different system than the one trading real money. This cluster makes the per-symphony replay FAITHFULLY reproduce the production exit path.

Covers autotuner-surface findings: the replay open-codes the trailing-stop exit (CRITICAL), the replay omits the VWAP open-window grace suppression (CRITICAL), the replay take-profit re-arm divergence (HIGH), the synthetic-history MC `k=5` vs production `k=150` (HIGH), the insufficient-MC replay substitute (handed off from Cluster 2), the fixed-`390` `time_ratio` literal (LOW), and the autotuner's port-level replay blind spot.

Audited at `main @ 53ef340`; branches from `main @ d97a383` (post-Cluster-2). Line numbers reference the audit SHA — re-locate against current code (Cluster 1 already changed `compute_breakeven_update`'s signature and Cluster 2 changed `run_monte_carlo` — the replay's call sites reflect those).

## Acceptance Criteria

- [ ] AC-1 (replay open-codes the exit): `run_simulation` and `_collect_sim_returns` call `math_engine.compute_exit_confirmation` for the trailing-stop exit decision — the inline `ret <= (stop_level - 0.10) and mc < 60.0 ... below_stop_count >= 3` block is removed. No exit-rule literal is duplicated; replay and production share the one function.
- [ ] AC-2 (VWAP grace): the replay suppresses both VWAP signals during the open-window grace period — the faithful equivalent of production's `is_in_open_window_grace` gate (e.g. `tick_idx < VWAP_OPEN_WINDOW_GRACE_MINUTES`). No phantom early-session VWAP exit.
- [ ] AC-3 (TP re-arm): the replay resets the take-profit confirm counter on a sub-threshold MC dip, matching production. Preferred: extract the production take-profit confirmation into a shared `math_engine.compute_tp_confirmation` (named confirm-count constant) that both production and the replay call.
- [ ] AC-4 (MC neighbor count): `synthetic_history` passes `neighbor_k = MC_DEFAULT_NEIGHBOR_K` (production's 150), not `5`; paths may stay lower. The replay's `mc_prob` is no longer a 5-step CDF.
- [ ] AC-5 (insufficient-MC replay): the replay handles the insufficient-MC case consistently with production's fail-safe (Cluster 2's `None`-sentinel contract) — the `MC_INSUFFICIENT_REPLAY_VALUE` substitute is replaced with handling that does not fabricate an arm/exit and matches the production fail-safe direction.
- [ ] AC-6 (parity test): a bit-identical exit-decision parity test — drive a tick sequence through both the production exit path and the autotuner replay; assert the exit-decision sequence (which trigger fired, on which tick) is identical. Covers exit-confirmation, the VWAP open-window grace gate, and TP-confirm.
- [ ] AC-7 (time_ratio): the replay's `time_ratio` is derived from the actual session length (`tick_idx / max(1, len(ticks) - 1)`, or session datetimes), not the unnamed `390.0` literal — so half-day sessions reach full end-of-day tightening.
- [ ] AC-8 (port-level replay blind spot): the autotuner replay simulates only the per-symphony altitude. At minimum, document and guard this — port-mode autotuning results are not replay-validated; emit a clear warning / guard so port-mode tuning cannot be silently trusted. (Full port-level replay simulation is a larger feature — flag it if the team judges it in-scope; port-level exiting is currently dormant.)
- [ ] AC-9 (per-position reset reconcile): reconcile the audit's "replay resets per-position state each day, production never resets" finding with Cluster 1's outcome (production resets daily via `wipe_transient_state`; intraday a triggered symphony freezes). Confirm with a test that the replay's per-day-independent simulation is faithful to that; if not, fix.
- [ ] AC-10 (regression): every changed layer ships a golden-fixture or property-based test; the full `math_engine` + `portmode` + `autotuner` + `execution` suite stays green; new tests RED-verified against pre-fix code; shifted characterization fixtures re-pinned from the corrected replay.

## Architecture

| Finding | File / function | Change |
|---|---|---|
| Replay open-codes exit (C#1) | `autotuner.py` `run_simulation` / `_collect_sim_returns` | replace the open-coded exit block with `compute_exit_confirmation` |
| Replay omits VWAP grace (C#2) | `autotuner.py` replay trigger blocks | add the VWAP open-window grace suppression |
| TP re-arm (#9) | `autotuner.py` + `math_engine.py` | fix the TP re-arm reset; ideally extract `compute_tp_confirmation` |
| MC k=5 (#7) | `synthetic_history.py:237` | `neighbor_k` 5 → 150 |
| Cluster-2 handoff | `synthetic_history.py` / `autotuner.py` | insufficient-MC replay handling matches production fail-safe |
| time_ratio (#15) | `autotuner.py` | `time_ratio` from session length |
| port-level blind spot | `autotuner.py` | guard / document the port-level replay gap |

## Edge Cases / Decisions

- The replay calling `compute_exit_confirmation` changes `run_simulation`'s output — characterization fixtures shift (intentional; re-pin from the corrected replay).
- Half-day sessions (the `time_ratio` fix).
- **D-C3a (AC-3):** extract a shared `compute_tp_confirmation` (preferred — single source of truth) vs fix the replay reset in place. Team's call; the shared function is preferred, but extraction touches production — if it expands risk, fix-in-place is acceptable since the AC-6 parity test guards it either way.
- **D-C3b (AC-8):** minimal (guard + document the port-level replay gap) vs full port-level replay simulation. Default: minimal — port-level exiting is currently dormant (`.env` runs `per_symphony`); a full port-level replay is disproportionate. Flag if the team judges otherwise.

## Security Considerations

Internal autotuner change. No new user input, no external calls, no auth surface. The replay is offline — no broker side effects. `quant-code-reviewer`'s gates apply (especially: no exit-rule magic numbers duplicated; the replay must not gain a path to live execution).

## Testing Strategy

- The bit-identical exit-decision parity test (AC-6) is the centerpiece — production vs replay, same decisions on the same ticks; it must call the real `math_engine` functions, not mock them.
- Golden-fixture tests for each changed replay layer; re-pin shifted characterization fixtures from the corrected replay.
- Full `math_engine` + `portmode` + `autotuner` + `execution` suite green; new tests RED-verified against pre-fix code.

## Scope Boundaries

- **IN**: `autotuner.py` replay (`run_simulation`, `_collect_sim_returns`) — the `compute_exit_confirmation` call, the VWAP grace gate, the TP re-arm; `synthetic_history.py` (`neighbor_k`, insufficient-MC handling); `math_engine.py` only if extracting `compute_tp_confirmation`; the `time_ratio` fix; the port-level-replay guard.
- **OUT**: the DSR / autotuner statistics layer — Sortino, deflated Sharpe, `compute_dsr_T`, the penalty scalars, the walk-forward split (Cluster 4); `synthetic_history`'s fetch-window / timezone (Cluster 5); portfolio / analytics (Cluster 6). Cluster 3 is the replay's EXIT-LOGIC fidelity only — not the statistics.
