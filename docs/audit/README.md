# Planet Stopper — Audit Reports Index

Cross-cycle audit reports are commissioned at the close of each sprint. Each report covers the full delta since the prior sprint tip, auditing code correctness, architectural drift, type-design, naming hygiene, test quality, provenance gaps, and documentation drift.

## Reports

| Report | Audited range | Run date | Findings | Status |
|--------|--------------|----------|----------|--------|
| [Sprint 1 Cross-Cycle Audit](sprint-1-cross-cycle-audit.md) | `0735b61..3e0b83a` | 2026-05-25 | 2 CRITICAL / 5 HIGH / 5 MEDIUM / 3 LOW (15 total) | All CRITICAL/HIGH closed by Sprint 2 fix-pass |
| [Sprint 2 Cross-Cycle Audit](sprint-2-cross-cycle-audit.md) | `d2328ca..8819867` | 2026-05-26 | 2 CRITICAL / 3 HIGH / 4 MEDIUM / 3 LOW (12 total) | All CRITICAL/HIGH closed at `4cf7be3` (Sprint 2 fix-pass) |

## Sprint 1 Audit — Top findings and resolutions

| Finding | Severity | Resolution |
|---------|----------|------------|
| CC-001: `advisor_observations` table missing from migrations | CRITICAL | Closed: migration 017 added to `_MIGRATION_FILES` (cycle 017) |
| CC-002: SQL injection in `write_telemetry_row` | CRITICAL | Closed: table+column injection guards (merge `bea62d1`) |
| CC-003: `_DISMISS_EXECUTOR` missing `atexit` shutdown | HIGH | Closed: `atexit.register` added (merge `2f4c964`) |
| CC-005: `flush_resync` silent breakage | MEDIUM (treated CRITICAL by PM) | Closed: background-dispatch via `_DISMISS_EXECUTOR` (merge `e146051`) |

## Sprint 2 Audit — Top findings and resolutions

| Finding | Severity | Resolution |
|---------|----------|------------|
| CRRA-001: `_haircut_select` feeds raw returns not CRRA utility | CRITICAL | Closed: U-transform applied in `_haircut_select` (merge `836e0ed`) |
| NEFF-001: `compute_n_effective` never called in production | CRITICAL | Closed: wired into both `_haircut_select` call sites (merge `836e0ed`) |
| ARCH-001: `save_autotune_run` missing 9 EUT audit columns | HIGH | Closed: extended with 9 EUT columns (merge `836e0ed`) |
| CC-NEW-001: Intra-process flush race in `_FLUSH_STATE_LOCK` | HIGH | Closed: lock serializes load+modify+save (merge `37c39cc`) |
| CVAR-001: CVaR branch reachable without Phase-1 scope guard | MEDIUM | Closed: scope-limit comment added (merge `4cf7be3`) |
| ARCH-002: `_MIGRATION_FILES` 021/020 order undocumented | MEDIUM | Closed: inline comment explaining intentional order (merge `4cf7be3`) |
| PROV-001: Incorrect migration numbers in handoff doc | LOW | Closed: corrected in `council-converged-migration-plan.md` (merge `4cf7be3`) |

## Relationship to feature-plans

Audit reports are read-only diagnostic artifacts. Findings feed remediation tasks back into the sprint cycle. No code ships from an audit report directly — each remediation is a separate tracked cycle or hotfix merge.
