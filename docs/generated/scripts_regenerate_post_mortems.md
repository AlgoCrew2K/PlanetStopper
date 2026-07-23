# scripts/regenerate_post_mortems

> Historical post-mortem money-math regeneration tool -- repairs `post_mortem_*.json` files whose if-held basis was booked from the clobbered `bot_state.current_return` instead of the authoritative `shadow_history` table.

**Source:** `scripts/regenerate_post_mortems.py`
**Last updated:** 2026-07-20 (fix-f008-regen-stamp, `DE-F008-REGEN-STAMP-001` -- stamp-on-verify fix, commit `dbf70932`). Prior: 2026-07-20 (fix-f008-data-integrity, `DE-POSTMORTEM-INTEGRITY-001` -- first doc-gen entry for this module; AC-1 default-window widening + a stale-docstring correction, commit `500964ff`)

## Overview

Standalone CLI tool, deliberately minimal-dependency so it runs stand-alone on the droplet -- stdlib-only plus one lightweight repo import as of the `DE-F008-REGEN-STAMP-001` cycle (`analytics`, for the shared provenance-trust check; no Flask, no network, no eager DB connection at import time). Recomputes, per trigger entry, ONLY: `shadow_return` (true if-held at the snapshot cutoff, percent), `saved_pct_guard_alpha` (`exit_return - true_if_held`, percentage points), `saved_dollars` (`symphony_value * saved_pct / 100`), and `summary.positive_guard_alpha_count`. Every other field (`exit_return`, `attempted_trigger_level`, hwm fields, `strategy_params`, holdings, etc.) is preserved byte-for-byte. As of `DE-F008-REGEN-STAMP-001`, the `if_held_source` provenance stamp can also be repaired independently of the value fields -- see that section below.

**Basis:** the last `shadow_history` row per `(symphony_id, trading_day)` with ET time-of-day `<= SNAPSHOT_CUTOFF_ET` (`"15:54:59"`) -- the same snapshot-time basis `reporting.py`'s Stage-1 post-mortem freeze declares (see `docs/generated/reporting.md`). `SNAPSHOT_CUTOFF_ET` here must equal `reporting.STAGE1_SNAPSHOT_CUTOFF_ET`; an AST drift-guard test enforces the two independently-declared constants stay in sync -- `reporting.py` itself is not imported here (a much heavier module, Discord/webhook code included), unaffected by the newer, lightweight `analytics` import described in the Stamp-on-verify section below.

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

## Stamp-on-verify fix (`DE-F008-REGEN-STAMP-001`, F-008 completion, 2026-07-20)

**The gap:** the per-entry repair loop (`regenerate_file`) stamped `if_held_source = "shadow_history"` ONLY inside the `if old != new:` value-change branch. An entry the script RESOLVED against `shadow_history` and confirmed byte-equal to the stored values was left completely untouched -- including its `if_held_source` field -- so the AC-1 window fix above could resolve and verify an entry as correct without that verification ever landing durably on disk. The `DE-POSTMORTEM-INTEGRITY-001` read-time guard (`analytics.is_valid_post_mortem_entry`) then permanently excluded that verified-correct entry from every live consumer. Production evidence: the 2026-07-20 droplet repair run of `post_mortem_2026-06-22.json` left 1 of 11 entries unstamped despite successful verification -- "(INVEST) LQD + EYEG 5 ways Full Market", $+28.72, silently missing from the live $-saved headline.

**The fix:** `regenerate_file` gains an `elif not analytics.is_valid_post_mortem_entry(entry):` branch alongside the value-change branch -- reachable only for already-resolved entries where `old == new`. It stamps `if_held_source = "shadow_history"` and records a `stamp_only: True` change, reusing the existing `changes`-non-empty rewrite gate so the file is actually rewritten (the gap the production miss above came from). An entry already carrying a trusted stamp (`shadow_history`, `shadow_history_post_cutoff`, `bot_state_fallback`) with equal values hits neither branch -- no re-stamp, no rewrite churn. The CLI report distinguishes stamp-only lines ("verified correct, stamped provenance") from the value-change old->new format, since `old == new` printed in arrow form would read as a no-op and hide that a repair happened.

**New import:** the script now imports `analytics` (`analytics.is_valid_post_mortem_entry`) -- the single source of truth for the trusted-stamp set, chosen over mirroring a second frozenset that could drift from the read-time guard it exists to satisfy. See the Overview's import note above.

**Idempotent:** a second run after a stamp-only `--apply` finds the entry now carries a trusted stamp and `old == new` -- neither branch fires, zero changes, nothing rewritten.

## Internal Reference

- `load_name_account_map(db_path) -> dict[(name, account_id), symphony_id]` -- builds the join key from `bot_state`, since `shadow_history.trigger_id` was never populated historically (name+account is the only reliable join).
- `SNAPSHOT_CUTOFF_ET = "15:54:59"` -- see Overview above; AST-guarded against drift from `reporting.STAGE1_SNAPSHOT_CUTOFF_ET`.
- Precision note: the file's `exit_return` is stored rounded to 2dp, so the recomputed `saved_dollars` inherits ~+/-0.005pp of that rounding (~+/-$0.06 on a $1,200 position) -- the audit's own ground-truth recompute carries the same limit.

See `DE-POSTMORTEM-INTEGRITY-001` and `DE-F008-REGEN-STAMP-001` in `DECISIONS.md`.
