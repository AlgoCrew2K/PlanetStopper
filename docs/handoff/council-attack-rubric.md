> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

# Council Attack Rubric — EUT+CVaR Exit-Core Migration

**Author:** `critic` (quant-code-reviewer) — adversarial gate, decision-science-council
**Date:** 2026-05-22
**Branch:** `design/decision-science-council`
**Status:** SOLO-PHASE deliverable. This is the standard every candidate architecture
is judged against. No candidate, no synthesis, reaches the PM until every gate below
is explicitly cleared.

---

## 0. How To Use This Rubric

- The rubric is **24 gates** in **12 attack families** (A–L). Each gate has an ID
  (e.g. `D-3`), a one-line **claim under attack**, a **pass bar** (what a candidate
  must demonstrate), and the **kill condition** (what fails it outright).
- **Three verdict tiers per gate:** `PASS` / `WEAK` / `FAIL`.
  - `FAIL` on any **load-bearing** gate (marked ★) = the candidate is dead; it cannot
    be a finalist.
  - `WEAK` is survivable **only** if the residual risk is honestly documented in the
    synthesis with a named owner and a mitigation or an explicit accepted-risk note.
  - An undocumented `WEAK` is treated as a `FAIL` by the gate (me).
- **The premise is on trial too.** Family L makes `skeptic`'s minimalist floor a
  mandated comparator. A richer finalist that cannot beat the minimalist floor on
  evidence — not on narrative — does not ship.
- **Evidence standard.** Every PASS claim must cite either (a) a research report
  section, (b) a `file:line` in the current codebase, or (c) a named, checkable
  validation artifact the candidate commits to producing. "It is principled" /
  "it is institutionally standard" is **not** evidence that *this composition* is
  sound — the provenance report is explicit that the composition is novel and the
  novel kNN→CVaR seam is contra-indicated.

---

## Family A — Math Correctness & Numerical Stability ★

### A-1 ★ — CVaR estimator is the correct estimator for a discrete sample
**Claim under attack:** "We compute CVaR from the simulated paths."
**Pass bar:** the design names the **Rockafellar-Uryasev general-distribution
definition** (R&U 2002) for the discrete/empirical MC sample — NOT the naive
"average of losses beyond the empirical VaR," which is biased on discrete samples
(eut-cvar-research §2.1). The design states the estimator formula and the alpha
handling at the atom.
**Kill condition:** design uses naive empirical-mean-beyond-quantile, or leaves the
estimator unspecified, or computes CVaR on a sample whose discreteness it ignores.

### A-2 ★ — Non-finite propagation is closed at the new boundary
**Claim under attack:** "The new path/CVaR layer is numerically safe."
**Pass bar:** every new pure function entry validates float inputs the way the
existing engine does — `_reject_non_finite` / `_reject_non_finite_in_records`
(`math_engine.py:30-54`). A NaN must not silently short-circuit a comparison to
`False` and suppress a stop; an Inf must not spuriously trigger one (the policy is
stated verbatim at `math_engine.py:36-39`). CVaR of an all-equal or degenerate path
set is defined (no 0/0).
**Kill condition:** any new path-gen / utility / CVaR function that can receive a
non-finite and not reject it at entry; any divide that can be 0/0 (cf. the existing
`_z` zero-std guard at `math_engine.py:783-786` — the new code must match that bar).

### A-3 — Utility function is horizon-unbiased OR the horizon convention is explicit
**Claim under attack:** "Exit when E[U(exit)] > E[U(hold)]."
**Pass bar:** the design states a horizon convention and acknowledges the
Henderson-Hobson time-inconsistency result (eut-cvar-research §1.2 item 3;
phase0-generator §3). It picks ONE of the three documented fix families
(A: horizon-unbiased utility; B: rolling/receding-horizon, inconsistency *managed*;
C: nested/iterated CVaR, time-consistent but the effective alpha is no longer the
nominal 5%) AND states the consequence it accepts. Per phase0 §3.5 there is **no**
option that is simultaneously time-consistent, keeps literal end-of-horizon 5%-CVaR
semantics, and is field-proven — the candidate must pick its poison openly.
**Kill condition:** "E[U(holding)]" used with no stated horizon; or fix family C
chosen while still claiming a literal "5% CVaR budget"; or the time-inconsistency
result not even acknowledged.

