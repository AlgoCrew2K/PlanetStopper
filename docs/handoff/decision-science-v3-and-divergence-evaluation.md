# Decision-Science Review — Evaluation of the v3 Synthesis and the CVaR-Divergence Idea

**Owner:** risk-architect (risk-engine-specialist), review-council coordinator
**Council:** decision-science-review — risk-architect, tuning-architect, persistence-architect, skeptic, critic
**Date:** 2026-05-22
**Branch:** design/decision-science-council
**Status:** CONVERGED — critic's G1–G8 CONVERGE gate PASSED; delivered to the PM (team-lead)

---

## THE HEADLINE — read this first

This council had two jobs: (1) adversarially evaluate the committed "v3"
synthesis (`decision-science-council-synthesis.md`), and (2) scrutinise the
user's "CVaR divergence signal" idea against v3. After independent review from
five lenses and an exhaustive adversarial debate, the converged answers are:

1. **v3 is SOUND.** Its verdict — "harden, don't migrate; a live CVaR trigger
   cannot be validated at AlphaBot's data scale" — and its two-finalist
   structure survive attack from every lens. The decisive
   ~1,000-tail-observation validation wall holds. v3's core verdict is **not
   overturned.** The council found **nine holes** — none fatal; they are
   corrections and disclosures that make v3 honest and implementable, not
   reasons to reject it.

2. **The CVaR divergence idea is REJECTED** as a validated regime-shift
   detector, as a Phase-2 co-signal, and as a live trigger. It does not
   sidestep the validation wall — it relocates it onto an equally-thin,
   correlated count of regime-shift events. The only surviving residue is
   operator-optional and unlocks no architecture (§B).

The user asked whether the divergence idea escapes v3's decisive validation
wall. The honest answer is **no** — but the user correctly identified one real
weakness in v3's M2 (a CVaR number with no temporal reference frame), and the
fix for that weakness is cheap, safe, and already inside Finalist A.

---

## 0. BLUF

- **On v3:** SOUND. Adopt "harden, don't migrate" and the two-finalist
  structure. Eight holes (§A) must be carried as corrections/disclosures
  before v3 is handed to an implementing team; none changes the verdict.
- **On the divergence idea:** REJECT as a validated detector / co-signal /
  trigger. The surviving residue — an operator may compute M2 over a second,
  longer window, each window read independently under its own S-3 contract,
  **with no signed-divergence quantity ever surfaced** — earns no new
  architecture, no new validation, and unlocks no finalist (§B).

---

## A. EVALUATION OF v3 — SOUND, WITH NINE HOLES

### A.0 What survived attack (the load-bearing claims that hold)

The council attacked v3's spine from all five lenses and could not break it:

- **The decisive finding (§2.3) is risk-math-correct.** A live CVaR *trigger*
  cannot be validated at AlphaBot's data scale. Expected Shortfall is not
  standalone-elicitable (Fissler-Ziegel 2016); the only honest validator is a
  joint VaR-ES coverage backtest (Acerbi-Székely class), which needs ~1,000
  tail-relevant observations (Yamai-Yoshiba). AlphaBot accrues ~6 tail days per
  125-day fold, ~37 per 3 years. Confirmed against `math_engine.py:705-833`:
  `run_monte_carlo` is a single-day i.i.d. resampler with no time axis — each
  "path" is one `rng.choice` draw from a kNN pool (`math_engine.py:828-829`).
  A multi-day CVaR is genuinely net-new. v3's claim that a live CVaR trigger
  cannot be validated is correct and correctly decisive.
- **S-2 (the re-derived t-stat) is correct and necessary.** Verified against
  `autotuner.py:289-301`: `compute_sortino_tstat` returns `sortino * sqrt(T)`.
  A Sortino ratio is already a mean/dispersion *ratio*, so `ratio·√T` is a
  genuine per-trial t-stat. A CRRA-EU objective is `mean(u(g_i))` — a bare
  *mean*, not a ratio — and its genuine significance statistic is the
  one-sample t-stat `t = mean(U)/(sd(U)/√T)`. Reusing `compute_sortino_tstat`
  for a CRRA objective would be the exact "H-6 category error" the code already
  fixed once (comment at `autotuner.py:266-271`). v3 catching this is correct.
- **The additive `N_effective = N_optuna + S` accounting is sound** and
  deliberately conservative — a haircut on a conservative upper bound errs safe
  (it can reject a genuine signal, never pass a spurious one). The
  multiplicative `N_optuna × D_spec` retraction was correct (it punished trials
  never run).
- **NN1 (the spec-freeze gate) is correct and load-bearing.** Verified against
  the BHY machinery: the Yekutieli `c(N) = Σ 1/j` factor
  (`autotuner.py:344-345`, the N-th harmonic number) corrects multiple-testing
  **only over the Optuna trial search `benjamini_hochberg_adjust` can see**. A
  spec facet chosen by looking at strategy P&L is an uncounted testing event;
  a P&L-frozen generator makes the haircut understate its effective N. NN1 is
  the precondition that keeps the haircut **true**, not a methodology
  preference.
- **The two-finalist / no-third structure is honest.** Finalist A and B
  genuinely share Phase 1; Phase 2 is honestly evidence-gated and may never
  unlock.

None of the nine holes below overturns any of the above.

### A.1 Hole H-1 (RISK-MATH CORRECTNESS) — CRRA is unbounded below; "bounded" is false; the haircut can be NaN-poisoned

