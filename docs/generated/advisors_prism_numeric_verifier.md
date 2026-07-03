# advisors/prism_numeric_verifier

> Post-council anti-fabrication numeric verifier: recomputes every number the Prism council declares it cited against the authoritative source payload and classifies the citation pass / flagged / overridden / unverifiable.

**Source:** `advisors/prism_numeric_verifier.py`
**Last updated:** 2026-07-03 (DE-PRISM-MOMENTUM-REGISTRY-001: +10 technicals-momentum `_INDICATOR_REGISTRY` entries; module created 2026-07-02, DE-PRISM-NUMERIC-VERIFY-001)

## Overview

The Market Prism council writes a `MARKET_PRISM` observation each night from LLM deliberation prose. Nothing upstream of this module checks that a number the synthesis states ("VIX is 22") matches what the authoritative source actually returned (FRED VIXCLS says 18.1) — an honest transcription error or a hallucination sails through uncaught.

`advisors/prism_numeric_verifier.py` closes that gap deterministically. It does **not** parse prose: the council is required (AC-2, see "Council contract" below) to also emit every numeric indicator it states as a structured `{indicator, value, lens}` tuple in `raw_response.cited_numbers`. The verifier walks that list, resolves each indicator's ground truth via a named registry against the lens payloads already fetched by `prism_scheduler._patch_provenance`, classifies the diff, and persists the result as a separate append-only `MARKET_PRISM_VERIFICATION` observation. The `MARKET_PRISM` row itself is never touched.

Off-execution-path (never imported from `alpha_bot_execution.py`), advisory-only (no `LIVE_EXECUTION`, no trade/position mutation), D-1 (`type(exc).__name__` only on any degraded path — FRED URLs embed an API key, so raw exception text is never logged).

## API Reference

### `verify_cited_numbers(run_id: str, market_prism_row: dict | None, lens_sections: dict | None = None) -> dict`

The public entry point and pure orchestrator.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `run_id` | `str` | Accepted for signature symmetry with `persist_verification`; not otherwise consulted — the row already carries its own `run_id` in `raw_response`. |
| `market_prism_row` | `dict \| None` | The `MARKET_PRISM` `advisor_observations` row (or `None`). `raw_response` may be a dict or a JSON string — both are handled. |
| `lens_sections` | `dict \| None` | Optional `{lens: section}` bundle reused verbatim from the caller's own patch-time fetch (AC-4 — no duplicate external call). When `None`, each lens actually referenced by a cited indicator is fetched live via `ai_advisor._build_*_section()`, lazily and at most once per call (per-call cache). |

**Returns:** `dict` — `{"checks": [...], "summary": {...}, "verdict": <str>}`. Never raises (D-1) — any unexpected failure returns the same shape as "no cited numbers" (`verdict="no-numeric-claims"`, empty checks).

Each entry in `checks` has the shape:
```python
{
    "indicator": "VIX",
    "lens": "derivatives",          # the citing lens, as reported by the entry
    "cited_value": 22.0,
    "ground_truth_value": 21.8,     # None when unverifiable
    "classification": "pass",       # pass | flagged | overridden | unverifiable
}
```

### `persist_verification(run_id: str, result: dict) -> int | None`

Persists the result of `verify_cited_numbers` as one append-only `advisor_role="MARKET_PRISM_VERIFICATION"` row via `database.insert_advisor_observation`.

**Idempotent (AC-8):** calls `database.get_latest_market_prism_verification_for_run(run_id)` first; if a row already exists for this `run_id`, skips the INSERT and returns `None`. Never touches the `MARKET_PRISM` row (D-2 append-only invariant — same discipline that led to deleting `update_advisor_observation_raw_response`).

**Persisted `raw_response` shape:**
```python
{
    "run_id": run_id,
    "verified_at": "<ISO UTC timestamp>",
    "checks": [...],       # from verify_cited_numbers
    "summary": {"n_checks": ..., "n_pass": ..., "n_flagged": ..., "n_overridden": ..., "n_unverifiable": ...},
    "verdict": "...",
}
```

