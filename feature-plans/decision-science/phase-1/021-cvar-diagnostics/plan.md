# Plan — Migration 021_cvar_diagnostics.sql (M2 runtime home + §6.2 second-window residue)

**Feature:** Phase-1 home for M2's single-day CVaR diagnostic — one row per
cycle (HOLD and EXIT both). Includes the §B (v3 evaluation) **operator-
optional second-window residue** columns `cvar_5pct_long` and
`cvar_n_tail_long` as additive `DEFAULT NULL`.

**Phase:** Phase 1 (HARDEN floor — M2 is operator instrumentation; see
synthesis §3.1 + v3 evaluation §A.4 re-label).

**Owner agent-type:** `sqlite-specialist`, `quant-test-writer`,
`quant-code-reviewer`. Consumer: `alpha_bot_execution.py` execution-path
writer (`risk-engine-specialist`) + dashboard reader
(`flask-dashboard-specialist`).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §3.1 (M2 specification
  + S-3 four-part display contract), §3.7 (row 023 — M2's runtime home is a
  NEW table because the state DB has no per-cycle decision row).
- `docs/handoff/council-converged-migration-plan.md` §3.1 row 023, §6 H2
  (fresh `CREATE`, no Q7 hazard), §6 H4 (telemetry-swallow vs replay-
  determinism).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.2
  (H-2 — stderr on the distinct-tail-observation count, not the resample
  count), §A.3 (H-3 — "zero decision impact, non-zero non-blocking I/O
  cost"), §B.6 (the second-window residue + the BINDING constraint: no
  `cvar_divergence` or `regime_recency_weight` persisted column).
- Precedent: `database.py:1147-1194` `record_shadow_observation` (the
  swallowed, off-transaction per-cycle write pattern).

## Why

M2's evidence question is "was CVaR elevated on cycles the engine HELD
through" — that needs HOLD rows. The state DB has **no** per-cycle decision
row (`bot_state` is a single-row JSON blob; `exit_triggers` logs exits only;
council §3.7), so M2's diagnostic needs its own small table. A column-add
on `exit_triggers` would skip HOLD rows entirely (the evidence question
collapses); an `ALTER` on a pre-existing decision-row table is also the
H1-class hazard surface — a NEW table has zero H1 exposure (council §6 H2).

The §6.2 / §B.6 second-window residue is admitted **here, in this plan,
unconditionally** because the synthesis explicitly authorises it: two
additive `DEFAULT NULL` columns, each read independently under its own S-3
contract, **no `cvar_divergence` column ever surfaced**. The execution-path
cost is unchanged from a single-window M2 (one wider row, one INSERT,
council §B.6).

## Numbering

Council `023_cvar_diagnostics.sql` → codebase `021_cvar_diagnostics.sql`
(shift +1; plan `016-spec-bundles` carries the full table). This is the
**last Phase-1 migration**.

## Deliverables

