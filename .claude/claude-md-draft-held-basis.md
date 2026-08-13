# CLAUDE.md key-files draft — held-basis-convergence cycle (DE-HELD-BASIS-001, 2026-08-13)

Proposal only — the doc-writer does not edit the project CLAUDE.md directly (per the doc-writer role charter). The PM applies (or amends) these on merge. Each block below is a drop-in addition to the named row's existing text in the `## Key Files` table.

---

## `analytics.py` row — append after the BL-5/BL-6/BL-8 hygiene-bundle clause

**held-basis convergence (`DE-HELD-BASIS-001`, 2026-08-13):** `get_symphony_today_change` gains a marker-gated `if_held` override — when `sym_dict["current_return_is_reconstructed"]` (the BL-9 marker) is truthy and the same-day `shadow_history` row already fetched for `dry_run` has a non-`None` `current_return`, `if_held` prefers that raw value over the reconstructed `bot_state`-derived one (no `*100` rescale — the DB column is already percent-scale). Fixes a Today-row guard-alpha overstatement (a triggered symphony's basket-reconstructed `bot_state.current_return` was standing in for its raw if-held trajectory; live capture: ~2x overstatement, 49% of the rendered delta a pure basis artifact). `_value_weighted_portfolio` gains an additive `include_paired_guard_delta` kwarg computing a paired-membership `guard_delta_vw` (both sides of the delta drawn from the same symphony set), consumed by `get_portfolio_today_change`/`get_portfolio_today_change_account_basis` — closes a latent phantom-delta-on-coverage-gap bug; the existing full-membership `if_held` average is untouched (preserves the `DE-EOD-BASIS-001` Tier-2 floor's contract). **TC-path only** — `get_portfolio_cumulative_return`'s `dry_run` is proven structurally never-`None` when `if_held` isn't (`analytics.py:899-974`), so the CR-side membership-exclusion mechanism this fix targets is unreachable there; 2 empirical regression-guard tests pin this as a future-refactor tripwire, not a shipped CR-path change (a documented deviation from the feature plan's original AC-5 wording, team-lead-ruled). Zero diff to `alpha_bot_execution.py`/`math_engine.py`/`reporting.py`. See `DE-HELD-BASIS-001` in `DECISIONS.md` and `docs/generated/analytics.md`.

---

## `app.py` row — append after the BL-7/BL-8 hygiene-fixes clause

**held-basis convergence (`DE-HELD-BASIS-001`, 2026-08-13):** `_compute_portfolio_strip()`'s and `get_state()`'s live-poll sym_dict builders (`app.py:~1508-1514`, `app.py:~2624-2630`) thread BL-9's `current_return_is_reconstructed` marker through to `analytics.get_symphony_today_change`, closing the Today-row/card guard-alpha overstatement described in `docs/audit/HELD-VS-BOT-DIVERGENCE-2026-08-13.md`. `dashboard()`'s SSR sym_dict builder (`app.py:1274`) and the frozen/closed-market snapshot branch (`app.py:2169`) are deliberately NOT threaded — the latter is structurally immune (its snapshot is built from the EOD pass's already-cleaned, marker-`False` `bot_state` entries, `alpha_bot_execution.py:1037-1058`/`:1119-1127`/`:1153`, roughly 60 lines apart in one synchronous call, so it can never carry a live mid-day reconstruction). `_compute_portfolio_strip`'s Tier-2 (no cached/last-good account totals) floor path re-derives its rendered `today_change.dry_run` from `if_held + guard_delta_vw` (the new paired-membership delta from `analytics._value_weighted_portfolio`) instead of the raw dry-run-only-membership average, closing the same mismatched-membership phantom delta at the route level; `if_held` stays full-membership. See `DE-HELD-BASIS-001` in `DECISIONS.md` and `docs/generated/app.md`.

---

## `alpha_bot_execution.py` row — correction to the existing BL-9 clause

**Find and replace** the clause reading:

> `-- discoverability hardening only, zero consumers, zero decision-path impact, zero behavioral diff (`tests/execution -n0` 426 passed unchanged)`

**With:**

> `-- discoverability hardening, zero decision-path impact, zero behavioral diff (`tests/execution -n0` 426 passed unchanged); gained its first real consumer 2026-08-13 (`DE-HELD-BASIS-001`) — `analytics.get_symphony_today_change` now reads this marker to prefer the raw `shadow_history` if-held trajectory over this module's reconstructed value for a triggered symphony's dashboard Today-row/card display; this module itself carries zero diff for that fix`

**Why:** the original "zero consumers" claim was accurate at BL-9's 2026-08-05 shipment (by design — discoverability hardening with no reader yet) but is now stale. This is the same correction already applied to `docs/generated/alpha_bot_execution.md` and annotated (not rewritten) onto `DECISIONS.md`'s historical `DE-AUDIT-BL9-001` entry this cycle — flagging it here so the top-level CLAUDE.md key-files table doesn't keep repeating the now-outdated phrase independently.

---

## Not proposed: a new standalone row

This cycle's changes are additive clauses on 3 existing rows (`analytics.py`, `app.py`, `alpha_bot_execution.py` correction) — no new file was created, so no new table row is proposed.

---

## Revise 1 (PR #125 review findings F1-F9/F13/F14, 2026-08-13) — corrections to the ALREADY-APPLIED blocks above

The original 3 blocks above were applied verbatim to `.claude/CLAUDE.md` at commit `e1e7496e`. The mandatory `/review` skill gate on PR #125 then returned 14 findings requiring a revise cycle (commits `1c7fe310`/`f35b3de7`/`50742343`) that changes some of what those blocks describe. This section gives find-and-replace instructions against the text as it now literally stands in `.claude/CLAUDE.md` — not a fresh append, since two of the original claims are now factually wrong and should not stand uncorrected in a living reference doc. The "Find" block below was extracted byte-for-byte from the live file at this doc pass (verified via `content.find(...)`, not hand-retyped) to avoid a mismatched-quote-character failure on application.

### `analytics.py` row — APPEND after the existing "held-basis convergence" clause's final sentence ("...See `DE-HELD-BASIS-001` in `DECISIONS.md` and `docs/generated/analytics.md`.")

Insert this sentence immediately after that sentence, same clause, no new bold header needed:

> **Revise 1 (PR #125 review, 2026-08-13):** the marker-resolution mechanism above is superseded — `get_symphony_today_change` now reads `current_return_is_reconstructed` from `bot_state_entry` PRIMARY (a key-presence check, not truthiness), falling back to `sym_dict` only when `bot_state_entry` lacks the key (F4) — fixing 3 previously-unthreaded consumers (`/api/strip`, the dashboard SSR card, the frozen per-symphony loop) with zero `app.py` diff at those sites. `_value_weighted_portfolio`'s `guard_delta_vw` formula is corrected from a paired-mean-times-full-invested-frac extrapolation to a coverage-scaled paired-sum-over-total-weight formula (F2/F7 — the original formula could overstate guard alpha up to 1.5x under partial coverage); `include_paired_guard_delta` now defaults `False` (was hardcoded `True`), requiring 4 explicit opt-in sites in `app.py` (F6). See `DE-HELD-BASIS-001`'s "Revise 1" section in `DECISIONS.md`.

### `app.py` row — FIND AND REPLACE within the existing "held-basis convergence" clause

**Find** (extracted byte-for-byte from the live `.claude/CLAUDE.md` at this doc pass):

> `dashboard()`'s SSR sym_dict builder (`app.py:1274`) and the frozen/closed-market snapshot branch (`app.py:2169`) are deliberately NOT threaded — the latter is structurally immune (its snapshot is built from the EOD pass's already-cleaned, marker-`False` `bot_state` entries, `alpha_bot_execution.py:1037-1058`/`:1119-1127`/`:1153`, one synchronous call, so it can never carry a live mid-day reconstruction). `_compute_portfolio_strip`'s Tier-2 (no cached/last-good account totals) floor path re-derives its rendered `today_change.dry_run` from `if_held + guard_delta_vw` (the new paired-membership delta) instead of the raw dry-run-only-membership average; `if_held` stays full-membership.

**Replace with:**

> **[SUPERSEDED, Revise 1, PR #125 review F1/F3/F4/F5/F6]** the 2-site threading design above (`_compute_portfolio_strip`/`get_state()` only) and the "structurally immune" claim about `app.py:2169` are both corrected. `analytics.get_symphony_today_change` now reads the marker from its `bot_state_entry` parameter as PRIMARY (F4) — `dashboard()`'s SSR card build, `/api/strip/<window>`, and the frozen per-symphony card loop all get the fix with zero additional `app.py` diff, since every real caller already passes the true `bot_state` sub-dict as `bot_state_entry`; the original 2 threaded sites had their marker-threading REMOVED as redundant. The frozen Tier-2 aggregate's hand-built `_snap_bot_state` gains the marker key explicitly (F5(b) — the ONE site F4 doesn't cover for free) and its own Tier-2 floor now gets the same `if_held + guard_delta_vw` re-derivation the live floor does (F5(a)), all 3 of `_compute_portfolio_strip`'s `get_portfolio_today_change` call sites plus the frozen branch's now pass `include_paired_guard_delta=True` explicitly (F6, `guard_delta_vw` no longer leaks by default). The "structurally immune" claim was FALSE as stated (F5(d)) — the EOD marker-reset loop (`alpha_bot_execution.py:1037`, scoped to `symphony_data_cache`) and the snapshot builder (`:1066`/`:1120`, scoped to ALL of `bot_state`) can disagree on symphony membership when a Composer fetch fails at the EOD run; the fix is render-layer-only (`alpha_bot_execution.py` carries zero diff, AC-7 held) — the frozen render path now honestly reads whatever marker the snapshot actually carries. See `DE-HELD-BASIS-001`'s "Revise 1" section in `DECISIONS.md` and `docs/generated/app.md`.

### `alpha_bot_execution.py` row — no correction needed, one addition

The BL-9 clause correction from the original proposal (already applied — confirmed live in `.claude/CLAUDE.md` at this doc pass) remains accurate: this module carries zero diff in the revise cycle too. One append to the SAME clause, documenting a genuinely new architectural fact about this file found by the PR #125 review:

> **Append, after the existing "gained its first real consumer..." sentence:** a PR #125 review finding (F5(d), 2026-08-13) additionally documents a read-scope mismatch in this module between the EOD marker-reset loop (`:1037`, scoped to `symphony_data_cache`) and the snapshot builder (`:1066`/`:1120`, scoped to ALL of `bot_state`) — a symphony whose Composer fetch fails at the EOD run can retain a stale `current_return_is_reconstructed=True` marker into the frozen snapshot; documented for architectural completeness, zero diff to this file (AC-7 held), fix is render-layer-only in `app.py`. See `docs/generated/alpha_bot_execution.md`'s "Known architectural gap" note.
