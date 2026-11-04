# 01-c1 fixtures: provenance

Plan: server-state-truth, cycle 1. Batch FX-01.

All records here are captured, none derived. They come from the PM's read-only run of `capture.py` with `_cases_01.py` on the droplet, as the service user, against the live `/opt/planetstopper/alphabot_state.db`. The database was opened `mode=ro` (plus `PRAGMA query_only`); nothing was written. The capture ran at deployed SHA `37c8e983` (`origin/main`). The records were copied to `.claude/review/refresh-timing-captures/20261023T005559Z/` in the main checkout (untracked) and committed here byte for byte; the sha256 values below were equal on the droplet and locally, and were re-checked after the copy into this folder.

## Records

| File | Case it covers | Source | Capture moment | Sidecar | sha256 |
|---|---|---|---|---|---|
| `nested_bot_state_row.json.gz` | R1: the stored engine state whose `last_market_close_snapshot` nests earlier snapshots, with the phantom `Unknown` account entry and 12 symphony entries (case `01-c1-bot-state-row`) | table `bot_state`, row key `id = 1`, column `data` (storage class `text`), read as the verbatim bytes and gzipped without a file name or time so the bytes reproduce | `captured_at_utc` 2026-10-23T00:55:19Z. The state's own stamps: `last_successful_cycle_at` 2026-10-22T16:00:01.413198-04:00, top snapshot `trading_day` 2026-10-22, `captured_at_et` `16:00:01 ET` | `nested_bot_state_row.capture.json` | `22cc4b094abe0c2c51f924d89ca5c8afcca859369c6812fbea30d958cc6708d4` Changed field: `ACCOUNT_ID_REDACTED` replaces the account id and its 8-character prefix. |

Sidecar `nested_bot_state_row.capture.json` sha256: `e98bd337837404585eb5ca926cab0a6fcd250f1ca7e3bd77269891a7b1c738b9`.