**Returns:** `int` (new row id) on insert, `None` on skip-idempotent or any error (D-1 — logs `PersistError: {type(exc).__name__}` to stderr, never re-raises).

No schema migration — this is a JSON-blob observation via the existing `insert_advisor_observation` accessor with a new `advisor_role`, identical to how `MARKET_PRISM_SOURCES` shipped (DE-PRISM-SOURCES-001).

## Indicator Registry (`_INDICATOR_REGISTRY`)

Named module constant (AC-3, AC-15) mapping each supported indicator string to `(truth_lens, payload_path, comparison_type, tolerance)`. `payload_path` is a dotted path resolved against `lens_sections[truth_lens]["payload"]`. An indicator absent from this table (or any non-string indicator) resolves to `unverifiable` — never a silent pass. 23 keys total (12 literal non-momentum + 10 momentum + 1 fundamentals wildcard).

| Indicator | Lens | Payload path | Comparison | Tolerance |
|-----------|------|---------------|------------|-----------|
| `VIX`, `VIXCLS` | `derivatives` | `vix_level` | absolute | `_VIX_TOLERANCE = 0.5` |
| `VXVCLS`, `VIX3M` | `derivatives` | `vix_term_structure.term_3m` | absolute | `_VIX_TERM_TOLERANCE = 0.5` |
| `DGS10`, `10Y` | `macro` | `series.DGS10.value` | absolute | `_RATE_TOLERANCE = 0.05` |
| `UNRATE` | `macro` | `series.UNRATE.value` | absolute | `_UNRATE_TOLERANCE = 0.2` |
| `CPIAUCSL`, `CPI` | `macro` | `series.CPIAUCSL.value` | absolute | `_CPI_TOLERANCE = 1.0` (index points, not a percent) |
| `FEDFUNDS` | `macro` | `series.FEDFUNDS.value` | absolute | `_RATE_TOLERANCE = 0.05` |
| `tone` | `sentiment` | `tone_score` | absolute | `_TONE_TOLERANCE = 0.15` — deliberately wider than `_RATE_TOLERANCE`; GDELT tone drifts between council-time and verify-time as the rolling artlist window moves (AC-13; see "Drift honesty" below) |
| `breadth` | `technicals` | `breadth` | absolute | `_BREADTH_TOLERANCE = 0.05` (0–1 fraction) |
| `momentum_<TICKER>_20d` for `TICKER` in `SPY`, `QQQ`, `IWM`, `EFA`, `AGG`, `GLD`, `XLF`, `XLE`, `XLV`, `XLI` (DE-PRISM-MOMENTUM-REGISTRY-001) | `technicals` | `momentum.<TICKER>` | absolute | `_MOMENTUM_TOLERANCE = 0.001` — 10 literal entries, one per `lens_technicals._PROXY_UNIVERSE` ticker; see "Momentum Registry Expansion" below |
| `<TICKER>.<CONCEPT>` (e.g. `AAPL.Revenues`) | `fundamentals` | `tickers.<TICKER>.key_facts.<CONCEPT>.value` (built dynamically by `_resolve_payload_path`) | relative | `_FUNDAMENTALS_RELATIVE_TOLERANCE = 0.05` (5%) — large-magnitude $ figures need a relative, not absolute, tolerance |

The `<TICKER>.<CONCEPT>` shape is matched via `_TICKER_CONCEPT_RE` (`^[A-Za-z0-9]+\.[A-Za-z0-9_]+$`) in `_lookup_registry_entry` — any indicator string matching this pattern that is not itself a literal registry key falls back to the fundamentals wildcard entry. The `momentum_<TICKER>_20d` indicators are **literal** entries, not wildcard-matched — a ticker outside the 10-ticker proxy universe (e.g. `momentum_TSLA_20d`) resolves `unverifiable`, never silently accepted.

`_MAX_CITED_NUMBERS = 100` bounds the checked list regardless of how many tuples the council (or a malformed/adversarial payload) supplies — a DoS guard, not a normal-path limit.

## Classification (`_classify`)

