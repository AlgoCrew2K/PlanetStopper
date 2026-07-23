# Feature Plan — Advisor Weekly Automatic Suggestions + Wiring Fixes

**Status: ready**
**Branch:** `fix/advisor-rewire` (off origin/main `ec52a49`)
**Ships:** DIRECT to origin/main after PM live E2E gate (advisory-only — no `LIVE_EXECUTION`, no trade path — so NO PR per operator rule).
**Scope source:** `scratchpad/rewire_scope.md` (recon @ ec52a49) + `scratchpad/audit_lane_{a,c,d}.md`.

## Summary
Planet Stopper's advisor built four suggestion capabilities that were left dead or silently broken:
the weekly Strategy-Builder scheduler (broken dedup + no timer), the auto asset-swap / logic-change
suggestion engines (functional but never called), the lens-evidence blend (mathematically inert), and
the operator-drift overfitting indicator (unfireable — missing DB writer). The operator's mandate is to
**WIRE these to their intended function — not delete them** — with **weekly automatic suggestions the
centerpiece**. This cycle restores all of them to actually-functional-on-real-data.

## Acceptance Criteria

### Workstream E — Indicator-3 operator-drift writer (`overfitting_conscience` s_count)
- **AC-E1:** `database.save_autotune_run` gains `s_count: int | None = None`; the INSERT
  (`database.py:693-698`) writes it into the existing `autotune_runs.s_count` column (already present,
  migration `023_autotune_runs_s_count.sql`). The table stays append-only — no UPDATE path introduced.
- **AC-E2:** `autotuner.py` hoists the DoF-ledger sum (`SELECT evidence_source, n_configs_searched … WHERE
  spec_bundle_id = ?`, currently at ~`autotuner.py:2860-2865`, computed too late) to BEFORE the
  `save_autotune_run` call (~`autotuner.py:2837`) and passes the summed S as `s_count=`. The current-run
  I-1/I-2 S computation and verdicts are unchanged (control-flow order change only).
- **AC-E3:** Given ≥2 prior BACKTEST_SELECTION runs for a symphony with non-NULL, increasing `s_count`,
  `overfitting_conscience` sets `drift_signal_available=True` and Indicator-3 fires (WATCH-floor verdict).
  The consumer (`overfitting_conscience.py:121-137`) is NOT modified — it is already correct.
- **AC-E4:** Historical rows with `s_count IS NULL` are tolerated (drift needs ≥2 non-NULL priors; NULLs
  are skipped, never crash).

### Workstream A — Dedup fix + suggestion surfacing
- **AC-A1 (dedup):** `strategy_builder_scheduler._already_ran_this_week` (`strategy_builder_scheduler.py:59-63`)
  calls `database.get_advisor_observations_for_role("STRATEGY_BUILDER", limit=…)` (real signature at
  `database.py:1133`) instead of the mis-kwarg'd `get_advisor_observations_for_symphony`. Same-ISO-week
  dedup returns True when a STRATEGY_BUILDER row exists this week; NO swallowed TypeError.
- **AC-A2 (surfacing):** `_ADVISOR_ROLES` (`app.py:5037-5043`) includes `"ASSET_SWAP"` and `"LOGIC_CHANGE"`;
  the Overview observation feed renders rows of both roles; `_ROLE_LABELS` (`templates/ai_advisor.html:2182`)
  gains human labels for both. Reuses the existing generic card renderer — no new panel/tab.
- **AC-A3 (regression):** A `symphony_id=""` STRATEGY_BUILDER row surfaces automatically in the Strategy
  Builder tab (already role-filtered/unscoped at `app.py:4069-4076`); a test pins this so the weekly
  convention stays visible.

### Workstream D — Lens-blend efficacy (`asset_swap_engine._apply_lens_blend`, lines 372-433)
- **AC-D1:** The blend combines lens evidence with the CONTINUOUS primary score (normalized per-objective —
  correlation / drawdown / Sharpe are on different scales) BEFORE any rank discretization. It must NOT sort
  on the integer `enumerate()` position (the current inert design at `asset_swap_engine.py:422-432`).