1. **`migrations/021_cvar_diagnostics.sql`** — `CREATE TABLE IF NOT EXISTS
   cvar_diagnostics`:
   - `id                  INTEGER PRIMARY KEY AUTOINCREMENT`
   - `cycle_id             TEXT    NOT NULL`  (the execution cycle's id;
     same convention as `record_shadow_observation`)
   - `ts_utc               TEXT    NOT NULL DEFAULT (datetime('now'))`
   - `symphony_id          TEXT    NOT NULL`  (per-symphony writes; the
     codebase's existing `account_id`/`symphony_id` naming convention)
   - `cvar_5pct            REAL    DEFAULT NULL`  (M2's single-day 5% CVaR;
     NULL when insufficient — mirrors the `run_monte_carlo` / MC `None`
     sentinel)
   - `cvar_5pct_stderr     REAL    DEFAULT NULL`  (stderr on the **distinct
     tail-obs count**, NOT the resample count — §A.2 H-2 binding)
   - `cvar_n_tail          INTEGER DEFAULT NULL`  (the **distinct** tail-
     observation count, ~7-8; the auditable denominator for stderr — §A.2)
   - `cvar_5pct_long       REAL    DEFAULT NULL`  (operator-optional
     second-window CVaR; §B.6 residue, NEVER NOT NULL)
   - `cvar_n_tail_long     INTEGER DEFAULT NULL`  (distinct tail-obs count
     for the long window; §B.6 residue)
2. **`_MIGRATION_FILES`** — append `"021_cvar_diagnostics.sql"`.
3. **No `init_db()` mirror.** This is a NEW table; council §6 H2: the
   duplicate-column-name swallow does not fire on `CREATE TABLE IF NOT
   EXISTS`. H1 has zero exposure here. Adding to `init_db()` is **NOT
   recommended** because Phase-1's H1 audit lane (engine-audit plan) should
   show this migration as a `CREATE`-only zero-exposure path; a defensive
   `init_db()` mirror would add a maintenance surface for no safety gain.
4. **Indexes** (separate from the table create, in the same migration file,
   one comment each — charter Operating Rule 5):
   - `CREATE INDEX IF NOT EXISTS idx_cvar_diag_cycle ON
     cvar_diagnostics(cycle_id);`  -- accelerates replay-determinism
     re-fetch by cycle_id (Gate-1 parity assertion)
   - `CREATE INDEX IF NOT EXISTS idx_cvar_diag_symphony_ts ON
     cvar_diagnostics(symphony_id, ts_utc DESC);`  -- accelerates the
     dashboard "latest CVaR per symphony" read
5. **Fixture refresh** — seed rows that cover:
   - the **sufficient** case (`cvar_5pct` non-null, `cvar_5pct_stderr`
     non-null, `cvar_n_tail` = 8)
   - the **insufficient sentinel** case (`cvar_5pct IS NULL`,
     `cvar_n_tail < MC_MIN_HISTORY_DAYS`)
   - the **second-window populated** case (both single + long windows
     present)
   - the **second-window absent** case (long-window columns NULL)
   - **HOLD** and **EXIT** cycle rows both present (M2's evidence question
     needs both — council §3.7)
6. **Schema-validator test** — fixture DB has the table + indexes + all
   eight columns.

## Dependencies

- **No hard dependency on earlier Phase-1 migrations** — the table is
  self-contained.
- **Consumer (Phase 1):**
  - Writer in `alpha_bot_execution.py` (per-cycle write; uses the H4
    telemetry helper — plan `phase-1/h4-telemetry-helper`).
  - Dashboard reader in `app.py` (uses `get_ro_connection()`).
  - Replay-determinism re-fetch (plan `phase-1/replay-determinism-anchor`).
- **Consumer (Phase 2, deferred):** the M2 evidence-gate analysis itself —
  read-only consumer; no schema change.

## Golden-fixture tests required (RED before GREEN)

1. **Sufficient case round-trips** — INSERT a row with all single-window
   fields non-null; SELECT returns matching values.
2. **Insufficient sentinel — `cvar_5pct IS NULL`** — a write where MC is
   insufficient writes the NULL sentinel; the dashboard reader treats it as
   "insufficient" (no fabricated zero). Mirrors the `run_monte_carlo` /
   `CVaRAssessment` `None` sentinel convention (council §B.6 binding).
3. **`cvar_n_tail` is the DISTINCT count** — a writer test asserts the
   column equals the count of **distinct** sub-5% neighbour-day returns,
   NOT the resample count (default 5000). §A.2 H-2 binding.
4. **`cvar_5pct_stderr` is computed on `cvar_n_tail`** — a fixture with
   `cvar_n_tail = 7` and a known sample variance has a stderr within
   tolerance of the small-`n` value, NOT the `√(5000)`-shrunken value (the
   §A.2 H-2 false-precision failure case). The test runs both formulas
   explicitly and asserts the persisted stderr matches the small-`n` one.
5. **Second-window residue — `cvar_5pct_long`/`cvar_n_tail_long` default
   NULL** — a row inserted without supplying the long-window columns reads
   NULL on both. No fabricated zero.
6. **No `cvar_divergence` column exists** — a `grep` test in CI rejects any
   `cvar_divergence`, `regime_recency_weight`, or similar derived-signed-
   quantity column being added to `cvar_diagnostics` (§B.6 binding: the
   residue is "two honest numbers, each S-3-displayed independently"; a
   derived divergence column manufactures the detector affordance the §B
   REJECT explicitly removes).
7. **HOLD and EXIT both present** — fixture has at least one HOLD-cycle row
   and one EXIT-cycle row for the same `symphony_id`; the read accessor
   returns both.
8. **Index sanity** — `EXPLAIN QUERY PLAN` for the dashboard
   latest-CVaR-per-symphony query uses `idx_cvar_diag_symphony_ts`.
9. **Schema-validator test** — fixture DB has the table + both indexes +
   all eight columns.

## Definition of Done

- Migration applies cleanly; fixture DBs rebuilt with all four shape
  cases.
- All nine tests pass GREEN.
- `pytest tests/` full tree passes.
- The dashboard reader uses `get_ro_connection()`; the writer goes through
  the H4 telemetry helper (plan `phase-1/h4-telemetry-helper`).
- The CI grep test rejects `cvar_divergence` introduction.

## Risk callouts

- **§A.3 H-3 wording — "zero decision impact, non-zero non-blocking I/O
  cost".** M2 drives no trade; the per-cycle write is a swallowed-on-fail,
  off-transaction INSERT (the `record_shadow_observation` pattern,
  `database.py:1147-1194`). The H4 telemetry helper covers the
  live-mode-swallow / replay-mode-raise dichotomy. A benchmark obligation
  ships in the H-3 plan (`phase-1/shadow-logging-pattern`).
- **§A.2 H-2 — stderr on the distinct tail count is BINDING.** A stderr on
  the 5000-resample count understates the true estimation error by ~27×;
  this would convert S-3's honesty mechanism into a false-precision
  generator (exactly the S-3 element (d) bias warning's failure mode in
  reverse). The auditable denominator is `cvar_n_tail` (persisted as a
  first-class column precisely so a reviewer can confirm the stderr was
  not computed on the resample count).
- **§B.6 binding — NO signed-divergence column.** The second-window
  residue is two honest numbers, each S-3-displayed independently. A
  `cvar_divergence` column would manufacture the detector affordance the
  REJECT explicitly removes. The grep test (§6) is the structural
  enforcement.
- **NULL sentinel propagation.** `cvar_5pct IS NULL` is the
  insufficient-MC sentinel; the codebase's existing precedent is the
  `run_monte_carlo` `None` return + the `mc_available` companion flag (per
  the v3 evaluation §A.0 and the project memory
  `project_cluster5_d6_orphaned_red_triage`). The dashboard's S-3 display
  surface MUST treat the NULL case as "insufficient — diagnostic
  unavailable for this cycle," NOT as a zero CVaR.
- **The S-3 four-part display contract is enforced at the display
  surface**, not at the schema. The schema persists the inputs
  (`cvar_5pct`, `cvar_5pct_stderr`, `cvar_n_tail`); the dashboard
  renders all four parts (the value, the stderr, the tail-obs count, the
  "diagnostic, not a signal" label, AND the bias warning). Plan
  `phase-1/h4-telemetry-helper` does NOT cover the display contract; the
  flask-dashboard-specialist's display plan does (out of scope here).

## Out of scope

- The CVaR computation itself (Rockafellar-Uryasev general-distribution
  estimator on the existing kNN pool; lives in `math_engine.py`;
  risk-engine-specialist domain).
- The S-3 four-part display contract enforcement on the dashboard
  (`flask-dashboard-specialist` domain; the four-part contract is a
  rendering obligation, not a schema obligation — the schema just
  persists the inputs).
- The H4 telemetry helper itself (plan `phase-1/h4-telemetry-helper`).
- The non-blocking benchmark (plan `phase-1/shadow-logging-pattern`).
- The cycle_id-seeded RNG anchor (plan
  `phase-1/replay-determinism-anchor`).
- Phase-2 `shadow_decisions.cvar_estimate` — a different column on a
  different table (plan `phase-2/025-shadow-decisions`).
