# Engine Audit — Parallelism / Reproducibility

**Feature:** Audit `n_jobs=-1` (autotuner.py:1010), the `.env`-driven
n_jobs discipline, joblib backend selection, TPESampler seeding under
parallelism, and best-params reproducibility under repeated runs at the
same seed.

**Phase:** Engine audit (post-Phase-1; correctness-discipline hardening
+ small wiring change for explicit `n_jobs` from `.env`)

**Owner agent-type:** `optuna-specialist` (drives), `sqlite-specialist`
(consults on RDBStorage contention), `quant-test-writer` (RED).

## Source-of-truth references

- `.claude/CLAUDE.md` (project) — agent operating rule 5: "Parallelism:
  read `n_jobs` from `.env`; never hardcode CPU counts. Joblib backend
  choice must match the existing pattern in `autotuner.py`."
- `autotuner.py:1010` — `study.optimize(objective, n_trials=500,
  n_jobs=-1)`.
- `autotuner.py:1003-1007` — RDBStorage with `timeout=60` (interacts
  with parallelism — see the study-persistence-versioning audit).
- The Sampler choice audit plan — TPESampler seed argument source.

## Why

Today, `n_jobs=-1` is hardcoded at the call site. This violates
project rule 5: "read `n_jobs` from `.env`; never hardcode CPU counts."

The audit's two-part contract:

1. **Read `n_jobs` from `.env`** (or environment variable equivalent).
   Provide a documented fallback to `-1` when the env var is unset.
2. **Document the determinism contract** under parallelism.
   Optuna's `TPESampler` with `seed=k` and `n_jobs > 1` is NOT
   strictly deterministic across runs — joblib's task-scheduling
   non-determinism makes the seed insufficient. Document this so
   the implementing team does not expect bit-identical best_params
   from repeated runs at the same seed under n_jobs > 1.

The full reproducibility contract — bit-identical replay parity for
Gate-1 — is **NOT** the best_params reproducibility. Gate-1 is about
replaying the SAME selected params against the same history and
getting the SAME decision record. That is single-threaded and
deterministic. The autotuner's BEST_PARAMS selection under n_jobs > 1
is a sampling-noise question, and the BHY haircut's c(N) factor is
the correctness safeguard at scale — c(N) treats trial-set
dependency robustly.

## Deliverables

### D1 — `_resolve_n_jobs()` helper

```python
import os

# Default joblib n_jobs: -1 == use all available CPU cores. Explicit
# integer (e.g. 4) caps the parallelism. Env-driven per project rule 5
# — never hardcode CPU counts.
_DEFAULT_N_JOBS = -1

def _resolve_n_jobs() -> int:
    """Read AUTOTUNER_N_JOBS from .env / environment; fall back to
    _DEFAULT_N_JOBS = -1 (all cores).

    Returns an int — never a string. Validates the parsed value is
    -1 OR a positive int; otherwise raises a clear error mentioning
    the env var name.
    """
    raw = os.environ.get("AUTOTUNER_N_JOBS")
    if raw is None or raw == "":
        return _DEFAULT_N_JOBS
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(
            f"AUTOTUNER_N_JOBS must be an integer; got {raw!r}."
        )
    if value == 0 or value < -1:
        raise RuntimeError(
            f"AUTOTUNER_N_JOBS must be -1 or a positive int; "
            f"got {value}."
        )
    return value
```

`study.optimize` becomes:
```python
study.optimize(objective, n_trials=N_TRIALS, n_jobs=_resolve_n_jobs())
```

Where `N_TRIALS = 500` is a named module-scope constant (already
implicit; the trial-floor audit plan formalizes this).

### D2 — `_resolve_optuna_seed()` helper

