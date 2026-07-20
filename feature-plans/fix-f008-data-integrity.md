# Feature: Post-Mortem Data Integrity (F-008)
Status: ready
Created: 2026-07-20

## Summary
Two historical post-mortem days carry wrong/sign-flipped per-symphony `saved_dollars` and are served LIVE into the operator's $-saved headline, History, and Performance: **2026-07-09** (pre-fix sign-flips — a real loss shown as a gain; the DE-GUARD-ALPHA-SAVED-001 fix did not take effect until 07-10) and **2026-06-22** (systematic snapshot-cutoff offsets). Two root causes: (a) the historical repair tool `scripts/regenerate_post_mortems.py:43-46,70-72` hard-excludes the boundary days (default window 06-23..07-08) on a wrong docstring assumption; (b) `app.py:2712-2735` globs EVERY `post_mortem_*.json` with NO read-time validity guard — it skips syntactically-malformed JSON but has no defense against **semantically-wrong-but-valid** JSON, so contaminated days sum in silently. This cycle fixes the CODE (regen window + a read-time validity guard on all live consumers); the LIVE DATA repair of the 2 droplet days is a SEPARATE PM-gated operational step (OUT of this TDD scope — see Scope Boundaries).

## Acceptance Criteria
- [ ] AC-1: `scripts/regenerate_post_mortems.py` regenerates the FULL requested `--start..--end` range INCLUSIVE of both boundary days — no hard-coded exclusion of 06-22 / 07-09 (or any date); the window is exactly what the caller requests.
- [ ] AC-2: the live $-saved aggregate (`app.py:2712-2735`) applies a read-time VALIDITY guard — a post-mortem entry lacking a RECOGNIZED provenance stamp is EXCLUDED from the sum, not summed silently. Trusted set (2026-07-20 ruling, corrected from the original "only shadow_history" wording): any of the producer's 3 recognized `if_held_source` values — `"shadow_history"`, `"shadow_history_post_cutoff"`, `"bot_state_fallback"` (all legitimate per reporting.py's own contract). Only a MISSING or unrecognized value is excluded.
- [ ] AC-3: correctly-generated days (carrying any recognized `if_held_source`) aggregate exactly as today — no regression to a valid day's contribution.
- [ ] AC-4: the guard distinguishes SEMANTICALLY-invalid (missing/unrecognized provenance → excluded) from SYNTACTICALLY-malformed (already skipped via `except (OSError, JSONDecodeError)`) — both non-fatal, honest count/empty-state, never a crash.
- [ ] AC-5: `analytics.py:90` `load_post_mortem_history(days=60)` (feeding History rows 2.1/2.4 + Performance rows 3.x) applies the SAME validity guard — History/Performance don't serve contaminated days either.
- [ ] AC-5b (2026-07-20 addition): `analytics.py:1785` `get_history_summary(days=30)` — the actual producer of the History tab's `total_saved`/`total_alpha`/`win_rate`/`by_reason` headline stats via `GET /api/history/<days>` (app.py:3085) — is a THIRD independent post-mortem consumer with the identical unguarded-sum defect. Applies the SAME validity guard via the SAME shared helper as AC-2/AC-5. Without this, the History tab's headline totals remain contaminated post-ship, defeating AC-5's stated goal.
- [ ] AC-6 (regression guard): with all-valid (recognized-provenance) post-mortems present, the headline / History / Performance render byte-identically to pre-change (the guard adds a filter, never alters a valid value).

## Architecture
- `scripts/regenerate_post_mortems.py:43-46,70-72` — the date-window construction; make the range fully caller-controlled + inclusive.
- `app.py:2712-2735` — the `post_mortem_*.json` glob + unconditional `saved_dollars` sum → insert the read-time validity guard (a shared helper).
- `analytics.py:90` `load_post_mortem_history` — same guard (shared helper, single source of truth for "is this post-mortem entry trustworthy").
- `analytics.py:1785` `get_history_summary` (AC-5b) — same guard, same shared helper. Three call sites total (app.py guard_alpha_summary + analytics load_post_mortem_history + analytics get_history_summary) must all route through ONE `_is_valid_post_mortem_entry(...)` helper so they can't diverge.
- Provenance signal: correctly-generated days stamp `if_held_source` to one of 3 recognized values (`shadow_history` / `shadow_history_post_cutoff` / `bot_state_fallback`, per DE-GUARD-ALPHA-SAVED-001 + the row-less-degradation contract); the 2 contaminated days LACK the field entirely — the clean discriminator. Prefer a shared `_is_valid_post_mortem_entry(...)` helper over duplicated inline checks.