Decompressed record: 2,065,179 bytes, sha256 `f9de4ca66297eaf909914353fd45ea2752d2fd145979e18bc2758aad9b0d4fa7` (the sidecar's `raw_sha256`).

## Droplet command

As the service user (the form recorded in the folder's root `PROVENANCE.md`):

```
runuser -u planetstopper -- /opt/planetstopper/.venv/bin/python -I /tmp/rt_capture/capture.py 01-c1-bot-state-row --db /opt/planetstopper/alphabot_state.db --out /tmp/rt_capture/out
```

The script is `tests/fixtures/refresh_timing/capture.py` with `_cases_01.py`, both from branch `refresh-timing/fixtures`.

## Structure of the record, checked by walking the committed bytes

Walked iteratively (the chain nests too deeply for a recursive reader to be safe), with `python -I`:

- UTF-8 decodes; `json.dumps(json.loads(text)) == text` is true, so the record round-trips byte for byte.
- Top level: a dict with 17 keys: 12 symphony entries (each a dict with `name`, every one carrying `account` `ACCOUNT_ID_REDACTED-...`) and the 5 reserved keys `date`, `last_execution_mode`, `last_market_close_snapshot`, `last_successful_cycle_at`, `post_mortem_run`.
- `last_market_close_snapshot` is a snapshot at the top, with keys `accounts_map`, `captured_at_et`, `data_as_of`, `portfolio_strip`, `shadow_divergence`, `trading_day`. The key name `last_market_close_snapshot` occurs once in the whole record (the sidecar's `last_market_close_snapshot_occurrences` is 1): the chain does not repeat that key.
- The nesting sits under `accounts_map`. `accounts_map` holds the real account list (12 entries, keyed `ACCOUNT_ID_REDACTED`) and an `Unknown` list of exactly one entry. That one entry is the previous snapshot itself, carrying the extra key `id` with the value `last_market_close_snapshot`: the builder treated the reserved key as a symphony. Each nested snapshot repeats the same shape, so the path of the Nth nested snapshot is `last_market_close_snapshot` then `accounts_map`, `Unknown`, `0` repeated N times.
- Snapshot-shaped dicts (carrying `captured_at_et`): 87 in all, the top snapshot plus 86 nested ones in one unbroken single-child chain. The first nested snapshot is at depth 5 and the 86th at depth 260; the record's maximum JSON depth is 265 (the sidecar's `max_json_depth`).
- The 87 snapshots' `trading_day` values are 87 distinct dates, 2026-10-22 down to 2026-06-22 (the go-live floor): one snapshot per trading day. The deepest snapshot has no `Unknown` list, only the real account's 11 entries.
- The phantom is the only `Unknown` entry anywhere in the chain.
- Entries `nOyb55RMGVCKPiYXv7TI` and `5XjzXjdGnjh99MIsdM97` are both `triggered: true` at this capture, with `_last_trigger_id` 509 and 505. This is the 2026-10-22 state, a trading day after the `01-c2` shadow rows (see `01-c2/PROVENANCE.md`).

Values above describe the record's shape. No test asserts them; a test derives its expectation from the record or recomputes it.

## Derived records

Written by cycle 1's RED (test-writer). Each is a stored `bot_state` row text, gzipped without a file name or time (`gzip.compress(text, compresslevel=9, mtime=0)`), written as `json.dumps(obj)` of the edited parse, which reproduces the captured bytes exactly when nothing is edited (R1 round-trips). The derivation is re-checked on every run by `tests/database/test_close_snapshot_repair.py::TestCapturedRecordIsUsable::test_derived_records_equal_their_recorded_derivation`, which recomputes each record from R1 and the repository's fleet-alert shape file. The PM's sign-off as producer owner is recorded in each row below (granted to the closer for the rt-01 round); the coverage rows of the scenarios that use these records are no longer held for it.

| File | Case it covers | Source record | Changed fields | Reason | Scenarios | sha256 of the file | PM sign-off |
|---|---|---|---|---|---|---|---|
| `flat_bot_state_row.json.gz` (R2) | a stored state whose close snapshot is flat (the repaired form) | R1 `nested_bot_state_row.json.gz` | removes `last_market_close_snapshot.accounts_map["Unknown"]` (a one-entry list holding the phantom previous snapshot; the only entry under that key); nothing else | the repaired row the repair must produce, byte for byte, and the input of every flat-in case | S-1.2 (expected row), S-1.7, S-1.11, S-1.14, S-1.15, S-1.16 | `9e7ed794a42c153a79ee5e3ff73d93ded40dc6b682440697c9ecdd3bc0e75fb9` | signed off (PM, granted to the closer for this round) Changed field: `ACCOUNT_ID_REDACTED` replaces the account id and its 8-character prefix. |
| `no_snapshot_bot_state_row.json.gz` (R3) | a stored state with no close snapshot | R1 | removes the top-level key `last_market_close_snapshot`; nothing else | the repair must leave such a row untouched | S-1.6 | `6b65c7f3fd454711bf9a944e4c9e899edb570b555804d2a60562980771d72197` | signed off (PM, granted to the closer for this round) Changed field: `ACCOUNT_ID_REDACTED` replaces the account id and its 8-character prefix. |
| `symphony_without_account_bot_state_row.json.gz` (R4) | a real symphony entry with no `account` field | R2 (R1 with the phantom removed, so the snapshot is flat) | removes the `account` key of the first symphony entry in key order, `iaSOOUsmnCJHiZvbrWfs`; nothing else | the builder keeps a symphony with no account, under `Unknown` | S-1.4 | `0df1cc61d5a763f7e9f6ff15924dec8fcafd955f61d1e60c8666b3a2a75e21d8` | signed off (PM, granted to the closer for this round) Changed field: `ACCOUNT_ID_REDACTED` replaces the account id and its 8-character prefix. |
| `dict_without_name_bot_state_row.json.gz` (R5) | a dict-valued top-level key with no `name` | R2 | appends the top-level key `fleet_correlation_alert` with the `example` object of `tests/fixtures/engine/fleet_correlation/alert_state_shape.json` | the builder must not list a non-symphony dict as a symphony | S-1.5 | `665af5d98febfbe8ae8249695db908ae03fb683182b2353402e92365694d9647` | signed off (PM, granted to the closer for this round) Changed field: `ACCOUNT_ID_REDACTED` replaces the account id and its 8-character prefix. |

R4 and R5 are derived from R2, not from R1 as the approach's record list words it: R1-based copies would add two more 275 KB records for no extra case, because every engine case already nests or flattens the same way whatever the input's depth (the nested input is exercised by R1 itself in S-1.1 and S-1.9).

The engine runs these records with `date` set to the close day by the test (the engine's first tick of the day writes it; without it the new-day wipe would run at 16:02). That is test-time input shaping, not a stored record.

## Golden

| File | Case it covers | Source | Producer | Scenarios | sha256 | PM sign-off |
|---|---|---|---|---|---|---|
| `golden_ref_eod_snapshot.json` | what the reference engine's EOD step leaves behind on R1's inputs, apart from the account map's content | R1 with `date` set to the close day `2026-10-23` | the engine at base `3be84137` (blob identical to `37c8e983`'s), `main()` at 2026-10-23 16:02 ET, run through the harness of `tests/engine/test_close_snapshot_builder.py` | S-1.9 | `70fc93613f0e668f087512dd071ee2d5b4d2c8c765d773097672fa313228a3e3` | signed off (PM, granted to the closer for this round) Changed field: `ACCOUNT_ID_REDACTED` replaces the account id and its 8-character prefix. |

The file's `_provenance` object records the input, the extraction and the command. The ref's account map is excluded because it holds the nested chain (S-1.1 recomputes the map from the stored entries). Its `portfolio_strip` is mostly null or zero: the temporary database holds only the close day's shadow rows, so cumulative and max-drawdown have no history; the golden still proves the builder change moves no other field.
