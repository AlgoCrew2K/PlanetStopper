# 01-c5 fixtures: provenance

Plan: server-state-truth, cycle 5 (frozen cards and verdicts) and cycle 7 (verdict label), whose
worlds share the helper `tests/app/_refresh_timing_card_world.py`.

This folder holds no record files. Every record these cycles' tests use is derived at test time, in
code, from a signed-off record of `01-c2/` with only the fields named below changed. Nothing is typed
from memory and no expected figure is copied from a response: each expected guard alpha is the seeded
exit return minus the seeded `current_return`, each expected card string is built from the
`symphonies` entry of the same response, and each expected verdict sentence comes from node's
`toFixed` on the expected figure.

## Source records (unchanged)

| Record | Source | Used for |
|---|---|---|
| R8 `01-c2/snapshot_two_symphonies.json` | derived and signed off in cycle 2 | the close snapshot of the capture day with the two captured symphonies |
| R7 `01-c2/exit_trigger_row.json` | captured (droplet, `37c8e983`), see `01-c2/PROVENANCE.md` | the exit row of the triggered symphony, written through `database.record_exit_trigger` (a new autoincrement id, not the captured 509; no test reads the id) |
| `01-c2/live_state_two_cycles.json`, `01-c2/shadow_rows_*.json` | derived and captured, cycle 2 | live state and rows of the open-market world |

## Derived records

| Derived record (builder) | Source record | Changed fields | Reason | Scenarios | PM sign-off |
|---|---|---|---|---|---|
| Close snapshot with one armed card (`card_snapshot`) | R8 | the second symphony (`5XjzXjdGnjh99MIsdM97`) set `triggered: false`, `armed: true`; every other field and entry as in R8 | R8 holds both symphonies triggered (the 2026-10-22 capture); a card list needs a triggered and a non-triggered card to show that only triggered cards carry a guard alpha | S-6.1, S-6.2, S-6.5, S-6.7, S-6.8, S-6.9, S-8.3, S-9.1 to S-9.7 | pending: sign-off requested |
| Close snapshot without the triggered card's `current_return` (`card_snapshot(drop_current_return=True)`) | `card_snapshot` | the `current_return` key of symphony `nOyb55RMGVCKPiYXv7TI` removed | the absent-input case: no guard alpha may be computed against 0.0 | S-6.4 | pending: sign-off requested |
| Live state without the triggered card's `current_return` (`open_cards_world(drop_current_return=True)`) | `01-c2/live_state_two_cycles.json` entry for the cycle M state | the `current_return` key of symphony `nOyb55RMGVCKPiYXv7TI` removed | the same case on the live branch | S-6.4 | pending: sign-off requested |
| Exit return of a chosen value (`seed_exit_row(at_return=...)`) | R7 | `at_return` replaced by the live `current_return` of the symphony (a figure of exactly 0) | the zero-figure case of the verdict | S-9.7 | pending: sign-off requested |
| Live return moved by a chosen amount (`set_live_return`) | live state entry | `current_return` of the triggered symphony replaced by the exit return minus or plus 0.3, or by the earlier value plus 0.5 | two engine cycles in which the held return moves and the figure crosses zero | S-9.1, S-9.3 | pending: sign-off requested |
| Live state moved on after the close (`move_live_state_on`) | stored live state | triggered symphony: `triggered: false`, `armed: true`, `current_return` 7.77; second symphony: `triggered: true`, `armed: false`, `current_return` -6.66 | live values that differ from the snapshot in status and return, so a card read from live state is caught | S-6.1 | pending: sign-off requested |
