# Feature: Cross-Asset Risk-Appetite Lens
Status: ready
Created: 2026-06-14

## Summary
A new lens producer measuring **cross-asset risk appetite** (risk-on ↔ risk-off) — an INDEPENDENT signal from the equity lenses (technicals/breadth) and the vol lens (derivatives/VIX). Built from free, Alpaca-fetchable ETFs. Instrument set settled by two research passes (afade852, a42e1ce1) + operator sign-off 2026-06-14: span the most independent factors with the fewest instruments; inflation is deliberately EXCLUDED (kept as a separate macro-lens concern — RINF goes to the macro lens, not here).

## Instrument set (operator-approved, research-settled)
| Instrument | Factor captured |
|------------|-----------------|
| **HYG − LQD** (relative/ratio, z-scored) | credit-spread risk appetite (HY vs IG; differencing strips shared duration) |
| **TLT** | US nominal duration / flight-to-safety |
| **UUP** | safe-haven USD / FX |
| **GLD** | real-asset / monetary hedge |
| **EMB** | EM credit-cycle risk — **AC: verify incremental independence vs HYG−LQD on our own bars; if partial-corr > ~0.8, down-weight/flag as double-counted credit** |
| **DBC** | commodity / real-economy growth (the factor the proposed-5 originally missed) |
Excluded (research): SCHP/STIP/LTPZ (duration-redundant with TLT), RINF/BNDX/BWX (RINF→macro lens; intl bonds = duration or already-in-UUP).

## Acceptance Criteria
- [ ] AC-1: New `advisors/lens_crossasset.py`, off-execution-path, advisory-only, never-raising, D-1 (`type(exc).__name__` only).
- [ ] AC-2: Fetches daily bars for the instrument set via `synthetic_history.fetch_bars` (free Alpaca IEX, batched). No new data source.
- [ ] AC-3: Per-component directional read from recent momentum/relative-strength (e.g. trailing return / vs own SMA), z-scored where sensible; **DO NOT hardcode fixed risk-on/off signs** — gold's and credit's relationships are regime-unstable (2025-26); make the aggregation explainable + robust (report each component's reading + the aggregate).
- [ ] AC-4: Aggregate to a `risk_read` ∈ {risk-on, neutral, risk-off} with a named-constant threshold; expose each component so the read is auditable (honest-availability per component — a missing/insufficient instrument is excluded, not fabricated).
- [ ] AC-5: HYG−LQD computed as the RELATIVE (ratio or spread), not HYG alone (strips duration, isolates credit; avoids overlap with the equity lens).
- [ ] AC-6: EMB incremental-independence check vs HYG−LQD documented (computed on our bars or flagged for follow-up); persist the component readings.
- [ ] AC-7: Public `fetch_crossasset() -> dict` returns `{available, risk_read, components:{...}, source, reason?}`; `available=False`+`reason` on fetch failure / too-few components; never raises.
- [ ] AC-8: Persists each run to the warehouse via `lens_warehouse.persist_lens_snapshot(lens="crossasset", ...)`.
- [ ] AC-9: IEX-basis caveat noted in metadata.

## Architecture
New `advisors/lens_crossasset.py`. Reuses `synthetic_history.fetch_bars`. Computes per-component signals + aggregates. Persists to `lens_warehouse`. Off-execution-path. Wiring into a 6th advisor lens section (`_build_crossasset_section`) is a fast-follow (producer + warehouse land first, mirroring the other lenses).

## Edge Cases
- An instrument's bars unavailable/short → exclude that component, compute the aggregate from the rest; if too few components remain → `available=False`.
- Regime instability → no fixed signs; the read is derived + explainable, robust to a component flipping.
- All fetch fails → `available=False, reason`.

## Security Considerations
- D-1; no creds logged; Alpaca creds via synthetic_history (env). Secret-strip before warehouse persist. No Flask route, no SQL injection (parameterized warehouse), no eval/subprocess. No LIVE_EXECUTION.

## Testing Strategy
- `tests/ai_advisor/test_lens_crossasset.py`: mock `synthetic_history.fetch_bars` + `lens_warehouse` (no live calls). Cover: per-component computation (derived from mocks, not magic literals), HYG−LQD relative, aggregate risk_read thresholds, honest-availability (component/total), warehouse persist, D-1. Run -n0.
- PM gates: full `tests/ai_advisor/` -n0 + LIVE functional (real risk_read with each component's reading against live Alpaca).

## Scope Boundaries
- IN: `lens_crossasset.py` producer + components + aggregate + warehouse persist + tests. 
- OUT: inflation/TIPS (separate — RINF→macro lens); the 6th advisor lens-section wiring (fast-follow); point-in-time/historical (live read only).
