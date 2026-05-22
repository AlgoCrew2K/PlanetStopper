# Feature: simulate_forward_paths — Regime-Conditioned Stationary Block Bootstrap (NET-NEW)

**Phase / Lane:** Phase 2 — Finalist B, **evidence-gated**. Scaffold now per the user's directive; **may never unlock**. The four preconditions (council synthesis §5.1) gate execution.
**Owner agent-type:** `risk-engine-specialist` (implementer) + `quant-test-writer` (RED) + `quant-risk-researcher` (Politis-White block-length selector + regime calibration) + `optuna-specialist` (autotuner-side parameter handling for gamma — NEVER for block length or generator family) + `quant-code-reviewer` (review). Pent for this surface.

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §2.3 (the live CVaR *trigger* is un-validatable — `simulate_forward_paths` exists to support a **CO-SIGNAL**, not a trigger), §5.1 (the four preconditions — `simulate_forward_paths` SHIPS only if a/b/c/d all pass), §5.3 (Phase 2 design: regime-conditioned stationary block bootstrap; `simulate_forward_paths` as a **net-new** function; `run_monte_carlo` **frozen** until last symphony cuts over; **block length frozen by Politis-White automatic selector — independent of strategy P&L**; preserves volatility clustering; accepts historical-worst-case ceiling), §5.6 R-3 (a pre-simulated path bank is structurally blind to intraday tail regime — a documented residual), §5.6 R-6 (a hand-set conservatism adjustment would re-introduce ad-hoc heuristic — forbidden), §5.7 (B-2/B-3 named, owned, blocking Phase-2 entry gates — "if the latency arithmetic does not clear OR the regime buckets are too thin, **Phase 2 does not proceed**").
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.0 (the validation-wall ~1,000 tail-obs requirement holds; `simulate_forward_paths` does NOT escape it — it supports a co-signal validated by "does CVaR agreement improve an exit the engine already supports").
- `docs/handoff/council-converged-migration-plan.md` §3.2 row 017 (`017_path_generator.sql` — `path_generator_calibrations` + `path_bank_manifest`; the path bank is a **FILE CACHE** `.npy`, NOT a state-DB blob; `tier1_seed` is load-bearing for Gate 1), §6 hazard **H7** (`run_monte_carlo` blast radius — signature **frozen** through both phases; `simulate_forward_paths` is **net-new**, not a mutation).
- `docs/handoff/council-attack-rubric.md` Family **B** ★★ (B-1 ★ no blocking I/O per minute; B-2 ★ path-count vs CVaR-stability vs latency budget; B-3 cost across symphony set; GARCH MLE is "the binding concern against the no-blocking-I/O constraint"), Family **C** ★★ (C-1 ★ hysteresis on the trigger — sibling plan; **C-2 ★ tail-bias direction acknowledged + GBM/IID-bootstrap KILLS** — `simulate_forward_paths` MUST be block bootstrap or FHS, NEVER GBM/IID; C-3 ★ volatility clustering preserved for multi-bar horizons; C-4 historical-worst ceiling acknowledged; C-5 kNN regime matcher feature count low + de-correlated), Family **D** ★ (D-1 ★ + D-2 ★ + **D-3 deterministic stochastic output** — `tier1_seed`-derived RNG, never numpy global), Family **F** (F-2 ★ replay parity; F-4 ★ insufficient-data sentinel mirror), Family **G** ★ (G-1 ★ all `run_monte_carlo` consumers enumerated — 7+ sites; G-2 ★ **the multi-day path simulator MUST be named net-new, NEVER a "parameter change"**), Family **M** ★ (M-2 ★ persisted derived artifacts replay-deterministic from a recorded seed — `tier1_seed` is the recorded seed; sibling plan).
- Code anchors: `math_engine.py:705-833` (`run_monte_carlo` — frozen; `simulate_forward_paths` lives **alongside**, not in place of); `math_engine.py:828-829` (the `rng.choice(nearest_day_returns, size=simulation_paths)` line that defines what `run_monte_carlo` IS — a single-day i.i.d. resampler; `simulate_forward_paths` is structurally different); `math_engine.py:695-702` (`derive_cycle_mc_seed` — the determinism precedent the new `tier1_seed` follows); `math_engine.py:30-54` (NaN/Inf rejection policy); `synthetic_history.py` (the file-cache precedent that `path_bank_manifest` mirrors); the project memory `project_mc_sentinel_consumer_blast_radius` (the 7+ consumers G-1 ★ enumerates).