**Owner:** skeptic. **New residual: W-H4.**

v3 §2.1, §3.3, §3.5, and S-2 describe the CRRA-transformed series `U` as
"bounded." **This is mathematically false.** CRRA utility
`u(W) = W^(1-gamma)/(1-gamma)` is **unbounded below** as `W → 0+` for
`gamma ≥ 1`: for `gamma > 1` the exponent `(1-gamma) < 0` so `W^(1-gamma) → +∞`
and `u → -∞`; at `gamma = 1` (log utility) `u(W) = ln(W) → -∞`.

W-H2 already flags that the wealth argument fed to CRRA is underived
(guard-alpha is a *difference*, not a wealth ratio). If that argument can
approach 0 — which a near-total-loss fold day legitimately produces — then a
single fold day sends `U_i → -∞`, which makes both `mean(U)` and `sd(U)`
non-finite. The downstream failure is concrete and silent:

- a non-finite `U` poisons `mean(U)` and `sd(U)`;
- `compute_haircut_pvalue`'s `erf` clamp (`autotuner.py:314`) cannot rescue a
  NaN — it only clamps a *finite* extreme;
- the NaN propagates through `benjamini_hochberg_adjust`'s running-min
  (`autotuner.py:349-354`) and silently breaks the entire haircut;
- a NaN-poisoned haircut is not bit-reproducible, so Gate-1 replay parity also
  fails.

This is a **binding correctness defect**, not a residual — it is the exact
NaN/inf propagation the risk-engine charter forbids.

**The fix (binding):** the wealth argument `W` fed to CRRA must be floored by a
**named module-scope constant** (e.g. `WEALTH_ARG_FLOOR > 0`), source-commented
per the project no-magic-numbers rule, derived as part of W-H2 before M1 ships;
**or** `gamma` is pre-registered `< 1` so CRRA is bounded below by construction.
The floor goes on the **input wealth argument `W`, never on the output utility
`U`** — flooring `U` directly compresses the lower tail of the `U`-series,
artificially shrinks `sd(U)`, and *inflates* the t-stat `mean(U)/(sd(U)/√T)`,
re-introducing an anti-conservative bias into the haircut. Flooring `W` keeps
`u(·)` monotone and continuous, so `sd(U)` remains the honest dispersion of a
series with a finite worst case.

The word "bounded" must be **struck** everywhere it appears in v3, or made
*true* by the named floor. `§8 test 1` (the CRRA t-stat fixture) gains a
near-floor-wealth sub-case asserting the returned t-stat is **finite**, `sd(U)`
is finite, and the floor was applied to `W` (not to `U`).

This is a new entry in the residual ledger — **W-H4** — and it is distinct
from W-H2: W-H2 is a *correctness-of-input* question ("what wealth argument is
the right thing to feed CRRA"); W-H4 is a *numerical-stability* question that
survives a perfect W-H2 resolution (a correctly-derived wealth ratio can still
legitimately approach 0).

### A.2 Hole H-2 (RISK-MATH CORRECTNESS) — M2's S-3 stderr must use the distinct-tail-observation count, not the resample count

**Owner:** skeptic / risk-architect. **Tightens S-3.**

S-3 element (a) requires M2's diagnostic display to carry "an uncertainty band
/ standard error." v3 does not state **which N** that standard error is
computed over. It must be the count of **genuine distinct tail observations**,
not the resample count.

`run_monte_carlo` draws `simulation_paths` (default 5000) samples *with
replacement* (`rng.choice(nearest_day_returns, size=simulation_paths)`,
`math_engine.py:828-829`) from a kNN pool of at most ~150 neighbour-days. At the
5% tail that pool contains only ~7-8 **distinct** sub-5% neighbour-day returns.
The 5000 resampled draws reduce *resampling* noise; they add **zero**
*estimation* information beyond those ~7-8 distinct tail observations (phase0
§2.2 states this explicitly).

A standard error naively computed on the 5000 resampled draws would understate
the true estimation error by a factor of roughly `√(5000/7) ≈ 27×`. That
converts S-3's honesty mechanism into a false-precision generator — manufacturing
exactly the comfort S-3 element (d) exists to prevent.

**The fix (binding):** S-3 element (a)'s standard error is computed on the
**distinct genuine tail-observation count** (~7-8). `§8 test 3` (M2's
CVaR-on-a-known-pool fixture) gains an assertion using a fixture where the
resample count and the distinct-tail count differ, asserting the displayed
stderr is within tolerance of the small-sample (n≈7) value and **not** within
tolerance of the n≈5000 value. The persisted `cvar_n_tail` column is the
auditable denominator — it is a first-class column precisely so a reviewer can
confirm the stderr was not computed on the resample count.

### A.3 Hole H-3 (EXECUTION-PATH SAFETY) — M2's per-cycle write is a bounded I/O cost, not "zero impact"

**Owner:** persistence-architect / risk-architect.

v3 §3.1 and §3.7 frame M2 as "zero" live impact. This is imprecise. M2 is zero
*decision* impact — it drives no trade — but it writes one `cvar_diagnostics`
row **every cycle**, and the execution path runs a 1-minute cadence under the
project's hard "no blocking I/O on the execution path" rule (project CLAUDE.md
architecture constraint 1). A per-cycle `INSERT` is an execution-path side
effect.

