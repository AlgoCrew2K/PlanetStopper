# advisors/backtest_gate_engine

> M2 backtest-and-gate engine: fold-transforms Composer backtest return series into walk-forward folds and runs batch BHY/Yekutieli FDR + acceptance gate over the full candidate set; C5b (2026-06-20) adds batch PBO veto and real SPY-OOS-fold baseline; AC-D3 (2026-07-12) fixes candidate-order dependence in the bootstrap seed; **AC-1/AC-2 (2026-07-17, `DE-MATH-R0-001`) fix the PBO veto's unit corruption** — the batch PBO boundary now converts percent-scale `dated_returns` to decimal before calling `math_engine.compute_pbo`, and `_BATCH_PBO_GAMMA` is aligned to the frozen Phase-1 THEORY gamma instead of a nonexistent constant citation.

**Source:** `advisors/backtest_gate_engine.py`
**Last updated:** 2026-07-17 (math-r0, `DE-MATH-R0-001` — AC-1 PBO percent-to-decimal unit boundary fix, closes `DE-MATH-AUDIT-001` MA-3 CRITICAL, + AC-2 THEORY-gamma alignment, closes M2)
**Prior update:** 2026-07-13 (advisor-remediation-r1, DE-ADVISOR-R1-001 — AC-17 panel-tie neutralization + AC-7b 4th rejection class + AC-4/5 gate-strength parity for Asset Swaps/Logic Changes)

## Overview

`advisors/backtest_gate_engine.py` is the reusable M2 spine called by the Strategy Builder's `propose_strategies` (and previously by M3/M4 proposal handlers). For each batch of advisor candidates it:

1. Receives `BacktestCandidate` objects — each carrying a Composer backtest return series plus optional date-keyed returns for C5b PBO/SPY computation.
2. **C5b Step 0a:** Computes a batch-level PBO (`math_engine.compute_pbo`) over the intersection of all candidates' date-keyed returns, then threads it into every `evaluate_acceptance_gate` call as `pbo=_batch_pbo`.
3. **C5b Step 0b:** Computes a real SPY-OOS baseline — aligns SPY's date-keyed returns to the candidate date span (intersection), fold-transforms via the same `_fold_transform_single`, and uses the resulting validation-fold OOS alpha as `default_oos_alpha`. SPY-unavailable degrades conservatively to `float("+inf")` — see edge-14 note below.
4. Applies the fold-transform: slices every candidate's return series into the autotuner's walk-forward fold structure (60/20/20 TRAIN/VALIDATION/FROZEN-EVAL with PURGE_DAYS + EMBARGO_DAYS boundary purge).
5. Runs BHY/Yekutieli FDR across the full candidate set (`n_effective = len(candidates)` — the honest multiple-testing count).
6. Calls `acceptance_gate.evaluate_acceptance_gate` unchanged for each candidate, feeding fold-derived inputs and the batch PBO.
7. Returns a `GatedBatch` — each candidate annotated with its gate verdict, `rejection_reason`, and honest caveats.

