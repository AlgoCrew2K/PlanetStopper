# scripts/mc_band_edge_stability_probe

> R3-a pre-retune checklist deliverable (b): quantifies how stable the replay Monte-Carlo estimator's arm decision is at its current path count (300) near an arm-band boundary, versus higher reference path counts, and emits a committed bump-vs-accept recommendation.

**Source:** `scripts/mc_band_edge_stability_probe.py`
**Last updated:** 2026-07-18

## Overview

The live engine arms a protective trailing stop when `acc_TAKE_PROFIT_MC_PCT <= prob_underperforming < acc_TRIGGER_THRESHOLD_PCT` (`alpha_bot_execution.py:1373-1381`). `math_engine.run_monte_carlo` estimates `prob_underperforming` as `(paths_at_or_above / paths) * 100` — a Binomial/paths estimate whose sampling error shrinks with path count. Near a band boundary, a 300-path replay estimate (`synthetic_history._MC_REPLAY_SIMULATION_PATHS`) can land on the opposite side of the boundary from a higher-path reference, flipping the arm decision. This module measures that disagreement rate (the "flip-rate"), honestly framed as "300 vs reference-N disagreement" — never "300 vs unknowable truth" — even for its own synthetic scenario, where the true probability is exactly known by construction (a fixed, known resampling pool via `neighbor_k == pool size`); the flip metric itself is still measured against the noisy reference estimator, matching how a real historical-data scenario would be assessed.

