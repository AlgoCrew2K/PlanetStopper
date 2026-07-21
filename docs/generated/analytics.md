# analytics

> Performance/History tab data layer -- loads per-day `post_mortem_<YYYY-MM-DD>.json` snapshots and exposes aggregate/per-symphony return series plus quantstats-derived risk metrics.

**Source:** `analytics.py`
**Last updated:** 2026-07-21 (fix-display-cluster, `DE-DISPLAY-TRUTH-001` F-018 -- `compute_windowed_portfolio_strip` documented for the first time; see the new section below). Prior: 2026-07-20 (fix-f008-data-integrity, `DE-POSTMORTEM-INTEGRITY-001` -- first doc-gen entry for this module; round-2 review hardening at `2eac42d0`)

**Coverage note (honest scope):** `analytics.py` is a large module (quantstats risk metrics, windowed strip/history aggregation, portfolio return series) with no exhaustive `docs/generated/` entry. This file documents the post-mortem-reading surface (`_POST_MORTEMS_DIR`, `is_valid_post_mortem_entry`, `load_post_mortem_history`, `get_history_summary`) plus, as of this cycle, `compute_windowed_portfolio_strip` -- the functions successive cycles have actually touched or depended on. The remainder of the module's public API (quantstats metrics, portfolio return-series helpers, etc.) is a pre-existing documentation gap, flagged to the PM as backlog -- not silently backfilled here, to keep this entry scoped to what was actually verified against each cycle's GREEN diff.

## Overview

`analytics.py` loads post-mortem snapshots written by `reporting.py:generate_eod_snapshot` and exposes aggregate / per-symphony return series plus quantstats-derived risk metrics for the Performance and History dashboard tabs. Every post-mortem trigger entry carries a producer-mapped field set (see `docs/generated/reporting.md`'s Stage-1 output keys) plus, since `DE-GUARD-ALPHA-SAVED-001` (PR #80), an `if_held_source` provenance stamp.

## API Reference

### `compute_windowed_portfolio_strip(symphonies: list, bot_state: dict, *, window: str, db_path: str | None = None) -> dict`

Computes the windowed portfolio strip (the hero guard-alpha headline's data source) for a given time window token (`30d`/`60d`/`90d`/`125d`/`ytd`/`1y`/`all`), including `guard_alpha` -- the value-weighted `windowed_alpha` across the portfolio's symphonies.

**F-018 basis-consistency fix (`DE-DISPLAY-TRUTH-001`, 2026-07-21).** The `windowed_alpha` loop now excludes the TWR-fallback symphony (`sym.get("simple_return") == 0.0 and sym.get("net_deposits") == 0.0` -- the identical condition `get_symphony_cumulative_return` checks at `analytics.py:826` to set `_twr_fallback`), matching the exclusion its own `if_held` anchor already applies via `get_portfolio_cumulative_return` -> `_value_weighted_portfolio` (the shipped F4 fix, `analytics.py:972-978`).

**Why this matters:** before this fix, `windowed_alpha` was value-weighted across *all* symphonies (including the TWR-fallback one) while the `if_held` anchor it is compared against excluded that same symphony -- two different weighting populations feeding one ratio. Net effect: the windowed guard-alpha headline (the tool's core value metric, e.g. "GUARD ALPHA 30D") systematically **under-reported** roughly 2x on every window. Verified on the golden fixture `tests/fixtures/math/guard_alpha_windowed_basis_mix.json` (`tests/analytics/test_windowed_strip.py::TestF018WindowedAlphaBasisConsistency`, expected values derived in-test from raw `shadow_history` rows, never hardcoded) and independently corroborated against a real live-DB snapshot recompute (`docs/audit/confidence-program/truth/f014-lifetime-label-basis-mismatch.md`): **0.627195 (old, mixed-basis) vs 1.241529 (fixed, 10-symphony-consistent basis)**.

This is a **display-truth fix, not a math-layer redesign**: the exclusion condition is inlined with a comment (not extracted to a new named constant) because it mirrors an existing shipped exclusion at `analytics.py:826` rather than introducing a new tunable threshold. See `DE-DISPLAY-TRUTH-001` in `DECISIONS.md` for the full account, including the F-016 escalation this cycle also shipped.

---

### `is_valid_post_mortem_entry(entry: object) -> bool`

Single source of truth for the post-mortem data-integrity guard (`F-008`, `DE-POSTMORTEM-INTEGRITY-001`) -- returns `True` iff `entry` is a `dict` and its `if_held_source` field is a `str` matching one of the 3 producer-recognized values:

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

**Resilience:**
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

See `DE-POSTMORTEM-INTEGRITY-001` and `DE-DISPLAY-TRUTH-001` in `DECISIONS.md`.
