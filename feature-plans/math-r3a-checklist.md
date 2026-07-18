# Feature: Math R3-a — Pre-Retune Checklist Prerequisites
Status: ready
Created: 2026-07-18

> Part of the math-remediation program (charter: `feature-plans/math-remediation-program.md`, audit basis DE-MATH-AUDIT-001). R3 is a **gated sequence**: **R3-a (this plan, tests-only)** → R3-b (MA-4 disarm-band fix, live-path) → R3-c (MA-11 MAX_SQUEEZE_FLOOR, live-path) → R3-d (first trustworthy retune, an operator-gated OPERATION). Scope basis: `feature-plans/math-r3-scoping.md` §1. **Base: `origin/main @ 77551f1c` (local main is a STALE pre-R0 husk — fork the worktree off origin/main explicitly).**

## Summary
R3-a delivers the two UNMET items on the pre-retune checklist (DECISIONS.md:6350-6355) that hard-gate the R3-d retune. It is **tests-only and off the live-execution path** — it adds diagnostic/probe test artifacts and (at most) one replay-path constant change; it changes **no** live exit decision, no live stop distance, and no live-execution code. Item (a): a bounded, deterministic-seed **walk-forward objective-variance smoke** proving the Optuna objective is genuinely sensitive to **every** tuned search-space dimension — closing the gap where the two parabolic dims (`PARABOLIC_VELOCITY_THRESHOLD`, `MAX_PARABOLIC_SQUEEZE`) are today proven inert-free only at the wiring level, not at walk-forward. Item (b): a **300-path band-edge stability probe** measuring the arm-decision flip-rate of the replay Monte-Carlo estimator (`_MC_REPLAY_SIMULATION_PATHS=300`) near the arm-band boundary versus higher path counts, emitting a committed **bump-vs-accept** recommendation. Together these satisfy the hard rule "no retune ships live params without demonstrating objective variance on every tuned dim" and quantify replay arm-decision stability before the retune leans on it.