### A-4 — Wealth argument into the utility function is consistent and stated
**Claim under attack:** "CRRA/CARA gamma is the risk-aversion knob."
**Pass bar:** the design states exactly what wealth argument enters U (position P&L
vs total wealth vs a wealth proxy) and shows that choice is consistent at both the
hold and exit branches. eut-cvar-research §1.2 items 1-2: CARA's effective risk
aversion drifts under an inconsistent wealth proxy; CRRA scale-invariance breaks
silently if the wealth base is inconsistent.
**Kill condition:** wealth argument unspecified, or different between the hold and
exit branches.

---

## Family B — Execution-Path Latency vs the 1-Minute Non-Blocking Rule ★

### B-1 ★ — No blocking I/O added to the per-cycle execution path
**Claim under attack:** "The new exit core runs every minute."
**Pass bar:** the design proves that nothing it adds to the per-cycle path
(`alpha_bot_execution.py:~1063-1496`) performs blocking I/O. Project CLAUDE.md
architecture constraint 1 is absolute. Specific trap: GARCH/EVT path generators
need MLE *calibration*; phase0-generator §1.3 flags GARCH MLE as "the binding
concern against the no-blocking-I/O constraint." If calibration is needed, the
design must place it **off** the minute path (pre-fetched / cached / background)
and prove the cached artifact's staleness bound.
**Kill condition:** any per-cycle GARCH/GPD MLE fit, any per-cycle history fetch,
any per-cycle disk/network call introduced by the new core.

### B-2 ★ — Path-count vs CVaR-stability vs latency budget is quantified jointly
**Claim under attack:** "N paths is enough and fast enough."
**Pass bar:** the design states a concrete path count and a per-cycle wall-clock
budget, and reconciles them with the tail-stability requirement. eut-cvar-research
§2.3 + phase0-generator §2: a stable 5% CVaR wants order ~1,000+ paths for
generators that produce novel tail values; for empirical-pool methods (kNN MC,
block bootstrap) more draws do NOT buy tail fidelity past the genuine sub-5%
observation count (phase0 §2.2 — "drawing 50,000 times from a 150-neighbour pool
does not add tail information beyond the ~7-8 genuine sub-5% neighbour-days"). The
candidate must state which regime it is in and not paper the gap.
**Kill condition:** path count and latency quoted separately with no reconciliation;
or a "few hundred paths" plain-MC 5% CVaR claimed as stable.

### B-3 — Per-cycle cost scales acceptably across the live symphony set
**Claim under attack:** "Per-position MC is cheap."
**Pass bar:** cost is stated as `N_paths × horizon_steps × N_positions × N_symphonies`
and shown to fit the minute budget at the realistic live portfolio size, not for a
single position. Variance-reduction claims (IS) must note IS adds a tilt-tuning
surface and can backfire under tail misspecification (eut-cvar-research §5.2 item 3;
phase0 §2.3).
**Kill condition:** feasibility argued only for one position; or VR used to hit the
budget with the tilt left unspecified.

---

## Family C — CVaR Tail-Estimation Error & Trigger Robustness ★

### C-1 ★ — Hysteresis on the CVaR trigger
**Claim under attack:** "Exit when CVaR breaches the budget."
**Pass bar:** the design has explicit hysteresis / multi-tick confirmation on the
CVaR breach, structurally analogous to the existing `EXIT_CONFIRM_TICKS = 3` and
`VWAP_BREAK_CONFIRM_TICKS = 3` confirmation counters. A *hard, single-tick* CVaR
trigger converts tail-estimation noise directly into spurious exits (architecture-
provenance §6b; eut-cvar-research §2.3). The council brief states this as an
established fact — a candidate without hysteresis fails here automatically.
**Kill condition:** a single-evaluation hard CVaR trigger with no confirmation
window.

### C-2 ★ — Tail-estimation-bias direction is acknowledged and not load-bearing
**Claim under attack:** "The CVaR budget catches tail risk."
**Pass bar:** the design acknowledges that small-sample empirical CVaR is **biased
toward understating the tail** (eut-cvar-research §2.3 — "fails toward not exiting")
and states why its generator choice does not leave the *protective* function
silently disabled. If the generator is GBM or IID bootstrap the candidate is
already dead here (both understate the tail in the dangerous direction —
eut-cvar-research §3.1/§3.3, crux table §3.6).
**Kill condition:** generator is GBM or IID bootstrap; or the downward tail bias is
not acknowledged; or the protective stop's last-line behavior depends on a CVaR
estimate that can be biased permissive.

### C-3 ★ — Path generator captures volatility clustering for multi-bar horizons
**Claim under attack:** "Our generator's tail is good enough for the budget."
**Pass bar:** if the horizon is multi-bar, the generator preserves volatility
clustering (block bootstrap, GARCH-FHS, or regime-conditioned block bootstrap —
phase0 §1.2/§1.3/§1.6). The crux is named in three reports: "the path generator is
the dominant risk, not gamma/lambda." If the candidate extends the existing kNN MC,
it must confront phase0 §1.6: i.i.d. resampling inside a multi-day path destroys
autocorrelation of squared returns; the kNN approach is competitive ONLY at the
short end of the horizon range and is "not a like-for-like substitute for FHS at
multi-day horizons."
**Kill condition:** multi-bar horizon with an i.i.d.-within-path generator and no
acknowledgement of the clustering gap.

### C-4 — Generator's historical-worst-case ceiling is acknowledged
**Claim under attack:** "The CVaR budget is conservative."
**Pass bar:** the design acknowledges that kNN MC, block bootstrap, and
FHS-on-empirical-residuals all share a hard ceiling at the historically realized
worst case (phase0 §1.6, eut-cvar-research §3.6) — only EVT extrapolation, or
FHS re-volatization partially, escapes it. The candidate states whether it accepts
that ceiling or breaks it, and at what cost (EVT adds the most degrees of freedom
of any candidate — phase0 §1.4).
**Kill condition:** the budget marketed as "catches tail risk" with no mention of
the worst-case ceiling.

### C-5 — kNN regime matcher is not fed many correlated features
**Claim under attack:** "Heuristic signals become conditioning features for the
regime matcher."
**Pass bar:** if a kNN matcher is in the design, the conditioning feature count is
kept low and de-correlated. architecture-provenance §6a is the strongest adverse
finding in the entire research set: distance concentration bites at ~10-15
effective dimensions; trailing-stop / VWAP / vol-ratchet signals are *strongly
mutually correlated* (all functions of recent price, trend, vol) — the exact
redundant-cluster failure case; and a ~125-day reference set makes "k nearest
neighbours barely distinguishable from k random draws." A candidate that pipes the
6-layer heuristic stack as features into a kNN matcher fails unless it commits to
dimensionality reduction / metric learning / aggressive feature selection AND
proves the resulting effective dimension.
**Kill condition:** many correlated heuristic features fed raw into a kNN matcher on
a small reference window.

---

## Family D — Fixture-Testability ★

### D-1 ★ — Every new math layer has a golden-fixture test
**Claim under attack:** "The new math is correct."
**Pass bar:** project CLAUDE.md — "Every change to math layers requires a
golden-fixture test." Each new pure function (path gen, CVaR estimator, utility,
the new resolver) ships a golden fixture with captured-or-derived expected outputs.
This is also a `quant-code-reviewer` standing gate (operating rule 1).
**Kill condition:** any new `math_engine.py` function without a golden-fixture test
referenced in the design's test plan.

