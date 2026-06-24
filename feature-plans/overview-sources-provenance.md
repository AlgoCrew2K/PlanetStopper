# Feature Plan: Overview Market Prism — clickable SOURCES provenance (DE-PRISM-SOURCES-001)

Status: ready
Owner cycle: scoped Toxic Pair team (post-PR#81)
Operator complaint: #2 — the AI Advisor Overview tab's Market Prism "sources" render as plain lens labels (technicals/sentiment/…), not clickable provenance links.

## Summary
Make the Overview Market Prism sources render as clickable `<a href>` provenance links. The render template ALREADY emits links for any source carrying a `url` (via `per_lens_digest[lens].article_corpus` of `{url,title,published}` dicts) — but the LIVE producer (the nightly Prism **council**) never populates it: the synthesizer role file hard-codes `per_lens_digest[lens].sources = []`, and the analysts file PROSE to the audit log, dropping the url-bearing citations they fetched. The url data exists and is already validated by `ai_advisor.build_citation` (GDELT artlist + RSS for sentiment; FRED release URLs for macro/derivatives; SEC EDGAR filing URLs for fundamentals). Fix: a DETERMINISTIC post-council patch in `prism_scheduler.py` that rebuilds those validated per-lens citations and writes them into the MARKET_PRISM row's `raw_response.per_lens_digest[lens].article_corpus`. No template change, no DB migration.

## Design decision [PM-ASSUMED 2026-06-24]
DETERMINISTIC-PATCH (Python in `prism_scheduler`) chosen over LLM-THREADING (analysts file sources-JSON → synthesizer threads). WHY: (1) reliability — the council is a non-deterministic headless LLM; relying on the synthesizer to emit valid `article_corpus` JSON every night is fragile; (2) TDD-able — deterministic Python can be unit-tested; an LLM step cannot; (3) reuses the existing validated `build_citation` pipeline. REJECTED LLM-threading: non-deterministic, untestable, fragile. COST of chosen design: a lightweight per-lens citation re-fetch at synthesis time (nightly, off-hours, acceptable). The gated daemon path (`advisors/lens_pipeline.py:_build_per_lens_digest`, lines ~152-182) already threads sources correctly and is the REFERENCE implementation to reuse — do NOT reinvent the citation-building.

## Acceptance Criteria
- AC-1: After a council run writes a MARKET_PRISM `advisor_observations` row, `prism_scheduler` deterministically patches `raw_response.per_lens_digest[lens].article_corpus` with validated `{url,title,published}` citations for the url-bearing lenses (sentiment, macro, derivatives, fundamentals), reusing `lens_pipeline._build_per_lens_digest` / the `ai_advisor._build_*_section` citation builders + `build_citation` (NO reinvented citation logic).
- AC-2: `technicals` stays `sources:[]` / no `article_corpus` (Alpaca bar data has no public URLs) — NO fabricated urls.
- AC-3: Every emitted citation passes `build_citation` validation (http/https url + title + published); a lens whose fetch fails contributes NO citations (honest empty, never invented).
- AC-4: The patch is D-1 never-raises + off-execution-path: any failure (fetch error, malformed `raw_response`, missing row) leaves the row exactly as the council wrote it (Overview shows labels, no crash, no partial corruption); the council run's success/failure verdict is unchanged by the patch.
- AC-5: The Overview tab renders the patched `article_corpus` as clickable `<a href>` links (template already does this at `templates/ai_advisor.html:962-964/1034-1041`) — guarded by a render-contract test (GIVEN a `raw_response` with `article_corpus` url dicts → the Overview render emits `<a href>` for each, BOTH directions: real urls render as links, urlless sources render as plain spans).
- AC-6: Idempotent — re-patching the same row (re-run / retry) does not duplicate citations.
- AC-7: No DB migration (`raw_response` is a JSON blob); any row-update accessor added to `database.py` is additive (NULLable/default-safe, parameterized).

## Architecture
- SEAM: `prism_scheduler.main()` already calls `row = _get_market_prism_row_for_run(run_id)` (line ~259) to verify the council wrote the row (F-4). Add the patch in the SUCCESS path right after that confirmation.
- NEW `prism_scheduler._patch_provenance(run_id, row) -> bool` (D-1, never raises): build per-lens validated citations (reuse `lens_pipeline._build_per_lens_digest` or the `ai_advisor` lens-section builders + `build_citation`), merge them into `row.raw_response.per_lens_digest[lens].article_corpus`, persist via a new additive `database.update_advisor_observation_raw_response(row_id, raw_response_json)` accessor.
- `database.py`: additive update accessor for `advisor_observations.raw_response` by row id (sqlite-specialist; parameterized; no migration).
- `templates/ai_advisor.html` + `app.py`: NO change (already render `article_corpus` as links + pass `market_prism_summary` through). Guard with a render-contract test only.

## Edge Cases
- A lens fetch fails → that lens contributes no citations (no url), Overview shows its label plain — honest. Other lenses still get links.
- MARKET_PRISM row absent / malformed `raw_response` → patch is a no-op (D-1), council verdict unchanged.
- Re-run / retry on the same run_id → idempotent merge (no duplicate citations).
- technicals → never gets fabricated urls.
- Council writes verdict=limited-inputs (all lenses unavailable) → no citations, no crash.

## Security Considerations
- NO invented urls — every citation passes `build_citation` (http/https + provenance fields). Advisory-only, off-execution-path. No secrets in citations (urls are public sources). The patch reads only public data already fetched by the lenses; `_strip_secrets` semantics preserved if the warehouse is touched (it isn't here).

## Testing Strategy
- Toxic Pair TDD. Bounded `python -m pytest <new test files> -n0` ONLY (NEVER the full/uncapped suite — host reboot). ruff in-cycle.
- Patch-unit tests (quant-test-writer + implementer): GIVEN a MARKET_PRISM row + mocked lens-citation builders → `_patch_provenance` populates `article_corpus` with validated url dicts for the 4 url-bearing lenses; D-1 on builder failure (row unchanged); idempotency (re-patch no dup); technicals stays empty; malformed/absent row no-op.
- Render-contract test (quant-test-writer / flask-dashboard-specialist): GIVEN a `raw_response` with `article_corpus` url dicts → Overview render emits `<a href>` per citation; urlless `sources` render as plain spans (both directions, guards the JS↔template/render contract — the lesson from PR #81).
- DB accessor test (sqlite-specialist): the additive update round-trips `raw_response`.
- LIVE COUNCIL E2E (PM, the make-or-break): trigger a council run (manual or nightly) → confirm the MARKET_PRISM row's `article_corpus` has real urls per lens → render the Overview → clickable provenance links. (Operator-password-gated for the visual, like PR #81.)

## Scope Boundaries
- ONLY the LIVE council path: `prism_scheduler.py` (the patch) + `database.py` (additive accessor) + tests + the render-contract guard. Reuse `lens_pipeline`/`ai_advisor` citation builders.
- Do NOT touch the gated daemon `lens_pipeline.py` (already correct).
- Do NOT change the `prism-synthesizer.md` / `prism-*-analyst.md` role-file prompts (the deterministic patch makes LLM-threading unnecessary; leave the council prompts alone).
- Do NOT change `templates/ai_advisor.html` / `app.py` render logic beyond a render-contract TEST (no behavior change — it already renders links).
- NOT in this cycle (separate next cycle): MDD-"+"-on-drawdown sign + $-saved EOD-lag label.