This is a **wording defect, not an architecture hazard.** The per-cycle
telemetry-write pattern already exists and is accepted: `record_shadow_observation`
(`database.py:1147-1194`) already writes one row every cycle on a self-opened
connection with a swallowed exception, off the `save_state` transaction. M2's
`cvar_diagnostics` write is the same object. A separate table is the correct
choice — the state DB has **no existing per-cycle decision row** to add a column
to (`bot_state` is a single-row JSON blob; `exit_triggers` logs exits only), and
a separate append-only table keeps M2's write off the live `save_state`
transaction so a telemetry failure cannot fail a cycle.

**The fix (binding):** v3 must (a) replace unqualified "zero impact" with
**"zero decision impact, non-zero non-blocking I/O cost"**; (b) route M2's write
through persistence-architect's H4 `live | replay`-mode telemetry helper (live
swallows on failure, replay raises) — H4 covers the live per-cycle write, not
only the Gate-1 parity exclusion; (c) carry an explicit benchmark obligation —
M2's writer benchmarked against the minute budget. The benchmark is a
fast-follow verification, not a blocker: `record_shadow_observation` already
demonstrates a per-cycle single-row INSERT fits the budget. Architecture-constraint-1
compliance must be asserted in the design.

### A.4 Hole H-4 (HONEST FRAMING) — M2 is operator instrumentation, not a Phase-2 stepping-stone

**Owner:** skeptic (W-H1) / risk-architect.

v3 §3.1's floor table presents M1 and M2 as **co-equal** Phase-1-floor
deliverables. They are not co-equal in merit, and they answer **two distinct
user motivations** — conflating them under one "floor" label is a defect.

- **M1 is the defensibility win.** It replaces the five hand-tuned
  loss-aversion multipliers (`autotuner.py:94-114`, residual R3) with one
  pre-registered theory-frozen `gamma`. It is offline, deterministic,
  bit-identical-replayable, and validatable. It answers the user's binding
  motivation: a methodology / **defensibility** upgrade.
- **M2 is operator instrumentation.** It answers a *different* concern — that
  the operator should not fly blind on tail risk. M2's value is operator
  situational awareness, which is a **terminal deliverable**, not scaffolding.

**The council debated and rejected demoting M2 out of the Phase-1 floor.** The
reasoning: M2's value as instrumentation is not negative. The harm case for
demotion rests on operator anchoring (an operator anchors on a reassuring,
systematically-too-mild number) — but that harm is exactly what the binding S-3
element (d) bias warning neutralises. With S-3 (d) present, the operator is told
the number is a known-low-biased *lower bound*. Conversely, an operator with no
tail-severity instrument cannot even tell they are in a data-starved regime —
M2's `n_tail = 7` display (S-3 element b) is itself the "you are tail-data-starved
right now" warning. And from the execution-path-safety lens, instrumentation
should ship *with* the thing it instruments: if M2 dropped to optional/Phase-1.5
and that slipped, the operator would have no tail read for an indefinite period
while the live engine kept exiting.

**The fix (binding):** do **not** demote M2. **Re-label** it within Phase 1:
M1 = the defensibility win; M2 = operator instrumentation. Both are Phase 1;
they answer different motivations. v3's error is the co-equal framing, and the
fix is two labels under one phase.

**Separately — and this correction stands regardless of the re-label:** M2 is a
**Phase-2 kill-switch, never a Phase-2 stepping-stone.** M2 computes a
*single-day* CVaR; it structurally cannot produce evidence that a *multi-day*
CVaR co-signal would work, because it does not compute a multi-day quantity. v3
§3.9 W-H1 correctly states M2's evidentiary ceiling is KILL-or-INCONCLUSIVE —
but v3 §5.1 precondition (a)'s prose implies M2 "starts the evidence chain"
toward Phase 2. That is an oversell. The honest statement: M2 can raise a gross
kill flag against Phase 2; it can never advance it. Finalist B precondition (a)
must rest entirely on **"a separately-powered discriminating test becomes
constructible"**, with M2's role limited to "can raise a gross kill flag." This
tightens v3 §5.1(a); it does not overturn it.

### A.5 Hole H-5 (HONEST FRAMING) — §3.3 "removes three provenance gaps" overstates the Phase-1 floor

**Owner:** skeptic.

v3 §3.3 claims HARDEN "removes the three documented provenance gaps" the code
self-flags (R1 time-squeeze curve, R2 VWAP System-A gate, R3 loss-aversion
multipliers). But R1 and R2 are removed **only by M3**, and M3 is explicitly
**not in the Phase-1 floor** — v3 §3.1 places M3 in Phase 1.5. The Phase-1 floor
as defined removes **one** gap: R3, via M1.

This is a real mis-statement: §3.3 says "removes three"; §3.1 ships one.

**The fix:** v3 §3.3 must read "the Phase-1 floor removes R3 (the hand-tuned
loss-aversion multipliers) via M1; R1 and R2 removal is the Phase-1.5
recommendation, contingent on M3 shipping under the S-1 two-stage parity gate."
Not fatal — but the synthesis must not claim the floor does what only the
fast-follow does.

### A.6 Hole H-6 (RISK-MATH DISCLOSURE) — the S-2 √T t-stat inherits a serial-correlation anti-conservatism

**Owner:** skeptic. **New residual: W-H5.**