### D-2 ★ — Fixture provenance is non-circular
**Claim under attack:** "We have fixtures for the path generator."
**Pass bar:** fixtures are captured from a producer (`/api-fixture`) or
schema-derived with a runtime validator — NOT defined inline alongside the
generator they test. Global rule `feedback_verify_backend_contract_before_fixtures`:
parser+fixture co-design is circular and an automatic Gate-1 fail. For a stochastic
path generator the fixture must pin a **seeded** deterministic draw; a hand-authored
"expected CVaR" computed by the same code under test is circular.
**Kill condition:** fixture for a generator authored from that generator's own
output with no independent reference (e.g., an analytic Student-t closed-form CVaR
check, a frozen captured neighbour pool, or a cross-checked reference implementation).

### D-3 — Stochastic outputs are testable deterministically
**Claim under attack:** "MC output is random — we assert ranges."
**Pass bar:** the new generator is seeded the way `run_monte_carlo` is —
`derive_cycle_mc_seed` (`math_engine.py:695-702`), SHA-256 of cycle_id into the
64-bit space, isolated `np.random.default_rng`, never the numpy global RNG. A test
must be able to assert an **exact** value at a fixed seed, not just a range.
**Kill condition:** new stochastic code touches the global RNG, or is unseeded, or
can only be range-tested.

### D-4 — API calls remain fixture-testable
**Claim under attack:** "The generator needs more history."
**Pass bar:** any new Composer/Alpaca data dependency is fixture-testable (project
CLAUDE.md coding standard). If the design needs intraday bars (phase0 §2.3 notes
intraday history is a *different* return process), the new fetch must have a
captured fixture and the design must not assume intraday returns are scaled-down
daily returns.
**Kill condition:** a new external data dependency with no fixture path.

