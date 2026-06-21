# CLAUDE.md Gotcha Draft — for PM approval before touching the real CLAUDE.md

Proposed addition to the Known Gotchas table in `CLAUDE.md`:

| Issue | Fix |
|-------|-----|
| `tests/advisors/` single-process peak RSS is ~7 GB (heavy-lib footprint: quantstats/Optuna/anthropic accumulate across files in one process) | CI and prod are unaffected — xdist bounds per-worker to ~270 MB. Single-process full-tree verification of `tests/advisors/` requires a host with >8 GB available RAM or must run that subset under xdist. `importlib.reload` was removed (commit 470de98, behavior-preserving — patch-visibility maintained via module-attribute patching) but was NOT the dominant driver; residual footprint is multi-cause heavy-lib, tracked LOW PRIORITY. |

## Notes for PM

- This is a new row, not a replacement of any existing row.
- The wording explicitly states the fix is behavior-preserving (no tests weakened/skipped) so
  future readers do not think coverage was dropped.
- The original "OOM hypothesis falsified" finding is recorded in DE-RELOAD-001 in DECISIONS.md
  and in `feature-plans/test-reload-leak-remediation.md`. The gotcha row intentionally omits
  the detailed falsification narrative (too long for a table); it points to the constraint and
  the fix.
- Suggest inserting this row after the "Blast-radius scanners sweeping `.claude/worktrees/`"
  row, since both are test-infrastructure concerns.