## Why (problem statement)

`run_monte_carlo` is a single-day i.i.d. resampler — each "path" is one `rng.choice` draw from a kNN pool; there is no time axis (council synthesis §2.3; phase0-generator §0). A **multi-day CVaR** — the only kind a forward-looking tail budget can be computed against — is **genuinely net-new construction**, NOT a parameter tweak (G-2 ★ binding).

The Phase-2 endpoint is a CVaR **co-signal** (§5.2), not a trigger — a value that can narrow, veto, or confirm an exit another layer already supports, never solely fires one. Validating "does CVaR agreement improve an exit the engine was already going to make" needs far less data than calibrating a literal 5% ES estimator (§5.2; §B.0 of evaluation: "validate a *detector* whose ROC tests `does the threshold-crossing predict worse forward outcomes`"). The co-signal's design rationale rests on a forward-looking multi-day tail simulation; **`simulate_forward_paths` is the function that produces it**.

The four Phase-2 preconditions (§5.1) gate whether this plan ever ships at all:

1. **(a) M2 evidence:** M2's diagnostic must not show gross uninformativeness AND a separately-powered discriminating test must become constructible. Per H-4, M2 can only **kill** Phase 2 — never advance it.
2. **(b) Gate-zero tail-data audit:** AlphaBot's history must yield enough genuine sub-5% tail observations per regime cluster to power *some* discriminating validation.
3. **(c) Latency + bucket arithmetic (B-2/B-3 deferred gates):** measured prototype proves the Tier-1 pre-open batch finishes with margin AND regime buckets are populous enough to simulate from. If either fails, Phase 2 does not proceed.
4. **(d) A powered validation design exists OR the trigger ships diagnostic-grade-permanent.**

Per §2.3, **precondition (d) may be structurally unsatisfiable** — the ~1,000-tail-observation requirement is a decade-plus of data away. The synthesis states plainly: *Phase 2 may never unlock, and stopping permanently at Finalist A is a full success, not a project failure.*

`simulate_forward_paths` is therefore scaffolded — not built — until the gates pass. This plan is the design that an authorized cycle would execute.

## Deliverables

### Code (designed; built only if Phase 2 unlocks)

#### Core function

- **`math_engine.py`** (or a new sibling module `forward_paths.py` — the module split decision is in scope of this plan; both options are NN1-equivalent) — new function:
  ```python
  def simulate_forward_paths(
      regime_returns: dict[str, np.ndarray],   # regime_key -> historical return series
      regime_key: str,                          # the current regime bucket
      horizon_steps: int,                       # number of forward steps (multi-bar)
      n_paths: int,                             # path count (B-2 ★ stable-CVaR sized)
      block_length: int,                        # Politis-White-selected, NN1-frozen
      tier1_seed: int,                          # SHA-256(symphony_id ‖ trading_day ‖ spec_bundle_hash); M-2 ★
  ) -> ForwardPathBundle:
      ...
  ```
