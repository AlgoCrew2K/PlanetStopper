# Plan — Replay-determinism anchor persistence (cycle_id-seeded RNG state)

**Feature:** Persist the one Phase-1 replay-determinism anchor — the
`cycle_id`-derived RNG seed that drives M2's CVaR resample. The schema home
for the seed (the auditable value a replay re-derives and re-uses to read
bit-identical `cvar_5pct`).

**Phase:** Phase 1 (HARDEN floor — supports the §8 test 4
one-anchor replay-determinism test).

**Owner agent-type:** `sqlite-specialist` (persistence + accessor),
`quant-test-writer` (RED), `quant-code-reviewer`.

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §3.7 — "The replay-
  determinism anchor count: Phase 1 = **1** anchor (M2's CVaR off the
  `cycle_id`-seeded kNN pool); Phase 2 = 5."
- `docs/handoff/decision-science-council-synthesis.md` §8 test 4 — "The
  one-anchor replay-determinism test — the same cycle run twice yields
  bit-identical `cvar_5pct`."
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.8 A3
  (Gate-1 parity exclusion list — assert on `cvar_5pct`,
  `cvar_5pct_stderr`, `cvar_n_tail`, `cvar_5pct_long`, `cvar_n_tail_long`;
  explicitly exclude `id` and `ts_utc`).
- Sibling plan: `feature-plans/decision-science/phase-1/red-test-4-replay-
  determinism/plan.md` (already drafted by a teammate; covers the RED
  test authoring; this plan covers the **persistence side** — where the
  seed lives, how it is re-derived, what the parity assertion reads).
- Codebase: `derive_cycle_mc_seed` (referenced in the sibling plan) — the
  existing SHA-256 cycle-seeded MC discipline.

## Why

A replay must be **bit-identical** for `cvar_5pct`. That is true if and
only if:

1. The kNN pool is reproducible from `cycle_id` (existing discipline —
   `synthetic_history.py` file cache + the rolling Alpaca pool seeded by
   `cycle_id`).
2. The RNG seed used inside `run_monte_carlo` is reproducible from
   `cycle_id` (existing discipline — `derive_cycle_mc_seed` SHA-256).
3. The CVaR estimator on the resampled pool is pure (math_engine
   responsibility; risk-engine-specialist).

The persistence question is **what to persist so that a replay harness can
verify (1)+(2)+(3) yielded the same number, and a Gate-1 audit can prove
nothing else moved**. The answer:

- The seed itself **does not need a new persisted column** — it is a pure
  function of `cycle_id` (which `cvar_diagnostics.cycle_id` already
  persists). A replay re-derives the seed from `cycle_id` via the same
  function. **Storing the seed would be a redundant copy** that creates a
  drift surface (a copy that diverges from the function is worse than no
  copy).
- The replay-parity assertion reads `cvar_5pct`, `cvar_5pct_stderr`, and
  `cvar_n_tail` from `cvar_diagnostics` keyed on `cycle_id`, and asserts
  bit-equality against the freshly-replayed values. The exclusion list
  (`id`, `ts_utc`) is enforced at the assertion site (the H4 helper plan
  documents the constant `_PARITY_EXCLUDE = {"id", "ts_utc"}`).

This plan formalises the **non-persistence decision** and the assertion
contract.

## Deliverables

1. **No new migration file.** The persistence already exists — `cycle_id`
   is a column on `cvar_diagnostics` (plan `021-cvar-diagnostics`), and
   the seed is a function of it. **Documenting the non-persistence
   decision is itself the deliverable** so a future maintainer does not
   add a `mc_seed` column to `cvar_diagnostics` and create the drift
   surface.
2. **`database.py` — `read_cvar_diagnostic_for_cycle(cycle_id)`
   accessor.** A `get_ro_connection()`-based read that returns the
   single row for a `(cycle_id, symphony_id)` pair. The replay harness
   and the Gate-1 parity assertion are its only callers.
3. **A `_PARITY_DECISION_COLUMNS` constant in `database.py` or a sibling
   module.** Binding enumeration of the decision-content columns Gate-1
   asserts on:
   ```python
   _PARITY_DECISION_COLUMNS = (
       "cvar_5pct",
       "cvar_5pct_stderr",
       "cvar_n_tail",
       "cvar_5pct_long",
       "cvar_n_tail_long",
   )
   _PARITY_EXCLUDE_COLUMNS = ("id", "ts_utc")
   ```
   The replay harness reads `_PARITY_DECISION_COLUMNS` to construct the
   assertion; a code reviewer reads `_PARITY_EXCLUDE_COLUMNS` to
   understand what is intentionally not compared.
