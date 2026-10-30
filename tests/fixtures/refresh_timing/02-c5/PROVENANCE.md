# Provenance: refresh-timing plan 02, cycle C5 (scheduled-pause phase and header label)

Every record is copied verbatim from the tracked production captures in `tests/fixtures/prod_droplet/`
(captured 2026-07-09 from the production droplet by `capture_from_droplet.py`, read-only). No
production database copy is persisted. Tests derive every expected value from these records and
from the frozen instant at run time; no phase or label is typed from a capture.

| File | Record | Source (file, record) | Case it covers | Changed fields | Used by | Sign-off |
|---|---|---|---|---|---|---|
| `symphony_state_entry.json` | symphony `5XjzXjdGnjh99MIsdM97` | `bot_state_positions.json`, key `5XjzXjdGnjh99MIsdM97` | the live `bot_state` card entry the live branch of `/api/state` and `/` reads | none (verbatim) | tests/app/test_engine_phase.py (all route cases) | not needed |
| `cycle_marker.json` | `last_successful_cycle_at` | `exit_triggers.json`, row id 87, column `cycle_id` (the engine writes that column from `last_successful_cycle_at`) | a stamp from a prior session (2026-07-09 10:14 ET): "no cycle yet today" on any later date | none (verbatim value) | tests/app/test_engine_phase.py (opening and no-cycle-today cases) | not needed |

## Stamps built at test time

A stamp for a moment of the frozen day (15:52, 10:57, 09:30:05) is produced by the test with
`datetime(...).isoformat()` in the captured value's own format (ET offset, six-digit microseconds), by
replacing the date and time of `cycle_marker.json`'s value. It is a scenario input (the plan's "last
stamp at 15:52"), not a stored record: no file holds it, and the only change from the captured value is
its date and time. A test asserts the built stamp has the captured value's shape. No derived file is
committed, so no sign-off line is open.
