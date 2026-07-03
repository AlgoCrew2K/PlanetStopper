# Feature: Prism Council Numeric-Verifier
Status: ready
Created: 2026-07-02

## Summary

Adds an anti-fabrication **numeric verifier tier** to the nightly Market Prism council (DE-PRISM-NUMERIC-VERIFY-001). Today the council validates only citation *shape* (`ai_advisor.build_citation`), honest lens degradation, and a post-council *source-list* re-fetch (`prism_scheduler._patch_provenance`) — **nothing checks that a NUMBER an analyst or the synthesizer stated matches the authoritative source**. A council that writes "VIX is 22" when FRED (VIXCLS) says 18.1 sails through uncaught.

This feature closes that gap. After the council writes its `MARKET_PRISM` observation, a new deterministic verifier reconciles the numeric indicators the council **declares it cited** against the already-fetched authoritative payloads (FRED macro series DGS10/UNRATE/CPIAUCSL/FEDFUNDS, FRED VIXCLS/VXVCLS derivatives, GDELT tone, Alpaca-derived breadth, SEC fundamentals) that `_patch_provenance` re-invokes at patch time. Each cited number is classified `pass` / `flagged` / `overridden` / `unverifiable` against a per-indicator tolerance registry, and the result is written as a **separate append-only `MARKET_PRISM_VERIFICATION` row** (the MARKET_PRISM row is never mutated — append-only invariant). The Overview tab additively overlays the verdict so the operator sees flagged/overridden numbers before trusting the read.

**Design fork resolved (Decision D-1 below): Option (b) — the council emits cited numbers as STRUCTURED tuples** that are recompiled deterministically against source payloads. Prose regex-extraction (Option a) is rejected as brittle and anti-ethos.

## Acceptance Criteria

