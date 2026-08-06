# Two-Week Review — Live Data + Tuning Audit (2026-07-21 → 2026-08-04)

**Date:** 2026-08-04
**Repo HEAD:** `2762635f` (#117, `DE-GAS-COHERENCE-001`) — branch `audit/two-week-review` off `main`
**Method:** Non-TDD audit team (read-only, zero code changed). Three parallel auditors — Tuning (optuna-specialist), Guard-Alpha Fidelity (risk-engine-specialist), Live-Data Integrity (integration-auditor) — cross-challenged, synthesized here. Every material claim carries a `file:line` or a re-run data check; grep-only claims were down-ranked.
**Evidence base:** `audit/live-data-snapshot-2wk.json` (40 `autotune_runs`, 121 `shadow_daily` rows across 11 symphonies × 11 trading days, `advisor_obs_by_role`) + 6 read-only droplet queries ferried by the PM + direct source reads at HEAD.

---

## 1. Executive Verdict

**Overall: HEALTHY CORE, with two write-side wiring gaps and one operator-honesty cluster to fix. Zero trade-execution-math defects found.**

The live exit-decision engine seam is structurally identical to the audited replay seam (no drift), shadow-history data is internally coherent (121/121 rows clean), the post-mortem provenance gate is holding on live data (9/9 in-window post-mortems valid), and the zero-adoption tuning pattern is a correctly-implemented honest null, not a broken gate. Nothing in this window touches the 1-minute execution path unsafely.

The findings are concentrated in **reporting/persistence/display honesty**, not in the risk math:

- **[HIGH] Two tuning write-side gaps** — the Phase-3 PBO overfitting veto fires live but its value is never persisted (unauditable from the DB), and the per-symphony optimization loop has no exception isolation (one symphony's failure silently drops all not-yet-processed siblings — the mechanism behind the 2026-07-24 7-of-11 partial batch).
- **[HIGH] The live $-saved headline is friction-blind on both bases** — it omits the team's own `SIM_EXIT_FRICTION_PCT=0.5` that the optimizer already applies, and this window's average gross save (+0.13pp/symphony-day) is *smaller* than that friction, so net-of-cost the headline overstates realized benefit.
- **[MEDIUM] The account-basis honesty markers are computed but never rendered** — the operator can see a stale account today-change/return under a fresh-looking timestamp with no indicator (this is the operator's actual reported "+0.16 bot vs −2.02 account" confusion).

**Two cross-track signals only visible in synthesis:**

1. **2026-07-24 was likely a bad day on the droplet, not two coincidental bugs.** [interpretation] The autotuner wrote only 7/11 symphonies AND the nightly Market Prism council was skipped across all 4 roles — two independent scheduled jobs degraded the *same calendar day*. Precision caveat: they ran ~14h apart (autotuner EOD ~21:48 UTC / ~17:48 ET; council nominally overnight, ~07:06–07:14 UTC), and the council-skip timing is from the PM's ferry, not independently re-derived — so this implies either a sustained resource-pressure day or two separate same-day events, **not** a single instantaneous kill. Root-cause the partial batch by pulling 2026-07-24 droplet logs *first*.
2. **The risk engine this window ran on 100% stock-default parameters (never once adopted a tuned set, across the entire visible history), and its realized guard-alpha net-of-friction on the triggered population is plausibly ~breakeven-to-slightly-negative** (+0.13pp gross vs 0.5pp friction). [interpretation] This is NOT a crisis — the guard still cut large drawdown days materially (e.g. 2026-07-31 saved ~6pp on multiple symphonies) — but the "$X saved" headline as displayed overstates the net, and the operator has no signal that tuning has never engaged.

---

## 2. Per-Dimension Verdict Table

| Dimension | Verdict | Headline |
|---|---|---|
| **Tuning / autotuner** | ⚠️ SOUND GATE, 2 WIRING GAPS | Honest-null adoption is correctly implemented; PBO value dropped at persist (HIGH); no per-symphony exception isolation → silent partial batches (HIGH); never adopted a tuned param set in visible history (product fact). |
| **Guard-Alpha fidelity (math)** | ✅ MATH CLEAN, 1 DISPLAY-HONESTY HIGH | Live⇄replay seam identical; two-sidedness correct end-to-end (#117); $-saved friction-blind on both bases (HIGH); read path immune to the old basket-reconstruction defect (which still exists in-engine but fenced off — latent footgun). |
| **Live-data integrity** | ✅ DATA COHERENT, 1 DISPLAY-HONESTY MEDIUM | shadow_history 121/121 clean; provenance gate holding live; stale/VW account-basis markers computed but never rendered (MEDIUM); minor window-edge + label-disclosure nits. |

---

## 3. Mapping to the Original Goal + Gaps Named

**Goal:** honest audit of the live data + tuning over the window, plus a prioritized backlog the PM can `/scaffold`.

| Question posed | Answered? | Gap |
|---|---|---|
| Is tuning adopting or reverting, and is that honest? | YES | 100% revert-to-fallback is an honest null (T3); **but** two persist/isolation gaps sit alongside it (T1, T2). |
| Is the guard-alpha / $-saved math faithful? | YES | Math faithful; **display** is friction-blind (M1). |
| Is the live data integral (no wrong-basis, no contamination, no mislabel)? | YES | Data integral; **display** hides the stale-basis honesty markers (D1). |
| Account-basis timeout → wrong basis (the operator's confusion)? | YES + CORRECTED | The lead's "→ value_weighted floor" mechanism is post-restart-only; the real steady-state failure is unrendered *stale account basis* + a misleading fresh timestamp (D1/D2). |
| Root cause of the 07-24 partial tuning batch? | PARTIAL | Structural cause found (no loop isolation, T2); exact trigger needs 2026-07-24 droplet logs (cross-track: a droplet-health event that day — §1). |

---

## 4. Prioritized Findings (with evidence)

Severity key: **HIGH** = fix this cycle · **MED** = fix soon · **LOW** = hygiene/defensive · **INFO** = disclosed design / no action.
`[FACT]` = code/data-verified. `[interp]` = reasoned inference, labeled.

### TUNING

**T1 — [HIGH] Phase-3 PBO veto computed and used live, but never persisted** `[FACT]`
- `_pbo_value` computed at `autotuner.py:2854/2870` (`math_engine.compute_pbo`), fed into the live gate at `autotuner.py:3075` and can veto (`3087-3098`, poisoning `oos_alpha`/`baseline_decision`). But `database.save_autotune_run(...)` at `autotuner.py:3218-3239` **omits `pbo=`** entirely, though the signature accepts it (`database.py:711`) and INSERTs it (index 15, `database.py:792`). Result: `autotune_runs.pbo` is NULL in **40/40** rows (re-verified from snapshot) — the value is discarded at persist, not un-computed. `tests/autotuner/test_pbo_migration_028.py` unit-tests the accessor round-trip but never calls `run_autotuner()`, so the integration gap slipped through.
- Impact: the overfitting veto's behavior is un-auditable from the DB; a future PBO regression would be invisible.
- **Fixed by `DE-AUDIT-BL1-001` (2026-08-05).** `run_autotuner`'s `save_autotune_run(...)` call now passes `pbo=_pbo_value`; forward-only (legacy NULL rows are not backfilled). See `DECISIONS.md` and `docs/generated/autotuner.md`'s "PBO Veto Persistence" section.

**T2 — [HIGH] Per-symphony optimization loop has no exception isolation → silent partial batches** `[FACT]` + `[interp]` root cause
- The `for normalized_name in symphony_names:` loop (`autotuner.py:2558-3288`) wraps `study.optimize` (`:2762`), CPCV/PBO, the OOS cascade, and `save_autotune_run` (`:3218`) with **no surrounding try/except** — only the two tail advisor-producer calls (Overfitting Conscience `:3259-3272`, Divergence Explainer `:3276-3288`) are isolated. An uncaught exception mid-loop aborts `run_autotuner()` for every not-yet-processed symphony, with the only symptom a missing `autotune_runs` row (no `aborted` marker — the `DE-AUTOTUNE-REPORTING-001` graceful aborts fire *before* the loop and would give 0/11, not 7/11).
- Observed: batch counts 07-10=11, 07-18=11, **07-24=7**, 07-31=11 (re-verified). The 4 missing on 07-24 (`corporate chaos 2060`, `corporate chaos 5 ways`, `planet lqd … waltanansi`, `planet of the paragons`) all return normally on 07-31.
- `[interp]` **Root cause is likely environmental, not a per-symphony code bug:** 2026-07-24 is *also* the single night the nightly Prism council was skipped across all 4 roles (PM ferry). Two independent scheduled jobs degraded the same calendar day. Precision caveat (per the tuning auditor): the two ran ~14h apart (autotuner EOD ~21:48 UTC vs council overnight), and the missing try/except is consistent with EITHER an uncaught code exception OR an OS-level kill — the data alone cannot distinguish them, so the cross-track same-day correlation is the tie-breaker toward environmental, not a proof of one instant. The isolation fix is correct defense-in-depth regardless, but **pull 2026-07-24 droplet logs first.**
- **Fixed by `DE-AUDIT-BL2-001` (2026-08-05).** Per-symphony loop isolation (try/except/continue) + `_batch_summary` attempted-vs-completed visibility + a distinct partial-batch EOD Discord embed. **INV-1 (below) root-caused the specific 07-24 incident to a deploy-restart SIGTERM, not an uncaught exception** -- this fix guards the uncaught-exception class as defense-in-depth and does not claim to prevent a 07-24-style recurrence. See `DECISIONS.md` and `docs/generated/autotuner.md`.

**T3 — [INFO / PRODUCT FACT] Never once adopted a tuned parameter set** `[FACT]`
- `fallback_oos_alpha == default_oos_alpha` byte-exact in **40/40** rows (re-verified) → every symphony's `current_params` == `database.DEFAULT_STRATEGY` in every run. Combined with 0/40 `Adopted AI` and 40/40 `oos_alpha=-inf`, Planet Stopper has run on **100% un-tuned stock-default risk parameters for the entire visible history (≥2026-07-10).**
- The gate is correctly implemented (honest null): reject site `autotuner.py:2836-2844` (`_haircut_select` returns `winner_trial=None`), genuine ~500-trial searches (`n_effective=500`, only reachable inside `if haircut_trials:` at `:2798`), noise-level `train_alpha` (~0.0001–0.012) never clears the BHY-corrected bar. This is design working as intended — but the operator has no signal that tuning has *never* engaged, and "Reverted to Fallback" is numerically identical to "Reset to Global Default" the whole time.
- **Fixed by `DE-AUDIT-BL5-12-001` (2026-08-05).** New pure `analytics.compute_never_adopted_streak(rows)` computes a symphony's consecutive non-"Adopted AI" run streak from existing `autotune_runs` rows (no schema change); `GET /api/autotune-runs` additively stamps each row with `never_adopted_streak`; `static/ai_advisor.js`'s `loadRecentRuns()` renders it as a dim one-liner per card, distinguishing the accumulated pattern from the per-run `baseline_decision` string already shown. See `DECISIONS.md` and `docs/generated/analytics.md`.

**T4 — [MED, needs live verification] Intermittent zero-triad (`train_alpha==fallback==default==0.0`), 11/40 rows** `[FACT]` data, `[interp]` mechanism
- 11/40 rows (6 in the 07-31 batch; re-verified). Two code mechanisms produce byte-identical output and cannot be disambiguated from the DB: (a) account-id-not-resolvable early-return (`autotuner.py:2637-2638`, `2925-2979`) — the symphony's Composer account id can't be matched in `bot_state` for that run; (b) a genuine zero-Guard-Alpha-trigger week (`run_simulation` returns 0.0 on zero trigger days).
- `[interp]` Suggestive against (b): live `shadow_daily` shows `any_trigger=1` on 87/121 (72%) symphony-days — a genuine zero-trigger 250-day replay hitting 6/11 symphonies in one week looks unlikely. Cannot join `shadow_daily` (hash-keyed) to `autotune_runs` (name-keyed) without a name→hash map absent from the snapshot. Needs `bot_state`/Composer-account-roster history around 07-24/07-31.

**T5 — [LOW] Dead/unwired diagnostic columns** `[FACT]`
- `deflated_sharpe`/`ce_metric`/`cvar_feasible`/`lambda_budget`/`sortino_sentinel_pct`/`fold_role`/`account_id` are permanently NULL (0/40). `deflated_sharpe` is intentionally dead (`tests/autotuner/test_c4_dsr_machinery_removed.py`, Decision D3); the other 6 have no parameter in `save_autotune_run`'s signature at all (`database.py:692-715`) — never-implemented schema, not a caller bug. (Distinct from T1: `selection_tstat`/`naive_sharpe`/`validation_sharpe`/`frozen_eval_sharpe` are correctly-nulled-on-this-path — never reached because no proposal was adopted, not dropped.)
- **Fixed by `DE-AUDIT-BL5-12-001` (2026-08-05).** The 6 never-wired columns gain schema-comment + `save_autotune_run`-docstring notes distinguishing 3 provenance tiers (truly never wired / orphaned-writer `record_autotune_run` with zero callers / `fold_role`'s real-but-writerless semantic, correcting the plan's own initial mischaracterization); documentation-only, no migration, never "always NULL" wording (a legacy row could theoretically be non-NULL from a historical accessor). See `DECISIONS.md`.

**T6 — [LOW] `oos_alpha` legs are raw multi-day SUMS, unannotated** `[FACT]`
- `total_guard_alpha` accumulates across every triggered OOS day (`autotuner.py:1928-2047`); `avg_oos_alpha = oos_alpha / test_days_count` (`:3036`) exists to un-inflate it. This is why values like −743%/−581% appear — a sum convention, not corruption (all 3 cascade legs use it, so comparisons are uncorrupted). Easy to misread on the surface.
- **Fixed by `DE-AUDIT-BL5-12-001` (2026-08-05).** A one-line comment at `total_guard_alpha`'s accumulation start (`autotuner.py`) documents the sum convention, cross-referencing `avg_oos_alpha`; the SAME annotation lands in `database.py`'s `save_autotune_run` docstring; `static/ai_advisor.js`'s `'OOS alpha: <code>'` label is relabeled to `'OOS alpha (cumulative sum across triggered days): <code>'` (AC-19 disclosure-relabeling, since no `avg_oos_alpha` companion is persisted anywhere to display alongside it). See `DECISIONS.md` and `docs/generated/autotuner.md`.

### GUARD-ALPHA FIDELITY (MATH)

**M1 — [HIGH] Live $-saved headline is friction-blind on both bases** `[FACT]` + `[FACT]` quantified
- Snapshot basis `saved_pct = f_ret − live_ret` (`reporting.py:92`) and realized/marks basis `saved_pct_realized = f_ret − realized_ret` (`reporting.py:193`) both carry **no cost term**. The team's own `SIM_EXIT_FRICTION_PCT=0.5` (`autotuner.py:1463`) is applied at 3 replay-only sites (`:1545/1742/2012`) and structurally **forbidden** from the live engine (`tests/autotuner/test_exit_friction_blast_radius.py`, `_FORBIDDEN_FILES={alpha_bot_execution.py, math_engine.py}`). Neither dashboard headline discloses "gross of trading costs" (`templates/index.html:1057-1081`).
- Quantified (independently re-derived from `shadow_daily`, 87 post-trigger symphony-days): mean gross save = **+0.13pp**; 47/87 (54%) positive; **net-of-0.5pp-friction the window mean flips to −0.37pp and positives drop to 29%**; 21/47 (45%) positive rows fall below 0.5pp (flip to net loss). Live droplet post-mortems corroborate that both bases are populated and close (snapshot≈realized within a few $; e.g. −31.34/−30.35, 14.79/11.39). **[interp]** The 87-row shadow figure is a window-magnitude illustration (per symphony-day, not per weighted exit); the code-level friction-blindness is the primary claim, the numbers size its materiality.
- Not cosmetic: this window's average gross save is *smaller than the friction the team already models*. Traces to `docs/research/methodology-validation-2026-07.md` §3 Rec 2, only partially closed by `DE-EXIT-FRICTION-REALIZED-001` (which fixed the optimizer objective + added a marks-basis note, but left the live headline gross-of-cost).
- **Fixed by `DE-AUDIT-BL3-001` (2026-08-05).** `guard_alpha_summary()` gains additive `cumulative_saved_dollars_net_of_friction`/`saved_dollars_realized_net_of_friction` fields (friction subtracted at the percentage level per entry via `autotuner.SIM_EXIT_FRICTION_PCT`, single-sourced) plus a static "gross of trading costs" dashboard caveat; display-layer only, zero diff to `reporting.py`'s VALUE computations or the live engine. See `DECISIONS.md` and `docs/generated/app.md`'s `GET /api/guard-alpha-summary` section.

**M2 — [CLEAN] Live exit-decision seam identical to the audited replay seam** `[FACT]`
- `alpha_bot_execution.py` and `autotuner.py:_replay_exit_tick` share `compute_arm_disarm_decision`, `compute_active_trailing_stop` (+`squeeze_floor`), `resolve_trigger_priority` — the `DE-MATH-R3B/R3C` shared seam, confirmed no drift at HEAD. One accepted config-only divergence (live MC 5000 paths vs replay 300, `alpha_bot_execution.py:106` / `synthetic_history.py:297`) already adjudicated "accept, no bump" (R3-a). No defect.

**M3 — [LOW-latent] `analytics.py:543` UTC trading-day fallback** `[FACT]` (independently found by BOTH math + data auditors)
- `analytics.get_symphony_today_change` falls back to `datetime.now(UTC)` only when a caller omits `trading_day`. Both production call sites pass an explicit ET `trading_day` (`app.py:1502/2676`, write path ET at `alpha_bot_execution.py:711`), so it is dead code today. After ~20:00 ET a future forgetful caller would query tomorrow's UTC date against ET-stamped rows → empty result. Cheap defensive fix (swap to ET).
- **Fixed by `DE-AUDIT-BL5-12-001` (2026-08-05).** The fallback now resolves `datetime.now(ZoneInfo("America/New_York"))`, matching the write-side ET convention. Dead-code-today defensive fix only — all 3 real call sites confirmed byte-unchanged (AST-pinned). See `DECISIONS.md`.

**M4 — [LOW-advisory, hardening] Basket-reconstruction defect mechanism still live in-engine, fenced off** `[FACT]` (+ synthesizer correction)
- The read path is clean: `reporting.py`/`analytics.py` source $-saved exclusively from `shadow_history` (written in the data phase), never trusting `bot_state["current_return"]` (only the explicitly-labeled `bot_state_fallback` tier reads it). But the `DE-GUARD-ALPHA-SAVED-001` defect *mechanism* — the "TRUE SHADOW RETURN OVERRIDE" that reconstructs a basket-based `current_return` (`alpha_bot_execution.py:1246-1260`) and writes it back into `bot_state["current_return"]` (`:1635`) for triggered symphonies — is still live code. **Synthesizer correction to the math auditor's report:** there are **three** write sites to `bot_state[...]["current_return"]` (`:886` clean, `:1037` clean, `:1635` override), not two; the extra clean site (`:1037`) does not change the finding — all three plus the shadow write (`:942`) precede/avoid the read path, so shadow_history stays clean. Latent footgun: any future code reading `bot_state[...]["current_return"]` for a triggered symphony silently gets the reconstructed-basket value. **Do NOT naively delete the override** — it also feeds live exit-decision inputs (holdings/HWM/MC) for already-triggered symphonies.
- **Fixed by `DE-AUDIT-BL9-001` (2026-08-05).** All 3 write sites (confirmed 3, matching the synthesizer's own correction above) now co-stamp an additive `bot_state[symphony_id]["current_return_is_reconstructed"]` boolean (`True` only at the override site, reusing the already-computed `is_triggered_now`) — refined from a single-site proposal after tracing that the EOD post-mortem pass also writes a clean `current_return` even for a still-triggered symphony (a single-site stamp would have gone stale after that write). Override NOT deleted or functionally altered (AC-13); zero consumers, zero decision-path impact. See `DECISIONS.md` and `docs/generated/alpha_bot_execution.md`.

**Two-sidedness — [CLEAN]** `[FACT]` — `#117` (`DE-GAS-COHERENCE-001`) fixed sign display end-to-end: aggregation is raw/signed (`app.py:3363/3373`, `analytics.py:2033-2034`, no abs/clip), display formatters do abs+word (`analytics.py:1973-1984`, `static/index.js:1450-1480`, `static/history.js`). A real loss day (Gpaw 07-29, guard −0.34 vs held +5.79 = −6.13pp) renders red/"lost", not clamped. Live-verified on the real render during the #117 ship.

### LIVE-DATA INTEGRITY

**D1 — [MED] Account-basis stale/VW markers computed but never rendered** `[FACT]`
- Backend stamps `account_basis_stale`/`account_basis_as_of`/`basis="value_weighted"` (`app.py:1749-1758` live, `2382-2387` frozen). **Zero consumers** in `static/index.js` (grep) or `templates/index.html`; comparison rows render raw `ps.today_change`/`ps.cumulative_return` (`static/index.js:938/943`). The only freshness stamp — `hero-data-as-of` — derives from `last_successful_cycle_at` (the ENGINE cycle, `app.py:1703`), **not** the account-fetch time. So on an account-fetch timeout the operator sees a stale account today-change/return under a fresh-looking cycle timestamp with no stale indicator — the operator's reported "+0.16 bot vs −2.02 account" confusion. Timeout is intermittent: **151 read-timeouts/7d (~21/day), 0 in the last 24h** (PM ferry), against a per-minute fetch (`app.py:960`) ≈ 1.5% of attempts.
- **Fixed by `DE-AUDIT-BL4-001` (2026-08-05):** `static/index.js` gained `renderAccountBasisChip()`/`renderAccountBasisFreshness()`, called from `updateComparisonRows()` every poll — a stale/value-weighted-floor chip now renders on the Today/Cumulative rows (never Max DD), plus a dedicated `#account-basis-as-of` freshness stamp distinct from `#hero-data-as-of`. Backend computation pinned byte-unchanged (AC-5). See `DECISIONS.md`.

**D2 — [CORRECTION to the PM's lead]** `[FACT]`
- The lead's "timeout → value_weighted floor" is only reachable **between a daemon restart and the first successful fetch**. `_account_totals_last_good` is a plain dict (`app.py:574`), reassigned only on success (`app.py:864`), never cleared → after the first success (droplet up ~4d), a single timeout falls to **Tier-1 STALE account basis** (last-good values + `account_basis_stale=True`), not the VW floor. The "sticky last-good" the lead proposed is already implemented; the missing piece is *rendering* it (D1). No separate fix — this reframes D1's mechanism and severity.
- **Addressed alongside D1 by `DE-AUDIT-BL4-001` (2026-08-05):** the render layer now honors both tiers distinctly — STALE (with a real as-of timestamp) takes priority over the VW-floor label (no timestamp, never fabricated) when both flags are simultaneously true, matching the mechanism this correction describes. No backend change was needed or made.

**D3 — [LOW] `$-saved` dashboard vs History window-edge asymmetry** `[FACT]`
- `/api/guard-alpha-summary?window=` uses a UTC-date cutoff *inclusive* of the boundary day (`app.py:3352` + `analytics._window_cutoff_date:1733`); `/api/history/<days>` uses naive-local `now()` carrying time-of-day (`analytics.py:2000-2022`), which *excludes* a post-mortem dated exactly on the cutoff day. Contradicts the CLAUDE.md "byte-comparable at any shared token" guarantee — but only at a boundary-dated post-mortem (the #117 live parity-verification didn't exercise that edge, so it is not contradicted, just narrowed). Fix: one cutoff fn + one timezone.
- **Fixed by `DE-AUDIT-BL5-12-001` (2026-08-05).** `get_history_summary` now calls `analytics._window_cutoff_date(days)` directly and compares via the SAME inclusive-of-boundary string compare `/api/guard-alpha-summary` uses; a new `TestBoundaryDatedFileByteParity` test class proves byte-parity between the two routes AT the boundary. Non-boundary day-count arithmetic unchanged. See `DECISIONS.md` and `docs/generated/analytics.md`.

**D4 — [MED display-honesty] The "Bot" comparison number is an unlabeled dry-run simulation** `[interp]` (surfaced by PM; overlaps D1)
- The dashboard's "Bot" today-change/return is a shadow/dry-run simulation, not labeled as such. Combined with D1's unrendered stale-basis, this is the compounding root of the operator's confusion (a simulated "Bot" number sitting next to a stale-but-unmarked "Account" number). Label it as simulated.
- **Fixed by `DE-AUDIT-BL4-001` (2026-08-05):** both "Bot" comparison spans (`templates/index.html`, Today + Cumulative rows) gained a `title=` tooltip — "Simulated (shadow-history) trajectory — not realized account P&L" — disclosing the figure's nature; the underlying `dry_run` value is unchanged.

**D5 — [INFO, RESOLVED benign] Prism companion-row completeness** `[FACT]` (ferry-confirmed)
- All-time counts (PRISM 43 / SOURCES 38 / LENS_CACHE 35 / VERIFICATION 32) are benign staggered-feature-intro. In-window, all 4 roles wrote **14 nights on the identical date set** — every council night has all companions (write order `prism_scheduler.py:495-533/590-598`; renders None-safe). Exception: **2026-07-24 skipped across all 4 roles** (a single missed council night — see the T2 cross-track correlation). No action beyond the optional per-run "provenance-complete" marker.

**D6 — [LOW] Stale comment on DIVERGENCE_EXPLAINER** `[FACT]`
- The role is deferred-by-design (flag `SECOND_WINDOW_CVAR_ENABLED` off; `advisors/divergence_explainer.py:150-181`), correctly writing nothing; the 22 rows (`last_id=99`) are pre-AC-14 legacy. But `app.py:7406-7409` still says the producer "is permanently rejected but still writes one per autotune run" — false since AC-14. Correct the comment (the suppression filter can stay as legacy-row defense).
- **Fixed by `DE-AUDIT-BL5-12-001` (2026-08-05).** Comment corrected to state the producer writes nothing today, and the filter below is legacy-row defense for the 22 pre-AC-14 rows, not ongoing-write suppression. Comment-only — zero change to the filter logic. See `DECISIONS.md`.

**D7 — [INFO] Cumulative Return is `simple_return`, basis undisclosed in label** `[FACT]`
- `portfolio_cr = simple_return*100` (`app.py:844`), deliberately matching Composer's displayed "Total return" (cash-flow-sensitive, ~5pp off TWR). Coherent with Composer, but the label ("Cumulative · lifetime") doesn't disclose it, so a TWR benchmark comparison misleads. Add a tooltip.
- **Fixed by `DE-AUDIT-BL5-12-001` (2026-08-05).** A sibling info-icon span (never nested inside `.vs-row-label`, preserving the pre-existing `TestCumulativeRowNamesItsBasis` no-nested-tags regex) discloses the cash-flow-sensitive basis via a `title=` tooltip. Zero change to the underlying `portfolio_cr` value. See `DECISIONS.md`.

**D8 — [INFO] MDD bot-vs-held mismatched lookbacks (guarded)** `[FACT]`
- Bot MDD = shadow (≤31 retained days) vs held MDD = Composer lifetime (`app.py:1640-1651`); mitigated by the `<30d` winner-suppression (`templates/index.html:887-889`) which, given ≤31-day retention, is effectively always-on. Not a defect; the operator just gets little MDD signal.

**POSITIVES (verified clean):**
- **shadow_history internally coherent** `[FACT]`: 121 rows, 0 duplicate (day,sym) pairs, complete 11×11 grid, `held_last`∈[held_min, held_close_max] 121/121, `guard_last==held_last` to 0.000000 on all 34 no-trigger rows. One benign intraday capture gap (2026-07-29 n=351 vs ~375-388); 2026-08-04 is the in-progress day (n=40).
- **Provenance gate holding** `[FACT]`: `analytics.is_valid_post_mortem_entry` (`analytics.py:99-113`) shared by all 3 consumers (`app.py:3362`, `analytics.py:179/2028`); write side stamps `if_held_source` unconditionally (`reporting.py:76/84/91`); **live ferry: 9/9 in-window post-mortems carry a valid `if_held_source`, zero contaminated days.** Both known historical contaminations (2026-06-22, 2026-07-09-style) predate the window.

---

## 5. What Could NOT Be Determined (and why)

1. **T2 exact trigger (07-24 partial batch):** the actual exception/OOM signal — not in the snapshot; needs 2026-07-24 droplet logs (`journalctl`, OOM/restart records, absence of the "finished all symphonies" print at `autotuner.py:3290`). Cross-track evidence points to a droplet-health event that day.
2. **T4 mechanism (zero-triad):** account-id-not-resolvable vs genuine zero-trigger week produce byte-identical DB output; disambiguation needs `bot_state`/Composer-account-roster history for the affected symphonies (snapshot lacks a name→hash map to join `shadow_daily`).
3. **Exact snapshot-vs-realized $ delta per exit:** requires the full `post_mortem_*.json` `saved_dollars`/`saved_dollars_realized` fields; ferried samples show they track within a few $, sufficient to conclude both bases share the same friction-blind gap (M1).
4. **Live current dashboard basis (fresh vs stale right now):** the authed `/api/state` poll was blocked (dashboard password not in `.env` under that name — only the hash). Not pursued: the timeout frequency (D1, 0 in last 24h) sizes the risk without it, and no finding hinges on the instantaneous basis.

---

## 6. Prioritized FIX BACKLOG (for `/scaffold`)

Ordered by priority. Each item is scoped for a PM dispatch decision. "Scope" is relative effort, not a schedule.

### P1 — HIGH (fix this cycle)

**BL-1 — Persist the PBO veto value** (T1)
- *What's wrong:* the live Phase-3 PBO overfitting veto is computed and can veto, but `autotune_runs.pbo` is NULL 40/40 — the value is dropped at persist.
- *Evidence:* `autotuner.py:3075` (used) vs `:3218-3239` (omitted from `save_autotune_run`); `database.py:711/792` accepts+INSERTs it.
- *Fix:* add `pbo=_pbo_value` to the `save_autotune_run(...)` call; add an **integration** test on `run_autotuner()` itself asserting the round-trip (the existing `test_pbo_migration_028.py` only exercises the accessor).
- *Scope:* XS (one-line code + one test). TDD — new assertion on an existing path.
- *Status:* **Shipped** — `DE-AUDIT-BL1-001` (2026-08-05).

**BL-2 — Isolate the per-symphony optimization loop + surface attempted-vs-completed** (T2)
- *What's wrong:* one symphony's uncaught exception silently drops all not-yet-processed siblings (the 07-24 7/11 partial batch), with no operator-visible marker.
- *Evidence:* `autotuner.py:2558-3288` core body unguarded; only tail producers isolated (`:3259/:3276`).
- *Fix:* wrap the per-symphony body in try/except (log-and-continue, mirroring `:3261/:3278`); add `symphonies_attempted` vs `symphonies_completed` to the EOD Discord/return value so a partial batch is never silent. **Pull 2026-07-24 droplet logs first** to root-cause (likely a droplet-health event — the council was also skipped that night).
- *Scope:* S (loop isolation + regression test); +S if the attempted/completed count touches `reporting.py`.
- *Status:* **Shipped** -- `DE-AUDIT-BL2-001` (2026-08-05). See `docs/audit/INV-FINDINGS-2026-08-05.md` for the INV-1 root-cause finding this fix's own honesty boundary is scoped against.

**BL-3 — Disclose (or apply) trading-cost friction on the live $-saved headline** (M1)
- *What's wrong:* both $-saved bases omit the `SIM_EXIT_FRICTION_PCT=0.5` the optimizer already models; this window's mean gross save (+0.13pp) is smaller than that friction, so the headline overstates net benefit.
- *Evidence:* `reporting.py:92/193` (cost-free), `autotuner.py:1463` (friction, forbidden from live engine); quantified −0.37pp net-of-friction window mean.
- *Fix (either, not mutually exclusive):* (a) apply a disclosed friction adjustment as a third figure on both bases; (b) minimum — add a "(gross of trading costs)" caveat to both headline labels (`templates/index.html:1057-1081`), mirroring the existing "marks basis" caveat. Reporting/display-layer only — **no live engine change** (respect the existing blast-radius boundary).
- *Scope:* 1 small TDD cycle (full friction) or doc/label-only (smaller). Recommend (a) with disclosure.
- *Status:* **Shipped** -- `DE-AUDIT-BL3-001` (2026-08-05). Implemented option (a) with disclosure, per the recommendation: a disclosed "gross of trading costs" caveat plus a genuine computed net-of-friction third/fourth line (snapshot + realized basis), gated on the same empty-state as the gross lines. See `DECISIONS.md`.

### P2 — MEDIUM (fix soon)

**BL-4 — Render the account-basis honesty markers + fix the freshness stamp** (D1/D2/D4)
- *What's wrong:* stale/VW markers are computed backend-side but never rendered; the freshness stamp reflects the engine cycle, not the account fetch; the "Bot" comparison number is an unlabeled dry-run simulation. Together = the operator's "+0.16 bot vs −2.02 account" confusion.
- *Evidence:* markers `app.py:1749-1758/2382-2387`, zero consumers in `static/index.js`; `hero-data-as-of`←`last_successful_cycle_at` (`app.py:1703`); intermittent timeout (~21/day, 0/24h).
- *Fix:* wire the existing markers to a visible "stale / value-weighted basis" chip on the Today/Cumulative rows; anchor the freshness stamp to `account_basis_as_of` when stale; label the "Bot" column as a simulation. Backend already emits everything — frontend-only. Optionally extend the account-fetch timeout (10→30s) to reduce stale flicker.
- *Scope:* S (frontend render fn + template spans + small CSS chip; optional XS backend timeout bump).
- *Status:* **Shipped** -- `DE-AUDIT-BL4-001` (2026-08-05). Both fixes landed as recommended: the visible chip + dedicated freshness stamp render on the Today/Cumulative rows only, the "Bot" spans gained the simulation-disclosure tooltip, and the optional timeout bump (10->30s) was ruled in-scope [PM-ASSUMED] and shipped. Backend computation byte-unchanged (AC-5, pinned). See `DECISIONS.md`.

### P3 — LOW (hygiene / defensive)

**BL-5 — Unify the two $-saved window cutoffs** (D3) — route `get_history_summary` through `_window_cutoff_date` + one timezone so the boundary-dated day matches. Scope: S.
- *Status:* **Shipped** -- `DE-AUDIT-BL5-12-001` (2026-08-05). `get_history_summary` now calls `_window_cutoff_date(days)` directly; a `TestBoundaryDatedFileByteParity` test proves byte-parity at the boundary. See `DECISIONS.md`.
**BL-6 — Swap the `analytics.py:543` UTC fallback to ET** (M3, = data F-LD-10) — defensive; dead code today. Scope: XS.
- *Status:* **Shipped** -- `DE-AUDIT-BL5-12-001` (2026-08-05). Fallback now resolves the ET calendar date; all 3 real call sites confirmed byte-unchanged. See `DECISIONS.md`.
**BL-7 — Correct the stale DIVERGENCE_EXPLAINER comment** (D6) at `app.py:7406-7409`. Scope: XS.
- *Status:* **Shipped** -- `DE-AUDIT-BL5-12-001` (2026-08-05). Comment-only correction. See `DECISIONS.md`.
**BL-8 — "N weeks at default params" operator signal** (T3) — surface that tuning has never adopted, distinct from a per-week revert. Scope: S dashboard/reporting.
- *Status:* **Shipped** -- `DE-AUDIT-BL5-12-001` (2026-08-05). New `analytics.compute_never_adopted_streak`, surfaced on `GET /api/autotune-runs` and rendered by `static/ai_advisor.js`'s `loadRecentRuns()` -- the render step landed as a dedicated follow-up commit after the team lead flagged an initial computed-but-not-rendered gap. See `DECISIONS.md`.
**BL-9 — Harden the basket-reconstruction footgun** (M4) — structurally fence or rename `bot_state["current_return"]` for triggered symphonies so a future reader can't get the reconstructed value; do NOT delete the override (feeds live exit inputs). Scope: investigation + S.
- *Status:* **Shipped** -- `DE-AUDIT-BL9-001` (2026-08-05, own dedicated entry -- touches the live execution path). Additive `current_return_is_reconstructed` marker at all 3 write sites; override untouched. See `DECISIONS.md`.

### P4 — INFO / cleanup (schedule opportunistically)

**BL-10 — Drop/document dead autotune columns** (T5): `deflated_sharpe`/`ce_metric`/`cvar_feasible`/`lambda_budget`/`sortino_sentinel_pct`/`fold_role`/`account_id`. Scope: XS.
- *Status:* **Shipped** -- `DE-AUDIT-BL5-12-001` (2026-08-05). 3-tier schema-comment + docstring documentation, no migration. See `DECISIONS.md`.
**BL-11 — Annotate the `oos_alpha`-is-a-sum convention** (T6): one-line comment near the schema/reporting surface. Scope: XS.
- *Status:* **Shipped** -- `DE-AUDIT-BL5-12-001` (2026-08-05). Comments at `autotuner.py`'s accumulation site + `database.py`'s docstring; `static/ai_advisor.js`'s OOS alpha label relabeled (AC-19 disclosure-relabeling deviation). See `DECISIONS.md`.
**BL-12 — Disclose CR=`simple_return` basis** (D7): tooltip/label. Scope: XS.
- *Status:* **Shipped** -- `DE-AUDIT-BL5-12-001` (2026-08-05). Sibling info-icon tooltip on the "Cumulative · lifetime" label; zero change to the underlying value. See `DECISIONS.md`.

### Open investigations (not code changes — droplet access required)

- **INV-1:** pull 2026-07-24 droplet logs to root-cause the partial batch + skipped council (feeds BL-2). **Resolved -- see `docs/audit/INV-FINDINGS-2026-08-05.md`.**
- **INV-2:** determine T4's mechanism (account-id-not-resolvable vs genuine zero-trigger) via `bot_state`/account-roster history for the affected symphonies. **Resolved -- see `docs/audit/INV-FINDINGS-2026-08-05.md`.**

---

## 7. Cross-Track Reconciliations (honest-broker notes)

- **PM lead "→ value_weighted floor" vs verified mechanism:** corrected to *stale account basis* (D2); the sticky last-good is already implemented, the render is missing.
- **PM's #117 live parity-verification vs D3 window-edge asymmetry:** not contradictory — D3 is a narrow boundary-dated edge the #117 scenario didn't exercise.
- **Math auditor "two write sites" vs three:** corrected to three (`:886/:1037/:1635`), substance intact (M4).
- **Convergence (raises confidence):** the `if_held_source` gate was confirmed independently by two auditors + live ferry; the `analytics.py:543` UTC fallback by two auditors independently; the guard-alpha two-sidedness numbers reproduced by the synthesizer and the math auditor to the digit; the 07-24 degradation appears independently in the tuning batch and the Prism ferry.

*Prepared by the audit synthesizer (honest-broker lead). All severities are the synthesizer's final calibration after cross-verification, and may differ from an individual auditor's initial rating where frequency/impact evidence warranted (e.g. D1 HIGH→MEDIUM after the timeout-frequency ferry).*
