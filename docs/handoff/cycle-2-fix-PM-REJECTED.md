> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

# Cycle 2-FIX · PM verification — REJECTED

**HEAD reviewed:** a375c39
**Verified via:** curl + content audit (CLI), against running daemon
**Date:** 2026-05-19 ~11:00 ET

## Verdict
CHANGES REQUESTED. Reasoning below; reproducers are test material.

## Dashboard — APPROVED conditionally
Real progress vs prior state:
- Fonts loaded (2 fonts.googleapis links) ✓
- 13 canvas elements (was 2) ✓
- setInterval polling + `fetch('/api/state')` + `fetch('/api/chart/<sym>')` present ✓
- Title-bleed fixed (no "AlphaBot Dashboard v3" in body) ✓
- 11 symphonies populated, real status pills (Armed / TP-Armed) ✓
- Cash Now on every card ✓

Carry-forward (likely browser-only, but flagging for next pass):
- Initial Jinja render shows "Guard Alpha +0.00%" and dual-bar rows as "+0.00%/+0.00%". `static/index.js` line 76-80 computes `guard_alpha = cr - crHeld` and updates DOM, so the AJAX update *should* fix this — but I cannot verify JS execution via curl. User must confirm in browser. If still zero in browser, the data binding inside the polling callback is broken.
- `hist_dates len=3` — backend is only emitting 3 days of historical equity. DB might genuinely have ≤3 days of data; if so the chart will be sparse — confirm that's the DB state, not a backend truncation bug.

## Performance — REJECTED
- Only **1 `<canvas>` + 1 `<svg>`** — FIX-26 required ≥ 8 chart elements (1 cumulative + 7 metric-comparison bars per design Performance.jsx). Current page has the cumulative chart but no per-metric mini-bars at all.

## AI Advisor — REJECTED
- **0 `<canvas>` + 0 `<svg>`** — FIX-28 required confidence ring per suggestion, projected-impact mini-bars, autotune-runs sparklines. The page rendered no chart elements whatsoever. Even if `/ai-advisor/suggest` returns proper structured data, there's no rendering surface for it.

## History — REJECTED
- Only **0 `<canvas>` + 1 `<svg>`** — FIX-27 required ≥ 5 chart elements (1 daily-alpha strip + 4 by-reason mini-bars). Currently only the strip SVG exists, no per-reason visualizations.
- `/api/history/30` returns `daily_alpha: len=3` — same 3-day truncation as the dashboard. If DB has more than 3 days of post-mortems, backend is dropping data.

## Required for re-verification
- Performance: implement the 7 metric-comparison mini-bars per `.design-handoff/project/performance.jsx`. Each metric row gets a (Live | Bot | Δ) value display + at least a small bar visualization showing the live-vs-bot gap.
- Advisor: implement the 3 chart surfaces per `.design-handoff/project/advisor.jsx` — confidence ring (SVG arc), projected-impact mini-bar (before/after with delta), autotune-runs sparkline on the right rail.
- History: implement the 4 by-reason mini-bars per `.design-handoff/project/history.jsx`. Each ReasonCard shows a small win-rate bar + avg-alpha-per-exit bar.
- Audit the 3-day series truncation: confirm `hist_dates`, `daily_alpha`, etc. are correctly emitting all available DB rows up to the requested window (not silently capping at 3).
- Add RED tests asserting per-screen chart counts AGAINST THE LIVE DAEMON, not just template parsing — they would have failed on this round and forced the implementer to deliver.

## NIT (acceptable to merge if everything above clears)
- SVG count is 0 on Dashboard; design uses SVG primitives for badges/dots/icons. Team chose canvas-only. Acceptable if visual fidelity holds; revisit if it doesn't.
