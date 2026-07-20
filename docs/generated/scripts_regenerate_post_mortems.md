# scripts/regenerate_post_mortems

> Historical post-mortem money-math regeneration tool -- repairs `post_mortem_*.json` files whose if-held basis was booked from the clobbered `bot_state.current_return` instead of the authoritative `shadow_history` table.

**Source:** `scripts/regenerate_post_mortems.py`
**Last updated:** 2026-07-20 (fix-f008-data-integrity, `DE-POSTMORTEM-INTEGRITY-001` -- first doc-gen entry for this module; AC-1 default-window widening + a stale-docstring correction, commit `500964ff`)

## Overview

Standalone, import-free CLI tool (deliberately no dependency on the rest of the codebase, so it runs stand-alone on the droplet). Recomputes, per trigger entry, ONLY: `shadow_return` (true if-held at the snapshot cutoff, percent), `saved_pct_guard_alpha` (`exit_return - true_if_held`, percentage points), `saved_dollars` (`symphony_value * saved_pct / 100`), and `summary.positive_guard_alpha_count`. Every other field (`exit_return`, `attempted_trigger_level`, hwm fields, `strategy_params`, holdings, etc.) is preserved byte-for-byte.

**Basis:** the last `shadow_history` row per `(symphony_id, trading_day)` with ET time-of-day `<= SNAPSHOT_CUTOFF_ET` (`"15:54:59"`) -- the same snapshot-time basis `reporting.py`'s Stage-1 post-mortem freeze declares (see `docs/generated/reporting.md`). `SNAPSHOT_CUTOFF_ET` here must equal `reporting.STAGE1_SNAPSHOT_CUTOFF_ET`; an AST drift-guard test enforces the two independently-declared constants stay in sync (the script stays import-free by design, so this can't be a shared import).

## CLI Reference

```
python scripts/regenerate_post_mortems.py                       # dry run, default window
python scripts/regenerate_post_mortems.py --db alphabot_state.db \
    --post-mortems-dir post_mortems --start 2026-06-22 --end 2026-07-09
python scripts/regenerate_post_mortems.py --apply               # gated write
```

**Safety model (operator-gated -- NEVER run against the droplet without an approved deploy + confirmed backup):**
- **DRY RUN is the default:** prints a full per-entry old->new report and writes nothing.
- **`--apply`** first copies the whole post-mortems directory to a timestamped backup sibling, then rewrites only files with changed entries.
- **`--apply` refuses to write if ANY entry in the window cannot be resolved to a `shadow_history` row** -- all-or-nothing; a partial money repair is judged worse than a loud failure.

## AC-1 window fix (`DE-POSTMORTEM-INTEGRITY-001`, F-008, 2026-07-20)

**The bug:** `DEFAULT_START`/`DEFAULT_END` hard-excluded both boundary days (prior default `2026-06-23`..`2026-07-08`), and the module docstring asserted -- wrongly -- that `2026-06-22` "was manually regenerated post-close and already matches truth" and that `2026-07-09` onward "is written correctly by the fixed Stage-1." The F-008 data-integrity audit refuted both claims: the real captured `post_mortem_2026-06-22.json` carries no `if_held_source` on any trigger (pre-dates PR #80's stamp) and is one of the two contaminated days that rides unguarded into the live $-saved aggregate (see `docs/generated/analytics.md`'s `is_valid_post_mortem_entry`).

**The fix:** `DEFAULT_START = "2026-06-22"`, `DEFAULT_END = "2026-07-09"` -- the default window now covers the FULL requested range INCLUSIVE of both boundary days; the window is exactly whatever `--start`/`--end` the caller requests, no silent narrowing. The module docstring's stale "06-22 already matches truth" / "07-09+ written correctly" claims are removed and replaced with a note pointing at the F-008 audit and the real-06-22-capture evidence in `tests/app/test_post_mortem_validity_guard.py`.

**Still out of scope (unchanged by this cycle):** the 2026-07-09 `exit_triggers` rows 80-83 4x-duplicate is a SEPARATE gated data repair, deliberately not addressed by this tool's window change.

**Relationship to the read-time guard:** this script and the `analytics.is_valid_post_mortem_entry` read-time guard (`docs/generated/analytics.md`) are two independent, complementary fixes for the SAME two contaminated days -- this script REPAIRS the on-disk data (an operator-gated `--apply` run against the real droplet files, tracked as a separate operational step, NOT part of this TDD cycle); the read-time guard EXCLUDES unrepaired contaminated data from live aggregates in the meantime and going forward. Repairing the data does not make the guard redundant -- a day repaired by this script is correctly re-stamped with a recognized `if_held_source` and passes the guard normally; the guard remains the durable defense against any FUTURE contamination reaching the operator's screen unnoticed.

## Internal Reference

- `load_name_account_map(db_path) -> dict[(name, account_id), symphony_id]` -- builds the join key from `bot_state`, since `shadow_history.trigger_id` was never populated historically (name+account is the only reliable join).
- `SNAPSHOT_CUTOFF_ET = "15:54:59"` -- see Overview above; AST-guarded against drift from `reporting.STAGE1_SNAPSHOT_CUTOFF_ET`.
- Precision note: the file's `exit_return` is stored rounded to 2dp, so the recomputed `saved_dollars` inherits ~+/-0.005pp of that rounding (~+/-$0.06 on a $1,200 position) -- the audit's own ground-truth recompute carries the same limit.

See `DE-POSTMORTEM-INTEGRITY-001` in `DECISIONS.md`.