`_OVERRIDE_FACTOR = 3.0` — the multiplier defining the "flagged" band's outer edge.

- **`pass`** — `|diff| <= tolerance` (relative comparisons normalize `diff` by `|truth|` first, guarding `truth == 0` with a `1.0` denominator).
- **`flagged`** — `tolerance < |diff| <= _OVERRIDE_FACTOR * tolerance`. Bounded mismatch; prose is never mutated, only annotated at render time.
- **`overridden`** — `|diff| > _OVERRIDE_FACTOR * tolerance`. Gross mismatch; the ground-truth value is recorded for the render layer to prefer.
- **`unverifiable`** — indicator not in the registry, citing lens unavailable at verify time, or either the cited or ground-truth value fails `_safe_normalize` (non-numeric, `None`, malformed string, or `bool`).

**AC-6 magnitude-only classification — deliberate deviation from the feature plan's prose.** The plan text described the override trigger as "gross mismatch or sign/regime flip." The shipped implementation classifies purely on the magnitude of `|cited − truth|` — it does **not** special-case a sign flip. This is intentional, not an oversight: forcing `overridden` on any sign change would false-override legitimate near-zero drift (e.g. GDELT tone crossing 0.0 between council-time and verify-time is exactly the kind of noise AC-13's drift tolerance exists to absorb). A sign flip that also crosses the magnitude threshold still lands `overridden` through the ordinary `_OVERRIDE_FACTOR` check — magnitude alone is a strictly more conservative and equally correct classifier for this threat model (an error-prone-but-not-adversarial council), so no separate sign check was added. The same magnitude-only logic governs momentum, which legitimately sits near and crosses zero (a 20d-return fraction) — no separate carve-out was needed for it.

## Verdict Taxonomy (`_derive_verdict`)

`verify_cited_numbers` reduces the check list's `summary` counts to exactly one of five verdicts, in this precedence order:

| Verdict | Fires when | Meaning |
|---------|-----------|---------|
| `no-numeric-claims` | `raw_response` has no `cited_numbers` key, the key is falsy, or the container is not a `list` (see "Finding-1 hardening" below) | The council declared no numeric citations at all — including every legacy row and every `lens_pipeline`-produced row (AC-11). Never an error. |
| `no-verifiable-claims` | `n_checks > 0` and `n_pass == 0` (i.e. every check landed `unverifiable`, `flagged`, or `overridden` — no wait, precisely: every check is `unverifiable` in practice, since any `flagged`/`overridden` count would already have tripped one of the two verdicts below) | **nvreview Finding 1 fix.** All declared citations resolved unverifiable (malformed entries, unmapped indicators, or every referenced lens unavailable) — nothing was actually checked clean. Returning `"clean"` here would be a silent pass in an anti-fabrication feature; this verdict makes "checked, but couldn't verify anything" visibly distinct from "checked and all passed." |
| `overrides-detected` | `n_overridden > 0` | At least one cited number is a gross mismatch against its ground truth. Highest-severity, checked first. |
| `flags-detected` | `n_overridden == 0` and `n_flagged > 0` | At least one cited number is a bounded mismatch; no gross mismatch present. |
| `clean` | `n_checks > 0`, `n_overridden == 0`, `n_flagged == 0`, and at least one check is `pass` | Every declared citation that could be checked passed; nothing flagged or overridden. |

`_derive_verdict`'s actual precedence in code is: `overrides-detected` → `flags-detected` → `no-verifiable-claims` (when `n_checks > 0` and `n_pass == 0`) → `clean` (the fallthrough). Because `no-verifiable-claims` is only reachable when neither override nor flag counts are positive, it is equivalent to "every check was `unverifiable`."

### Finding-1 hardening (malformed `cited_numbers`, never a silent "clean")

An earlier sufficiency review (`nvreview`) found that a truthy-but-malformed `cited_numbers` container (a `dict`, a bare `str`, or a list containing non-dict entries) could either be silently misread (iterating a dict's keys or a string's characters as if they were `{indicator, value, lens}` tuples) or silently dropped. The shipped code closes both gaps:

