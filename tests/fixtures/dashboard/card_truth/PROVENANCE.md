# Provenance: dashboard card verdict, MC dial and fleet banner tests

Used by `tests/dashboard/test_card_verdict_dial_first_paint.py` and
`tests/dashboard/test_fleet_banner_render_dismiss.py`. No record is stored in this
directory: every record is built at test time from a committed capture and written
through the real producer, so the files here are this note only.

Time of record: 2026-10-22. Base: main at 37c8e983b64c.

## Records

### R1: one triggered symphony state record (case: a triggered card)
- Source: first record with `triggered: true` in
  `tests/fixtures/prod_droplet/bot_state_positions.json`, captured from the droplet by
  `tests/fixtures/prod_droplet/capture_from_droplet.py`. All other keys verbatim.
- Derived edits (named): `name` replaced by an invented name ("Fixture Ridge NN");
  `account` replaced by an invented UUID (`7e5b2c1a-4d3f-4a86-9b0e-2f6c8d1a3e57`);
  the symphony id replaced by an invented 20-character base62 id (`ct` plus 18 digits);
  `current_return` set to `1.0` so the difference the producer injects
  (exit return minus current return) is exact in binary for the dyadic family values.
- Written through: `database.save_state` (real writer). Read by the real `GET /` and
  `GET /api/state`.
- Cases served: every triggered-card scenario (verdict text, dash dial, no-exit-row).

### R2: one untriggered symphony state record (case: an armed untriggered card)
- Source: first record with `triggered: false` in the same capture, keys verbatim.
- Derived edits (named): `name`, `account` and the symphony id as in R1; `armed` set to
  `true` (the capture carries no `armed` key, and an untriggered card renders in the
  active section only when armed); `current_return` set to `1.0`; `mc_prob` set to each
  value of the dial family (0, 0.5, 12.5, 14.99, 15, 50, 62.5, 80, 80.5, 99.5, 100, 150,
  -5), to `null`, to absent (key removed), and to the engine sentinels +999 and -999.
- Cases served: dial label and arc offset rounding (exact ties at zero places: 0.5,
  12.5, 62.5, 99.5; an exact tie at two places: 50 gives an offset of 47.125),
  colour thresholds (14.99, 15, 80, 80.5), clamps (100, 150, -5), zero, unknown
  reading (null, absent), sentinel.

### R3: one exit-trigger row (case: a recorded exit)
- Source: first row of `tests/fixtures/prod_droplet/exit_triggers.json` with a
  `triggered_reason`; `triggered_reason`, `cycle_id`, `ts_utc`, `ts_et` verbatim.
- Derived edits (named): `symphony_id` and `account_id` invented as in R1; `at_return`
  set to `1.0 + g` for each guard-alpha value `g` of the family 0.25, 0.75, 1.25, 2.5,
  -0.25, -0.75, -1.75, 0.0, 0.04, -0.04 (the exact binary ties of one decimal place,
  both signs, and the near-zero pair).
- Written through: `database.record_exit_trigger` (real writer). The producer under
  test (`GET /` and `GET /api/state`) computes the guard alpha; the tests assert the
  producer's value equals `g` (absolute tolerance 1e-9).

### R4: fleet alert row (case: an undismissed engine alert)
- Source: the `example` of the schema-derived
  `tests/fixtures/engine/fleet_correlation/alert_state_shape.json`.
- Derived edits (named): `tripped_count` and `active_count` varied (1 of 8, 5 of 8,
  3 of 8, 1 of 3, 2 of 3, 0 of 8, 3 of 0); `triggered_reason` varied ("take_profit",
  "VWAP Breakdown", a reason with a non-ASCII letter, a reason with an apostrophe, a
  reason holding markup); `tripped_at_et` varied (a zone-offset form, a later time);
  `tripped_symphonies` set to invented names.
- Written through: `alpha_bot_execution.set_fleet_correlation_alert` ->
  `database.write_fleet_alert` (the real engine writer is the runtime validator).
  Cleared through `database.clear_fleet_alert` (the engine's auto-clear writer).
  Read back through `database.read_fleet_alert`.
- Cases served: sentence rounding of the fleet percentage (exact ties 12.5, 62.5,
  37.5), thirds, no percentage with nothing tripped or nothing active, reason title
  case at ASCII word boundaries, escaping, dismissal lifecycle.

### R5: `/api/state` bodies
- Source: the real route over the seeded state, fetched through the Flask test client
  with the market state pinned open or closed. The closed-market body is produced from
  a snapshot whose accounts map holds R1 and R2 entries; the snapshot shape follows the
  existing `tests/fixtures/dashboard/market_mode/eod_snapshot.json`.
- Derived edits: none, except the string-alert poll, whose `fleet_correlation_alert` is
  replaced by the legacy string form the engine used before the alert table.

## Rules applied

- No value is typed from a capture; expected strings are computed by node
  (`Number.prototype.toFixed`, `Math.round`) or read back from the producer.
- Display names, account ids and symphony ids are invented values of the real format.
- Real captures are not copied into this directory.

## Sign-off

PM sign-off on the derived records R1 to R4 (the `current_return`, `mc_prob`,
`at_return` and alert-count values, the invented names, ids and account UUID): given in
the cycle brief for this cycle, 2026-10-22; recorded here by the test author at the PM's
instruction.