S-2's one-sample t-stat `t = mean(U)/(sd(U)/√T)` assumes the `T` observations
are **independent**. Guard-alpha days within a 125-day fold are **not
independent** — overlapping 20-day volatility regimes and autocorrelated squared
returns (phase0 §1.6) induce serial dependence. The autotuner's purge/embargo
(`PURGE_DAYS = 20`, `EMBARGO_DAYS = 1`; `autotuner.py:129`, `:147`) is sized for
*fold-boundary* leakage, **not** for within-fold serial correlation of the daily
series. A `√T` on serially-correlated `U` therefore **overstates significance** —
the effective sample size is `< T`.

Two things must be stated honestly:

1. **S-2 fixes the H-6 metric *category* error** — a mean needs
   `mean/(sd/√T)`, not `effect_size·√T`; the statistic must match the
   functional. That is the in-scope fix.
2. **S-2 does NOT fix the serial-correlation exposure.** This exposure is
   **inherited unchanged from the incumbent `compute_sortino_tstat`** —
   `sortino·√T` carries the identical latent flaw — so S-2 is **not a
   regression**. But v3 presents the re-derived t-stat as *the fix* for the
   t-stat while silently inheriting an independence assumption the data
   violates. A monotone (pointwise) transform preserves serial dependence; the
   bounded CRRA transform does **not** remedy it.

**Characterisation (carried verbatim, not used to downgrade):** the
serial-correlation inflation is roughly common-mode across trials, so the BHY
*selection* step (argmin `p_adj`) is less distorted than the *absolute*
significance level — but the absolute `p_adj` vs `HARVEY_LIU_FDR_Q` gate **is**
affected, so a borderline-noise trial set could clear a gate it should not.
This is a characterisation of the residual, **not** grounds to treat it as
benign.

**Disposition:** disclose-and-accept at HARDEN Phase 1 scale. The named
remediation path — a HAC / Newey-West standard error or a `T_eff` effective-sample
correction — is logged as a **future workstream, explicitly out of scope for
HARDEN Phase 1**: closing it for the CRRA t-stat without also closing it for the
incumbent `compute_sortino_tstat` would be incoherent. At the ~5-day frozen-fold
scale the lag-1 autocorrelation `ρ` is itself unestimable, so a `T_eff`
correction is not even constructible there — that is the honest reason the
remediation is deferred, and it must be stated. A documentation fixture (a known
`U`-series with injected lag-1 autocorrelation, asserting plain-`√T` is used and
is therefore *known* anti-conservative) makes the residual visible rather than
asserted.

This is a new entry in the residual ledger — **W-H5**.

### A.7 Hole H-7 (DOCUMENTATION PRECISION) — §8 test 1 PINS the S-2 formula; it does not VALIDATE the statistic

**Owner:** tuning-architect / critic.

v3 §8 describes its five RED fixtures as making the binding conditions
"verifiable" and says test 1 "covers" S-2. The verb must be precise: `§8 test 1`
asserts `t == mean(U)/(sd(U)/√T)` AND `t ≠ effect_size·√T` (plus the W-H4
near-floor-wealth finite-t sub-case). That **pins** the formula — it proves the
CRRA t-stat is wired as specified and is not silently the old Sortino form. It
does **not validate** the statistic: a unit test cannot discharge the
methodology claim that `√T` is the correct denominator under the data's
dependence structure (that is the W-H5 residual).

**The fix:** v3 §8's language for test 1 must read "pins the S-2 formula
(wiring)," not "verifies" or "validates" S-2. A regression pin and a methodology
validation are different objects; the document must not claim the former is the
latter.

### A.8 Hole H-8 (DOCUMENTATION / AUDIT TRAIL) — v3 internal-consistency drafting defects

**Owner:** persistence-architect.

These are defects in v3's **write-up**, not its reasoning. v3's reasoning is
sound; v3 *as drafted* contains three internal-consistency defects that must be
corrected before it is handed to an implementing team — a plan that disagrees
with itself does not run.

- **A1 — migration filename inconsistency (BLOCKING).** v3 §3.7's table lists
  `021_fold_role_columns.sql`; the converged migration plan
  (`council-converged-migration-plan.md`, which v3 §3.7 defers to by commit
  hash) names it `021_fold_role.sql`. A migration filename is load-bearing:
  `run_migrations` (`database.py:751-763`) opens
  `os.path.join(_MIGRATIONS_DIR, migration_name)` literally — a name mismatch
  is a `FileNotFoundError`, caught by the generic `except` at
  `database.py:780-781`, logged, and the migration is **silently skipped, never
  recorded in `schema_migrations` (the failure path does not insert the tracker
  row), and retried forever every startup**. It is a permanent silent no-op the
  operator discovers only when a downstream query hits a missing table. **Fix:**
  adopt the converged plan's canonical names verbatim (`021_fold_role.sql`) —
  v3 defers to that doc, so the converged plan wins by v3's own deference.
- **A2 — self-contradicting spec-registry table count (BLOCKING).** v3 §3.7's
  table hard-lists `015_spec_bundles.sql` as `spec_bundles` + `spec_facets`
  (two tables); v3 §3.7's last paragraph then says the team *may* collapse
  `spec_facets` into a JSON column. A reader cannot tell whether `spec_facets`
  ships. **Fix:** state it once: "Phase-1 spec registry = `spec_bundles`
  (mandatory; immutable, content-hashed, `frozen_at`-stamped) + `spec_facets`
  (recommended; the team may collapse it into a `facets_json` column).
  `015` ships one or two tables at team discretion." The substance is fine —
  the team's-choice latitude is genuine, and the one binding constraint holds:
  whatever ships must be an immutable, content-hashed, freeze-timestamped
  persisted record; a source-code named constant satisfies none of those and is
  not an acceptable substitute. It is the *drafting* that contradicts itself.