- **Non-list container** (`cited_numbers` present but not a `list`) → degrades the same as "no numeric claims": `{"checks": [], "summary": _empty_summary(), "verdict": "no-numeric-claims"}`. It is never iterated as if it were a list of tuples.
- **Non-dict list entry** (e.g. `42`, `"foo"` inside the list) → coerced to `{}` before `_build_check`, so it surfaces as its own explicit `"unverifiable"` check (with `indicator: None`) rather than vanishing from the list. A malformed entry is counted, not silently discarded — which is what drives `no-verifiable-claims` instead of a false `clean` when every entry in the list is garbage.

## Ground-Truth Sourcing (no duplicate fetch, AC-4)

When the caller (see [prism_scheduler](prism_scheduler.md)) supplies `lens_sections`, `_get_lens(lens)` returns `lens_sections.get(lens)` directly — the live builders are never re-invoked by this module in that path. When `lens_sections is None` (e.g. direct/unit-test callers), `_fetch_lens_live(lens)` lazy-imports `ai_advisor` and calls the matching builder via `_LENS_BUILDER_ATTR` (`getattr()` lookup at call time, never a pre-bound reference, so `patch.object(ai_advisor, "_build_X_section", ...)` is always honored in tests); the result is cached per-call in `_lens_cache` so a lens referenced by multiple cited indicators is fetched at most once.

## Drift Honesty (AC-13)

Daily FRED/VIX/fundamentals series are stable within the minutes between council-time and verify-time — a strict tolerance is appropriate. GDELT `tone` is a rolling-window scalar that can legitimately move as the artlist window advances; `_TONE_TOLERANCE` is set wider than `_RATE_TOLERANCE` specifically to avoid a false `overridden` from ordinary drift (mirrors the same honesty principle documented for `MARKET_PRISM_SOURCES` in `DECISIONS.md` §DE-PRISM-SOURCES-001 "Provenance honesty").

## Momentum Registry Expansion (DE-PRISM-MOMENTUM-REGISTRY-001, 2026-07-03)

Before this cycle, every `momentum_<TICKER>_20d` citation from the council resolved `unverifiable` for every ticker — confirmed live in production (see `DECISIONS.md` §DE-PRISM-NUMERIC-VERIFY-001 post-deploy-verified addendum: 4 of 24 real citations, all momentum, were the entire `unverifiable` count that day). This closed that blind spot with a pure, minimal (16-line) additive diff: one named constant (`_MOMENTUM_TOLERANCE = 0.001`) plus 10 literal `_INDICATOR_REGISTRY` entries, one per `lens_technicals._PROXY_UNIVERSE` ticker (SPY, QQQ, IWM, EFA, AGG, GLD, XLF, XLE, XLV, XLI) — `_classify`, `_resolve_dotted_path`, and `_lookup_registry_entry` are untouched.

**Absolute, not relative, tolerance.** Momentum is a naturally bounded 20-day-return fraction (roughly ±0.15) that legitimately sits near and crosses zero — the same shape as `breadth`, not a large-magnitude dollar figure. A relative tolerance would be actively wrong here: `_classify()`'s relative branch divides by `|truth|`, so a near-zero momentum reading (e.g. `0.0003`) would make ordinary rounding noise look like a huge relative error.

**Tolerance grounding (0.001).** Live council citations round to roughly 4 decimals (confirmed in production, e.g. `-0.0124`), so honest rounding noise tops out at `0.00005` (half the last digit) — `0.001` gives 20x headroom over that floor while still rejecting a hallucinated citation (a `-0.02` cited against a true `-0.0124`, diff `0.0076`, is well outside even the `_OVERRIDE_FACTOR`-widened band and correctly lands `overridden`).

**Ground-truth stability.** The verifier compares against a post-council re-fetch (`prism_scheduler._fetch_lens_sections()`), not the literal payload the council read at synthesis time. This is safe for momentum specifically because momentum and `breadth` are computed inside the same `_build_technicals_section()` call from the same Alpaca daily-bar fetch — a completed trading day's daily bar is immutable once posted, so two calls to that code path within one overnight window return identical values. This is unlike `tone`, whose rolling GDELT artlist window genuinely moves between council-time and verify-time (hence `_TONE_TOLERANCE`'s deliberately wider band) — momentum's tolerance only needs to absorb citation-rounding noise, not re-fetch drift.