## Edge Cases
- Entry missing `if_held_source` entirely (both the 07-09 case AND the real captured 06-22 fixture — confirmed via direct inspection, no `if_held_source` key present) → excluded.
- Entry with `if_held_source` present but NOT one of the 3 recognized values (`shadow_history` / `shadow_history_post_cutoff` / `bot_state_fallback`) → excluded. Honest scope note: this is a provenance-based guard — a hypothetical future entry stamped with a RECOGNIZED value but still numerically wrong would not be caught (no such case exists in current data; both known-contaminated days are caught by "missing" alone).
- Syntactically-malformed JSON file → already skipped (keep that path; add a test to prove the guard doesn't double-handle it).
- Empty post_mortems dir → honest $0/0-exits empty state, no crash.
- ALL days invalid → honest empty state (not a false $0.00 that reads as "nothing saved").
- Mixed valid+invalid → aggregate only the valid subset + surface the excluded count.

## Security Considerations
- No new external input (post-mortems are internally produced files). No secrets. The guard is read-only (filters; never mutates or deletes a file). Do not leak raw file contents / exception strings into any operator-facing string.

## Testing Strategy
- **RED (quant-test-writer):** fixtures = captured/schema-derived post-mortem SHAPES — a valid stamped day (schema-derived, since NO real captured fixture in the repo carries `if_held_source` yet — all were captured 2026-07-09, pre-dating the PR #80 stamp), the REAL captured `post_mortem_2026-06-22.json` (confirmed unstamped — genuinely representative of the contamination signature, no fabrication needed), a 07-09-style schema-derived day (missing the stamp — no real capture exists for this date in the repo). Tests: (1) the aggregate EXCLUDES an entry with a missing/unrecognized `if_held_source` and its wrong dollars don't reach the sum; (2) a valid day (any of the 3 recognized values) is INCLUDED unchanged; (3) analytics.load_post_mortem_history applies the same exclusion; (3b) analytics.get_history_summary (AC-5b) applies the same exclusion; (4) malformed JSON still skipped (not double-counted); (5) regen window includes the boundary days; (6) golden: all-valid → headline/History totals unchanged. Derive fixture shapes from real post_mortem_*.json (schema-derived, assert shape/presence — no hand-invented producer dollar values). No live DB, no droplet, no live Discord; `-n0` only.
- **RESOLVED at plan-approval (2026-07-20):** 06-22's real captured fixture has NO `if_held_source` key on any trigger — caught by "missing" alone. No stamped-but-offset residual exists for either contaminated day; the provenance guard fully covers both. See the honest-scope note in Edge Cases for the (currently hypothetical) gap this guard doesn't close.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Provenance-stamp guard (any of 3 RECOGNIZED `if_held_source` values, not only `"shadow_history"`) over full per-entry recompute | The correct-generation marker already exists and cleanly separates the pre-fix (unstamped) contaminated days from all 3 legitimate producer outputs; a full recompute would duplicate reporting.py's Stage-1 math at read time. Narrowing to only `"shadow_history"` was ruled out at plan-approval (2026-07-20) — it would exclude the sanctioned `bot_state_fallback`/`shadow_history_post_cutoff` paths and regress an existing passing test (`test_flush_resync.py::test_post_mortem_path_round_trip`). Both real contaminated days are caught by "missing" alone — no stamped-but-wrong residual exists in current data. |
| DATA repair is OUT of this TDD cycle | Repairing the 2 live droplet days = running the fixed regen against production data — a PM-gated operational step (dry-run + backup + re-verify vs $160.43), not a codepath. Bundled with the batched droplet deploy. |
| Shared `_is_valid_post_mortem_entry` helper | Single source of truth used by ALL THREE live consumers (app.py guard_alpha_summary, analytics.load_post_mortem_history, analytics.get_history_summary — AC-5b added at plan-approval 2026-07-20 once `get_history_summary` was found to be a third independent unguarded aggregate feeding the History-tab headline) so none can diverge. |

## Scope Boundaries
- **IN:** regen date-window fix (AC-1); a shared read-time validity guard applied to app.py's $-saved aggregate, analytics.py's load_post_mortem_history, AND analytics.py's get_history_summary (AC-2..AC-6, AC-5b).
- **OUT:** the OPERATIONAL data-repair of the live droplet's 06-22 + 07-09 files (PM-gated: `regenerate_post_mortems.py --start 2026-06-22 --end 2026-07-09 --apply` with dry-run + backup + re-verify vs $160.43 — done during the batched deploy); F-020 History drill-down UI; F-018 guard-alpha basis; F-009 DE record correction (already done); any engine/trade change.