- **A3 — Gate-1 parity column-exclusion list underspecified (MODIFY).** v3
  §3.8's Gate-1 says "decision-content columns only" but does not name the
  exclusion list. `§8 test 4` (the one-anchor replay-determinism test) must
  assert on the decision-content columns (`cvar_5pct`, `cvar_5pct_stderr`,
  `cvar_n_tail`, and — if the §B residue is adopted — `cvar_5pct_long`,
  `cvar_n_tail_long`) and **explicitly exclude** `id` (autoincrement) and
  `ts_utc` (wall-clock). A replay legitimately produces a different
  autoincrement `id` and a different wall-clock `ts_utc`; asserting on those
  would falsely fail a bit-identical decision.

### A.9 Hole H-9 (DOCUMENTATION / AUDIT TRAIL) — the Finalist-C exclusion is never argued in v3's body

**Owner:** critic / risk-architect.

v3's lineage included a notional **Finalist C** — a third option distinct from
Finalist A (HARDEN) and Finalist B (Phased Replace). Per v3's own §10
compliance index (condition 2), Finalist C was "explicitly ruled out (folded
into Finalist B's Phase-1/Phase-2 framing as the user's decision point)." But
that exclusion is stated **only in the §10 compliance index** — v3's body (§0,
§3, §5) never names Finalist C and never argues why there is no third finalist.
A reader of v3's body sees two finalists asserted with no account of what the
discarded third option was or why it was discarded.

This is an **audit-trail gap**, not a soundness defect — the underlying
decision (two finalists, not three) is correct. But a design document should
argue its own structure in the body, not relegate the exclusion of an
alternative to a compliance checklist.

**The fix:** v3 must argue the Finalist-C exclusion **in its body** (one or two
sentences). The argument: there is no coherent standalone third finalist
because the only candidate "third path" — pre-committing to the evidence-gated
Phase-2 roadmap — is **not a separate architecture; it is Finalist B**.
Finalist A is the terminal-acceptable floor; Finalist B is Finalist A plus the
evidence-gated Phase-2 roadmap; "pre-commit to Phase 2" is therefore a *choice
within Finalist B's framing*, not a third architecture. The genuine decision
space is **two finalists plus the user's pre-commit choice** — and v3's body
should say so where the two-finalist structure is introduced, rather than
leaving the discarded third option unexplained until §10.

**Kind:** documentation / audit trail. **Disposition:** non-binding to the
verdict; must be corrected before v3 reaches an implementing team — same tier
as H-7 and H-8.

### A.10 v3 evaluation — verdict

**v3's verdict ("harden, don't migrate") and two-finalist structure are SOUND
and ADOPTED.** The nine holes are corrections and disclosures, not a rejection:
H-1 and H-2 are binding correctness fixes; H-3 is a binding wording + routing
fix; H-4 and H-5 are binding honest-framing corrections; H-6 is a binding
disclosure (new residual W-H5); H-7 is a documentation-precision fix; H-8 is
three internal-consistency drafting defects; H-9 is an audit-trail gap (the
Finalist-C exclusion argued only in a compliance index, not the body).
**None overturns the verdict.** v3, corrected for H-1 through H-9, is a sound,
implementable design.

---

## B. EVALUATION OF THE CVaR-DIVERGENCE IDEA — REJECT

### B.0 The idea, as scrutinised

Compute CVaR two ways: `CVaR_recent` (a short ~125-day regime-current window,
~6-8 tail observations, noisy) and `CVaR_long` (a 3-year history, ~37 tail
observations, a stale-regime blend). Treat the **signed divergence**
(`CVaR_recent` rising sharply above `CVaR_long` = recent tail risk elevated vs
the historical baseline) as a signal, **validated as a regime-shift / regime-
danger DETECTOR** — a decision-quality test ("when divergence exceeds a
threshold, were forward outcomes materially worse?" over the population of
regime shifts) — rather than as a CVaR **estimate** (which needs ~1,000 tail
observations, unreachable). Claim: validating a detector rather than an estimate
sidesteps the validation wall v3 §2.3 makes decisive. A secondary element
proposes tail-data-quantity and regime-recency as explicit confidence weights.

### B.1 Verdict — REJECT as a validated detector, co-signal, or trigger

The verdict word is **REJECT**, chosen deliberately: the user reads the headline
word, and "MODIFY" would be read as "a smaller detector ships." It does not. The
divergence idea is rejected as a validated regime-shift detector, as a Phase-2
co-signal, and as a live trigger. The reasoning is **overdetermined** — three
converging routes, below — and the one route the user proposed to escape the
validation wall does not even buy independent data.

### B.2 The make-or-break question — does "validate a detector, not an estimate" escape the data wall?

**No. It relocates the wall onto an equally-thin, correlated count — and the
new count is the same scarce events re-labelled.**

The user's instinct that v3 §2.3's wall is *estimate-specific* is partly
correct: certifying a calibrated 5% CVaR number needs ~1,000 tail observations;
asking "does a threshold crossing predict worse forward outcomes" is a different
question whose validation power is the count of **independent regime-shift
events**, not tail observations. That reframing is legitimate as far as it goes.