- [ ] AC-1: New module `advisors/prism_numeric_verifier.py` exposes `verify_cited_numbers(run_id: str, market_prism_row: dict, lens_sections: dict | None = None) -> dict`. It **never raises** (D-1 — only `type(exc).__name__` on any failure path), is **off the execution path** (never imported from `alpha_bot_execution.py`; CC-2 lazy import at every caller), and is **advisory-only** (never reads/writes `LIVE_EXECUTION`, trade orders, or position state).
- [ ] AC-2: The council output schema is extended so the synthesizer emits a structured `cited_numbers: list[{indicator, value, lens, source_hint?}]` array inside the `MARKET_PRISM` `raw_response`. The synthesizer + 5 analyst role files (`.claude/agents/prism-*.md`) instruct that **every numeric indicator stated in prose MUST also appear as a `cited_numbers` tuple** (so the verifier sees the same numbers the operator reads). Existing prose fields (`summary`, `sentiment_rationale`) are unchanged.
- [ ] AC-3: A deterministic **indicator→ground-truth registry** (named module constant) maps each supported indicator to `(lens, payload_path, comparison_type, tolerance)`: VIX/VIXCLS→`derivatives.vix_level`; VXVCLS/VIX3M→`derivatives.vix_term_structure.term_3m`; DGS10/10Y→`macro.series.DGS10.value`; UNRATE→`macro.series.UNRATE.value`; CPIAUCSL/CPI→`macro.series.CPIAUCSL.value`; FEDFUNDS→`macro.series.FEDFUNDS.value`; tone→`sentiment.tone_score`; breadth→`technicals.breadth`; `<TICKER>.<concept>`→`fundamentals.tickers.<TICKER>.key_facts.<concept>.value`. An indicator with no registry entry resolves to `unverifiable` — never a silent pass.
- [ ] AC-4: Ground truth is sourced from the **already-fetched `_build_*_section()` payloads reused from `_patch_provenance`'s patch-time fetch** — no new/duplicate external fetch on the happy path. When `lens_sections` is not supplied, the verifier fetches once via the builders (bounded, D-1). No unbounded LLM call is introduced.
- [ ] AC-5: Numeric comparison **normalizes units** (FRED `value` strings → float; percent-points vs fractions; breadth fraction 0–1) and is **rounding-aware**. A check is `pass` iff `abs(cited − ground_truth) <= tolerance` for that indicator (relative tolerance for large magnitudes like CPI index level and fundamentals dollars; absolute tolerance for rates/VIX/breadth).
- [ ] AC-6: **Mismatch policy by severity**: within tolerance → `pass`; outside tolerance but bounded (≤ `_OVERRIDE_FACTOR` × tolerance, same sign/order) → `flagged` (annotate only; prose never mutated); gross mismatch or sign/regime flip → `overridden` (the ground-truth value is recorded for render preference); no registry mapping OR source unavailable → `unverifiable`. **No LLM re-ask** — the verifier is fully deterministic.
- [ ] AC-7: The verifier persists **exactly one** append-only `advisor_role="MARKET_PRISM_VERIFICATION"` row via `database.insert_advisor_observation`, keyed to `run_id` inside `raw_response`, carrying `{run_id, verified_at, checks:[...], summary:{n_checks,n_pass,n_flagged,n_overridden,n_unverifiable}, verdict}`. The `MARKET_PRISM` row is **never modified** (append-only invariant — same discipline that deleted `update_advisor_observation_raw_response`). **No schema migration is required** (raw_response is a JSON column; mirrors `MARKET_PRISM_SOURCES`).
- [ ] AC-8: The verifier is wired into `prism_scheduler.main()` after F-4 row-verification (alongside `_patch_provenance`, `prism_scheduler.py:521`). It is **idempotent** — if a `MARKET_PRISM_VERIFICATION` row already exists for this `run_id`, it skips the INSERT. It **never gates `sys.exit(0)`** — a verifier failure never fails the nightly run.
- [ ] AC-9: New accessor `database.get_latest_market_prism_verification_for_run(run_id) -> dict | None` — exact `json_extract(raw_response,'$.run_id') = ?` match, returns `None` on mismatch (no stale-bleed), D-1 never-raises, `get_ro_connection()`. `"MARKET_PRISM_VERIFICATION"` is **NOT** added to `app.py:_ADVISOR_ROLES` (keeps it out of the Overview observations loop + `_preview_text` stamp).
- [ ] AC-10: **Render overlay** — `app.py:ai_advisor_tab()` additively fetches the VERIFICATION row by `run_id` (after the existing SOURCES merge) and attaches per-lens/overall verification annotations (flag/override badges + the corrected value for overrides) to the template context. Honest empty-state when the row is absent. The `MARKET_PRISM` row object is deep-copied before any attachment (mirrors the SOURCES merge at `app.py:3862-3904`); overridden values render a "council cited X; source says Y" annotation.
- [ ] AC-11: **Backward/degraded compatibility** — a `MARKET_PRISM` row with no `cited_numbers` key (legacy rows, or rows produced by the `lens_pipeline.run_pipeline` fallback producer) yields an empty check set and verdict `no-numeric-claims`. Never an error, never a fabricated pass.
- [ ] AC-12: **Source-unavailable-at-verify-time** — when a lens builder returns `available=False` at verify time, every cited number mapped to that lens is `unverifiable` (never `flagged`/`overridden`), and the verdict reflects limited-inputs for that lens.
- [ ] AC-13: **Drift honesty** — drift-prone indicators (GDELT `tone`) use a drift-tolerant tolerance and the verification row records the council-time-vs-verify-time caveat; stable daily series (macro/derivatives/fundamentals) use strict tolerances. No false `overridden` from legitimate intraday/rolling drift (mirrors DE-PRISM-SOURCES-001 §Provenance honesty).
- [ ] AC-14: **Multiple lenses citing the same indicator** are each verified independently against that indicator's *canonical* ground-truth source (a number's truth is lens-independent — VIX is VIX). Duplicate citations never collapse or mask a mismatch.
- [ ] AC-15: Every constant (tolerances, `_OVERRIDE_FACTOR`, the registry) is a **named module constant with a source comment** (project standard: no magic numbers). All DB writes are parameterized; the module performs no destructive DB operation.

## Architecture

**New module** — `advisors/prism_numeric_verifier.py`:
- `verify_cited_numbers(run_id, market_prism_row, lens_sections=None) -> dict` — the pure orchestrator (AC-1). Extracts `raw_response.cited_numbers`, resolves ground truth per indicator via the registry, classifies each check, assembles the result dict.
- `_INDICATOR_REGISTRY: dict[str, tuple]` — the named indicator→ground-truth map (AC-3, AC-15).
- `_resolve_ground_truth(indicator, lens_sections) -> tuple[float | None, str, bool]` — navigates the payload path; returns `(value, source_id, available)`.
- `_normalize(indicator, raw) -> float | None` and `_classify(cited, truth, tolerance) -> str` — unit normalization + severity classification (AC-5, AC-6).
- `persist_verification(run_id, result) -> int | None` — idempotent append-only INSERT (AC-7, AC-8); lazy `import database`.

