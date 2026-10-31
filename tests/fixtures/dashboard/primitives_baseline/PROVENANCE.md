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