But quantify the new wall. AlphaBot's MC pool is 3 years (~750 trading days;
`alpha_bot_execution.py:272` pulls `365*3+30` days). Genuine **independent**
macro/volatility regime transitions in a 3-year equity window — a vol-spike
onset, its decay/recovery, a trend-to-chop or chop-to-trend transition — occur
roughly 6-18 months apart. The honest count is **~5 ±2 over 3 years**; over the
fullest available history, be generous and call it **5-15**. A
detector-validation hypothesis test ("did forward outcomes worsen materially
after a threshold crossing?") has a **sample size equal to that count**. At
`n ≈ 5-15` the test has near-zero statistical power — the confidence interval on
the detector's true-positive rate spans nearly `[0, 1]`. The detector cannot be
validated.

**The decisive compounding point:** the ~5-15 regime shifts are **not an
independent budget**. A regime shift *is*, substantially, the arrival of a
cluster of tail days — the ~37 sub-5% tail days in 3 years are not uniformly
scattered; they cluster inside the ~5-15 regime episodes (volatility clustering;
Cont 2001; the entire §3 of the EUT-CVaR research report). "Count of regime
shifts" and "count of tail days" are two views of the **same scarce clustered
events**. You cannot escape a data wall by re-labelling the same data. The
detector wall is not a different, larger data pool — it is the ~1,000-tail-obs
wall's underlying events, counted a second time and found just as thin.

### B.3 The three converging routes to REJECT — a squeeze plus an independent provenance kill

The REJECT is overdetermined. Three routes — but, stated honestly, **two are
coupled (they share the regime-shift-scarcity root) and one is independent**.
The coupling is a feature, not a weakness: it is a squeeze with no escape
between its jaws.

- **Route 1 — validation power (the squeeze, lower jaw).** The population of
  independent regime shifts (~5-15) is too thin to power a detector ROC. A
  detector validated on ~5-15 events is an anecdote, not a validated detector.
- **Route 2 — NN1 / uncounted threshold (the squeeze, upper jaw).** A detector
  is useless without an operating threshold `τ`: divergence must exceed `τ` to
  fire. If `τ` is chosen by scanning the regime-shift population for the `τ`
  that best separates good-exit from bad-exit outcomes, `τ` is a **P&L-selected
  specification facet**. Per the additive accounting it contributes to `S`; per
  Defect 2 (§A.0 — a P&L-toured spec that received its own sub-sweep contributes
  its **full sub-sweep count** to `S`), a grid-searched `τ` contributes its full
  grid size. Either way `N_effective = N_optuna + S` strictly exceeds
  `N_optuna`, and the Yekutieli `c(N)` at `N` reflecting ~5 effective tests is
  `c(5) = 2.28` — a `τ` tuned on ~5 events and haircut at `c(5)` essentially
  cannot clear `HARVEY_LIU_FDR_Q = 0.05`. **The BHY haircut automatically kills
  the divergence-as-trigger if `τ` is honestly counted.** The danger of the
  user's "validate a detector, not an estimate" reframing is precisely that it
  makes `τ` *feel* like it lives outside the haircut's view — an uncounted test,
  NN1 violated, the haircut a lie by omission. Forcing `τ` back inside
  `N_effective`, `c(5)` ends it.

  **Routes 1 and 2 are coupled** — same `N ≈ 5`, two consequences (underpowered
  to validate AND auto-rejected if honestly counted). They close on the same
  point from opposite sides: relax Route 1 by enlarging the regime-shift
  population to gain validation power, and you have toured more events, so `S`
  grows and Route 2 tightens. You cannot satisfy one without violating the
  other. State it as a **squeeze**, not as two independent votes.

- **Route 3 — circular detector-validation provenance (independent).** To
  validate a *detector* you must score its calls against a ground-truth
  population of regime shifts. That requires a persisted regime-shift event log
  with labelled episodes. A **hand-curated** label set means the validating
  team **authors its own ground truth** — a circular validation, and an
  automatic Gate-1 failure under the fixture-provenance hard rule (the only
  acceptable provenances are captured-from-producer, schema-derived with a
  runtime validator, or producer-owner sign-off; "we eyeball history and label
  it" is none of those). This route is **genuinely independent of routes 1 and
  2**: even with infinite data, a hand-drawn label set is circular. It attacks
  the label set, not the count or the threshold accounting.

So: a coupled squeeze (routes 1+2) plus an independent provenance kill (route
3). The REJECT is overdetermined. The honest framing for the user is "a squeeze
plus an independent provenance kill" — three reasons, two coupled, the coupling
a feature — **not** "three independent routes," which would overclaim.

### B.4 Do the horizon trilemma and the small-sample bias pass through? Yes — both

The user asked whether a *relative* quantity (a divergence/ratio) sidesteps the
horizon trilemma (v3 §2.4) and the small-sample low-bias. It does not.

- **Horizon trilemma.** A divergence is `CVaR_recent(H) − CVaR_long(H)`. Both
  terms still require a horizon `H`. If `H` is single-day (what the kNN pool
  supports natively), both terms are single-day CVaRs and the trilemma is moot
  — but then the multi-day tail-budget ambition is also gone, and the divergence
  has the same single-day horizon limitation as M2. If `H` is multi-day, both
  terms inherit the full time-consistency / effective-alpha trilemma of v3 §2.4,
  and a difference of two time-inconsistent quantities is itself
  time-inconsistent. A relative quantity does not escape an absolute defect that
  afflicts both operands. The divergence carries two copies of the horizon
  problem.
- **Small-sample low-bias — and it is worse, not cancelled.** Both
  `CVaR_recent` (~6-8 tail obs) and `CVaR_long` (~37 tail obs) are biased
  **low** (small-sample empirical CVaR understates the tail — the rare extreme
  is usually absent). If the two biases were equal, the divergence would
  partially cancel them. They are **not** equal: the bias magnitude grows as
  tail-obs count shrinks, so `CVaR_recent` (~7 obs) is **more** downward-biased
  than `CVaR_long` (~37 obs). The divergence `CVaR_recent − CVaR_long` therefore
  carries a **net bias** that makes it **understate** a genuine regime
  deterioration — the rising recent tail is the more under-measured leg. The
  bias direction is the dangerous one again: the divergence detector is biased
  toward **not firing** when the recent regime is genuinely deteriorating. Part
  of the "signal" is just the differential small-sample bias of two unequal
  sample sizes — not a regime shift.

### B.5 The correlated-windows defect

`CVaR_long`'s 3-year window **contains** `CVaR_recent`'s 125-day window. The two
estimators are positively correlated by construction — the recent tail
observations are a subset of the long tail observations. The divergence is
therefore dominated by the *non-overlapping* part of `CVaR_long` — the stale
~625-day tail — so the "signal" is largely "is the recent regime different from
the stale 2.5-year-ago regime," a slow-moving, mostly-deterministic quantity,
not a sharp detector. If the divergence were ever pursued, a **disjoint** long
baseline (`CVaR_recent` vs a CVaR computed on a strictly non-overlapping prior
window) would be cleaner: the two would then be statistically independent and
the signal interpretable as "recent regime vs prior-era regime." This is noted
for completeness — it does not rescue the idea, because routes 1-3 reject it
regardless of how the baseline is constructed.

### B.6 The surviving residue — operator-optional, unlocks nothing

The user correctly identified one real weakness in v3's M2: M2 as specified is a
**single static CVaR number with no temporal reference frame** — an operator
sees "5% CVaR = -X%" and has no baseline to judge whether X is alarming. That is
a genuine, if minor, gap.

The **only** surviving residue that addresses it:

> An operator **may** run M2 over a second, longer window and read **both**
> numbers, **each independently under its own full four-part S-3 contract**
> (stderr on the distinct-tail count, the genuine tail-observation count, the
> "diagnostic, not a signal" label, and the bias warning).

This residue earns: **no new architecture, no new validation, no new finalist,
and no signed-divergence quantity surfaced as a single derived value.** The last
clause is binding: the moment a `cvar_divergence` value appears on a display with
a threshold-shaped affordance, an operator anchors on it as a detector and the
REJECT is undone. If a divergence delta is ever computed it belongs in an
offline analysis notebook, never in a state-DB column the dashboard can render.

**Schema consequence (binding).** The unshipped migration `023`
(`cvar_diagnostics`) **may** gain `cvar_5pct_long` and `cvar_n_tail_long` as
additive NULLable columns — these are simply "M2's inputs, second window," two
honest numbers each readable under S-3. It must **not** gain a `cvar_divergence`
or `regime_recency_weight` *persisted, displayable* column — those manufacture
the detector affordance the REJECT removes. Both new columns are `DEFAULT NULL`
(never `NOT NULL`): if `CVaR_long` is insufficient — a thin 3-year tail, an
early-life symbol — its field writes NULL, mirroring the `run_monte_carlo` /
`CVaRAssessment` `None` sentinel; a `NOT NULL` constraint on a possibly-insufficient
diagnostic field would force a fabricated zero, the exact failure the sentinel
exists to prevent.

**Execution-path cost of the residue: zero on top of M2.** With
`cvar_5pct_long` and `cvar_n_tail_long` folded into `cvar_diagnostics`, the
per-cycle write is still **one `INSERT` of one (wider) row into one table** — a
wider row, not a second row; SQLite's write cost is dominated by the connection
open + the WAL-frame commit, both per-INSERT, not per-column. No second INSERT,
table, commit, or connection. Hole H-3's mitigation (H4-helper routing +
non-blocking benchmark) covers the residue identically. The residue also adds
**zero new replay-determinism anchors** — the longer window is a second statistic
off the same `cycle_id`-seeded resample discipline — so v3's "Phase 1 = 1
anchor" claim is unchanged, and it is two-DB-clean (`cvar_diagnostics` is
state-DB; the longer window reads the same rolling Alpaca pool the kNN MC
already uses).