Off-execution-path: MUST NOT be imported or called from `alpha_bot_execution.py` (architecture constraint #1).

## Load-Bearing Soundness Requirements

- **One-directional-brake invariant:** no discretionary score can resurrect a veto-failed candidate.
- **Honest multiple-testing count:** `n_effective = len(candidates)` — identical to the autotuner's semantics (autotuner.py:_haircut_select docstring, plan D3).
- **Purge integrity:** fold-split respects `PURGE_DAYS` on the validation-fold boundary. Too-short series → WITHHOLD (never fabricate).
- **NN1 compliance:** Composer backtest trees are not BACKTEST_SELECTION-spec facets; `nn1_compliant=True` is correct for all Composer backtest paths.
- **C5b SPY date-alignment (not positional):** SPY is aligned to the candidate date span via date intersection BEFORE fold-transform; positional-only alignment would land the fold window on different calendar dates for a longer SPY series, producing a wrong baseline.
- **C5b edge-14 (+inf, not -inf):** `_SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA = float("+inf")`. Using `-inf` would make the `oos_alpha <= default_oos_alpha` withhold-clause in `acceptance_gate.py:257` always-false for finite candidates, collapsing to beats-zero — the exact behaviour AC-25 edge-14 forbids. With `+inf` the clause is always-true → KEEP_INCUMBENT (conservative WITHHOLD) for every finite candidate when SPY is unavailable.
- **AC-D3 order-independence (2026-07-12):** `evaluate_candidate_batch`'s output for a FIXED candidate set must not depend on the order those candidates were submitted in. See "Bug Fix — Order-Dependent Bootstrap Seed" below.
- **Percent-to-decimal boundary (AC-1, `DE-MATH-R0-001`, 2026-07-17):** `dated_returns` arrives percent-scale from every producer; `math_engine.compute_pbo` requires decimal. The conversion happens exactly once, at this module's batch-PBO boundary — never at the producer, and never mutating the caller's `BacktestCandidate.dated_returns` dict.

## Constants

### Fold-transform constants (imported from autotuner — single source of truth)

| Constant | Source | Description |
|----------|--------|-------------|
| `TRAIN_RATIO` | `autotuner` | 0.60 — train fold fraction |
| `VALIDATION_RATIO` | `autotuner` | 0.20 — validation fold fraction |
| `PURGE_DAYS` | `autotuner` | Boundary purge width (train-side) |
| `EMBARGO_DAYS` | `autotuner` | Embargo width at train-validation boundary |
| `HARVEY_LIU_FDR_Q` | `autotuner` | BHY FDR significance level |
| `RETURN_PCT_TO_FRACTION` | `autotuner` | `100.0` — divisor used at the batch-PBO boundary (AC-1) to convert percent-scale `dated_returns` to decimal before `math_engine.compute_pbo`; same named constant the autotuner's own PBO path already divides by (`autotuner.py:2369-2374`) |
| `FOLD_TRANSFORM_MIN_VALIDATION_DAYS` | local | 5 — minimum validation days for a defensible t-stat |
| `FOLD_TRANSFORM_MIN_TOTAL_DAYS` | local | Derived minimum total series length: `ceil((PURGE_DAYS + EMBARGO_DAYS + 5) / (1 - TRAIN_RATIO))` |

### C5b constants

| Constant | Value | Description |
|----------|-------|-------------|
| `_BATCH_PBO_GAMMA` | `float(database.PHASE1_THEORY_GAMMA)` (`2.0`) | CRRA risk-aversion coefficient passed to `math_engine.compute_pbo`; aligned to the frozen Phase-1 THEORY gamma (`database.PHASE1_THEORY_GAMMA` is a `str`, cast via `float()` — same pattern as `autotuner.py:1592`). **Fixed 2026-07-17 (`DE-MATH-R0-001` AC-2):** pre-fix this cited a nonexistent `autotuner.py: GAMMA = 1.0` constant and diverged from the autotuner's own PBO gate for the "same" decision (`DE-MATH-AUDIT-001` M2). |
| `_PBO_MIN_CONFIGS` | `2` | Minimum number of date-keyed configs to compute a meaningful batch PBO; fewer → `pbo=None`, veto does not fire |
| `_PBO_MIN_ALIGNED_DATES` | `8` | Minimum intersection dates across all configs; fewer → `pbo=None` (CSCV needs ≥1 date per block, S=8 blocks) |
| `_SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA` | `float("+inf")` | Conservative SPY-unavailable sentinel. `+inf` makes `oos_alpha <= default_oas_alpha` always-true for every finite candidate → KEEP_INCUMBENT (conservative WITHHOLD). `-inf` would make it always-false → beats-zero fallback, which AC-25 edge-14 forbids. Withheld candidates carry `rejection_reason="below_spy_alpha"`. |
| `SPY_BENCHMARK_TICKER` | `"SPY"` | US equity broad-market benchmark ticker (SPDR S&P 500 ETF) for the AC-25 OOS-fold baseline |

### AC-D3 constants (2026-07-12)

| Constant | Value | Description |
|----------|-------|-------------|
| `_STABLE_SEED_DIGEST_BYTES` | `4` | Number of leading SHA-256 digest bytes used to derive a candidate's bootstrap seed — 4 bytes → a 32-bit unsigned int, comfortably within `numpy.random.default_rng`'s accepted seed range. |

### Caveat constants

| Constant | Description |
|----------|-------------|
| `SURVIVOR_OVERFITTING_CAVEAT` | Mandatory caveat appended to every `ADOPT_CANDIDATE` result |
| `THIN_WINDOW_CAVEAT` | Appended when validation window is below `FOLD_TRANSFORM_MIN_VALIDATION_DAYS` |

## Public Types

### `BacktestCandidate` (NamedTuple)

One advisor-proposed variant to be fold-transformed and gated.

| Field | Type | Description |
|-------|------|-------------|
| `candidate_id` | `str` | Opaque identifier for operator traceability. **AC-D3: also the sole input to `_stable_seed_from_candidate_id` — the bootstrap seed source.** |
| `daily_returns_pct` | `list[float]` | Chronologically ordered daily returns in percent; used for fold-transform and BHY t-stat |
| `candidate_params` | `dict` | Parameter vector for panel stability scoring (D2) |
| `incumbent_params` | `dict` | Live incumbent's parameter dict for stability comparison |
| `theory_prior_params` | `dict` | Theory-anchor parameter dict for prior-anchor scoring (D4) |
| `nn1_compliant` | `bool` | Default `True` for all Composer backtest paths; override for audit |
| `purge_integrity_ok` | `bool` | Default `True`; series-derived check wins over caller-supplied `True` |
| `dated_returns` | `dict[str, float]` | **C5b (AC-24/25).** Date-keyed returns (`"YYYY-MM-DD" -> pct`), **percent-scale** as written by every producer. Enables batch PBO computation and SPY date-alignment. Default `{}` — callers that omit this field receive `pbo=None` (PBO veto does not fire) and the SPY-unavailable conservative WITHHOLD. In production `propose_strategies` populates this from `result.daily_returns` pct-scaled. **AC-1 (`DE-MATH-R0-001`):** this dict is NEVER mutated by `evaluate_candidate_batch` — the percent-to-decimal conversion happens on a fresh copy at the `compute_pbo` call boundary, so other callers that read `dated_returns` still see its native percent scale. |

### `CandidateGateResult` (NamedTuple)

Gate result for one candidate.

| Field | Type | Description |
|-------|------|-------------|
| `candidate_id` | `str` | Echoes the `BacktestCandidate` identifier |
| `verdict` | `AcceptanceVerdict` | From `acceptance_gate.evaluate_acceptance_gate` |
| `validation_days` | `int` | Days in the post-purge validation fold |
| `oos_alpha` | `float` | Sum of validation-fold daily returns (percent) |
| `caveats` | `list[str]` | Plain-text caveats; always non-empty for `ADOPT_CANDIDATE` |
| `winner_p_adj` | `float \| None` | BHY-adjusted p-value for this candidate (audit trail) |
| `rejection_reason` | `str \| None` | **C5b + AC-7b.** Why the candidate was culled, or `None` for survivors. FOUR possible non-`None` values as of AC-7b (2026-07-13, `3fa2e7f8`): `pbo_veto`, `below_spy_alpha`, `fdr_not_winner`, `oos_inferior_to_incumbent`. Deterministic stage-order precedence — see below. On SPY-unavailable, withheld candidates carry `"below_spy_alpha"` (the `+inf` sentinel makes the alpha-gate clause always-true). |

#### `rejection_reason` stage-order precedence (C5b + AC-7b, final at `3fa2e7f8`, 2026-07-13)

The precedence ensures the most-specific cause is recorded. A candidate that triggers multiple gates reports the highest-priority cause:

| Priority | Value | Condition |
|----------|-------|-----------|
| 1 (highest) | `"pbo_veto"` | `_batch_pbo is not None and _batch_pbo > PBO_REJECT_THRESHOLD` |
| 2 | `"below_spy_alpha"` | `spy_returns_fn is not None and fold.oos_alpha <= _effective_default_oos_alpha` — also fires on SPY-unavailable (sentinel is `+inf`, always-true for finite alpha) |
| 3 | `"fdr_not_winner"` | **EXPLICIT `this_winner_trial_is_none` check** (AC-7b, `3fa2e7f8` — no longer a blind catch-all): BHY non-winner, nn1 failure, purge failure, or thin-window |
| 4 (new, AC-7b) | `"oos_inferior_to_incumbent"` | The candidate IS the BHY winner (`this_winner_trial_is_none` is False) but still loses the OOS-superiority comparison against the incumbent (`acceptance_gate.py:257`). |
| 5 (lowest — survivor) | `None` | `verdict.decision == "ADOPT_CANDIDATE"` |

**AC-7b fix (`3fa2e7f8`, per the audit's F6 finding):** pre-fix, priority-4 candidates were MISLABELED `"fdr_not_winner"` — a genuine FDR winner that lost only on OOS-superiority was indistinguishable, in every persisted record and rendered UI surface, from a true BHY non-winner. The former blind `"fdr_not_winner"` catch-all is now an EXPLICIT `this_winner_trial_is_none` check; a candidate that IS the winner but still loses to the incumbent gets the new `"oos_inferior_to_incumbent"` token instead.

PBO veto (`pbo_veto`) dominates `below_spy_alpha` because PBO is the `acceptance_gate` Stage-1 hard veto — a high-PBO batch is too sample-dependent to consider further, regardless of alpha. A candidate that is both high-PBO and below-SPY reports `"pbo_veto"` so the operator can see which gate fired first.

The `"pbo_veto"` string deliberately contains `"pbo"` (lowercase) so live-probe scripts can identify PBO culls with a case-insensitive substring check.

### `GatedBatch` (NamedTuple)

Result of `evaluate_candidate_batch`.

| Field | Type | Description |
|-------|------|-------------|
| `results` | `list[CandidateGateResult]` | Per-candidate results in input order |
| `survivors` | `list[CandidateGateResult]` | Results where `verdict.decision == "ADOPT_CANDIDATE"` |
| `n_candidates` | `int` | Total candidates (FDR denominator audit trail) |
| `fdr_q` | `float` | `HARVEY_LIU_FDR_Q` used for the correction |

## API Reference

### `evaluate_candidate_batch(candidates, *, incumbent_oos_alpha, default_oos_alpha, spy_returns_fn) -> GatedBatch`

Fold-transform a batch of Composer backtest candidates and run them through the gate.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `candidates` | `Sequence[BacktestCandidate]` | — | Batch to gate; empty input returns an empty `GatedBatch` |
| `incumbent_oos_alpha` | `float` | `0.0` | Live incumbent's OOS alpha; used as `fallback_oos_alpha` in the gate (KEEP_INCUMBENT when candidate does not beat it) |
| `default_oos_alpha` | `float` | `0.0` | Global-default params' OOS alpha; overridden by the SPY-fold baseline when `spy_returns_fn` is supplied and non-empty |
| `spy_returns_fn` | `Callable[..., dict] \| None` | `None` | **C5b (AC-25) injectable seam.** Returns SPY's date-keyed returns (`dict[str, float]`). When supplied and non-empty: SPY series is date-aligned to the candidate span (intersection, not positional), fold-transformed via `_fold_transform_single`, and the resulting validation-fold OOS alpha replaces `default_oos_alpha`. When `None` or the callable returns `{}`: `_SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA` (`float("+inf")`) is used — the `oos_alpha <= default_oos_alpha` clause in `acceptance_gate.py:257` is always-true for finite candidates, so every candidate WITHHOLDS with `rejection_reason="below_spy_alpha"` (conservative, never silent fallback to beats-zero). In production `propose_strategies` passes `spy_returns_fn=lambda: _spy_returns_dict` where `_spy_returns_dict` is sourced via `run_backtest` on a 100%-SPY tree. Tests inject a fixed fixture dict. |

**Returns:** `GatedBatch`

**Pipeline:**

```
C5b Step 0a: batch PBO — dated_returns converted percent→decimal (AC-1), then
             math_engine.compute_pbo over the intersection, gamma=_BATCH_PBO_GAMMA
C5b Step 0b: SPY-fold baseline (align SPY to candidate dates → _fold_transform_single)
Step 1: _fold_transform_single per candidate (60/20/20 + purge)
Step 2: compute_sortino_tstat per candidate (seed=_stable_seed_from_candidate_id(cand.candidate_id) — AC-D3, see below)
Step 3: BHY/Yekutieli FDR over full batch (n_effective = N)
Step 4: BHY winner = argmin p_adj over veto-eligible candidates
Step 4.5 (AC-17, 2026-07-13): panel-tie neutralization when both
         candidate_params and incumbent_params are structurally empty
         (see "Panel-Tie Neutralization" below)
Step 5: evaluate_acceptance_gate per candidate (pbo=_batch_pbo, default_oos_alpha=_effective_default_oos_alpha)
        → rejection_reason cascade (pbo_veto > below_spy_alpha > fdr_not_winner > oos_inferior_to_incumbent)
```

**FDR integrity invariant:** `evaluate_candidate_batch` must receive ALL successfully-backtested candidates — built-new/Fable-generated and Atlas-suggested together — in one call. Screens NEVER shrink the gate input. `n_effective = len(candidates)` is the honest multiple-testing count; raising N raises the correction bar. **Gate-strength parity across all three advisor engines, closed 2026-07-13 (AC-4/5, `82479560`):** this invariant, the PBO veto, and the SPY-relative OOS baseline documented above were previously wired ONLY for Strategy Builder's `evaluate_candidate_batch` call sites — Asset Swaps and Logic Changes passed neither `dated_returns=` nor `spy_returns_fn=`, so their PBO veto could never fire and their OOS baseline was "beats a flat 0.0% return," not SPY-relative (the advisor-intent audit's F2 finding). **This gap is now closed:** `dated_returns=` is threaded into `BacktestCandidate` construction at the single `_evaluate_single_variant` site shared by both engines (reaching every real gate call, operator N=1 and weekly batch alike, automatically); a new `_spy_returns_fn_for(symphony_id)` helper in both engines (mirroring `strategy_builder_engine.py:807-826`) is wired at all 4 real gate calls (`asset_swap_engine.py`'s `propose_operator_swap` + `suggest_swaps`, `logic_change_engine.py`'s `propose_operator_logic_change` + `suggest_logic_changes`). All three engines now gate on genuinely equivalent statistical machinery. The `_PBO_MIN_CONFIGS=2` guard (audit-proved load-bearing — K=1 -> PBO=1.0 always -> would veto everything) is untouched, so PBO stays structurally `None` at N=1 (the operator's single-candidate Evaluate buttons) as designed. See `DECISIONS.md` `DE-ADVISOR-R1-001` §AC-4..6 for the full record.

## Panel-Tie Neutralization (AC-17, 2026-07-13, `3fa2e7f8`)

**The defect this closes:** all three advisor engines construct `BacktestCandidate` with structurally empty `candidate_params`/`incumbent_params`/`theory_prior_params` on every real production path. This made `candidate_panel_score` the CONSTANT `0.5` against the incumbent's CONSTANT `0.75` (hardcoded `inc_stability=1.0`) — so the panel-comparison clause in `acceptance_gate.py` was **false unconditionally, regardless of actual candidate OOS performance.** `ADOPT_CANDIDATE` was mathematically unreachable on every reachable production path until this fix (advisor-intent audit's finding, PM adjudication @ `ad9b1629`).

**The fix:** when `candidate_params` AND `incumbent_params` are BOTH structurally empty (every current engine construction site builds candidates this way), `cand_stability` is tied to `inc_stability` (an exact tie) instead of falling through to `_compute_parameter_stability_score`'s asymmetric-neutral fallback. The adoption decision then rests entirely on the OOS-superiority precondition (`acceptance_gate.py:257`) plus the three hard vetoes (BHY winner, PBO, SPY baseline). Requires BOTH sides empty — a one-side-empty shape (a caller bug) does NOT trigger the tie. `panel_breakdown` carries `{"note": "not applicable — no parameter-vector representation"}` (the `_PANEL_NA_NOTE` constant) whenever the tie condition fired, REGARDLESS of the eventual decision, so a downstream reader is never misled into thinking a real panel score was evaluated. **Deliberately NOT fixed by populating real params instead** — real params without a real theory-prior require `stability >= 1.0`, satisfiable only by zero-change candidates; that path was ruled out algebraically, not merely deprioritized. `acceptance_gate.py` and `autotuner.py` received ZERO diff for this fix — it is contained entirely to this module.

**[PM-ASSUMED] marker (operator may overrule):** this changes the advisor suite's adoption semantics — candidates can now actually be adopted where none ever could before. See `DECISIONS.md` `DE-ADVISOR-R1-001` §AC-17 for the full proof, the narrative-correction this forces (the long-standing "0 survivors is expected — the gate is intentionally strict" explanation was incomplete; structural unreachability was the dominant cause, gate strictness secondary), and the doc-tree sweep this proof triggered.

**C5b batch PBO details (AC-24; unit boundary fixed 2026-07-17, `DE-MATH-R0-001` AC-1):**

- `dated_returns` dicts from all candidates are PERCENT-scale as written by every producer (`composer_backtest_client.py:182` -> `strategy_builder_engine.py:997`, `asset_swap_engine.py:954`, `logic_change_engine.py:677`, `frontrunner_builder.py:1605`) — collected into a list of configs, **converted to DECIMAL scale at this boundary** (`pct / RETURN_PCT_TO_FRACTION`, a fresh dict per candidate, never mutating `candidate.dated_returns`, which other callers still consume in its native percent scale) before being passed to `math_engine.compute_pbo`, which requires decimal returns (`math_engine.py:1939-1941`).
- If `len(configs) >= _PBO_MIN_CONFIGS` (2) AND the intersection of all date keys has `>= _PBO_MIN_ALIGNED_DATES` (8) dates, `math_engine.compute_pbo` is called with the intersection dates and `_BATCH_PBO_GAMMA=float(database.PHASE1_THEORY_GAMMA)` (2.0).
- Result is threaded into every `evaluate_acceptance_gate(pbo=_batch_pbo)` call.
- If either condition is not met, `_batch_pbo` stays `None` — the veto correctly does NOT fire (no false reject on thin batches).
- In production, `propose_strategies` populates `BacktestCandidate.dated_returns` from `result.daily_returns` with date keys preserved and values pct-scaled (`r * 100.0`), identical to the `daily_returns_pct` scale — the percent-to-decimal conversion happens ONLY at the `compute_pbo` call boundary inside `evaluate_candidate_batch`, never at the producer.
- Mirrors `autotuner.py:2699-2711` wiring pattern **and** the identical unit-boundary fix the autotuner already applied to its own PBO path (`autotuner.py:2369-2374`) — the advisor path never received it until this cycle.

**Pre-fix defect (`DE-MATH-AUDIT-001` MA-3, CRITICAL; fixed `DE-MATH-R0-001` AC-1, commit `616da6b0`):** before this fix, `compute_pbo` received the percent-scale values UNCHANGED — a single -2% day scored `U=-6.908` (wealth-floor saturation in `compute_crra_eu_objective`) instead of the correct `-0.0202`, corrupting the IS-best/OOS ranking the veto depends on and flipping the veto decision arbitrarily w.r.t. an accidental unit scale (40/60 and 7/20 seeded probe flips, two independent DGPs, per the audit). Golden fixture (`tests/fixtures/math/pbo_unit_boundary_flip.json`): PBO=0.8714 (vetoes) at decimal scale vs PBO=0.1714 (passes) at percent scale, identical data. See `DECISIONS.md` `DE-MATH-R0-001` §AC-1/AC-2 for the full record.

**C5b SPY-fold baseline details (AC-25, edge-14):**

- SPY dates are restricted to the union of candidate dates (intersection of SPY dates with candidate span) so the fold window lands on the same calendar dates as the candidates.
- The aligned SPY value list is fed to the same `_fold_transform_single` used for candidates.
- SPY-unavailable (empty series or callable error): `_effective_default_oos_alpha = _SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA = float("+inf")`. The `+inf` sentinel makes `oos_alpha <= default_oos_alpha` always-true for every finite candidate → KEEP_INCUMBENT (conservative WITHHOLD). Withheld candidates carry `rejection_reason="below_spy_alpha"`.
- **Edge-14 inversion (4ccea92):** the original implementation used `float("-inf")`, which made the withhold-clause always-false → collapsed to beats-zero. Fixed to `float("+inf")` at commit 4ccea92.

**Atlas parity (AC-26):**

Atlas community candidates and built-new (accessor-driven, currently Fable per AC-16, `model_config.get_advisor_suggestion_model()`) candidates flow through the SAME call, receive the SAME batch PBO, the SAME SPY-fold baseline, and the SAME BHY/Yekutieli FDR correction. Advertised community `oos_metrics` are structurally inert in the gate (parameter stability scoring only float-coerces shared param keys; dict metrics never influence survival). Identical fresh return series produce identical gate verdicts regardless of provenance.

## Bug Fix — Order-Dependent Bootstrap Seed (AC-D3, 2026-07-12)

**The bug:** Step 2 above seeded `compute_sortino_tstat`'s nonparametric bootstrap with `seed=idx`, where `idx` was the candidate's `enumerate()` position within the `candidates` argument — a property of the BATCH SUBMISSION ORDER, not of the candidate itself. `compute_sortino_tstat` forwards `seed` into `compute_sortino_se_bootstrap` (`autotuner.py`, `numpy.random.default_rng(seed)`), so reordering (e.g. reversing) the SAME candidate set reassigned different seeds to each candidate — producing a different bootstrap standard error, t-stat, and BHY-adjusted p-value for the IDENTICAL candidate and return series, purely as a function of submission order. This violated the "`evaluate_candidate_batch` output is unchanged for a fixed candidate set" invariant (AC-D3), and became directly observable once Workstream D's lens-blend fix started genuinely reordering candidates ahead of the gate call.

**The fix:** `_stable_seed_from_candidate_id(candidate_id: str) -> int` — a new helper that derives the seed from a SHA-256 hash of the candidate's own `candidate_id` (truncated to `_STABLE_SEED_DIGEST_BYTES` = 4 bytes → a 32-bit unsigned int), not the builtin `hash()` (CPython randomizes `hash(str)` per-process via `PYTHONHASHSEED` unless disabled, so it would not be reproducible across process restarts — violating `compute_sortino_tstat`'s own "reproducible under a fixed trial set" contract). The same candidate now always gets the same seed regardless of what order it was submitted in or who else is in the batch.

