# Feature: xdist Test-Isolation Fix
Status: ready
Created: 2026-06-13

## Summary

Running the test suite under pytest-xdist (`-n auto` / `-n 2`) produces spurious test-isolation failures that do not reproduce at `-n0`. The "85 failures" observed on Cycle-4 main were xdist artifacts — the same tree passed `125/0` at `-n0`. This forces the PM to gate every merge at `-n0` (via `-o addopts= -p no:xdist`), which is slower and a standing workaround. Separately, `-n auto` recursive subprocesses combined with nested joblib `n_jobs=-1` were a PC-crash root cause; `pyproject.toml` is pinned to `-n 2 --dist loadfile` (since `a7f2bac`) as a memory-safety cap. This feature root-causes and fixes the isolation failures so `-n2` gives the same results as `-n0`, removing the merge-gate workaround — without regressing the crash-safety cap.

## Acceptance Criteria

- [ ] AC-1: Root-cause the isolation failures: identify the shared mutable state (DB path, module-level singletons, file handles, working dir, Optuna study names, env vars) leaking across xdist workers. Deliver a `file:line` diagnosis BEFORE any fix code is written (diagnose-before-fixing rule).
- [ ] AC-2: The full test tree passes identically at `-n0` and `-n2` — verified across ≥10 runs on each setting with no flaky delta between them. Flake replication on BOTH counts before declaring fixed.
- [ ] AC-3: The crash-safety story is preserved: no unbounded subprocess/joblib nesting is reintroduced; memory stays bounded under the parallel run.
- [ ] AC-4: Fixes target the real isolation defect (conftest fixtures, per-worker temp DBs, `--dist loadfile` grouping, env var isolation) — not blanket test skips or `@pytest.mark.xfail` wrappers.

## Architecture

**Root-cause candidates to investigate (inform architecture once diagnosed):**
- `tests/conftest.py` `_isolate_db` autouse fixture — does the per-test temp DB path collide across xdist workers? Each worker needs a truly unique path (e.g. include `os.getpid()` or a worker-scoped temp dir).
- `database._db_file()` pytest sentinel — is it worker-safe? Does `DB_PATH` env var get inherited correctly by all workers?
- Module-level singletons or cached state in `database.py`, `ai_advisor.py`, `advisors/lens_pipeline.py` — any `@lru_cache` or module-level globals that are shared across tests?
- Optuna study names — do parallel tests try to create/open the same study name? (Known gotcha: study names must be unique per `<timestamp>__<symphony>` pattern.)
- `--dist loadfile` grouping — are test files that share state grouped onto the same worker? If not, cross-worker state bleeds.
- Working directory — does any test assume `os.getcwd()` is the project root? xdist workers may run from different directories.
- `pytest_configure` hook order — does `DB_PATH` get set before any module-level DB import under xdist? (The sentinel and `conftest.py` already address this for single-process runs; xdist may bypass the hook for workers.)

**Files likely changed (after diagnosis):**
- `tests/conftest.py` — per-worker DB temp path; worker-scoped env var isolation; `--dist loadfile` groupings for state-sharing test files
- `pyproject.toml` — potentially adjust `--dist` strategy or `addopts` while preserving `-n 2` crash cap
- Individual test files — fix any test that mutates module-level state without teardown

**No changes to production code** unless the diagnosis reveals a production-code bug (e.g. a module-level singleton that should not be module-level).

## Design-System Mapping

N/A — backend feature, no UI surface. (All 10 are backend/infra; the Cycle-5 Market Prism Overview UI already shipped separately.)

## Edge Cases

- **Flaky failures on `-n0`:** if the full tree has pre-existing flakes at `-n0`, those are separate from the xdist isolation issue. Classify failures by branch (pre-existing vs. xdist-caused) before treating any as a blocker.
- **Diagnosis reveals a production-code bug:** if root-cause is a module-level singleton in production code (not just test fixtures), that is a separate fix tracked in DECISIONS.md. Do not conflate with the isolation fix.
- **`-n2` fixes but `-n auto` regresses:** the crash-safety cap (`-n 2`) stays; the fix only needs to make `-n2` reliable. `-n auto` is not the target.
- **Worker count variation:** on different machines (local vs. CI), worker count may differ. The fix must be robust to any worker count ≤ 2 (the cap), not tuned to a specific worker count.
- **NEVER run two pytest invocations at once:** crash history. The ≥10-run validation must be sequential, not parallel.

