# Provenance: dashboard primitives base capture

Used by `tests/ui/test_dashboard_primitives_shared.py`. One file of data, `baseline.json`; the script
that wrote it, `capture_baseline.py`, is committed beside it.

## Record: `baseline.json` (case: GET / on the branch base, before the primitives move)

- Source: the real `GET /` route (`app.dashboard`) rendered by the Flask test client, and the
  stylesheets the returned markup links or inlines, read in document order.
- Producer call: `python tests/fixtures/dashboard/primitives_baseline/capture_baseline.py` with `DB_PATH`
  and `ATLAS_CACHE_DB_PATH` set to scratch files. The script refuses to run when `templates/index.html`,
  `static/layout.css`, `static/tokens.css` or `static/tweaks.css` differs from `HEAD`, and renders the
  route twice and refuses to write when the element structure differs between the two.
- Base SHA: `4bc4400ac0a8ecb5ca2a9af739a23e1c1cee3660` (branch `lane/ux-02` before any edit of this plan;
  the SHA is also written into `baseline.json` as `base_sha`). Time: 2026-10-30 23:24 UTC (`captured_at_utc`).
- Input state: every record of `tests/fixtures/prod_droplet/bot_state_positions.json`, verbatim (live names
  and ids as captured, no edit), written through the real writer `database.save_state`. The records are
  triggered and untriggered symphonies, so the route renders triggered and standby cards. The market state
  is pinned to `open`; the network is refused. No `armed` card is rendered because the capture holds no
  record with `armed` set, and making one would be a derived edit that needs sign-off; the status pill
  rules are compared as rules (the `resolved` entries), which does not depend on a rendered card.
- Contents: `structure` (the tag and sorted class tokens of every element in `<body>`, no text, no ids),
  `resolved` (declarations per `<at-rule>||<selector>` for the 23 selectors that name a primitive class),
  `ordered` (the 23 rules of the base inline stylesheet in source order).
- Cases covered: the element structure of the dashboard; the resolved declarations of each primitive
  selector; the order and content of the moved rules.
- Edits: none. Nothing in `baseline.json` was typed or changed after the script wrote it.
- Sign-off: not needed (no derived record).

The reading code is `tests/ui/_dashboard_primitives_capture.py`, shared by the script and the test.

## Revision: `resolved` and `ordered` for the phone layout (UX review fixes)

The UX review fixes change two primitives on purpose, so the base's rule records were revised; `structure` and `base_sha` are still the base's, and nothing else in the file changed.

- Producer call: `python tests/fixtures/dashboard/primitives_baseline/capture_baseline.py --rules-only` with `DB_PATH` and `ATLAS_CACHE_DB_PATH` set to scratch files, at head `38b0cbe8007ec7442cfb127114a8ff58d79b2a24` (templates, static and `app.py` clean). The flag runs the script's own render and reading code (twice, as always), replaces only `resolved` (from the rendered page) and `ordered` (read from `static/layout.css`, where the rules live since the move), and records `rules_revision` (head, time, counts before and after: 23 to 24 each).
- What differs from the base, read from `git diff` of the file: (1) a new rule `@media (max-width: 700px) .hero-section { padding: 18px 16px 16px; margin-bottom: 16px }`, a smaller hero box on a phone; (2) `.status-pill` gains `white-space: nowrap`, after `align-items`. No other selector's declarations changed, none was removed, and the order of the other 22 rules is unchanged.
- Sign-off: not needed (producer output; the two differences are the fixes' own).