---

## Family E — Schema Reversibility ★

### E-1 ★ — All schema changes are additive-first
**Claim under attack:** "We persist the new decision fields."
**Pass bar:** project CLAUDE.md — "additive-first, NULLable + DEFAULT, never
destructive in one step." Every new column on `database.py` state-DB tables
(decisions, positions, chart history) is NULLable with a DEFAULT; a migration file
exists; first deploy contains no `DROP`. `quant-code-reviewer` standing gate 4.
**Kill condition:** a destructive migration on first deploy; a NOT NULL column with
no default added to an existing table; no migration file.

### E-2 ★ — Two-DB boundary is not crossed
**Claim under attack:** "The exit core reads tuned spec facets."
**Pass bar:** state DB owns live positions/decisions; optimization DB owns Optuna
studies. The frozen spec facets (generator family, block length, horizon, alpha,
gamma, lambda) live on one side; if the live engine needs them, the design **copies
rows** — never cross-joins state DB and optimization DB in app code (project
CLAUDE.md architecture constraint 3; `quant-code-reviewer` design ethos).
**Kill condition:** any design path that cross-joins the two DBs in app code.

### E-3 — Replay/backtest schema and live schema stay coherent
**Claim under attack:** "Backtest replay and live use the same decision record."
**Pass bar:** the autotuner replay tick record (`mc_prob` field today) and the live
decision record evolve together; new CVaR/utility fields appear in both or the
divergence is deliberate and documented. The MC-sentinel blast-radius memory
(`project_mc_sentinel_consumer_blast_radius`) shows a return-type change touches 7+
sites — the same discipline applies to any new decision field.
**Kill condition:** a new decision field in live but not in replay (or vice versa)
with no stated reason.

---

## Family F — Live-vs-Replay Safety Boundary & Determinism ★

### F-1 ★ — `is_live` stays explicit, never default
**Claim under attack:** "The new core works in live and replay."
**Pass bar:** project CLAUDE.md architecture constraint 4 — `is_live=True` is
explicit, never a default. No new code path can reach a live order
(`submit_order` / `place_order` / `cancel_order` / `liquidate`) without an explicit
live-mode flag check. `quant-code-reviewer` standing gate 2.
**Kill condition:** any new exit-decision path that can reach a broker call without
an explicit live-mode check; `is_live` defaulted to True anywhere.

### F-2 ★ — Backtest-replay parity is bit-identical under a fixed seed
**Claim under attack:** "Replay reproduces the live decision."
**Pass bar:** Validation Gate 1 (backtest-replay parity) is one of the BOTH gates
the user mandated. The design must show the new core is **deterministic given
(cycle_id, inputs)** — same seed derivation as `derive_cycle_mc_seed`, isolated
RNG, no global state, no wall-clock reads inside the math. The design must name the
exact parity check (replay a historical day, assert the new decision record equals
the live-captured one bit-for-bit).
**Kill condition:** any nondeterminism in the new core (global RNG, wall-clock,
unordered dict iteration affecting numerics, thread-scheduling-dependent reduction);
or no concrete parity-check definition.

### F-3 ★ — Live shadow-mode gate is verifiably defined
**Claim under attack:** "We will validate live before cutover."
**Pass bar:** Validation Gate 2 (live shadow mode) is the other mandated gate. The
design must specify shadow mode concretely: the new core runs alongside the
incumbent, emits its would-be decision to a log/record, takes **no** live action,
and a divergence metric is defined. The dashboard stays read-only (it may *display*
shadow output; it must never *act*). `quant-code-reviewer` standing gate 8.
**Kill condition:** shadow mode described as "we'll watch it" with no recorded
divergence metric, no defined run length, no acceptance threshold; or shadow mode
that can take a live action.

### F-4 — Determinism survives the MC-sentinel contract
**Claim under attack:** "Insufficient history is handled."
**Pass bar:** the new core preserves a fail-safe out-of-band sentinel discipline.
Today `run_monte_carlo` returns `None` (`MC_INSUFFICIENT_HISTORY_SENTINEL`) and the
protective stop still fires on the ticks-below-stop condition alone
(`math_engine.py:70-74`, `:739-744`). A CVaR engine that cannot compute a budget
(thin pool, failed calibration) must fail **safe** — the protective exit must still
function — and the sentinel must be out-of-band, never an in-band CVaR value.
**Kill condition:** insufficient-data path returns an in-band CVaR, or disables the
protective stop, or is undefined.

---

## Family G — MC-Sentinel Consumer Blast Radius ★

