# Plan — Migration 027_decision_core_state.sql + legacy retention (DEFERRED — Phase 2)

**Feature:** Phase-2 deferred — `decision_core_state` table for the new
engine's transient state (hysteresis, EU crossover, generator warm
state), plus the **legacy-engine retention contract** through the
20-trading-day post-cutover inverted-shadow window. **Ships only if the
Phase-2 entry gates pass.** The legacy-drop release is human-operator-
authorized only.

**Phase:** Phase 2 (Finalist B; evidence-gated).

**Owner agent-type:** `sqlite-specialist`, `quant-test-writer`,
`quant-code-reviewer`. Writer: the new engine in
`alpha_bot_execution.py`; legacy-drop release: human operator only.

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §5.3 (CVaR
  trigger design — two-level hysteresis band + multi-tick
  confirmation), §5.4 (Phase-2 persistence — `018_decision_core_state.sql`
  council-numbered).
- `docs/handoff/council-converged-migration-plan.md` §3.2 row 018, §6
  H6 (legacy retention — 20-trading-day inverted-shadow window;
  human-operator-authorized drop), §6 H7 (`run_monte_carlo` blast
  radius — graceful NULL when a symphony stops producing legacy
  scalar).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §B.6
  (the divergence REJECT — no `cvar_divergence` column anywhere
  including here).
- Codebase: `bot_state` single-row JSON blob precedent (the
  decision-core's state is walled FROM `bot_state`, not folded into
  it — council §3.2 row 018 binding).

## Why

The new engine carries transient per-cycle state — the hysteresis band
position, the multi-tick confirmation counter, the EU crossover
counter, the generator warm-state pointer. Council §3.2 row 018
binding: **this state lives in its own columnar table, NEVER inside
`bot_state`**. A bug in the legacy engine's `bot_state` reset cannot
wipe the new engine's state — and vice versa.

The retention contract is the second half: the legacy engine and all
its tables (`bot_state` legacy fields, `run_monte_carlo`,
`exit_triggers`) stay live and untouched through the entire shadow +
per-symphony-cutover period + 20 trading days post-cutover. The legacy
drop is a separate, **human-authorized**, release.

## Numbering

Council `018_decision_core_state.sql` → codebase
`027_decision_core_state.sql` (Phase-2 renumbering — see
`025-shadow-decisions`).

## Deliverables

1. **`migrations/027_decision_core_state.sql`** — `CREATE TABLE IF NOT
   EXISTS decision_core_state`:
   - `symphony_id            TEXT NOT NULL`
   - `account_id             TEXT NOT NULL`  (multi-account
     discriminator; `port_state` precedent)
   - `cvar_breach_ticks      INTEGER NOT NULL DEFAULT 0`
   - `eu_crossover_ticks     INTEGER NOT NULL DEFAULT 0`
   - `hysteresis_state       TEXT NOT NULL DEFAULT 'idle'`  (idle |
     watch | arm | confirmed — the state-machine label)
   - `generator_warm_state   TEXT`  (JSON; the pre-open warm pointer)
   - `last_updated_utc       TEXT NOT NULL DEFAULT (datetime('now'))`
   - `PRIMARY KEY (symphony_id, account_id)`
2. **`database.py` — `wipe_decision_core_state(symphony_id,
   account_id)` accessor.** The analogue of the existing
   `wipe_transient_state` for `bot_state` legacy fields; resets the
   row to defaults on a position-lifecycle boundary. **Wipes ONLY the
   new engine's columns** — never touches `bot_state` (council §3.2
   row 018 binding).
3. **`_MIGRATION_FILES`** — append `"027_decision_core_state.sql"`.
4. **No `init_db()` mirror.** New table; H1 zero exposure.
5. **Legacy retention contract** — a documentation block in this plan
   declaring:
   - **No legacy tables are dropped in Phase 2.** Migrations
     `001`–`021` (the Phase-1 floor and all pre-existing tables) stay
     present.
   - **The 20-trading-day post-cutover window is enforced by
     calendar, not by schema.** No SQL trigger; the operator's
     runbook tracks the date.
   - **The legacy-drop migration** (`028_legacy_drop_<symphony>.sql`)
     is a **per-symphony**, **human-authored**, **human-merged**
     migration. It is **never** drafted by an agent and **never**
     applied without explicit operator sign-off. The §6 H6 binding is
     codified by **leaving the legacy-drop migration unauthored** in
     Phase 2's scaffold.
6. **Fixture refresh** — seed one row per (symphony, account)
   covering the four `hysteresis_state` labels (`idle`, `watch`,
   `arm`, `confirmed`); the wipe accessor test asserts a wipe resets
   to `idle`.

## Dependencies

- **Hard-depends on Phase-1 spine** (the spec-bundle frozen-eval wall
  applies to any Advisor query of this table).