- **Stationary block bootstrap (Politis-Romano 1994):** block length is **random** per draw with mean `block_length`, drawn from a geometric distribution — gives a stationary resampled series. Implemented inline (small, pure, testable; the project memory says no third-party dep without a researcher dispatch).
- **Regime conditioning:** before block-resampling, the historical return series is filtered to the current regime bucket (`regime_key`). The bucket is derived from a small (C-5 ★ — low feature count, de-correlated) regime classifier on macro/volatility features — NEVER on the 6-layer heuristic stack.
- **Volatility clustering preserved (C-3 ★):** block bootstrap structurally preserves autocorrelation of squared returns when block length is non-trivial. The block-length selector is the load-bearing piece — Politis-White automatic selector from the autocorrelation structure of squared returns. **NN1-frozen, NOT Optuna-searched.**
- **Historical-worst ceiling acknowledged (C-4):** block bootstrap shares this ceiling with kNN MC and FHS-on-empirical-residuals (synthesis §5.3 — accepts the ceiling). The function does NOT attempt EVT extrapolation (EVT adds the most degrees of freedom of any candidate and is rejected at design time).
- **Forbidden alternatives:** the function **MUST NOT** be GBM or IID bootstrap (C-2 ★ kill). The implementation is structurally not either: GBM lacks blocks; IID bootstrap is block-length-1.

#### Frozen typed return

- `ForwardPathBundle` — frozen dataclass / NamedTuple:
  ```python
  @dataclass(frozen=True)
  class ForwardPathBundle:
      paths: np.ndarray | None   # shape (n_paths, horizon_steps); None when insufficient
      tier1_seed: int            # the seed that produced this bundle (audit)
      regime_key: str            # the regime bucket used
      block_length: int          # the block length used (NN1-frozen)
      tail_obs_count: int        # count of genuine distinct sub-5% historical observations in the regime bucket (canonical name per synthesis §2.6 — mirrors CVaRAssessment.tail_obs_count semantics one layer up)
      insufficient_reason: str | None
  ```
- `paths = None` is the **out-of-band insufficient sentinel** — mirrors `MC_INSUFFICIENT_HISTORY_SENTINEL = None` (F-4 ★). The downstream consumer must abstain fail-safe when `paths is None`.
- **Field-name reconciliation (binding):** `tail_obs_count` is canonical per synthesis §2.6 verbatim and critic's `mc-sentinel-blast-radius` plan's four-field `CVaRAssessment` contract. The `ForwardPathBundle.tail_obs_count` field is semantically the same kind of quantity (distinct genuine tail observations in the underlying historical pool — never the path count); using the same name across the two typed objects keeps the reviewer surface coherent.

#### Determinism

- The function uses `np.random.default_rng(tier1_seed)` — **never** the numpy global RNG (D-3 ★).
- The `tier1_seed` is computed by sibling plan `tier1-seed-determinism/plan.md` and passed in. The function does not derive its own seed.
- Bit-identical output guaranteed for the same `(tier1_seed, regime_returns, regime_key, horizon_steps, n_paths, block_length)` tuple (F-2 ★).

#### Persistence — file cache + manifest

Per migration `017_path_generator.sql` (deferred Phase-2 schema):

- **`path_bank_manifest`** state-DB table — `~200-byte` metadata row: `regime_fingerprint`, `bank_file_path`, `bank_sha256`, `tier1_seed`, `built_at_utc`, `superseded_at_utc`. Soft FK to `path_generator_calibrations.id`.
- **The path bank itself is a FILE CACHE** — `.npy` artifact at `bank_file_path`. NOT a state-DB blob (~40 MB/day of regenerable floats would bloat the WAL and every backup; H7 binding).
- The bank is content-hashed (`bank_sha256`) — replay verifies the file matches the recorded hash; a mismatch fails Gate 1 (M-2 ★).
- Tier-1 (out-of-band pre-open batch) writes the bank; Tier-2 (per-cycle in-band) reads the manifest + the `.npy` file. **No per-cycle disk write.**

#### Execution-path split (B-1 ★, B-2 ★)

