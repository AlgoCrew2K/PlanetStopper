# Feature: Persist the Phase-3 PBO Veto Value (BL-1)
Status: shipped (pending merge) — see `DE-AUDIT-BL1-001` in `DECISIONS.md`
Created: 2026-08-04
Source: `docs/audit/TWO-WEEK-REVIEW-2026-08-04.md` §4 Finding T1, §6 Backlog BL-1 (commit `ca7f2beb`)

## Summary
The Phase-3 PBO (Probability of Backtest Overfitting) overfitting veto is computed
live and can veto an AI-tuned proposal, but the computed value is discarded at
persistence time — `autotune_runs.pbo` is NULL in 40/40 observed rows even though
the gate genuinely consumed it. `_pbo_value` is computed at `autotuner.py:2854/2870`
(`math_engine.compute_pbo`), fed into `acceptance_gate.evaluate_acceptance_gate` at
`autotuner.py:3075`, and can trigger the veto branch at `autotuner.py:3084-3100`
(`_pbo_veto_fired`). But the `database.save_autotune_run(...)` call at
`autotuner.py:3218-3239` omits `pbo=` entirely, even though the function signature
already accepts it (`database.py:711`) and the INSERT already includes the column
(`database.py:766-796`, tuple position after `overfitting_verdict`). This is a
persistence bug, not a computation bug — `_pbo_value` is correctly computed and
correctly used for the veto decision; it simply never reaches the database. Because
of this, the overfitting veto's behavior is currently un-auditable from the DB, and
a future PBO regression (e.g. a unit-scale bug like the one fixed in
`DE-MATH-R0-001` AC-1/AC-2) would be invisible to any post-hoc row inspection.

The existing test `tests/autotuner/test_pbo_migration_028.py::TestSaveAutotuneRunPboRoundTrip`
proves the *accessor* round-trips `pbo` correctly when called directly — it never
calls `run_autotuner()` itself, so the integration gap at the call site slipped
through 40 consecutive live runs undetected.

## Acceptance Criteria
- [x] **AC-1 — thread `pbo=` into the persistence call.** The
      `database.save_autotune_run(...)` call at `autotuner.py:3218-3239` gains
      `pbo=_pbo_value` (the SAME variable already used for the veto decision at
      `autotuner.py:3075/3087-3088`, never a re-derived or re-computed value). No
      other kwarg in this call changes.
- [x] **AC-2 — integration test on `run_autotuner()` itself, not just the accessor.**
      A new test drives `run_autotuner()` (or the smallest realistic slice of it that
      reaches the `save_autotune_run` call — e.g. via existing `run_autotuner`
      integration-test fixtures already used elsewhere in `tests/autotuner/`) with a
      fixture that produces a genuine, non-`None` `_pbo_value` (>= 2 CSCV-eligible
      configs with `cscv_date_returns` present, i.e. `haircut_trials` populated),
      and asserts the persisted `autotune_runs.pbo` row for that run is non-NULL and
      matches the value the gate itself consumed for the veto decision — closing the
      exact gap `test_pbo_migration_028.py` structurally cannot close (it never
      calls `run_autotuner`).
- [x] **AC-3 — `pbo=None` still persists correctly when PBO is not computed.** When
      `haircut_trials` is empty or fewer than 2 CSCV-eligible configs exist
      (`autotuner.py:2855/2869` — `_pbo_value` stays its initialized `None`), the
      persisted row's `pbo` column is `NULL` (matches the accessor's existing
      `test_pbo_none_persists_as_null` contract) — this AC pins the NEGATIVE case so
      AC-1's fix cannot regress into "PBO always fabricated as some non-null value."
- [x] **AC-4 — zero change to gate/veto logic.** The PBO computation
      (`math_engine.compute_pbo`), the veto decision (`_pbo_veto_fired`,
      `autotuner.py:3084-3100`), and the acceptance-gate call
      (`autotuner.py:3060-3076`) are byte-unchanged — this is a persistence-only fix.
      A run that would have vetoed before this fix still vetoes identically after it;
      the ONLY observable difference is that `autotune_runs.pbo` is now populated.

## Architecture
- **`autotuner.py`** — one-line addition (`pbo=_pbo_value`) to the existing
  `database.save_autotune_run(...)` call at `:3218-3239`. `_pbo_value` is already in
  scope at this point in the per-symphony loop body (computed at `:2854-2874`,
  unchanged from its last use at `:3087-3088`).
- **`database.py`** — no change. `save_autotune_run`'s `pbo=None` keyword parameter
  (`:711`) and its INSERT-column wiring (`:766-796`) already exist and are already
  covered by `test_pbo_migration_028.py`'s accessor-level round-trip tests.