**Reused hook** — `prism_scheduler.py`:
- `main()` already calls `_patch_provenance(run_id, row)` after F-4 row-verification (`prism_scheduler.py:521`). `_patch_provenance` (`prism_scheduler.py:332-487`) already builds `_lens_cache_sections` from all 5 `ai_advisor._build_*_section()` builders. **Recommended:** refactor `_patch_provenance` to *return* its `_lens_cache_sections` (or extract a shared `_build_all_lens_sections()` helper) so `main()` can pass the same payloads into `verify_cited_numbers` — one fetch feeds both SOURCES and VERIFICATION (AC-4). Fallback: verifier re-fetches when `lens_sections=None`.
- Add the verifier call in `main()` immediately after `_patch_provenance`, guarded, never gating `sys.exit(0)` (AC-8).

**Ground-truth payload shapes** (the source of truth — confirmed at these lines):
- derivatives (`ai_advisor.py:755-765`): `payload.vix_level` (float), `payload.vix_term_structure.{spot,term_3m,ratio,spread,regime}`, `payload.risk_read`.
- macro (`ai_advisor.py:900-905`): `payload.series.{DGS10,UNRATE,CPIAUCSL,FEDFUNDS}.{label,value,date}` — `value` is a **string** from FRED (normalize to float).
- sentiment (`ai_advisor.py:673-684`): `payload.tone_score` (float), `payload.article_count`.
- technicals (`ai_advisor.py:542-552`): `payload.{ma_posture, breadth (fraction 0-1), momentum (per-ticker dict)}`.
- fundamentals (`ai_advisor.py:1242-1253`): `payload.tickers.<TICKER>.key_facts.<concept>.{label,value,unit,end,filed}`.

**Council contract change** — `.claude/agents/prism-synthesizer.md` (step 9 raw_response block, lines 130-145) gains `cited_numbers`; the 5 analyst files (`prism-*-analyst.md`) gain a rule: any numeric indicator you state in prose must be emitted as a `{indicator, value, lens}` tuple to the synthesizer.

**DB accessor** — `database.py`: add `get_latest_market_prism_verification_for_run(run_id)` (AC-9), a byte-for-byte structural mirror of `get_latest_market_prism_sources_for_run` (`database.py:1205-1234`) with the role string swapped. No migration; no new columns.

**Render** — `app.py:ai_advisor_tab()` (AC-10): after the SOURCES merge (`app.py:3862-3904`), add a sibling block that fetches the VERIFICATION row by `_mp_run_id` and attaches a `numeric_verification` context key + per-lens badges. `templates/ai_advisor.html` Overview block gains a minimal badge/annotation region (additive; honest empty-state).

**No migration.** Verification output is a JSON-blob append-only observation via the existing `insert_advisor_observation` with a new `advisor_role` — identical to how `MARKET_PRISM_SOURCES` shipped with zero schema change. Current highest migration is `032_prism_audit_log.sql`; this feature adds none.

## Edge Cases

- **No `cited_numbers` on the row** (legacy MARKET_PRISM, or `lens_pipeline`-produced): empty checks, verdict `no-numeric-claims` (AC-11). Never an error.
- **Cited value is a string / malformed / non-numeric**: `_normalize` returns `None` → that check is `unverifiable`; never raises.
- **Indicator not in registry** (e.g. council cites a derived "2s10s spread" or "YoY CPI %" that has no raw payload field): `unverifiable`, counted separately (AC-3). Note: derivatives *does* expose derived `spread`/`ratio`/`regime`, so those are registrable; raw-CPI-index vs YoY-inflation is the classic un-mappable case.
- **Source unavailable at verify time** (FRED down, `available=False`): all that lens's cited numbers → `unverifiable` (AC-12); never a false override.
- **Ground-truth drift council-time → verify-time**: daily FRED/VIX series are stable within the minutes between council and patch; GDELT tone (rolling artlist) may drift → drift-tolerant tolerance + documented caveat (AC-13). Prevents false override on legitimate drift.
- **Two lenses cite the same indicator**: each verified independently against the canonical source (AC-14); a mismatch in either is not masked by a pass in the other.
- **Rounding / significant figures**: council says "VIX ~22", truth 21.8 → within absolute tolerance → `pass` (AC-5). Council says "VIX 22", truth 18.1 → `overridden` (AC-6).
- **Fundamentals magnitude**: revenue in the hundreds of billions → relative tolerance, not absolute (AC-5).
- **Idempotent re-run** of the scheduler for the same `run_id`: existing VERIFICATION row → skip INSERT (AC-8).
- **`_patch_provenance` builder raised for a lens**: that lens's section is a BuildError block → treated as source-unavailable (AC-12).
- **Verifier itself raises**: caught at the `main()` call site and in the module (D-1); nightly run still exits 0 (AC-1, AC-8).

