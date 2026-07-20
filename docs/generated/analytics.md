# analytics

> Performance/History tab data layer -- loads per-day `post_mortem_<YYYY-MM-DD>.json` snapshots and exposes aggregate/per-symphony return series plus quantstats-derived risk metrics.

**Source:** `analytics.py`
**Last updated:** 2026-07-20 (fix-f008-data-integrity, `DE-POSTMORTEM-INTEGRITY-001` -- first doc-gen entry for this module; round-2 review hardening at `2eac42d0`)

**Coverage note (honest scope):** `analytics.py` is a large module (quantstats risk metrics, windowed strip/history aggregation, portfolio return series) with no prior `docs/generated/` entry. This file documents only the post-mortem-reading surface this cycle touched or depends on -- `_POST_MORTEMS_DIR`, `is_valid_post_mortem_entry`, `load_post_mortem_history`, and `get_history_summary`. The remainder of the module's public API (quantstats metrics, windowed-strip computation, portfolio return-series helpers, etc.) is a pre-existing documentation gap, flagged to the PM as backlog -- not silently backfilled here, to keep this entry scoped to what was actually verified against the GREEN diff.

## Overview

`analytics.py` loads post-mortem snapshots written by `reporting.py:generate_eod_snapshot` and exposes aggregate / per-symphony return series plus quantstats-derived risk metrics for the Performance and History dashboard tabs. Every post-mortem trigger entry carries a producer-mapped field set (see `docs/generated/reporting.md`'s Stage-1 output keys) plus, since `DE-GUARD-ALPHA-SAVED-001` (PR #80), an `if_held_source` provenance stamp.

## API Reference

### `is_valid_post_mortem_entry(entry: object) -> bool`

**New in this cycle (F-008, `DE-POSTMORTEM-INTEGRITY-001`).** Single source of truth for the post-mortem data-integrity guard -- returns `True` iff `entry` is a `dict` and its `if_held_source` field is a `str` matching one of the 3 producer-recognized values:

```python
_TRUSTED_IF_HELD_SOURCES = frozenset(
    {"shadow_history", "shadow_history_post_cutoff", "bot_state_fallback"}
)
```

These are exactly the 3 tiers `reporting.py:generate_eod_snapshot`'s Stage-1 if-held lookup declares (see `docs/generated/reporting.md`). An entry that is not a `dict`, or whose `if_held_source` is missing, non-string, or not one of these 3 values, is invalid. Never raises.

**Round-2 review hardening (AC-4, `2eac42d0`):** the initial implementation checked `entry.get("if_held_source") in _TRUSTED_IF_HELD_SOURCES` directly -- safe for the expected string case, but a `list`/`dict` value (a corrupted-but-syntactically-valid post-mortem file stamping the wrong type) would raise `TypeError` on the frozenset membership check (unhashable type), crashing every caller instead of degrading to "invalid." Fixed with an explicit `isinstance(value, str)` guard before the membership test, closing a blast-radius gap the reviewer found.

**Why this exists:** two historical post-mortem days -- the real captured `post_mortem_2026-06-22.json` and a 2026-07-09-style day -- predate the `if_held_source` field entirely (it did not exist before PR #80) and carry wrong/sign-flipped `saved_dollars`. Before this cycle, every reader of `post_mortem_*.json` summed `saved_dollars` unconditionally, so these two contaminated days rode silently into the operator's $-saved headline, History tab, and Performance tab.

**Callers (all 3 live post-mortem-reading consumers route through this one function so they cannot diverge):**
1. `app.guard_alpha_summary()` (`app.py:2690`) -- the `$`-saved headline aggregate. See `docs/generated/app.md`'s `GET /api/guard-alpha-summary` section.
2. `analytics.load_post_mortem_history` (below).
3. `analytics.get_history_summary` (below).

**Honest scope note:** this is a PROVENANCE guard, not a value-recompute. A hypothetical future entry stamped with a RECOGNIZED `if_held_source` but still numerically wrong would NOT be caught -- no such case exists in the fixtures verified for this cycle (both known-contaminated days are unstamped, caught by "missing" alone). See `DE-POSTMORTEM-INTEGRITY-001` in `DECISIONS.md` for the full ruling, including why the trusted set is 3 values rather than just `"shadow_history"`.

---

### `load_post_mortem_history(days: int = 60, base_dir: str = ".") -> dict`

Loads up to `days` most-recent `post_mortem_<YYYY-MM-DD>.json` files from `base_dir` and returns the DV1 internal shape: `{date_str: {symphony_id: {"live_ret", "f_ret", "value", ...trigger}}}`, applying the producer-field mapping (`symphony_name`->key, `symphony_value`->`value`, `shadow_return`->`live_ret`, `exit_return`->`f_ret`; all other trigger fields carried forward verbatim).

**F-008 validity guard (AC-5):** each trigger entry is passed through `is_valid_post_mortem_entry` before being folded into the returned shape -- an entry lacking a recognized `if_held_source` is skipped, the same as a `None`/non-dict trigger.

**Resilience (unchanged by this cycle):**
- Missing/unreadable files are silently skipped.
- Malformed JSON is silently skipped (does not raise).
- Files without a parseable date in the filename are skipped.
- When more than `days` files exist, the `days` most-recent (by the date embedded in the filename) are kept.

---

### `get_history_summary(days: int = 30, base_dir: str = ".") -> dict`

Aggregates guard-alpha history for the History tab. Returns the envelope `GET /api/history/<int:days>` (`app.py:3095` `get_history()`) emits directly: `total_alpha`, `total_saved`, `trigger_count`, `wins`, `by_reason` (per-exit-reason `{alpha, count, wins, dollars}`), `avg_guard_alpha`, `win_rate`, `daily_alpha` (chronological list), plus `todays_exits` (today's post-mortem file, or an `exit_triggers` intraday fallback -- see `docs/generated/app.md`'s `GET /api/history/<int:days>` section for that fallback's semantics).

**F-008 validity guard (AC-5b, added at plan-approval 2026-07-20 after `get_history_summary` was found to be a THIRD independent unguarded post-mortem consumer, not originally scoped in the plan's first draft):** each trigger entry within the `[start_date, end_date]` window is passed through `is_valid_post_mortem_entry` -- an invalid entry contributes to neither `total_alpha`/`total_saved`/`trigger_count`/`wins`/`by_reason`/`daily_alpha`. Without this, AC-5's stated goal (History/Performance don't serve contaminated days) would have been defeated by this route's own headline-stats producer.

**Not touched by this guard:** the `todays_exits` intraday fallback (`exit_triggers`/`shadow_history` live query, not a post-mortem file read) -- the `if_held_source` provenance stamp only exists on post-mortem JSON, so it doesn't apply there.

## Internal Dependencies

- `database` -- consulted by other (undocumented-in-this-entry) parts of this module for live state.
- `reporting.py` -- upstream producer whose `generate_eod_snapshot` trigger schema this module consumes; `is_valid_post_mortem_entry`'s trusted set is a direct mirror of `reporting.py`'s 3-tier `if_held_source` lookup (see `docs/generated/reporting.md`).

See `DE-POSTMORTEM-INTEGRITY-001` in `DECISIONS.md`.