- **`tests/autotuner/`** — new integration test (AC-2/AC-3), placed alongside the
  existing `test_pbo_*.py` files (e.g. a new `TestRunAutotunerPboIntegration` class,
  either appended to `test_pbo_migration_028.py` or a new sibling file — implementer's
  choice, but it must exercise `run_autotuner()`, not `save_autotune_run()` directly,
  to close the exact gap this plan documents).

## Edge Cases
- `_pbo_value` computed but the run is a "Reverted to Fallback" / "Reset to Global
  Default" outcome (not "Adopted AI") — `pbo` still persists (it reflects what the
  GATE evaluated, independent of which baseline ultimately won); no conditional
  gating on `baseline_decision` should be added here — the column always reflects
  the same-run PBO computation regardless of outcome.
- A run where `_pbo_veto_fired` is True — `pbo` persists the SAME value that
  triggered the veto (this is the primary auditability case AC-1 exists for).
- Legacy rows written before this fix — remain `NULL` (already the observed
  production state); this fix is forward-only, no backfill/migration required
  (`pbo` is already a NULLable column from migration 028).

## Security Considerations
- No new input surface — `_pbo_value` is an internally-computed float already
  flowing through the existing gate call; this fix only extends its lifetime by one
  more assignment into an already-authorized DB write. No user-controllable data
  path.

## Testing Strategy
- `tests/autotuner/test_pbo_migration_028.py` (or new sibling) — AC-2/AC-3: a
  `run_autotuner()`-level integration test proving the round-trip through the REAL
  call site, using realistic CSCV/CPCV fixtures (reuse existing
  `tests/autotuner/` fixture-building helpers for `haircut_trials` /
  `cscv_date_returns` rather than hand-rolling new ones — grep
  `tests/autotuner/test_pbo_cscv_date_returns_persist.py` and
  `test_pbo_acceptance_gate_veto.py` first for reusable fixture patterns before
  writing new ones).
- Regression: existing `test_pbo_migration_028.py` accessor-level tests
  (`TestSaveAutotuneRunPboRoundTrip`, `TestDsrDroppedEntirely`) must stay green
  unchanged — this fix does not touch the accessor.
- Consumer-suite discovery (house lesson,
  `feedback_consumer_suite_discovery_before_sufficiency`): grep the whole tree for
  existing tests that assert the FULL kwarg list of the `save_autotune_run(...)`
  call site in `autotuner.py` (e.g. a mock-based call-arg assertion) — a test
  hardcoding the old (pbo-less) kwarg set would need updating alongside this fix,
  not left as a stale duplicate assertion.
- Both ruff gates (`ruff format --check .` && `ruff check .`) stay green.
- PM's LIVE functional gate (Merge Workflow step 4): after merge, trigger (or wait
  for) a real weekly autotune run against a symphony with >=2 CSCV-eligible
  configs and confirm `autotune_runs.pbo` is non-NULL on the droplet DB — this is
  the actual live-data claim the audit made (40/40 NULL) and the fix must be
  verified to flip it going forward.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Persistence-only fix, zero gate/veto logic change | The audit's own finding (T1) is explicit: `_pbo_value` is correctly computed and correctly used for the live veto decision — only the DB write drops it. Touching the gate logic would be unjustified scope expansion. |
| Integration test on `run_autotuner()`, not another accessor test | `test_pbo_migration_028.py`'s existing tests already prove the accessor round-trips correctly — that is NOT the gap. The gap is specifically that nothing exercises the real call site inside `run_autotuner()`, which is exactly why this bug shipped and stayed invisible across 40 runs. |

## Scope Boundaries
- **IN:** the one-line `pbo=_pbo_value` kwarg addition at `autotuner.py:3218-3239`;
  a new `run_autotuner()`-level integration test proving the round-trip; the
  null-persists-as-null negative-case regression guard.
- **OUT:** any change to `math_engine.compute_pbo`, `acceptance_gate.py`, the veto
  threshold (`math_engine.PBO_REJECT_THRESHOLD`), the `database.py` accessor
  signature or INSERT (already correct), or any schema migration. BL-2's loop
  isolation (a separate, independently-scoped fix) — this plan does not touch the
  surrounding per-symphony loop's exception handling.

## Shipped
All 4 ACs implemented and reviewed. Commits: `332ddf83` (RED — `tests/autotuner/test_pbo_run_autotuner_persistence.py`, 3 tests) → `b28f070e` (GREEN — `autotuner.py`, `+5` lines). `quant-code-reviewer` APPROVE, zero findings. Test-writer sufficiency verdict: SUFFICIENT (one documented residual — the fixture is single-symphony; cross-symphony carryover/isolation is BL-2's separate scope, not this fix's). Full record: `DE-AUDIT-BL1-001` in `DECISIONS.md`.