### G-1 ★ — Every `run_monte_carlo` consumer is enumerated and addressed
**Claim under attack:** "We extend / replace the Monte Carlo."
**Pass bar:** `run_monte_carlo`'s output is consumed at 7+ sites across
`alpha_bot_execution` / `reporting` / `synthetic_history` / `autotuner` (project
memory `project_mc_sentinel_consumer_blast_radius`; baseline §5). Any change to its
return **type or semantics** must list every consumer and state the migration for
each: trailing-stop arm/disarm, MC sanity veto in `compute_exit_confirmation`, TP
arm/confirm in `compute_tp_confirmation`, `mc_history` buffer, port-level
`mc_sanity_gate_would_block` snapshot, chart history, autotuner replay `mc_prob`.
**Kill condition:** a return-type/semantics change to `run_monte_carlo` (or its
replacement) that does not enumerate all consumers; treating the change as
math_engine-local.

### G-2 ★ — Net-new path simulator is named as net-new, not a "parameter change"
**Claim under attack:** "We extend the existing MC for forward paths."
**Pass bar:** phase0-generator §0 is explicit — the current `run_monte_carlo` is a
**single-day return resampler, NOT a forward-path simulator**; each "path" is one
i.i.d. draw, there is no time axis. A multi-day CVaR needs net-new construction
(council brief; phase0 §0). The design must call this what it is — net-new — and
budget for it, not present it as a tuning tweak.
**Kill condition:** the multi-day path/horizon-aggregation layer presented as an
extension/parameterization of the existing function rather than net-new code.

### G-3 — The 6 incumbent layers' fate is explicit
**Claim under attack:** "Replace the heuristic stack."
**Pass bar:** scope is "replace, phased." The design states, per incumbent layer
(vol-scaling, time-squeeze, parabolic ratchet, breakeven, VWAP×2, MC), whether it is
deleted, demoted to a conditioning feature, or retained as a safety floor. The
deterministic priority resolver (`resolve_trigger_priority`,
`math_engine.py:669-692`) and its named order `_TRIGGER_PRIORITY_ORDER` must have a
stated successor. Baseline §2.2: the layers do **not** conflict today — a candidate
that justifies itself by the "conflicting heuristics" framing is repeating a claim
the baseline report refuted and must be challenged on it.
**Kill condition:** "replace the stack" with no per-layer disposition; or the
priority resolver left with no successor.

---

## Family H — Overfitting Exposure & BHY-Haircut Integrity ★

### H-1 ★ — Specification facets are frozen by evidence independent of OOS P&L
**Claim under attack:** "Two parameters — gamma and lambda — so it's not overfit."
**Pass bar:** the design freezes generator family, block length, horizon, and alpha
by the disciplines in phase0-generator §4.2 — automatic block-length selection
(Politis-White) from the autocorrelation structure; alpha by mandate/risk-budget;
horizon by decision cadence; generator by model-free stylized-fact / ES-coverage
tests on the **return series itself, not strategy P&L**. eut-cvar-research §4: "2
parameters" counts only parameter risk and hides specification risk; the honest
count is 2 visible knobs plus 5-7 structural choices.
**Kill condition:** any spec facet selected by which value produced the best
backtest; "mathematically immune to curve-fitting" asserted (eut-cvar-research §4.3
— not defensible).

### H-2 ★ — The BHY multiple-testing haircut is preserved or strengthened
**Claim under attack:** "The new core is tuned by the autotuner."
**Pass bar:** council brief — "the BHY haircut is an asset; any candidate that
weakens it is suspect." The live Harvey-Liu / Benjamini-Hochberg-Yekutieli haircut
(`autotuner.py:698-732`, `:319-356`; baseline §4.4) with the Yekutieli c(N)
dependence factor must still gate any tuned parameter of the new core. If the new
core adds tuned parameters (gamma, lambda, block length if tuned), they enter the
same FDR gate — they do not get a private, un-haircut tuning path.
**Kill condition:** new tuned parameters bypass the BHY haircut; the haircut's
c(N) dependence factor removed or weakened; the purge/embargo
(`PURGE_DAYS`/`EMBARGO_DAYS`) dropped.

### H-3 ★ — gamma/lambda tuning does not consume the frozen-eval fold
**Claim under attack:** "We tune gamma and lambda on walk-forward."
**Pass bar:** the train/validation/frozen-eval split (60/20/20, port 50/20/30 —
baseline §4.3) is preserved; selection happens on validation only; the frozen-eval
fold is consumed exactly once post-selection. New parameters tune on validation, not
on frozen-eval.
**Kill condition:** any new-parameter selection that reads the frozen-eval fold; or
the fold consumed more than once.

