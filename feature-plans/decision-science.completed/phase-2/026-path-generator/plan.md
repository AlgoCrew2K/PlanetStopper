# Plan — Migration 026_path_generator.sql + file-cache path bank (DEFERRED — Phase 2)

**Feature:** Phase-2 deferred — `path_generator_calibrations` and
`path_bank_manifest` state-DB tables, plus the disk file-cache path
bank that holds the actual ~40 MB/day of simulated paths. **Ships only
if the Phase-2 entry gates pass.**

**Phase:** Phase 2 (Finalist B; evidence-gated).

**Owner agent-type:** `sqlite-specialist` (the schema + the manifest
write/read), `quant-test-writer` (RED on the file-cache integrity +
the manifest contract), `quant-code-reviewer`. File-cache pruning lives
with the writer's owner (`risk-engine-specialist`).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §5.3 (Phase-2
  design — block-bootstrap generator; pre-open batch + per-minute
  reduction; the `tier1_seed` discipline), §5.4 (Phase-2 persistence —
  `017_path_generator.sql` council-numbered), §5.7 (Phase-2 entry
  gates).
- `docs/handoff/council-converged-migration-plan.md` §3.2 row 017, §6
  H7 (`run_monte_carlo` blast radius), §8 (count-plus-weight framing
  — Phase-2 schema is heavy runtime-state, not light additive).