- **AC-D2 (invariant):** A lens-favored candidate whose primary-score gap to its neighbor is SMALL CAN move
  up ≥1 rank; a candidate differing by a LARGE primary margin CANNOT be inverted by lens evidence
  ("supporting evidence only, never override"). Encoded as adversarial fixtures.
- **AC-D3:** The downstream BHY-FDR gate (`evaluate_candidate_batch`) output is unchanged for a fixed
  candidate set (pre-backtest reordering changes only try-order, not FDR math).
- **AC-D4 (RED-first):** A RED test first proves the CURRENT inertness (lens scores cannot change order at
  ec52a49 — the existing `test_lens_scores_reranks_candidates` does not actually assert this), then GREEN.

### Workstream C — Weekly auto-suggestion callers (`suggest_swaps` / `suggest_logic_changes`)
- **AC-C1 (logic-change loop):** `run_weekly_logic_change_suggestions()` enumerates live symphonies
  (`database.load_state()`), resolves each Composer hash, fetches its score tree, and calls
  `suggest_logic_changes(symphony_id, score_tree, objective, …)`. Per-symphony D-1 containment — one
  symphony's failure does not block the others. Rows persist as `LOGIC_CHANGE` (engine already does, at
  `logic_change_engine.py:827-843`).
- **AC-C2 (asset-swap loop):** `run_weekly_asset_swap_suggestions()` — same enumeration; assembles the
  ticker-level return-series `correlation_data` via a `synthetic_history.fetch_bars`-style step over a
  candidate pool from `universe_provider.get_tradeable_set()`; objective defaults to `reduce_correlation`
  (v1); calls `suggest_swaps(...)`. `lens_scores` is wired through (via `extract_lens_scores`) ONLY after D
  is GREEN. Per-symphony D-1; rows persist as `ASSET_SWAP` (`asset_swap_engine.py:723-764`).
- **AC-C3:** The engines themselves are UNCHANGED (their existing tests stay green) — this is purely the
  caller/loop layer that did not exist.
- **AC-C4:** No `LIVE_EXECUTION` interaction; advisory-only — `insert_advisor_observation` forces
  `is_advisory_only=1` (`database.py:1069-1070`).

### Workstream B — Weekly orchestrator + droplet timer
- **AC-B1:** New `advisors/weekly_suggestions_scheduler.py::run_weekly_suggestions()` calls, in sequence,
  each wrapped in its own D-1 try/except (one failure never blocks the next): (1)
  `strategy_builder_scheduler.run_weekly_build()`, (2) `run_weekly_asset_swap_suggestions()`, (3)
  `run_weekly_logic_change_suggestions()`. Invokable via `python -m advisors.weekly_suggestions_scheduler`.
  Do NOT overload `strategy_builder_scheduler.py` (Strategy-Builder-only per its AC-18 scope).