```python
def _resolve_optuna_seed() -> int | None:
    """Read AUTOTUNER_SEED from .env / environment; return None
    (Optuna's default — non-deterministic) if unset.

    A reproducible TPESampler seed is useful for debugging single-
    threaded runs; under n_jobs > 1, the seed is INSUFFICIENT for
    bit-identical best_params reproducibility (joblib task-scheduling
    non-determinism). See findings.md for the determinism contract.
    """
    raw = os.environ.get("AUTOTUNER_SEED")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(
            f"AUTOTUNER_SEED must be an integer; got {raw!r}."
        )
```

Used by the sampler-choice plan's D1 (`TPESampler(seed=_resolve_optuna_seed())`).

### D3 — Determinism contract documentation

A NEW comment block at `study.optimize`:

```
DETERMINISM CONTRACT:
  Under n_jobs == 1:
    With AUTOTUNER_SEED set, TPESampler produces bit-identical trial
    sequences across runs. best_params is reproducible.

  Under n_jobs > 1:
    TPESampler seeding does NOT make best_params bit-identical
    across runs — joblib's task-scheduling non-determinism + the
    constant-liar smoothing of pending trials introduces ordering
    noise. The BHY haircut's c(N) factor is the correctness
    safeguard: it accounts for the TPE-induced trial dependency
    structure regardless of the exact trial-completion order.

  Gate-1 REPLAY parity is INDEPENDENT of best_params reproducibility:
  Gate-1 replays a FIXED selected set of params against history. It
  is single-threaded by construction and bit-identical.
```

### D4 — Joblib backend documentation

Optuna's `study.optimize(..., n_jobs=...)` uses joblib under the hood.
The current pattern is the joblib default backend (loky on most
platforms). The audit documents:
- the active backend is joblib's default (loky / thread / process —
  platform-dependent);
- no `parallel_backend(...)` context manager is wrapped around
  `study.optimize`;
- per project rule 5: "Joblib backend choice must match the existing
  pattern in `autotuner.py`" — i.e. no explicit override.

### D5 — RDBStorage timeout interaction

The RDBStorage `timeout=60` (`autotuner.py:1006`; named constant in
the study-persistence-versioning plan) is sized for `n_jobs=-1`
contention on an 8-core machine. The audit cross-checks: at
n_jobs=16 or higher, the timeout may need to scale. The plan
records this as a future-watch item, not a current change.

### D6 — Determinism integration test

A NEW test:

```
T_single_thread_reproducibility —
With AUTOTUNER_N_JOBS=1 and AUTOTUNER_SEED=42, run a small autotuner
study twice (small fixture history, n_trials=10). Assert best_params
is bit-identical across the two runs.

This pins the single-threaded determinism. The test does NOT assert
multi-threaded determinism (which is the documented "not held"
contract).
```

### D7 — `_resolve_n_jobs` env-var test

A NEW test:

```
T_resolve_n_jobs —
- AUTOTUNER_N_JOBS unset → -1 (default)
- AUTOTUNER_N_JOBS="4"  → 4
- AUTOTUNER_N_JOBS="-1" → -1
- AUTOTUNER_N_JOBS="0"  → RuntimeError
- AUTOTUNER_N_JOBS="-2" → RuntimeError
- AUTOTUNER_N_JOBS="abc"→ RuntimeError
```

### D8 — Audit findings record

`findings.md`:
- Confirmation that `n_jobs` is now env-driven via
  `_resolve_n_jobs()`.
- Confirmation of the seed resolution via `_resolve_optuna_seed()`.
- Statement of the determinism contract (D3).
- Note on joblib backend (default; no override).
- RDBStorage timeout cross-check at current parallelism level.
- Empirical observation of best_params variability across repeated
  multi-threaded runs (a small benchmark — record the variance, not
  to assert a threshold, but to make the noise floor visible).

## Dependencies

- **Coupled to:** Engine Audit sampler-choice plan (the
  `_resolve_optuna_seed()` helper is consumed there).
- **Coupled to:** Engine Audit study-persistence-versioning plan
  (the RDBStorage timeout interaction).