### H-4 — Effective parameter count is honestly stated for the haircut
**Claim under attack:** "Fewer knobs than the heuristic stack."
**Pass bar:** the design states the honest effective parameter count (visible knobs
+ structural choices that were data-influenced) and confirms the haircut's trial
count / deflation reflects the true search size. Per baseline §4.4 the code already
uses a Sortino t-stat, not DSR — the new core must not silently reintroduce a
Sharpe-deflation category error (the "H-6 category error" the code comment at
`autotuner.py:266-271` explicitly rejects).
**Kill condition:** trial count understated to flatter the haircut; DSR/Sharpe
deflation reintroduced where the code deliberately uses a Sortino sampling
distribution.

---

## Family I — AI Advisor Scope-Boundary Integrity ★

### I-1 ★ — The Advisor cannot auto-tune specification facets
**Claim under attack:** "The AI Advisor is a Specification Critic."
**Pass bar:** the Advisor's 4 roles are bounded — Specification Critic, Shadow-mode
Divergence Explainer, Regime & Decision Narrator, Overfitting Conscience. ALL FOUR
are **advisory/read-only**. The Advisor may *critique* a spec facet; it must never
*write* one. The design must show the Advisor has no write path to generator family,
block length, horizon, alpha, gamma, or lambda.
**Kill condition:** any Advisor code path that mutates a spec facet, a tuned
parameter, or an engine setting; the Advisor wired into the autotuner's search.

### I-2 ★ — The Advisor is walled off from the frozen-eval fold
**Claim under attack:** "The Overfitting Conscience watches the tuning."
**Pass bar:** the Advisor must not see the frozen-eval fold's contents before it is
consumed — if the "Overfitting Conscience" reads OOS results and feeds commentary
back into spec choices, it has turned the frozen fold into a selection input
(eut-cvar-research §4; phase0 §4.1). The design must state the Advisor's data
access explicitly and prove the frozen fold is not in it during selection.
**Kill condition:** the Advisor reads frozen-eval results during the selection
window; Advisor commentary is allowed to influence a re-tune.

### I-3 — The Advisor cannot take or trigger a live action
**Claim under attack:** "The Advisor narrates decisions on the dashboard."
**Pass bar:** the dashboard is a read-only operator surface (project CLAUDE.md
architecture constraint 2; `quant-code-reviewer` standing gate 8). The Advisor's
output is display/log only — it must never call an engine function that mutates
state and never reach a broker call. Logging-redaction gate applies: Advisor log
lines must not echo raw Composer/Alpaca response bodies (`quant-code-reviewer`
standing gate 7).
**Kill condition:** Advisor output wired to any state mutation or order path;
Advisor logs echo raw API response bodies; the dashboard gains an action surface.

---

## Family J — Phasing Safety (scope = "replace, phased") ★

### J-1 ★ — Each phase is independently shippable and reversible
**Claim under attack:** "We migrate in phases."
**Pass bar:** the design decomposes the migration into phases where each phase
(a) leaves the engine in a working, deployable state, (b) is independently
revertible, and (c) does not require a big-bang cutover. The incumbent stack stays
the live decision-maker until the new core has cleared BOTH validation gates.
**Kill condition:** a phase that leaves the engine non-functional; a phase that
cannot be reverted without data loss; the new core taking live decisions before
both gates pass.

### J-2 ★ — The incumbent remains the safety floor until cutover is evidence-backed
**Claim under attack:** "The new core is ready after shadow mode."
**Pass bar:** the cutover from incumbent to new core is gated on **documented
evidence** from both Gate 1 (replay parity) and Gate 2 (shadow divergence within
threshold) — not on a phase calendar. The design names who signs off the cutover
and on what artifact.
**Kill condition:** cutover scheduled by phase number rather than gated on evidence;
no named cutover artifact.

### J-3 — Rollback path is defined for a post-cutover failure
**Claim under attack:** "After cutover the new core is live."
**Pass bar:** the design defines how to roll back to the incumbent if the new core
misbehaves in production — the incumbent code path is retained (not deleted) for at
least one defined observation window, and the schema additivity (E-1) makes the
rollback non-destructive.
**Kill condition:** the incumbent deleted at cutover with no rollback path.

---

## Family K — Validation Gates Are Verifiably Met ★

