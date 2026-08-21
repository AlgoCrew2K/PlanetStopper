# Feature: Frontrunner Proposal Identity
Status: ready
Created: 2026-08-20

## Summary
Frontrunner Builder proposals are, by design, FULL standalone copies of the incumbent symphony with one frontrunner overlay spliced over one cascade (`advisors/frontrunner_builder.py:1120-1220`, migration `034` comment: "the full spliced candidate symphony"). But every surface names them as if they were a bare frontrunner: the Composer upload is named `"Frontrunner Candidate — <hash>"` with a generic description (`frontrunner_builder.py:1933-1934`), and the dashboard cards never state that the artifact is a complete copy of the operator's symphony. The operator (2026-08-20) ruled this misleading. This feature makes every proposal's identity honest: the Composer draft name carries the incumbent's display name plus an explicit "full copy + frontrunner overlay" identity, the Composer description carries full provenance, the dashboard cards state what the artifact actually is and what Approve actually creates, and the overlay delta itself (the thing that IS "the frontrunner") is persisted and surfaced per proposal.

Operator context (verified 2026-08-20, droplet DB): 3 live rows, all `proposal_source='frontrunner_builder'`, all incumbent `8FAXAnQmYi1INDubazeC`, each candidate_tree ~186KB (full spliced copies), all status `uploaded`. Two were created 2 minutes apart in the same weekly run (per-cascade loop at `:1497`) — so names must be unique per proposal, not per symphony/day.

