# Feature Plan: Overview Market Prism — clickable SOURCES provenance v2 (DE-PRISM-SOURCES-001)

Status: ready
Owner cycle: scoped Toxic Pair team (v2 redesign — append-only fix)

## Summary

Make the Overview Market Prism sources render as clickable `<a href>` provenance links.
The render template ALREADY emits links for any source carrying a `url` (via
`per_lens_digest[lens].article_corpus` of `{url,title,published}` dicts) — but the LIVE
producer (the nightly Prism council) never populates it.

**v1 design (BLOCKED):** `prism_scheduler._patch_provenance` mutated the existing
MARKET_PRISM row via `database.update_advisor_observation_raw_response`. This violated
the `advisor_observations` append-only contract and caused
`test_017::test_no_update_advisor_observation_symbol_in_database_module` to fail in
CI (1 failed / 8305 passed on PR #82).

**v2 design (this plan):** Persistence step redesigned to be fully append-only.
`_patch_provenance` builds the validated per-lens `article_corpus` citations (citation
logic SOUND and unchanged from v1) and then INSERTs a NEW `advisor_observations` row
with `advisor_role="MARKET_PRISM_SOURCES"`. `app.py` additively fetches that supplementary
row matched by the MARKET_PRISM row's `raw_response["run_id"]` and merges `article_corpus`
into the render context. No UPDATE accessor, no mutation, no migration.

## Acceptance Criteria

- AC-1: After a council run writes a MARKET_PRISM row, `prism_scheduler._patch_provenance`
  deterministically INSERTs a new `advisor_observations` row with
  `advisor_role="MARKET_PRISM_SOURCES"`, `subject_type="portfolio"`, `subject_id="global"`,
  and `raw_response={"run_id": <run_id>, "per_lens_digest": {lens: {"article_corpus":
  [{url,title,published}, ...]}}}` for the url-bearing lenses (sentiment, macro,
  derivatives, fundamentals). Reuses `ai_advisor._build_*_section` builders +
  `ai_advisor.build_citation`. No reinvented citation logic.
- AC-2: `technicals` lens is NEVER written into the SOURCES row (Alpaca bar data has no
  public URLs — no fabricated urls).
- AC-3: Every emitted citation passes `build_citation` validation (http/https url + title
  + published); a lens whose fetch fails contributes NO citations (honest empty-state).
- AC-4: The patch is D-1 never-raises + off-execution-path. Any failure (fetch error,
  malformed `raw_response`, missing row) leaves the existing MARKET_PRISM row unchanged
  and produces no SOURCES row. The council run's success/failure verdict is unaffected.
- AC-5: The Overview tab renders the merged `article_corpus` as clickable `<a href>` links.
  The template already does this at `templates/ai_advisor.html:1034-1041` — no template
  change needed. Guarded by a render-contract test (both directions: real urls render as
  links, urlless sources render as plain spans, no crash on empty-state).
- AC-6: `_patch_provenance` is idempotent per run: re-running for the same `run_id` does
  not insert a second SOURCES row (guard: check if a SOURCES row for this run_id already
  exists before inserting). The existing dedup-by-url logic (seen_urls set,
  first-occurrence wins) is preserved within each citation build.
- AC-7: No DB migration. No mutation accessor. `advisor_observations` remains append-only.
  The new read accessors in `database.py` use `get_ro_connection()`.
- AC-8: `get_latest_market_prism_summary()` is UNCHANGED — it is hard-filtered to
  `WHERE advisor_role='MARKET_PRISM'` and NEVER returns a MARKET_PRISM_SOURCES row.
- AC-9: `app.py` fetches the MARKET_PRISM_SOURCES row matched by the MARKET_PRISM row's
  `raw_response["run_id"]`. On run_id mismatch (no SOURCES row for this run), the route
  returns `None` and renders honest empty-state — NEVER bleeds a different run's citations
  into the current MARKET_PRISM read. A night where all lenses are unavailable produces no
  SOURCES row; falling back to last night's would show stale links against tonight's read.
- AC-10: `test_017_advisor_observations.py` passes in its entirety — in particular
  `test_no_update_advisor_observation_symbol_in_database_module` (no mutation accessor
  present) and `test_insert_advisor_observation_ids_are_monotonically_increasing` (ids
  strictly increase; no INSERT OR REPLACE).

## Architecture

**Surface 1 — `database.py`:**
- DELETE `update_advisor_observation_raw_response` (the v1 mutation accessor that fails
  test_017).
- ADD `get_latest_market_prism_sources_for_run(run_id: str) -> dict | None`:
  RO accessor. Queries `advisor_observations WHERE advisor_role='MARKET_PRISM_SOURCES'`,
  matches on `raw_response["run_id"] == run_id`. Returns the first matching row or `None`
  on no-match. Implementer may choose query mechanism; contract is the return shape.
  Uses `get_ro_connection()`.
- ADD `get_latest_market_prism_sources() -> dict | None`:
  RO accessor. Returns the most recent MARKET_PRISM_SOURCES row by id, or `None`.
  Uses `get_ro_connection()`.

**Surface 2 — `prism_scheduler._patch_provenance`:**
- KEEP the deterministic citation-build body (four builders, dedup-by-url, build_citation).
- REPLACE persistence: build a per_lens_digest-shaped payload carrying ONLY
  `per_lens_digest[lens]["article_corpus"]` for the 4 url-bearing lenses, then:
  1. Guard idempotency: if a SOURCES row already exists for this run_id, return True (no-op).
  2. INSERT via `insert_advisor_observation(advisor_role="MARKET_PRISM_SOURCES",
     subject_type="portfolio", subject_id="global",
     raw_response={"run_id": run_id, "per_lens_digest": {lens: {"article_corpus": [...]}}})`.
- Signature and D-1/AC-4 contract unchanged.

**Surface 3 — `app.py` `ai_advisor_tab()`:**
- AFTER fetching `market_prism_summary` (existing `get_latest_market_prism_summary()` call):
  - Extract `run_id = market_prism_summary["raw_response"].get("run_id")` if summary exists.
  - Call `database.get_latest_market_prism_sources_for_run(run_id)` -> `sources_row`.
  - If `sources_row` is not None: for each lens in
    `sources_row["raw_response"].get("per_lens_digest", {})`, merge
    `sources_lens["article_corpus"]` into the corresponding `per_lens_digest[lens]` on
    `market_prism_summary` BEFORE humanization.
  - If `sources_row` is None (no sources for this run — no match by run_id): render
    unchanged — honest empty-state. Never fall back to a different run's citations.
  - Entire merge is wrapped in try/except (never crashes the route).
- TEMPLATE: UNCHANGED.
- `.claude/agents/prism-synthesizer.md`: UNCHANGED.

## Edge Cases

- No SOURCES row for this run_id (all lenses unavailable, or patch failed): route renders
  MARKET_PRISM with plain lens labels — honest. No crash.
- SOURCES row is from a DIFFERENT run_id: returns None -> no merge, no stale citation bleed.
- Re-run / retry same run_id: idempotency guard in `_patch_provenance` prevents double-insert.
- Lens builder raises: that lens contributes no article_corpus key in the SOURCES row.
- MARKET_PRISM row absent: `_patch_provenance(run_id, None)` -> D-1 no-op, no INSERT.
- Malformed raw_response in SOURCES row: merge try/except swallows, renders unchanged.

## Security Considerations

- No invented urls — every citation passes `build_citation` (http/https + provenance fields).
- Advisory-only, off-execution-path. No secrets in citations (public sources only).
- No SQL injection risk — parameterized INSERT via `insert_advisor_observation`.

## Testing Strategy

Bounded `python -m pytest <target files> -n0` ONLY. NEVER full/uncapped/-n>4 suite.

- `tests/database/test_market_prism_sources_accessor.py` (new):
  Behavioral tests for new RO accessors. No coupling to internal query helper names.
  Asserts: `get_latest_market_prism_sources_for_run` returns matching row; None on
  run_id mismatch; None when no SOURCES row exists; RO connection enforced;
  `get_latest_market_prism_sources` returns latest by id; `get_latest_market_prism_summary`
  never returns MARKET_PRISM_SOURCES row. Verifies `update_advisor_observation_raw_response`
  is absent from database module.
- `tests/prism_scheduler/test_patch_provenance.py` (rewrite):
  INSERT asserts: advisor_observations row count grows +1; MARKET_PRISM row is
  byte-unchanged; SOURCES row has advisor_role="MARKET_PRISM_SOURCES", subject_id="global",
  raw_response["run_id"] matches; article_corpus has valid url dicts for 4 url-bearing lenses;
  dedup-by-url preserved; technicals absent; D-1/None-row no-op; idempotency (second call
  does NOT insert a second SOURCES row).
- `tests/prism_scheduler/test_patch_provenance_render_contract.py` (update):
  Mock both `get_latest_market_prism_summary` AND `get_latest_market_prism_sources_for_run`.
  Test url-bearing direction, urlless direction, AND empty-state (sources None -> no crash).
  Add cross-run mismatch test: latest MARKET_PRISM and latest SOURCES row have different
  run_ids -> per_lens_digest unchanged (no stale citation bleed).
- `tests/app/test_ai_advisor_tab_sources_merge.py` (new):
  Route merges article_corpus when sources row present; no merge when sources row is None;
  no bleed when run_id mismatch (MARKET_PRISM run_id != SOURCES run_id).
- `tests/database/test_017_advisor_observations.py` included in bounded -n0 gate run.

## Scope Boundaries

- ONLY: `database.py` (delete mutation accessor, add 2 RO accessors);
  `prism_scheduler.py` (`_patch_provenance` persistence step);
  `app.py` (`ai_advisor_tab` merge logic); new/rewritten tests.
- Do NOT touch: `templates/ai_advisor.html` (already correct);
  `advisors/lens_pipeline.py` (reuse, not modify);
  `.claude/agents/prism-*.md` role files; existing migration files.
- NOT in this cycle: MDD-"+"-on-drawdown sign, $-saved EOD-lag label.