### K-1 ★ — Gate 1 (backtest-replay parity) has a concrete, checkable definition
**Claim under attack:** "It passes backtest replay."
**Pass bar:** the design states the exact replay-parity test: a fixed set of
historical cycles, the new core run in replay, the decision record asserted
**bit-identical** to a reference. Depends on F-2 determinism. The parity test is a
named, committed artifact, not a description.
**Kill condition:** "we ran a backtest and it looked fine"; no bit-level reference;
parity asserted on P&L rather than on the decision record.

### K-2 ★ — Gate 2 (live shadow mode) has a pre-registered acceptance threshold
**Claim under attack:** "Shadow mode validated it."
**Pass bar:** the divergence metric, the minimum shadow run length, and the
acceptance threshold are **pre-registered before shadow mode starts** (phase0 §4.2
pre-registration discipline). ES is not standalone-elicitable — crediting the CVaR
trigger needs a joint VaR-ES backtest (Acerbi-Székely / Fissler-Ziegel —
eut-cvar-research §2.4), not a raw P&L check. The design must name that machinery.
**Kill condition:** acceptance threshold chosen after seeing shadow results; CVaR
trigger "validated" by a raw P&L backtest with no joint VaR-ES coverage test.

### K-3 — Neither gate's evidence is recycled as selection input
**Claim under attack:** "Both gates passed."
**Pass bar:** the replay set and the shadow period are confirmation-only — neither
is used to choose a spec facet or re-tune a parameter (phase0 §4.1; H-1/H-3/I-2 all
feed this). If a gate fails and the design is changed, the gate's data is burned —
a fresh OOS window is required.
**Kill condition:** a failed gate's data reused to re-tune and then re-tested as if
fresh.

---

## Family L — The Premise Is On Trial ★

### L-1 ★ — Every richer finalist must beat `skeptic`'s minimalist floor on evidence
**Claim under attack:** "The EUT+CVaR core is the right architecture."
**Pass bar:** `skeptic`'s minimalist floor (the smallest defensible change — at the
limit, the incumbent stack itself, or the incumbent plus a single well-grounded
addition) is a **mandated comparator**. Any richer finalist must demonstrate, on
evidence independent of OOS P&L cherry-picking, that its added complexity buys
something the floor does not. eut-cvar-research cross-cutting verdict: the migration
trades *visible* parameter risk for *less visible* specification risk and the
literature "does not support the conclusion that this lowers total overfitting
risk." The burden of proof is on the richer candidate.
**Kill condition:** a finalist justified by narrative ("more principled,"
"institutional-grade") rather than by a concrete advantage over the minimalist
floor; the minimalist floor not evaluated as a peer candidate.

### L-2 ★ — The original pitch's false claims are not smuggled into the rationale
**Claim under attack:** the pitch's framing — "7+ conflicting heuristic variables,"
"one-size-fits-all 3-year static history," "mathematically immune to curve-fitting."
**Pass bar:** baseline §0 and §6 refute all three: 8 tunable params (6 Optuna-
searched), tuned per symphony, no inter-layer conflict (one total deterministic
priority order), a rolling 125-day walk-forward with purge/embargo and a live BHY
haircut, and MC already on the live path. The user's binding framing is explicit:
motivation is **methodology/defensibility upgrade, NOT an overfitting fix or a perf
play**. Any candidate or the synthesis that leans on the refuted claims, or sells
the migration as an overfitting fix, fails this gate.
**Kill condition:** the synthesis or any finalist repeats "conflicting heuristics,"
"static history," or "immune to curve-fitting"; or frames the migration as fixing
overfitting / improving performance rather than as a defensibility upgrade.

### L-3 — "Novel composition" is labeled as bespoke, not as established
**Claim under attack:** "This is an institutional-grade architecture."
**Pass bar:** architecture-provenance §5 — the four primitives are individually
grounded but the **full composition is not published or institutionally
documented**; it is "a bespoke composition of individually-proven parts," and the
kNN→CVaR seam is the genuinely novel and contra-indicated joint. The synthesis must
say this plainly. Individual-primitive soundness is NOT composition soundness.
**Kill condition:** the composition presented as an established/standard
architecture; primitive-level institutional adoption cited as if it validates the
whole stack.

---

## Gate Summary

