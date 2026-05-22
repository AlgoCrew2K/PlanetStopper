# Feature: M2 — CVaR Diagnostic (S-3 four-part display + operator-optional second-window residue)

**Phase / Lane:** Phase 1 — HARDEN-core floor. **Operator instrumentation** (per H-4 re-label: M1 = defensibility win; M2 = operator instrumentation; both Phase 1, answering different motivations).
**Owner agent-type:** `risk-engine-specialist` (implementer) + `quant-test-writer` (RED) + `sqlite-specialist` (migration 023 + telemetry helper) + `flask-dashboard-specialist` (S-3 display surface) + `quant-code-reviewer` (review). A Pent for this cycle (the dashboard surface adds a UI specialist).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §2.3 (live CVaR trigger un-validatable — M2 is **diagnostic**, never a signal), §3.1 (M2 floor row: Rockafellar-Uryasev general-distribution CVaR off the EXISTING kNN pool that `run_monte_carlo` already builds), §3.4 (M2 single-day; no horizon problem at this scope), §3.8 (Gate 2 diagnostic-quality criteria), §3.9 W-H1 (M2's evidentiary ceiling is **KILL-or-INCONCLUSIVE**; M2 starts the evidence chain — but per H-4 below, only as a kill switch), §4 binding condition **S-3** (the four-part display contract).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.2 hole **H-2** (stderr on the **distinct-tail-observation count** ~7-8, NOT the resample count 5000), §A.3 hole **H-3** (M2's per-cycle write is "zero **decision** impact, non-zero non-blocking I/O cost" — not unqualified "zero"; route through H4 live|replay telemetry helper; benchmark vs minute budget), §A.4 hole **H-4** (M2 is **operator instrumentation, re-labelled** within Phase 1 — NOT demoted; M2 is a Phase-2 **kill-switch**, never a stepping-stone), §A.8 hole **H-8 A3** (Gate-1 parity column-exclusion list named — assert on `cvar_5pct`, `cvar_5pct_stderr`, `cvar_n_tail`, plus the §B columns if adopted, exclude `id` and `ts_utc`), **§B (CVaR-divergence REJECT)** §B.6 (operator-optional **second longer window**, each window read independently under its own S-3 contract; **NO signed-divergence quantity ever surfaced**; preferred **DISJOINT** baseline per §B.5).
- `docs/handoff/council-converged-migration-plan.md` §3.1 row 023 (`023_cvar_diagnostics.sql` — 6-column NEW table; HOLD and EXIT cycles BOTH; **NOT** an ALTER on `exit_triggers` — H2: a fresh CREATE has no Q7 H1 exposure), §6 hazard **H3** (`fold_role` SQL-NULL trap; M2's writer is not bound by H3 directly but the same `COALESCE` pattern guards any future fold-role filter on `cvar_diagnostics`), §6 hazard **H4** (live|replay-mode telemetry helper — **the binding routing path for M2's write**; live swallows on failure, replay raises).
- `docs/handoff/council-attack-rubric.md` Family **A** (A-1 ★ Rockafellar-Uryasev correct estimator; A-2 ★ non-finite propagation closure), Family **B** (B-1 ★ no blocking I/O on execution path — M2 must clear this benchmark), Family **D** ★ (D-1 golden fixture; D-2 non-circular provenance; D-3 deterministic stochastic output via `derive_cycle_mc_seed`), Family **F** (F-3 ★ shadow-mode display gate; F-4 ★ sentinel discipline mirror), Family **I** (I-3 ★ dashboard is read-only — M2's display NEVER an action surface).
- Code anchors: `math_engine.py:705-833` (`run_monte_carlo` — the kNN pool source; **frozen**, NOT mutated for M2 per H7); `math_engine.py:828-829` (the `rng.choice(nearest_day_returns, size=simulation_paths)` line — confirms with-replacement resample, why H-2 matters); `math_engine.py:695-702` (`derive_cycle_mc_seed`); `math_engine.py:71-74` (`MC_INSUFFICIENT_HISTORY_SENTINEL = None` — the out-of-band fail-safe pattern M2 must mirror); `database.py:1147-1194` (`record_shadow_observation` — the per-cycle self-opened-connection swallowed-exception precedent the H4 helper generalizes).

## Why (problem statement)

The Phase-1 deliverable for operator situational awareness is a **5% CVaR diagnostic** — computed every cycle, logged every cycle, displayed every cycle, **drives no trade**. It answers a question the existing instrument stack cannot: *"what is the conditional severity of the bottom 5% of the kNN pool, right now, on this cycle?"* — a number an operator can read off the dashboard to know whether the regime is producing fat tails the heuristic stack is not directly measuring.

Three council corrections shape the design:

1. **A live CVaR *trigger* cannot be validated** at AlphaBot's data scale (~6 tail days per 125-day fold; ~37 per 3 years; vs the ~1,000 tail-relevant observations a joint VaR-ES backtest needs — council synthesis §2.3). M2 is therefore strictly diagnostic — it computes, logs, displays; it never moves money. A wrong M2 number misleads a human; it never fires a trade — **provided** the S-3 display contract is intact so the human is not misled by a systematically low estimate.
2. **Small-sample empirical CVaR on ~7-8 tail observations is biased toward UNDERSTATING the tail** (council attack rubric C-2). The S-3 **bias warning** (element d) is therefore **load-bearing**: an operator anchoring on a reassuring-looking-but-systematically-too-mild CVaR number is the actual harm M2 risks. Without (d) M2 manufactures false comfort and is mildly harmful.
3. **The standard error displayed under S-3(a) must be computed on the DISTINCT-tail-observation count (~7-8)**, NOT on the resample count (5000) — H-2. A stderr naively computed on 5000 understates the true estimation error by ~`√(5000/7) ≈ 27×` and converts S-3's honesty mechanism into a false-precision generator.

The user identified one real minor weakness in M2 (a CVaR number with no temporal reference frame — evaluation §A.4 / §B.6); the safe fix is **operator-optional**: the operator may compute M2 over a second, longer window, each window read independently under its own full S-3 contract, **with no signed-divergence quantity ever surfaced**. This is the surviving residue of the divergence-idea REJECT and the only allowed enrichment.

## Deliverables

### Code

- **`math_engine.py`** — new pure functions with `_reject_non_finite` entry validation:
  - `compute_cvar_5pct_general_distribution(returns: list[float], alpha: float = 0.05) -> CVaRAssessment` — **Rockafellar-Uryasev 2002 general-distribution estimator** (NOT the naive "average of losses beyond the empirical VaR" — A-1 ★). Handles the atom at the discrete `α`-quantile correctly. Returns a frozen typed object `CVaRAssessment(cvar_pct: float|None, tail_obs_count: int, stderr: float|None, insufficient_reason: str|None)` — the `None` sentinel mirrors `MC_INSUFFICIENT_HISTORY_SENTINEL` (F-4 ★) for an out-of-band insufficient signal (thin pool → `cvar_pct=None`, `tail_obs_count=0`, `stderr=None`).
  - `compute_cvar_stderr_distinct_tail(returns: list[float], alpha: float = 0.05) -> float | None` — **stderr computed on the distinct-tail-observation count**, the n≈7-8 number, **NOT** the resample count (H-2 binding). This is a separate function so the H-2 test can assert the function-under-test consumes the distinct-tail count exclusively.
  - Module-level dataclass / NamedTuple `CVaRAssessment` — frozen, with field order pinned for Gate-1 replay-parity assertions (H-8 A3). **Field name `tail_obs_count` is canonical** per synthesis §2.6 verbatim and critic's `mc-sentinel-blast-radius` plan's four-field contract. (The Phase-2 cosignal cycle adds two latched-output fields `breach`/`recovery` — out of M2's scope; Phase-1 consumers see only the four fields above.)
  - **Column-to-field scalar projection (binding):** the SQL column in `cvar_diagnostics` is `cvar_n_tail` (renaming a migration column would force a destructive migration — forbidden by additive-first). The Python boundary projection is `cvar_n_tail → tail_obs_count`. Wherever code, tests, or downstream consumers reference the Python attribute, it is `tail_obs_count`; the SQL column stays `cvar_n_tail`. Critic's `mc-sentinel-blast-radius` plan's DoD (e) carries this projection line as the authoritative reconciliation point.
- **`math_engine.py` constants** (no-magic-numbers):
  - `CVAR_ALPHA_DEFAULT = 0.05` — source comment cites the council synthesis (the only `α` Phase 1 supports; Phase 2 may parameterize).
  - `CVAR_MIN_TAIL_OBS = 1` — minimum distinct-tail-observation count below which `cvar_pct = None` (mirrors the sentinel discipline; one observation is already too thin for a meaningful CVaR — but the boundary IS deliberately set at 1 not at the rule-of-thumb-7 because the diagnostic should always report when *any* observation exists, alongside the S-3(b) `tail_obs_count` display that **tells** the operator the sample is thin).
- **M2 wiring into `alpha_bot_execution.py`** — at the per-cycle MC site, after `run_monte_carlo` returns, M2 reads the **same kNN pool** the MC used (the council synthesis is explicit: M2 is computed off the *existing* single-day pool — no new fetch, no parallel simulation). M2 wiring extracts `nearest_day_returns` from the MC's path, calls `compute_cvar_5pct_general_distribution` + `compute_cvar_stderr_distinct_tail`, and persists the assessment via the H4 helper.
  - **No mutation to `run_monte_carlo`** (H7 from migration plan — frozen until last symphony cuts over; the 7+ consumers in `project_mc_sentinel_consumer_blast_radius` are NOT in M2's blast radius).
  - **No new fetch / no parallel MC.** The existing kNN pool is reused.
- **`database.py`** + **`migrations/023_cvar_diagnostics.sql`** — NEW table:
  - Columns: `id INTEGER PRIMARY KEY AUTOINCREMENT`, `cycle_id TEXT NOT NULL`, `ts_utc TEXT NOT NULL`, `symphony_id TEXT NOT NULL` (the existing `account_id` analogue), `cvar_5pct REAL` (NULLable), `cvar_5pct_stderr REAL` (NULLable), `cvar_n_tail INTEGER NOT NULL DEFAULT 0`.
  - HOLD **and** EXIT cycles both — one row per (cycle, symphony) regardless of decision.
  - Fresh `CREATE TABLE` — **no Q7 H1 exposure** (the duplicate-column swallow fires only on `ALTER TABLE ADD COLUMN`; H2 from migration plan). Also dual-write to `init_db()` for fresh DBs is unnecessary because `run_migrations()` always runs all listed migrations on fresh DBs too — but for parity with the migration discipline, the table's CREATE statement is **only** in `023_cvar_diagnostics.sql` and `init_db()` does NOT re-CREATE it (H2 explicitly).
- **`database.py`** — new H4 telemetry helper `record_telemetry_row(table: str, columns: dict, mode: Literal["live", "replay"])`:
  - **Live mode:** swallows on write failure (a telemetry failure must never fail a live cycle — the `record_shadow_observation` precedent at `database.py:1147-1194`). The cycle continues.
  - **Replay mode:** raises on write failure (a replay that cannot persist its decision record is broken and must fail loud, not silently produce a partial record — H4 binding).
  - Self-opened connection, off the `save_state` transaction (preserves project architecture-constraint-1 spirit).
- **`reporting.py`** + dashboard surface — M2's display surface emits the **S-3 four-part contract** as a single coherent visual block:
  - (a) the CVaR value AND its uncertainty band (stderr from `compute_cvar_stderr_distinct_tail`).
  - (b) the genuine tail-observation count (`cvar_n_tail`).
  - (c) the explicit label *"diagnostic, not a signal — do not trade on this"*.
  - (d) the explicit bias warning *"this CVaR estimate is a known-low-biased LOWER BOUND on tail severity, not a point estimate."*
  - **All four** are required; if the display surface ever lacks even one, the test fails. The display is **read-only** (project architecture-constraint-2; I-3 ★) — never an action surface.

### §B operator-optional second-window enrichment (binding additive schema)

The §B residue is **additive**, NULLable, and operator-toggled. Per §B.6:

- `023_cvar_diagnostics.sql` ships **two additional columns** in this Phase-1 cycle: `cvar_5pct_long REAL DEFAULT NULL`, `cvar_n_tail_long INTEGER DEFAULT NULL`.
- These are simply *"M2's inputs, second window"* — two honest numbers each readable under S-3 with its own (a)(b)(c)(d) display block.
- They are **never** accompanied by a `cvar_divergence` or `regime_recency_weight` column. The moment a signed-divergence value appears on a display with a threshold-shaped affordance, the divergence REJECT is undone (§B.6 binding).
- Preferred baseline shape (§B.5): **DISJOINT** — `cvar_5pct_long` is computed on a strictly non-overlapping prior window so the two are statistically independent. The window definition is part of the M2 spec bundle and is **NN1-frozen** (not P&L-selected).
- NULL semantics: if `CVaR_long` is insufficient (thin 3-year tail, an early-life symbol), the long-window fields write NULL — mirroring `run_monte_carlo`'s `None`. A `NOT NULL` constraint would force a fabricated zero, the exact failure F-4 ★ exists to prevent.
- Execution-path cost: **zero on top of M2** (per §B.6) — the per-cycle write is still **one `INSERT` of one (wider) row into one table** — a wider row, not a second row.
- Replay-determinism anchor count is **unchanged**: Phase 1 = **1** anchor (M2's CVaR off the `cycle_id`-seeded kNN pool). The longer window is a second statistic off the same seeded resample discipline.

### Tests

The five RED golden-fixture tests for HARDEN-core are specified in council synthesis §8. M2 owns **test 3** + the H-2 tightening:

- **Test 3 (M2 CVaR-on-a-known-pool + S-3 display + H-2 stderr — RED):**
  - Sub-case 3a — known kNN pool (schema-derived, captured-from-producer fixture, D-2 ★); assert `compute_cvar_5pct_general_distribution(pool).cvar_pct == known_RU_value`.
  - Sub-case 3b — H-2 binding: a fixture where the resample count and the distinct-tail count differ; assert the displayed stderr is within tolerance of the small-sample (n≈7) value and **NOT** within tolerance of the n≈5000 value. The persisted `cvar_n_tail` column is the auditable denominator — a reviewer can confirm the stderr was not computed on the resample count.
  - Sub-case 3c — bias-warning presence: the dashboard render surface emits a string matching the literal *"known-low-biased LOWER BOUND"* phrasing. A regex / exact-string assertion (this IS the load-bearing test for S-3 element d).
  - Sub-case 3d — all four S-3 elements present: the render surface emits (a) stderr label, (b) `tail_obs_count`, (c) the "diagnostic, not a signal" label, and (d) the bias warning. Asserting all four ON THE SAME DISPLAY BLOCK (operator cannot anchor on (a) without seeing (d)).
- **Test 4 (one-anchor replay-determinism — bit-identical `cvar_5pct`):** same `cycle_id` run twice yields bit-identical `cvar_5pct`, `cvar_5pct_stderr`, `cvar_n_tail`. Parity assertion **excludes** `id` (autoincrement) and `ts_utc` (wall-clock) per H-8 A3.
- **Sentinel mirror (F-4 ★):** when the kNN pool is empty / `tail_obs_count == 0`, M2 returns `cvar_pct=None`, `stderr=None`, `tail_obs_count=0` — and the protective stop (the ticks-below-stop condition) still fires unaffected.
- **A-2 ★ closure:** `compute_cvar_5pct_general_distribution(returns=[NaN, ...])` raises `ValueError` at entry; `returns=[+Inf, ...]` raises; the function never silently propagates a non-finite into a comparison.
- **H-3 latency benchmark (binding):** a per-cycle wall-clock micro-benchmark — M2 read + compute + `record_telemetry_row` round-trip — fits within the documented per-cycle budget margin. The benchmark is captured as a CI assertion (warn-but-pass on regression; fail on a 10× regression). Architecture-constraint-1 compliance is the binding fact.
- **H4 live|replay test pair:** the live-mode helper swallows a forced write failure; the replay-mode helper raises. Same input, two modes, two distinct outcomes verified.
- **§B second-window NULL semantics:** when the long-window is insufficient (`cvar_n_tail_long == 0` in SQL / `tail_obs_count_long == 0` at the Python boundary), `cvar_5pct_long` writes NULL — never zero, never a fabricated point estimate. The test asserts NULL is written, not zero.
- **§B no-divergence-column tripwire:** a pytest scans `023_cvar_diagnostics.sql` and asserts NEITHER `cvar_divergence` NOR `regime_recency_weight` appears as a column. The moment one is added in a future cycle, this test fails — surfacing the §B.6 binding violation before it ships.

### Documentation

- The `CVaRAssessment` docstring quotes the council synthesis §2.3 + §3.1 verbatim: "M2 is a diagnostic; it changes zero decisions; a wrong M2 misleads a human, it never moves money — *which is why the S-3 bias warning is mandatory*."
- The S-3 four-part display contract is documented in `reporting.py` AND in `templates/<dashboard>.html` as a coherent rendering block — a future contributor cannot remove element (c) or (d) without breaking the test.
- The H-3 benchmark result is captured in the plan's final commit message: *"M2 per-cycle write benchmarked at <X> ms vs <Y> ms minute budget."*

## Dependencies

- **Blocks:** the Phase-2 evidence gate (council synthesis §5.1 precondition a — but per H-4, M2 can only **kill** Phase 2, never advance it). Also blocks any future cycle that consumes `cvar_diagnostics` data (the Overfitting Conscience's `advisor_observations` reads M2 rows; the AI Advisor's Divergence Explainer Phase 1.5 minimal role reads M2's shadow log).
- **Blocked by:** the H4 telemetry helper plan (`Plan: H4 telemetry helper` — owner: persistence-architect). The helper is a generic infrastructure piece that other Phase-1 telemetry writes also depend on.
- **Soft dependency:** the M2 display surface depends on the dashboard's existing display patterns (`reporting.py` + `app/templates/`) — no new dashboard infrastructure required; M2 is a new display block on an existing page.
- **NOT dependent on M1.** M1 and M2 ship in parallel — they answer different motivations (per H-4: M1 = defensibility win, M2 = operator instrumentation).
- **NOT dependent on M3.** M3 is Phase 1.5 and re-derives heuristic provenance; M2 is independent.

## Golden-fixture tests required (RED before GREEN)

| # | RED-test | What must exist before GREEN |
|---|---|---|
| 3a | Rockafellar-Uryasev CVaR on a known kNN pool | A captured-from-producer fixture pool; an analytic / cross-checked reference CVaR value; the function asserts a bit-equal output. |
| 3b | H-2 stderr on distinct-tail count | A fixture where `n_resample != tail_obs_count`; the stderr is within tolerance of the n≈7 value, NOT within tolerance of the n≈5000 value. |
| 3c | S-3 bias-warning string presence | Display-surface render asserts the literal "known-low-biased LOWER BOUND" phrasing. |
| 3d | S-3 all-four-elements presence on one display block | Render asserts (a)+(b)+(c)+(d) co-located. |
| 4 | One-anchor replay-determinism | Same `cycle_id` → bit-identical `cvar_5pct`, `cvar_5pct_stderr`, `cvar_n_tail`; exclude `id`/`ts_utc`. |
| — | F-4 sentinel mirror | Empty pool → `cvar_pct=None`; protective stop still fires. |
| — | A-2 NaN/Inf closure | `[NaN, ...]` / `[+Inf, ...]` raises at entry. |
| — | H-3 per-cycle benchmark | M2 round-trip fits the minute budget margin. |
| — | H4 live|replay pair | Live swallows, replay raises, same forced-failure input. |
| — | §B second-window NULL semantics | Long-window insufficient → `cvar_5pct_long` is NULL. |
| — | §B no-divergence-column tripwire | `023_cvar_diagnostics.sql` schema scan: neither `cvar_divergence` nor `regime_recency_weight`. |

**Fixture provenance (D-2 ★):** the known-pool fixture is captured-from-producer (a real kNN pool snapshot) with an independent analytic / cross-checked Rockafellar-Uryasev CVaR reference — NOT computed by the same code under test.

## Definition of Done

- All RED tests above land first.
- GREEN: every RED test passes; full-tree pytest with HEAD SHA + count + zero errors per `feedback_full_suite_means_genuine_full_tree`.
- `CVAR_ALPHA_DEFAULT`, `CVAR_MIN_TAIL_OBS` are named module-scope constants with source-comment justifications.
- M2's writer is routed through the H4 telemetry helper (live swallows, replay raises). The `record_shadow_observation` precedent at `database.py:1147-1194` is the structural reference.
- The S-3 four-part display contract is rendered as a coherent block; tests 3c + 3d pass; the bias warning is literal-string-present.
- Migration `023_cvar_diagnostics.sql` lands with all six columns + the two §B columns (eight columns total). `cvar_diagnostics` is a fresh CREATE (no Q7 exposure). HOLD and EXIT rows both written.
- M2 reads the kNN pool that `run_monte_carlo` ALREADY built — no parallel MC, no new fetch, no mutation of `run_monte_carlo` (G-1 ★, H7).
- The per-cycle benchmark result is captured in the commit message: M2's round-trip fits the minute budget margin with documented headroom.
- Wording defect fix from H-3: every doc / docstring / commit-message use of "zero impact" is replaced with "**zero decision impact, non-zero non-blocking I/O cost**."

## Risk callouts / hazards

- **H-2 (stderr on distinct-tail count, NOT resample count).** Binding. A naively-computed n≈5000 stderr understates by ~27× and converts the honesty mechanism into a false-precision generator. The test asserts the **n≈7 value**, the **n≈5000 value is a forbidden alternative**.
- **S-3 element (d) bias warning (load-bearing).** Without the bias warning, M2 manufactures false comfort and is mildly harmful (council synthesis §3.1, §4). The literal string is part of the test surface.
- **H-3 (per-cycle write is non-zero I/O cost).** The wording matters AND the benchmark matters. Architecture-constraint-1: no blocking I/O on the execution path. M2's per-cycle `INSERT` is structurally analogous to the accepted `record_shadow_observation` per-cycle write — the test makes it explicit.
- **H-4 (re-label, do NOT demote).** M2 stays in Phase 1; M2 is **operator instrumentation**, not scaffolding for Phase 2. The plan must NOT frame M2 as "starting the evidence chain toward Phase 2." Per H-4: M2 can raise a gross kill flag against Phase 2; it can NEVER advance it.
- **§B operator-optional second window — no signed-divergence.** Binding. A `cvar_divergence` column on the dashboard would manufacture the detector affordance the REJECT removes. The schema tripwire test exists to prevent regression.
- **NN1 spec-freeze (★).** The CVaR_long window length, the kNN pool definition, and the alpha=0.05 facet are all frozen by theory/mandate — NEVER by P&L. `spec_facets.freeze_discipline` for each must be `THEORY` or `MANDATE`, never `BACKTEST_SELECTION`.
- **MC sentinel discipline mirror (F-4 ★).** `cvar_pct=None` when insufficient. The protective stop still fires on the ticks-below-stop condition alone. M2 NEVER disables a safety floor.
- **G-1 / G-2 ★ (run_monte_carlo blast radius).** M2 reuses `nearest_day_returns` from the existing MC — does NOT mutate `run_monte_carlo`'s signature or return type. The 7+ consumers in `project_mc_sentinel_consumer_blast_radius` are untouched.
- **Replay-determinism (F-2 ★, K-1 ★).** M2's CVaR is deterministic given the seeded kNN pool. Replay parity test 4 verifies this. Phase-1 anchor count = 1.
- **I-3 ★ (dashboard read-only).** M2's display surface is a passive render — no button, no toggle that mutates engine state. Log lines do not echo raw Composer/Alpaca response bodies (rubric standing gate 7).
- **Two-DB boundary (E-2 ★).** `cvar_diagnostics` lives in the **state DB**. No optimization-DB migration.

## Out of scope

- A live CVaR **trigger**. Synthesis §2.3 — un-validatable at this data scale. M2 is diagnostic only.
- Phase-2 forward-path simulator. M2 reuses the EXISTING single-day kNN pool; multi-day CVaR is net-new (G-2 ★) and Phase-2 only.
- A `cvar_divergence` column or any signed-divergence quantity surfaced on the dashboard. Forbidden by §B.6 binding. The schema tripwire test prevents regression.
- A `regime_recency_weight` column. Forbidden by §B.6.
- Hand-curated regime-shift label sets (§B.3 route 3: circular validation, D-2 ★ fixture-provenance failure).
- LLM-authored Advisor commentary on M2 rows. The `advisor_observations` table is Phase 1, but Phase-1 rows are the **computed** Overfitting-Conscience verdict only; LLM authorship is Phase 2.
- Touching `run_monte_carlo`. Frozen until last symphony cuts over (H7).
- Calibrating M2 against a joint VaR-ES coverage backtest. Underpowered at AlphaBot's data scale (synthesis §2.3) — Gate 2 acceptance is **diagnostic quality** (zero NaN/Inf, S-3 four-part present, reproducible under replay), NOT trigger behavior.