### B.7 If the detector were ever genuinely wanted — the only conceivable path

For completeness: the divergence-as-detector is not resurrectable in Phase 1 or
the near term. The **only** conceivable future path is for the regime-shift
ground-truth labels to come from an **external, pre-registered regime
chronology** (an NBER-style classifier, or a published volatility-regime
dataset) — never hand-curated by the validating team — and for that to be a
**Phase-2 entry gate**, a sibling to v3's gate-zero tail-data audit. Even then,
routes 1 and 2 (the validation-power / NN1 squeeze) remain — an external label
set fixes the provenance kill (route 3) but not the data-scarcity squeeze. The
honest position is that the divergence-as-detector is a Phase-2-or-never item,
and most likely never.

### B.8 The secondary element — confidence weights — is already covered by S-3

The user's secondary proposal — tail-data-quantity and regime-recency as
explicit confidence weights on a CVaR signal — is sound risk-math hygiene, and
it is **already mandated** by v3's S-3 display contract: element (b) is the
genuine tail-observation count, and the `CVaRAssessment` object already carries
`tail_obs_count`. The divergence framing adds nothing S-3 does not already
require. If the user wants the recency/quantity weighting surfaced *more
prominently* on M2's display, that is a cheap, safe, diagnostic-only display
enhancement of M2 — a MODIFY of M2's display, not an ADOPT of a new signal, and
not a persisted derived column.

