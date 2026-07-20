# Feature: Post-Mortem Data Integrity (F-008)
Status: ready
Created: 2026-07-20

## Summary
Two historical post-mortem days carry wrong/sign-flipped per-symphony `saved_dollars` and are served LIVE into the operator's $-saved headline, History, and Performance: **2026-07-09** (pre-fix sign-flips — a real loss shown as a gain; the DE-GUARD-ALPHA-SAVED-001 fix did not take effect until 07-10) and **2026-06-22** (systematic snapshot-cutoff offsets). Two root causes: (a) the historical repair tool `scripts/regenerate_post_mortems.py:43-46,70-72` hard-excludes the boundary days (default window 06-23..07-08) on a wrong docstring assumption; (b) `app.py:2712-2735` globs EVERY `post_mortem_*.json` with NO read-time validity guard — it skips syntactically-malformed JSON but has no defense against **semantically-wrong-but-valid** JSON, so contaminated days sum in silently. This cycle fixes the CODE (regen window + a read-time validity guard on all live consumers); the LIVE DATA repair of the 2 droplet days is a SEPARATE PM-gated operational step (OUT of this TDD scope — see Scope Boundaries).

## Acceptance Criteria
- [ ] AC-1: `scripts/regenerate_post_mortems.py` regenerates the FULL requested `--start..--end` range INCLUSIVE of both boundary days — no hard-coded exclusion of 06-22 / 07-09 (or any date); the window is exactly what the caller requests.
- [ ] AC-2: the live $-saved aggregate (`app.py:2712-2735`) applies a read-time VALIDITY guard — a post-mortem day/entry lacking the correct provenance stamp (`if_held_source == "shadow_history"`) is EXCLUDED from the sum, not summed silently.
- [ ] AC-3: correctly-generated days (carrying `if_held_source="shadow_history"`) aggregate exactly as today — no regression to a valid day's contribution.
- [ ] AC-4: the guard distinguishes SEMANTICALLY-invalid (missing/failed provenance → excluded) from SYNTACTICALLY-malformed (already skipped via `except (OSError, JSONDecodeError)`) — both non-fatal, honest count/empty-state, never a crash.
- [ ] AC-5: `analytics.py:90` `load_post_mortem_history(days=60)` (feeding History rows 2.1/2.4 + Performance rows 3.x) applies the SAME validity guard — History/Performance don't serve contaminated days either.
- [ ] AC-6 (regression guard): with all-valid post-mortems present, the headline / History / Performance render byte-identically to pre-change (the guard adds a filter, never alters a valid value).

## Architecture
- `scripts/regenerate_post_mortems.py:43-46,70-72` — the date-window construction; make the range fully caller-controlled + inclusive.
- `app.py:2712-2735` — the `post_mortem_*.json` glob + unconditional `saved_dollars` sum → insert the read-time validity guard (a shared helper).
- `analytics.py:90` `load_post_mortem_history` — same guard (shared helper, single source of truth for "is this post-mortem entry trustworthy").
- Provenance signal: correctly-generated days stamp `if_held_source="shadow_history"` (per DE-GUARD-ALPHA-SAVED-001); the 2 contaminated days LACK it — the clean discriminator. Prefer a shared `_is_valid_post_mortem_entry(...)` helper over duplicated inline checks.

## Edge Cases
- Entry missing `if_held_source` entirely (the 07-09 case) → excluded.
- Entry with `if_held_source` present but a different value → excluded (only "shadow_history" trusted).
- Syntactically-malformed JSON file → already skipped (keep that path; add a test to prove the guard doesn't double-handle it).
- Empty post_mortems dir → honest $0/0-exits empty state, no crash.
- ALL days invalid → honest empty state (not a false $0.00 that reads as "nothing saved").
- Mixed valid+invalid → aggregate only the valid subset + surface the excluded count.

## Security Considerations
- No new external input (post-mortems are internally produced files). No secrets. The guard is read-only (filters; never mutates or deletes a file). Do not leak raw file contents / exception strings into any operator-facing string.

## Testing Strategy
- **RED (quant-test-writer):** fixtures = captured post-mortem SHAPES — a valid day (stamped `if_held_source="shadow_history"`), a 07-09-style day (missing the stamp, sign-flipped values), a 06-22-style day (stamped? verify — if unstamped, excluded; if stamped-but-offset, document that the provenance guard does NOT catch a stamped-but-wrong value and note the residual). Tests: (1) the aggregate EXCLUDES an unstamped day and its wrong dollars don't reach the sum; (2) a valid day is INCLUDED unchanged; (3) analytics.load_post_mortem_history applies the same exclusion; (4) malformed JSON still skipped (not double-counted); (5) regen window includes the boundary days; (6) golden: all-valid → headline unchanged. Derive fixture shapes from real post_mortem_*.json (schema-derived, assert shape/presence — no hand-invented producer dollar values). No live DB, no droplet, no live Discord; `-n0` only.
- **NOTE for the team to resolve in plan-approval:** if 06-22 is STAMPED but still offset (a provenance guard won't catch it), flag it — that day's repair relies on the operational regen, and the read-time guard's scope is provenance-only. Be honest about what the guard does and does not catch (don't overclaim it fixes all contamination).

## Decisions
| Decision | Rationale |
|----------|-----------|
| Provenance-stamp guard (`if_held_source=="shadow_history"`) over full per-entry recompute | The correct-generation marker already exists and cleanly separates the pre-fix (unstamped) contaminated days; a full recompute would duplicate reporting.py's Stage-1 math at read time. If a stamped-but-wrong day exists (06-22), that's the operational repair's job, honestly scoped. |
| DATA repair is OUT of this TDD cycle | Repairing the 2 live droplet days = running the fixed regen against production data — a PM-gated operational step (dry-run + backup + re-verify vs $160.43), not a codepath. Bundled with the batched droplet deploy. |
| Shared `_is_valid_post_mortem_entry` helper | Single source of truth used by BOTH app.py (headline) and analytics.py (History/Performance) so the two live consumers can't diverge. |

## Scope Boundaries
- **IN:** regen date-window fix (AC-1); a shared read-time validity guard applied to app.py's $-saved aggregate AND analytics.py's load_post_mortem_history (AC-2..AC-6).
- **OUT:** the OPERATIONAL data-repair of the live droplet's 06-22 + 07-09 files (PM-gated: `regenerate_post_mortems.py --start 2026-06-22 --end 2026-07-09 --apply` with dry-run + backup + re-verify vs $160.43 — done during the batched deploy); F-020 History drill-down UI; F-018 guard-alpha basis; F-009 DE record correction (already done); any engine/trade change.
