# Strategy Builder — Phase 3.6 Contract: Inline-SVG Sparklines

**Status:** BINDING contract for the Phase-3.6 Toxic Pair TDD team.
Implements the PM-accepted recommendation of
`strategy-builder-sparkline-research.md` (Option A: server-side inline SVG via
Jinja macro + persisted downsampled equity curve). Closes the final ledgered
Strategy Builder deferral.

## 1. Objective

Each Strategy Builder proposal card renders a small equity-curve sparkline from
a series persisted at observation-write time. Server-rendered inline SVG —
zero JS, zero external requests, CSP-clean, visible without script execution.

## 2. Scope of change (exhaustive)

| Surface | Granted edit |
|---------|--------------|
| `advisors/strategy_builder_engine.py` | Add `_cumulative_returns()` + `_downsample()` helpers (pure CPU); extend `raw_response` with `equity_curve_downsampled`: JSON array of floats, percent-scale cumulative return, 2-decimal rounding, uniform stride to 60 points with first AND last points always included. Series passes through the existing `_sanitize_non_finite` boundary. No new I/O (Phase-3.5 HR-2 carries over). |
| `app.py` GET strategy-builder route | Surface the field into card context. NO M6 artifact change — `equity_curve_downsampled` is NOT in `CHAT_ARTIFACT_ALLOWED_FIELDS` and MUST NOT be added (chat doesn't need the series; allowlist is FROZEN this phase). |
| `templates/ai_advisor_strategy_builder.html` | `{% macro render_sparkline(points) %}` (~20 lines): inline `<svg>` polyline, `stroke: var(--studio-accent)` (theme-aware), `preserveAspectRatio`, `aria-label` describing start→end return, `{% if points %}` guard. Called on survivor and rejected cards. |
| `tests/**` | test-writer owned. |
| `feature-plans/**` | doc-writer owned. |

## 3. Hard requirements

- HR-1: Backward compat — old rows (no series) render exactly as today; no
  empty `<svg>` stubs, no crashes. Golden fixture on a pre-3.6 row.
- HR-2: Size budget — persisted series ≤ 2 KB per candidate (spec: ~500 B).
  Test asserts serialized size.
- HR-3: Downsampling determinism — same input → same output; first/last
  points preserved exactly; series shorter than 60 points persisted unchanged.
- HR-4: SVG injection safety — points are floats formatted server-side via
  numeric formatting (never string interpolation of untrusted values into SVG
  attributes); NaN/inf already impossible past `_sanitize_non_finite`, but the
  macro must also guard (skip render) if any point is not a number.
- HR-5: All prior invariants pass. Full-suite baseline: **6,025 / 4 / 0**.

## 4. Acceptance criteria

- AC-1: New run persists a ≤60-point series; cards render an `<svg>` sparkline.
- AC-2: Old-row golden fixture renders with no sparkline and no artifacts.
- AC-3: aria-label present; stroke uses the theme CSS variable.
- AC-4: Full default suite 0 failures vs 6,025/4/0; ruff clean.

## 5. Team & process (incorporates Phase-3.5 process findings)

Toxic Pair TDD: test-writer (quant-test-writer) ⇄ implementer,
quant-code-reviewer + flask-dashboard-specialist, doc-writer.
- Cycle 2 MUST be a separate post-GREEN commit authored after reading the
  implementation. The PM audits commit history; a merged cycle is rejected and
  an independent cycle commissioned (Phases 4 and 3.5 precedent: independent
  cycles found 3 and 2 real bugs respectively).
- EACH reviewer produces their own evidence (file:line, command output).
  "Defer to other reviewer" verdicts are void.

[PM-ASSUMED] Rejected cards get sparklines too (parity with survivors).
[PM-ASSUMED] 60-point target and 2-decimal rounding per research spec.