4. **A documentation block in this plan** (this section) declaring
   non-persistence of the seed and the rationale.
5. **Fixture refresh** — at least one fixture row carries a
   reproducible-from-`cycle_id` triple `(cvar_5pct, cvar_5pct_stderr,
   cvar_n_tail)` so the RED test 4 (sibling plan) has a baseline to
   replay against.

## Dependencies

- **Hard-depends on `021_cvar_diagnostics.sql`** (the table whose columns
  the parity assertion reads).
- **Hard-depends on the H4 telemetry helper** (the M2 writer uses it).
- **Hard-depends on the existing `derive_cycle_mc_seed` function** (no
  schema impact; existing discipline).
- **Sibling plan dependency:** `red-test-4-replay-determinism` (the RED
  test that asserts bit-identity reads `_PARITY_DECISION_COLUMNS`).

## Golden-fixture tests required (RED before GREEN)

1. **Bit-identical CVaR across two replays of the same `cycle_id`** —
   the §8 test 4 RED test (sibling plan covers the test authoring; this
   plan asserts the accessor + the column list support it).
2. **`read_cvar_diagnostic_for_cycle` reads from a `mode=ro` connection**
   — the accessor uses `get_ro_connection()` (charter Operating Rule 3).
3. **`_PARITY_DECISION_COLUMNS` and `_PARITY_EXCLUDE_COLUMNS` are
   complementary subsets of `cvar_diagnostics`'s column list** — a
   property test: `set(EXCLUDE) | set(DECISION) ⊆ set(all_columns)` AND
   `set(EXCLUDE) ∩ set(DECISION) == ∅`. This is the structural guard
   that a column-add to `cvar_diagnostics` does not silently land in
   neither set (a decision-content column missing from
   `_PARITY_DECISION_COLUMNS` would silently pass parity it should
   fail).
4. **A column-add reminder test** — when `cvar_diagnostics` gains a new
   column at any future migration, an assertion fires that the column
   must be classified into one of the two sets. The test reads the
   table's current columns via `PRAGMA table_info(cvar_diagnostics)`
   and computes the unclassified set; if non-empty, fail with a clear
   message naming the unclassified columns. This is the structural
   anti-drift fence.
5. **No `mc_seed` column on `cvar_diagnostics`** — a grep test that
   forbids re-introducing the redundant copy.

## Definition of Done

- The accessor + the two constants land in `database.py` (or a sibling
  module the replay harness imports).
- All five tests pass GREEN.
- The sibling RED test (`red-test-4-replay-determinism`) GREEN against
  the new accessor.
- A documentation block in the implementer file explains the
  non-persistence decision with a link to this plan.

## Risk callouts

- **The single biggest risk is a well-intentioned `mc_seed` column
  add.** A future maintainer reads "Phase 1 = 1 replay anchor" and
  thinks "the anchor is the seed, persist it." That **creates** the
  drift surface this plan removes. Test §5 is the structural enforcement.
- **`_PARITY_DECISION_COLUMNS` must include `cvar_5pct_long` and
  `cvar_n_tail_long`** (§A.8 A3 binding) — the §B.6 second-window
  residue is bit-identically replayable for the same reason as the
  single-window CVaR (it reads a second statistic off the same
  cycle_id-seeded pool, council §B.6). Omitting them from the parity
  list would let a long-window regression pass.
- **`id` is autoincrement** — a replay legitimately produces a different
  `id`; including it in `_PARITY_DECISION_COLUMNS` would false-fail
  every replay.
- **`ts_utc` is wall-clock** — same as `id`; a replay produces a
  different wall-clock. Excluded.
- **Phase 2 adds 4 more anchors** (council §3.7: "Phase 2 = 5"). Each
  carries its own non-persistence-or-persistence decision; the Phase-2
  plans (`025-shadow-decisions`, `026-path-generator`,
  `027-decision-core-state`) own those decisions. This Phase-1 plan
  governs only the M2 CVaR anchor.

## Out of scope

- The MC seed derivation function (`derive_cycle_mc_seed` — already
  exists; risk-engine-specialist domain).
- The kNN pool re-fetch from the rolling Alpaca history (lives in
  `synthetic_history.py`; `composer-alpaca-integration` domain).
- The CVaR computation (`math_engine.py`; risk-engine-specialist).
- The Gate-1 attribution table itself (Phase 1.5 — separate plan
  `phase-1.5/s1-parity-attribution`).
- Phase-2 anchors (seed for `tier1_seed`, calibration_id,
  history_fingerprint, hysteresis snapshot — Phase-2 plans).