- Precedent: `synthetic_history.py` file cache (the council §3.2 row
  017 explicit reference: "matches the `synthetic_history.py`
  file-cache precedent").

## Why

The Phase-2 forward-path generator pre-simulates a regime-conditioned
bank of price paths once per trading day (Tier-1 batch, pre-open). The
per-minute execution path reduces the bank with a light array
operation. Two persistence questions arise:

1. **Where does the ~40 MB/day of path floats live?** **Not in the
   state DB.** A 40 MB blob in a WAL DB bloats every backup, every
   replication, every dashboard read. Council §3.2 row 017 binding: a
   **file cache** (`.npy` or similar), the `synthetic_history.py`
   precedent.
2. **What does the state DB hold?** Two metadata tables:
   `path_generator_calibrations` (the calibration params) and
   `path_bank_manifest` (the bank's location + integrity hash + the
   load-bearing replay seed). Both rows are ~200 bytes; both are
   queryable; both are part of the Gate-1 replay contract.

The combination is the cheapest reproducible architecture: the heavy
data on disk; the auditable identifiers in the state DB.

## Numbering

Council `017_path_generator.sql` → codebase `026_path_generator.sql`
(see Phase-2 renumbering note in `025-shadow-decisions`).

## Deliverables

1. **`migrations/026_path_generator.sql`** — `CREATE TABLE IF NOT
   EXISTS` for two tables:
   - `path_generator_calibrations`:
     - `id                  INTEGER PRIMARY KEY AUTOINCREMENT`
     - `created_at          TEXT NOT NULL DEFAULT (datetime('now'))`
     - `calibration_params  TEXT NOT NULL`  (JSON — generator-
       agnostic blob; absorbs block bootstrap, GARCH-FHS, or any
       Phase-2-design-time choice; council §3.2 row 017 explicit)
     - `history_fingerprint TEXT NOT NULL`  (content-hash of the
       historical fold the calibration was derived from — replay
       anchor #2)
     - `n_tail_observations INTEGER NOT NULL`  (the count of
       sub-5% tail-days the calibration saw; council §4 gate-zero
       data audit input)
     - `superseded_at_utc   TEXT DEFAULT NULL`  (NULL = currently
       live; non-NULL = a newer calibration superseded this one;
       the immutability convention)
   - `path_bank_manifest`:
     - `id                  INTEGER PRIMARY KEY AUTOINCREMENT`
     - `created_at          TEXT NOT NULL DEFAULT (datetime('now'))`
     - `regime_fingerprint  TEXT NOT NULL`  (the regime-bucket
       fingerprint the bank was built under — replay anchor #3)
     - `bank_file_path      TEXT NOT NULL`  (the disk path; the
       file lives outside the state DB)
     - `bank_sha256         TEXT NOT NULL`  (content hash of the
       bank file — integrity verification at read time)
     - `tier1_seed          TEXT NOT NULL`  (SHA-256 of
       `symphony_id ‖ trading_day ‖ spec_bundle_hash`; council §5.3
       binding — load-bearing for Gate 1 — "without it, replay
       parity cannot pass")
     - `built_at_utc        TEXT NOT NULL DEFAULT (datetime('now'))`
     - `superseded_at_utc   TEXT DEFAULT NULL`
     - `calibration_id      INTEGER`  (soft FK to
       `path_generator_calibrations.id`)
2. **Indexes:**
   - `CREATE INDEX IF NOT EXISTS idx_calib_live ON
     path_generator_calibrations(superseded_at_utc)
     WHERE superseded_at_utc IS NULL;`  -- accelerates "which
     calibration is live now" (partial index, codebase's first; the
     index is conventional and SQLite supports partial indexes)
   - `CREATE INDEX IF NOT EXISTS idx_manifest_live ON
     path_bank_manifest(superseded_at_utc)
     WHERE superseded_at_utc IS NULL;`  -- accelerates the live-bank
     lookup
3. **`_MIGRATION_FILES`** — append `"026_path_generator.sql"`.
4. **File-cache contract** (in the writer's plan, restated here so a
   reviewer reads one shape):
   - Path: `data/path_banks/<bank_sha256>.npy` (the codebase's
     existing `data/` convention; verify at implementation).
   - The bank file is **write-once**: a new bank gets a new
     `bank_sha256`, a new file, a new manifest row. Old banks are
     pruned by a scheduled job (not by app code at read time).
   - The integrity check at read time: re-compute the file's SHA-256
     and assert it matches the manifest row. A mismatch is a hard
     fail, never a silent fallback.
5. **No `init_db()` mirror** (both tables are new; H1 zero exposure).
6. **Fixture refresh** — seed two calibration rows (one current, one
   superseded) and two manifest rows (one current, one superseded),
   each pointing at a tiny test bank file under
   `tests/fixtures/path_banks/`. The schema-validator test asserts the
   fingerprints + the integrity hash check.

## Dependencies

- **Hard-depends on Phase-1 spine** — the file-cache discipline
  references `spec_bundle_hash` (`016_spec_bundles`) for the
  `tier1_seed` derivation.
- **Phase-2 entry gates must pass** (council §5.7 — gate fails → stop).
- **Soft-coupled to `025_shadow_decisions.sql`** — `shadow_decisions.
  generator_calib_id` and `path_bank_manifest_id` reference these
  tables.

## Golden-fixture tests required (RED before GREEN)

1. **`tier1_seed` is reproducible from `(symphony_id, trading_day,
   spec_bundle_hash)`** — a unit test on the seed derivation function
   asserts a known input yields a known output. Bit-identical SHA-256
   discipline.
2. **The integrity hash catches a corrupted bank file** — a test
   that mutates one byte of the fixture bank file then attempts to
   read via the manifest; the read raises a clear "bank integrity
   check failed" error.
3. **`superseded_at_utc IS NULL` returns exactly one row per
   calibration family** — the live-lookup query returns one row
   even when the table has two rows from yesterday + today
   (yesterday's `superseded_at_utc` is non-NULL after the
   supersession write).
4. **A new manifest row is never an UPDATE** — the
   `path_bank_manifest` accessor surface has no `update_*` symbol;
   supersession is "write new row + UPDATE the old row's
   `superseded_at_utc`." The grep test rejects an
   `UPDATE bank_sha256` SQL form.
5. **`bank_file_path` is OUTSIDE the state DB** — the path string
   does not contain `alphabot_state.db` (a defensive check against
   accidentally storing the bank as a BLOB in the state DB).
6. **The bank file does NOT live in `tests/fixtures/state_db/...`**
   — a path-validation test asserts the file lives under a
   `path_banks/` directory.
7. **Schema-validator test** — fixture DB has both tables + both
   indexes + all columns.

## Definition of Done

- Migration applies cleanly; fixture DBs rebuilt with two calibration
  rows + two manifest rows + two test bank files.
- All seven tests pass GREEN.
- `pytest tests/` full tree passes.
- The bank-file pruning is a **scheduled job** (a CLI / cron / engine
  daily task), NOT a read-time side-effect (deletion at read is the
  exact failure the council §3.2 row 017 warned against — bank file
  disappearing mid-replay).
- The H6 binding (legacy-drop is human-operator-authorized) carries
  over here: bank-file deletion via the prune job requires an explicit
  CLI flag, never silent in the daemon's startup.

## Risk callouts

- **The 40 MB/day is a WAL-DB anti-pattern.** Council §3.2 row 017
  explicit: "**The pre-simulated path bank itself is a FILE CACHE
  (`.npy`), NOT a state-DB blob**." The §5 path-validation test is the
  structural enforcement.
- **`tier1_seed` is THE load-bearing Phase-2 replay anchor.** Without
  it, Gate-1 cannot pass — a re-simulated bank from an undeterministic
  seed is a different bank. The `NOT NULL` constraint + the §1 test
  guard.
- **Two-DB clean.** Both tables are in the state DB. The autotuner
  (`autotuner.py`) already reads the state DB; no cross-DB join is
  introduced (project constraint 3).
- **The block-length selector (Politis-White, NN1-compliant —
  council §5.3) is part of `calibration_params`.** That choice is
  evidence-source `STYLIZED_FACT` / `CALIBRATION`, never
  `BACKTEST_SELECTION` (NN1). The DoF ledger captures the choice as
  a `FIXED`/`STYLIZED_FACT` row; this migration just stores the
  resulting parameters.
- **Pre-open batch latency budget is BLOCKING** (council §5.7 entry
  gate). Without a measured prototype proving the batch finishes
  with margin before the first `:00` cycle, this migration does not
  ship. The persistence layer cannot fix a latency miss; the gate
  fails → stop.

## Out of scope

- The block-bootstrap / GARCH-FHS choice itself (Phase-2 design;
  risk-engine-specialist + tuning-architect domain).
- The pre-open batch scheduler (Phase-2 plumbing; risk-engine-
  specialist + composer-alpaca-integration).
- The bank-file pruning job (separate Phase-2 plumbing plan;
  scheduled, human-CLI-invoked).
- `shadow_decisions.generator_calib_id` and
  `path_bank_manifest_id` (plan `025-shadow-decisions`).
- `decision_core_state.hysteresis_snapshot_json` (plan
  `027-decision-core-state`).
