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
| `01-c1/` | server-state-truth, cycle 1 | R1 `nested_bot_state_row.json.gz` | captured 2026-10-23T00:55Z at deployed SHA `37c8e983`, committed; see `01-c1/PROVENANCE.md` |
| `01-c2/` | server-state-truth, cycle 2 | R6 `shadow_rows_two_cycles.json`, R7 `exit_trigger_row.json` | captured 2026-10-23T00:55Z at deployed SHA `37c8e983`, committed; see `01-c2/PROVENANCE.md` (R6 is 2026-10-21, R7 is exit id 509 of 2026-10-22) |

Later cycles add their folder and a row here.

## Record row format (each folder's `PROVENANCE.md`)

One row per record: file, case it covers, source (table and row key, or route and served SHA), capture moment, sidecar file (`<record>.capture.json` holds source table, row key, capture moment, byte length and sha256), and for a derived record the source record, each changed field, the reason, the scenario and the PM's sign-off as producer owner. A record that covers no case is removed.

Records of production state hold account identifiers and symphony names, the same class of content as `tests/fixtures/prod_droplet/`.

## Pending captures

None required by the plans as written. Optional, only if a scenario is added that needs it:

- `01-c2`: the exit row R6's triggered symphony points at (`exit_triggers` id 498, 2026-10-21T15:02:07 ET, symphony `nOyb55RMGVCKPiYXv7TI`), by a new case flag on the `01-c2-exit-trigger` case. See `01-c2/PROVENANCE.md`, section "Shape of R7, and the day judgement".

The droplet run commands of the committed captures are in each folder's `PROVENANCE.md`.