**Literal, not wildcard.** Unlike the fundamentals `<TICKER>.<CONCEPT>` shape, the 10 momentum entries are registered as literal keys — a ticker outside the fixed proxy universe (e.g. `momentum_TSLA_20d`) is never silently matched; it resolves `unverifiable`, same as any other unmapped indicator.

## Security Considerations

- **D-1 error contract:** every degraded path (both `verify_cited_numbers` and `persist_verification`) prints `type(exc).__name__` only to `sys.stderr` — never `str(exc)`, which for the macro/derivatives lenses may embed a FRED-API-key-bearing URL.
- **Untrusted LLM input:** `cited_numbers` originates from the council (an LLM). `_safe_normalize` coerces defensively (rejects `bool`, dict, list; `float()` in a `try/except` for strings) and the list is bounded by `_MAX_CITED_NUMBERS` before any per-entry work — no `eval`, no unbounded loop.
- **Append-only / parameterized:** `persist_verification` writes through the existing `insert_advisor_observation` accessor (parameterized) and the idempotency check uses `database.get_latest_market_prism_verification_for_run`'s `json_extract(..., ?)` binding — no string interpolation, no destructive operation, `MARKET_PRISM` row is never mutated.
- **Off-execution-path:** never imported from `alpha_bot_execution.py` (guarded by a dedicated test, `test_prism_numeric_verifier_not_imported_from_alpha_bot_execution`).

## Residual Limitation (D-1 design decision, OUT of scope)

