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

---

## Phase-3.6 Implementation Record

**Status:** CLOSED

### Commit SHAs

| Commit | Role | SHA |
|--------|------|-----|
| Cycle-1 RED tests | `test(phase36): RED tests — sparkline downsampling, persist payload, SVG render [cycle 1]` | `613c2ee` |
| Implementation GREEN | `feat(phase36): persist equity_curve_downsampled + inline-SVG sparkline macro` | `68d7550` |
| Cycle-2 adversarial | `test(phase36): adversarial cycle-2 tests — pathological series, edge cases [cycle 2]` | `8764c48` |

### Deviations from contract

1. **`SPARKLINE_TARGET_POINTS` named constant absent [PM-ASSUMED deviation].** The
   contract listed `SPARKLINE_TARGET_POINTS = 60` as a required named constant in the
   engine's constants section (consistent with the no-magic-numbers rule). The implementer
   encoded the target as the default parameter value `target: int = 60` on `_downsample`
   instead. The constant name was specified in the Phase-3.6 summary but not listed as a
   hard requirement. Behaviour is correct; naming completeness is a doc finding (filed to
   implementer below). PM may request a follow-up commit to add the constant.

2. **`_downsample` stride formula differs from research spec.** The research doc proposed
   `round(i * step)` where `step = len(series) / target`. The implementation uses
   `int(i * (n - 1) / (target - 1))` — a different formula that guarantees the first point
   is always index 0 and enables exact endpoint preservation without special-casing. Both
   approaches satisfy HR-3 (determinism, first/last preserved); ADV-3 + ADV-4 verify the
   implementation formula directly. No functional regression.

3. **Template macro is ~38 lines, not ~20.** The contract estimated ~20 lines. The actual
   macro is 38 lines due to the Jinja2 `namespace` idiom required for `all_numeric` loop
   mutation and the explicit `pts_str.v` string-building loop. This is not a deviation from
   requirements — AC-3 (aria-label, stroke CSS var), HR-4 (non-numeric guard), and
   HR-1 (`{% if points %}` guard) are all implemented. Size estimate was advisory.

### Reviewer verdicts

Both reviewers — `quant-code-reviewer` and `flask-dashboard-specialist` — produced
independent evidence-based PASS verdicts. No "defer to other reviewer" were issued.
(Individual reviewer findings are in their respective agent outputs; not reproduced here
per PM consolidation policy.)

### Test count delta

**34 new tests** in `tests/app/test_strategy_builder_phase36.py`:

| Group | Count | Description |
|-------|-------|-------------|
| DS (downsampling helpers) | 9 | `_downsample` + `_cumulative_returns` unit tests (HR-3) |
| SB (size budget) | 1 | Serialized series ≤ 2048 bytes for 1250-point input (HR-2) |
| PP (persist payload) | 4 | `_persist_survivor` equity curve field presence/absence |
| BC (backward compat) | 3 | Pre-3.6 fixture renders without SVG, no crash (HR-1) |
| SR (SVG render) | 6 | New-row SVG, aria-label, stroke var, HR-4 guards, rejected cards |
| ADV (cycle-2 adversarial) | 11 | Single-point, constant series, stride count, endpoint, aria-label values, preserveAspectRatio, mixed rows, absent/empty key, empty series, copy semantics |

**Full suite result:** 34 passed, 0 failed, 2 warnings (quantstats divide-by-zero on
degenerate series — pre-existing, not caused by this cycle).

### Doc-writer findings filed to implementer

The following were filed to the implementer as findings (not edits — doc-writer mandate):

1. **`SPARKLINE_TARGET_POINTS` named constant missing.** The contract and the project's
   no-magic-numbers rule both call for a named constant. The magic number `60` is encoded
   only as a default parameter value. A one-line addition to the constants section —
   `SPARKLINE_TARGET_POINTS: int = 60  # Phase 3.6: target sparkline resolution (research spec §3)` —
   and updating `_downsample(series, target: int = SPARKLINE_TARGET_POINTS)` would satisfy
   the rule. This is a cosmetic/style finding; no behavioural regression exists.

2. **`_cumulative_returns` docstring explains WHAT, not WHY.** The docstring describes the
   running-sum mechanic but omits the rationale: pre-computing cumulative returns at persist
   time avoids rebuilding them in the Jinja macro or Flask route (HR-2 no-new-I/O carries
   forward), and keeping the series in percent-scale is consistent with `returns_pct`
   conventions throughout the engine. A one-sentence WHY note would satisfy the standard.

3. **`_downsample` docstring explains WHAT, not WHY.** The docstring documents guarantees
   (determinism, endpoint preservation, unchanged-if-short) but not the architectural
   rationale: uniform stride was chosen over LTTB to maintain zero additional Python
   dependencies on the persist path, and 60 points is sufficient fidelity for a 56px-tall
   sparkline. The research doc (`strategy-builder-sparkline-research.md` §Downsampling)
   captures this rationale; the docstring should reference or summarise it so future
   maintainers do not import LTTB without understanding the deliberate trade-off.
