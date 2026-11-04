# state_poll_no_table fixtures: provenance

The poll cleanup compares `GET /api/state` with what the reference commit `758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7` served for the same stored state. Inputs are captured records or derived records with one named change each; every golden is the reference commit's own output, written by a committed producer.

## Derived records

`derive_records.py` (stdlib, `python -I derive_records.py`) writes these seven files from the captured records they name, with `json.dumps(indent=2, sort_keys=True)`; a test (`test_the_derived_records_are_what_the_derivation_writes`) re-derives them and compares bytes. Captured sources: `tests/fixtures/refresh_timing/01-c2/snapshot_two_symphonies.json` and `live_state_two_cycles.json` (themselves PM-signed derivations of captured droplet records), and `tests/fixtures/prod_droplet/bot_state_positions.json` (captured from the droplet by `capture_from_droplet.py`).

PM sign-off recorded in every row below: session 8055360f, 2026-11-04, producer owner, derived exactly as the approach C1 §Derived records states.

| File | Case it covers | Source records | Changed fields | sha256 | PM sign-off |
|---|---|---|---|---|---|
| `snapshot_two_accounts.json` | the close snapshot with two accounts and four symphonies, stored in non-name order | `snapshot_two_symphonies.json` (R8, PM-signed) and `prod_droplet/bot_state_positions.json` (captured) | the first account's list is stored in reverse order (Land of Feaver'd, then Corporate Chaos); a second account key `9a735bfb-ef62-4705-b9de-52f6de25363f` (a lower-case UUID v4, the captured key's format) holds the captured entries `Gpaw3IhZghQPRE6AdEKx` and `iaSOOUsmnCJHiZvbrWfs` in stored order Gpaw then iaSOO, each with `id` added and `account` set to the second account key; nothing else | `8418cc926abd455e34f46c636b6e9d337fa898c900998b83019e31799e3fb90a` | session 8055360f, 2026-11-04, producer owner, derived exactly as the approach C1 §Derived records states |
| `live_state_two_accounts.json` | the live state of three symphonies in two accounts | cycle `m` of `refresh_timing/01-c2/live_state_two_cycles.json` and the captured `Gpaw3IhZghQPRE6AdEKx` entry | the captured entry is added to the entries with its `account` set to the second account key; nothing else | `81588767247442c36687d7d4fc6b218b7131cc38aac81e4774f89ac32cf44b5f` | session 8055360f, 2026-11-04, producer owner, derived exactly as the approach C1 §Derived records states |
| `snapshot_missing_defaults.json` | one entry lacks all 13 defaulted fields | `snapshot_two_accounts.json` | the 13 keys of `app._FROZEN_SYM_DEFAULTS` removed from entry `5XjzXjdGnjh99MIsdM97` | `cf7ce2fce70af52eb83a8f0da0049fe30c368975d5cab4042901fc22937d3243` | session 8055360f, 2026-11-04, producer owner, derived exactly as the approach C1 §Derived records states |
| `snapshot_null_defaults.json` | the same entry carries each of the 13 fields as null | `snapshot_two_accounts.json` | the 13 keys of entry `5XjzXjdGnjh99MIsdM97` set to null | `8a09b9614f6375181c78ad6a4feeaa6d0e445f8e81a2115345cf7256a8fd447f` | session 8055360f, 2026-11-04, producer owner, derived exactly as the approach C1 §Derived records states |
| `snapshot_unnamed_entry.json` | one entry has no name | `snapshot_two_accounts.json` | `name` removed from entry `Gpaw3IhZghQPRE6AdEKx`, chosen so that its lower-cased id sorts differently from the stored order | `8dfa5565c43cbc44c079a3daff95e577feaae21c61b9179c8c278c0bc8b33dc9` | session 8055360f, 2026-11-04, producer owner, derived exactly as the approach C1 §Derived records states |
| `snapshot_no_accounts_map.json` | a snapshot with no account map | `snapshot_two_symphonies.json` (R8) | `accounts_map` removed | `8e312c4d7b18e9ff9cc14973949802ee73bd4f50f7acd513bc437e8f85002515` | session 8055360f, 2026-11-04, producer owner, derived exactly as the approach C1 §Derived records states |
| `live_state_prev_return_cases.json` | three live entries, one per `prev_return` and `current_return` case | `live_state_two_accounts.json` | `current_return` removed from entry `5XjzXjdGnjh99MIsdM97`; the captured Gpaw entry already has no `prev_return`; Corporate Chaos keeps both | `8461a58d98d270726631fa814969bd754d7bbd7c1ce95f429447499dbea36897` | session 8055360f, 2026-11-04, producer owner, derived exactly as the approach C1 §Derived records states |

## Goldens (the reference commit's output)

Each `golden_ref_<case>.json` is the whole `/api/state` body that `758f7f95` serves for one named world and request (`GOLDEN_CASES` in `tests/app/_state_poll_worlds.py`), `html` dropped, written with `json.dumps(indent=2, sort_keys=True)` and LF line ends. Producer output, not a hand-built mock: `tests/app/produce_state_poll_golden_ref.py` builds the worlds through the same builders the tests use and refuses to write unless `git diff --quiet 758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7 -- app.py templates static analytics.py database.py market_calendar.py` exits 0 and `git status --porcelain` on those paths is empty.

Command, run on the RED tree (whose production paths equal the reference) at 2026-11-04T00:11Z: `GOLDEN_OUT=<existing directory> python -m pytest -n0 tests/app/produce_state_poll_golden_ref.py`. Two runs in separate processes gave bodies equal except `daemon_started_at` (the test process start time), which the tests drop before comparing.

The producer lives in `tests/app/` and not in this folder so that the directory conftest applies to it exactly as it applies to the tests (the scheduler run state each test starts from decides `degraded_reasons` and `engine_phase`).

To regenerate later: `git worktree add --detach <tmp> 758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7`, copy `tests/app/produce_state_poll_golden_ref.py`, `tests/app/_state_poll_worlds.py` and this folder's records into the same relative paths, and run the command above there; the refusal check then passes by construction.

Fault worlds store the captured exit row `tests/fixtures/refresh_timing/01-c2/exit_trigger_row.json` through `database.record_exit_trigger` (the writer assigns a new id) so that the failing read would otherwise have found a figure. `golden_ref_closed_guard_alpha_fail.json` equals `golden_ref_closed_frozen_two_accounts.json` apart from `daemon_started_at`, as it must: a failed guard-alpha read leaves the body as if no exit row existed.

| File | World and request | sha256 | PM sign-off |
|---|---|---|---|
| `golden_ref_closed_frozen_two_accounts.json` | `closed_frozen_two_accounts` | `0347f8cc46e3888ef3965c14ae931c076ba6689514c6ab05cf2a5b8841590ec3` | n/a (producer output) |
| `golden_ref_closed_guard_alpha_fail.json` | `closed_guard_alpha_fail` | `0347f8cc46e3888ef3965c14ae931c076ba6689514c6ab05cf2a5b8841590ec3` | n/a (producer output) |
| `golden_ref_closed_missing_defaults.json` | `closed_missing_defaults` | `845d21772b7ab1a02971bbd74eb0324878f7ca13b6905e47e839481662db4452` | n/a (producer output) |
| `golden_ref_closed_no_accounts_map.json` | `closed_no_accounts_map` | `9c13725576f95947d0242babd7b882fab31b9871424ba674512535ebbebc2c4d` | n/a (producer output) |
| `golden_ref_closed_no_longer_held.json` | `closed_no_longer_held` | `89a5d53449744ba75189121fca052d91470b447e2bbbc0aa25c420d033fedff6` | n/a (producer output) |
| `golden_ref_closed_no_snapshot_two_accounts.json` | `closed_no_snapshot_two_accounts` | `dd1528db8b80ded35751b1b28a72e0894e176c84cd59fc56de82cc9ad63bb66d` | n/a (producer output) |
| `golden_ref_closed_null_defaults.json` | `closed_null_defaults` | `3a807656390ff9c6c466af2957c8f6a286f4f887bb86ff91e9882481b646dcb2` | n/a (producer output) |
| `golden_ref_closed_unnamed_entry.json` | `closed_unnamed_entry` | `6309520baae86ded446f8d3b722769cfa15d62c822decfa32c408d8cd241575e` | n/a (producer output) |
| `golden_ref_held_view.json` | `held_view` | `135ba9cacfebd62f27a33b1ed407265d25eae24a31cf105582e29a2d6f7697a9` | n/a (producer output) |
| `golden_ref_no_state.json` | `no_state` | `a359b73de8d4db347c41ec20076d703cb4c3790805633b1e875632be1f73cff8` | n/a (producer output) |
| `golden_ref_no_state_closed.json` | `no_state_closed` | `0b8787308ade949762dac9fd5ab6ef8fe1a5c205417389e792db7075cb4ec311` | n/a (producer output) |
| `golden_ref_open_analytics_fail.json` | `open_analytics_fail` | `c1fa570cab84b6ac89593f34fa014a739d4eafbcc0e650e181100963d89c4298` | n/a (producer output) |
| `golden_ref_open_panels_fail.json` | `open_panels_fail` | `4c93cb1bef95199d9c98c3054860d163c38552231064cda871d8794326c2e79e` | n/a (producer output) |
| `golden_ref_open_prev_return_cases.json` | `open_prev_return_cases` | `2a4da37d460258555bc79a0760c07d678a0c1372c74128c808e322c58c930f4b` | n/a (producer output) |
| `golden_ref_open_triggers_fail.json` | `open_triggers_fail` | `3dce14868d7852c764de85345fdf8a25e95d3568988446949453fc65f20681a4` | n/a (producer output) |
| `golden_ref_open_two_accounts.json` | `open_two_accounts` | `e995b40e0e84fed4c9e783cdda77fd4c4d8fed108a6e43af297564041532022f` | n/a (producer output) |
| `golden_ref_open_two_accounts__account_first.json` | `open_two_accounts__account_first` | `98d113f37fccc105e2ec1728ff3c4836723c784f5117c763aab3d983682c36cb` | n/a (producer output) |
| `golden_ref_open_two_accounts__account_none.json` | `open_two_accounts__account_none` | `6c8f803cca65e11d53259509fa4478d3f2c0fce09f5d58339041f00dfffdfce5` | n/a (producer output) |
| `golden_ref_open_with_snapshot_key.json` | `open_with_snapshot_key` | `892d8487492cf8dd2cde5237303ab3f4e588d89d8087d132abb4dc2f7b8e69db` | n/a (producer output) |
| `golden_ref_pre_market.json` | `pre_market` | `29dc142baef40cc31f917b4598c29696c0b0c58713ad8fa0153c0d771776cb32` | n/a (producer output) |
| `golden_ref_stalled_open.json` | `stalled_open` | `f27465521dd0f8acdda918cf4ea5486422f9b3cf149e91b6040c3d1ece7c4d87` | n/a (producer output) |
