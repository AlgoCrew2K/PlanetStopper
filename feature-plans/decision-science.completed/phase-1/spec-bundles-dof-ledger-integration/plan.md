# Phase 1 — `spec_bundles` / `spec_facets` / `researcher_dof_ledger` Interaction with `N_effective`

**Feature:** Wire the autotuner's `N_effective` consumer to the state-DB
spec registry (migrations 015, 020). Single-DB-clean accessor surface;
deterministic, idempotent. Closes the loop between NN1 (rule), `N_effective`
(structural enforcement), and persistence (immutable audit trail).

**Phase:** Phase 1 (HARDEN floor — overfitting-accounting spine)

**Owner agent-type:** `optuna-specialist` (drives the read-side wiring),
`sqlite-specialist` (reviews the accessor surface for single-DB
discipline), `quant-test-writer` (adversarial RED on the
winner-self-exclusion + the cross-run pollution case).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §2.2 (additive
  accounting), §3.7 (persistence footprint — `spec_bundles` immutable +
  content-hashed + `frozen_at`-stamped; `spec_facets` is the queryable
  projection; team's-choice on collapsing `spec_facets` into a JSON
  column under one binding constraint), §3.7 "Phase-1 apparatus sizing"
  paragraph (the team's-choice latitude).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.8
  (H-8 internal-consistency drafting defects: A1 — migration filename
  `021_fold_role.sql`, NOT `_columns.sql`; A2 — the spec-registry
  table-count statement; A3 — Gate-1 parity column-exclusion list).
- `docs/handoff/council-converged-migration-plan.md` §2 (single-DB
  state-DB placement; the haircut reads `D_spec` by COPYING from state
  DB into the autotune run, NEVER cross-joining), §3.1 (migrations
  015 / 020 / 022 schemas in detail), §5 (`N_effective` consumer; zero
  schema impact beyond migration 022).
- `autotuner.py:973-975` — current single-DB read pattern
  (`database.get_symphony_strategy`). The new accessor follows this
  pattern exactly.

## Why

The additive `N_effective = N_optuna + S` accounting (separate plan)
defines the consumer math. The NN1 spec-freeze plan defines the
discipline. **This plan is the wiring** — the concrete accessor surface
the autotuner calls to fetch the data, and the integration contract that
keeps the read single-DB-clean while preserving the persistence-
architect's immutability + content-hash + `frozen_at` constraint.

Without this plan, the M1 implementation is ambiguous on three load-
bearing questions:
1. WHICH `spec_bundles` row is the "active" bundle for a given
   `run_autotuner` invocation?
2. HOW does the autotuner pass the bundle's `gamma` to
   `compute_crra_eu_tstat` and `run_simulation_crra_eu` without
   re-deriving it from somewhere else?
3. WHAT counts as "the same run window" for the `researcher_dof_ledger`
   query — and how does cross-run pollution (a ledger row from a
   previous run) NOT inflate `S` for the current run?

This plan answers all three.

## Deliverables

### D1 — Active-bundle resolution

A NEW helper in `database.py`:

```python
def get_active_spec_bundle(as_of_utc: str | None = None) -> dict:
    """Return the active spec_bundles row for an autotune run.

    Active = the most-recently-frozen bundle whose frozen_at <= as_of_utc
    (default: now). Frozen-at-stamped semantics: a bundle becomes active
    the moment it freezes, and stays active until superseded. There is
    ALWAYS exactly one active bundle in steady-state Phase 1 (the
    initial Phase-1 cutover bundle).

    Raises RuntimeError if zero bundles found — a missing active bundle
    is unrecoverable; an autotuner run cannot proceed without a frozen
    gamma. NEVER falls back to a default value.

    Returns a dict with: bundle_id, bundle_hash, frozen_at, facets_json,
    gamma (denormalised projection), and the rendered spec_facets list.
    """
```

Constraints:
- Single-DB (state DB).
- Returns a dict shape, not an ORM object — keeps the autotuner free of
  ORM coupling (consistent with current `get_symphony_strategy` shape).
- NEVER returns a default; explicit fail-loud if no bundle is found.
- Honors the H1 dual-write hazard transparently — both `init_db()` and
  migration 022 paths produce DB rows this function reads.

### D2 — `ledger_query_for_run` accessor

A NEW helper in `database.py` (consumed by the additive `N_effective`
plan's `compute_n_effective`):

```python
def get_researcher_dof_ledger_for_run(
    run_timestamp: str,
    winner_spec_bundle_id: int,
) -> list[dict]:
    """Rows that contribute to S for the current autotune run.

    Filters:
      - evidence_source = 'BACKTEST_SELECTION'    (NN1-violation rows only)
      - spec_bundle_id  != winner_spec_bundle_id  (winner already in n_optuna)
      - COALESCE(touched_frozen_eval, 0) = 0      (frozen-eval-tainted
                                                   is a worse OOS_PEEK
                                                   alarm, separate path)
      - run_window predicate                       (see below)

    The run_window predicate is the KEY semantic decision (see Q3 of the
    Why). Phase 1 ships the conservative shape: returns rows whose
    ledger_ts_utc is within the lookback window
    [run_timestamp - LEDGER_LOOKBACK_DAYS, run_timestamp] — bounded so
    a ledger row from 6 months ago does not inflate every Phase-1
    haircut indefinitely.

    LEDGER_LOOKBACK_DAYS: a named module constant in autotuner.py,
    source-commented per no-magic-numbers. Phase 1 floor: 30 days
    (one calendar month — same regime, same data, same testing event).
    """
```

The `LEDGER_LOOKBACK_DAYS` named constant lives in `autotuner.py`
with a source comment:

```python
# Window over which previously-flushed researcher_dof_ledger rows count
# toward the current run's S (additive N_effective accounting; council
# §2.2). 30 days keeps the same-regime / same-data assumption tight; a
# stale ledger row from a prior data regime is not a current
# multiple-testing event.
# Operator may tighten/loosen. Increasing inflates haircut conservatism
# (errs safe); decreasing risks under-counting genuine recent
# multiple-testing pressure.
LEDGER_LOOKBACK_DAYS = 30
```

### D3 — Active-bundle → autotuner threading

At the top of `run_autotuner`, after `validate_search_space_nn1()`
(NN1 plan, D5):

```python
active_bundle = database.get_active_spec_bundle()
nn1_ok, nn1_violations = validate_nn1_compliance(active_bundle["bundle_id"])
if not nn1_ok:
    # Log loud — Overfitting Conscience advisor reads this. NN1 violation
    # is NOT a hard abort: the additive N_effective accounting fires the
    # tripwire structurally.
    print(f"  -> WARNING: active spec_bundle has NN1 violations: {nn1_violations}")
gamma = active_bundle["gamma"]
# gamma threaded through to objective() closure and to
# compute_crra_eu_tstat via a closure-capture or functools.partial — see
# the M1 plan D6 and the compute_crra_eu_tstat plan D2.
```

The `gamma` value is captured ONCE per `run_autotuner` invocation —
NEVER re-read inside the trial loop, NEVER read from a global. Single
source of truth: the active bundle's `gamma` column.

### D4 — `selection_tstat` / `selection_p_adj` / `winner_spec_bundle_id` write-back

After `_haircut_select` returns, the autotuner writes the
`autotune_runs` row (migration 022) with:
- `spec_bundle_id = active_bundle["bundle_id"]` (the WINNING bundle for
  this run — by construction in Phase 1, the active bundle IS the
  winner because Phase 1 has only one bundle. Phase 2 introduces
  cross-bundle competition).
- `gamma = active_bundle["gamma"]` — copied, not joined.
- `ce_metric = u_inv(winner_trial.value, gamma)` — CE in return units
  (M1 plan D7).
- `d_spec` and `n_effective` from `compute_n_effective` (additive
  accounting plan D5).
- `overfitting_verdict` — the human-readable summary.

The COPY discipline is load-bearing: a future analytics query
(`SELECT gamma FROM autotune_runs WHERE …`) reads the persisted snapshot
of `gamma` at run-time, even if the active bundle is later superseded.
NEVER `SELECT … FROM autotune_runs r JOIN spec_bundles b ON …` — a
two-DB-violation surface is the easy bug here when both tables happen
to be in the same DB.

### D5 — Idempotency under retry

If `run_autotuner` is invoked twice for the same `(run_timestamp,
symphony_id, account_id)` (a retry after a flake), the bundle resolution
must be deterministic: the SAME active bundle is returned, the SAME
`gamma` is used, the SAME `n_optuna` is consumed (Optuna's
`load_if_exists=False` at `autotuner.py:1009` enforces a fresh study —
but the bundle resolution must NOT race).

`get_active_spec_bundle` is idempotent by construction (it reads, never
writes). The single-bundle invariant in Phase 1 means the retry sees
the same bundle as the first attempt.

### D6 — Cross-run pollution guard

The `LEDGER_LOOKBACK_DAYS=30` window in D2 bounds cross-run pollution.
A regression test (T4 below) drives this: a ledger row 60 days old MUST
NOT contribute to today's `S`. The implementing team's risk: a
maintainer decides "actually let's include all-time history" — that
would make `S` grow monotonically forever, eventually freezing the
haircut into permanent non-passable state.

## Dependencies

- **Blocks:** Phase 1 — additive `N_effective` accounting plan (D4 in
  that plan uses `get_researcher_dof_ledger_for_run`).
- **Blocks:** Phase 1 — M1 CRRA-EU objective plan (D4 in that plan
  reads `gamma` from `get_active_spec_bundle`).
- **Blocks:** Phase 1 — NN1 spec-freeze plan (D2 in that plan calls
  `validate_nn1_compliance(active_bundle.bundle_id)`).
- **Blocked by:** persistence-architect's migrations 015, 020, 022.

## Golden-fixture tests required

### T1 — Single-bundle steady-state resolution

Fixture: a state-DB seeded with ONE `spec_bundles` row, three
`spec_facets` rows (gamma, utility_family, wealth_argument). Assert
`get_active_spec_bundle()` returns that bundle with `gamma` correctly
populated.

### T2 — Two-bundle supersession

Fixture: an OLDER bundle frozen 30 days ago + a NEWER bundle frozen
1 hour ago. Assert `get_active_spec_bundle()` returns the NEWER one
(most-recent `frozen_at` wins).

### T3 — Zero-bundle fail-loud

Fixture: empty `spec_bundles` table. Assert `get_active_spec_bundle()`
raises `RuntimeError` with a message mentioning "no active spec bundle"
or equivalent. NEVER returns a default.

### T4 — `LEDGER_LOOKBACK_DAYS` cross-run pollution guard

Fixture: a `researcher_dof_ledger` row from 60 days ago with
`evidence_source='BACKTEST_SELECTION'`, `n_configs_searched=10`. Assert
`get_researcher_dof_ledger_for_run(run_ts_now, winner_bundle_id)`
returns an EMPTY list (the row is outside the lookback window). A
naive implementation that returns all rows would silently inflate `S`
by 10 on every Phase-1 run forever.

### T5 — Winner self-exclusion

Fixture: a ledger row whose `spec_bundle_id` matches
`winner_spec_bundle_id`. Assert it is EXCLUDED (the winner is already
counted in `n_optuna`; double-counting it would mean every Phase-1
NN1-honest run silently inflates `S` by 1 — the recommended Phase-1
floor must NOT exhibit this drift).

### T6 — Frozen-eval-tainted row exclusion

Fixture: a ledger row with `touched_frozen_eval=1`. Assert it is
EXCLUDED from this query (it is the separate OOS_PEEK alarm path).

### T7 — Two-DB cleanliness

Static-analysis-style: assert no SQL fragment in `database.py` joins
`spec_bundles` or `researcher_dof_ledger` against any table outside
the state DB. Tripwire against a future "let me just join the autotune
study via SQLAlchemy URL" PR.

### T8 — Immutability negative pin

Attempt `UPDATE spec_bundles SET facets_json = '{}' WHERE bundle_id =
…`. Assert the write fails (via a write-guard, an absence of update
helpers, or — at minimum — an integration test that asserts the row
is unchanged after attempted overwrite). The immutability invariant
must be enforced by code, not by convention.

### T9 — Gate-1 parity column-exclusion list (H-8 A3)

Assert the Gate-1 replay-parity test's column-exclusion list explicitly
names `id` AND `ts_utc` AND `frozen_at` AND `bundle_hash` (these are
incidental / wall-clock / autoincrement and must be excluded). Per
H-8 A3 — the exclusion list is named, not "decision-content columns
only" prose.

## Definition of Done

1. T1-T9 RED on a clean implementer commit, GREEN after.
2. `pytest tests/autotuner/` + `tests/database/` PASS.
3. `get_active_spec_bundle` and `get_researcher_dof_ledger_for_run`
   live in `database.py`; both are single-DB.
4. `LEDGER_LOOKBACK_DAYS` is a named module-scope constant in
   `autotuner.py` with a source comment.
5. The `autotune_runs` row writes the COPIED `gamma` and
   `spec_bundle_id` (never joined at read-time downstream).
6. The Gate-1 parity column-exclusion list is named in the test fixture
   (closing H-8 A3).
7. Commit message: `feat(autotuner): single-DB spec_bundles +
   researcher_dof_ledger accessor surface;
   LEDGER_LOOKBACK_DAYS=30; winner-self-exclusion + frozen-eval-tainted
   exclusion; Gate-1 parity exclusion list named; n_trials=500;
   objective=CRRA-EU mean(U)`.

## Risk callouts

- **Cross-run pollution (silent S inflation).** The single biggest
  systemic risk: a maintainer removes the `LEDGER_LOOKBACK_DAYS` window
  arguing "we want ALL historical multiple-testing pressure counted."
  In a long-lived deployment, this monotonically inflates `S` forever
  and eventually saturates `c(N)` such that no trial can clear the FDR
  gate. T4 (60-day-old row excluded) catches the most likely shape of
  the regression. The named constant + source comment makes the
  trade-off explicit at edit time.
- **Two-DB cleanliness leak.** Both `spec_bundles` and `autotune_runs`
  are state-DB; the temptation to do a "tiny join, just this once"
  shows up at the moment a future analytics dashboard wants
  `gamma_used_per_run` × `spec_bundle_metadata`. T7 (static-analysis
  no-join) catches the form; the COPY discipline (D4) provides the
  intended pattern.
- **`get_active_spec_bundle` returning a default.** A maintainer might
  reasonably add a `default_gamma=2.0` fallback "for tests." That
  silently re-introduces the H-3-class drift (a "gamma" value not from
  the immutable hashed registry). T3's fail-loud assertion is the
  guard.
- **Active-bundle race under retry.** If two threads of `run_autotuner`
  invoke `get_active_spec_bundle` simultaneously and a bundle
  supersession happens between, they see different bundles. The
  Phase-1 single-bundle invariant makes this concretely impossible;
  Phase-2 cross-bundle competition reopens the question. Phase-1 plan
  is: the bundle resolution is captured at the top of `run_autotuner`
  ONCE, and threaded down. T1 / T2 verify this in steady-state.
- **Gate-1 parity exclusion list incompleteness.** H-8 A3 names `id`
  and `ts_utc`. This plan extends to `frozen_at` and `bundle_hash` for
  `spec_bundles`-related rows in the parity assertion. Future Phase-2
  additions (e.g. `mc_seed`) extend the list further — the named-list
  shape makes the extension surface clear at PR review.
- **Immutability enforcement vs convention.** SQLite does not natively
  enforce "INSERT-only" tables. The implementing team must either (a)
  install a trigger that raises on UPDATE/DELETE of `spec_bundles`,
  (b) gate all writes through an accessor that does not expose
  update/delete methods, or (c) at minimum, the T8 test stays in CI
  as a tripwire. Option (b) is the recommended Phase-1 floor —
  cheapest, immediately effective, matches the `llm_suggestions`
  precedent in the project.

## Out of scope

- The schema of `spec_bundles` / `spec_facets` / `researcher_dof_ledger`
  / `autotune_runs` — owned by persistence-architect's migrations 015 /
  020 / 022.
- The NN1 rule and enum constants — owned by the NN1 spec-freeze plan.
- The additive `N_effective = N_optuna + S` computation — owned by the
  additive accounting plan.
- The Phase-2 multi-bundle competition surface — Phase 2 plans.
- The `advisor_observations` write of the `overfitting_verdict` —
  Phase 1 computed branch of the Overfitting Conscience advisor, owned
  separately.