- **Phase-2 entry gates must pass** (council §5.7).
- **Soft-coupled to `025_shadow_decisions.sql`** —
  `shadow_decisions.hysteresis_snapshot_json` captures a point-in-time
  read of this row.

## Golden-fixture tests required (RED before GREEN)

1. **`(symphony_id, account_id)` PK enforced** — a second insert with
   the same PK raises `IntegrityError`; the writer uses `INSERT OR
   REPLACE` semantics or an explicit `UPDATE`. The choice is
   implementer's; the test asserts the PK does not silently
   duplicate.
2. **Wipe accessor resets to defaults** — after
   `wipe_decision_core_state`, the row has
   `cvar_breach_ticks=0`, `eu_crossover_ticks=0`,
   `hysteresis_state='idle'`, `generator_warm_state IS NULL`.
3. **Wipe DOES NOT touch `bot_state`** — a fixture with a
   `bot_state` row alongside a `decision_core_state` row; wipe the
   latter; assert the former is byte-identical before and after.
   **This is the council §3.2 row 018 binding's structural test.**
4. **`hysteresis_state` is application-level enum** — no SQL CHECK
   constraint (codebase convention); the writer's enum is
   `('idle','watch','arm','confirmed')`; a property test on the
   writer enforces.
5. **Legacy retention — no `DROP TABLE` in any Phase-1/Phase-2
   migration file.** A grep test on `migrations/*.sql` rejects
   `DROP TABLE` (the H6 structural guard against an agent-authored
   legacy drop).
6. **No `cvar_divergence` column on this table either** (§B.6
   binding — the grep test from plan `021-cvar-diagnostics`
   extends to this table).
7. **Schema-validator test** — fixture DB has the table + all
   columns; one row per `hysteresis_state` label.
8. **H7 graceful-NULL `mc_history`** — a sibling test that exercises
   the chart-history `mc_prob` field for a symphony that has cut over
   and no longer produces a legacy MC scalar; the read accessor
   returns NULL/empty cleanly, never crashes. Council §6 H7 binding.

## Definition of Done

- Migration applies cleanly; fixture DBs rebuilt; all four hysteresis
  labels covered.
- All eight tests pass GREEN.
- `pytest tests/` full tree passes.
- The legacy retention runbook entry exists (referenced by this plan;
  authored at human-operator handoff time).
- The grep guard rejects `DROP TABLE` in any agent-authored migration
  file.

## Risk callouts

- **`bot_state` ISOLATION IS THE BINDING DESIGN.** Council §3.2 row
  018: "**Never inside `bot_state`** — the new engine's state is
  walled from the legacy engine's so a bug in one reset cannot wipe
  the other." Test §3 is the structural enforcement. A future PR that
  proposes folding `decision_core_state` columns into the `bot_state`
  JSON blob to "simplify" is **rejected** — the dual-reset isolation
  is the entire point.
- **`hysteresis_state` MUST be persisted, not memory-only.** A daemon
  restart mid-arm without persistence loses the arm count and the
  symphony silently degrades to "no co-signal" until the arm
  re-accumulates. Persistence prevents the silent fail.
- **H6 — legacy drop is HUMAN-OPERATOR-AUTHORIZED ONLY.** Council §6
  H6 verbatim: "a destructive irreversible schema change is the
  highest-stakes change and cannot be council- or agent-authorized."
  The §5 grep guard refuses to ship an agent-authored `DROP TABLE`;
  the legacy-drop migration is left **unauthored** in this scaffold.
- **H7 — `run_monte_carlo` blast radius.** At per-symphony cutover,
  the `mc_history` buffer and the chart-history `mc_prob` field must
  gracefully handle NULL/empty. Test §8 is the existence proof. The
  fix lives in `alpha_bot_execution.py` (risk-engine-specialist
  domain); this plan provides the structural test.
- **20-trading-day post-cutover window** — council H6: 20 =
  `PURGE_DAYS` in `autotuner.py`, the codebase's existing
  regime-settling constant. Reusing it ties the post-cutover window
  to the same regime-settling discipline; a different constant would
  invent a new period for no reason.
- **The dashboard surfaces divergence-rollup over the 20-day
  window** — the operator reads this to sign the legacy-drop
  release. `flask-dashboard-specialist` owns the rendering; this
  plan owns the underlying tables that feed it.

## Out of scope

- The hysteresis state-machine logic (`alpha_bot_execution.py` /
  `math_engine.py`; risk-engine-specialist).
- The CVaR co-signal computation (`math_engine.py`).
- The pre-open generator warm-state derivation (plan
  `026-path-generator`).
- The legacy-drop migration itself (deliberately unauthored in
  Phase 2; ships only on human-operator authorisation, per H6).
- The dashboard divergence-rollup rendering
  (`flask-dashboard-specialist`).
