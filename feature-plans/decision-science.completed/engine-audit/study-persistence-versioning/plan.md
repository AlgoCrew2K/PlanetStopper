# Engine Audit — Study Persistence + Versioning

**Feature:** Audit the `optuna_studies.db` study-name discipline, the
`load_if_exists=False` invariant, the `LEGACY__` archive migration
(`optuna_001_archive_accumulated_studies.sql`), and the RDBStorage
timeout. Pin the invariants; surface drift.

**Phase:** Engine audit (post-Phase-1; correctness-discipline hardening)

**Owner agent-type:** `optuna-specialist` (drives), `sqlite-specialist`
(reviews the optimization-DB shape), `quant-test-writer` (RED).

## Source-of-truth references

- `.claude/CLAUDE.md` (project) — agent operating rule 3: "`study_name`
  must include a timestamp and symphony-id (e.g.,
  `symphony_A_20260512T1430`). Never reuse a `study_name` across runs —
  Optuna will append trials to an existing study and corrupt comparisons."
- `autotuner.py:189-196` — `build_port_study_name` and
  `build_symphony_study_name` — the canonical study-name builders.
- `autotuner.py:1008-1009` — `study_timestamp =
  datetime.now(timezone.utc).strftime(...)` and the
  `optuna.create_study(study_name=f"{study_timestamp}__{normalized_name}",
  ..., load_if_exists=False, ...)` call.
- `autotuner.py:805-841` — `_apply_optuna_archive_migration_if_needed`
  + the inline migration logic; the `LEGACY__` prefix invariant.
- `autotuner.py:1003-1007` — RDBStorage construction with
  `connect_args={"timeout": 60}`.
- `migrations/optuna_001_archive_accumulated_studies.sql` — the actual
  migration file.

## Why

The optimization DB (`optuna_studies.db`) is a long-lived artifact. Its
study-name discipline is the foundation of:
- `optuna-compare` skill — diffs two studies by name without re-parsing
  logs;
- the BHY haircut's calibration scope — `c(N)` is calculated over THE
  trials in ONE study, not across concatenated studies;
- audit reproducibility — the `study_name` + `frozen_at`-stamped
  spec_bundles row + Optuna seed gives a deterministic reproduction
  triple.

If a maintainer ever reuses a `study_name` (e.g. "best_symphony_A"),
Optuna's `load_if_exists` default (currently False — explicit per the
project rule) prevents the silent corruption. But the `load_if_exists`
flag is itself load-bearing; if a future PR flips it to `True`
"to resume interrupted studies," the BHY calibration silently breaks
on the second run.

The `LEGACY__` archive migration is a one-time non-destructive rename
of bare legacy study names. It runs on every startup but is idempotent.
The audit verifies it cannot corrupt newly-named studies.

The RDBStorage `timeout=60` is the SQLite-busy timeout under
`n_jobs=-1` parallel writes. Too low → spurious "database is locked"
under parallel trials; too high → debugging stalls.

## Deliverables

### D1 — Pin `load_if_exists=False` invariant

A NEW test:

```
T_load_if_exists_false_pin — read autotuner.py around the create_study
call; assert the kwarg `load_if_exists=False` is present literally.
A PR that omits it (Optuna's default is False, but explicit-is-better)
OR sets it to True fails the test.
```

The rationale comment at the call site is updated to reference the
project rule 3:

```
load_if_exists=False — project rule 3: never reuse a study_name. The
timestamp-prefixed name (study_timestamp + symphony) guarantees
uniqueness in steady state; load_if_exists=False is the structural
guard against an accidental same-millisecond collision OR a future PR
that nukes the timestamp from the name.
```

### D2 — Pin study-name format

A NEW test:

```
T_study_name_format — assert the f-string at autotuner.py:1009 produces
a name matching the regex `^[0-9]{8}T[0-9]{6}[0-9]+Z__[a-z0-9_-]+$`.
Tripwire against a future "let me simplify the timestamp" PR.
```

Cross-check `build_port_study_name` and `build_symphony_study_name`
helpers (`autotuner.py:189-196`) for the same format.

### D3 — `LEGACY__` archive migration idempotency

A NEW integration test:

```
T_archive_migration_idempotent —
1. Seed a temp optimization-DB with one bare-legacy study + one
   already-LEGACY__-prefixed study + one timestamp-prefixed study.
2. Run _apply_optuna_archive_migration_if_needed twice.
3. Assert:
   - the bare-legacy study is now LEGACY__-prefixed;
   - the already-LEGACY__-prefixed study is unchanged;
   - the timestamp-prefixed study is unchanged;
   - running twice produces no new changes (idempotent).
```

### D4 — RDBStorage timeout pin

A NEW test:

```
T_rdbstorage_timeout_pin — assert the timeout kwarg in
RDBStorage(engine_kwargs={"connect_args": {"timeout": <X>}}) has
X >= 60. Tripwire against a "let me drop it to 5 for testing" PR.
```

The named-constant approach:

