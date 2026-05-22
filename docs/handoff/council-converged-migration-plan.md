# Council — Converged Migration Plan (EUT+CVaR Decision-Science Roadmap)

**Author:** persistence-architect (sqlite-specialist)
**Date:** 2026-05-22
**Council:** decision-science-council · Branch: `design/decision-science-council`
**Status:** CONVERGED — debate-phase output. All five members agreed. Hand to `risk-architect` for the synthesis.
**Supersedes:** the flat migration list in `docs/handoff/council-persistence-layer.md` §4. That document's table designs stand; this document re-tiers them into the phased roadmap the debate converged on.

> This is the canonical schema artifact for the synthesis. It is design only — no production migration files are written here. It states, per migration: phase, contents, risk, and the implementation hazards the council surfaced.

---

## 1. The converged decision the schema serves

The debate converged on a **sequenced roadmap**, not a single-shot replacement:

- **Phase 1 — HARDEN-core + the overfitting-accounting spine.** Skeptic's HARDEN (M1 CRRA-EU offline objective, M2 CVaR *diagnostic* off the existing kNN pool, M3 re-derive two ad-hoc heuristic layers) shipped together with tuning-architect's overfitting-accounting spine (frozen-spec registry, researcher-degrees-of-freedom ledger, the `N_effective` haircut extension). No live CVaR trigger, no forward-path simulator. This is the council's recommended first deliverable.
- **Phase 2+ — the rich EUT+CVaR replace.** A net-new forward-path generator (regime-conditioned block bootstrap), a live hysteresis CVaR trigger, the paired shadow-decision log. **Evidence-gated**: Phase 2 is authorized only if M2's own diagnostic shows the CVaR carries incremental signal, and only after the Phase-2 entry gates pass (notably the pre-open batch latency budget — see §4).

The persistence layer mirrors this exactly: a small Phase-1 schema, a heavier Phase-2 schema designed-now-but-applied-only-on-the-gate.

---

## 2. Two-DB boundary and naming — settled