### B.9 Divergence idea — verdict

**REJECTED** as a validated regime-shift detector and as a Phase-2 co-signal
(route 1: the data-scarcity wall is relocated onto a ~5-15 independent
regime-shift count, correlated with the very tail days the original wall counts;
route 2: the divergence threshold is an uncounted P&L-selected S-facet — NN1
violation — and `c(5) = 2.28` auto-kills it once honestly counted; route 3: the
detector's regime-shift label set is hand-curated, a circular validation and an
automatic Gate-1 fixture-provenance failure). The horizon trilemma and the
small-sample low-bias both pass through, the latter compounded into a
fail-toward-not-detecting net bias. The idea does **not** strengthen Finalist B
precondition (d) and does **not** earn a deficiency-closure pass — it is M2
computed over two windows and subtracted, a *variant* of M2, not a *closure* of
M2's documented "watches but cannot act" deficiency (it cannot act either).

The only surviving residue is operator-optional: M2 computed over a second,
longer window, each window's CVaR read independently under its own S-3 contract,
**no signed-divergence quantity surfaced**. That residue unlocks no
architecture, no finalist, and no validation. v3's two-finalist structure
stands.

---

## C. SUMMARY — THE TWO DELIVERABLE QUESTIONS

### C.1 Is v3 sound?

**Yes — sound, with nine holes that are corrections and disclosures, not a
rejection.** The verdict "harden, don't migrate" and the two-finalist structure
survive attack from all five lenses. The decisive ~1,000-tail-observation
validation wall holds. The nine holes:

| # | Hole | Kind | Disposition |
|---|---|---|---|
| H-1 | CRRA unbounded below; "bounded" false; haircut NaN-poisonable | Risk-math correctness | Binding fix — named `WEALTH_ARG_FLOOR` on the input `W`; new residual W-H4 |
| H-2 | M2's S-3 stderr must use the distinct-tail-obs count | Risk-math correctness | Binding fix — stderr on ~7-8 distinct obs, `§8 test 3` asserts it |
| H-3 | M2's per-cycle write is a bounded I/O cost, not "zero" | Execution-path safety | Binding fix — H4-helper routing, "near-zero benchmarked," not "zero" |
| H-4 | M2 is operator instrumentation, not a Phase-2 stepping-stone | Honest framing | Binding — re-label M2 (not demote); M2 is a Phase-2 kill-switch only |
| H-5 | §3.3 "removes three provenance gaps" overstates the floor | Honest framing | Binding — the floor removes R3 only; R1/R2 need M3 (Phase 1.5) |
| H-6 | S-2 √T inherits a serial-correlation anti-conservatism | Risk-math disclosure | Disclose-and-accept; new residual W-H5; remediation out-of-scope |
| H-7 | §8 test 1 pins the S-2 formula; it does not validate it | Documentation precision | Verb fix — "pins," not "verifies/validates" |
| H-8 | v3 internal-consistency drafting defects (A1/A2/A3) | Documentation / audit trail | Binding — canonical migration filenames, single table-count statement, named parity-exclusion list |
| H-9 | the Finalist-C exclusion is argued only in the §10 compliance index, never in v3's body | Documentation / audit trail | Argue the Finalist-C exclusion in v3's body — there is no third architecture; "pre-commit to Phase 2" is a choice within Finalist B |

None overturns the verdict. v3, corrected for H-1 through H-9, is a sound,
implementable design.

### C.2 The CVaR divergence idea — ADOPT / REJECT / MODIFY?

**REJECT** as a validated regime-shift detector, as a Phase-2 co-signal, and as
a live trigger. The "validate a detector, not an estimate" reframing does not
escape the data-scarcity wall — it relocates it onto an equally-thin,
correlated count of regime-shift events (~5-15 over 3 years), and the new count
is the same scarce clustered events re-labelled. The REJECT is overdetermined
by a coupled squeeze (validation power + NN1/uncounted-threshold) plus an
independent circular-provenance kill.

**Precise placement in v3:** the divergence idea unlocks **nothing** in v3 — not
M2's enrichment as a signal, not Finalist B precondition (d), not W-H1's
KILL-or-INCONCLUSIVE ceiling, not a new finalist. The only surviving residue is
**operator-optional**: M2 may be computed over a second, longer window, each
window's CVaR read independently under its own full S-3 contract, with **no
signed-divergence quantity ever surfaced**. v3's two-finalist structure is
unchanged. The user correctly identified one real (minor) weakness in M2 — a
CVaR number with no temporal reference frame — and the safe fix for it is the
operator-optional second window, already inside Finalist A's M2.

---

## D. Council sign-off

All five council members signed off their lenses and the adversarial debate
converged with zero open disagreements. This document was built to critic's
G1–G8 gate criteria; critic's CONVERGE gate **PASSED** all eight criteria on a
line-by-line review. The evaluation is delivered to the PM (team-lead). Branch
`design/decision-science-council`.