```python
# RDBStorage SQLite-busy timeout (seconds). Sized for n_jobs=-1
# parallel trials writing to optuna_studies.db; under sustained
# contention, the timeout must exceed the longest write-serialization
# wait. 60s is empirically safe at the project's typical 500-trial /
# 8-core scale. A LOWER value risks spurious "database is locked"
# errors that kill trials mid-evaluation; a HIGHER value hides
# debugging signals.
_RDBSTORAGE_TIMEOUT_SECONDS = 60
```

### D5 — `study_timestamp` micro-second precision

The current f-string is
`datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")` —
microsecond precision. A NEW test asserts the format includes `%f`
(microseconds) so two studies started in the same millisecond do not
collide. Catches a future "let me simplify to seconds" drift.

### D6 — `optuna_studies.db` is NEVER cross-joined with state DB

A NEW test:

```
T_two_db_separation — grep autotuner.py and database.py for SQL
literals containing both 'autotune_runs' (state DB) and 'trials'
(optimization DB) OR 'studies' (optimization DB). Assert zero
matches. Two-DB rule (project CLAUDE.md architecture constraint 3) —
the haircut copies values across DBs, never cross-joins.
```

### D7 — Audit findings record

`findings.md` committed alongside the plan:
- Confirmation that `load_if_exists=False` is set and rationale-
  commented.
- Confirmation of the study-name format and uniqueness invariant.
- LEGACY__ migration current state — count of legacy studies renamed
  vs new-format studies.
- RDBStorage timeout current value and empirical justification.
- Any drift observed since the last audit (if a prior findings.md
  exists).

## Dependencies

- **NOT blocked by** any other plan or migration.
- **Coupled to:** the parallelism/reproducibility audit plan (the
  RDBStorage timeout interacts with `n_jobs=-1`).

## Golden-fixture tests required

### T1 — `load_if_exists=False` pin (D1)

Static-analysis-style on `autotuner.py:1009`.

### T2 — Study-name format pin (D2)

Static-analysis on the f-string + the build_*_study_name helpers.

### T3 — Archive migration idempotency (D3)

Integration test on a temp optimization DB.

### T4 — Timeout pin (D4)

Static-analysis on the RDBStorage construction.

### T5 — Microsecond precision pin (D5)

Static-analysis on the `strftime` format.

### T6 — Two-DB separation (D6)

Static-analysis on autotuner.py + database.py.

### T7 — `study_name` uniqueness under concurrency

Integration: spawn two threads that simultaneously call
`run_autotuner` (or a unit version of the create_study call); assert
they produce different study names AND both succeed. The
microsecond-precision timestamp (T5) is the structural guard; T7
verifies it under stress.

## Definition of Done

1. T1-T7 RED on a clean implementer commit, GREEN after.
2. `pytest tests/autotuner/ tests/database/` PASSES — unchanged
   behaviour.
3. `findings.md` committed.
4. The `_RDBSTORAGE_TIMEOUT_SECONDS` constant is named in
   `autotuner.py` with the source comment.
5. The `load_if_exists=False` and microsecond-precision invariants
   are pinned and visible at PR review.
6. Commit message: `feat(autotuner): study_name=<TS>__<symphony>,
   study persistence + versioning audit — load_if_exists=False pin,
   study-name regex pin, LEGACY__ migration idempotency, RDBStorage
   timeout named constant; n_trials=500; objective=<unchanged>`.

## Risk callouts

- **`load_if_exists=True` smuggle.** A maintainer reasonably argues
  "let me set load_if_exists=True so an interrupted study can
  resume." T1 catches it. Resumption with the BHY haircut creates a
  mixed-N trial set where c(N) is silently wrong.
- **Study-name simplification.** A maintainer drops the microsecond
  precision "because it's ugly." T5 catches it. Microsecond
  precision is load-bearing under parallel autotuner invocations.
- **`LEGACY__` migration corrupting newly-named studies.** The current
  SQL filters by `study_name NOT LIKE 'LEGACY__%' AND INSTR(study_name,
  '__') = 0` — the second clause excludes timestamp-prefixed names
  (which always contain `__`). T3 verifies this with a mixed seed.
- **RDBStorage timeout too low.** Under `n_jobs=-1` on an 8-core
  machine with 500 trials, the autotuner has historically observed
  "database is locked" errors at timeout=5. The 60s value is the
  empirically safe minimum. T4 pins it.
- **Cross-DB join smuggle.** A future analytics PR joins
  `optuna_studies.db.trials` with `state_db.autotune_runs` "for a
  unified report." T6 catches it. The COPY discipline (Phase-1
  spec-bundles-integration plan D4) is the intended pattern.
- **Migration drift.** The migration file
  `optuna_001_archive_accumulated_studies.sql` must be present at the
  expected path. T3's integration test runs the actual migration; if
  the file is missing or its SQL changes, T3 catches it.

## Out of scope

- Adding new migrations to `optuna_studies.db` — out of scope; the
  two-DB rule favors state-DB migrations for the decision-science
  spine.
- Changing `n_trials` — owned by the trial-floor audit plan.
- The sampler / pruner choice — owned by their respective audit
  plans.
- Reproducibility under `n_jobs=-1` — owned by the parallelism/
  reproducibility audit plan.
- The BHY haircut implementation correctness — owned by the
  BHY-implementation audit plan.
