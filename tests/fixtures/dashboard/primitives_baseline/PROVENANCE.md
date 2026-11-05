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

## Revision: `resolved` and `ordered` for the contrast fix (UX review fix batch)

`.legend-as-of` text carries a stamp, so its colour moved from `--studio-ink-faint` to `--studio-ink-dim` to clear 4.5:1 in both themes. `structure` and `base_sha` are untouched.

- Producer call: `python tests/fixtures/dashboard/primitives_baseline/capture_baseline.py --rules-only` with `DB_PATH` and `ATLAS_CACHE_DB_PATH` set to scratch files, at head `2e114278e95ccb8235fc94e84664242bbf7a499b` (templates and static clean). `rules_revision` now records this run (24 to 24 selectors and rules); the earlier phone-layout revision above is the one before it.
- What differs, read from `git diff` of the file: `.legend-as-of` `color` only, in `resolved` and in `ordered`. `.mini-stat-note` also changed colour in `static/layout.css` but is not one of the primitive selectors this record holds.
- Sign-off: not needed (producer output; the one difference is the fix's own).

## Revision: `resolved` and `ordered` for the unslop pass (shared chrome)

The unslop pass restyles the shared primitives on purpose (a status is a dot and a word, a row of stat figures has no box, section headers are sentence-case headings, a card has a hairline and no shadow, labels have no tracking, no weight above 600). `structure` and `base_sha` are still the base's.

- Producer call: `python tests/fixtures/dashboard/primitives_baseline/capture_baseline.py --rules-only` with `DB_PATH` and `ATLAS_CACHE_DB_PATH` set to scratch files, at head `efa817096f942f122129e1921e521513e3396966` (templates and static clean). `rules_revision` records this run (24 to 27 selectors and rules).
- What differs, read from `git diff` of the file: `.hero-section`, `.legend-as-of` (10 px on a desktop, a new 11 px rule at the phone breakpoint), `.mini-stat`, `.mini-stat-value`, `.mini-stat-label`, `.section-header`, `.sym-card` (and its hover), `.card-name` and `.status-pill` lose their old fills, uppercase, tracking, heavy weights and shadows; `.status-pill` and each variant gain a `::before` dot rule (the 11 variant selectors now name the `::before` dot; `.status-pill.sleeve-paused` keeps a rule for its word weight). The earlier colour admission in `tests/ui/test_dashboard_primitives_shared.py` is retired: the record holds the new colours.
- Sign-off: not needed (producer output; the differences are the pass's own).

## Revision: the `cash-now-btn` entries removed from `structure` (derived edit, security-and-controls C2)

- Why not the producer: `capture_baseline.py` cannot re-record `structure` at this head, for a reason that predates C2: it has no structure-only flag, its `--rules-only` mode leaves `structure` as it is, and its full mode rewrites `base_sha` and builds `ordered` from the inline template stylesheet, whose rules now live in `static/layout.css`, so a full run would break the ordered-rules test.
- Edit: the 11 `["button", ["cash-now-btn"]]` entries (the per-card Cash Now button, one per symphony card) are deleted from `structure` by removing their lines, one entry at a time; nothing else in the file changed, and `json.loads` of the new file equals `json.loads` of the old file minus exactly those 11 entries (997 to 986 entries). The file carries no other mention of Cash Now. The line endings are kept.
- Reason: the security-and-controls slice removes the control (AC-6), so a structure record that still lists its button would describe a page that no longer exists. The tests that read this list only check which primitive classes it holds and how many cards it holds, so they are unaffected.
- Commit: the commit that carries this edit, `test(security-and-controls): remove the Cash Now button entries from the primitives and shell baselines (C2)` on `feat/security-and-controls-c2`, after the GREEN commit `5b0ea52ac3011fd370f3c71b2221849cec7387f8`.
- Sign-off: PM approval of the derived edit, given in the C2 close review ([PM-ASSUMED]).