- **Coupled to:** Engine Audit trial-floor plan (`N_TRIALS=500`
  named constant introduction in the same code region).

## Golden-fixture tests required

(D6-D7 above are themselves the tests.)

### T_n_jobs_call_site — Pin

Static-analysis-style: assert the `study.optimize(...)` call site
uses `n_jobs=_resolve_n_jobs()` AND does NOT contain a hardcoded
integer or `-1` literal in that kwarg.

### T_seed_call_site — Pin (cross-plan with sampler-choice)

Static-analysis: assert `TPESampler` is constructed with
`seed=_resolve_optuna_seed()`.

## Definition of Done

1. T_single_thread_reproducibility + T_resolve_n_jobs +
   T_n_jobs_call_site + T_seed_call_site RED on a clean implementer
   commit, GREEN after.
2. `pytest tests/autotuner/` PASSES — unchanged behaviour (the
   default fallback to `-1` preserves existing behaviour when
   `AUTOTUNER_N_JOBS` is unset).
3. `findings.md` committed.
4. `_resolve_n_jobs()` and `_resolve_optuna_seed()` live in
   `autotuner.py` with the documented determinism contract block.
5. The `.env` template (if any) gains AUTOTUNER_N_JOBS and
   AUTOTUNER_SEED with default-value comments.
6. Commit message: `feat(autotuner): study_name=<TS>__<symphony>,
   n_jobs env-driven (project rule 5) + seed env-driven + determinism
   contract documented; n_trials=500; objective=<unchanged>`.

## Risk callouts

- **Default-fallback compatibility.** With `AUTOTUNER_N_JOBS` unset,
  the default is `-1` — preserves existing behaviour exactly. The
  rule-5 violation (the hardcoded `-1`) is closed by the env-driven
  resolution; the value at runtime is unchanged in the unset case.
- **Determinism overclaim.** A maintainer might add a docstring
  claim that "the autotuner is reproducible with a seed." That is
  TRUE under n_jobs=1 and FALSE under n_jobs>1. The D3 documentation
  block makes the contract precise; T_single_thread_reproducibility
  pins the n_jobs=1 case explicitly.
- **`AUTOTUNER_N_JOBS=0` mistake.** `n_jobs=0` is meaningless;
  joblib accepts it as "no parallelism" but it is not a documented
  contract. T_resolve_n_jobs rejects it loudly.
- **`AUTOTUNER_N_JOBS` very high.** A user setting it to 64 on a
  4-core machine would spawn 64 joblib workers contending on the
  SQLite RDBStorage. The 60s timeout (study-persistence-versioning
  plan) absorbs some contention; beyond ~16 the contention starts
  dominating. The plan does NOT cap the value (it is an operator
  policy); it documents the interaction.
- **Joblib backend swap smuggle.** A future PR wraps
  `study.optimize` in `with parallel_backend("threading"):` to
  "fix a bug." That changes the active backend — rule-5 violation.
  No current test catches this; the audit findings.md records the
  current backend and PR reviewers must scrutinize backend changes.
  (A static-analysis-style negative pin for `parallel_backend` is
  recommended but optional.)
- **Best-params variance under multi-threaded runs.** The audit
  records the empirical variance, not asserts it. The BHY haircut's
  c(N) is the correctness safeguard; variance in BEST_PARAMS is
  NOT a correctness problem at scale — it is sampling noise the
  haircut accounts for.

## Out of scope

- Capping `n_jobs` at runtime — operator-policy, not autotuner
  policy.
- Changing the joblib backend — rule-5 violation if changed.
- Multi-threaded determinism — explicitly documented as "not held"
  per the contract.
- Replay parity (Gate-1) — INDEPENDENT of best_params
  reproducibility; owned by replay-parity tests elsewhere.
- The TPE seed value itself — operator policy (set via env).
- RDBStorage timeout sizing — owned by the study-persistence-
  versioning plan; this audit only cross-checks at the current
  parallelism scale.