- **AC-B2:** Same-ISO-week idempotency per engine — the swap/logic loops get their own dedup guard
  (role-filtered, like A.1's fix) so a re-run in the same week does not duplicate suggestions.
- **AC-B3:** systemd oneshot service + weekly timer (`OnCalendar=*-*-* Mon 04:00 America/New_York`,
  `Persistent=true`) + a `docs/DEPLOYMENT.md` section, mirroring the Prism unit pattern
  (`docs/DEPLOYMENT.md:238-268`). `EnvironmentFile=/opt/planetstopper/.env` ONLY — no council-env /
  OAuth-token (SDK path, metered `ANTHROPIC_API_KEY`). Runs as non-root `planetstopper`.
- **AC-B4:** D-1 never-raises; per-engine bounded; a documented `MAX_BUDGET_USD`-style guard consistent
  with the existing schedulers. (Droplet registration of the timer is a PM gated DEPLOY step — the team
  ships the unit files + docs, not a droplet change.)

## Architecture
- **One orchestrator, three engines** (recon §B.3): a new `advisors/weekly_suggestions_scheduler.py` calls
  the existing `run_weekly_build()` (SB) plus two new per-symphony loop functions for swaps and logic. All
  three share the D-1 / bounded-retry / `.env`-credential shape; per-engine try/except gives the same
  blast-radius isolation as separate timers without the ops overhead.
- **Surfacing reuse** (recon §A): Strategy Builder already aggregates by role unscoped-by-symphony;
  asset-swap/logic-change plug into the generic Overview feed via `_ADVISOR_ROLES`. No accept/reject —
  advisory-only display + the existing "Discuss" chat affordance (`advisor_chat.explain_artifact`).
- **Lens-blend on continuous score** (recon §D): normalize primary metric + lens score to a comparable
  scale per objective, sort by `primary - LENS_BLEND_WEIGHT * lens`, never by integer position.
- **s_count via hoist** (recon §E, option (a)): move the ledger-sum earlier in `autotuner.py`, pass as a
  new `save_autotune_run(s_count=…)` kwarg — additive, no UPDATE on the append-only table.

## Edge Cases
- Symphony enumeration returns empty / a symphony has no resolvable Composer hash → skip, continue.
- One symphony's Composer `/score` or bar fetch fails mid-loop → D-1 contained, next symphony proceeds.
- Composer `/backtest` 1-req/s ceiling: 4 SB objectives + N-symphonies × (swap + logic) may exceed one
  oneshot window → if too slow, split into two timers offset by an hour (flag; not a blocker).
- Objective-scale normalization: correlation, drawdown, Sharpe differ by orders of magnitude — normalize
  per objective before the blend (min-max vs z-score is the team's call, pinned by fixtures).
- ISO-week boundary for dedup; NULL `s_count` priors; re-run idempotency.

## Security Considerations
- Advisory-only: no `LIVE_EXECUTION`, no order path, `is_advisory_only=1` forced on every persisted row.
- No new credentials — reuse `ANTHROPIC_API_KEY` (.env, SDK), Alpaca paper keys, Composer hash. No OAuth
  token / council-env (this is not a `claude -p` subprocess).
- No new write routes — surfacing is read-only display; CSRF surface unchanged.
- systemd unit runs as non-root `planetstopper` from `/opt/planetstopper`.

## Testing Strategy
- **TDD RED-first per workstream** (Toxic Pair). Hermetic — NO live network in tests (schema-derived
  fixtures + runtime validator per project rule; capture skill refuses write verbs). Bounded `-n0` through
  `ALPHABOT_TEST_MEM_CAP_GB` — NEVER full/uncapped/-n>4.
- D: RED proves inertness at ec52a49, then the efficacy invariant (AC-D2). E: RED asserts drift fires with
  ≥2 non-NULL priors. C: per-symphony D-1 isolation (one failure doesn't abort the loop). A: both roles
  surface in the tab's context.
- **PM live E2E gate (real data, not tests-green):** run `run_weekly_suggestions()` against the real
  environment; confirm it produces REAL, non-trivial suggestions from all three engines, that they SURFACE
  in the dashboard, that the lens blend actually moves a ranking on real lens data, and that Indicator-3
  fires on a symphony with prior runs.

## Scope Boundaries
- **F (lens_warehouse consumer) is OUT** of this cycle — deferred to a later, independent cycle; does not
  block weekly suggestions.
- No accept/reject/apply UI on any of the three roles (advisory-only display + chat-discuss, matching the
  existing Strategy Builder pattern).
- No bespoke ASSET_SWAP/LOGIC_CHANGE panel — reuse the Overview generic renderer (bespoke card treatment is
  a later stretch goal).
- Asset-swap v1 = `reduce_correlation` objective only (expand objectives later).
- Droplet timer REGISTRATION is a PM gated deploy step — the team delivers unit files + docs only.
- No market-cap weighting anywhere (Composer-deprecated).
