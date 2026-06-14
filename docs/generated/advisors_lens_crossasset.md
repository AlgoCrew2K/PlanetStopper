# advisors/lens_crossasset

> Cross-asset risk-appetite lens: an independent risk-on ↔ risk-off read from 6 free Alpaca-fetchable ETFs spanning credit, duration, FX, gold, EM credit, and commodity-growth. Off-execution-path, advisory-only.

**Source:** `advisors/lens_crossasset.py`
**Last updated:** 2026-06-14

## Overview
Measures **cross-asset risk appetite** — a signal independent of the equity lenses (technicals/breadth = equity trend/breadth) and the volatility lens (derivatives = VIX regime). Instrument set settled by two research passes + operator sign-off (2026-06-14): the fewest instruments spanning the most *independent* factors; **inflation is deliberately excluded** (kept as a separate macro-lens concern — see RINF in the macro lens).

**Instruments → factor:**
| Instrument | Factor |
|---|---|
| HYG vs LQD (relative) | credit-spread risk appetite (differencing strips shared duration) |
| TLT | US duration / flight-to-safety |
| UUP | safe-haven USD / FX |
| GLD | real-asset / monetary hedge |
| EMB | EM credit-cycle risk |
| DBC | commodity / real-economy growth |

**Key properties:**
- **Free.** Reuses `synthetic_history.fetch_bars` (Alpaca IEX daily bars, one batched request for the 7 symbols). No new data source.
- **No hardcoded risk-on/off signs.** Each component's directional signal is derived from recent momentum (the aggregate is computed from the component readings), so the read is robust to a component flipping — gold's and credit's relationships are regime-unstable (2025-26).
- **Per-component honest-availability.** A component whose bars are missing/short is excluded from the aggregate (not fabricated); if fewer than `_MIN_COMPONENTS` remain → `available=False`. Never raises (D-1: `reason = type(exc).__name__` only).
- **Credit as a relative.** HYG−LQD uses both symbols (isolates the credit factor; avoids overlap with the equity lens).

## Public API
### `fetch_crossasset() -> dict`
Returns `{available, risk_read, components, source, reason?}`. `risk_read` ∈ {`risk-on`, `neutral`, `risk-off`} (aggregate crosses `_RISK_ON_THRESHOLD`). `components` exposes each instrument's `{available, signal}` for auditability. `available=False` + `reason` on fetch failure or too-few components. Persists each run to the warehouse via `lens_warehouse.persist_lens_snapshot(lens="crossasset", ...)` (lazy import, CC-2). Never raises.

Internal helpers: `_compute_component_signals(bars)`, `_aggregate_risk_read(signals)`.

## Constants
| Constant | Value | Purpose |
|---|---|---|
| `_ALL_SYMBOLS` | HYG, LQD, TLT, UUP, GLD, EMB, DBC | instrument set |
| `_LOOKBACK_DAYS` | 90 | bar lookback window |
| `_MIN_BARS` | 20 | min bars for a usable component signal |
| `_MIN_COMPONENTS` | 3 | min available components for an aggregate |
| `_RISK_ON_THRESHOLD` | 0.5 | aggregate threshold for the risk_read label |
| `_SOURCE` | `"alpaca-iex"` | provenance |

## Scope
Off-execution-path; advisory-only; no Flask route; no `LIVE_EXECUTION`. No production caller yet — intended to feed a 6th advisor lens-section (`_build_crossasset_section`) as a fast-follow, mirroring the other lenses. EMB's incremental independence vs HYG−LQD should be verified on our own bars before it's treated as a fully independent factor (it partly loads on the same credit factor). Inflation/TIPS are out of scope here (separate macro-lens concern).

## Tests
`tests/ai_advisor/test_lens_crossasset.py` (46 tests): static contract (off-exec-path, no Flask/LIVE_EXECUTION/eval), component signal derivation (from mocked bars, not magic literals), HYG−LQD relative, aggregate risk_read vocabulary + threshold, honest-availability (component + total), warehouse persist, D-1. All network mocked — no live calls in CI.
