> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

# Decision-Science Council — Synthesis

**Owner:** risk-architect (risk-engine-specialist), council coordinator
**Council:** decision-science-council — risk-architect, tuning-architect, persistence-architect, skeptic, critic
**Date:** 2026-05-22
**Branch:** design/decision-science-council
**Status:** CONVERGED — critic's CONVERGE gate PASSED (all 9 binding conditions verified carried); all five council members signed off; cleared for delivery to the PM.
**Doc-sweep:** corrections H-1, H-3, H-4, H-5, H-7, H-8 applied inline on 2026-05-23 per the v3-and-divergence evaluation (`docs/handoff/decision-science-v3-and-divergence-evaluation.md`). H-9 closed by §0.1 below (and mirrored in `feature-plans/decision-science/README.md` §0.1). Doc edits are tagged inline with their `H-N correction` reference.

---

## THE HEADLINE — read this first

**The user asked to migrate AlphaBot v3's exit-decision core to an EUT+CVaR
architecture. After exhaustive adversarial design, the council's evidence-based
answer is: do not migrate the core — harden it. The migration is conditional.**

This is a **scope challenge**, not a compliant implementation of the literal
brief, and the council states it plainly rather than burying it. The four
research reports debunked all five load-bearing claims of the original pitch.
A live CVaR *trigger* — the centrepiece of the pitched migration — cannot be
validated at AlphaBot's data scale; that is a structural property of the data,
not a fixable design gap. The genuine, defensible core of the "decision
science" idea is small, ships now, and is fully validatable. The ambitious part
is reframed honestly to an evidence-gated co-signal that may never be authorized.

The user asked for a migration; the council's honest answer is **"harden now,
migrate only on evidence — and the evidence may never arrive."**

---

## 0. BLUF

**Two finalists. No third.**

- **FINALIST A — HARDEN (recommended).** Replace the hand-tuned
  Sortino+loss-aversion autotuner objective with a theory-grounded CRRA
  expected-utility objective; add a CVaR **diagnostic** (computed, logged,
  drives no trade); ship the overfitting-accounting and provenance spine. A
  complete, shippable, fully-validatable, **terminal-acceptable** architecture.
  Recommended **subject to** three binding conditions (S-1, S-2, S-3, §4).
- **FINALIST B — Phased Replace (presented honestly, not recommended).**
  Finalist A as Phase 1, then a conditional, evidence-gated Phase 2 that adds a
  forward-path simulator and a CVaR **co-signal** (never a sole trigger).
  Phase 2 is authorized only if four named preconditions all pass — and one is
  likely structurally unsatisfiable, so **Phase 2 may never unlock, and
  stopping permanently at Finalist A is a full success.**

Both finalists **share Phase 1 entirely.** The user's decision is whether to
pre-commit to the evidence-gated Phase-2 roadmap.

**The single decisive finding:** a live CVaR *trigger* cannot be validated at
AlphaBot's data scale. A powered joint VaR-ES coverage backtest
(Acerbi-Székely / Fissler-Ziegel) needs ~1,000 tail-relevant observations;
AlphaBot accrues ~6 tail days per 125-day fold, ~37 per 3 years. A trigger that
cannot be validated must not go live. This applies to **every** candidate.

---

## 0.1 Why two finalists, not three (H-9 — the Finalist-C exclusion, argued in-body per H-9 correction)

The evaluation flagged v3 as having argued the Finalist-C exclusion **only** in §10 (compliance index), never in the body. Per the H-9 correction, the argument is restated here once, in the body where a reader of the two-finalist structure naturally looks (the same argument is mirrored in `feature-plans/decision-science/README.md` §0.1):

**There is no coherent standalone third finalist.** The only candidate "third path" — pre-committing to the evidence-gated Phase-2 roadmap — is **not a separate architecture; it is Finalist B**. Finalist A is the terminal-acceptable floor; Finalist B is *Finalist A plus the evidence-gated Phase-2 roadmap*; "pre-commit to Phase 2" is therefore a **choice within Finalist B's framing**, not a distinct third architecture. The genuine decision space is **two finalists plus the user's pre-commit choice** — and the user has made that choice (scaffold Phase 2 now, evidence-gate execution). The roadmap structure is two finalists, with Phase 2's gating preconditions making the user's pre-commit visible and reversible.

A second candidate sometimes raised — *"Finalist A + a permanent diagnostic-grade CVaR layer that never moves money"* — collapses into Finalist A by inspection: M2 already ships that diagnostic-grade layer (§3.1). It is not a separate architecture; it is what Finalist A delivers.

---

## 1. The premise on trial

The four research reports (`decision-science-{baseline,eut-cvar-research,
architecture-provenance,phase0-generator}-2026-05-22.md`) debunked the pitch's
five load-bearing claims. The council treats these as established:

- **"7+ conflicting heuristic variables"** — false. 6 exit layers, 8 tunable
  parameters (6 Optuna-searched), ONE total deterministic priority resolver,
  no conflicts.
- **"One-size-fits-all 3-year static history"** — false. Rolling 125-day
  walk-forward with purge/embargo and a live BHY multiple-testing haircut;
  the 3-year window is a rolling kNN-MC pool re-fetched daily.
- **"EUT+CVaR is a proven institutional architecture"** — false *as a
  composition*. The primitives are individually grounded (CVaR-as-constraint =
  Basel FRTB); the full stack is unpublished and bespoke. Individual-primitive
  institutional adoption does not validate the composition.
- **"2-parameter model immune to curve-fitting"** — not defensible. It
  relocates *parameter* risk into less-visible *specification* risk; it does
  not lower total overfitting risk.
- **"Extend the existing MC to get CVaR"** — false. `run_monte_carlo` is a
  single-day i.i.d. resampler with no time axis; a multi-day CVaR is net-new.