| Family | Gates | Load-bearing (★) |
|---|---|---|
| A — Math correctness & numerical stability | A-1..A-4 | A-1, A-2 |
| B — Execution-path latency | B-1..B-3 | B-1, B-2 |
| C — CVaR tail error & trigger robustness | C-1..C-5 | C-1, C-2, C-3 |
| D — Fixture-testability | D-1..D-4 | D-1, D-2 |
| E — Schema reversibility | E-1..E-3 | E-1, E-2 |
| F — Live-vs-replay safety & determinism | F-1..F-4 | F-1, F-2, F-3 |
| G — MC-sentinel blast radius | G-1..G-3 | G-1, G-2 |
| H — Overfitting & BHY haircut | H-1..H-4 | H-1, H-2, H-3 |
| I — AI Advisor scope boundary | I-1..I-3 | I-1, I-2 |
| J — Phasing safety | J-1..J-3 | J-1, J-2 |
| K — Validation gates | K-1..K-3 | K-1, K-2 |
| L — The premise is on trial | L-1..L-3 | L-1, L-2 |

**24 gates, 12 families, 26 load-bearing.** A finalist must clear every ★ gate with
`PASS`, and every non-★ gate with `PASS` or a documented-and-owned `WEAK`. The
synthesis does not reach the PM until I confirm, by SendMessage to `risk-architect`,
that every finalist's residual risks are honestly documented and no ★ gate is
`FAIL`.

---

## Standing Reviewer Gates (always-on, not candidate-specific)

These eight `quant-code-reviewer` overlay gates apply to any code the council's
design ultimately produces — they are restated here so the synthesis's
implementation plan is written with them in view:

1. Math-layer change → golden-fixture test diff required (→ D-1).
2. No path to `submit_order`/`place_order`/`cancel_order`/`liquidate`/`is_live`
   without an explicit live-mode check (→ F-1).
3. Fixture provenance non-circular (→ D-2).
4. `database.py` change → additive migration file, no first-deploy `DROP` (→ E-1).
5. No hardcoded credentials / webhook URLs / account IDs.
6. No magic numbers in `math_engine.py` — every constant named + source comment.
7. New log lines must not echo Composer/Alpaca response bodies verbatim (→ I-3).
8. `app.py` routes must not call engine functions that mutate state (→ I-3).

---

## DEBATE-PHASE ADDENDUM (2026-05-22) — Rubric v2: 26 gates, 13 families

The debate phase produced two new gates and one refinement. The original 24 gates
above are unchanged. This addendum is authoritative where it overlaps.

### Family M — Structural Determinism & Wall Enforcement (NEW) ★

Added from `persistence-architect`'s proposed gates. Both are determinism/wall
invariants squarely in the council's mandate (the user named replay-parity and the
Advisor frozen-fold wall as binding). Verified not subsumed by E/F/I/K.

**M-1 ★ — The AI Advisor frozen-eval wall is a STRUCTURAL invariant, not a convention.**
I-2 requires the Advisor not read the frozen-eval fold; M-1 makes that *structurally
enforced*. The Advisor's data-access layer must be structurally incapable of reading
frozen-eval data — enforced at a single read helper that filters
`fold_role != 'frozen_eval'`, not by caller discipline — AND a wall breach must be a
queryable tripwire (any DoF-ledger row touching frozen-eval data after a spec
bundle's `frozen_at` is detectable by query). A convention-only wall fails M-1.

**M-2 ★ — Persisted derived artifacts are replay-deterministic from a recorded seed.**
Extends F-2. Any candidate that persists a derived artifact consumed by the decision
core (pre-simulated path bank, cached calibration) must record the generating seed;
a replay must regenerate the artifact bit-identically from that seed and verify by
hash. An unpersisted / wall-clock seed makes Gate 1 (K-1) structurally unachievable.
Catches the two-tier pre-sim-bank pattern. F-4 also binds: a Tier-2 read of an
absent/stale bank manifest must abstain fail-safe.

### L-1 refinement — design-time evidence standard

L-1 does NOT require a deployed experiment proving the trigger profitable. At design
time a richer finalist passes L-1 by demonstrating it closes a **specific,
documented deficiency the floor leaves open**, with its added complexity the minimum
needed to close it. The floor (`HARDEN`-core = M1 + M2) leaves exactly one
documented deficiency: M2 is a CVaR *diagnostic* — it changes zero decisions — so a
tail event the heuristic floor under-catches is observed but never acted on. A
richer CVaR-*trigger* finalist passes L-1 by claiming precisely that gap. Because
the *final* L-1 evidence needs M2-class data, **an M2-class CVaR diagnostic is a
shared Phase-1 prerequisite for any richer CVaR-trigger finalist** — the converge
output is a *sequencing*, not a beauty contest.

### Updated count

**26 gates, 13 families, 28 load-bearing (★)** — Family M adds M-1 ★ and M-2 ★.