## Security Considerations

- **Advisory-only / off-path**: no `LIVE_EXECUTION`, no trade/position mutation, never imported from `alpha_bot_execution.py` (CC-2 lazy import). No new Flask *write* route; the render overlay is read-only.
- **D-1 error contract**: FRED URLs embed `FRED_API_KEY` as a query param — only `type(exc).__name__` is ever logged or persisted; never `str(exc)`, never a URL, never a payload dump (AC-1). Follows the existing `_build_macro_section`/`_patch_provenance` discipline.
- **Append-only / injection**: all DB writes go through parameterized `insert_advisor_observation`; no destructive op; the MARKET_PRISM row is immutable (never updated/deleted). The new accessor uses `json_extract(...) = ?` parameter binding (no string interpolation).
- **Untrusted council output**: `cited_numbers` originates from an LLM — treat every value as untrusted input. `_normalize` coerces defensively (float() in try/except), bounds list length (cap `_MAX_CITED_NUMBERS`), and never `eval`s or templates content.
- **Data exposure**: verification results are advisory analysis stored in the local state DB only; no external send.
- **Adopt-existing-contracts**: ground truth is the provider payload (FRED/SEC/GDELT/Alpaca) surfaced by the existing builders — the verifier never invents a composite or infers a missing value.
- **Pytest DB sentinel**: tests set `DB_PATH` via `tests/conftest.py`; the verifier honors it (no direct `alphabot_state.db` access).

## Testing Strategy

Backend-only feature — **no UI, so no design-system / computed-color tests** (the render badge is asserted at the template-context level, not by rendered RGB).

**New unit test file** — `tests/prism/test_prism_numeric_verifier.py` (target the module directly, `-n0`):
- `test_pass_within_tolerance` — cited VIX 21.8, ground truth 22.0 → `pass`.
- `test_flagged_outside_tolerance` — cited UNRATE 4.3, truth 4.0 (bounded) → `flagged`, prose unchanged.
- `test_overridden_gross_mismatch` — cited VIX 22, truth 18.1 → `overridden`, ground-truth value recorded.
- `test_unverifiable_no_registry_entry` — cited "2s10s YoY" with no mapping → `unverifiable`.
- `test_unverifiable_source_unavailable` — macro section `available=False` → all macro checks `unverifiable` (AC-12).
- `test_no_cited_numbers_returns_no_claims` — MARKET_PRISM row without `cited_numbers` → verdict `no-numeric-claims` (AC-11).
- `test_malformed_cited_value_never_raises` — non-numeric / None value → `unverifiable`, no exception (D-1).
- `test_fred_string_value_normalized` — FRED `value:"4.21"` (string) compared correctly (AC-5).
- `test_duplicate_indicator_independent` — two lenses cite VIX, one wrong → both checks present, mismatch not masked (AC-14).
- `test_drift_tolerant_tone` — tone drift within drift tolerance → `pass`; stable series strict (AC-13).
- `test_persist_idempotent` — second call with an existing VERIFICATION row skips INSERT (AC-8).
- `test_verify_never_raises_on_builder_exception` — a builder raising is swallowed, verdict degrades honestly (AC-1).
- `test_off_execution_path` — assert the module is not importable from `alpha_bot_execution.py` (grep-style guard, mirrors existing CC-2 tests).

**Golden-fixture tests** — `tests/fixtures/prism_verifier/`:
- A captured `MARKET_PRISM` `raw_response` with `cited_numbers` + captured `_build_*_section()` payloads (schema-derived or captured-from-producer; **not** parser+fixture co-design). Assert the full `verify_cited_numbers` result dict (checks + summary + verdict) shape and classifications. Values that are provider-computed are asserted by shape/status, never hardcoded.

**Scheduler wiring test** — `tests/prism_scheduler/test_verifier_wiring.py`:
- `main()` calls `verify_cited_numbers` after `_patch_provenance`; a verifier exception does not change `sys.exit(0)` (AC-8).
- The shared `lens_sections` are passed through (no double fetch) — assert the builders are invoked once.

**DB accessor test** — extend `tests/database/`:
- `get_latest_market_prism_verification_for_run` exact-match + None-on-mismatch (AC-9), mirroring the SOURCES accessor tests.