## Acceptance Criteria
- [ ] **AC-1 (dim enumeration is source-derived, not hardcoded):** The variance smoke enumerates the tuned dimensions from the **production Optuna search-space definition in `autotuner.py`** (the authority) and asserts that the swept set equals that production set — so a future dim added to the search space without a variance demo makes this test FAIL, not silently pass. The known expected set from scope — the 6-dim `autotuner.py:157` `OPTUNA_SEARCH_SPACE_KEYS` (`TAKE_PROFIT_MC_PCT`, `VWAP_CROSS_HWM_PCT`, `VWAP_BLEED_MULTIPLIER`, `VWAP_BLEED_TICKS`, `PARABOLIC_VELOCITY_THRESHOLD`, `MAX_PARABOLIC_SQUEEZE`) — is confirmed against source during RED, not assumed. **Corrected (PM binding ruling, 2026-07-18):** `TRIGGER_THRESHOLD_PCT` is NOT part of the tuned search space — it is a frozen, non-tuned default read via `p.get("TRIGGER_THRESHOLD_PCT", 15.0)` (`autotuner.py:1173`), never a `trial.suggest_*` call — and must not appear in the swept set; the plan's original expected-set line wrongly included it and omitted the 3 VWAP dims (`VWAP_CROSS_HWM_PCT`, `VWAP_BLEED_MULTIPLIER`, `VWAP_BLEED_TICKS`), now fixed to match `OPTUNA_SEARCH_SPACE_KEYS` exactly.
- [ ] **AC-2 (walk-forward variance per dim):** For **each** tuned dim, sweeping ≥2 distinct in-range values (all other dims held at a fixed baseline) yields a walk-forward Optuna objective that is **not identical** across the swept values — i.e. strictly non-zero variance, asserted per-dim. A dim that produces zero variance FAILS with a message naming the dead dim (it is either inert-wired or unexercised by the fixture — both are defects this test exists to catch).
- [ ] **AC-3 (fixture provably exercises each dim's codepath):** The smoke asserts, via instrumentation/counters on the replay, that the fixture history actually **fires** each dim's decision codepath at least once (the parabolic squeeze arms, the take-profit MC arms, the trigger threshold is reached, etc.). A dim whose codepath never fires cannot yield honest variance and must FAIL loudly rather than pass on a vacuous zero. (memory: E2E-exam scoring factors must VARY — "N results" hid a dead factor.)
- [ ] **AC-4 (determinism):** The smoke is fully deterministic — every stochastic source seeded (numpy/global RNG, Optuna sampler seed, MC path seed). Two runs on the same SHA produce byte-identical objective values per swept point. No wall-clock, no network, no live API.
- [ ] **AC-5 (bounded / cheap):** The smoke runs under a strict trial/day/path budget (a SMOKE, not a 500-trial production walk-forward) and completes fast under targeted `-n0`. It never invokes the full production trial floor.
- [ ] **AC-6 (band-edge probe measures flip-rate):** The stability probe constructs a scenario whose true underperformance probability sits at/near the arm-band edge (the boundary of `acc_TAKE_PROFIT_MC_PCT <= prob < acc_TRIGGER_THRESHOLD_PCT`), then over a seeded sweep of independent MC runs measures the **arm-decision flip-rate** (fraction of runs whose 300-path estimate lands on the opposite side of a band boundary from the higher-path reference) at 300 paths versus reference path counts (≥1000; the exact ladder chosen and justified during RED).
- [ ] **AC-7 (recommendation artifact):** The probe emits a committed, human-readable **bump-vs-accept recommendation** artifact (markdown/JSON under `docs/generated/` or `feature-plans/`) recording: the measured flip-rate at 300 vs each reference count, the decision threshold used, and the recommendation. The decision threshold is a documented `[PM-ASSUMED]` default (see Decisions) — the probe supplies the evidence.
- [ ] **AC-8 (bump is replay-path only, if taken):** IF the probe recommends a bump, the ONLY code change permitted under R3-a is the single-constant edit to `_MC_REPLAY_SIMULATION_PATHS` (a **replay/analysis-path** constant — NOT the live-execution MC). No live-execution path count changes. IF the recommendation is accept, no constant change is made.
- [ ] **AC-9 (checklist status updated):** DECISIONS.md checklist items (a) and (b) are flipped from UNMET to MET with a pointer to the new artifacts/tests, so R3-d's gate reads true from the durable record. **Gating clarification (PM binding ruling, 2026-07-18):** this flip lands ONLY after r3a-review's APPROVE verdict on the tests (not merely after they are green) — a flip that outruns a clean non-vacuous verdict is itself a BLOCK.
- [ ] **AC-10 (no live-path leakage):** A scope-guard assertion confirms R3-a touches no live-execution-path file behavior — no change to `alpha_bot_execution.py` decision logic, no change to `math_engine.py` live-stop math, no change to any live exit decision or stop distance. (The only sanctioned code change is AC-8's replay constant, if taken.)

## Architecture
Backend quant / test-infra cycle. **No UI, no data-layer migration, no new route, no new production codepath on the live engine.** Design-System Mapping is **N/A** (no UI surface).

- **New test files (RED authors: quant-test-writer, adversarial):**
  - `tests/autotuner/test_r3a_walkforward_variance_all_dims.py` — AC-1..AC-5. Extends the pattern of the existing partial `tests/autotuner/test_ac7_inert_dims_objective_variance_smoke.py` (which proves `TAKE_PROFIT_MC_PCT` at walk-forward + the parabolic dims only at wiring level) up to full walk-forward coverage of every tuned dim. Uses deterministic synthetic replay history (fixture-sourced; the fixture MUST arm/fire each dim's codepath — AC-3).
  - `tests/autotuner/test_r3a_band_edge_stability_probe.py` — AC-6..AC-8. Drives the MC replay estimator at 300 vs reference path counts near the band edge over a seeded run-sweep; asserts the probe computes a flip-rate and emits the artifact deterministically.
- **Probe/analysis module (if a runnable probe is needed beyond the test):** a small, off-execution-path helper (e.g. `tools/` or `advisors/`-adjacent, D-1 never-raises, no live I/O) that the test imports and that also produces the committed recommendation artifact. Kept out of `alpha_bot_execution.py` / `math_engine.py` live paths. The team decides module placement at plan-approval; it must be import-clean and seed-driven.
- **Fixtures:** synthetic price/return history under `tests/fixtures/` designed so every tuned dim's decision branch is reachable and fires (AC-3). Provenance: schema-derived synthetic with a runtime validator or captured-from-producer — never parser+fixture co-design (Gate-1 fixture-provenance rule). Since these are engine-internal replay inputs (not an external API contract), schema-derived synthetic with an assertion that each codepath fired is the accepted provenance here.
- **Authoritative sources the RED tests read:** the Optuna search-space definition in `autotuner.py` (`:157` `OPTUNA_SEARCH_SPACE_KEYS`, the authority for AC-1's expected set; `~:308-309` squeeze range, plus the full space around `:253` `OPTUNA_N_TRIALS_PRODUCTION` / the space builder); `TRIGGER_THRESHOLD_PCT` is frozen/non-tuned (`:1173`, `p.get(..., 15.0)`), never part of the swept set; `_MC_REPLAY_SIMULATION_PATHS=300`; the arm band `alpha_bot_execution.py:1373-1381`. DB sentinel: tests set `DB_PATH` via `tests/conftest.py`.
- **No new dependencies.**

## Design-System Mapping
N/A — backend quant / test-infra cycle, no UI components.

## Edge Cases
- **A dim that legitimately can't vary on a given fixture** — the fixture is the defect, not the dim. AC-3 forces the fixture to fire every codepath; if it can't, the test FAILS and the fixture is fixed (never relax the variance assertion to make it pass).
- **Near-degenerate variance** (objective differs only in float noise) — the assertion must require variance meaningfully above float-epsilon / seed jitter, not merely `!=`. Distinguish "genuinely sensitive" from "numerically noisy."
- **Band-edge probe meta-stability** — the flip-rate estimate itself needs enough seeded runs to be stable; too few runs makes the recommendation noise. The run count is chosen so the flip-rate estimate's own CI is tight enough to separate bump from accept.
- **Reference path count "ground truth" is itself an estimate** — flip-rate is measured against a higher-but-finite reference, not the unknowable true probability. Frame the metric honestly as "300 vs reference-N disagreement," not "300 vs truth."
- **Exactly-on-the-boundary scenarios** — define the band-edge target as a small offset inside/outside the boundary (not exactly on it) so "flip" is well-defined; document the offset.
- **Determinism across OS/BLAS** — seed-pinning must survive the Windows/`-n0` runner; avoid any thread-nondeterministic reduction in the objective path (no nested joblib fan-out — memory: PC-crash rootcause xdist×joblib).
- **Budget blowout** — if the bounded walk-forward smoke is too slow, shrink the day window / trial count before relaxing coverage; coverage of all dims is non-negotiable, speed is the tunable.

## Security Considerations
Minimal attack surface (tests-only, no new external input, no route, no credential handling). Applicable safety rules for this quant cycle:
- **No live API / real-money reads in tests** — the walk-forward smoke and the probe use synthetic/fixture history ONLY; no live Alpaca/Composer/Atlas/Mongo fetch (memory: atlas-mongo-real-money structural guard; feedback-credential-less-pass-not-sufficient — a source-level seam-mock audit, not just a credential-less run, confirms no live fetch is reachable).
- **DB sentinel** — tests must resolve `DB_PATH` to a temp path via `conftest.py`; never touch `alphabot_state.db` (the `_db_file()` pytest sentinel enforces this).
- **No secret material** — no keys/tokens read or logged by the probe or its artifact.
- **Artifact hygiene** — the committed recommendation artifact contains only aggregate flip-rate stats, never raw credentials or live account data.

## Testing Strategy
These deliverables **are** tests/probes, so "testing strategy" = proving the tests themselves are non-vacuous and the cycle doesn't regress the tree.
- **Unit / smoke tests:** the two new files above. RED first (they fail on the current tree because the parabolic dims lack walk-forward variance proof and no band-edge probe/artifact exists), GREEN by adding the probe helper + fixtures.
- **Non-vacuity proof (critical):** each variance assertion must be shown to FAIL if a dim were made inert (the test-writer demonstrates the RED by temporarily stubbing a dim to a constant and confirming the test catches it) — otherwise the test could pass trivially. AC-3's codepath-fired counters are the guard.
- **Determinism check:** run each new test twice on the same SHA; objective values / flip-rate must be identical.
- **PM full-tree battery (MANDATORY, targeted `-n0`, temp DB_PATH):** because R3-a lives in `tests/autotuner/` and reads engine/`math_engine` internals, the PM pre-merge battery MUST include `tests/autotuner/` **and** the hot-file guard suites `tests/error_handling/`, `tests/execution/`, `tests/math_engine/` (F7 CI-bounce lesson: a lineno-keyed guard in `tests/error_handling/test_exception_specificity.py` shifted under a diff). Never a feature-scoped battery for engine-adjacent changes.
- **CI:** the repo `-n2` GitHub Actions run (ruff + full pytest) must be READ-green (`gh pr checks`) — never trust `gh run watch --exit-status`.
- **NO full/uncapped/-n>4 local pytest** (238GB host-crash lesson).

## Decisions
| Decision | Rationale |
|----------|-----------|
| R3-a ships as its own tests-only PR, separate from R3-b/c/d | Each live-path change (MA-4, MA-11) gets its own review + PM live E2E; the checklist prerequisites are the low-risk on-ramp and gate the retune. (r3-scout, settled in handoff §6) |
| Tuned-dim set is enumerated from `autotuner.py` source, not hardcoded in the test | A hardcoded list silently rots when the search space changes; source-derivation makes an un-demoed new dim fail the gate. |
| `[PM-ASSUMED]` bump threshold: arm-decision flip-rate ≥ ~5% at 300 paths near the band edge → recommend **bump**; below → **accept 300** | A ~5% disagreement with the higher-path reference near the boundary is a defensible "materially unstable" line for a live-money arm decision; the probe supplies the actual measured number and the operator/PM can move the line with evidence. Documented as assumed, not asserted as truth. |
| A "bump" outcome edits only `_MC_REPLAY_SIMULATION_PATHS` (replay/analysis constant) | That constant is off the live-execution path (replay fidelity, R1 territory), so bumping it stays inside R3-a's tests-only / no-live-behavior-change boundary. |
| No deploy required for R3-a | Tests-only (or replay-constant only); the live daemon's exit decisions are unchanged. Merge-to-origin is the definition of done for this PR. |
| AC-1's expected tuned-dim set corrected to the real 6-dim `OPTUNA_SEARCH_SPACE_KEYS` (drop `TRIGGER_THRESHOLD_PCT`, add the 3 VWAP dims) | PM binding ruling, 2026-07-18: `TRIGGER_THRESHOLD_PCT` is a frozen non-tuned default (`autotuner.py:1173`), never a `trial.suggest_*` call; the plan's original scope line was wrong and would have mis-anchored the RED test's source-of-truth assertion. |
| The UNMET→MET checklist flip (AC-9) lands only after r3a-review's APPROVE verdict, never merely on green tests | PM binding ruling, 2026-07-18: a checklist flip that outruns a clean non-vacuous verdict is a theater gate — the one thing that would let the R3-d live-money retune proceed on a false MET. |

## Scope Boundaries
- **IN:**
  - (a) Deterministic walk-forward objective-variance smoke covering **every** tuned Optuna dim, with source-derived dim enumeration + fixture-fired-codepath proof.
  - (b) 300-path band-edge arm-decision stability probe + committed bump-vs-accept recommendation artifact.
  - IF (b) recommends bump: the single-constant `_MC_REPLAY_SIMULATION_PATHS` edit (replay-path only).
  - Flip DECISIONS.md checklist items (a)+(b) UNMET→MET with pointers.
  - New fixtures/probe helper required to make the above deterministic and non-vacuous.
- **OUT (explicitly deferred to later R3 cycles or excluded):**
  - MA-4 disarm-band bug fix (`alpha_bot_execution.py:1394-1402` + TP-disarm `:1561`) → **R3-b**.
  - MA-11 `MAX_SQUEEZE_FLOOR` wire-or-remove (`math_engine.py:379-381`, `alpha_bot_execution.py:1234`) → **R3-c**.
  - The retune itself (walk-forward → live param persist via `save_symphony_strategy`) → **R3-d** (operation, operator-gated).
  - ANY change to a live exit decision, live stop distance, or live-execution-path code (beyond the sanctioned replay constant).
  - Reconciling the stale local `main` husk (optional, separate hygiene).
