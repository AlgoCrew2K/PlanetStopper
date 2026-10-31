# Provenance: a state entry whose rows stopped (the "no longer held" cases)

Used by `tests/analytics/test_no_longer_held_predicate.py` and `tests/app/test_no_longer_held_surfaces.py`.
No data file lives here: the records are built at test time from committed captures, so the capture stays
the only copy.

## Class: captured record with a named derived edit

- Source records: `tests/fixtures/prod_droplet/bot_state_positions.json` (the entry `INfCn3eKsu6i4oTTqdUp`
  and ten others, verbatim) and `tests/fixtures/prod_droplet/shadow_last_rows.json` (13 sessions,
  2026-06-22 to 2026-07-09, one last row per symphony and day, verbatim).
  Producer: `tests/fixtures/prod_droplet/capture_from_droplet.py`.
- The case being modelled, from the PM's read-only droplet check of 2026-10-31 (named in
  `feature-plans/ux-overhaul/06-review-fixes.md` AC-8): `bot_state` holds `INfCn3eKsu6i4oTTqdUp`, its newest
  `shadow_history` row is old, and every other symphony has rows through the latest session.
  The capture itself cannot show this, because in it the symphony has all 13 sessions.
- Derived edit (the only one): the rows of `INfCn3eKsu6i4oTTqdUp` after a stated day are removed, so that the
  symphony's newest row is older than the two most recent sessions that hold rows for the others.
  The days kept are stated in each test (the boundary cases keep one row on the session just inside the
  cut and one just outside). Nothing else in a row is changed.
- Added state entries for the "new symphony with no rows yet" case are copies of one captured entry with a new
  id and name; they have no rows by construction.
- The surface tests (`tests/app/test_no_longer_held_surfaces.py`) copy a captured live entry of the
  refresh-timing world (`tests/fixtures/refresh_timing/01-c2/`) under a new id and give it shadow rows on two
  sessions older than the three the world holds. The returns of those rows are synthetic and labelled so in the
  test: they are large and opposite in sign to the other symphonies' so that any leak into a portfolio figure
  moves it.
- Sign-off: none needed for the derivation; it removes rows and adds no value. A later live read of the droplet
  replaces nothing here.
