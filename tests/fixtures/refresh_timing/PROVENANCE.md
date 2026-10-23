# refresh-timing fixtures: provenance index

Every record under this folder is a verbatim capture of a real producer's output, or a derived record that changes only the named fields of one real record. Nothing is typed from memory and no record is a mock built to match a parser. Tests derive their expected values from a record or recompute them; they never assert a value copied from a capture.

## Layout

| Path | What it is |
|---|---|
| `capture.py` | The one shared capture script. Stdlib only, run with `python -I`. Opens a database only as `sqlite3.connect("file:<path>?mode=ro", uri=True)` (plus `PRAGMA query_only`), so it can never write its source. Has the record writer, the read snapshot helper and the HTTP GET recorder (`get --base-url <launcher> --path <path>`: status, `Content-Type`, `Date` and the body verbatim, loopback hosts only). Refuses to overwrite an existing record without `--force`. |
| `_cases_<NN>.py` | One module per plan, discovered beside `capture.py`. Defines `register(helpers)` and subcommands named `<NN>-c<K>-<case>`. A plan's first capturing cycle adds its module; no plan edits `capture.py`. |
| `<NN>-c<K>/` | One folder per cycle holding that cycle's records, each with a row in its own `PROVENANCE.md`. |
| `.gitignore` | `*.sqlite`, `*.db`, `*.db-*`: a database copy dropped here is never staged. |
| `test_capture.py`, `test_state_capture_cases.py`, `conftest.py` | Tests of the tooling itself, on temporary databases built from the repository's migrations. Synthetic rows, used only by these tests. |

## Sources, in order of preference

1. The service user's read-only run of `capture.py` on the droplet against `/opt/planetstopper/alphabot_state.db`, copied to `.claude/review/refresh-timing-captures/<UTC stamp>/` (untracked, outside the scratchpad).
2. The tracked 2026-07-09 captures in `tests/fixtures/prod_droplet/`, reused by path.
3. Launcher GETs recorded by `capture.py get`; the row names the served SHA and the launcher's snapshot time.
4. Producer output generated at test time through the real route or a real `database` writer, never committed.

A database file is never committed, including the launcher's copy of production data.

## Subfolders

| Folder | Plan and cycle | Records | State |
|---|---|---|---|
| `01-c1/` | server-state-truth, cycle 1 | R1 `nested_bot_state_row.json.gz` | pending capture (see below) |
| `01-c2/` | server-state-truth, cycle 2 | R6 `shadow_rows_two_cycles.json`, R7 `exit_trigger_row.json` | pending capture (see below) |

Later cycles add their folder and a row here.

## Record row format (each folder's `PROVENANCE.md`)

One row per record: file, case it covers, source (table and row key, or route and served SHA), capture moment, sidecar file (`<record>.capture.json` holds source table, row key, capture moment, byte length and sha256), and for a derived record the source record, each changed field, the reason, the scenario and the PM's sign-off as producer owner. A record that covers no case is removed.

Records of production state hold account identifiers and symphony names, the same class of content as `tests/fixtures/prod_droplet/`.

## Pending captures

### Server-state-truth (cases in `_cases_01.py`)

Files to copy to the droplet's `/tmp/rt_capture/`: `capture.py` and `_cases_01.py` from this folder on branch `refresh-timing/fixtures`.

As root, once:

```
install -d -m 755 /tmp/rt_capture
# copy capture.py and _cases_01.py into /tmp/rt_capture/
chmod 644 /tmp/rt_capture/capture.py /tmp/rt_capture/_cases_01.py
install -d -o planetstopper -m 755 /tmp/rt_capture/out
```

As the service user, in this order (the exit-trigger case reads the symphony the shadow-rows case chose, from the same `out/`):

```
runuser -u planetstopper -- /opt/planetstopper/.venv/bin/python -I /tmp/rt_capture/capture.py 01-c1-bot-state-row --db /opt/planetstopper/alphabot_state.db --out /tmp/rt_capture/out
runuser -u planetstopper -- /opt/planetstopper/.venv/bin/python -I /tmp/rt_capture/capture.py 01-c2-shadow-rows --db /opt/planetstopper/alphabot_state.db --out /tmp/rt_capture/out
runuser -u planetstopper -- /opt/planetstopper/.venv/bin/python -I /tmp/rt_capture/capture.py 01-c2-exit-trigger --db /opt/planetstopper/alphabot_state.db --out /tmp/rt_capture/out
```

Each command prints the record path and exits 0; a non-zero exit prints `error: ...` and writes nothing. Optional flags on the two `01-c2` cases: `--day YYYY-MM-DD`, `--triggered-id`, `--untriggered-id` (use them only if the default choice, recorded in the sidecar, is wrong for the cycle).

Expected output, all under `/tmp/rt_capture/out/`:

```
01-c1/nested_bot_state_row.json.gz        01-c1/nested_bot_state_row.capture.json
01-c2/shadow_rows_two_cycles.json         01-c2/shadow_rows_two_cycles.capture.json
01-c2/exit_trigger_row.json               01-c2/exit_trigger_row.capture.json
```

Record the hashes, then copy `out/` into the main checkout's `.claude\review\refresh-timing-captures\<UTC stamp>\` and confirm the same hashes there:

```
cd /tmp/rt_capture/out && find . -type f | sort | xargs sha256sum
```

What each record is, for its future row:

| Case | Source table | Row key | Capture moment |
|---|---|---|---|
| `01-c1-bot-state-row` | `bot_state` | `id = 1`, verbatim bytes, gzipped without a name or time so the bytes reproduce | in `01-c1/nested_bot_state_row.capture.json`, `captured_at_utc`; the stored state's own cycle stamp is inside the record |
| `01-c2-shadow-rows` | `shadow_history` | the capture day (newest `trading_day`), the two newest `cycle_id` values by `MAX(ts_utc)`, one triggered and one untriggered symphony, plus each one's newest earlier row | `captured_at_utc` and `newest_shadow_history_ts_utc_at_capture` in `01-c2/shadow_rows_two_cycles.capture.json` |
| `01-c2-exit-trigger` | `exit_triggers` | the triggered symphony's newest row (`ts_utc` then `id` descending) | `captured_at_utc` in `01-c2/exit_trigger_row.capture.json` |