**User's binding framing (honored throughout):** scope = "replace, phased";
motivation = methodology/**defensibility** upgrade, NOT an overfitting fix or a
performance play; validation = BOTH gates (backtest-replay parity + live
shadow-mode N weeks clean); a four-role AI Advisor, never auto-tuning, walled
off from the frozen-eval fold.

**Honest framing of the outcome (critic condition 1, L-2/L-3 gates).** This is
a successful *council* outcome even though it is a scaled-down *migration*. The
council does not frame the migration as "succeeding." It frames itself as
having done its job: the defensible core ships now (Finalist A); the
un-validatable ambition is reframed honestly (Finalist B Phase 2 co-signal);
the pitch's headline claims were already debunked by research. The user gets a
real defensibility upgrade *and* an honest account of why the bigger thing
cannot be validated.

---

## 2. What the debate settled — banked consensus (in every finalist)

Attacked from all five lenses across three debate rounds; survived.

### 2.1 The CRRA expected-utility offline objective (SETTLED — the one unambiguous win)

The current deployment objective is a Sortino ratio with five hand-tuned
loss-aversion multipliers (`autotuner.py:94-114`, `run_simulation`). It is
replaced — in **both finalists** — by a theory-grounded **CRRA
expected-utility objective** on the existing per-day guard-alpha series,
offline (autotuner only), with a single pre-registered risk-aversion parameter
`gamma`.

- skeptic's M1 and tuning-architect's certainty-equivalent (CE) objective are
  the **same move**: `CE = u⁻¹(mean(u(g_i)))` is a monotone transform of
  `mean(u(g_i))`, so as an Optuna objective they give identical trial rankings.
  The haircut runs on `mean(u(g_i))`; the audit displays `CE` in return units.
- **Binding correctness requirement S-2 (critic condition 3):** the per-trial
  BHY significance statistic is a **genuine one-sample t-stat**,
  `t = mean(U) / (sd(U)/√T)` on the CRRA-transformed series `U` (kept finite by the named `WEALTH_ARG_FLOOR > 0` on the input wealth argument `W` — H-1 correction; CRRA is unbounded below as `W → 0⁺` for `γ ≥ 1`, so "bounded" without the floor is false) —
  **NOT** `effect_size·√T`. A new `compute_crra_eu_tstat` replaces
  `compute_sortino_tstat` for this objective. Silently reusing the Sortino
  t-stat would be the exact "H-6 category error" the code already fixed once
  (`autotuner.py:266-271`). The BHY step-up + Yekutieli c(N) machinery is
  **100% preserved**; only the per-trial statistic changes. This re-derivation
  is **new statistical machinery that must be validated** — it is a cost, not
  free.

### 2.2 The overfitting-accounting correction (SETTLED)

The honest multiple-testing count for the BHY haircut is **additive**:

```
N_effective = N_optuna + S
```

where `S` = the count of additional distinct configurations evaluated on a
P&L / strategy-return basis beyond the single Optuna sweep actually run. Common
case (tour K specs cheaply, full-sweep the winner): `S = K − 1`. The
multiplicative form `N_optuna × D_spec` was **retracted** — it punishes trials
never run.

Three properties (owned by tuning-architect): (1) under the spec-freeze
discipline NN1 (§2.5), every facet is frozen by theory/mandate/calibration,
none by P&L → `S = 0` → `N_effective = N_optuna` exactly → the haircut is
byte-identical to today's. (2) The form is a deliberately **conservative upper
bound** — a haircut on a conservative bound errs safe (can reject a genuine
signal, cannot pass a spurious one). (3) It only bites when someone P&L-toured
a facet — it is a **tripwire** that enforces NN1 structurally, not a routine
penalty.

For any CVaR-derived haircut score (Phase 2 only): the t-stat's `T` is the
count of genuine independent tail observations (~7-8), **never the simulation
path count** — a shared path bank's pseudo-replication would over-credit `√T`.

### 2.3 A live CVaR TRIGGER is not a defensible deliverable (SETTLED — decisive)

Three independent, compounding reasons, each from a different lens:

1. **The horizon trilemma has no good answer at this quality bar**
   (phase0 §3.5 — an impossibility result). A fixed-horizon CVaR re-evaluated
   per minute is time-inconsistent and oscillates as the window slides. A
   time-consistent nested/iterated CVaR is correct but its effective alpha ≠
   the nominal "5%" — the flagship risk number is no longer the literal "5% of
   end-of-horizon P&L" the user asked for. No option is simultaneously
   time-consistent, literal-5%-CVaR, and field-proven.
2. **The trigger cannot be validated at AlphaBot's data scale.** A powered
   joint VaR-ES coverage backtest (ES is not standalone-elicitable —
   Fissler-Ziegel; needs Acerbi-Székely-class machinery) requires ~1,000
   tail-relevant observations. AlphaBot has ~6 tail days per 125-day fold, ~37
   per 3 years. The backtest is structurally underpowered — it cannot reject a
   mis-calibrated CVaR model, so it cannot honestly pass one either.
3. **The path generator is caught in a latency/responsiveness dilemma.** A
   generator heavy enough to produce a credible multi-day tail cannot run
   per-minute on the non-blocking execution path; a pre-computed path bank is
   non-blocking but structurally blind to an intraday-developing tail regime —
   the exact event a tail budget exists to catch.

**Therefore** the defensible CVaR contribution is: (a) the CRRA-EU objective
[ships now], (b) a CVaR **diagnostic** [ships now — a wrong diagnostic misleads
a human, it never moves money], and (c) at most a CVaR **co-signal**
[Phase 2, evidence-gated — narrows/vetoes/confirms an exit another layer
already supports, never solely fires one].

### 2.4 The horizon convention is a genuine unresolved trade-off (critic condition 5)

phase0 §3.5 is an impossibility theorem: no horizon convention is
simultaneously time-consistent, keeps literal end-of-horizon 5%-CVaR
semantics, and field-proven. Fix Family C (iterated/nested CVaR — time-
consistent, un-interpretable effective alpha) and Fix Family B
(rolling/receding — interpretable, time-inconsistent) each ship a disclosed,
owned residual. **The synthesis presents this as an open decision for the PM,
not a settled design choice** — see escalation §6.2.

### 2.5 NN1 — the spec-freeze hard gate (SETTLED — verbatim, critic condition 9)

**NN1 (synthesis hard gate):** the generator family and the horizon convention
may NEVER be frozen by P&L / backtest selection. Mechanism — the BHY haircut's
Yekutieli c(N) factor corrects multiple-testing ONLY over the Optuna trial
search it can see; a spec facet chosen by looking at strategy P&L is an
UNCOUNTED testing event, so a P&L-frozen generator makes the haircut understate
its effective N — the FDR gate is then silently miscalibrated and the haircut
becomes a lie by omission. NN1 is therefore not a methodology preference; it is
the precondition that keeps the BHY haircut TRUE. Generator family is frozen by
model-free stylized-fact / ES-coverage calibration on the return series;
horizon by decision cadence; both OUTSIDE the Optuna search space.

NN1 is the **rule**; the `N_effective = N_optuna + S` accounting (§2.2) is its
structural **enforcement** — a tripwire, a no-op in the honest case
(`S = 0` → `N_effective = N_optuna`, the haircut byte-identical to today's),
collapsing to one ledger row under HARDEN, biting only on an NN1 violation. It
is not "extra overfitting machinery."

### 2.6 The candidate landscape — what each member contributed (critic's "honest record")

No candidate "won." The architecture is the honest composition of all five:

- **skeptic** produced the **floor** — Finalist A (HARDEN).
- **risk-architect** produced the **Phase-2 risk-math design** — the
  block-bootstrap generator, the hysteresis trigger, the resolver extension.
- **tuning-architect** produced the **overfitting-control spine** — the
  spec-freeze discipline, the additive `N_effective` tripwire, the re-derived
  t-stat.
- **persistence-architect** produced the **schema spine** — the additive-first
  migration plan, the shadow-mode tables, the structural frozen-eval wall.
- **critic** produced the **code-soundness spine** — and explicitly conceded
  (self-attack) that critic's own candidate is **not a standalone finalist**.
  Its contribution: no mutation of `run_monte_carlo` (zero blast radius across
  7+ consumers); the `CVaRAssessment` frozen typed object with
  `tail_obs_count`; the single-resolver, no-new-arbiter composition;
  named-constant spec facets; the explicit `eut_cvar_live` flag. Finalist B's
  Phase 2 is built on this spine carrying risk-architect's decision content.

---

## 3. FINALIST A — HARDEN (recommended)

**One sentence.** Replace the hand-tuned Sortino+loss-aversion deployment
objective with a theory-grounded single-parameter CRRA expected-utility
objective; add a CVaR diagnostic that is computed and logged but drives no
trade; ship the overfitting-accounting and provenance spine — and ship nothing
that touches a live exit decision.

Finalist A is a **complete, terminal-acceptable architecture** — not merely
"Phase 1." If the user adopts only Finalist A, that is a full and honest
delivery of the defensibility upgrade.

### 3.1 Scope — Phase 1 floor is M1 + M2

| Component | What it is | Live impact |
|---|---|---|
| **M1** | CRRA-EU autotuner objective replacing 5 loss-aversion constants. `gamma` pre-registered, frozen by theory, NOT Optuna-searched. Per-trial t-stat re-derived (S-2). | Offline (autotuner only). Changes which parameter set the autotuner *selects*; changes no exit logic. |
| **M2** | A 5% CVaR **operator-instrumentation diagnostic** (H-4 re-label — distinct from M1's defensibility win; **Phase-2 kill-switch only**, never a stepping-stone, per §3.9 W-H1) computed single-day from the kNN pool `run_monte_carlo` already builds (Rockafellar-Uryasev general-distribution estimator). Logged every cycle under the **four-part S-3 display contract** (§4). | **Zero decision impact, non-zero non-blocking I/O cost** (H-3 correction — M2 writes one `cvar_diagnostics` row per cycle via the H4 `live|replay` telemetry helper, benchmarked vs the minute budget). Drives no decision. A wrong M2 number misleads a human reading a dashboard; it never moves money — *which is why the S-3 bias warning is mandatory* (see below). |

**M2's display contract (S-3, binding — §4).** M2's diagnostic display must
carry **all four** of: (a) an uncertainty band / standard error; (b) the
genuine tail-observation count (~7-8); (c) an explicit label
*"diagnostic, not a signal — do not trade on this"*; (d) an explicit **bias
warning** — *"this CVaR estimate is a known-low-biased LOWER BOUND on tail
severity, not a point estimate."* Element (d) is load-bearing: small-sample
empirical CVaR on ~7-8 tail observations is biased toward *understating* the
tail; the standard error is a variance statement and does not warn of the
bias. An operator anchoring on a reassuring-looking number that is
systematically wrong in the dangerous direction is the actual harm M2 risks —
without (d) on the display, M2 is mildly harmful.

**M3 is NOT in the Phase-1 floor.** M3 (empirically re-derive the two layers
the code self-flags as having no literature provenance — the time-squeeze
decay curve `math_engine.py:88-94`, the VWAP System-A HWM gate `:601-606`) is a
genuine **live-exit-logic change** carrying a real test burden. It is
**Phase 1.5**, a recommended fast-follow on its own TDD cycle, shipped under
binding condition **S-1 (§4) — a TWO-STAGE parity gate**: Stage 1, the pre-M3
engine replays bit-identical to the current frozen reference (proves the
harness); Stage 2, the post-M3 engine is replayed and **every** divergent cycle
is individually attributed in a **committed per-cycle attribution table** to a
specific re-derived curve value, each divergence in the intended direction, and
the post-M3 output becomes the new committed frozen reference. "Explained
divergence" as prose fails Gate-1 (K-1); a per-cycle attribution table passes
it.

### 3.2 Fate of the 6 heuristics

All 6 retained, unchanged. HARDEN does not touch the exit-decision layers or
the priority resolver. M3 (Phase 1.5) *re-derives* — does not delete — two of
them. This is the honest meaning of HARDEN: it hardens the provenance of what
exists; it does not replace.

### 3.3 HARDEN's honest claim (critic condition 4 — the precise un-flattering version)

**The Phase-1 floor removes R3 only** (the hand-tuned loss-aversion multipliers — via M1; H-5 correction — the earlier "removes three" wording conflated Phase 1 with Phase 1.5). **R1 (time-squeeze curve) and R2 (VWAP System-A gate) are removed in Phase 1.5 via M3** — a recommended fast-follow, NOT the Phase-1 floor. HARDEN's total provenance closure across Phase 1 + Phase 1.5 is R1 + R2 + R3. In exchange it:

- **adds three specification facets** — the `gamma` value, the utility *family*
  (CRRA, chosen over CARA / skew-aware), and the **wealth argument** fed to
  CRRA — all frozen by theory/a-priori choice and pre-registration, **none** by
  backtest P&L (so each contributes `D_spec = 1`; the haircut's `S = 0`);
- **adds one new validated statistical component** — the CRRA one-sample
  t-stat `compute_crra_eu_tstat`, which must be golden-fixture tested;
- has `D_spec = 1` **conditional on the gamma sensitivity check** holding.

The honest claim is **not** "adds 1 facet." It is the four-part statement
above. The contrast with Finalist B still holds decisively: HARDEN = 3
theory-frozen facets; the full migration = 3 + 8-to-10 more, several
un-freezable cleanly.

### 3.4 Path generator / horizon / CVaR trigger

- **Path generator:** none. M2's CVaR is computed off the *existing* kNN
  single-day pool.
- **Horizon:** M2's CVaR is a **single-day** 5% CVaR — the only thing the
  existing pool supports natively. HARDEN does not claim a multi-day CVaR.
- **CVaR trigger:** none. M2 is a diagnostic; there is no horizon problem
  (M1's objective is evaluated once per candidate over a fixed fold; M2 is a
  read-only statistic).

### 3.5 Optuna / autotuner integration

- BHY haircut **preserved unchanged**; search space stays 6-D (gamma frozen,
  not added).
- The 60/20/20 train/validation/frozen-eval split is **preserved** — M1's
  CRRA-mean objective is a sample mean of a finite-on-the-floored-domain
  transform (the named `WEALTH_ARG_FLOOR > 0` on input `W` keeps `U` finite —
  H-1 correction; CRRA itself is unbounded below as `W → 0⁺`), **small-sample-
  estimable** on the ~4-5-day frozen fold (the standard error of a mean is
  merely *wide* at n≈5, not undefined). **No rolling k-fold needed.** This is
  H-3 PASS and it also closes the NN2 question: NN2's route-(a)/(b) dilemma
  only ever bit a CVaR-*valued* Optuna objective; no finalist uses one.
- The deployment objective changes; the per-trial t-stat is re-derived (S-2).
  Nothing else in the autotuner changes.

### 3.6 AI Advisor — 2 of 4 roles active in Phase 1

- **Specification Critic** — active. Polices 3 facets (gamma, utility family,
  wealth argument).
- **Overfitting Conscience** — active. The degree-of-freedom ledger records the
  frozen facet honestly from a clean start.
- **Shadow-mode Divergence Explainer** — minimal / Phase 1.5 (meaningful only
  once M2's shadow log exists).
- **Regime & Decision Narrator** — **Phase-2-conditional, structurally
  inapplicable at Phase 1.** It interprets gamma/lambda *drift*; HARDEN has no
  lambda and a single frozen non-drifting gamma. **Surfaced to the user**
  (escalation §6.3) — a role whose precondition does not exist yet, not a
  scope cut.

### 3.7 Persistence — Phase-1 footprint as a first-class cost (critic condition 7)

Per persistence-architect's converged migration plan
(`council-converged-migration-plan.md`, commit `bb0c480`). The Phase-1 schema
is **4 or 5 new state-DB tables + 2 additive ALTER migrations** (H-8 A2 correction
— single consistent count statement: 5 in the recommended `spec_bundles + spec_facets`
shape; 4 if the implementing team collapses `spec_facets` into a JSON column on
`spec_bundles` per the apparatus-sizing latitude documented below) — stated as
cost, not hidden, and **not** "1 column":

| Migration | Contents | Kind |
|---|---|---|
| `015_spec_bundles.sql` | `spec_bundles` + `spec_facets` | 2 new tables |
| `019_advisor_observations.sql` | `advisor_observations` (thin) | 1 new table |
| `020_researcher_dof_ledger.sql` | `researcher_dof_ledger` | 1 new table |
| `021_fold_role.sql` | `fold_role` + the structural wall (H-8 A1 correction — canonical filename has NO `_columns` suffix; a mismatch between the literal file on disk and the `_MIGRATION_FILES` list at `database.py` silently swallows the FileNotFoundError, leaves the migration un-applied, and re-attempts every startup) | 1 ALTER |
| `022_autotune_runs_eut.sql` | `autotune_runs` EUT audit columns | 1 ALTER |
| `023_cvar_diagnostics.sql` | `cvar_diagnostics` (M2's home) | 1 new table |

**Honest framing (skeptic / persistence-architect / tuning-architect agree):**
of the 6 Phase-1 migrations, **five are the overfitting-accounting and
provenance spine** — they are *not* migration overhead, they *are* the
defensibility deliverable the user's binding motivation asked for ("the user is
paying for provenance and getting provenance"). Only `cvar_diagnostics` is M2
runtime. There is no per-cycle decision row in the state DB (`bot_state` is a
single-row JSON blob; `exit_triggers` logs exits only) — so M2's diagnostic
needs its own small table; this is the honest minimum, not a complexity smuggle.
`021` (the frozen-eval wall) **is Phase 1** — the moment gamma is frozen, the
structural proof the freeze was clean must already exist; a wall retrofitted in
Phase 2 cannot certify a Phase-1 freeze.

All migrations additive-first, `NULLable + DEFAULT` (or fresh `CREATE TABLE`),
idempotent, appended to `_MIGRATION_FILES` in order, single-underscore naming
to match the codebase. All in the state DB — **zero optimization-DB
migrations.** The replay-determinism anchor count: Phase 1 = **1** anchor (M2's
CVaR off the `cycle_id`-seeded kNN pool); Phase 2 = 5. 1-vs-5 is itself
evidence Phase 1 is the safer build.

**Implementation hazards carried forward** (persistence-architect §6): H1 —
migration 022's columns must be dual-written to *both* the migration ALTER and
the `init_db()` `CREATE TABLE autotune_runs`, or fresh and upgraded DBs
diverge. H3 — the frozen-eval wall filter must be
`COALESCE(fold_role,'') != 'frozen_eval'` (a bare `!=` silently hides
train/validation rows). H4 — the telemetry write helper takes an explicit
`live|replay` mode (live swallows, replay raises); Gate-1 parity asserts
decision-content columns only. **The named non-decision parity-exclusion list
is exactly `id` (autoincrement primary key) and `ts_utc` (wall-clock insertion
timestamp); no other columns are excluded** (H-8 A3 correction — exclusion list
fully named, not under-specified).

**Phase-1 apparatus sizing (bounded team's-choice, not a council mandate).**
tuning-architect notes that for HARDEN's *single* tunable frozen facet (gamma),
the full `spec_bundles`/`spec_facets` table pair may be heavier than strictly
required. The council leaves the *spec-registry table count* to the
implementing team — **under one binding constraint (persistence-architect):**
whatever the team picks MUST be (a) an immutable persisted record with a freeze
timestamp, and (b) content-hashed. A named constant in source code satisfies
**neither** — a code edit changes it with no row-level provenance, no
`frozen_at`, no hash — and is therefore **NOT** an acceptable substitute; a
non-immutable, non-hashed registry leaves the Phase-1 Overfitting Conscience
with nothing auditable to point at, and the defensibility upgrade would be
hollow. The recommended shape is `spec_bundles` + `spec_facets`; the genuine
right-sizing latitude is "collapse `spec_facets` into a JSON column on
`spec_bundles` if 3 facets does not warrant a child table" — the team may drop
the child table, **never** the immutable hashed bundle. The frozen-eval wall
(`021` — `fold_role` + the `advisor_ro_query` `COALESCE` accessor + the
wall-breach tripwire) ships in Phase 1 in full, **not** team's-choice.

### 3.8 Validation gates

- **Gate 1 (backtest-replay parity).** HARDEN gets the strongest possible
  version: M2 changes no decision → a replay is **bit-identical** to the
  reference Guard-Alpha sequence by construction; M1 is offline and
  deterministic. A committed replay-parity test asserts the decision record
  bit-identical to a frozen reference (decision-content columns only).
- **Gate 2 (live shadow N-weeks-clean).** M2's diagnostic runs in shadow
  permanently (it takes no action). The pre-registered acceptance criterion is
  **diagnostic quality**, not trigger behavior: zero NaN/inf, the **full
  four-part S-3 display contract present** (stderr, tail-observation count, the
  "diagnostic, not a signal" label, AND the bias warning — §3.1, §4),
  reproducible under replay. **M2 does not need an ES-calibration backtest to
  be safe** — a wrong diagnostic misleads a human; it does not fire a trade —
  *provided* the S-3 bias warning is on the display so the human is not misled
  by a systematically low estimate.

The five RED golden-fixture tests in §8 are the verifiability spec for both
gates and for the binding conditions S-1/S-2/S-3 and W-H2 — they make the
floor's correctness conditions *verifiable*, not asserted. In particular the
new `compute_crra_eu_tstat` (S-2) is one of those five tests; §8 test 1 covers
it.

### 3.9 HARDEN's owned residuals (documented)

- **W-H1 — M2's evidentiary power is asymmetric** (owner: skeptic). M2 computes
  a *single-day* CVaR. Its evidentiary ceiling is **KILL-or-INCONCLUSIVE**: it
  can raise a gross red flag (single-day CVaR grossly uninformative → weak
  prior against the trigger) or be inconclusive — it **cannot deliver a clean
  "BUILD"** verdict, because a weak positive could be a single-day-horizon
  artifact. M2 starts the evidence chain; it does not end it.
- **W-H2 — the wealth argument into CRRA is unverified** (owner: skeptic /
  Phase-1 implementing team). M1 feeds CRRA a growth factor derived from
  guard-alpha; guard-alpha is a *difference*, not a wealth ratio. The correct
  wealth argument must be **derived, not assumed**, before M1 ships — currently
  an A-4 exposure, fixable. A Phase-1 design item with a golden fixture.
- **W-H3 — HARDEN does not satisfy the literal "replace" scope word.** It
  hardens; it does not replace. This is the explicit reason Finalist B exists.
  See escalation §6.1.

---

## 4. Finalist A is recommended SUBJECT TO three binding conditions (critic condition 3)

HARDEN is the council's recommendation **subject to** all three of the
following. These are binding conditions, not residual risks:

- **S-1 — M3's two-stage parity gate.** M3 (Phase 1.5) ships only with a
  two-stage parity gate: Stage 1 — the pre-M3 engine replays bit-identical to
  the current frozen reference; Stage 2 — the post-M3 replay enumerates **every
  divergent cycle in a committed per-cycle attribution table**, each divergence
  attributed to a specific re-derived curve value in the intended direction;
  the post-M3 output then becomes the new committed frozen reference. A prose
  summary fails K-1; the attribution table passes it.
- **S-2 — the re-derived t-stat.** The CE objective's per-trial BHY statistic
  is the genuine one-sample t-stat `t = mean(U)/(sd(U)/√T)` via a new
  `compute_crra_eu_tstat`, replacing `compute_sortino_tstat` for this
  objective. BHY step-up + Yekutieli c(N) machinery 100% preserved. This is new
  statistical machinery that must be golden-fixture validated.
- **S-3 — M2's four-part display contract.** M2's diagnostic display must carry
  **all four** of: (a) an uncertainty band / standard error; (b) the genuine
  tail-observation count (~7-8); (c) an explicit label *"diagnostic, not a
  signal — do not trade on this"*; (d) an explicit **bias warning** — *"this
  CVaR estimate is a known-low-biased LOWER BOUND on tail severity, not a point
  estimate."* Element (d) is load-bearing — small-sample empirical CVaR on
  ~7-8 tail observations is biased toward *understating* the tail, and the
  standard error (a variance statement) does not warn of that bias. Without
  (c) and (d) M2 manufactures false comfort and is mildly harmful.

**M1 and M2 are conditional deliverables, not free** (critic condition 4): M1
is conditional on the W-H2 wealth-argument derivation; M2 is conditional on the
S-3 display contract.

---

## 5. FINALIST B — Phased Replace (presented honestly, not recommended)

**One sentence.** Finalist A as Phase 1; then, only if four named preconditions
all pass, a Phase 2 that adds a forward-path simulator and a CVaR **co-signal**
— never a sole trigger — built on critic's code-soundness spine carrying
risk-architect's risk-math decisions.

### 5.1 Phase 2 is honestly conditional — it may never unlock (critic condition 6)

**Phase 2 is authorized IF AND ONLY IF four preconditions ALL pass:**

- **(a) M2 evidence — KILL-or-INCONCLUSIVE ceiling.** Per W-H1, M2 cannot
  deliver a clean "BUILD." The honest precondition is: M2 does **not** show
  gross uninformativeness **AND** a separately-powered discriminating test
  becomes constructible. "M2 shows incremental signal" is **not** an
  achievable gate — M2's ceiling is kill-or-inconclusive.
- **(b) Gate-zero tail-data audit.** A data audit (phase0 OQ-1) must confirm
  AlphaBot's history yields enough genuine sub-5% tail observations *per regime
  cluster* to power *some* discriminating validation. If stressed regime
  buckets are thin, the CVaR core would abstain exactly when it must work. This
  audit can kill Phase 2 outright.
- **(c) Latency + bucket arithmetic (the deferred B-2/B-3 gates).** A measured
  prototype must prove the Tier-1 pre-open batch finishes with margin before
  the first cycle, AND the regime buckets are populous enough to simulate from.
  **If the latency arithmetic does not clear or the buckets are too thin,
  Phase 2 does not proceed and the system stays at Finalist A permanently.**
- **(d) A powered validation design exists** — OR the trigger ships
  diagnostic-grade-permanent (never as a calibrated budget).

**Per §2.3, precondition (d) may be structurally unsatisfiable** — the
~1,000-tail-observation requirement is a decade-plus of data away. **The
synthesis states plainly: Phase 2 may never unlock, and stopping permanently at
Finalist A is a full success, not a project failure.** If Phase 2 proceeds at
all, a live CVaR layer may be permanently un-validatable as a calibrated ES
model — in which case its honest output is a diagnostic-grade trigger with
conservative hysteresis, NOT a calibrated CVaR budget.

### 5.2 Phase 2 endpoint — a CVaR CO-SIGNAL, not a trigger

Because a live CVaR *trigger* is un-validatable at this data scale, Phase 2's
endpoint is **CVaR as a co-signal** — an input that can **narrow, veto, or
confirm** an exit another layer already supports, but that **never solely fires
an exit.** A co-signal needs far less validation power: you validate "does CVaR
agreement improve an exit the engine was already going to make," not "is CVaR's
standalone tail estimate calibrated to 5%." The former is testable on
AlphaBot's data; the latter is not.

### 5.3 Phase 2 design (critic's soundness spine + risk-architect's risk math)

Carried for completeness so the user sees what Finalist B would buy:

- **Path generator:** regime-conditioned stationary **block bootstrap**, a
  **net-new** function `simulate_forward_paths` — `run_monte_carlo` frozen and
  untouched until the last symphony cuts over (zero blast radius). Block length
  frozen by the Politis-White automatic selector (NN1-compliant — independent
  of strategy P&L). Preserves volatility clustering. Accepts the
  historical-worst-case ceiling.
- **CVaR computation:** Rockafellar-Uryasev general-distribution estimator;
  `CVaRAssessment` frozen typed object — `cvar_pct: float|None`, `breach`,
  `tail_obs_count`, `insufficient_reason`. `None` = out-of-band insufficient
  sentinel mirroring MC's `None`; `breach` always `False` when `None`
  (fail-safe; the heuristic floor still protects).
- **Horizon:** an open trade-off escalated to the PM (§6.2) — Family B
  (interpretable, time-inconsistent) or Family C (time-consistent,
  un-interpretable effective alpha). Whichever is chosen ships a disclosed,
  owned residual.
- **Composition:** the CVaR co-signal enters the **existing**
  `resolve_trigger_priority` as one additional boolean — the resolver kept and
  extended, never replaced, never collapsed into a single condition. The 6
  heuristics are retained as a **permanent safety floor.**
- **CVaR trigger design:** a two-level hysteresis band + multi-tick
  confirmation state machine, a sibling of `compute_exit_confirmation`. Abstains
  fail-safe when the ensemble is unavailable. Operates as a **co-signal.**
- **Expected utility:** EUT enters as the `gamma` risk-aversion *shaping* of
  the CVaR budget — there is **no separate `E[U(exit)]` vs `E[U(hold)]`
  crossover layer** (a soft objective arbitrated by a boolean resolver is a
  category mismatch). This is an honest narrowing of the 4-primitive pitch.
- **`lambda` frozen by mandate, NOT Optuna-searched** — a searched lambda
  compared against a regime-drifting effective alpha is optimization against a
  non-stationary objective, which the BHY haircut cannot correct. **The system
  is therefore honestly ONE tuned parameter — gamma — not two.**
- **Execution path:** a two-tier compute split — heavy out-of-band pre-open
  batch + light in-band per-minute array reduction. The path bank lives in a
  **file cache** with a state-DB `path_bank_manifest` metadata row (not a
  40 MB blob in the WAL DB). The Tier-1 bootstrap is seeded deterministically:
  `tier1_seed = SHA-256(symphony_id ‖ trading_day ‖ spec_bundle_hash)`,
  persisted in the manifest — load-bearing for Gate 1 (without it, replay
  parity cannot pass).
- **AI Advisor:** all 4 roles, read-only, walled off from the frozen-eval fold
  by the structural accessor filter + the tripwire (rubric M-1). The accessor
  filter must be `WHERE COALESCE(fold_role,'') != 'frozen_eval'` — a bare `!=`
  silently passes untagged (NULL) rows through the wall. The Regime & Decision
  Narrator becomes applicable in Phase 2.

### 5.4 Phase 2 persistence (deferred schema)

Migrations `016_shadow_decisions.sql`, `017_path_generator.sql`
(`path_generator_calibrations` + `path_bank_manifest`),
`018_decision_core_state.sql` — 4 heavy runtime-state tables — ship **only if
Phase 2 unlocks.** `shadow_decisions.spec_bundle_id` and `mc_seed` are
`NOT NULL` (a shadow decision with no spec bundle is unreplayable — a
correctness defect; safe because it is a fresh `CREATE TABLE`). The legacy
engine and its tables are retained through a 20-trading-day post-cutover
inverted-shadow window; the legacy drop is human-operator-authorized only
(persistence-architect H6).

### 5.5 Phase 2 validation gates

- **Gate 1:** bit-identical Guard-Alpha replay under a fixed seed — possible
  only because every new function is pure, the generator seeds from
  `tier1_seed`, and no global RNG or wall-clock enters the math. A named,
  committed replay-parity test.
- **Gate 2:** pre-registered divergence metric, minimum run length, acceptance
  threshold. The CVaR **co-signal** is credited by "does CVaR agreement improve
  an exit the engine already supports" — NOT by a standalone joint VaR-ES
  calibration backtest (underpowered at this data scale). A failed gate's data
  is burned, never recycled as selection input.

### 5.6 Finalist B's full residual ledger (critic condition 8 — every R-1..R-8 recorded)

So the user sees exactly what the richer migration costs:

- **R-1** — Finalist B is **more complex than the incumbent**, not less, during
  and after Phase 2 (6 heuristics + a CVaR resolver signal). The "2-parameter
  simplicity" of the pitch never materializes; the honest tuned-parameter count
  is **one** (gamma).
- **R-2** — the iterated-CVaR horizon (if Family C is chosen) makes the
  flagship "5%" risk number un-interpretable; Family B keeps interpretability
  but is time-inconsistent. No clean answer (§2.4).
- **R-3** — a pre-simulated path bank is structurally blind to an
  intraday-developing tail regime; a per-minute fresh simulation cannot run
  non-blocking. The per-minute-simulation premise is not fully deliverable.
- **R-4** — thin stressed regime buckets may make the CVaR core abstain
  exactly when it matters most — the gate-zero data audit (precondition b) can
  kill Phase 2 on this.
- **R-5** — no real `E[U(exit)]` vs `E[U(hold)]` crossover ships; "EUT+CVaR"
  becomes **"CVaR-with-risk-aversion-shaping"** in every buildable candidate.
  A shared, honest finding — the literal 4-primitive pitch is narrowed.
- **R-6** — a hand-set conservatism adjustment for the block-bootstrap
  historical-worst ceiling would re-introduce an ad-hoc heuristic of the kind
  the migration was meant to eliminate.
- **R-7** — regime bucketing is kNN with hand-drawn boundaries; the boundary
  thresholds are ~4-6 additional un-validated specification facets.
- **R-8** — a live CVaR trigger is un-backtestable at AlphaBot's data scale —
  structural, the reason the Phase-2 endpoint is a co-signal, not a trigger.

### 5.7 Phase-2 entry gates (deferred, owned, blocking — critic's B-2/B-3 ruling)

The B-2/B-3 latency-and-bucket arithmetic is **not** design-doc hand-waving —
it is **named, owned, blocking Phase-2 entry work.** The explicit consequence:
**if the latency arithmetic does not clear or the regime buckets are too thin
to populate, Phase 2 does not proceed and the system stays at Finalist A
permanently.** A deferred gate is honest only because "the gate fails → we
stop" is written down here.

---

## 6. Items requiring a user decision (escalations)

The council cannot resolve these — they are the user's call and must be put to
the user before any implementation cycle begins.

1. **"Replace, phased" reinterpreted.** The binding scope word was "replace."
   The council's evidence-based finding is that a literal replacement of the
   exit core with a CVaR trigger is un-validatable at AlphaBot's data scale.
   Both finalists deliver "harden now; replace-as-co-signal later only if the
   evidence earns it." **The user must confirm this satisfies their intent, or
   correct the council.**
2. **The horizon convention is an unresolved trade-off.** A time-consistent
   per-minute CVaR is not the literal "5% of end-of-horizon P&L"; a literal 5%
   end-of-horizon CVaR re-run per minute is time-inconsistent and oscillates
   (phase0 §3.5 impossibility). If Finalist B's Phase 2 ever proceeds, the user
   must choose the horizon convention knowing each option ships a disclosed
   residual. **The user must accept this reframing of the "5% CVaR budget."**
3. **The AI Advisor's Regime & Decision Narrator role is Phase-2-conditional.**
   It interprets gamma/lambda *drift*; Finalist A has no drift to narrate.
   **The user should accept that role 3 of 4 is a Phase-2 deliverable** — a
   role whose precondition does not exist until Phase 2, not a scope cut.
4. **"EUT+CVaR" ships as "CVaR-with-risk-aversion-shaping."** No buildable
   candidate ships the literal `E[U(exit)]` vs `E[U(hold)]` per-minute
   crossover as a live decision rule. EUT enters as the CRRA objective
   (Finalist A) and as gamma-shaping of the CVaR budget (Finalist B Phase 2).
   **The user should know the literal 4-primitive pitch is narrowed.**

---

## 7. Open items — non-blocking, owned

- Exact treatment of a spec candidate that received its own sub-sweep in the
  `N_effective` formula — the conservative-upper-bound framing covers it; owner:
  tuning-architect. Not blocking.
- The wealth argument into CRRA (W-H2) — a Phase-1 design task with a golden
  fixture; owner: skeptic / the Phase-1 implementing team.
- The pre-open batch latency budget — a Phase-2 entry gate requiring a measured
  prototype; owner: the Phase-2 implementing team. Not blocking Phase 1.
- The Phase-2 path-generator family — the schema is generator-agnostic
  (`calibration_params` is a JSON blob); chosen at Phase-2 design time under NN1.
- Whether the LLM-authored advisor roles ship in Phase 1 or Phase 2 — the
  `advisor_observations` table is Phase 1 regardless; only the LLM authorship
  is open.

---

## 8. RED golden-fixture regression spec (for the Phase-1 implementing team)

The debate specified these as the verifiability spec for HARDEN-core's binding
conditions — they **PIN** the formulas and wiring for S-2 / W-H2 / S-3 /
replay-determinism (H-7 correction — "PINS" the implementation, **not**
"verifies" or "validates" the statistical correctness of the underlying methods;
these tests confirm the code matches the specified formula, not that the
method is statistically correct on its own merits):

1. The CRRA t-stat test — **pins the formula (wiring), does not validate the
   statistic.** A known `U`-series with `sd(U) ≠ 1`; assert
   `t == mean(U)/(sd(U)/√T)` AND `t ≠ effect_size·√T`. Discriminating power:
   the two formulas diverge exactly when `sd(U) ≠ 1`, so the test fails if
   the implementation silently reuses the Sortino effect-size form (H-6
   category error).
2. The M1 wealth-argument test — once W-H2's argument is derived.
3. M2's CVaR-on-a-known-pool golden fixture + an assertion the bias warning is
   present on the display surface.
4. The one-anchor replay-determinism test — the same cycle run twice yields
   bit-identical `cvar_5pct`.
5. The S-1 two-stage parity gate with the committed per-cycle attribution
   table — when M3 ships at Phase 1.5.

---

## 9. Attack-rubric status

critic's attack rubric (v2, 26 gates, 13 families — Family M added for the
structural Advisor wall M-1 and persisted-artifact seed determinism M-2) is the
judging standard. Per critic's refined L-1 ruling, a richer finalist passes L-1
at design time on the **deficiency-closure** standard — by demonstrating it
closes a documented deficiency Finalist A leaves open with minimum complexity;
it does not need a deployed experiment. Finalist B's deficiency-closure claim:
Finalist A's M2 is diagnostic-only and cannot *act* on a tail event the
heuristic floor under-catches — *if* M2 data shows such events recur, a
diagnostic that watches without acting is a documented protective gap. That
claim is design-time-checkable but **contingent on M2 data**, which is why
Finalist B's Phase 2 is evidence-gated, not pre-committed.

Every conceded weakness in this synthesis is a documented WEAK with a named
owner and a mitigation — none is an undocumented WEAK. No load-bearing (★) gate
is left in an unfixably-FAILed state for either finalist: Finalist A passes
every gate it is in scope for (B-2/B-3 are N/A — it deploys nothing requiring
the latency arithmetic); Finalist B's Phase-2 FAILs (B-2, B-3, K-2) are
converted to explicitly-deferred, owned, blocking Phase-2 entry gates with the
"gate fails → we stop" consequence written down (§5.7, §5.1).

---

## 10. The 9 binding conditions — compliance index

This synthesis was drafted to carry critic's 9 binding conditions. For the
gate review:

1. **HARDEN is a scope challenge** — stated as the headline ("THE HEADLINE")
   and §1, unburied.
2. **Two finalists, not three** — §0, §5; Finalist C explicitly ruled out
   (folded into Finalist B's Phase-1/Phase-2 framing as the user's decision
   point).
3. **S-1, S-2, S-3 as binding conditions on HARDEN** — §4, "recommended
   SUBJECT TO."
4. **M1/M2 conditional, honest claim precise** — §3.3 (four-part claim), §4
   (M1↔W-H2, M2↔S-3).
5. **Horizon convention an unresolved trade-off escalated to the PM** — §2.4,
   §6.2.
6. **Phase 2 may never unlock — acceptable terminal outcome** — §0, §5.1
   (four preconditions, gate-α reframed KILL-or-INCONCLUSIVE, R-8 stated).
7. **Persistence footprint a first-class cost line** — §3.7 (5 tables + 2
   ALTERs, hazards H1/H3/H4, the count-plus-weight framing).
8. **Every R-1..R-8 recorded against Finalist B** — §5.6.
9. **NN1 verbatim + tripwire reframing** — §2.5.

Plus the "what each candidate contributed" honest record — §2.6.

**Gate status:** `critic` re-read the full synthesis and explicitly **confirmed
the CONVERGE gate PASSED** — all 9 binding conditions verified carried (checked
against the body, not just the index), all three scrutiny-pass gaps closed, no
★ load-bearing gate left unfixably FAILed for either finalist. `tuning-architect`,
`persistence-architect`, and `skeptic` each signed off their lenses. The
synthesis is cleared for delivery to the PM.