- **Tier 1 (Pre-open batch, out-of-band):** Politis-White block-length selection + regime bucketing + path-bank pre-simulation, ALL symphonies. Runs **before** the first `:00` cycle. **Must be benchmarked by a prototype** (precondition c) — if it does not finish with margin before the first cycle, Phase 2 stops (synthesis §5.7).
- **Tier 2 (Per-cycle, in-band, < per-cycle budget):** loads the path bank from `.npy`, applies the regime key + the CVaR estimator (sibling plan), produces the `CVaRAssessment` co-signal. **No GARCH MLE, no path simulation, no fetch.** Light array reduction only (B-1 ★).
- **R-3 residual (synthesis §5.6 — documented):** a pre-simulated path bank is structurally blind to an intraday-developing tail regime. The fail-safe abstention (F-4 ★) catches the obvious case (bank manifest stale → abstain); the residual is documented and accepted as the cost of running non-blocking per-cycle.

### Tests (RED before GREEN)

| Test | What must exist before GREEN |
|---|---|
| `simulate_forward_paths` golden fixture | A captured-from-producer historical return series; a known `tier1_seed`; a known regime key; the expected paths array (computed by an independent reference implementation, NOT the function under test — D-2 ★). |
| Determinism (F-2 ★) | Same seed → bit-identical paths array. Two consecutive runs with same args. |
| Volatility-clustering preservation (C-3 ★) | A return series with measurable autocorrelation of squared returns; the resampled paths preserve the autocorrelation within tolerance. The test FAILS for block_length=1 (IID degenerate). |
| Block bootstrap structure | A test asserts the function uses random block lengths (Politis-Romano) — distribution of consecutive identical historical indices follows the expected geometric distribution. |
| Historical-worst ceiling acknowledged (C-4) | The test asserts no path value exceeds the historical worst-case (block bootstrap structural property); a comment explicitly cites C-4 acceptance. |
| GBM / IID rejection (C-2 ★ kill) | A regression test asserts the function is NOT GBM (no closed-form Brownian draws) AND NOT IID bootstrap (block_length > 1 in the spec bundle). |
| Insufficient-data sentinel (F-4 ★) | Empty / too-small regime bucket → `ForwardPathBundle(paths=None, ...)`. Downstream abstains fail-safe. |
| NaN/Inf closure (A-2 ★) | `regime_returns` containing NaN/Inf → `ValueError` at entry. |
| `tier1_seed` audit (M-2 ★) | The returned bundle's `tier1_seed` field equals the input; the recorded bank's `bank_sha256` matches the file. |
| `run_monte_carlo` blast-radius preservation (G-1 ★) | The 7+ consumers in `project_mc_sentinel_consumer_blast_radius` are NOT touched in the M3 PR; `run_monte_carlo`'s signature is byte-identical to `main`. |
| G-2 net-new naming check | A test asserts the new function is named `simulate_forward_paths` (or a similar net-new name) AND `run_monte_carlo` is unchanged. The plan does NOT extend `run_monte_carlo`. |
| C-5 ★ regime feature count | A test asserts the regime classifier uses ≤ a small named cap of features (e.g. 3-5 macro/vol features) and the features are de-correlated by construction (NOT the 6-layer heuristic stack). |
| Tier 1 vs Tier 2 separation (B-1 ★) | A test asserts the per-cycle code path does NOT call `simulate_forward_paths` — only reads the file-cache manifest. |
| Path-count vs latency budget (B-2 ★) | A test runs the Tier 1 batch on a representative load and asserts wall-clock < the pre-open budget margin. |
| Hash mismatch fails Gate 1 (M-2 ★) | A test corrupts the `.npy` file; the Tier 2 read detects the mismatch and abstains fail-safe AND fails the Gate-1 parity assertion. |