**Scope:** minimal, single call site (`evaluate_candidate_batch`'s Step 2 loop). `autotuner.py` (`compute_sortino_se_bootstrap`, and `_haircut_select`'s own `seed=trial_idx` for its DIFFERENT never-reordered-Optuna-study context) was explicitly NOT touched — different seed-derivation context, different owning module.

**Authorization:** this was outside Workstream D's stated scope boundary ("do NOT change `evaluate_candidate_batch`") but was authorized by the PM as a scoped exception specifically for this order-dependence bug, discovered while verifying Workstream D's own AC-D3 test.

## Internal Helpers

### `_stable_seed_from_candidate_id(candidate_id: str) -> int` (AC-D3, 2026-07-12)

Deterministic bootstrap seed derived from a candidate's own `candidate_id` via `hashlib.sha256(candidate_id.encode("utf-8")).digest()[:_STABLE_SEED_DIGEST_BYTES]`, big-endian int conversion. Returns a non-negative int in `[0, 2**32)` suitable for `numpy.random.default_rng`. See "Bug Fix" above.

### `_fold_transform_single(daily_returns_pct) -> _FoldResult`

Slices a daily return series into the walk-forward fold structure (60/20/20 + PURGE_DAYS boundary purge). Returns validation-fold returns, OOS alpha (sum), validation days, purge integrity flag, and thin-window flag. Series shorter than `FOLD_TRANSFORM_MIN_TOTAL_DAYS` returns an empty fold with `purge_integrity_ok=False` (WITHHOLD, never fabricate).