## Acceptance Criteria
- [ ] AC-1: On approve, the Composer draft is named `"{incumbent_display_name} + FR overlay (full copy, #{proposal_id})"` — incumbent display name resolved hash→name from `database.load_state()` (same loop pattern as `ai_advisor_suggest`'s `resolved_hash`); falls back to the hash when unresolvable; total name length bounded (≤100 chars, display name truncated with `…`, the `#{proposal_id}` suffix never truncated). The bare-hash `"Frontrunner Candidate — <hash>"` format no longer occurs.
- [ ] AC-2: The Composer draft `description` carries provenance: incumbent display name + hash, the replaced cascade node id, a human-readable overlay summary, proposal id, creation date, and the sentences "Full standalone copy — the original symphony is not modified." and "Undeployed candidate — review before investing." Built by a pure, unit-testable helper.
- [ ] AC-3: At proposal-creation time (`frontrunner_builder.py` persist site `:1588-1596`), `metrics_json` gains three additive keys: `overlay_tree` (the compiled overlay node as spliced, JSON), `replaced_node_id` (the incumbent cascade node id that was swapped), `overlay_summary` (human-readable one-liner, e.g. condition + THEN-branch asset). Existing metrics keys byte-unchanged. No schema migration (the column is already free-form JSON).
- [ ] AC-4: Each `frontrunner_builder` dashboard card (Frontrunner Builder tab, `templates/ai_advisor.html` pending-approval cards) carries an identity line: "Full standalone copy of {incumbent name} with one new frontrunner overlay" and an explainer: "Approve creates a NEW undeployed Composer symphony — the incumbent is never modified."
- [ ] AC-5: Each `frontrunner_builder` card surfaces the overlay delta: the `overlay_summary` line plus a collapsible, escaped, bounded (≤4000 chars) preview of `overlay_tree` — rendered DISTINCT from the existing full-candidate preview, which is relabeled "Full spliced symphony (preview)".
- [ ] AC-6: Legacy rows (metrics_json lacking the AC-3 keys) render an honest empty-state — "overlay not recorded for this proposal" — never a fabricated summary; name/description on approve degrade gracefully (omit overlay summary / node id, keep the full-copy identity sentences).
- [ ] AC-7: `strategy_builder_retrofit` cards in the same queue get their own honest identity line ("From-scratch symphony proposed by Strategy Builder — does not contain the incumbent's logic") and are never given the frontrunner full-copy wording. Their approve-path name gains the same display-name + `#{proposal_id}` treatment with a "Strategy Builder candidate" identity instead of "FR overlay (full copy…)".
- [ ] AC-8: Zero change to detection, splice, gating, or acceptance math; `save_symphony`'s payload changes ONLY in the `name`/`description` strings; no new write routes; `LIVE_EXECUTION` untouched; the approve route's request/response contract unchanged.
- [ ] AC-9: Every new rendered field is `| e`-escaped (no `| safe`, no innerHTML); the incumbent display name (external Composer data) is escaped in both template and any JS path; name/description/overlay-preview sizes bounded by named constants.

## Architecture
- **`advisors/frontrunner_builder.py`**:
  - `splice_candidate_into_symphony` (`:1120-1220`) additionally returns the compiled overlay node + replaced cascade node id (additive extension of its return shape; single production caller in the build loop — verify actual return type at implementation and keep the change additive).
  - Persist site (`:1588-1596`): stamp `metrics["overlay_tree"]`/`["replaced_node_id"]`/`["overlay_summary"]` before `insert_frontrunner_proposal`.
  - New pure helpers (unit-testable, no I/O): `build_proposal_symphony_name(display_name, proposal_id, source)`, `build_proposal_symphony_description(...)`, `summarize_overlay(overlay_tree)` (condition + hedge-branch one-liner; degrades to "compound condition overlay" when underivable).
  - `approve_frontrunner_proposal` (`:1931-1939`): resolve display name, call the name/description builders, branch wording on `proposal_source` (AC-7).
- **`composer_draft_client.py`**: NO functional change — `save_symphony` already accepts `name`/`description`. (The hardcoded `_DEFAULT_ASSET_CLASS="EQUITIES"` defect is OUT of scope — separate cycle.)
- **`database.py`**: no migration; `insert_frontrunner_proposal` unchanged (metrics_json already free-form).
- **`app.py` `ai_advisor_tab()`**: extend the existing `get_pending_frontrunner_proposals()` prefetch to parse the three new metrics keys into template context (bounded overlay preview, mirroring the existing 4000-char candidate_tree bound).
- **`templates/ai_advisor.html`** (Frontrunner Builder tab cards, ~`:2254-2261` source-label region): identity line, explainer line, overlay summary, second collapsible `<details>` for the overlay preview, relabeled full-tree preview, retrofit-card identity line.
- **`static/ai_advisor.js`**: no changes expected (cards are Jinja-rendered; `frApprove`/`frReject` untouched).

## Design-System Mapping
No component library — project uses its studio CSS token conventions (`templates/ai_advisor.html` + `static/`), design tokens only, no raw hex.

| Element | Convention |
|---------|-----------|
| Identity line | existing card body text style, full-strength ink token |
| Explainer line | dim ink token (`--studio-ink-dim` family), small text — matches existing card meta lines |
| Overlay summary | existing card metric-row styling |
| Overlay preview | second `<details>` block, identical styling to the existing raw-candidate-preview `<details>` |
| Retrofit identity line | same dim-ink convention |

## Edge Cases
- Incumbent hash not present in `bot_state` (symphony removed since proposal creation) → name/description fall back to the hash; never crash, never blank name.
- Two proposals from one run (real: 2026-08-03 rows #1/#2 two minutes apart) → `#{proposal_id}` guarantees unique names.
- Overlay summary underivable (compound condition, missing fields) → generic "compound condition overlay"; never invented specifics.
- Legacy rows without AC-3 keys (the 3 live rows) → honest empty-state on cards; approve still produces the improved name + degraded description.
- `metrics_json` unparseable/non-dict → card renders without overlay section; approve proceeds with degraded description.
- Display name very long → truncate with `…` inside the 100-char bound, suffix preserved.
- Composer rejects long name/description → surfaced via existing `error_message` path (`:1941-1947`); a fixture pins the accepted payload shape.

## Security Considerations
- **XSS**: incumbent display name and overlay-derived strings originate from external data (Composer API / LLM output). All card renders `| e`-escaped; no `| safe`; JSON previews rendered as escaped text inside `<details>`, never parsed into DOM HTML.
- **Data exposure**: description sent to Composer contains only tree-derived, non-secret provenance; no credentials, no env values; builders are pure functions over already-persisted data.
- **DoS/size**: overlay preview bounded (≤4000 chars) server-side before template render; name ≤100 chars; description bounded by a named constant (≤1000 chars).
- **Injection**: no new SQL (existing parameterized accessors only); no new routes; CSRF surface unchanged.
- **Authz**: all rendering behind the existing global auth hook; approve route untouched.

## Testing Strategy
- `tests/advisors/test_frontrunner_proposal_identity.py` (new):
  - Unit: name builder (format, hash fallback, truncation, uniqueness via id, retrofit wording branch), description builder (provenance fields, degraded/legacy branch), `summarize_overlay` (simple condition, compound degrade, malformed input).
  - Persist: build-loop stamps the three metrics keys; existing keys unchanged (compare against a pre-change fixture dict).
  - Approve: `save_symphony` called with the new name/description (mocked client, assert payload); bare-hash format asserted ABSENT; legacy-row degrade path.
- Route/template tests (`tests/ai_advisor/`): `ai_advisor_tab` context carries overlay fields bounded; rendered HTML contains identity + explainer lines escaped (adversarial display-name with `<script>` asserted escaped); legacy-row empty-state; retrofit card wording; full-tree preview relabeled.
- Regression: existing frontrunner suites (`tests/security/test_frontrunner_no_trade_boundary.py`, approve-flow tests) stay green — AC-8.
- No design-system computed-style e2e (no JS change); PM live functional gate: render the tab against a droplet DB copy and eyeball the cards + one dry approve payload log before merge.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Persist overlay in `metrics_json` additive keys, not a migration | Column is already free-form JSON (`034`); additive-first, zero schema risk. [PM-ASSUMED] |
| Name format `"{name} + FR overlay (full copy, #{id})"` | States what it IS (operator ruling); unique per proposal (same-run duplicates are real). [PM-ASSUMED] — operator may re-word; format centralized in one pure helper. |
| Retrofit cards in scope (AC-7) | Same queue, same approve route; leaving them generic recreates the same confusion for the other source. |
| Renaming the 3 already-uploaded Composer drafts | OUT — one-time ops action after merge, not product code. |
| SIMPLIFY-ratio unreachable + `asset_class` EQUITIES defects | OUT — separate remediation cycle (found 2026-08-20, tracked in DECISIONS on close). |

## Scope Boundaries
- **IN**: naming/description of created drafts, overlay persistence at proposal creation, dashboard card identity/overlay rendering, retrofit-card identity line, pure helper functions + tests.
- **OUT**: any splice/detection/gating/acceptance math; `composer_draft_client` behavior; asset_class fix; SIMPLIFY ratio fix; renaming existing uploaded drafts; Discord surfaces; migrations.
