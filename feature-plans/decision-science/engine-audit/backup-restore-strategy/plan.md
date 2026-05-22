# Plan — Engine-audit: backup/restore strategy

**Feature:** A post-Phase-1 audit + documented backup/restore strategy
covering the **two state DBs** (state DB + optimization DB) and the
**Phase-2 file cache** (path-bank `.npy` files). No backup tooling
ships from this plan; the audit verifies an honest strategy exists
and surfaces any gap.

**Phase:** Engine-audit (post-Phase-1).

**Owner agent-type:** `sqlite-specialist`, cross-reviewed by
`composer-alpaca-integration` (the daemon's runtime constraints) and
`flask-dashboard-specialist` (the read-time constraints).

## Source-of-truth references

- Project CLAUDE.md architecture constraint 3 — two-DB pattern.
- `docs/handoff/decision-science-council-synthesis.md` §3.7 (the
  Phase-1 schema is the defensibility deliverable — a defensibility
  upgrade with no backup story is a half-delivery), §5.4 (Phase-2
  file-cache path bank).
- Charter anti-pattern: "Never run `VACUUM` from app code — it is a
  manual operator action only."
- SQLite docs: WAL mode + the `.backup` command + file-copy-while-live
  hazards.

## Why

The Phase-1 floor adds the spec-bundle / DoF-ledger / advisor-
observations / CVaR-diagnostic spine. These are the auditable
provenance — if they are lost, the defensibility upgrade is undone.
The Phase-2 file cache adds ~40 MB/day of path-bank data — large
enough that the existing "copy `alphabot_state.db`" backup runbook
silently misses it.

This audit makes the backup story honest **before** the user trusts
the defensibility upgrade with money.

## Sub-audits

1. **Current state — what is backed up today?** Read the existing
   operator runbook (or its absence). Enumerate: state DB
   (`alphabot_state.db`), optimization DB (`optuna_studies.db`),
   fixture DBs (`tests/fixtures/...`). Acceptance: a captured "current
   backup story" document; if the runbook is silent, the audit says
   so explicitly.
2. **WAL-mode backup hazard.** Copying a WAL-mode SQLite file
   mid-write captures an inconsistent snapshot (the `.db` plus the
   `.db-wal` plus the `.db-shm` must all be captured atomically, or
   the snapshot is corrupt). The correct command is `sqlite3
   alphabot_state.db ".backup target.db"` or the `sqlite3_backup_*`
   C API. Acceptance: the audited runbook uses one of these, NOT a
   bare `cp alphabot_state.db backup/`.
3. **Optimization-DB backup.** `optuna_studies.db` is separately
   backed up. Optuna study persistence is the autotuner's audit trail;
   losing it loses the `compute_haircut_pvalue` / `compute_sortino_tstat`
   replay history. Acceptance: optimization DB is in the backup story.
4. **Phase-2 file cache pre-declaration.** The `data/path_banks/`
   directory is on the backup list IF Phase-2 unlocks. The audit
   pre-declares this. Acceptance: the post-Phase-2 backup runbook
   draft (committed even though Phase-2 may not unlock) includes the
   directory.
5. **Fixture-DB exclusion.** `tests/fixtures/*.db` files are
   **excluded** from production backup (they are test artifacts;
   restoring a fixture DB over production would corrupt state).
   Acceptance: the backup script's exclusion list names
   `tests/fixtures/`.
6. **Restore-rehearsal test.** A documented rehearsal procedure: take
   a backup, restore to a scratch directory, run `pytest
   tests/audit/test_fixture_schema_parity.py` against the restored
   DB. Acceptance: the procedure exists in the runbook + the
   `tests/audit/` test passes against a restored DB.
7. **VACUUM ban audit.** Confirm no `VACUUM` SQL appears in
   `database.py` or any migration; VACUUM is a manual operator action
   per the charter. Acceptance: grep returns zero matches.

## Deliverables

1. **`docs/runbooks/backup-restore-strategy.md`** — the documented
   strategy (committed). Sections: what is backed up, when, how, where,
   restore procedure, rehearsal cadence.
2. **`tests/audit/test_no_vacuum_in_app_code.py`** — the §7 sub-audit.
3. **`tests/audit/test_fixture_db_exclusion.py`** — verifies the
   `tests/fixtures/` files are not in any production backup config
   (if a backup config script exists; the audit notes its absence
   otherwise).
4. **A captured "current state" report** in
   `docs/handoff/backup-audit-current-state-<date>.md`.
5. **No code changes in `database.py`** from this plan directly.

## Dependencies

- **Hard-depends on Phase-1 floor being applied** (so the audit
  enumerates the full Phase-1 surface).
- **Phase-2 backup pre-declaration is conditional on Phase 2
  unlocking** — but the pre-declaration is authored regardless, so
  the operator inherits a complete plan if Phase 2 ships.

## Golden-fixture tests required (RED before GREEN)

1. **VACUUM ban grep** — zero matches in `database.py` and
   `migrations/`.
2. **Fixture-DB exclusion** — the backup config (if it exists)
   names the fixture path in its exclusion list; if no config
   exists, the test asserts the absence is documented in the
   runbook.
3. **Backup runbook exists** — `docs/runbooks/backup-restore-
   strategy.md` is present and references the two DBs + the
   Phase-2 file cache.
4. **Restore rehearsal documented** — the runbook has a "rehearsal"
   section with a concrete sequence of commands.
5. **`.backup` command, not `cp`** — the runbook's primary backup
   command is `sqlite3 ... ".backup ..."` or
   `sqlite3_backup_init`; a grep on the runbook rejects bare `cp`
   for the live DB file.

## Definition of Done

- All five tests pass GREEN.
- The runbook is committed.
- The current-state report is committed.
- A restore rehearsal has been performed at least once and the
  procedure verified.
- The Phase-2 pre-declaration is in the runbook even if Phase 2 is
  not yet authorised.

## Risk callouts

- **WAL-mode + `cp` is the most-missed defect.** A naive `cp` of
  `alphabot_state.db` mid-write misses the `.db-wal` and `.db-shm`
  side-files. The captured "backup" is corrupt; the restore silently
  fails. The §5 test is the structural enforcement.
- **`optuna_studies.db` is a separate, smaller, but load-bearing
  artifact.** The autotuner reads its history every cycle. A missing
  optimization DB after restore means the haircut starts from
  scratch — every prior trial is lost. The §3 sub-audit prevents
  this.
- **Fixture DBs restored over production is the worst possible
  failure mode.** A test-shape `alphabot_state.db` with zero
  positions overwrites a live production DB — the engine boots clean
  and trades the wrong size. The §5 exclusion test + the
  §2 sub-audit's runbook layer are both required.
- **A restore-rehearsal is the single most operator-impactful test.**
  An untested backup is a backup that fails when needed. The §4 +
  §6 sub-audits drive an actual rehearsal once.
- **The audit reads; it does not write.** Defects surfaced get their
  own remediation plans. The audit's own deliverable is the runbook
  + the captured current state, not the backup tooling itself.

## Out of scope

- The backup tooling implementation (cron, systemd timer, cloud
  storage choice — all operator decisions).
- The optimization DB schema (separate; its backup story is
  identical to the state DB's because it is also WAL-mode SQLite).
- Phase-2 file-cache pruning (separate Phase-2 plan; the pruning
  is a write-side concern, not a backup concern).
- Replay-determinism (separate audit plan).
