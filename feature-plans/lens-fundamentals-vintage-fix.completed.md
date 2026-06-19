# Feature: Fundamentals Lens Vintage Fix (F5 — Mode A + Mode B)
Status: ready
Created: 2026-06-17

## Summary
The SEC EDGAR fundamentals lens (`ai_advisor.py`) reports `available=True` but serves WRONG-VINTAGE values — the "available=True but wrong vintage" defect class (the stale-VIX #37 analog), confirmed end-to-end by the AI Advisor closeout (`CLOSEOUT-VERDICT.md`, finding F5, with live runnable SEC evidence). Two concurrent defects, BOTH must be fixed:
- **Mode A — XBRL concept deprecation.** `_SEC_KEY_CONCEPTS` (`ai_advisor.py:354-360`) hardcodes a single us-gaap tag per concept with no fallback. When an issuer migrates a concept to a new GAAP tag, the old tag freezes and the producer never reaches the current data. Live evidence: MSFT migrated `Revenues` → `SalesRevenueNet` → `RevenueFromContractWithCustomerExcludingAssessedTax`; the producer queries only `Revenues` (frozen at `end=2010-06-30`) and never reaches the current `RevenueFromContractWithCustomerExcludingAssessedTax` (`val` at `end=2025-06-30`, present in EDGAR, 48 10-K entries).
- **Mode B — wrong sort key picks the OLDEST comparative entry.** The entry selection (`ai_advisor.py:1008-1019`) sorts `entries_to_check` by `e.get("filed","")` descending and takes `[0]`. A single 10-K bundles comparative prior-period entries that all share one `filed` date; Python's stable sort then yields the OLDEST `end` first. Affects ALL tickers / ALL non-deprecated concepts (JPM control: all 5 concepts 1-2 yr stale; no Mode A). The fix: sort by `end` descending (latest reporting period), `filed` descending as a secondary tiebreak.

Fix is advisory-only / off-execution-path / never-raising (D-1). Both the single-ticker path and the portfolio fan-out path consume the same selection logic and must both be preserved.

## Acceptance Criteria
- [ ] AC-1 (Mode B — latest period selected): given a us-gaap concept whose `units` contain multiple 10-K entries sharing one `filed` date but differing `end` dates (the bundled-comparative case), the producer selects the entry with the MOST RECENT `end` (not the oldest). Assert the selected `end` equals the max `end` present in the fixture for that concept; assert the selected `value` equals the fixture's value AT that max-`end` entry (derived from the fixture — no hardcoded producer numbers).
- [ ] AC-2 (Mode A — concept fallback reached): given an issuer whose primary/legacy tag is frozen (older `end`) but a migrated equivalent tag carries current data (newer `end`), the producer returns the value from whichever candidate tag has the most recent `end`. Assert the producer reaches the migrated tag (e.g. `RevenueFromContractWithCustomerExcludingAssessedTax`) and returns its latest-`end` value when it is more recent than the legacy `Revenues` tag.
- [ ] AC-3 (cross-tag latest-end selection): when MULTIPLE candidate tags for one logical concept are present, the producer picks the single entry with the most recent `end` ACROSS all present candidate tags (union, then latest-`end`), not merely the first tag in the list. Assert with a fixture where the first-listed candidate tag is staler than a later-listed one.
- [ ] AC-4 (payload shape preserved / backward-compat): the `key_facts` output dict keeps its existing logical keys and value shape (`{label, value, unit, end, filed, form}`) and existing source-citation structure; downstream consumers (Overview render, synthesis prompt) see no key-name or shape change. Assert the output keys are exactly the current set and each entry has the same fields.
- [ ] AC-5 (no false freshness; honest degradation preserved): when NO candidate tag for a logical concept is present, that concept is omitted (as today) — never fabricated. When companyfacts is missing/unfetchable, `available=False` with `type(exc).__name__`-only reason (D-1) is unchanged. Assert a concept with zero present candidate tags is absent from `key_facts`, and the fetch-failure path is byte-equivalent to current behavior.
- [ ] AC-6 (both paths preserved): the single-ticker path (`_build_fundamentals_section(ticker=<one>)`) and the portfolio fan-out path (`ticker=None` over `logic_holdings ∪ _FUNDAMENTALS_PROXY_UNIVERSE`) both apply the corrected selection; the single-ticker call shape is otherwise preserved. Assert both paths return corrected vintages.
- [ ] AC-7 (never-raising): a malformed/partial companyfacts payload (missing `units`, non-list entries, missing `end`/`filed`) does not raise — the producer degrades honestly per the existing D-1 contract. Assert no exception escapes for several malformed fixtures.

## Architecture
Edit `ai_advisor.py` only (the SEC companyfacts producer; no other module changes).

1. **`_SEC_KEY_CONCEPTS` restructure (Mode A).** Change the value type from a single label string to a `(label, ordered-candidate-tag tuple)` so each logical concept can carry GAAP-tag-migration fallbacks. Suggested shape (implementer finalizes via /tdd):
   ```python
   # logical concept key -> (display label, ordered us-gaap candidate tags newest-naming first)
   _SEC_KEY_CONCEPTS: dict[str, tuple[str, tuple[str, ...]]] = {
       "Revenues":           ("Revenue",              ("RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues")),
       "NetIncomeLoss":      ("Net Income / Loss",    ("NetIncomeLoss",)),
       "Assets":             ("Total Assets",         ("Assets",)),
       "Liabilities":        ("Total Liabilities",    ("Liabilities",)),
       "StockholdersEquity": ("Stockholders Equity",  ("StockholdersEquity",)),
   }
   ```
   Keep the OUTER logical key names identical to today's (`Revenues`, `NetIncomeLoss`, `Assets`, `Liabilities`, `StockholdersEquity`) so `key_facts` output keys are byte-preserved (AC-4).
2. **Selection loop rewrite (`ai_advisor.py:997-1027` region) — combines Mode A + Mode B.** For each logical concept: gather candidate entries from ALL present candidate tags in `us_gaap` (10-K preferred via the existing `form == "10-K"` filter, falling back to all entries as today); UNION them; sort by `e.get("end","") or ""` DESCENDING with `e.get("filed","") or ""` descending as the secondary key; take `[0]`. Preserve the existing `seen_accessions`, source-citation, and unit-walk logic. The `try/except` around the sort stays (never-raising).
3. **Both call paths unchanged in signature.** The selection logic lives in the shared companyfacts parser used by both single-ticker and portfolio fan-out, so both inherit the fix without API changes.
4. **Ride-along (trivial, documented — C2-COMMENT-1):** correct the stale comment at `ai_advisor.py:1710` ("Three independent layers" → four gates; add the locked-var gate to the enumeration). Comment-only, no behavior change; reviewer confirms it is purely cosmetic.

## Design-System Mapping
N/A — backend producer fix; no UI components. (The Overview render of these values is RF-1, a SEPARATE follow-on cycle; this cycle must not change the `key_facts` shape it renders.)

## Edge Cases
- A logical concept where NONE of the candidate tags are present → concept omitted from `key_facts` (AC-5); never fabricated.
- Candidate tags present but all with empty `units` → omit; no raise.
- Entries missing `end` → treated as oldest (empty-string sort key) so they never outrank a dated entry; never raise (AC-7).
- All entries share the same `end` (genuine restatement) → secondary `filed`-desc tiebreak prefers the most recently filed.
- Non-10-K-only issuer (no `form=="10-K"` entries) → falls back to all entries, latest `end` (existing behavior preserved, now with correct sort).
- 10-Q vs 10-K mixing: existing logic prefers 10-K; preserved. (We do NOT start trusting 10-Q over 10-K — out of scope.)
- Portfolio fan-out with a mix of resolvable/unresolvable tickers → per-ticker honest degradation unchanged.

## Security Considerations
- **Input validation / injection:** companyfacts JSON is external (SEC). Parsing is read-only dict traversal; no eval, no query interpolation. Malformed payloads must degrade (AC-7), not raise or leak.
- **SSRF / URL:** CIK/companyfacts URLs are built from validated CIK lookups (unchanged); no user-supplied URL component introduced.
- **Data exposure:** values are public SEC filings; no secrets. No new fields reach the client beyond the existing `key_facts` shape.
- **DoS:** candidate-tag union is bounded (≤3 tags/concept, 5 concepts); no unbounded growth. Bounded SEC fan-out (existing) unchanged.
- **Advisory-safety:** touches no `LIVE_EXECUTION`/credential path; not added to `_SETTINGS_WRITE_ALLOWLIST`; off the execution path.

## Testing Strategy
Unit tests (new `tests/ai_advisor/test_fundamentals_vintage.py` or extend the existing fundamentals test module) — NO live SEC; all via golden companyfacts fixtures under `tests/fixtures/`:
- **Golden fixtures (schema-derived / captured-shape):** build companyfacts JSON fixtures exhibiting (a) the bundled-comparative case (multiple 10-K entries sharing one `filed`, differing `end`) for Mode B; (b) a migrated-concept case (legacy `Revenues` frozen-old + `RevenueFromContractWithCustomerExcludingAssessedTax` current) for Mode A; (c) a cross-tag case where a later-listed candidate tag is the freshest (AC-3); (d) malformed/partial payloads (AC-7). Fixture provenance: schema-derived from the real SEC companyfacts shape (the closeout's runnable SEC evidence documents the exact structure) — NOT parser-co-designed.
- **Assertions derive from the fixture, never hardcoded producer numbers** ([[feedback_no_hardcoded_test_values]]): assert the SELECTED `end` == max `end` in the fixture; assert the selected `value` == the fixture entry's `val` AT that `end`; assert the fallback tag is reached; assert `key_facts` keys == the current logical set (AC-4); assert membership/shape/counts.
- **Both-path tests (AC-6):** single-ticker and portfolio fan-out both apply the fix (mock `_get`/HTTP seam; fixture-fed).
- **Never-raising (AC-7):** several malformed fixtures → no exception, honest degradation.
- **Regression / no-hollow:** a control fixture matching the JPM case (no Mode A; Mode B only) confirming all 5 concepts now select the latest `end`.
- **Gate:** `-n0` scoped run on `tests/ai_advisor` (+ any fundamentals tests elsewhere) before the PM merges; then the PM runs the genuine full-tree verifier vs base origin/main, `/review`, and a LIVE SEC functional test (real MSFT/JPM companyfacts → current `end` selected: MSFT Revenue end≈2025-06-30 via the migrated tag, JPM concepts current) before merge.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Combine Mode A + Mode B in one cycle | The closeout proved both are required; Fix 2 alone can't recover deprecated-tag concepts, Fix 1 alone leaves non-Revenue concepts 1-2 yr stale. Shipping one without the other still serves wrong vintages. |
| Restructure `_SEC_KEY_CONCEPTS` to (label, candidate-tag tuple) | Generalizes the migration fallback to ANY concept (not a Revenue special-case), and keeps the outer logical keys stable so the payload shape is byte-preserved. |
| Sort by `end` desc, `filed` desc secondary | `end` is the reporting period (what "latest fundamentals" means); `filed` secondary prefers the most recent restatement when `end` ties. |
| Preserve `key_facts` output keys | RF-1 (Overview render) + the synthesis prompt consume this shape; changing keys is out of scope and would risk the render. |
| Fold in C2-COMMENT-1 | Same file, one-line comment, zero behavior; cheaper than a dedicated cycle. Reviewer confirms triviality. |
| `[PM-ASSUMED]` candidate-tag lists | The Revenue fallback chain is from the closeout's live SEC evidence; other concepts keep their single tag (no migration evidence). If a future issuer shows another migrated concept, extend the tuple — additive. |

## Scope Boundaries
- **IN:** `_SEC_KEY_CONCEPTS` restructure + the selection-loop rewrite (Mode A + Mode B) in `ai_advisor.py`; golden companyfacts fixtures + unit tests; both single-ticker and fan-out paths; the trivial C2-COMMENT-1 comment correction at `ai_advisor.py:1710`; doc updates (DECISIONS entry + the CLAUDE.md `ai_advisor.py` key-files row note).
- **OUT:** RF-1 (Overview raw-JSON render — separate cycle); HF-1 (community route wiring — separate cycle); any 10-Q-over-10-K policy change; new composite ratios or invented metrics; any change to the fan-out universe, the lens-availability contract, or the synthesis path; any execution-path / credential / allowlist change.