**Off-execution-path (AC-8/AC-10 scope guard):** this module NEVER imports `alpha_bot_execution` — the arm-band boundary value (`_LOWER_ARM_BOUNDARY_PCT = 5.0`, mirroring `alpha_bot_execution.TAKE_PROFIT_MC_PCT`'s default) and the production-parity reference (`_PRODUCTION_PARITY_PATHS = 5000`, mirroring `alpha_bot_execution.SIMULATION_PATHS`) are mirrored constants, not imports, keeping this probe off the live-execution import graph. The only code change R3-a may make elsewhere is the single replay/analysis constant `synthetic_history._MC_REPLAY_SIMULATION_PATHS`, and only if the probe recommends a bump AND an evidence-based target search certifies a specific value — the live engine's `alpha_bot_execution.SIMULATION_PATHS` (5000) is never touched, confirmed by a dedicated scope-guard test.

D-1 / off-execution-path / no DB / no network / no blocking I/O beyond the two committed artifact files this module writes on request.

## API Reference

### `measure_flip_rate(*, target_true_prob_pct, boundary_pct, n_seeds, reference_counts, base_seed) -> FlipRateResult`

AC-6: measures the replay's focal path count (300, sourced live from `synthetic_history._MC_REPLAY_SIMULATION_PATHS`, never duplicated as a second magic number) arm-decision flip-rate against each of `reference_counts`, for one fixed synthetic scenario built via `_build_band_edge_fixture`. Deterministic — every draw is independently seeded via `math_engine.derive_cycle_mc_seed`, keyed by `(base_seed, slot, paths, seed_index)`; no wall clock, no global RNG.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `target_true_prob_pct` | `float` | The synthetic scenario's target true underperformance probability |
| `boundary_pct` | `float` | The arm-band boundary to measure flips against |
| `n_seeds` | `int` | Number of independent seeded draws |
| `reference_counts` | `tuple[int, ...]` | Higher-fidelity path counts to compare the 300-path draw against |
| `base_seed` | `int` | Fixed seed root for full determinism |

**Returns:** `FlipRateResult`.

---

### `recommend(flip_rate: float, *, threshold: float = RECOMMENDATION_THRESHOLD) -> str`

AC-7: pure threshold decision — `"bump"` if `flip_rate >= threshold` else `"accept"`. No hardcoded outcome; a test drives both branches.

---

### `_select_bump_target(*, target_true_prob_pct, boundary_pct, n_seeds, base_seed, threshold) -> dict`

Evidence-based bump-target search (PM ruling on the R3-a (b) plan). Searches `_BUMP_CANDIDATE_LADDER` (400 → 5000) ascending and certifies the SMALLEST candidate whose own flip-rate vs `_PRODUCTION_PARITY_PATHS` (5000) is below `threshold`. **Never returns an unmeasured value.** If no candidate up to and including production parity clears the bar, returns `stable=False` — an honest degenerate finding, not a forced number (comparing 5000 against itself is still two independently-drawn estimates, so even parity self-comparison is not guaranteed stable when the true probability sits extremely close to the boundary).

**Returns:** `dict` — `stable: bool`, `target_path_count: int | None`, `target_flip_rate_vs_5000: float | None`, `candidates_tried: dict[int, float]`.

---

### `run_probe(*, md_path=ARTIFACT_MD_PATH, json_path=ARTIFACT_JSON_PATH) -> dict`

Runs the headline near-edge scenario (0.3pp inside the arm-band boundary — `_HEADLINE_NEAR_EDGE_OFFSET_PCT`), decides bump/accept via `recommend`, runs `_select_bump_target` if the verdict is `"bump"`, and writes both the human-readable Markdown artifact and the machine-readable JSON sidecar. Returns the same payload dict persisted to `json_path` — the returned summary and the committed artifact always agree on the verdict.

**CLI invocation:** `python -m scripts.mc_band_edge_stability_probe` (the module's `if __name__ == "__main__":` block calls `run_probe()` with its default artifact paths).

**Committed artifact:** `docs/generated/mc-band-edge-stability.md` + `.json` sidecar (see below).

## Types

### `FlipRateResult` (frozen dataclass)

| Field | Type | Description |
|-------|------|-------------|
| `flip_rate_by_reference` | `dict[int, float]` | reference path count → fraction of `n_seeds` independent 300-path draws whose side of `boundary_pct` disagrees with an independently-drawn reference-N estimate of the same scenario |
| `n300_paths` | `int` | the focal (under-test) path count — `synthetic_history._MC_REPLAY_SIMULATION_PATHS` at call time |
| `p_true_estimate` | `float` | the exact closed-form true probability of the synthetic scenario (informational only, not used to compute the flip-rate) |

## Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `_LOWER_ARM_BOUNDARY_PCT` | 5.0 | Mirrors `alpha_bot_execution.TAKE_PROFIT_MC_PCT`'s default (mirrored, not imported — scope guard) |
| `_PRODUCTION_PARITY_PATHS` | 5000 | Mirrors `alpha_bot_execution.SIMULATION_PATHS`'s default — the live-fidelity reference the headline recommendation is driven by |
| `_PROBE_FOCAL_PATH_COUNT` | `synthetic_history._MC_REPLAY_SIMULATION_PATHS` | Sourced live from `synthetic_history`, never duplicated |
| `RECOMMENDATION_THRESHOLD` | 0.05 | `[PM-ASSUMED]` bump line (`feature-plans/math-r3a-checklist.md` Decisions table) — a ≥5% arm-decision disagreement near the edge is a defensible "materially unstable" line for a live-money arm decision |
| `_BUMP_CANDIDATE_LADDER` | `(400, 500, 600, 750, 1000, 1250, 1500, 2000, 2500, 3000, 4000, 5000)` | Ascending search ladder for the evidence-based bump-target search |
| `DEFAULT_REFERENCE_COUNTS` | `(1000, 5000, 20000)` | Reference path counts the committed artifact's headline table reports against |
| `SANCTIONED_KNOB` | `"synthetic_history._MC_REPLAY_SIMULATION_PATHS"` | The only R3-a-sanctioned bump target (replay/analysis-path, never the live-execution MC) |
| `_HEADLINE_NEAR_EDGE_OFFSET_PCT` | 0.3 | The offset (in percentage points) inside the boundary the committed headline scenario uses |
| `_ARTIFACT_N_SEEDS` | 300 | Seeded draws for the committed artifact run |
| `_POOL_SIZE` | 1000 | Resolution of the synthetic scenario's true-probability placement (100/`_POOL_SIZE` = 0.1pp) |

## Committed Artifact: `docs/generated/mc-band-edge-stability.{md,json}`

Persisted by `run_probe()` at its default paths. Records, for the single committed headline scenario (0.3pp offset inside the 5.0% lower arm boundary): the flip-rate at 300 paths vs each of the 3 reference counts, the decision threshold, the recommendation, and — on the bump branch — the evidence-based target search's candidates-tried table and stability verdict. See `docs/generated/synthetic_history.md`'s `_MC_REPLAY_SIMULATION_PATHS` constant row for the cross-linked finding summary and `DE-MATH-R3A-001` in `DECISIONS.md` for the full pre-retune checklist record.

**Scope note:** the committed artifact covers only this one headline scenario. `measure_flip_rate`/`_select_bump_target` are general-purpose and were also exercised at other offsets during the RED/review cycle (see the DE entry for the full characterization) — those runs are reproducible via the same functions but are not themselves persisted to a committed file.

## Internal Dependencies

- `math_engine` — `run_monte_carlo` (the real estimator this probe drives, never a reimplemented Binomial), `derive_cycle_mc_seed`
- `synthetic_history` — `_MC_REPLAY_SIMULATION_PATHS` (the focal path count, read live, never duplicated)
- stdlib + `numpy` only beyond the above: `dataclasses`, `json`, `pathlib`

## Consumers

- `tests/autotuner/test_r3a_band_edge_stability_probe.py` — the sole test consumer. Drives `measure_flip_rate` (validity, real-estimator non-vacuity via a `run_monte_carlo` spy, near-vs-far boundary-instability non-vacuity crux, determinism), the reference-ladder shape, the bump-target search's non-vacuity (reducible-vs-irreducible scenario contrast, added during r3a-test's sufficiency review — `dbd06f0e`), the pure `recommend` decision function, the committed artifact's required fields (via a hermetic `tmp_path` run — the real committed artifact is produced by running this module standalone), and the AC-8/AC-10 scope guards.
- `python -m scripts.mc_band_edge_stability_probe` — the CLI invocation that (re)generates the committed artifact.