The verifier only checks numbers the council **declares** as `cited_numbers` tuples. A number stated in prose (e.g. in `sentiment_rationale` or a lens `summary`) but never emitted as a tuple is invisible to this module — there is no NLP/regex prose-extraction fallback (rejected at design time as brittle: "VIX ~22", "the vol index near 22" defeat regex and can't be bounded deterministically). This gap is mitigated, not closed, by the AC-2 prompt mandate on the 6 `.claude/agents/prism-*.md` role files (see "Council contract" below): every numeric indicator stated in prose must also be reported as a tuple. Full NLP prose-extraction is explicitly out of scope for this cycle (see `DECISIONS.md` §DE-PRISM-NUMERIC-VERIFY-001).

## Council Contract (AC-2)

`.claude/agents/prism-synthesizer.md` (step 9, the `raw_response` block) and all 5 `.claude/agents/prism-*-analyst.md` role files were extended to instruct: **every numeric indicator stated in prose must also appear as a `{indicator, value, lens}` tuple in `cited_numbers`.** Existing prose fields (`summary`, `sentiment_rationale`) are unchanged — this is additive schema only. See [prism_scheduler](prism_scheduler.md) for the council orchestration this contract lives alongside.

## Related

- [prism_scheduler](prism_scheduler.md) — wires `verify_cited_numbers` + `persist_verification` into the nightly `main()` flow, reusing the same shared lens-section fetch as the SOURCES patch (DE-PRISM-SOURCES-001).
- [database](database.md) — `get_latest_market_prism_verification_for_run(run_id)` accessor.
- [advisors/prism_render](advisors_prism_render.md) — the sibling render-layer guard; the AC-10 Overview badge overlay (in `app.py`/`templates/ai_advisor.html`) is additive to the same Overview surface `prism_render` humanizes, but is implemented directly in `app.py:ai_advisor_tab()`, not in `prism_render.py`.
- `DECISIONS.md` §`DE-PRISM-NUMERIC-VERIFY-001` — full design rationale, the Option (b) vs prose-regex fork, the AC-6 magnitude-only deviation, and the post-deploy-verified production confirmation.
- `DECISIONS.md` §`DE-PRISM-MOMENTUM-REGISTRY-001` — the momentum registry expansion documented above.
- `DECISIONS.md` §`DE-PRISM-SOURCES-001` — the precedent this module's "separate append-only row, never mutate MARKET_PRISM" pattern reuses.

## Tests

- `tests/prism/test_prism_numeric_verifier.py` — 86 tests: classification boundaries (pass/flagged/overridden, exact-tolerance and exact-override-factor edges), registry resolution (documented indicators, `<TICKER>.<CONCEPT>` wildcard, unmapped indicators), FRED string-value normalization, relative-vs-absolute comparison typing, no-cited-numbers / source-unavailable / malformed-value degradation, the 3 Finding-1 malformed-container/entry tests, duplicate-indicator independence, drift-tolerant tone vs strict macro tolerance, `persist_verification` idempotency + row-shape + never-mutates-MARKET_PRISM, off-execution-path guard, named-constant exposure, the golden-fixture end-to-end run, and (DE-PRISM-MOMENTUM-REGISTRY-001) a dedicated `TestMomentumRegistryExpansion` class (out-of-universe-ticker unmapped, exact registry-size pin at 23 keys, correctly-rounded-citation passes, tolerance/override-factor boundaries using `truth=0.0` for an exact IEEE-754 round-trip, near-miss-hallucination rejected, absolute-not-relative correctness near zero) plus the 10 momentum indicators added to the two existing registry-wide parametrized tests (never-unverifiable-when-available, comparison-type-is-absolute).
- `tests/fixtures/prism_verifier/verify_cited_numbers_mixed_classifications.json` — schema-derived (not parser+fixture co-design; provenance noted in the fixture's own `_provenance` key) golden fixture: 4 cited indicators exercising all 4 classifications in one fixed input (VIX → pass, UNRATE → flagged, DGS10 → overridden, an unmapped `2s10s_yoy_inflation_pct` → unverifiable).
- `tests/prism/test_prism_numeric_verifier_registry_drift.py` — 6 tests (`/review` PR #90 Finding F2). For each `_INDICATOR_REGISTRY` entry, calls the REAL `ai_advisor._build_<lens>_section()` builder (only the external network mocked, never the builder itself) and resolves the registry's `payload_path` against the REAL returned payload — a permanent regression guard that fails the moment a builder's payload shape drifts from what the registry expects, closing a gap the verifier's own unit tests (which mock the builder entirely) can't catch. Extended this cycle (DE-PRISM-MOMENTUM-REGISTRY-001) with `test_technicals_momentum_resolves_against_real_builder_for_all_proxy_tickers`, covering all 10 proxy-universe tickers against the real `_build_technicals_section()` output.
- `tests/prism_scheduler/test_verifier_wiring.py` — 4 tests: `main()` calls the verifier after `_patch_provenance`, the shared `lens_sections` are reused (builders invoked once, not twice), a verifier exception never changes the exit code, and the verifier is skipped when no MARKET_PRISM row was found.
- `tests/prism_scheduler/test_patch_provenance_lens_sections_equivalence.py` — 1 test: `_patch_provenance`'s SOURCES row is byte-equivalent whether it fetches its own lens sections or reuses a caller-supplied `lens_sections` bundle.
- `tests/database/test_market_prism_verification_accessor.py` — 9 tests: exact-match, `None`-on-mismatch, correct-row-among-many, empty-table, `get_ro_connection` usage, expected shape, nested-table robustness, and cross-role isolation from `get_latest_market_prism_summary`.
- `tests/ai_advisor/test_prism_role_files_cited_numbers.py` — 6 tests: each of the 6 `.claude/agents/prism-*.md` role files exists and references the `cited_numbers` tuple contract.
- `tests/app/test_ai_advisor_tab_verification_overlay.py` — 8 tests: the AC-10 render overlay (see [app](app.md)) — fetch-by-run_id, overridden-annotation rendering, honest empty-state, no-stale-bleed on run_id mismatch, no in-place mutation of the `MARKET_PRISM` row, hostile-indicator-field escaping, no `| safe` filter in the template block, and `MARKET_PRISM_VERIFICATION` absent from `_ADVISOR_ROLES`.
