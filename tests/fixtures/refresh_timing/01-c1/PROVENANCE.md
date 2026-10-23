# 01-c1 fixtures: provenance

Plan: server-state-truth, cycle 1. Batch FX-01.

All records here are captured, none derived. They come from the PM's read-only run of `capture.py` with `_cases_01.py` on the droplet, as the service user, against the live `/opt/planetstopper/alphabot_state.db`. The database was opened `mode=ro` (plus `PRAGMA query_only`); nothing was written. The capture ran at deployed SHA `37c8e983` (`origin/main`). The records were copied to `.claude/review/refresh-timing-captures/20261023T005559Z/` in the main checkout (untracked) and committed here byte for byte; the sha256 values below were equal on the droplet and locally, and were re-checked after the copy into this folder.

## Records

| File | Case it covers | Source | Capture moment | Sidecar | sha256 |
|---|---|---|---|---|---|
| `nested_bot_state_row.json.gz` | R1: the stored engine state whose `last_market_close_snapshot` nests earlier snapshots, with the phantom `Unknown` account entry and 12 symphony entries (case `01-c1-bot-state-row`) | table `bot_state`, row key `id = 1`, column `data` (storage class `text`), read as the verbatim bytes and gzipped without a file name or time so the bytes reproduce | `captured_at_utc` 2026-10-23T00:55:19Z. The state's own stamps: `last_successful_cycle_at` 2026-10-22T16:00:01.413198-04:00, top snapshot `trading_day` 2026-10-22, `captured_at_et` `16:00:01 ET` | `nested_bot_state_row.capture.json` | `534b7838bca588e533736bcb77360fc920baf273307854c7ca42998690ba0be7` |

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
- Top level: a dict with 17 keys: 12 symphony entries (each a dict with `name`, every one carrying `account` `880be47e-...`) and the 5 reserved keys `date`, `last_execution_mode`, `last_market_close_snapshot`, `last_successful_cycle_at`, `post_mortem_run`.
- `last_market_close_snapshot` is a snapshot at the top, with keys `accounts_map`, `captured_at_et`, `data_as_of`, `portfolio_strip`, `shadow_divergence`, `trading_day`. The key name `last_market_close_snapshot` occurs once in the whole record (the sidecar's `last_market_close_snapshot_occurrences` is 1): the chain does not repeat that key.
- The nesting sits under `accounts_map`. `accounts_map` holds the real account list (12 entries, keyed `880be47e-efe4-4b44-9d83-b6d86098fe0d`) and an `Unknown` list of exactly one entry. That one entry is the previous snapshot itself, carrying the extra key `id` with the value `last_market_close_snapshot`: the builder treated the reserved key as a symphony. Each nested snapshot repeats the same shape, so the path of the Nth nested snapshot is `last_market_close_snapshot` then `accounts_map`, `Unknown`, `0` repeated N times.
- Snapshot-shaped dicts (carrying `captured_at_et`): 87 in all, the top snapshot plus 86 nested ones in one unbroken single-child chain. The first nested snapshot is at depth 5 and the 86th at depth 260; the record's maximum JSON depth is 265 (the sidecar's `max_json_depth`).
- The 87 snapshots' `trading_day` values are 87 distinct dates, 2026-10-22 down to 2026-06-22 (the go-live floor): one snapshot per trading day. The deepest snapshot has no `Unknown` list, only the real account's 11 entries.
- The phantom is the only `Unknown` entry anywhere in the chain.
- Entries `nOyb55RMGVCKPiYXv7TI` and `5XjzXjdGnjh99MIsdM97` are both `triggered: true` at this capture, with `_last_trigger_id` 509 and 505. This is the 2026-10-22 state, a trading day after the `01-c2` shadow rows (see `01-c2/PROVENANCE.md`).

Values above describe the record's shape. No test asserts them; a test derives its expectation from the record or recomputes it.

## Derived records

None in this folder from this batch. R2 to R5 and the EOD golden are written by cycle 1's RED, each with its own row.