- **All new tables live in the STATE DB** (`alphabot_state.db`). **Zero optimization-DB (`optuna_studies.db`) migrations** in either phase. tuning-architect originally placed the spec registry / DoF ledger in the optimization DB and **withdrew that** during the debate: those objects are consumed by the AI Advisor and by the BHY haircut in `autotuner.py`, and `autotuner.py` already reads the state DB (`get_symphony_strategy`, `save_autotune_run`). State-DB placement keeps every Advisor query and every haircut read single-DB; optimization-DB placement would have forced a cross-DB join, violating the project two-DB rule. The haircut reads the `D_spec` count by **copying** it from the state DB into the autotune run, never cross-joining.
- **Migration filenames: single underscore** — `NNN_description.sql` — to match the live `_MIGRATION_FILES` list in `database.py`. The sqlite-specialist charter's `NNN__description.sql` (double underscore) wording is **wrong for this codebase**; `run_migrations()` applies the names in `_MIGRATION_FILES` and a double-underscore file would not be found. critic confirmed the codebase convention wins. (This was attack question Q8.)
- New files are **appended** to `_MIGRATION_FILES` in `database.py` in numeric order — never inserted mid-list, never reordered (the list's own contract, `database.py:719`).

---

## 3. The converged migration plan

### 3.1 PHASE 1 — ships with HARDEN-core + the accounting spine

Six migration files. Five create new tables; two are `ALTER` (021, 022). All additive-first.

| # | File | Contents | Type | Notes |
|---|---|---|---|---|
| 015 | `015_spec_bundles.sql` | `spec_bundles` (immutable hashed frozen-facet bundle: `bundle_hash` UNIQUE, `frozen_at`, `facets_json`, `horizon_bars`, `cvar_alpha`, `generator_family`) **+ `spec_facets`** child (one row per facet: `bundle_id` soft-FK, `facet_name`, `facet_value` JSON, `freeze_discipline`, `justification`, `calibration_evidence`) | 2 new tables | `facets_json` is the canonical hashable blob; `spec_facets` is the queryable projection. Both written in one transaction at freeze time; `spec_bundles` immutable after `frozen_at` — they cannot drift. Enables `SELECT facet_name FROM spec_facets WHERE freeze_discipline='BACKTEST_SELECTION'`. |
| 019 | `019_advisor_observations.sql` | `advisor_observations` — append-only, immutable, no update/delete accessor (the `llm_suggestions` precedent). `advisor_role`, `subject_type`, `subject_id`, `raw_response` JSON, `is_advisory_only` hard-wired `1`. | 1 new table | **Phase 1 unconditionally.** In Phase 1 it holds the **computed** Overfitting-Conscience verdict (`D_spec=1, N_effective=N_optuna, no violations`) — one row per autotune run. The LLM-authored advisor roles (Spec Critic, Divergence Explainer, Narrator) are Phase 2 — *same table, no schema change*; `advisor_role` discriminates, `raw_response` is `'{}'` for a computed row. |
| 020 | `020_researcher_dof_ledger.sql` | `researcher_dof_ledger` — append-only, immutable. `facet_name`, `facet_category` (specification\|parameter), `decision_type` (FIXED\|SEARCHED\|REVISED\|OOS_PEEK), `evidence_source` (THEORY\|MANDATE\|STYLIZED_FACT\|BACKTEST_SELECTION\|OOS), `n_configs_searched`, `touched_frozen_eval`, `spec_bundle_id` soft-FK. | 1 new table | Phase 1 holds ~3-4 rows (the theory-frozen Phase-1 facets). Must exist **from Phase 1** — a retrofitted ledger cannot reconstruct a pre-existence freeze decision; the Overfitting Conscience must count from a clean start. Feeds `N_effective` (§5). |
| 021 | `021_fold_role.sql` | `ALTER` — adds `fold_role TEXT DEFAULT NULL` to the fold-partitioned replay table(s). | 1 ALTER (1 column) | **Phase 1.** The frozen-eval **wall** must exist the moment a facet is frozen (gamma freezes in Phase-1 M1) — a wall retrofitted in Phase 2 cannot certify a Phase-1 freeze was clean. **SQL-NULL trap:** the wall filter must be `WHERE COALESCE(fold_role,'') != 'frozen_eval'`, NOT `WHERE fold_role != 'frozen_eval'` — a bare `!=` evaluates NULL→falsy and would silently hide legitimate train/validation rows. |
| 022 | `022_autotune_runs_eut.sql` | `ALTER` — adds 8+1 columns to `autotune_runs`: `spec_bundle_id`, `d_spec`, `n_effective`, `ce_metric`, `cvar_feasible`, `gamma`, `lambda_budget`, `overfitting_verdict`, `paired_heuristic_study_name`. All NULLable + DEFAULT. | 1 ALTER (9 columns) | **Phase 1.** The `N_effective` haircut runs in `autotuner.py` in Phase 1. Historical heuristic rows legitimately read NULL. `paired_heuristic_study_name` lets `optuna-compare` diff an EUT study against its paired heuristic study by name (single-DB-per-read). **Q7 HAZARD — see §6.** |
| 023 | `023_cvar_diagnostics.sql` | `cvar_diagnostics` — NEW 6-column table: `cycle_id`, `ts_utc`, `symphony_id` (`account_id`), `cvar_5pct`, `cvar_5pct_stderr`, `cvar_n_tail`. One row per (cycle, symphony), **HOLD and EXIT cycles both**. | 1 new table | **Phase 1.** Home for skeptic's M2 CVaR diagnostic. It is a NEW table — **not** a column-add — because there is **no per-cycle decision row** in the state DB (decisions live in the `bot_state` JSON blob; `exit_triggers` logs exits only). M2's evidence question is "was CVaR elevated on cycles the engine HELD through" — that needs HOLD rows. No per-row bias flag (estimator bias is a fixed documented property). |

**Phase-1 totals:** 5 new tables (`spec_bundles`, `spec_facets`, `advisor_observations`, `researcher_dof_ledger`, `cvar_diagnostics`) + 2 `ALTER` migrations (`021`, `022`) = 6 migration files. All small: largest is `researcher_dof_ledger` at ~10 columns; `spec_facets` and `cvar_diagnostics` are 6 columns; `fold_role` is one column. ~6-8 indexes total. One fixture refresh (§7).

**Framing for the trade-off table (council-agreed, skeptic's reframe):** of the 6 Phase-1 migrations, FIVE are the overfitting-**accounting/provenance** spine — they are **not migration overhead, they ARE the defensibility deliverable** the user's binding motivation asked for. Only `cvar_diagnostics` is M2-runtime. The honest framing is *"HARDEN's defensibility upgrade is partly delivered AS provenance tables — the user is paying for provenance and getting provenance."*

### 3.2 PHASE 2+ — applied only if the evidence gate unlocks the live-CVaR replace

Three migration files; one creates two tables. All heavy runtime-state tables. Designed now, applied later.

| # | File | Contents | Type | Notes |
|---|---|---|---|---|
| 016 | `016_shadow_decisions.sql` | `shadow_decisions` — the paired legacy-vs-new decision log, one row per (cycle, symphony[, account]). ~25 columns: `legacy_action`/`legacy_reason`, `shadow_action`/`shadow_reason`, `decisions_agree` (denormalised + indexed), `cvar_estimate`/`cvar_std_error`/`cvar_breach`, `eu_hold`/`eu_exit`/`eu_margin`, `spec_bundle_id` **NOT NULL**, `mc_seed` **NOT NULL**, `generator_calib_id`, `hysteresis_snapshot_json`. 3 indexes. Optionally `shadow_inputs` IF a deferred-compute (Shape-B) generator is chosen. | 1 (or 2) new tables | Heavy. `spec_bundle_id`/`mc_seed` are `NOT NULL` — safe because a NOT NULL on a brand-new `CREATE TABLE` (no pre-existing rows) is fully additive-compliant; a shadow decision with no spec bundle or seed is unreplayable and must fail loud at write. `shadow_inputs` is needed only for a non-pre-sim generator; a pre-sim-bank architecture (risk-architect's) does inline (Shape-A) compute and does not need it. |
| 017 | `017_path_generator.sql` | `path_generator_calibrations` (calibration params JSON blob, `history_fingerprint`, `n_tail_observations`, `superseded_at_utc`) **+ `path_bank_manifest`** (`regime_fingerprint`, `bank_file_path`, `bank_sha256`, `tier1_seed`, `built_at_utc`, `superseded_at_utc`). | 2 new tables | Heavy. **The pre-simulated path bank itself is a FILE CACHE (`.npy`), NOT a state-DB blob** — ~40 MB/day of regenerable floats would bloat the WAL and every backup. `path_bank_manifest` holds only ~200-byte metadata rows + the file path. Matches the `synthetic_history.py` file-cache precedent. `tier1_seed` (= `SHA-256(symphony_id ‖ trading_day ‖ spec_bundle_hash)`) is the load-bearing replay anchor — the Tier-1 bootstrap is where the randomness is; a bank from an unpersisted seed fails Gate 1. |
| 018 | `018_decision_core_state.sql` | `decision_core_state` — columnar, PK `(symphony_id, account_id)`. The new engine's transient state: `cvar_breach_ticks`, `eu_crossover_ticks`, `hysteresis_state`, `generator_warm_state`. Daily-reset helper (the `wipe_transient_state` analogue). | 1 new table | Heavy — a live hysteresis state machine. **Never inside `bot_state`** — the new engine's state is walled from the legacy engine's so a bug in one reset cannot wipe the other. |

**Phase-2 totals:** 3 migration files, 4 new tables (`016`, `017`×2, `018`), all heavy runtime-state. Plus the path-bank `.npy` file cache (with its own retention/prune). Plus the five-determinism-anchor replay surface (seed, calibration, history fingerprint, spec bundle, hysteresis snapshot).

---

## 4. Phase-2 ENTRY GATES (must pass before Phase 2 is built)

The debate established these as hard preconditions, not design details:

1. **M2 evidence gate.** Phase 2 is authorized only if HARDEN's M2 CVaR diagnostic shows the CVaR carries incremental signal beyond the existing heuristic stack. If M2 shows nothing, Phase 2 may never be built — and the Phase-1 schema is the permanent end state.
2. **Pre-open batch latency budget** (risk-architect's accepted BREAK-2). The Tier-1 batch (block-length selection + regime bucketing + path-bank pre-simulation, all symphonies) must be **measured by a prototype** and proven to finish with margin before the first `:00` cycle — otherwise the engine runs blind at the open. If it cannot be bounded, the two-tier architecture must change (e.g. stagger the batch the prior evening). This cannot be budgeted from a design doc.
3. **Q1-Q14 checklist** (the persistence-layer attack questions from `council-persistence-layer.md` §6) — every one answered before the Phase-2 schema is built.

---

## 5. `N_effective` — the haircut consumer (zero schema impact)

The multiplicative form `N_effective = N_optuna × D_spec` was **retracted** during the debate (over-penalizes). The converged form is **additive and conservative**:

> `N_effective = N_optuna + Σ` (over P&L-toured-but-not-selected spec bundles) `of n_configs_searched`

`D_spec` = `COUNT(DISTINCT spec_bundle_id)` in `researcher_dof_ledger` where `evidence_source='BACKTEST_SELECTION'` — a bundle-distinct-count, **not** a sum of `n_configs_searched`. Both `d_spec` (stores `K`, the distinct P&L-toured count) and `n_effective` (the additive result) are columns on `autotune_runs` (migration 022). **Schema impact of the multiplicative→additive correction: zero** — same table, same `n_configs_searched` column, corrected consumer arithmetic only.

---

## 6. Implementation hazards — numbered notes for the synthesis

**H1 — Migration 022 dual-write (the Q7 hazard).** `run_migrations()` (`database.py:770-779`) catches `duplicate column name` and marks a migration applied **without running it**. `autotune_runs` columns are inlined in `init_db()`'s `CREATE TABLE` *and* added by prior migrations. Therefore migration 022's 9 new columns **must be added to BOTH** the `022_autotune_runs_eut.sql` `ALTER` statements **AND** the `init_db()` `CREATE TABLE autotune_runs` statement in `database.py`. A fresh DB gets them inline; an upgraded DB gets them via 022; the duplicate-column swallow reconciles the overlap. Omitting `init_db()` → fresh DBs lack the columns. Omitting 022 → upgraded DBs lack them. This is a numbered implementation STEP, not a footnote.

**H2 — Migration 023 is a fresh `CREATE`, no Q7 exposure.** `cvar_diagnostics` is a new table created with `CREATE TABLE IF NOT EXISTS`. The duplicate-column swallow only fires on `ALTER TABLE ADD COLUMN`. No collision surface. (This is *why* M2's columns are a new table, not an `ALTER` on `exit_triggers` — an `ALTER` would re-expose H1.)

**H3 — `fold_role` SQL-NULL trap.** The frozen-eval wall filter must be `WHERE COALESCE(fold_role,'') != 'frozen_eval'`. A bare `WHERE fold_role != 'frozen_eval'` evaluates a NULL `fold_role` to NULL/falsy and silently excludes the row — hiding legitimate train/validation data from the Advisor.

**H4 — Telemetry-swallow vs replay-determinism.** The shadow/diagnostic write helper takes an explicit `live | replay` mode (the `is_live`-explicit discipline). **Live mode:** swallow-on-fail (a telemetry failure must never fail a live cycle — the `record_exit_trigger` precedent). **Replay mode:** raise on write failure — a replay that cannot persist its decision record is broken and must fail loud, not produce a partial record. Separately, the Gate-1 bit-identical parity assertion compares **decision-content columns only** (`cvar_5pct`/`cvar_5pct_stderr`/`cvar_n_tail`, or in Phase 2 `shadow_action`/`cvar_estimate`/`eu_margin`) and **explicitly excludes** telemetry incidentals (`id`, `ts_utc`, `built_at_utc`) so a different autoincrement id or wall-clock never falsely fails parity.

**H5 — Reversibility taxonomy.** Every 015-023 migration is additive-first (safe to apply, safe to leave). But "reversible" differs by type: a **new-table** migration (015, 016, 017, 018, 019, 020, 023) can be reversed by dropping the unused table if absolutely necessary — there is no replacement being switched to, so the add→backfill→switch→drop ceremony does not apply. An **`ALTER`** migration (021 `fold_role`, 022 `autotune_runs`) is **abandon-in-place only** — the column is permanent once shipped (charter anti-pattern: never drop a column in the migration that added it); "reversible" means the *feature* is reversible (revert the accessor code), not the schema.

**H6 — Legacy-engine retention (rubric J-3).** The legacy engine and ALL its tables (`bot_state` legacy fields, `run_monte_carlo`, the kNN MC path, `exit_triggers`) stay live and untouched through the entire shadow + per-symphony-cutover period. The destructive add→backfill→switch→drop of legacy tables begins only after BOTH: (a) the LAST symphony has cut over, AND (b) a **20-trading-day post-cutover observation window** closes clean (20 = `PURGE_DAYS` in `autotuner.py`, the codebase's existing regime-settling constant), during which the legacy engine runs as the **inverted shadow**. The legacy-drop release is **human-operator-authorized only** — a destructive irreversible schema change is the highest-stakes change and cannot be council- or agent-authorized. The persistence layer's job is to make the 20-day divergence rollup visible so the operator can sign off on evidence.

**H7 — `run_monte_carlo` blast radius.** `run_monte_carlo`'s signature/return type is **frozen** through both phases until the LAST symphony cuts over. The Phase-2 forward-path generator is a **net-new function** (`simulate_forward_paths`), not a mutation of `run_monte_carlo` — the 7+ legacy consumers keep reading the legacy scalar unchanged. At per-symphony cutover, the `mc_history` buffer and the chart-history `mc_prob` field must gracefully handle a symphony that stops producing a legacy scalar (NULL/empty, not crash) — a Phase-2 cutover implementation note.

---

## 7. Fixture-update obligation

Charter rule: a schema diff with no fixture update breaks the test suite silently. Each phase's migrations carry a **mandatory fixture refresh in the same PR**:

- **Phase 1:** `tests/fixtures/` gains seed rows for `spec_bundles`, `spec_facets`, `advisor_observations`, `researcher_dof_ledger`, `cvar_diagnostics`, and the `fold_role` column / `autotune_runs` EUT columns. The fixture builder runs `run_migrations()` so fixtures carry 015-023.
- **Phase 2:** seed rows for `shadow_decisions`, `path_generator_calibrations`, `path_bank_manifest`, `decision_core_state`.
- A runtime validator test opens each fixture DB and asserts the expected tables/columns exist — that test IS the guard against silent schema/fixture drift.
- Fixture provenance: the new tables are producer-owned by the engine code — fixtures must be schema-derived with the runtime validator or captured from the producer, never hand-authored to match a parser.

---

## 8. Summary for the synthesis trade-off table

| Line | Finalist A — HARDEN-core + accounting spine (Phase 1) | Finalist B — full EUT+CVaR replace (Phase 1 + Phase 2) |
|---|---|---|
| **Migration files** | 6 (015, 019, 020, 021, 022, 023) | 6 + 3 (016, 017, 018) = 9 |
| **New state-DB tables** | 5, all small (≤10 cols) | 5 + 4 heavy (`shadow_decisions` ~25 cols, `path_generator_calibrations`, `path_bank_manifest`, `decision_core_state`) = 9 |
| **`ALTER` migrations** | 2 (`021` fold_role, `022` autotune_runs) | same 2 |
| **Optimization-DB migrations** | 0 | 0 |
| **File cache** | none | the path-bank `.npy` cache (~40 MB/day, needs retention/prune) |
| **Replay determinism anchors** | 1 (M2 CVaR off the `cycle_id`-seeded kNN pool) | 5 (seed, calibration, history fingerprint, spec bundle, hysteresis snapshot) |
| **Pre-open latency gate** | none | mandatory Phase-2 entry gate (measured prototype) |
| **Nature of the schema** | 5-of-6 are the provenance/defensibility deliverable; 1 is M2 runtime | + heavy live runtime-state tables |

**Count plus weight (council-agreed framing):** Phase 1 is 6 *small additive* migrations; Phase 2 adds 3 *heavy runtime-state* migrations. A raw "6 vs 3" count would falsely suggest Phase 1 is heavier — it is not. Show both the count and the qualitative weight so the council is misled in neither direction. The Phase-1 schema is the methodology/defensibility upgrade made auditable; the Phase-2 schema is the cost of a live forward-path simulator and a live CVaR trigger.

---

## 9. What is settled vs what the synthesis still decides

**Settled by the debate (do not re-open):**
- Phased roadmap; Phase 1 = HARDEN + spine; Phase 2 evidence-gated.
- All new tables in the state DB; zero optimization-DB migrations.
- Single-underscore migration filenames.
- `spec_bundles` + `spec_facets` + `researcher_dof_ledger` schema; `N_effective` additive form.
- `cvar_diagnostics` as a new 6-column table (M2's home).
- `run_monte_carlo` frozen until last cutover; the Phase-2 generator is net-new.
- The path bank is a file cache with a `path_bank_manifest` metadata row, not a WAL blob.
- 20-trading-day post-cutover window; legacy-drop is human-authorized.

**Left to the synthesis (`risk-architect` owns):**
- Whether to recommend Finalist A only, or A-then-B, or both as finalists.
- The Phase-2 path-generator family (block bootstrap vs GARCH-FHS) — `path_generator_calibrations.calibration_params` is a JSON blob and absorbs either; the schema is generator-agnostic.
- Whether the LLM-authored AI Advisor roles are Phase 2 (the `advisor_observations` table is Phase 1 regardless; only the LLM authorship is the open question).
- The pre-registered acceptance thresholds for Gate 1 (replay parity) and Gate 2 (shadow N-weeks-clean).