## Security Considerations

- **Input validation / injection:** N/A — test infrastructure change only. No new external inputs.
- **Data exposure:** test isolation prevents test data from leaking to production DB. The fix strengthens this guarantee — it does not weaken it.
- **Crash safety:** the `-n 2` cap must not be removed. The crash-safety constraint (no unbounded subprocess/joblib nesting) is a hard non-regression requirement. If the fix requires removing the cap, escalate to the PM — do not silently remove it.
- **No production-path changes:** unless diagnosis reveals a production bug. Any production-code change must follow the standard Toxic Pair TDD process.

## Testing Strategy

**Phase 1 — Diagnosis (non-TDD team):**
- Dispatch a non-TDD audit team (auditor + synthesizer) to root-cause the failures with `file:line` evidence. No code written in this phase.
- The diagnoser runs the full suite under `-n2` to capture failing test names, then re-runs those tests at `-n0` to confirm the delta. Documents the shared mutable state causing each class of failure.

**Phase 2 — Fix (Toxic Pair TDD for any new conftest/fixture codepaths):**
- `test_xdist_isolation.py` (new, optional) — if a fix introduces new conftest logic, add a test that simulates two workers accessing the same resource and asserts they get isolated paths
- The validation bar (AC-2) IS the test: ≥10 runs at `-n2` and ≥10 runs at `-n0` must show identical pass counts

**Flake-replication protocol (per project memory `feedback_n_run_flake_check_before_blaming_cycle`):**
- Run the suite ≥10x on `-n2` on the fix branch AND ≥10x on `-n2` on the fork-point baseline before declaring fixed
- Any failure that appears at `-n2` on the fix branch and NOT at `-n0` is a remaining isolation defect
- A failure that appears at the same rate on both branches is a pre-existing flake (separate remediation)

**Run protocol:** `DB_PATH` set via `tests/conftest.py`; NEVER run two pytest invocations at once; one run at a time; targeted scoping for diagnosis but full-tree for validation.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Diagnosis team first (non-TDD) | Diagnose-before-fixing rule: a `file:line` root cause is required before any fix code is written |
| Preserve `-n 2` crash-safety cap | The PC-crash root cause was unbounded subprocess nesting; removing the cap risks a repeat |
| Fix conftest/fixtures, not production code | The isolation failure is most likely a test-infrastructure problem; do not touch production code unless diagnosis proves otherwise |
| ≥10-run flake replication before declaring fixed | Per project memory `feedback_n_run_flake_check_before_blaming_cycle`; single-run signals are not sufficient |
| Blanket skips are not acceptable | Skipping tests to hide isolation failures defeats the purpose; fixes must target the real defect |

## Scope Boundaries

- **IN**: root-cause diagnosis with `file:line` evidence; fixes to `tests/conftest.py` and test files for isolation; ≥10-run validation at `-n2` and `-n0`; doc-gen updates to `DECISIONS.md`
- **OUT**: removing the `-n 2` crash-safety cap; production-code changes (unless diagnosis reveals a production bug); fixing pre-existing test failures unrelated to xdist isolation; enabling `-n auto`

**Dependencies:** none hard. Lower priority than Epic A. This unblocks faster merge gates for all other features — but Epic A is the exclusive focus until its observed proof run.

**Team note:** non-TDD audit team (auditor + synthesizer) for diagnosis, then Toxic Pair TDD (quant-test-writer + implementer) for fix if conftest changes are new codepaths. Watch: `database._db_file()` pytest sentinel, `_isolate_db` autouse fixture, `pytest_configure` DB_PATH wiring. NEVER run two pytest invocations at once.