**Run protocol:** `DB_PATH` via `tests/conftest.py`; targeted `-n0` through the memory cap, e.g. `pytest tests/prism -n0 -o addopts= -p no:xdist`. No real FRED/SEC/GDELT/Alpaca calls (all builders mocked/fixtured). Never the full uncapped suite locally.

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| **D-1** | **Verify STRUCTURED cited-number tuples (Option b), NOT prose regex-extraction (Option a).** | Option (a) is brittle: "VIX ~22", "the vol index near 22", "22-ish" defeat regex; fuzzy indicator matching produces false mismatches and can't be bounded — the antithesis of the codebase's deterministic/testable/never-fabricate ethos. Option (b) fits: the synthesizer *already* writes a structured `raw_response` dict (`prism-synthesizer.md:130-153`), so adding a `cited_numbers` array is a small additive schema change; every check becomes a deterministic path lookup + numeric compare. **Threat model:** the council is error-prone, not adversarial — an honest hallucination/transcription error emits the same wrong number into both prose and the tuple, which (b) catches. The residual "prose contains a number absent from `cited_numbers`" gap is mitigated by the analyst/synthesizer prompt rule (AC-2) and documented as a known limitation; full NLP prose-extraction is explicitly OUT. |
| **D-2** | **Write a separate append-only `MARKET_PRISM_VERIFICATION` row; render-layer overlay applies "override" — never mutate the MARKET_PRISM row.** | The append-only invariant is load-bearing here (the codebase *deleted* `update_advisor_observation_raw_response` to enforce it). The verifier runs post-council (reusing the `_patch_provenance` hook, after the row is already written), so it *cannot* edit the row anyway. The `MARKET_PRISM_SOURCES` precedent (separate row + render overlay, zero migration) is the proven pattern. "Override" therefore means the render prefers the ground-truth value with an annotation, not an in-place edit. |
| **D-3** | **No schema migration.** | Output is a JSON-blob observation via existing `insert_advisor_observation` + a new `advisor_role`. Identical to how `MARKET_PRISM_SOURCES` shipped. Additive, reversible, zero DB-shape risk. |
| **D-4** | **Reuse the `_patch_provenance` patch-time section fetch as ground truth; no LLM re-ask.** | `_patch_provenance` already invokes all 5 builders; threading those payloads into the verifier gives deterministic, bounded, zero-extra-fetch ground truth. A bounded LLM re-ask is deferred (OUT) to keep this cycle fully deterministic. |
| **D-5** | **Severity policy: pass / flagged / overridden / unverifiable; drift-tolerant for GDELT tone, strict for daily series.** | Distinguishes a legitimate rounding/period difference (flag, keep prose) from a gross hallucination (override, prefer truth) from an un-checkable claim (unverifiable, honest limited-inputs) — never a silent pass. Drift tolerance prevents false overrides on legitimately-moved rolling data (DE-PRISM-SOURCES-001 §Provenance honesty). |

## Scope Boundaries

**IN:**
- `advisors/prism_numeric_verifier.py` (new): deterministic verifier + registry + persistence.
- `cited_numbers` schema addition to the council output + the 6 `.claude/agents/prism-*.md` prompt updates (AC-2).
- `prism_scheduler.main()` wiring after `_patch_provenance` (+ optional shared-section refactor) (AC-8).
- `database.get_latest_market_prism_verification_for_run` accessor (AC-9).
- Additive Overview render overlay in `app.py:ai_advisor_tab()` + minimal `templates/ai_advisor.html` badge region (AC-10).
- Unit + golden-fixture + wiring + accessor tests.

**OUT (explicitly deferred to separate future cycles):**
- **#2 bad-bar / data-quality gate** on the source payloads themselves (validating that FRED/Alpaca returned sane values before the council reads them) — separate cycle.
- **#3 verifier calibration** (empirically tuning per-indicator tolerances / override thresholds from historical runs) — separate cycle; this cycle ships defensible defaults with source comments.
- **Bounded LLM re-ask** on a detected mismatch (asking the council to reconcile) — deferred (D-4); this cycle is fully deterministic.
- **Full NLP prose-extraction** of numbers not declared in `cited_numbers` (D-1 residual-gap) — OUT; mitigated by the prompt rule only.
- **Verifying derived/composite figures with no raw payload field** (e.g. YoY inflation % from the raw CPI index, custom spreads not in the derivatives payload) — those resolve to `unverifiable`; a derivation layer is future work.
- **Any change to the live execution path, trade logic, or gating** — the verifier is advisory-only and never blocks trading.
- **Retrofitting verification onto historical MARKET_PRISM rows** — verification runs forward-only on new nightly runs (legacy rows → `no-numeric-claims`).