**Fixture provenance (D-2 ★):** the forward-paths golden fixture is computed by an independent reference implementation (the council research note's calibration script OR a numpy-only re-implementation written by `quant-risk-researcher`). The fixture is **never** the same code under test's own output (D-2 ★ load-bearing kill).

### Documentation

- A new `docs/research/phase2-forward-paths-derivation-<date>.md` research note documenting the Politis-White block-length selector, the regime classifier features, the pre-open batch timing prototype results, and the Tier 1 / Tier 2 split.
- `path_generator_calibrations` and `path_bank_manifest` schema documentation.
- The R-3 residual disclosed in the M3 PR commit message verbatim: *"`simulate_forward_paths` is built on a pre-simulated path bank; per synthesis §5.6 R-3, the bank is structurally blind to an intraday-developing tail regime; fail-safe abstention catches the bank-stale case; the residual is the cost of non-blocking per-cycle execution."*

## Dependencies

- **Blocks:** the CVaR co-signal hysteresis-trigger plan (`phase-2/cvar-cosignal-hysteresis-trigger/plan.md`); the priority resolver extension plan (`phase-2/priority-resolver-cvar-cosignal/plan.md`).
- **Blocked by:** the four Phase-2 preconditions (synthesis §5.1 + migration plan §4). **This plan does NOT ship unless all four preconditions pass.** Specifically:
  - Precondition (b) — gate-zero tail-data audit (`engine-audit/abstain-failsafe-coverage-audit/plan.md` is the closest sibling but a fresh data-audit cycle is the upstream).
  - Precondition (c) — measured Tier-1 latency prototype (`engine-audit/live-execution-path-latency-audit/plan.md` is the gate's home).
- **Blocked by:** the `tier1_seed` determinism sibling plan (`phase-2/tier1-seed-determinism/plan.md`).
- **Blocked by:** the deferred `017_path_generator.sql` migration (which is itself blocked by the Phase-2 gate passing).
- **Blocked by:** `run_monte_carlo` remaining frozen through last symphony cutover (H7 — a permanent constraint for both phases).

## Definition of Done

- All RED tests above land first; GREEN: every test passes.
- Full-tree pytest with HEAD SHA + count + zero errors.
- The G-1 ★ blast-radius enumeration is committed as a verification artifact — a list of every `run_monte_carlo` consumer with the assertion that none is touched by this PR.
- The Tier-1 pre-open batch latency prototype result is captured in the commit message: *"Tier 1 batch wall-clock = X ms for N symphonies; pre-open budget = Y ms; margin = Z ms."*
- The path bank file-cache + manifest are operational; `bank_sha256` matches the `.npy` content; replay verifies the hash.
- The Politis-White block-length selector is implemented (or wraps a small numpy helper) and is **independent of strategy P&L** — the test asserts its inputs are returns only, not decisions or P&L.
- All new constants in `math_engine.py` (or `forward_paths.py`) carry source comments — block-length minimum, regime feature count, path-count target.
- The four Phase-2 preconditions have been authorized as PASS by the user / PM in writing before this plan executes. (The plan **stops at design time** absent that authorization.)
- The R-3 residual is documented in the PR commit message verbatim.

## Risk callouts / hazards

- **G-2 ★ (NEVER a "parameter change").** `simulate_forward_paths` is named, documented, and tested as net-new. A future reviewer must see at a glance that this is not an extension of `run_monte_carlo`. A test asserts the function name; a regression test asserts `run_monte_carlo`'s signature is byte-identical to `main`.
- **G-1 ★ (blast radius).** The 7+ consumers of `run_monte_carlo` are listed in `project_mc_sentinel_consumer_blast_radius`. None is touched. The plan commits a verification artifact.
- **C-2 ★ (GBM / IID bootstrap KILLS).** The function structurally cannot be either; the test enforces it.
- **C-3 ★ (volatility clustering).** Block bootstrap is the structural mechanism. Block length 1 = IID degenerate = C-2 ★ kill. The Politis-White selector ensures block length > 1.
- **C-5 ★ (regime classifier feature count low + de-correlated).** ~3-5 macro/vol features, NEVER the 6-layer heuristic stack (the architecture-provenance research §6a is the strongest adverse finding in the entire research set — fed-raw heuristics into kNN on a small reference window is the redundant-cluster failure case).
- **B-1 ★ / B-2 ★ (latency).** Tier 1 = out-of-band; Tier 2 = light array reduction only. The benchmark is a Phase-2 entry gate; if it fails, Phase 2 stops (§5.7 binding).
- **NN1 (★ load-bearing).** Block length frozen by Politis-White (model-free, on the return series — independent of strategy P&L). Regime classifier features frozen by stylized-fact / a-priori choice. Generator FAMILY (stationary block bootstrap) frozen by theory. **None** of these enters the BHY haircut's search space. The spec bundle records each with `freeze_discipline ∈ {STYLIZED_FACT, THEORY, MANDATE}`.
- **F-4 ★ (insufficient-data sentinel).** `paths = None` mirrors MC's `None`. Downstream consumers abstain fail-safe; the protective stop still fires on the ticks-below-stop condition alone. The new core NEVER disables the safety floor.
- **D-2 ★ (non-circular fixture provenance, LOAD-BEARING).** The golden fixture is computed by an **independent** reference implementation. A fixture from the same code under test = circular = automatic Gate-1 fail.
- **M-2 ★ (persisted-artifact replay determinism).** The path bank's `.npy` content is hashed; `bank_sha256` recorded in the manifest. Replay verifies hash match; mismatch = abstain fail-safe + Gate-1 fail.
- **R-3 (pre-sim-bank blindness to intraday tail).** Documented residual. The fail-safe abstention covers the bank-stale case; the intraday-tail blindness itself is structural and accepted.
- **R-6 (no hand-set conservatism adjustment, ★ forbidden).** A "conservatism factor" applied to the bootstrap output would re-introduce an ad-hoc heuristic. The plan does not ship one. The historical-worst ceiling (C-4) is accepted as the structural property.
- **MC sentinel discipline.** `run_monte_carlo` returns `None` when insufficient (`MC_INSUFFICIENT_HISTORY_SENTINEL`). `simulate_forward_paths` mirrors this with `ForwardPathBundle(paths=None, ...)`. Two parallel sentinels — never crossed.
- **Two-DB boundary (E-2 ★).** `path_generator_calibrations` + `path_bank_manifest` are state-DB. `.npy` is a file-system cache (separate from both DBs).
- **`is_live` explicit (F-1 ★).** Tier 1 batch and Tier 2 read both honor the `is_live` flag; no path to `submit_order` / `place_order` from inside `simulate_forward_paths`. Pure function.

## Out of scope

- A live CVaR **trigger**. The Phase-2 endpoint is a CO-SIGNAL (§5.2). The trigger plan is sibling (`phase-2/cvar-cosignal-hysteresis-trigger/plan.md`) — that plan implements the hysteresis state machine that consumes the `ForwardPathBundle`.
- Mutating `run_monte_carlo`. Frozen until last symphony cuts over (H7). The 7+ consumers untouched.
- GARCH-FHS, EVT, or any alternative generator family. Stationary block bootstrap is the chosen Phase-2 generator (synthesis §5.3 + migration plan §3.2 — generator-agnostic schema, but THIS plan ships block bootstrap). A future Phase-2.1 cycle could swap the family; the schema (`calibration_params` JSON blob) absorbs the change.
- A signed `cvar_divergence` quantity. Forbidden by §B.6 (rejection of the divergence idea).
- A hand-curated regime-shift label set. The regime classifier is feature-based, NOT label-based.
- An EVT extrapolation tail. EVT adds the most DoF; rejected at design time (synthesis §5.3 — historical-worst ceiling accepted).
- A conservatism factor / shrinkage adjustment / heuristic safety multiplier. R-6 forbidden.
- Per-cycle GARCH MLE fit (B-1 ★ kill if introduced).
- LLM-authored advisor commentary on the path bank. The `advisor_observations` table is Phase 1; LLM-authored Advisor roles (Spec Critic, Divergence Explainer, Narrator) are Phase 2 — but they are sibling plans, not in scope here.
- An Optuna-tuned block length, regime feature, or generator family. NN1 binding.