Non-finite values are stripped before computation (conservative: gate on finite observations; too-few remaining → WITHHOLD via min-length check).

### `_compute_parameter_stability_score(candidate_params, incumbent_params) -> float`

Panel criterion D2 (parameter move-magnitude penalty). Normalised L1 distance scaled to `[0.0, 1.0]`; 1.0 = identical to incumbent, 0.0 = maximally far. Returns 0.5 (neutral prior) when params are empty or share no keys. Zero-sample-cost — consumes no validation budget.

### `_compute_prior_anchor_score(candidate_params, theory_prior_params) -> float`

Panel criterion D4 (prior-anchor / theory-consistency). Same normalised-L1 formula as stability score; 1.0 = matches theory prior exactly. Returns 0.5 neutral prior on empty or no-shared-keys input.

## Internal Dependencies

- `acceptance_gate` — `AcceptanceVerdict`, `evaluate_acceptance_gate`
- `autotuner` — fold constants (`TRAIN_RATIO`, `VALIDATION_RATIO`, `PURGE_DAYS`, `EMBARGO_DAYS`, `HARVEY_LIU_FDR_Q`, `RETURN_PCT_TO_FRACTION`); `benjamini_hochberg_adjust`, `compute_haircut_pvalue`, `compute_sortino_tstat`
- `database` — `PHASE1_THEORY_GAMMA` (AC-2, `DE-MATH-R0-001`, module-level import — `_BATCH_PBO_GAMMA`'s single source of truth)
- `math_engine` — `compute_pbo` (C5b batch PBO, AC-24); `PBO_REJECT_THRESHOLD` (imported locally inside the per-candidate loop to avoid circular import risk)
- `hashlib` — stdlib, `_stable_seed_from_candidate_id` (AC-D3)

No import of `alpha_bot_execution`, `app`, or any execution module. Off-execution-path; advisory-only.
